# -*- coding: utf-8 -*-
"""自定义规则 DSL：可视化构造入场条件，严格因果（indicator[i] 只依赖 bars[0..i]）。

设计对齐 PortfolioPilot 的 ConfiguredRuleStrategy，指标口径逐项还原：
  17 个指标 × 6 种比较符 × 4 个时序算子（当前 / 上穿 / 下穿 / 连续N日）
  条件可 AND/OR 分组嵌套，JSON 结构跨语言通用（前端直接构造，后端直接求值）

节点 JSON:
  条件: {"$type":"condition","left":"close","cmp":"gt",
         "right_indicator":"sma20" | "right_constant":10.0,
         "temporal":"current","periods":1}
  分组: {"$type":"group","op":"all"|"any","children":[...]}

注意：价格一律用前复权价（adj_*），与回测撮合同源；
      PortfolioPilot 用的是其库内价格，若两边对比需注意复权口径差异。
"""
from .data import ma

# ---------------- 指标定义（key / 中文名 / 量纲提示） ----------------
INDICATORS = [
    {'key': 'close', 'label': '收盘价', 'unit': '元'},
    {'key': 'sma5', 'label': 'MA5', 'unit': '元'},
    {'key': 'sma10', 'label': 'MA10', 'unit': '元'},
    {'key': 'sma20', 'label': 'MA20', 'unit': '元'},
    {'key': 'sma60', 'label': 'MA60', 'unit': '元'},
    {'key': 'highest_high20', 'label': '20日最高', 'unit': '元'},
    {'key': 'lowest_low20', 'label': '20日最低', 'unit': '元'},
    {'key': 'volume', 'label': '成交量', 'unit': '股'},
    {'key': 'avg_volume20', 'label': '20日均量', 'unit': '股'},
    {'key': 'prev_highest_high20', 'label': '前20日最高', 'unit': '元'},
    {'key': 'prev_lowest_low20', 'label': '前20日最低', 'unit': '元'},
    {'key': 'volume_ratio20', 'label': '量比20', 'unit': '倍'},
    {'key': 'rsi14', 'label': 'RSI14', 'unit': ''},
    {'key': 'dist_to_sma20', 'label': '距MA20', 'unit': '倍'},
    {'key': 'daily_return', 'label': '日涨跌幅', 'unit': '倍'},
    {'key': 'sma5_change', 'label': 'MA5日变化', 'unit': '元/日'},
    {'key': 'ma5_pullback', 'label': '回踩MA5', 'unit': '0/1'},
]

INDICATOR_KEYS = [x['key'] for x in INDICATORS]
INDICATOR_LABEL = {x['key']: x['label'] for x in INDICATORS}

COMPARISONS = [
    {'key': 'gt', 'label': '>'}, {'key': 'gte', 'label': '≥'},
    {'key': 'lt', 'label': '<'}, {'key': 'lte', 'label': '≤'},
    {'key': 'eq', 'label': '='}, {'key': 'neq', 'label': '≠'},
]
CMP_KEYS = [x['key'] for x in COMPARISONS]
CMP_LABEL = {x['key']: x['label'] for x in COMPARISONS}

TEMPORALS = [
    {'key': 'current', 'label': '当前满足'},
    {'key': 'cross_above', 'label': '上穿（由不满足转为满足）'},
    {'key': 'cross_below', 'label': '下穿（由满足转为不满足）'},
    {'key': 'consecutive', 'label': '连续 N 日满足'},
]
TEMPORAL_KEYS = [x['key'] for x in TEMPORALS]
TEMPORAL_LABEL = {x['key']: x['label'] for x in TEMPORALS}


# ---------------- 指标计算：全部返回 list[float|None]，None = 历史不足 ----------------
def _closes(bars):
    return [b.adj_close for b in bars]


def _sma_series(bars, n):
    return ma(_closes(bars), n)


def _rsi14(bars):
    """Cutler RSI：近 15 根收盘（14 段涨跌）的涨跌幅简单均值，非 Wilder 平滑。"""
    n = len(bars)
    out = [None] * n
    for i in range(14, n):
        gains = losses = 0.0
        for j in range(i - 14, i):
            ch = bars[j + 1].adj_close - bars[j].adj_close
            if ch > 0:
                gains += ch
            else:
                losses -= ch
        out[i] = 100.0 if losses == 0 else round(100.0 - 100.0 / (1.0 + gains / losses), 4)
    return out


def _sma5_change(bars):
    n = len(bars)
    c = _closes(bars)
    out = [None] * n
    for i in range(5, n):
        out[i] = (c[i] - c[i - 5]) / 5.0
    return out


