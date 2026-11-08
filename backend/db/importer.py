# -*- coding: utf-8 -*-
"""CSV 行情导入校验（先校验、后提交）。

历史实现把坏数值静默置 0、日期只按字符串排序、重复键被 INSERT OR REPLACE 覆盖，
导致「坏数据变成正常行情」且无法追溯。这里改为：

1. 必需列缺失 → 直接拒绝；
2. 逐行校验日期格式、6 位代码、有限正价格、OHLC 关系、因子/成交量非负；
3. 重复 (代码, 日期) → 拒绝并报行号；
4. 停牌日允许价格为空/0，自动用前收盘价填充并标记 suspended（不产生假行情）；
5. 全部校验通过才返回结构，由 repository 在单个事务里写入（失败回滚）。
"""
import csv
import dataclasses
from datetime import date

from engine.bars import RawRow

REQUIRED_COLUMNS = ('交易日期', '股票代码', '开盘价', '最高价', '最低价', '收盘价', '成交量(股)')
MAX_ERRORS = 20
_EPS = 1e-6


class ImportValidationError(ValueError):
    """导入校验失败；errors 为 [(行号, 字段, 说明), ...]。API 映射 422。"""

    def __init__(self, message: str, errors: list | None = None):
        super().__init__(message)
        self.errors = errors or []


@dataclasses.dataclass
class Row(RawRow):
    """一行已校验的原始行情（RawRow 的全部字段 + 定位信息）。"""
    code: str
    name: str


def _num(raw, field, line, errors, *, required=True, default=0.0):
    s = (raw or '').strip()
    if s in ('', '-', '--', 'None', 'nan', 'NaN'):
        if required:
            errors.append((line, field, '缺失'))
            return None
        return default
    try:
        v = float(s.replace(',', ''))
    except ValueError:
        errors.append((line, field, f'非数值: {s!r}'))
        return None
    if v != v or v in (float('inf'), float('-inf')):
        errors.append((line, field, f'非有限数值: {s!r}'))
        return None
    return v


def _parse_date(raw, line, errors):
    s = (raw or '').strip().replace('/', '-')
    if len(s) != 10:
        errors.append((line, '交易日期', f'格式应为 YYYY-MM-DD，收到 {raw!r}'))
        return None
    try:
        date.fromisoformat(s)
    except ValueError:
        errors.append((line, '交易日期', f'非法日期 {raw!r}'))
        return None
    return s


