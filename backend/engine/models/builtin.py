# -*- coding: utf-8 -*-
"""内置模型：M1 买入持有 / M3 均线规则 / M4 止盈止损 / M6 下跌空仓 (+ DP oracle 基线)

每个模型只负责「目标仓位」(0/1 数组)，撮合、费用、T+1、涨跌停、停牌由引擎统一处理。
param_schema 供前端自动渲染参数面板；grid 为寻优默认搜索空间。
M3/M4/M6 共用「买入触发信号库」(engine.signals)，可在 5 种入场触发间切换。
"""
from .base import Model
from ..data import ma
from ..signals import entry_signal, ENTRY_PARAM_SCHEMA
from ..rules import evaluate_rule, default_rule, rule_text


# 通用参数 schema 片段
_SCH_MIN_HOLD = {'key': 'min_hold', 'label': '最小持仓(天)', 'type': 'int', 'min': 1, 'max': 30, 'step': 1, 'default': 3}
_SCH_MAX_HOLD = {'key': 'max_hold', 'label': '最长持有(天)', 'type': 'int', 'min': 1, 'max': 60, 'step': 1, 'default': 12, 'allow_none': True}
_SCH_MA = {'key': 'ma_n', 'label': '均线周期', 'type': 'int', 'min': 3, 'max': 60, 'step': 1, 'default': 5}
_SCH_TP = {'key': 'tp', 'label': '止盈(%)', 'type': 'float', 'min': 1, 'max': 30, 'step': 1, 'default': 8}
_SCH_SL = {'key': 'sl', 'label': '止损(%)', 'type': 'float', 'min': 1, 'max': 20, 'step': 1, 'default': 5}

# 规则模型共用的「买入触发 + 出场」参数面板（买入触发可切换 5 种）
_RULE_SIGNAL_SCHEMA = [
    ENTRY_PARAM_SCHEMA[0],  # entry 下拉
    _SCH_MA,
    ENTRY_PARAM_SCHEMA[1],  # lookback
    ENTRY_PARAM_SCHEMA[2],  # ma_long
    ENTRY_PARAM_SCHEMA[3],  # vol_n
    ENTRY_PARAM_SCHEMA[4],  # vol_ratio
    ENTRY_PARAM_SCHEMA[5],  # mom_n
    ENTRY_PARAM_SCHEMA[6],  # mom_th
]


class BuyHold(Model):
    id = 'M1'
    name = '买入持有'
    color = '#8c8c8c'
    tradable = True
    exit_on_signal = False
    defaults = {'min_hold': 1, 'max_hold': None}
    param_schema = [_SCH_MIN_HOLD, _SCH_MAX_HOLD]
    grid = {}

    def target(self, bars, p):
        return [0] + [1] * (len(bars) - 1)


class MARule(Model):
    id = 'M3'
    name = '均线规则'
    color = '#f0a020'
    tradable = True
    exit_on_signal = True
    defaults = {'entry': 'ma_cross', 'ma_n': 5, 'min_hold': 3, 'max_hold': 15}
    param_schema = _RULE_SIGNAL_SCHEMA + [_SCH_MIN_HOLD, _SCH_MAX_HOLD]
    grid = {'ma_n': [3, 60, 1], 'min_hold': [1, 20]}

    def target(self, bars, p):
        return entry_signal(bars, p)


class TpSl(Model):
    id = 'M4'
    name = '止盈止损'
    color = '#5470c6'
    tradable = True
    uses_tp_sl = True
    exit_on_signal = False          # 出场只由 止盈/止损/到期 决定
    defaults = {'entry': 'ma_cross', 'ma_n': 20, 'min_hold': 3, 'tp': 8, 'sl': 5, 'max_hold': 10}
    param_schema = _RULE_SIGNAL_SCHEMA + [_SCH_MIN_HOLD, _SCH_TP, _SCH_SL, _SCH_MAX_HOLD]
    grid = {
        'entry': ['ma_cross', 'breakout', 'volume_confirm', 'momentum'],
        'min_hold': [1, 20],
        'tp': [2, 30, 2],
        'sl': [1, 15, 1],
    }

    def target(self, bars, p):
        return entry_signal(bars, p)


