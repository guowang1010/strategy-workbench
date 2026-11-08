# -*- coding: utf-8 -*-
"""交易日历：回答「下一个交易日是哪天」，并明确区分**已确认**与**估算**。

为什么需要它（BUY-01）：
    计划面板旧实现用「as_of + 1 个自然日」暗示执行日，周五会算出周六、国庆会算出假期，
    而回测是「收盘确认 → 下一交易日开盘执行」。日历不明说，时序就会说谎。

三级依据，可靠性从高到低：
    1. **dataset**：数据集内全部个股的交易日并集。覆盖区间内「某工作日不在并集里」
       即该日休市（A 股 7 只样例跨 300 日足以还原春节/国庆休市）；
    2. **holidays**：`backend/data/trading_holidays.json` 里人工维护的交易所休市安排，
       按年声明；只有声明过的年份才敢对「未来日期」下结论；
    3. **weekday_estimate**：以上都没有时的**估算**（周一~周五）。永远标记
       `confirmed=False`，调用方不得据此断言「可成交」，只能进 pending_checks。

A 股周末一律休市（含调休上班的周六周日），所以周末判断不需要额外数据。
"""
import json
import os
from datetime import date, datetime, time, timedelta, timezone

try:                                     # 3.9+ 自带 zoneinfo；Windows 缺 tzdata 时退回固定偏移
    from zoneinfo import ZoneInfo
    CN_TZ = ZoneInfo('Asia/Shanghai')
    TZ_NAME = 'Asia/Shanghai'
except Exception:                        # 中国无夏令时（1991 年后），固定 +08:00 等价
    CN_TZ = timezone(timedelta(hours=8))
    TZ_NAME = 'Asia/Shanghai (UTC+08:00)'

MARKET_OPEN = time(9, 30)                # 连续竞价开始（集合竞价 9:15 起，这里只关心「可否成交」）
MARKET_CLOSE = time(15, 0)               # 收盘：15:00 之后当天的日线才算完成

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HOLIDAY_FILE = os.path.join(os.path.dirname(_HERE), 'data', 'trading_holidays.json')

MAX_SCAN_DAYS = 400          # 连续 400 个自然日内必然存在交易日（长假最长 9 天）

SOURCE_DATASET = 'dataset'
SOURCE_HOLIDAY = 'holidays'
SOURCE_ESTIMATE = 'weekday_estimate'
SOURCE_UNKNOWN = 'unknown'


def to_date(v) -> date:
    """接受 'YYYY-MM-DD' / date / datetime，统一成 date。"""
    if isinstance(v, date) and not hasattr(v, 'hour'):
        return v
    if hasattr(v, 'date') and not isinstance(v, date):
        return v.date()
    return date.fromisoformat(str(v)[:10])


def iso(v) -> str:
    return to_date(v).isoformat()


def now_cn(now=None) -> datetime:
    """统一的「现在」：一律带 Asia/Shanghai 时区。

    三种输入：
      - None            → 真实当前时刻（带时区）；
      - datetime        → 无时区按上海解释，有时区先转换；
      - date（只有日期）→ **该日收盘后 15:30**。这是研究/回放兼容入口：
        只给日期时无法表达「尚未收盘」，所以按「这一天已经走完」解释最不容易误导
        （F01：想要盘前/盘中语义必须传 datetime 或走真实 now）。

    为什么必须显式时区：`date.today()` 取的是本机时区日期，且丢失时刻信息，
    于是「周一 9:00 还没开盘」和「周一 20:00 已收盘」被当成同一件事——
    周五收盘的计划会在周一开盘前被误判失效。
    """
    if now is None:
        return datetime.now(CN_TZ)
    if isinstance(now, datetime):
        return now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    if isinstance(now, date):
        return datetime.combine(now, time(15, 30), tzinfo=CN_TZ)
    text = str(now).strip()
    if len(text) <= 10 and 'T' not in text and ' ' not in text:
        # ISO 日期字符串（'2026-04-27'）与 date 同义：按该日收盘后解释
        return datetime.combine(to_date(text), time(15, 30), tzinfo=CN_TZ)
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return datetime.combine(to_date(text), time(15, 30), tzinfo=CN_TZ)
    return dt.replace(tzinfo=CN_TZ) if dt.tzinfo is None else dt.astimezone(CN_TZ)


