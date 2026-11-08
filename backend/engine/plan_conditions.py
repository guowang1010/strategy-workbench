# -*- coding: utf-8 -*-
"""计划侧的条件解释与「下一根触发」求解（BUY-03 / BUY-04）。

两个**不能混为一谈**的东西：
  - `build_condition_tree()`：对 **as_of 这根已完成的 K 线**求值，回答「今天为什么确认 / 还缺什么」；
  - `build_trigger()`：回答「下一根要满足什么才触发」，价格阈值必须是**可复算**的，
    且严格不等式要按 0.01 最小价格单位取整（价格 10.00 不满足 >10.00，10.01 才满足）。

所有阈值最终都用**引擎自己的 target()** 校验一遍（合成一根收平于该价的 K 线），
保证「计划说的触发价」与「回测真正买入的信号」是同一套语义（BUY-06）。
"""
import math

from .data import ma
from .rules import (
    CMP_LABEL, COMPUTE, INDICATOR_LABEL, _collect_keys, _node_series, rule_text, validate_rule,
)

TICK = 0.01                      # A 股最小价格变动
_VOLUME_KEYS = ('volume', 'avg_volume20', 'volume_ratio20')


# ---------------- 价格取整（严格不等式必须跨过一个价位） ----------------
def _ticks(x: float) -> float:
    return round(float(x) / TICK, 6)


def tick_up(x: float) -> float:
    """> x 的最小合法价。"""
    return round((math.floor(_ticks(x)) + 1) * TICK, 2)


def tick_down(x: float) -> float:
    """<= x 的最大合法价。"""
    return round(math.floor(_ticks(x)) * TICK, 2)


def tick_ceil(x: float) -> float:
    """>= x 的最小合法价。"""
    return round(math.ceil(_ticks(x)) * TICK, 2)


def _fmt(v, digits: int = 2) -> str:
    if v is None:
        return '—'
    if abs(v) >= 10000:
        return f'{v:,.0f}'
    return f'{v:.{digits}f}'


# ---------------- as_of 条件树 ----------------
def _leaf_node(node, series, last) -> dict:
    key = node.get('left')
    right_key = node.get('right_indicator')
    left_val = (series.get(key) or [None] * (last + 1))[last]
    if right_key:
        right_val = (series.get(right_key) or [None] * (last + 1))[last]
        right_label = INDICATOR_LABEL.get(right_key, right_key)
    else:
        right_val = node.get('right_constant')
        right_label = _fmt(right_val, 4)
    temporal = node.get('temporal', 'current')
    satisfied = bool(_node_series(node, series)[last])
    unknown = left_val is None or right_val is None
    cmp_key = node.get('cmp', 'gt')
    margin = None
    if left_val is not None and right_val is not None:
        margin = round(left_val - right_val, 4)
    return {
        '$type': 'condition',
        'text': rule_text(node),
        'left': {'key': key, 'label': INDICATOR_LABEL.get(key, key), 'value': left_val},
        'cmp': cmp_key, 'cmp_label': CMP_LABEL.get(cmp_key, cmp_key),
        'right': {'key': right_key, 'label': right_label, 'value': right_val},
        'temporal': temporal, 'periods': int(node.get('periods', 1) or 1),
        'satisfied': satisfied,
        'unknown': unknown,
        'margin': margin,
    }


def _tree_from_rule(node, series, last) -> dict:
    if node.get('$type') == 'group':
        kids = [_tree_from_rule(c, series, last) for c in (node.get('children') or [])]
        op = node.get('op', 'all')
        if not kids:
            satisfied = False
        elif op == 'any':
            satisfied = any(k['satisfied'] for k in kids)
        else:
            satisfied = all(k['satisfied'] for k in kids)
        return {'$type': 'group', 'op': op, 'text': rule_text(node),
                'satisfied': satisfied, 'children': kids}
    return _leaf_node(node, series, last)


def _series_for(rule, bars) -> dict:
    keys = sorted(_collect_keys(rule, set()))
    return {k: COMPUTE[k](bars) for k in keys if k in COMPUTE}


def build_rule_conditions(bars, rule) -> dict:
    validate_rule(rule)
    series = _series_for(rule, bars)
    return _tree_from_rule(rule, series, len(bars) - 1)