class RiskOff(Model):
    id = 'M6'
    name = '下跌空仓'
    color = '#2f9e44'
    tradable = True
    exit_on_signal = True
    defaults = {'entry': 'ma_cross', 'ma_n': 20, 'min_hold': 3, 'max_hold': None}
    param_schema = _RULE_SIGNAL_SCHEMA + [_SCH_MIN_HOLD, _SCH_MAX_HOLD]
    grid = {'ma_n': [3, 60, 1], 'min_hold': [1, 20]}

    def target(self, bars, p):
        return entry_signal(bars, p)


# 自定义规则 DSL：前端可视化编辑，后端求值（params['rule'] 为规则树 JSON）
_SCH_RULE = {'key': 'rule', 'label': '入场规则', 'type': 'rule', 'default': None}


class CustomRule(Model):
    """M9：自定义规则入场 + 止盈止损出场。DSL 求值严格因果，可通过防未来函数审计。"""
    id = 'M9'
    name = '自定义规则(止盈止损)'
    color = '#7F77DD'
    tradable = True
    uses_tp_sl = True
    exit_on_signal = False
    defaults = {'rule': default_rule(), 'min_hold': 3, 'tp': 8, 'sl': 5, 'max_hold': 10}
    param_schema = [_SCH_RULE, _SCH_MIN_HOLD, _SCH_TP, _SCH_SL, _SCH_MAX_HOLD]
    grid = {'min_hold': [1, 20], 'tp': [2, 30, 2], 'sl': [1, 15, 1]}   # 寻优出场参数；入场规则由 base_params 固定

    def target(self, bars, p):
        return evaluate_rule(p.get('rule'), bars)


class CustomRuleSignal(Model):
    """M10：自定义规则入场 + 规则失效即出场（信号出场）。"""
    id = 'M10'
    name = '自定义规则(信号出场)'
    color = '#1D9E75'
    tradable = True
    uses_tp_sl = False
    exit_on_signal = True
    defaults = {'rule': default_rule(), 'min_hold': 3, 'max_hold': 15}
    param_schema = [_SCH_RULE, _SCH_MIN_HOLD, _SCH_MAX_HOLD]
    grid = {'min_hold': [1, 20], 'max_hold': [5, 30, 5]}

    def target(self, bars, p):
        return evaluate_rule(p.get('rule'), bars)


MODELS = {'M1': BuyHold(), 'M3': MARule(), 'M4': TpSl(), 'M6': RiskOff(),
          'M9': CustomRule(), 'M10': CustomRuleSignal()}


def model_specs():
    """返回 /models 接口所需清单（含 param_schema、grid）。"""
    specs = []
    for mid in ['M1', 'M3', 'M4', 'M6', 'M9', 'M10']:
        m = MODELS[mid]
        specs.append({
            'id': m.id, 'name': m.name, 'tradable': m.tradable, 'color': m.color,
            'uses_tp_sl': m.uses_tp_sl, 'exit_on_signal': m.exit_on_signal,
            'defaults': m.defaults, 'param_schema': m.param_schema, 'grid': m.grid,
        })
    # M2 DP oracle：事后最优上界，仅作对照，不可交易
    specs.append({
        'id': 'M2', 'name': 'DP 最优上界', 'tradable': False, 'color': '#d93f3a',
        'uses_tp_sl': False, 'exit_on_signal': False,
        'defaults': {'min_hold': 3}, 'param_schema': [_SCH_MIN_HOLD], 'grid': {},
    })
    return specs