def _daily_return(bars):
    n = len(bars)
    c = _closes(bars)
    out = [None] * n
    for i in range(1, n):
        out[i] = c[i] / c[i - 1] - 1.0 if c[i - 1] else None
    return out


def _ma5_pullback(bars):
    """昨日收盘高于昨日MA5，且今日最低价落在 [MA5, MA5×1.01] 内，且收盘不低于 MA5 → 1，否则 0。"""
    n = len(bars)
    m5 = ma(_closes(bars), 5)
    out = [None] * n
    for i in range(5, n):
        prev_ma, cur_ma = m5[i - 1], m5[i]
        if prev_ma is None or cur_ma is None:
            continue
        matched = (bars[i - 1].adj_close > prev_ma
                   and bars[i].adj_low >= cur_ma
                   and bars[i].adj_low <= cur_ma * 1.01
                   and bars[i].adj_close >= cur_ma)
        out[i] = 1.0 if matched else 0.0
    return out


def _highest_high20(bars):
    n = len(bars)
    out = [None] * n
    for i in range(19, n):
        out[i] = max(b.adj_high for b in bars[i - 19:i + 1])
    return out


def _lowest_low20(bars):
    n = len(bars)
    out = [None] * n
    for i in range(19, n):
        out[i] = min(b.adj_low for b in bars[i - 19:i + 1])
    return out


def _prev_highest_high20(bars):
    """不含当日的前 20 根最高价（用于「突破前高」类条件，天然无未来）。"""
    n = len(bars)
    out = [None] * n
    for i in range(20, n):
        out[i] = max(b.adj_high for b in bars[i - 20:i])
    return out


def _prev_lowest_low20(bars):
    n = len(bars)
    out = [None] * n
    for i in range(20, n):
        out[i] = min(b.adj_low for b in bars[i - 20:i])
    return out


def _avg_volume20(bars):
    return ma([float(b.volume) for b in bars], 20)


def _volume(bars):
    return [float(b.volume) for b in bars]


def _volume_ratio20(bars):
    n = len(bars)
    avg = _avg_volume20(bars)
    out = [None] * n
    for i in range(n):
        if avg[i] and avg[i] > 0:
            out[i] = float(bars[i].volume) / avg[i]
    return out


def _dist_to_sma20(bars):
    n = len(bars)
    c = _closes(bars)
    m = ma(c, 20)
    out = [None] * n
    for i in range(n):
        if m[i]:
            out[i] = c[i] / m[i] - 1.0
    return out


COMPUTE = {
    'close': _closes,
    'sma5': lambda b: _sma_series(b, 5),
    'sma10': lambda b: _sma_series(b, 10),
    'sma20': lambda b: _sma_series(b, 20),
    'sma60': lambda b: _sma_series(b, 60),
    'highest_high20': _highest_high20,
    'lowest_low20': _lowest_low20,
    'volume': _volume,
    'avg_volume20': _avg_volume20,
    'prev_highest_high20': _prev_highest_high20,
    'prev_lowest_low20': _prev_lowest_low20,
    'volume_ratio20': _volume_ratio20,
    'rsi14': _rsi14,
    'dist_to_sma20': _dist_to_sma20,
    'daily_return': _daily_return,
    'sma5_change': _sma5_change,
    'ma5_pullback': _ma5_pullback,
}


# ---------------- 校验 ----------------
def validate_rule(node, path='root'):
    """递归校验规则树，任何非法之处抛 ValueError（中文说明）。"""
    if not isinstance(node, dict):
        raise ValueError(f'{path}: 规则节点必须是对象')
    ntype = node.get('$type')
    if ntype == 'group':
        op = node.get('op', 'all')
        if op not in ('all', 'any'):
            raise ValueError(f'{path}: 分组逻辑必须是 all(同时满足) 或 any(任一满足)')
        kids = node.get('children') or []
        if not kids:
            raise ValueError(f'{path}: 分组内至少需要一个条件')
        for k, child in enumerate(kids):
            validate_rule(child, f'{path}.children[{k}]')
        return
    if ntype != 'condition':
        raise ValueError(f'{path}: 未知节点类型 {ntype!r}（应为 condition 或 group）')

    if node.get('left') not in INDICATOR_KEYS:
        raise ValueError(f'{path}: 左值指标无效：{node.get("left")!r}')
    if node.get('cmp') not in CMP_KEYS:
        raise ValueError(f'{path}: 比较符无效：{node.get("cmp")!r}')

    has_ind = node.get('right_indicator') not in (None, '')
    has_const = node.get('right_constant') is not None
    if has_ind == has_const:
        raise ValueError(f'{path}: 右值必须且只能选择「指标」或「常量」之一')
    if has_ind and node['right_indicator'] not in INDICATOR_KEYS:
        raise ValueError(f'{path}: 右值指标无效：{node["right_indicator"]!r}')

    temporal = node.get('temporal', 'current')
    if temporal not in TEMPORAL_KEYS:
        raise ValueError(f'{path}: 时序算子无效：{temporal!r}')
    periods = int(node.get('periods', 1) or 1)
    if not 1 <= periods <= 60:
        raise ValueError(f'{path}: 连续周期须在 1-60 之间')
    if temporal != 'consecutive' and periods != 1:
        raise ValueError(f'{path}: 仅「连续 N 日」算子允许周期大于 1')
    if temporal == 'cross_above' and node.get('cmp') not in ('gt', 'gte'):
        raise ValueError(f'{path}: 上穿算子只能搭配 > 或 ≥')
    if temporal == 'cross_below' and node.get('cmp') not in ('lt', 'lte'):
        raise ValueError(f'{path}: 下穿算子只能搭配 < 或 ≤')