def _cond(left_label, left_val, cmp_key, right_label, right_val, *, satisfied=None,
          unknown=False, extra=None) -> dict:
    if satisfied is None:
        if left_val is None or right_val is None:
            satisfied = False
        elif cmp_key == 'gt':
            satisfied = left_val > right_val
        elif cmp_key == 'gte':
            satisfied = left_val >= right_val
        elif cmp_key == 'lt':
            satisfied = left_val < right_val
        elif cmp_key == 'lte':
            satisfied = left_val <= right_val
        else:
            satisfied = abs(left_val - right_val) < 1e-9
    node = {
        '$type': 'condition',
        'text': f'{left_label} {CMP_LABEL.get(cmp_key, cmp_key)} {right_label}',
        'left': {'label': left_label, 'value': left_val},
        'cmp': cmp_key, 'cmp_label': CMP_LABEL.get(cmp_key, cmp_key),
        'right': {'label': right_label, 'value': right_val},
        'temporal': 'current', 'periods': 1,
        'satisfied': bool(satisfied), 'unknown': bool(unknown),
        'margin': round(left_val - right_val, 4) if (left_val is not None and right_val is not None) else None,
    }
    if extra:
        node.update(extra)
    return node


def _group(children, op='all', text=None) -> dict:
    if op == 'any':
        satisfied = any(c['satisfied'] for c in children)
    else:
        satisfied = all(c['satisfied'] for c in children)
    return {'$type': 'group', 'op': op,
            'text': text or (' 且 '.join(c['text'] for c in children) if op == 'all'
                             else ' 或 '.join(c['text'] for c in children)),
            'satisfied': bool(satisfied), 'children': children}


def _missing(tree, acc=None):
    """收集未满足 / 未知的叶子条件（按树顺序）。"""
    acc = [] if acc is None else acc
    if tree.get('$type') == 'group':
        for c in tree.get('children') or []:
            _missing(c, acc)
        return acc
    if tree.get('unknown'):
        acc.append({'text': tree['text'], 'reason': 'unknown'})
    elif not tree.get('satisfied'):
        acc.append({'text': tree['text'], 'reason': 'not_satisfied'})
    return acc


def build_condition_tree(bars, model, params: dict) -> dict:
    """as_of 条件树。未知量（历史不足/无数据）不得被当成「满足」。"""
    p = params
    if model.id == 'M1':
        node = {'$type': 'group', 'op': 'all', 'satisfied': True,
                'text': '买入持有：无入场条件（首个可交易日建仓）', 'children': []}
        return {'tree': node, 'missing': []}
    etype = (p.get('entry') or 'ma_cross') if not p.get('rule') else 'custom_rule'
    closes = [b.adj_close for b in bars]
    last = len(bars) - 1
    if etype == 'custom_rule':
        tree = build_rule_conditions(bars, p.get('rule'))
    elif etype == 'ma_cross':
        mn = int(p.get('ma_n', 20))
        m = ma(closes, mn)
        tree = _cond('收盘价', closes[last], 'gt', f'MA{mn}', m[last],
                     unknown=m[last] is None,
                     extra={'semantics': 'above_ma',
                            'note': '引擎口径是「收盘价站上均线」的状态，不是当日上穿'})
    elif etype == 'breakout':
        lb = max(2, int(p.get('lookback', 20)))
        hh = max((b.adj_high for b in bars[last - lb:last]), default=None) if last >= lb else None
        tree = _cond('收盘价', closes[last], 'gt', f'前{lb}日最高', hh, unknown=hh is None)
    elif etype == 'pullback':
        sn = int(p.get('ma_n', 5))
        ln = int(p.get('ma_long', 20))
        ms, ml = ma(closes, sn), ma(closes, ln)
        kids = [
            _cond('收盘价', closes[last], 'gt', f'MA{ln}', ml[last], unknown=ml[last] is None),
            _cond('收盘价', closes[last], 'lte', f'MA{sn}', ms[last], unknown=ms[last] is None),
        ]
        tree = _group(kids, 'all', f'回踩：MA{ln} < 收盘价 ≤ MA{sn}')
    elif etype == 'volume_confirm':
        mn = int(p.get('ma_n', 20))
        vn = int(p.get('vol_n', 20))
        vr = float(p.get('vol_ratio', 1.5))
        m = ma(closes, mn)
        vavg = ma([float(b.volume) for b in bars], vn)
        vol = float(bars[last].volume)
        kids = [
            _cond('收盘价', closes[last], 'gt', f'MA{mn}', m[last], unknown=m[last] is None),
            _cond('成交量', vol, 'gt', f'{vr:g}×{vn}日均量', (vavg[last] * vr) if vavg[last] else None,
                  unknown=vavg[last] is None),
        ]
        tree = _group(kids, 'all')
    elif etype == 'momentum':
        mn = int(p.get('mom_n', 10))
        th = float(p.get('mom_th', 0.0))
        base = closes[last - mn] if last >= mn else None
        roc = (closes[last] / base - 1) * 100 if base else None
        tree = _cond(f'近{mn}日动量', roc, 'gt', f'{th:g}%', th, unknown=roc is None)
    else:
        tree = {'$type': 'group', 'op': 'all', 'text': '未知入场类型', 'satisfied': False, 'children': []}
    return {'tree': tree, 'missing': _missing(tree)}