def load_holidays(path: str | None = None) -> dict:
    """读取休市安排文件：{年份(int): set(date)}；文件缺失/格式错误一律当作「无数据」。

    格式：
        {"source": "说明文字", "years": {"2026": {"holidays": ["2026-01-01", ...]}}}
    只接受**完整年度**的声明——部分年份写一半会让「工作日=交易日」的推断出错，
    所以宁可整年不认，也不能拿半年的数据去断言未来。
    """
    p = path or DEFAULT_HOLIDAY_FILE
    try:
        with open(p, encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    years = raw.get('years') if isinstance(raw, dict) else None
    if not isinstance(years, dict):
        return {}
    out = {}
    for y, node in years.items():
        try:
            yi = int(y)
        except (TypeError, ValueError):
            continue
        items = node.get('holidays') if isinstance(node, dict) else node
        if not isinstance(items, list) or not items:
            continue
        try:
            out[yi] = {to_date(x) for x in items}
        except (TypeError, ValueError):
            continue                 # 该年数据有坏日期 → 整年不采用
    return out


class TradingCalendar:
    """交易日历。sessions 为**已知真实交易日**（通常来自数据集），可为空。"""

    def __init__(self, sessions=(), holidays: dict | None = None, source: str = SOURCE_DATASET):
        self._sessions = {to_date(x) for x in sessions}
        self._holidays_by_year = dict(holidays or {})
        self._holidays = set().union(*self._holidays_by_year.values()) if self._holidays_by_year else set()
        self._lo = min(self._sessions) if self._sessions else None
        self._hi = max(self._sessions) if self._sessions else None
        self.source = source

    # ---------------- 基本信息 ----------------
    @property
    def coverage(self) -> dict:
        return {
            'start': self._lo.isoformat() if self._lo else None,
            'end': self._hi.isoformat() if self._hi else None,
            'sessions': len(self._sessions),
            'holiday_years': sorted(self._holidays_by_year),
        }

    def is_session(self, day) -> bool | None:
        """True=交易日，False=确定休市，None=**无法判断**（不得当成交易日）。"""
        d = to_date(day)
        if d in self._sessions:
            return True
        if d in self._holidays:
            return False
        if self._lo and self._hi and self._lo <= d <= self._hi:
            return False                 # 覆盖区间内缺席 → 休市（或该股无行，但并集已消除）
        if d.weekday() >= 5:
            return False                 # A 股周末必休市
        if d.year in self._holidays_by_year:
            return True
        return None

    def describe(self, day) -> dict:
        d = to_date(day)
        known = self.is_session(d)
        if known is True:
            src = SOURCE_DATASET if d in self._sessions else SOURCE_HOLIDAY
        elif known is False:
            if d in self._holidays:
                src = SOURCE_HOLIDAY
            elif d.weekday() >= 5:
                src = 'weekend'
            else:
                src = SOURCE_DATASET     # 覆盖区间内的工作日缺席 = 休市
        else:
            src = SOURCE_UNKNOWN
        return {'date': d.isoformat(), 'is_session': known, 'known': known is not None,
                'source': src, 'weekday': d.weekday()}

    # ---------------- 前后交易日 ----------------
    def next_session(self, day, include_self: bool = False) -> tuple[str | None, dict]:
        """返回 (ISO 日期, 依据信息)。依据信息里 confirmed=False 时**只是估算**。"""
        d = to_date(day)
        start = 0 if include_self else 1
        for k in range(start, MAX_SCAN_DAYS):
            c = d + timedelta(days=k)
            known = self.is_session(c)
            if known is True:
                src = SOURCE_DATASET if c in self._sessions else SOURCE_HOLIDAY
                return c.isoformat(), {'source': src, 'confirmed': True, 'date': c.isoformat()}
            if known is None:
                return c.isoformat(), {'source': SOURCE_ESTIMATE, 'confirmed': False,
                                       'date': c.isoformat(),
                                       'note': '该年度未内置交易所休市安排，执行日需按公告确认'}
        return None, {'source': SOURCE_UNKNOWN, 'confirmed': False, 'date': None,
                      'note': f'{day} 之后 {MAX_SCAN_DAYS} 天内无可用交易日'}

    def prev_session(self, day, include_self: bool = False) -> tuple[str | None, dict]:
        d = to_date(day)
        start = 0 if include_self else 1
        for k in range(start, MAX_SCAN_DAYS):
            c = d - timedelta(days=k)
            if self.is_session(c) is True:
                src = SOURCE_DATASET if c in self._sessions else SOURCE_HOLIDAY
                return c.isoformat(), {'source': src, 'confirmed': True, 'date': c.isoformat()}
        return None, {'source': SOURCE_UNKNOWN, 'confirmed': False, 'date': None}

    # ---------------- 时点：现在能不能看/能不能执行 ----------------
    def latest_completed_session(self, now=None) -> tuple[str | None, dict]:
        """最近一个**已经收盘**的交易日：(ISO 日期, 依据)。

        「已完成日线」= 交易日 15:00 之后的那根 K 线。所以：
          - 周一 09:00（开盘前）→ 仍是上周五，周五的日线就是最新完整数据；
          - 周一 10:30（盘中）  → 仍是上周五（周一的 bar 还没走完）；
          - 周一 15:30（收盘后）→ 周一；
          - 周六/周日           → 上周五；
          - 休市日（数据集覆盖内缺席，或 holidays 声明）→ 继续往前找。

        与 prev_session 的关键差别：prev_session 遇到「数据集覆盖之外、又未声明休市年份」
        的工作日会一路回退到区间内的最后一天（as_of），于是陈旧数据被误判成「没落后」；
        这里把这种未知工作日按工作日**估算**为交易日，并标 `confirmed=False`，
        让调用方知道「这是估算，可能刚好是节假日」。
        """
        local = now_cn(now)
        after_close = local.time() >= MARKET_CLOSE
        anchor = local.date() if after_close else local.date() - timedelta(days=1)
        for k in range(0, MAX_SCAN_DAYS):
            c = anchor - timedelta(days=k)
            if c in self._sessions:
                return c.isoformat(), {'date': c.isoformat(), 'source': SOURCE_DATASET,
                                       'confirmed': True, 'now': local.isoformat(timespec='seconds'),
                                       'timezone': TZ_NAME,
                                       'note': f'{c.isoformat()} 15:00 收盘后的日线'}
            if c in self._holidays:
                continue
            if self._lo and self._hi and self._lo <= c <= self._hi:
                continue                 # 覆盖区间内缺席 → 休市
            if c.weekday() >= 5:
                continue                 # 周末必休市
            if c.year in self._holidays_by_year:
                return c.isoformat(), {'date': c.isoformat(), 'source': SOURCE_HOLIDAY,
                                       'confirmed': True, 'now': local.isoformat(timespec='seconds'),
                                       'timezone': TZ_NAME}
            return c.isoformat(), {'date': c.isoformat(), 'source': SOURCE_ESTIMATE,
                                   'confirmed': False, 'now': local.isoformat(timespec='seconds'),
                                   'timezone': TZ_NAME,
                                   'note': f'{c.isoformat()} 按工作日估算为交易日；'
                                           f'{c.year} 年未内置休市安排，若恰逢节假日需修正'}
        return None, {'date': None, 'source': SOURCE_UNKNOWN, 'confirmed': False,
                      'now': local.isoformat(timespec='seconds'), 'timezone': TZ_NAME}

    def session_timing(self, day, now=None, assume_session: bool = True) -> dict:
        """某交易日的时点阶段：pre_open / in_session / after_close / non_session / unknown。

        `assume_session=True` 用于「已由 next_session 推出的执行日」：该日可能落在数据集
        覆盖之外（只知道是工作日估算），这里仍按日期/时刻判断阶段，并标 `assumed=True`。
        """
        local = now_cn(now)
        d = to_date(day)
        known = self.is_session(d)
        base = {'date': d.isoformat(), 'now': local.isoformat(timespec='seconds'), 'timezone': TZ_NAME}
        if known is False:
            return {**base, 'phase': 'non_session', 'known': True, 'assumed': False}
        if known is None and not assume_session:
            return {**base, 'phase': 'unknown', 'known': False, 'assumed': True}
        if local.date() < d:
            phase = 'pre_open'
        elif local.date() > d:
            phase = 'after_close'
        elif local.time() < MARKET_OPEN:
            phase = 'pre_open'
        elif local.time() < MARKET_CLOSE:
            phase = 'in_session'
        else:
            phase = 'after_close'
        return {**base, 'phase': phase, 'known': known is True, 'assumed': known is None}

    def sessions_between(self, start, end) -> tuple[list[str], bool]:
        """(start, end] 之间的交易日列表；第二项表示是否**全部确认**（有估算则为 False）。"""
        a, b = to_date(start), to_date(end)
        if b <= a:
            return [], True
        out, confirmed = [], True
        for k in range(1, (b - a).days + 1):
            c = a + timedelta(days=k)
            known = self.is_session(c)
            if known is True:
                out.append(c.isoformat())
            elif known is None:
                confirmed = False
                if c.weekday() < 5:
                    out.append(c.isoformat())   # 估算：先按工作日算，由调用方标注未确认
        return out, confirmed

    def add_sessions(self, days):
        """并入额外已知交易日（测试与「历史数据回放」用）。"""
        self._sessions |= {to_date(x) for x in days}
        self._lo = min(self._sessions) if self._sessions else None
        self._hi = max(self._sessions) if self._sessions else None
        return self


def calendar_from_dates(dates, holidays: dict | None = None) -> TradingCalendar:
    """用数据集内全部交易日构建日历（覆盖区间内可判断休市）。"""
    return TradingCalendar(dates, holidays if holidays is not None else load_holidays())


def calendar_from_bars(bars_groups, holidays: dict | None = None) -> TradingCalendar:
    """bars_groups: [[Bar, ...], ...]，取所有股票交易日的并集。"""
    s = set()
    for group in bars_groups:
        s.update(b.date for b in group)
    return TradingCalendar(s, holidays if holidays is not None else load_holidays())