# ---------------- 求值 ----------------
def _compare(a, b, cmp_key):
    if a is None or b is None:
        return False
    try:
        if cmp_key == 'gt':
            return a > b
        if cmp_key == 'gte':
            return a >= b
        if cmp_key == 'lt':
            return a < b
        if cmp_key == 'lte':
            return a <= b
        if cmp_key == 'eq':
            return abs(a - b) < 1e-9
        return abs(a - b) >= 1e-9
    except TypeError:
        return False


def _apply_temporal(raw, temporal, periods):
    n = len(raw)
    if temporal == 'current' or temporal is None:
        return raw
    out = [False] * n
    if temporal == 'consecutive':
        p = max(1, int(periods or 1))
        run = 0
        for i in range(n):
            run = run + 1 if raw[i] else 0
            out[i] = run >= p
        return out
    # 上穿 / 下穿：今日满足且昨日不满足（首根无前值，视为新满足）
    for i in range(n):
        if not raw[i]:
            continue
        out[i] = True if i == 0 else not raw[i - 1]
    return out


def _node_series(node, series):
    n = len(next(iter(series.values()))) if series else 0
    if node.get('$type') == 'group':
        kids = [_node_series(c, series) for c in (node.get('children') or [])]
        if not kids:
            return [False] * n
        if node.get('op') == 'any':
            return [any(k[i] for k in kids) for i in range(n)]
        return [all(k[i] for k in kids) for i in range(n)]

    left = series.get(node['left']) or [None] * n
    if node.get('right_indicator'):
        right = series.get(node['right_indicator']) or [None] * n
    else:
        const = node.get('right_constant')
        right = [const] * n
    cmp_key = node.get('cmp', 'gt')
    raw = [_compare(left[i], right[i], cmp_key) for i in range(n)]
    return _apply_temporal(raw, node.get('temporal', 'current'), node.get('periods', 1))


def _collect_keys(node, acc):
    if node.get('$type') == 'group':
        for c in (node.get('children') or []):
            _collect_keys(c, acc)
        return acc
    if node.get('left'):
        acc.add(node['left'])
    if node.get('right_indicator'):
        acc.add(node['right_indicator'])
    return acc


def evaluate_rule(rule, bars) -> list:
    """规则 → 每根 K 线的 0/1 入场意图数组（严格因果，可直接送撮合）。"""
    n = len(bars)
    if not rule or not isinstance(rule, dict):
        return [0] * n
    validate_rule(rule)
    keys = sorted(_collect_keys(rule, set()))
    series = {k: COMPUTE[k](bars) for k in keys if k in COMPUTE}
    if not series:
        return [0] * n
    sig = _node_series(rule, series)
    return [1 if x else 0 for x in sig]


# ---------------- 人类可读描述 ----------------
def _fmt_const(v):
    if v is None:
        return '—'
    if isinstance(v, (int, float)) and abs(v) >= 10000:
        return f'{v:,.0f}'
    return f'{v:g}'