def dp_oracle(bars, min_hold=3, cost=None, capital=100000.0):
    """事后最优上界（收盘价成交，含费用）。仅作对照，不可交易。"""
    n = len(bars)
    px = [b.adj_close for b in bars]
    memo = {}

    def best(i):
        if i >= n:
            return 1.0, []
        if i in memo:
            return memo[i]
        bv, bp = best(i + 1)          # 空仓等待
        for j in range(i + min_hold, n):
            if cost is not None:
                ratio = 1.0 + cost.net_return(px[i], px[j], max(capital / px[i], 100))
            else:
                ratio = px[j] / px[i]
            if ratio <= 1:
                continue
            rest, rest_path = best(j + 1)
            val = ratio * rest        # 复利：倍数相乘（不是收益率相乘）
            if val > bv:
                bv, bp = val, [(i, j)] + rest_path
        memo[i] = (bv, bp)
        return memo[i]

    total, path = best(0)
    return {'total_pct': round((total - 1) * 100, 2), 'n_trades': len(path),
            'segments': [[bars[i].date, bars[j].date] for i, j in path]}


def dp_oracle_result(bars, min_hold=3, cost=None, capital=100000.0):
    """把 DP 上界路径转成与引擎一致的 Result 结构（收盘价成交，仅供对照展示）。"""
    from ..types import Trade
    from .. import metrics as M
    import dataclasses
    o = dp_oracle(bars, min_hold, cost, capital)
    n = len(bars)
    d2i = {b.date: i for i, b in enumerate(bars)}
    segs = sorted((d2i[bd], d2i[sd]) for bd, sd in o['segments'])
    position = [0] * n
    for bi, si in segs:
        for k in range(bi, si):
            position[k] = 1
    cash, sh = capital, 0.0
    entry_cost = hold_price = entry_i = 0.0
    holding = False
    trades, equity = [], [1.0] * n
    for i in range(n):
        if position[i] == 1 and not holding:
            px = cost.slip_buy(bars[i].adj_close)
            lots = int(cash * 0.98 // (px * 100))
            if lots >= 1:
                qty = lots * 100
                amt = px * qty
                cash -= amt + cost.buy_fee(amt)
                sh, hold_price, entry_cost, entry_i, holding = qty, px, amt + cost.buy_fee(amt), i, True
        elif position[i] == 0 and holding:
            px = cost.slip_sell(bars[i].adj_close)
            amt = px * sh
            proc = amt - cost.sell_fee(amt)
            cash += proc
            trades.append(_mk_dp_trade(Trade, bars, entry_i, i, hold_price, entry_cost, proc, px))
            sh, holding = 0.0, False
        equity[i] = (cash + sh * bars[i].adj_close) / capital
    if holding:
        i = n - 1
        px = cost.slip_sell(bars[i].adj_close)
        amt = px * sh
        proc = amt - cost.sell_fee(amt)
        cash += proc
        trades.append(_mk_dp_trade(Trade, bars, entry_i, i, hold_price, entry_cost, proc, px))
        sh = 0.0
    for idx, t in enumerate(trades):
        t.seq = idx + 1
    met = M.compute(bars, equity, trades, position, capital)
    return {
        'model_id': 'M2', 'name': 'DP 最优上界', 'tradable': False, 'color': '#d93f3a',
        'metrics': met,
        'trades': [dataclasses.asdict(t) for t in trades],
        'equity': [round(x, 6) for x in equity], 'position': position,
        'markers': {'buy': [[t.buy_date, t.buy_price] for t in trades],
                    'sell': [[t.sell_date, t.sell_price] for t in trades],
                    'segments': [[[t.buy_date, t.buy_price], [t.sell_date, t.sell_price]] for t in trades]},
    }


def _mk_dp_trade(Trade, bars, bi, si, hold_price, entry_cost, proc, fill_sell_px=None):
    sell_px = fill_sell_px if fill_sell_px is not None else bars[si].adj_close
    gross = sell_px / hold_price - 1
    net = proc / entry_cost - 1
    return Trade(seq=0, buy_i=bi, buy_date=bars[bi].date, buy_price=round(hold_price, 2),
                 sell_i=si, sell_date=bars[si].date, sell_price=round(sell_px, 2),
                 hold_days=si - bi, gross_pct=round(gross * 100, 2), net_pct=round(net * 100, 2),
                 exit_reason='dp', mfe_pct=0.0, mae_pct=0.0,
                 entry_reason='DP 事后最优（对照基线，不可交易）')