# ---------------- 下一根触发 ----------------
def _synth(bars, price: float, volume: float | None = None):
    import dataclasses
    last = bars[-1]
    return dataclasses.replace(
        last, open=price, high=price, low=price, close=price,
        adj_open=price, adj_high=price, adj_low=price, adj_close=price,
        volume=(float(last.volume) if volume is None else float(volume)))


def _trigger_at(bars, model, p, price: float, volume: float | None = None) -> bool:
    """合成一根收平于 price 的 K 线，用**引擎自身**判断是否产生入场信号。"""
    try:
        seq = model.target(bars + [_synth(bars, price, volume)], p)
        return bool(seq[-1])
    except Exception:
        return False


def _first_true(bars, model, p, start_price: float, *, volume=None,
                max_steps: int = 6) -> float | None:
    """从 start_price 起按 0.01 向上找第一个真正触发的最低价（校验解析解）。"""
    price = round(start_price, 2)
    for _ in range(max_steps):
        if _trigger_at(bars, model, p, price, volume):
            return price
        price = round(price + TICK, 2)
    return None


def _scan_rule(bars, model, p, rule, span: float = 0.25, steps: int = 400):
    """数值扫描：返回 (True 区间列表, 扫描区间)。区间用「真实触发/不触发」的两端点逼近。"""
    last = bars[-1].adj_close
    lo, hi = round(last * (1 - span), 2), round(last * (1 + span), 2)
    if hi <= lo:
        return [], [lo, hi]
    pts = [round(lo + (hi - lo) * k / steps, 2) for k in range(steps + 1)]
    flags = [_trigger_at(bars, model, p, x) for x in pts]
    runs, i = [], 0
    while i < len(pts):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(pts) and flags[j + 1]:
            j += 1
        a = pts[i] if i == 0 else pts[i - 1]      # a 不触发（或扫描左端）
        b = pts[j]
        # 左边界二分：找最小触发价
        if i > 0:
            x, y = a, b
            for _ in range(18):
                mid = round((x + y) / 2, 2)
                if mid <= x or mid >= y:
                    break
                if _trigger_at(bars, model, p, mid):
                    y = mid
                else:
                    x = mid
            left = y
            left_open = True
        else:
            left, left_open = pts[0], None
        if j == len(pts) - 1:
            right, right_open = None, None
        else:
            x, y = pts[j], pts[j + 1]             # x 触发，y 不触发
            for _ in range(18):
                mid = round((x + y) / 2, 2)
                if mid <= x or mid >= y:
                    break
                if _trigger_at(bars, model, p, mid):
                    x = mid
                else:
                    y = mid
            right, right_open = x, False          # 闭区间：x 仍触发
        runs.append({'lo': left, 'lo_open': left_open, 'hi': right, 'hi_open': right_open})
        i = j + 1
    return runs, [lo, hi]