def rule_text(node, depth=0) -> str:
    """把规则树渲染成中文说明（用于「下一笔计划」面板展示入场条件）。"""
    if not node:
        return '（未配置规则）'
    if node.get('$type') == 'group':
        joiner = ' 且 ' if node.get('op', 'all') == 'all' else ' 或 '
        parts = [rule_text(c, depth + 1) for c in (node.get('children') or [])]
        if not parts:
            return '（空分组）'
        s = joiner.join(parts)
        return f'（{s}）' if depth > 0 and len(parts) > 1 else s

    left = INDICATOR_LABEL.get(node.get('left'), node.get('left'))
    cmp_s = CMP_LABEL.get(node.get('cmp'), '>')
    right = (INDICATOR_LABEL.get(node['right_indicator'], node['right_indicator'])
             if node.get('right_indicator') else _fmt_const(node.get('right_constant')))
    temporal = node.get('temporal', 'current')
    if temporal == 'consecutive':
        return f'{left} 连续 {int(node.get("periods", 1))} 日 {cmp_s} {right}'
    if temporal == 'cross_above':
        return f'{left} 上穿 {right}'
    if temporal == 'cross_below':
        return f'{left} 下穿 {right}'
    return f'{left} {cmp_s} {right}'


def _trigger_at(bars, rule, price):
    """假设下一根 K 线以 price 收平（开高低收同为 price），判断规则是否触发。"""
    import dataclasses
    last = bars[-1]
    synth = dataclasses.replace(last, open=price, high=price, low=price, close=price,
                                adj_open=price, adj_high=price, adj_low=price, adj_close=price)
    try:
        return bool(evaluate_rule(rule, bars + [synth])[-1])
    except Exception:
        return False


def rule_threshold(bars, rule, span=0.20, steps=48):
    """数值求解「下一根收盘价达到多少会触发规则」（单调假设下二分细化）。

    规则含成交量等与价格无关的条件时无法单值表达，返回 None。
    """
    if not rule or not bars:
        return None
    try:
        validate_rule(rule)
    except Exception:
        return None
    c = bars[-1].adj_close
    if not c or c <= 0:
        return None
    lo, hi = c * (1 - span), c * (1 + span)
    prev_p, prev_t = lo, _trigger_at(bars, rule, lo)
    if prev_t:
        return round(lo, 2)
    hit = None
    for k in range(1, steps + 1):
        p = lo + (hi - lo) * k / steps
        t = _trigger_at(bars, rule, p)
        if t:
            hit = (prev_p, p)
            break
        prev_p, prev_t = p, t
    if hit is None:
        return None
    a, b = hit                       # a 未触发，b 触发
    for _ in range(20):
        mid = (a + b) / 2
        if _trigger_at(bars, rule, mid):
            b = mid
        else:
            a = mid
    return round(b, 2)


# ---------------- 预置模板（前端「载入模板」用） ----------------
def default_rule():
    """默认规则：收盘价上穿 MA20（等价于 M4 默认入场）。"""
    return {
        '$type': 'group', 'op': 'all',
        'children': [{
            '$type': 'condition', 'left': 'close', 'cmp': 'gt',
            'right_indicator': 'sma20', 'right_constant': None,
            'temporal': 'cross_above', 'periods': 1,
        }],
    }


TEMPLATES = [
    {'name': '收盘上穿MA20', 'rule': default_rule()},
    {'name': '放量突破前高', 'rule': {
        '$type': 'group', 'op': 'all', 'children': [
            {'$type': 'condition', 'left': 'close', 'cmp': 'gt',
             'right_indicator': 'prev_highest_high20', 'right_constant': None,
             'temporal': 'current', 'periods': 1},
            {'$type': 'condition', 'left': 'volume_ratio20', 'cmp': 'gte',
             'right_indicator': None, 'right_constant': 1.5,
             'temporal': 'current', 'periods': 1},
        ]}},
    {'name': '回踩MA5不破', 'rule': {
        '$type': 'group', 'op': 'all', 'children': [
            {'$type': 'condition', 'left': 'ma5_pullback', 'cmp': 'gte',
             'right_indicator': None, 'right_constant': 1,
             'temporal': 'current', 'periods': 1},
        ]}},
    {'name': 'RSI超卖反弹', 'rule': {
        '$type': 'group', 'op': 'all', 'children': [
            {'$type': 'condition', 'left': 'rsi14', 'cmp': 'lt',
             'right_indicator': None, 'right_constant': 30,
             'temporal': 'current', 'periods': 1},
            {'$type': 'condition', 'left': 'daily_return', 'cmp': 'gt',
             'right_indicator': None, 'right_constant': 0.0,
             'temporal': 'current', 'periods': 1},
        ]}},
    {'name': '连续3日站稳MA5', 'rule': {
        '$type': 'group', 'op': 'all', 'children': [
            {'$type': 'condition', 'left': 'close', 'cmp': 'gt',
             'right_indicator': 'sma5', 'right_constant': None,
             'temporal': 'consecutive', 'periods': 3},
        ]}},
]