def parse_csv(path: str) -> dict:
    """解析并校验 CSV，返回 {groups, names, start, end, rows, warnings}。"""
    errors: list[tuple[int, str, str]] = []
    groups: dict[str, list[Row]] = {}
    names: dict[str, str] = {}
    seen: dict[tuple[str, str], int] = {}
    warnings: list[str] = []

    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise ImportValidationError(f'缺少必需列：{", ".join(missing)}',
                                        [(0, '表头', f'缺失列 {", ".join(missing)}')])
        reader.fieldnames = header

        for line, row in enumerate(reader, start=2):
            code = (row.get('股票代码') or '').strip()
            if not (len(code) == 6 and code.isdigit()):
                errors.append((line, '股票代码', f'需为 6 位数字，收到 {code!r}'))
                continue
            d = _parse_date(row.get('交易日期'), line, errors)
            if not d:
                continue
            if (code, d) in seen:
                errors.append((line, '交易日期', f'{code} 在 {d} 重复（首次出现在第 {seen[(code, d)]} 行）'))
                continue
            seen[(code, d)] = line

            st_raw = (row.get('ST状态') or '').strip()
            status = (row.get('交易状态') or '').strip()
            flag = (row.get('停牌标记') or '').strip()
            suspended = flag == '是' or status in ('停牌', '停牌一天') or (
                status not in ('', '正常交易') and not row.get('收盘价'))

            req = not suspended
            o = _num(row.get('开盘价'), '开盘价', line, errors, required=req, default=None)
            h = _num(row.get('最高价'), '最高价', line, errors, required=req, default=None)
            lo = _num(row.get('最低价'), '最低价', line, errors, required=req, default=None)
            c = _num(row.get('收盘价'), '收盘价', line, errors, required=req, default=None)
            pc = _num(row.get('前收盘价'), '前收盘价', line, errors, required=False, default=None)
            vol = _num(row.get('成交量(股)'), '成交量(股)', line, errors, required=False, default=0.0)
            amt = _num(row.get('成交额(元)'), '成交额(元)', line, errors, required=False, default=0.0)
            tov = _num(row.get('换手率(%)'), '换手率(%)', line, errors, required=False, default=0.0)
            fac = _num(row.get('复权因子'), '复权因子', line, errors, required=False, default=1.0)

            if any(v is None for v in (o, h, lo, c)):
                if not suspended:
                    continue
                prev = groups.get(code, [])
                base = pc if pc else (prev[-1].close if prev else None)
                if not base or base <= 0:
                    errors.append((line, '收盘价', '停牌行缺少可用前收盘价，无法填充'))
                    continue
                o = h = lo = c = base

            for k, v in (('开盘价', o), ('最高价', h), ('最低价', lo), ('收盘价', c)):
                if v is None or v <= 0:
                    errors.append((line, k, f'必须为正数，收到 {v!r}'))
            if pc is not None and pc <= 0:
                errors.append((line, '前收盘价', f'必须为正数，收到 {pc!r}'))
            if (o is not None and h is not None and lo is not None and c is not None
                    and all(v is not None and v > 0 for v in (o, h, lo, c))):
                hi_expect = max(o, c)
                lo_expect = min(o, c)
                if h + _EPS < hi_expect:
                    errors.append((line, '最高价', f'{h} 低于 开/收 最大值 {hi_expect}'))
                if lo - _EPS > lo_expect:
                    errors.append((line, '最低价', f'{lo} 高于 开/收 最小值 {lo_expect}'))
            if fac is not None and fac <= 0:
                errors.append((line, '复权因子', f'必须为正数，收到 {fac!r}'))
            for k, v in (('成交量(股)', vol), ('成交额(元)', amt), ('换手率(%)', tov)):
                if v is not None and v < 0:
                    errors.append((line, k, f'不能为负，收到 {v}'))

            nm = (row.get('股票名称') or '').strip()
            if nm:
                names[code] = nm

            groups.setdefault(code, []).append(Row(
                code=code, date=d, name=nm, open=o, high=h, low=lo, close=c,
                pre_close=(pc if pc else c), volume=vol or 0.0, amount=amt or 0.0,
                turnover=tov or 0.0, factor=(fac if fac else 1.0),
                st=(st_raw == '是' or 'ST' in (nm or '').upper()), suspended=suspended,
            ))

    if errors:
        head = errors[0]
        more = f'（共 {len(errors)} 处问题，仅列前 {min(len(errors), MAX_ERRORS)} 条）' if len(errors) > 1 else ''
        raise ImportValidationError(
            f'第 {head[0]} 行 {head[1]}: {head[2]}{more}', errors[:MAX_ERRORS])
    if not groups:
        raise ImportValidationError('CSV 中没有有效行情行', [(0, '文件', '无有效数据')])

    for code in groups:
        groups[code].sort(key=lambda r: r.date)
        # 交易日历完整性：同一股票内日期必须严格递增（重复已在上面拦截）
        dates = [r.date for r in groups[code]]
        if len(set(dates)) != len(dates):
            raise ImportValidationError(f'{code} 存在重复交易日')

    all_dates = [r.date for rows in groups.values() for r in rows]
    return {
        'groups': groups,
        'names': names,
        'start': min(all_dates),
        'end': max(all_dates),
        'rows': len(all_dates),
        'warnings': warnings,
    }