def build_trigger(bars, model, params: dict) -> dict:
    """下一根「会触发」的价格条件。不可靠表达时**不编造**单值阈值。"""
    p = params
    etype = (p.get('entry') or 'ma_cross') if not p.get('rule') else 'custom_rule'
    closes = [b.adj_close for b in bars]
    last = len(bars) - 1
    base = {
        'entry_type': etype,
        'classification': 'not_applicable',
        'threshold_price': None,
        'cmp': None,
        'intervals': [],
        'unknown_components': [],
        'requirements': [],
        'search_range': None,
        'notes': [],
        'price_basis': 'adj_close',
    }
    if not model.tradable or model.id == 'M1':
        base['notes'].append('该模型无「下一根触发价」概念')
        return base

    if etype == 'ma_cross':
        mn = int(p.get('ma_n', 20))
        if mn < 2 or last < mn - 2:
            base.update(classification='insufficient_history',
                        notes=[f'历史不足：需要至少 {mn - 1} 根收盘价'])
            return base
        s = sum(closes[last - mn + 2:last + 1])
        analytic = s / (mn - 1)
        price = _first_true(bars, model, p, tick_up(analytic))
        base.update(classification='single_threshold', cmp='gt',
                    semantics='above_ma',
                    threshold_price=price,
                    analytic_price=round(analytic, 4),
                    intervals=[{'lo': price, 'lo_open': True, 'hi': None, 'hi_open': False}],
                    notes=['引擎口径：下一根**收盘**价站上均线即产生信号，随后再下一根开盘执行'])
        return base

    if etype == 'breakout':
        lb = max(2, int(p.get('lookback', 20)))
        if last < lb:
            base.update(classification='insufficient_history',
                        notes=[f'历史不足：需要至少 {lb} 根'])
            return base
        hh = max(b.adj_high for b in bars[last - lb + 1:last + 1])
        price = _first_true(bars, model, p, tick_up(hh))
        base.update(classification='single_threshold', cmp='gt', threshold_price=price,
                    analytic_price=round(hh, 4),
                    intervals=[{'lo': price, 'lo_open': True, 'hi': None, 'hi_open': False}])
        return base

    if etype == 'pullback':
        sn = int(p.get('ma_n', 5))
        ln = int(p.get('ma_long', 20))
        if sn < 2 or ln < 2 or last < max(sn, ln) - 1:
            base.update(classification='insufficient_history')
            return base
        s_s = sum(closes[last - sn + 2:last + 1])
        s_l = sum(closes[last - ln + 2:last + 1])
        lo = s_l / (ln - 1)          # 收盘 > MA_long
        hi = s_s / (sn - 1)          # 收盘 <= MA_short
        lo_p = _first_true(bars, model, p, tick_up(lo))
        # 上界：从 hi 往下找最大的仍触发价（<= 的闭区间）
        hi_p = tick_down(hi)
        for _ in range(6):
            if _trigger_at(bars, model, p, hi_p):
                break
            hi_p = round(hi_p - TICK, 2)
        else:
            hi_p = None
        ok = lo_p is not None and hi_p is not None and lo_p <= hi_p
        notes = ['回踩是**区间条件**：只给单边触发价会误导']
        if not ok:
            notes.append(f'当前回踩区间为空：站上 MA{ln} 需收盘 > {lo:.2f}，'
                         f'但回到 MA{sn} 之下需收盘 ≤ {hi:.2f}，两者矛盾 → 下一根不可能触发')
        base.update(classification='interval' if ok else 'unsatisfiable',
                    threshold_price=lo_p if ok else None,
                    cmp='between',
                    intervals=[{'lo': lo_p, 'lo_open': True, 'hi': hi_p, 'hi_open': False}] if ok else [],
                    requirements=[{'text': f'收盘价 > MA{ln}',
                                   'cmp': 'gt', 'value': round(lo, 4)},
                                  {'text': f'收盘价 ≤ MA{sn}',
                                   'cmp': 'lte', 'value': round(hi, 4)}],
                    notes=notes)
        return base

    if etype == 'volume_confirm':
        mn = int(p.get('ma_n', 20))
        vn = int(p.get('vol_n', 20))
        vr = float(p.get('vol_ratio', 1.5))
        if mn < 2 or vn < 2 or last < max(mn, vn) - 1:
            base.update(classification='insufficient_history')
            return base
        s = sum(closes[last - mn + 2:last + 1])
        vol = [float(b.volume) for b in bars]
        s_v = sum(vol[last - vn + 2:last + 1])
        if vr >= vn:
            base.update(classification='unsatisfiable', unknown_components=['volume'],
                        notes=[f'放量倍数 {vr:g} ≥ 量均周期 {vn}：成交量数学上无法满足该条件'])
            return base
        v_req = vr * s_v / (vn - vr)
        # 只对**价格条件**取整：整体触发还取决于当日成交量（未知），不能拿合成量当已知。
        price = tick_up(s / (mn - 1))
        base.update(
            classification='conditional_on_volume',
            threshold_price=price,
            cmp='gt',
            price_condition_only=True,
            intervals=[{'lo': price, 'lo_open': True, 'hi': None, 'hi_open': False}],
            unknown_components=['volume'],
            requirements=[{'text': f'收盘价 > MA{mn}', 'cmp': 'gt', 'value': round(s / (mn - 1), 4)},
                          {'text': f'成交量 > {vr:g}×{vn}日均量',
                           'cmp': 'gt', 'value': round(v_req, 0)}],
            notes=['价格阈值只是**必要条件**：触发还取决于当日成交量，量未知时不得视为已触发'])
        return base

    if etype == 'momentum':
        mn = int(p.get('mom_n', 10))
        th = float(p.get('mom_th', 0.0))
        if last < mn:
            base.update(classification='insufficient_history',
                        notes=[f'历史不足：需要 {mn + 1} 根收盘价'])
            return base
        # 下一根 i = last+1 的基准是 closes[i-mn] = closes[last+1-mn]（旧实现少算了一根）
        anchor = closes[last + 1 - mn]
        analytic = anchor * (1 + th / 100.0)
        price = _first_true(bars, model, p, tick_up(analytic))
        base.update(classification='single_threshold', cmp='gt', threshold_price=price,
                    analytic_price=round(analytic, 4),
                    intervals=[{'lo': price, 'lo_open': True, 'hi': None, 'hi_open': False}],
                    notes=[f'基准收盘取 {bars[last + 1 - mn].date}（i-{mn}），不是 as_of 前 {mn} 根'])
        return base

    # 自定义规则：先看是否含与价格无关的量能条件，再数值扫描
    rule = p.get('rule')
    try:
        validate_rule(rule)
    except Exception as e:
        base.update(classification='invalid_rule', notes=[str(e)])
        return base
    keys = _collect_keys(rule, set())
    unknown = [k for k in ('volume', 'avg_volume20', 'volume_ratio20') if k in keys]
    runs, span = _scan_rule(bars, model, p, rule)
    base['search_range'] = span
    base['unknown_components'] = unknown
    if not runs:
        base.update(classification='outside_search_range',
                    notes=[f'在 {span[0]:.2f}~{span[1]:.2f} 扫描范围内始终不触发；'
                           f'不代表永远不可触发，也不把扫描边界当成阈值'])
        return base
    if len(runs) > 1:
        base.update(classification='multi_interval', intervals=runs, cmp='between',
                    notes=['规则存在多个互不相连的触发区间，不能压成单点阈值'])
        return base
    r = runs[0]
    unbounded_lo = r['lo'] is not None and r['lo_open'] is None      # 扫描左端就已触发
    unbounded_hi = r['hi'] is None
    if not unbounded_hi and r['lo'] is not None and r['hi'] is not None and abs(r['hi'] - r['lo']) < TICK:
        base.update(classification='unsatisfiable', intervals=[], notes=['扫描区间内仅单点触发，稳定性不足'])
        return base
    classification = 'interval' if not unbounded_hi else 'single_threshold'
    notes = list(base['notes'])
    if unbounded_lo:
        classification = 'outside_search_range'
        notes.append(f'扫描下界 {span[0]:.2f} 处已触发，真实阈值低于扫描范围；不把边界当阈值')
    if unknown:
        classification = 'conditional_on_volume'
        notes.append('规则含量能条件：量未知时价格区间只是必要条件，不构成触发')
    base.update(classification=classification,
                intervals=[r] if not unbounded_lo else [],
                threshold_price=(r['lo'] if (not unbounded_lo and unbounded_hi) else None),
                cmp='gt' if unbounded_hi else 'between',
                notes=notes + ['阈值为数值扫描 + 边界二分逼近，非解析解'],
                requirements=[{'text': rule_text(rule), 'cmp': 'rule', 'value': None}])
    return base
