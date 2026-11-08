# -*- coding: utf-8 -*-
"""下一笔计划验收集（BUY-01～10）。

重点不是「字段存在」，而是三条硬性质：
  1. **时序**：收盘确认 → 下一**交易日**开盘执行（周末/节假日/日历未知都要说清楚）；
  2. **一致性**：计划的 signal_confirmed 必须等于引擎 entry_signal 在同一前缀上的结论，
     执行日/成交价/数量必须与回测逐笔对得上；
  3. **不撒谎**：未知量（成交量、未来开盘价、未确认日历）不得被标成「已满足/可成交」。
"""
import datetime as _dt
import math
import os

import pytest

from engine.backtest import order_size, run
from engine.config import ExecutionConfig
from engine.cost import Cost
from engine.models.base import Model
from engine.models.builtin import MODELS
from engine.plan import next_plan
from engine.plan_conditions import build_trigger, tick_up
from engine.rules import default_rule
from engine.signals import entry_signal
from engine.trading_calendar import TradingCalendar, calendar_from_dates
from engine.types import Bar

CFG = ExecutionConfig()
COST = CFG.cost()


# ---------------- 构造数据 ----------------
def weekdays(start: str, n: int, skip: set | None = None) -> list[str]:
    d = _dt.date.fromisoformat(start)
    out = []
    skip = skip or set()
    while len(out) < n:
        s = d.isoformat()
        if d.weekday() < 5 and s not in skip:
            out.append(s)
        d += _dt.timedelta(days=1)
    return out


def mk_bars(dates, prices=None, start=10.0):
    bars = []
    for i, d in enumerate(dates):
        if prices is not None:
            px = prices[i]
        else:
            px = round(start + 0.02 * i, 2)
        bars.append(Bar(
            date=d, open=px, high=round(px * 1.01, 2), low=round(px * 0.99, 2), close=px,
            pre_close=px, volume=100000, amount=px * 100000, turnover=1.0, factor=1.0,
            adj_open=px, adj_high=round(px * 1.01, 2), adj_low=round(px * 0.99, 2), adj_close=px,
            limit_up=round(px * 1.1, 2), limit_down=round(px * 0.9, 2),
        ))
    return bars


def rising(n=40, ma_n=3):
    """前 12 根横盘、之后单边上行：足以让 close>MA 从「不满足」翻成「满足」。"""
    px = [10.0] * 12 + [round(10.0 + 0.15 * i, 2) for i in range(1, n - 11)]
    return mk_bars(weekdays('2026-03-02', n), px)


def flat(n=40, px=10.0):
    """横盘：收盘 == 均线，均线类信号「不满足」（严格大于）。"""
    return mk_bars(weekdays('2026-03-02', n), [px] * n)


def jump(n=40, px=10.0, delta=1.0):
    """横盘后最后一根跳涨：as_of 收盘确认信号，但账本还没买入。"""
    return mk_bars(weekdays('2026-03-02', n), [px] * (n - 1) + [round(px + delta, 2)])


def sustained(n=40, px=10.0):
    """末段持续上行：as_of 与前一根都满足 → 属于「已存在的持续信号」。"""
    tail = 10
    px_list = [px] * (n - tail) + [round(px + 0.2 * (i + 1), 2) for i in range(tail)]
    return mk_bars(weekdays('2026-03-02', n), px_list)


def zigzag(n=80, base=10.0, amp=1.5):
    """反复穿越均线：用于逐日核对「计划确认 → 回测次日成交」。"""
    return mk_bars(weekdays('2026-03-02', n),
                   [round(base + amp * math.sin(i / 3.0), 2) for i in range(n)])


FLAT_ACCOUNT = {'mode': 'manual_actual', 'cash': 100000, 'shares': 0, 'sellable': 0}


def plan_for(bars, model_id='M4', params=None, **kw):
    kw.setdefault('today', bars[-1].date)
    kw.setdefault('config_hash', CFG.fingerprint())
    return next_plan(bars, MODELS[model_id], params or {}, COST, CFG.capital, **kw)


# ================= BUY-01 交易日历与时序 =================
def test_calendar_skips_weekend():
    cal = TradingCalendar(weekdays('2026-03-02', 10))
    nxt, info = cal.next_session('2026-03-06')          # 周五
    assert nxt == '2026-03-09' and info['confirmed'] is True      # 下周一，不是周六
    assert cal.is_session('2026-03-07') is False


def test_calendar_uses_dataset_holidays():
    dates = weekdays('2026-09-28', 8, skip={f'2026-10-0{i}' for i in range(1, 8)})
    cal = TradingCalendar(dates)
    assert cal.is_session('2026-10-02') is False        # 覆盖区间内缺席 → 休市
    nxt, info = cal.next_session('2026-09-30')
    assert nxt == '2026-10-08' and info['confirmed'] is True


def test_calendar_unknown_year_is_not_confirmed():
    cal = TradingCalendar(weekdays('2026-03-02', 5))
    nxt, info = cal.next_session('2026-03-06')
    assert nxt == '2026-03-09' and info['confirmed'] is False
    assert info['source'] == 'weekday_estimate'


def test_calendar_holiday_file_makes_future_session_confirmed():
    cal = TradingCalendar(weekdays('2026-03-02', 5),
                          holidays={2026: {_dt.date(2026, 3, 9)}})
    nxt, info = cal.next_session('2026-03-06')
    assert nxt == '2026-03-10' and info['confirmed'] is True


def test_plan_execution_session_is_next_trading_day_not_next_calendar_day():
    bars = rising(40)
    trailing = sum(b.adj_close for b in bars[-2:]) / 2
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    plan = plan_for(bars, 'M4', p)
    assert plan['signal']['status'] == 'confirmed'
    assert bars[-1].adj_close > trailing
    # 2026-03-02 起 40 个工作日 → 末日落在工作日；执行日必须是「下一交易日」
    last = _dt.date.fromisoformat(bars[-1].date)
    expect = last + _dt.timedelta(days=1)
    while expect.weekday() >= 5:
        expect += _dt.timedelta(days=1)
    assert plan['execution']['earliest_execution_session'] == expect.isoformat()
    assert plan['execution']['mode'] == 'next_open'


def test_plan_watching_checks_next_close_then_executes_after():
    bars = flat(40)                                  # 收盘 == MA3 → 严格大于不成立
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    plan = plan_for(bars, 'M4', p, account=FLAT_ACCOUNT)
    assert plan['signal']['status'] == 'not_confirmed'
    assert plan['status'] == 'watching'
    check = plan['execution']['next_check_session']
    assert check and plan['execution']['earliest_execution_session'] > check
    assert plan['execution']['mode'] == 'awaiting_confirmation'
    assert plan['execution']['quantity_estimate']['shares'] > 0          # 假设性数量
    assert plan['execution']['quantity_estimate']['hypothetical'] is True


def test_plan_threshold_is_close_condition_not_open_price():
    bars = rising(40)
    plan = plan_for(bars, 'M4', {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12})
    trig = plan['execution']['trigger']
    assert trig['classification'] == 'single_threshold'
    assert '收盘' in trig['notes'][0]
    assert plan['execution']['quantity_estimate']['price_basis'].startswith('as_of 收盘')


# ================= BUY-02 状态与原因 =================
def test_status_data_unavailable_on_short_history():
    bars = rising(6)[:2]
    plan = plan_for(bars, 'M4', {'entry': 'ma_cross', 'ma_n': 20})
    assert plan['status'] == 'data_unavailable'
    assert plan['reason_codes'][0]['code'] == 'insufficient_history'
    assert plan['execution']['eligibility'] == 'n/a'


def test_status_expired_when_data_stale_and_research_mode_keeps_it():
    bars = jump(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    stale_today = (_dt.date.fromisoformat(bars[-1].date) + _dt.timedelta(days=10)).isoformat()
    plan = plan_for(bars, 'M4', p, today=stale_today, account=FLAT_ACCOUNT)
    assert plan['status'] == 'expired'
    assert plan['validity']['expired'] is True
    assert plan['reason_codes'][0]['code'] == 'stale_data'

    research = plan_for(bars, 'M4', p, today=stale_today, research=True, account=FLAT_ACCOUNT)
    assert research['status'] == 'signal_confirmed'
    assert research['validity']['mode'] == 'research'
    assert research['context']['data_status']['stale'] is True


def test_status_blocked_when_cash_below_one_lot():
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    plan = plan_for(bars, 'M4', p, account={'mode': 'manual_actual', 'cash': 100, 'shares': 0})
    assert plan['status'] == 'blocked'
    assert any(r['code'] == 'insufficient_cash' for r in plan['reason_codes'])
    assert plan['execution']['lifecycle'] == 'blocked'


def test_status_holding_when_simulated_ledger_holds():
    bars = rising(40)
    plan = plan_for(bars, 'M4', {'entry': 'ma_cross', 'ma_n': 3, 'tp': 30, 'sl': 30, 'min_hold': 1})
    if plan['holding']:
        assert plan['status'] == 'holding'
        assert plan['execution']['lifecycle'] == 'not_applicable'


def test_status_blocked_when_signal_never_allowed():
    bars = sustained(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    plan = plan_for(bars, 'M4', p, rules={'follow_from': 'new_signal_only'},
                    account=FLAT_ACCOUNT)
    # 该前缀上信号已连续成立 → 「只等新信号」不得入场
    assert plan['status'] == 'watching'
    assert plan['reason_codes'][0]['code'] == 'existing_signal_not_allowed'


# ================= BUY-03 信号复用与参数合并 =================
@pytest.mark.parametrize('entry,kw', [
    ('ma_cross', {'ma_n': 3}),
    ('breakout', {'lookback': 5}),
    ('pullback', {'ma_n': 3, 'ma_long': 8}),
    ('volume_confirm', {'ma_n': 3, 'vol_n': 5, 'vol_ratio': 1.1}),
    ('momentum', {'mom_n': 5, 'mom_th': 1.0}),
])
def test_plan_signal_matches_engine_on_every_prefix(entry, kw):
    bars = rising(60)
    p = {'entry': entry, 'tp': 9, 'sl': 12, **kw}
    for t in range(15, len(bars), 3):
        prefix = bars[:t + 1]
        expected = entry_signal(prefix, {**MODELS['M4'].defaults, **p})[-1] == 1
        plan = plan_for(prefix, 'M4', p)
        assert (plan['signal']['status'] == 'confirmed') == expected, (entry, t)


def test_plan_uses_model_defaults_not_hardcoded_values():
    bars = rising(40)
    explicit = {**MODELS['M3'].defaults}
    p1 = plan_for(bars, 'M3', {})
    p2 = plan_for(bars, 'M3', explicit)
    assert p1['signal']['status'] == p2['signal']['status']
    assert p1['identity']['plan_id'] == p2['identity']['plan_id']
    # M3 默认 ma_n=5，旧实现按 20 推阈值会得到完全不同的数
    thr = p1['execution']['trigger']['threshold_price']
    closes = [b.adj_close for b in bars]
    assert thr == tick_up(sum(closes[-4:]) / 4)


def test_rule_plan_signal_matches_engine():
    bars = rising(50)
    rule = {'$type': 'group', 'op': 'all', 'children': [
        {'$type': 'condition', 'left': 'close', 'cmp': 'gt',
         'right_indicator': 'sma5', 'right_constant': None,
         'temporal': 'current', 'periods': 1},
        {'$type': 'condition', 'left': 'daily_return', 'cmp': 'gt',
         'right_indicator': None, 'right_constant': 0.0,
         'temporal': 'current', 'periods': 1},
    ]}
    p = {'rule': rule, 'tp': 9, 'sl': 12}
    for t in range(12, len(bars), 4):
        plan = plan_for(bars[:t + 1], 'M9', p)
        expected = bool(MODELS['M9'].target(bars[:t + 1], {**MODELS['M9'].defaults, **p})[-1])
        assert (plan['signal']['status'] == 'confirmed') == expected


# ================= BUY-04 条件树与阈值精度 =================
def test_condition_tree_reports_values_and_missing_conditions():
    bars = rising(40)
    plan = plan_for(bars, 'M4', {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12})
    node = plan['signal']['condition_tree']
    assert node['satisfied'] is True
    assert node['left']['value'] == pytest.approx(bars[-1].adj_close)
    assert node['right']['value'] is not None
    assert plan['signal']['missing_conditions'] == []


def test_threshold_is_tick_adjusted_and_verified_by_engine():
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3}
    trig = build_trigger(bars, MODELS['M4'], {**MODELS['M4'].defaults, **p})
    thr = trig['threshold_price']
    closes = [b.adj_close for b in bars]
    analytic = sum(closes[-2:]) / 2
    assert thr >= analytic and round(thr - analytic, 4) < 0.02
    # 严格大于：thr 本身触发，差一个价位不触发
    assert MODELS['M4'].target(bars + [_synth(bars, thr)], {**MODELS['M4'].defaults, **p})[-1] == 1
    assert MODELS['M4'].target(bars + [_synth(bars, round(thr - 0.01, 2))],
                               {**MODELS['M4'].defaults, **p})[-1] == 0


def test_pullback_trigger_is_interval_with_bounds():
    bars = rising(40, ma_n=3)
    p = {**MODELS['M4'].defaults, 'entry': 'pullback', 'ma_n': 5, 'ma_long': 20}
    trig = build_trigger(bars, MODELS['M4'], p)
    assert trig['classification'] in ('interval', 'unsatisfiable')
    if trig['classification'] == 'interval':
        iv = trig['intervals'][0]
        assert iv['lo'] < iv['hi'] and iv['lo_open'] is True and iv['hi_open'] is False
    else:
        assert trig['threshold_price'] is None
        assert any('区间为空' in n for n in trig['notes'])


def test_volume_condition_is_never_treated_as_satisfied():
    bars = rising(40, ma_n=3)
    p = {**MODELS['M4'].defaults, 'entry': 'volume_confirm', 'ma_n': 3,
         'vol_n': 5, 'vol_ratio': 3.0}
    trig = build_trigger(bars, MODELS['M4'], p)
    assert trig['classification'] == 'conditional_on_volume'
    assert trig['unknown_components'] == ['volume']
    assert trig['price_condition_only'] is True
    assert any('成交量' in r['text'] for r in trig['requirements'])


def test_momentum_threshold_uses_correct_base_bar():
    bars = rising(40)
    mn, th = 10, 3.0
    p = {**MODELS['M4'].defaults, 'entry': 'momentum', 'mom_n': mn, 'mom_th': th}
    trig = build_trigger(bars, MODELS['M4'], p)
    closes = [b.adj_close for b in bars]
    anchor = closes[len(bars) - mn]          # 下一根 i = n 的基准 closes[n-mn]
    assert trig['threshold_price'] == tick_up(anchor * (1 + th / 100.0))


def test_momentum_threshold_is_percent_not_multiple():
    """回归：旧实现把 3% 当 3 倍比较（roc > 3），任何正阈值永不触发。"""
    bars = rising(60)
    p = {**MODELS['M4'].defaults, 'entry': 'momentum', 'mom_n': 5, 'mom_th': 1.0}
    sig = entry_signal(bars, p)
    assert sum(sig) > 0


def test_custom_rule_multi_interval_is_not_collapsed():
    bars = rising(40)
    rule = {'$type': 'group', 'op': 'any', 'children': [
        {'$type': 'condition', 'left': 'close', 'cmp': 'lt',
         'right_indicator': None, 'right_constant': 9.0, 'temporal': 'current', 'periods': 1},
        {'$type': 'condition', 'left': 'close', 'cmp': 'gt',
         'right_indicator': None, 'right_constant': 20.0, 'temporal': 'current', 'periods': 1},
    ]}
    p = {**MODELS['M9'].defaults, 'rule': rule}
    trig = build_trigger(bars, MODELS['M9'], p)
    assert trig['classification'] in ('multi_interval', 'outside_search_range')
    if trig['classification'] == 'multi_interval':
        assert len(trig['intervals']) == 2
        assert trig['threshold_price'] is None
    assert trig['threshold_price'] is None


def test_default_rule_plan_threshold_matches_rule_engine():
    bars = flat(40)          # 收盘 == MA20：下一根只要略高于均线即构成「上穿」
    trig = build_trigger(bars, MODELS['M9'], {**MODELS['M9'].defaults, 'rule': default_rule()})
    assert trig['classification'] in ('single_threshold', 'interval')
    assert trig['threshold_price'] is not None
    p = {**MODELS['M9'].defaults, 'rule': default_rule()}
    assert MODELS['M9'].target(bars + [_synth(bars, trig['threshold_price'])], p)[-1] == 1


# ================= BUY-05 账户隔离 =================
def test_manual_account_is_not_overwritten_by_backtest_ledger():
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 30, 'sl': 30, 'min_hold': 1}
    sim = plan_for(bars, 'M4', p)
    manual = plan_for(bars, 'M4', p, account={'mode': 'manual_actual', 'cash': 100000,
                                              'shares': 0, 'sellable': 0,
                                              'snapshot_at': bars[-1].date})
    assert sim['context']['account_mode'] == 'strategy_simulated'
    assert manual['context']['account_mode'] == 'manual_actual'
    if sim['holding']:
        assert manual['holding'] is False
        assert manual['status'] == 'signal_confirmed'
    assert manual['context']['account_snapshot']['source'].startswith('用户手动输入')


def test_manual_holding_blocks_buy_and_addon_is_explicitly_unsupported():
    bars = jump(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    plan = plan_for(bars, 'M4', p, account={'mode': 'manual_actual', 'cash': 5000,
                                            'shares': 1000, 'sellable': 1000,
                                            'cost_price': 9.5})
    assert plan['status'] == 'holding' and plan['holding'] is True
    assert plan['execution']['quantity_estimate']['shares'] == 0

    addon = plan_for(bars, 'M4', p, account={'mode': 'manual_actual', 'cash': 5000,
                                             'shares': 1000, 'sellable': 1000,
                                             'cost_price': 9.5, 'allow_addon': True})
    assert addon['status'] == 'blocked'
    assert addon['reason_codes'][0]['code'] == 'addon_not_supported'


def test_manual_account_validation():
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3}
    with pytest.raises(ValueError):
        plan_for(bars, 'M4', p, account={'mode': 'manual_actual', 'cash': 1000})
    with pytest.raises(ValueError):
        plan_for(bars, 'M4', p, account={'mode': 'manual_actual', 'cash': 1000,
                                         'shares': 100, 'sellable': 200})
    with pytest.raises(ValueError):
        plan_for(bars, 'M4', p, account={'mode': 'weird'})


# ================= BUY-06 与回测一致性 =================
def test_confirmed_plan_executes_exactly_where_backtest_buys():
    """计划说「下一交易日开盘买」，回测就必须在同一根开盘成交，价格含滑点。"""
    bars = zigzag(90)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'min_hold': 1, 'max_hold': 100}
    checked = 0
    for t in range(8, len(bars) - 1):
        prefix = bars[:t + 1]
        plan = plan_for(prefix, 'M3', p, account=FLAT_ACCOUNT)
        if plan['status'] != 'signal_confirmed':
            continue
        nxt = plan['execution']['earliest_execution_session']
        if nxt != bars[t + 1].date or not bars[t + 1].can_buy:
            continue
        r = run(bars[:t + 2], MODELS['M3'], p, COST, CFG.capital,
                liquidation_policy='mark_to_market')
        if r.position[t] != 0 or r.position[t + 1] != 1:
            continue
        # 期末仍持仓 → 成交价就是 final 里的 entry（买入不发生平仓，不会出现在 trades 里）
        assert r.final['entry_date'] == nxt
        assert r.final['entry_price'] == pytest.approx(round(bars[t + 1].adj_open, 4))
        checked += 1
    assert checked >= 3, f'可核对样本过少（{checked}）'


def test_plan_never_claims_filled_before_execution():
    """计划生成时成交尚未发生：lifecycle 只能是 planned，不能出现 filled。"""
    bars = jump(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    plan = plan_for(bars, 'M4', p, account=FLAT_ACCOUNT)
    assert plan['status'] == 'signal_confirmed'
    assert plan['execution']['lifecycle'] == 'planned'
    assert 'filled' not in str(plan['execution']['lifecycle'])


def test_plan_quantity_matches_order_size_with_next_open():
    bars = mk_bars(weekdays('2026-03-02', 40), [10.0] * 38 + [11.0, 11.2])
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 40, 'sl': 40, 'min_hold': 1}
    t = len(bars) - 2
    prefix = bars[:t + 1]
    plan = plan_for(prefix, 'M4', p, account=FLAT_ACCOUNT)
    assert plan['status'] == 'signal_confirmed'
    cash = plan['context']['account_snapshot']['cash']
    px = COST.slip_buy(bars[t + 1].adj_open)
    qty, amount, fee = order_size(cash, px, COST)
    r = run(bars[:t + 2], MODELS['M4'], p, COST, CFG.capital, liquidation_policy='mark_to_market')
    if r.trades:
        assert r.trades[0].buy_price == pytest.approx(round(px, 2))
    # 计划的数量是按参考价估的：只要求「可复算 + 现金流不为负」
    est = plan['execution']['quantity_estimate']
    assert est['shares'] % 100 == 0
    assert est['amount'] + est['estimated_fees'] <= cash + 1e-6


def test_plan_signal_and_backtest_agree_on_no_entry_when_not_confirmed():
    bars = rising(60)
    p = {'entry': 'ma_cross', 'ma_n': 30, 'tp': 40, 'sl': 40, 'min_hold': 1}
    for t in range(35, len(bars) - 1, 5):
        plan = plan_for(bars[:t + 1], 'M4', p)
        if plan['signal']['status'] != 'not_confirmed':
            continue
        r = run(bars[:t + 2], MODELS['M4'], p, COST, CFG.capital,
                liquidation_policy='mark_to_market')
        assert not any(tr.buy_i == t + 1 for tr in r.trades)


# ================= BUY-07 数量 / 费用 / 风险预算 =================
def test_quantity_respects_cash_after_all_fees():
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    for cap in (2000, 10000, 100000):
        cfg = ExecutionConfig(capital=cap, commission=0.003, min_commission=5)
        plan = next_plan(bars, MODELS['M4'], p, cfg.cost(), cap,
                         today=bars[-1].date, config_hash=cfg.fingerprint())
        q = plan['execution']['quantity_estimate']
        if q['shares'] == 0:
            continue
        assert q['amount'] + q['estimated_fees'] <= cap + 1e-6
        assert q['cash_after'] >= -1e-9


def test_max_position_pct_caps_budget():
    bars = jump(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    full = plan_for(bars, 'M4', p, account=FLAT_ACCOUNT)
    half = plan_for(bars, 'M4', p, rules={'max_position_pct': 50}, account=FLAT_ACCOUNT)
    assert half['execution']['quantity_estimate']['budget'] == pytest.approx(50000.0)
    assert half['execution']['quantity_estimate']['shares'] <= full['execution']['quantity_estimate']['shares']
    assert half['execution']['quantity_estimate']['binding_constraint'] == 'max_position'


def test_risk_budget_caps_quantity_and_carries_warning():
    bars = jump(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 5}
    plan = plan_for(bars, 'M4', p, rules={'risk_pct': 1.0}, account=FLAT_ACCOUNT)
    q = plan['execution']['quantity_estimate']
    if q['risk']:
        expect = int((CFG.capital * 0.01) / (q['reference_price'] * 0.05) // 100) * 100
        assert q['shares'] == expect
        assert '跳空' in q['risk']['note']


def test_insufficient_lot_never_negative_cash():
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    cfg = ExecutionConfig(capital=1000, min_commission=50)
    plan = next_plan(bars, MODELS['M4'], p, cfg.cost(), cfg.capital,
                     today=bars[-1].date, config_hash=cfg.fingerprint())
    q = plan['execution']['quantity_estimate']
    assert q['cash_after'] >= 0
    assert q['shares'] % 100 == 0


# ================= BUY-08 价格边界 =================
def test_price_bound_violation_cancels_plan():
    bars = jump(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    ref = bars[-1].adj_close
    plan = plan_for(bars, 'M4', p, rules={'max_price': round(ref * 0.9, 2)},
                    account=FLAT_ACCOUNT)
    assert plan['status'] == 'blocked'
    assert any(r['code'] == 'price_bound_exceeded' for r in plan['reason_codes'])
    assert plan['execution']['lifecycle'] == 'cancelled'
    assert plan['execution']['price_bounds']['violated'] is True


def test_deviation_cap_uses_signal_price():
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    trig = plan_for(bars, 'M4', p, account=FLAT_ACCOUNT)['execution']['trigger']['threshold_price']
    plan = plan_for(bars, 'M4', p, rules={'max_deviation_pct': 1.0}, account=FLAT_ACCOUNT)
    assert plan['execution']['price_bounds']['deviation_cap'] == pytest.approx(
        round(trig * 1.01, 2), abs=0.01)


def test_pending_checks_always_list_unknown_execution_variables():
    bars = rising(40)
    plan = plan_for(bars, 'M4', {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12})
    txt = ' '.join(plan['execution']['pending_checks'])
    assert '开盘价' in txt and '涨停' in txt


# ================= BUY-09 价格口径 =================
def test_price_mapping_documents_basis_and_corporate_action():
    bars = rising(40)
    bars[20].factor = 1.2                     # 模拟一次除权导致因子变化
    plan = plan_for(bars, 'M4', {'entry': 'ma_cross', 'ma_n': 3})
    mapping = plan['context']['data_status']
    assert mapping['calendar']['sessions'] == 40
    prot = plan['protection']
    assert prot['basis'] in ('estimated_from_reference', 'estimated_from_trigger', 'confirmed_fill')
    assert prot.get('tp_price') is None or prot['tp_price'] > 0


def test_protection_recomputed_from_trigger_price_when_not_filled():
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 10, 'sl': 5}
    plan = plan_for(bars, 'M4', p)
    if plan['status'] == 'signal_confirmed':
        prot = plan['protection']
        ep = prot['estimated_entry_price']
        assert prot['tp_price'] == pytest.approx(round(ep * 1.10, 2), abs=0.01)
        assert prot['sl_price'] == pytest.approx(round(ep * 0.95, 2), abs=0.01)


# ================= 第九轮：数量/费用/风险/保护价同一价格情景 =================
def test_quantity_protection_share_one_estimate_price():
    """未确认计划：数量、费用、保护价必须用同一个估算入场价（触发价），不能各算一套。"""
    bars = flat(40)                                  # 收盘 == MA3 → watching，触发价 10.01
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    plan = plan_for(bars, 'M4', p, account=FLAT_ACCOUNT)
    assert plan['status'] == 'watching'
    q = plan['execution']['quantity_estimate']
    prot = plan['protection']
    assert q['entry_price_basis'] == 'trigger_price'
    assert q['entry_price_estimate'] == pytest.approx(10.01, abs=0.01)
    assert q['price_basis'].startswith('触发价')
    assert prot['estimated_entry_price'] == q['entry_price_estimate']
    assert prot['entry_price_basis'] == 'trigger_price'
    assert prot['tp_price'] == pytest.approx(round(q['entry_price_estimate'] * 1.09, 2), abs=0.01)
    assert prot['sl_price'] == pytest.approx(round(q['entry_price_estimate'] * 0.88, 2), abs=0.01)
    # 数量在估算价下必须买得起：旧实现按收盘价算，换成真实要付的触发价就超预算
    assert q['amount'] + q['estimated_fees'] <= q['budget'] + 1e-6
    assert q['shares'] % 100 == 0


def test_price_scenarios_expose_close_vs_estimate_gap():
    """两种价格情景都要摆出来：各自能买多少、拿「计划数量」去另一个价买会不会超预算。"""
    bars = flat(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    plan = plan_for(bars, 'M4', p,
                    account={'mode': 'manual_actual', 'cash': 100000, 'shares': 0})
    q = plan['execution']['quantity_estimate']
    sc = {s['key']: s for s in q['scenarios']}
    assert set(sc) == {'close', 'estimate'}
    assert sc['estimate']['used'] is True and sc['close']['used'] is False
    # 触发价更高 → 同样现金买得更少；两个情景各自都在预算内
    assert sc['estimate']['shares'] < sc['close']['shares']
    for s in sc.values():
        assert s['amount'] + s['fees'] <= q['budget'] + 1e-6
        assert s['cash_after'] >= -1e-9
    # 旧口径的错法：用收盘价算出的数量，换成触发价去下单金额会变大（甚至吃掉现金缓冲）
    assert sc['estimate']['price'] > sc['close']['price']
    assert sc['close']['shares'] * sc['estimate']['price'] > q['amount']
    assert q['amount'] + q['estimated_fees'] <= q['budget'] + 1e-6


def test_estimated_plan_quantity_is_affordable_at_any_scenario_price():
    """不变式：计划数量 × 任一情景价格 + 费用，都不能让现金变负（除了明确标注的超预算）。"""
    bars = flat(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    for cash in (8000, 20000, 100000):
        plan = plan_for(bars, 'M4', p,
                        account={'mode': 'manual_actual', 'cash': cash, 'shares': 0})
        q = plan['execution']['quantity_estimate']
        if not q['shares']:
            continue
        for s in q['scenarios']:
            cost = q['shares'] * s['price'] + s['fees']
            assert cost <= q['budget'] + 1e-6 or s['plan_qty_over_budget']


def test_price_cap_bound_uses_same_estimate_price():
    """上限比的是「实际要付的价」：收盘在限价内、触发价超限也要报出来。"""
    bars = flat(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    tight = plan_for(bars, 'M4', p, rules={'max_price': 10.00}, account=FLAT_ACCOUNT)
    assert tight['execution']['price_bounds']['violated'] is True
    assert tight['status'] == 'watching'          # 未确认计划只提示，不谎称已通过
    loose = plan_for(bars, 'M4', p, rules={'max_price': 10.50}, account=FLAT_ACCOUNT)
    assert loose['execution']['price_bounds']['violated'] is False


def test_research_mode_marks_dates_as_historical():
    """研究模式回放的是历史情景：执行日/有效期不得指向「下一笔待执行订单」。"""
    bars = jump(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    stale_today = (_dt.date.fromisoformat(bars[-1].date) + _dt.timedelta(days=10)).isoformat()
    plan = plan_for(bars, 'M4', p, today=stale_today, research=True, account=FLAT_ACCOUNT)
    assert plan['execution']['historical'] is True
    assert plan['status_text'].startswith('历史情景')
    assert plan['validity']['mode'] == 'research'
    live = plan_for(bars, 'M4', p, today=bars[-1].date, account=FLAT_ACCOUNT)
    assert live['execution']['historical'] is False
    assert not live['status_text'].startswith('历史情景')


# ================= BUY-10 可复现 / 失效 =================
def test_plan_id_is_deterministic_and_sensitive_to_inputs():
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    a = plan_for(bars, 'M4', p, dataset={'id': 1, 'data_hash': 'hash1', 'code': '600000'})
    b = plan_for(bars, 'M4', p, dataset={'id': 1, 'data_hash': 'hash1', 'code': '600000'})
    assert a['validity']['plan_id'] == b['validity']['plan_id']
    c = plan_for(bars, 'M4', {**p, 'tp': 11}, dataset={'id': 1, 'data_hash': 'hash1', 'code': '600000'})
    assert c['validity']['plan_id'] != a['validity']['plan_id']
    d = plan_for(bars, 'M4', p, dataset={'id': 1, 'data_hash': 'hash2', 'code': '600000'})
    assert d['validity']['plan_id'] != a['validity']['plan_id']


def test_validity_lists_invalidation_triggers():
    bars = rising(40)
    plan = plan_for(bars, 'M4', {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12})
    v = plan['validity']
    assert v['valid_until_session']
    assert '新交易日线到达' in v['invalidated_by']
    assert v['data_hash'] == ''      # 未传 dataset 时不伪造哈希


# ================= 第十二轮：约束边界（D01～D04） =================
def test_max_position_pct_zero_is_rejected_not_silently_full():
    """D01：0 是用户输入，不是「没填」。旧实现 `or 100.0` 把它静默变成满仓。"""
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    with pytest.raises(ValueError, match='max_position_pct'):
        plan_for(bars, 'M4', p, rules={'max_position_pct': 0}, account=FLAT_ACCOUNT)
    for bad in (-1, 100.1, float('inf'), float('nan')):
        with pytest.raises(ValueError):
            plan_for(bars, 'M4', p, rules={'max_position_pct': bad}, account=FLAT_ACCOUNT)
    # 只有「不传 / None」才采用默认 100%
    full = plan_for(bars, 'M4', p, rules={}, account=FLAT_ACCOUNT)
    assert full['execution']['quantity_estimate']['budget'] == pytest.approx(100000.0)
    explicit = plan_for(bars, 'M4', p, rules={'max_position_pct': None}, account=FLAT_ACCOUNT)
    assert explicit['execution']['quantity_estimate']['budget'] == pytest.approx(100000.0)


def test_price_bound_blocked_plan_has_zero_quantity():
    """D02：被价格上限阻止的计划，下单数量必须是 0；购买力只能作为独立字段出现。"""
    bars = jump(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    plan = plan_for(bars, 'M4', p, rules={'max_price': 1.0}, account=FLAT_ACCOUNT)
    assert plan['status'] == 'blocked'
    assert plan['execution']['price_bounds']['violated'] is True
    q = plan['execution']['quantity_estimate']
    assert q['shares'] == 0 and q['lots'] == 0
    assert q['amount'] == 0 and q['estimated_fees'] == 0
    assert q['binding_constraint'] == 'price_bound'
    for s in q['scenarios']:
        assert s['shares'] == 0 and s['amount'] == 0 and s['fees'] == 0
        assert s['cash_power_shares'] >= 0        # 购买力单列，不冒充下单数量
    assert any(r['code'] == 'price_bound_exceeded' for r in plan['reason_codes'])


def test_risk_budget_zero_reason_is_risk_not_cash():
    """D03：risk_pct=0 折算不足一手时，原因必须是风险预算，而不是谎称现金不足。"""
    bars = jump(40)
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}
    plan = plan_for(bars, 'M4', p, rules={'risk_pct': 0}, account=FLAT_ACCOUNT)
    assert plan['status'] == 'blocked'
    codes = [r['code'] for r in plan['reason_codes']]
    assert 'risk_budget_below_one_lot' in codes
    assert 'insufficient_cash' not in codes
    q = plan['execution']['quantity_estimate']
    assert q['shares'] == 0 and q['binding_constraint'] == 'risk_budget'
    hint = [r['text'] for r in plan['reason_codes'] if r['code'] == 'risk_budget_below_one_lot'][0]
    assert '风险预算' in hint and '50000' not in hint


def test_rules_reject_negative_and_nonfinite_values():
    bars = rising(40)
    p = {'entry': 'ma_cross', 'ma_n': 3}
    for bad in ({'risk_pct': -1}, {'risk_pct': float('inf')},
                {'max_price': -3}, {'max_price': 0}, {'max_price': float('nan')},
                {'max_deviation_pct': -2}, {'follow_from': 'weird'}):
        with pytest.raises(ValueError):
            plan_for(bars, 'M4', p, rules=bad, account=FLAT_ACCOUNT)


def test_scenario_applies_risk_budget_and_used_row_matches_plan():
    """D04：情景表套用与主计划相同的风险预算；「采用」行数量必须等于主计划数量。"""
    bars = flat(40)                       # watching，触发价 10.01 作预算假设价
    p = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 5}
    plan = plan_for(bars, 'M4', p, rules={'risk_pct': 0.5}, account=FLAT_ACCOUNT)
    q = plan['execution']['quantity_estimate']
    assert q['binding_constraint'] == 'risk_budget' and q['shares'] > 0
    used = [s for s in q['scenarios'] if s['used']][0]
    assert used['shares'] == q['shares']
    # 每一档都受同一风险预算约束；纯现金购买力只作为对照字段，不得小于计划数量
    for s in q['scenarios']:
        per_share = s['price'] * 0.05
        cap = int((CFG.capital * 0.005) / per_share // 100) * 100
        assert s['shares'] <= cap
        assert s['cash_power_shares'] >= s['shares']
    # 未启用风险预算时，情景表数量回到纯现金购买力（约束不是凭空加的）
    free = plan_for(bars, 'M4', p, account=FLAT_ACCOUNT)
    fq = free['execution']['quantity_estimate']
    for s in fq['scenarios']:
        assert s['shares'] == s['cash_power_shares']


# ================= 兼容层 =================
def test_legacy_fields_still_present():
    bars = rising(40)
    plan = plan_for(bars, 'M4', {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12})
    for k in ('as_of_date', 'tradable', 'holding', 'entry_condition', 'threshold_price',
              'last_close', 'position'):
        assert k in plan
    assert set(plan['position']) == {'cash', 'shares', 'market_value', 'equity_value', 'equity_ratio'}


class _Oracle(Model):
    id, name, color, tradable = 'X2', 'oracle', '#000', False
    uses_tp_sl, exit_on_signal, defaults, param_schema = False, False, {}, []

    def target(self, bars, p):
        return [0] * len(bars)


def test_untradable_model_is_blocked():
    bars = flat(40)
    plan = next_plan(bars, _Oracle(), {}, COST, CFG.capital, today=bars[-1].date)
    assert plan['status'] == 'blocked'
    assert plan['reason_codes'][0]['code'] == 'model_not_tradable'


# ---------------- 辅助 ----------------
def _synth(bars, price, volume=None):
    import dataclasses
    last = bars[-1]
    return dataclasses.replace(
        last, open=price, high=price, low=price, close=price,
        adj_open=price, adj_high=price, adj_low=price, adj_close=price,
        volume=(float(last.volume) if volume is None else float(volume)))


# ================= API 契约 =================
SAMPLE_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'sample_600186.csv')


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import core.state as state
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'plan.db')
    state.reset()
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        r = c.post('/api/datasets/seed', json={'path': SAMPLE_CSV, 'name': 'plan'})
        assert r.status_code == 200, r.text
        ds = c.get('/api/datasets').json()['data'][0]
        yield c, ds
    state.reset()


def test_api_plan_returns_full_contract(client):
    c, ds = client
    import json as _json
    code = _json.loads(ds['codes'])[0]
    r = c.post('/api/plan', json={'dataset_id': ds['id'], 'code': code, 'model_id': 'M4',
                                  'params': {'entry': 'ma_cross', 'ma_n': 20, 'tp': 9, 'sl': 12}})
    assert r.status_code == 200, r.text
    d = r.json()['data']
    for k in ('status', 'reason_codes', 'identity', 'context', 'signal', 'execution',
              'protection', 'validity', 'evidence', 'as_of_date', 'config_hash'):
        assert k in d, k
    assert d['status'] in ('watching', 'signal_confirmed', 'blocked', 'holding', 'expired')
    assert d['context']['account_mode'] == 'strategy_simulated'
    assert d['signal']['condition_tree'] is not None


def test_api_plan_manual_account_and_research(client):
    c, ds = client
    import json as _json
    code = _json.loads(ds['codes'])[0]
    body = {'dataset_id': ds['id'], 'code': code, 'model_id': 'M4', 'params': {'ma_n': 20},
            'account': {'mode': 'manual_actual', 'cash': 50000, 'shares': 0, 'sellable': 0,
                        'snapshot_at': '2026-01-05'},
            'research': True}
    r = c.post('/api/plan', json=body)
    assert r.status_code == 200, r.text
    d = r.json()['data']
    assert d['context']['account_mode'] == 'manual_actual'
    assert d['context']['account_snapshot']['cash'] == 50000
    assert d['validity']['research_mode'] is True


def test_api_plan_rejects_bad_account_and_unknown_model(client):
    c, ds = client
    import json as _json
    code = _json.loads(ds['codes'])[0]
    base = {'dataset_id': ds['id'], 'code': code, 'model_id': 'M4'}
    r = c.post('/api/plan', json={**base, 'account': {'mode': 'manual_actual', 'cash': 1000,
                                                     'shares': 100, 'sellable': 900}})
    assert r.status_code == 422
    r = c.post('/api/plan', json={**base, 'model_id': 'M99'})
    assert r.status_code == 422
    r = c.post('/api/plan', json={**base, 'dataset_id': 99999})
    assert r.status_code == 404


def test_api_plan_evidence_links_same_params_backtest_only(client):
    c, ds = client
    import json as _json
    code = _json.loads(ds['codes'])[0]
    body = {'dataset_id': ds['id'], 'codes': [code], 'models': [{'id': 'M4', 'params': {}}],
            'global_params': {'capital': 100000, 'commission': 0.00025, 'min_commission': 5,
                              'stamp_duty': 0.0005, 'transfer_fee': 0.00001, 'slippage_pct': 0}}
    assert c.post('/api/runs', json=body).status_code == 200
    r = c.post('/api/plan', json={'dataset_id': ds['id'], 'code': code,
                                  'model_id': 'M4', 'params': {}})
    d = r.json()['data']
    ev = d['evidence']
    assert ev['status'] == 'matched', ev
    assert ev['in_sample_only'] is True and ev['oos'] is None
    assert all(rec['in_sample_only'] for rec in ev['records'])
    assert '样本外' in ev['note']

    # 参数不同 → 不得绑定为验证依据
    r2 = c.post('/api/plan', json={'dataset_id': ds['id'], 'code': code,
                                   'model_id': 'M4', 'params': {'ma_n': 7}})
    assert r2.json()['data']['evidence']['status'] == 'not_found'


def test_api_plan_rejects_zero_position_and_bad_rules(client):
    """D01 API 层：0/越界/负数/非法枚举一律 422，不能落到「静默按满仓」的旧路径。"""
    import json as _json
    c, ds = client
    code = _json.loads(ds['codes'])[0]
    base = {'dataset_id': ds['id'], 'code': code, 'model_id': 'M4', 'params': {'ma_n': 20}}
    for rules in ({'max_position_pct': 0}, {'max_position_pct': 101},
                  {'max_position_pct': -10}, {'risk_pct': -1},
                  {'max_price': 0}, {'max_deviation_pct': -5},
                  {'follow_from': 'nope'}):
        r = c.post('/api/plan', json={**base, 'exec_rules': rules})
        assert r.status_code == 422, (rules, r.status_code, r.text)
    ok = c.post('/api/plan', json={**base, 'exec_rules': {'max_position_pct': 50}})
    assert ok.status_code == 200, ok.text
    assert ok.json()['data']['execution']['quantity_estimate']['budget'] > 0


# ================= F01 时点：盘前 / 盘中 / 收盘后 / 周末 / 节假日 =================
CN = _dt.timezone(_dt.timedelta(hours=8))
M4_PARAMS = {'entry': 'ma_cross', 'ma_n': 3, 'tp': 9, 'sl': 12}


def _plan_at(bars, now, **kw):
    kw.setdefault('account', FLAT_ACCOUNT)
    return plan_for(bars, 'M4', M4_PARAMS, now=now, **kw)


def test_plan_preopen_monday_keeps_friday_plan_alive():
    """F01：周五收盘的计划，周一开盘前仍是最新完整日线——不得因「今天已经是周一」作废。"""
    bars = jump(40)
    assert bars[-1].date == '2026-04-24' and _dt.date(2026, 4, 24).weekday() == 4
    plan = _plan_at(bars, _dt.datetime(2026, 4, 27, 9, 0, tzinfo=CN))
    ds = plan['context']['data_status']
    assert ds['latest_completed_session'] == '2026-04-24'
    assert ds['sessions_behind'] == 0 and ds['stale'] is False
    assert plan['status'] == 'signal_confirmed'
    assert plan['execution']['phase'] == 'pre_open'
    assert plan['execution']['window'] == 'open'
    assert plan['execution']['earliest_execution_session'] == '2026-04-27'
    assert plan['validity']['expired'] is False


def test_plan_intraday_still_reads_last_completed_bar():
    """F01：周一 10:30 时周一的日线还没走完，最新完整数据仍是周五；但开盘价已出，需按现价重算。"""
    bars = jump(40)
    plan = _plan_at(bars, _dt.datetime(2026, 4, 27, 10, 30, tzinfo=CN))
    ds = plan['context']['data_status']
    assert ds['stale'] is False and ds['latest_completed_session'] == '2026-04-24'
    assert plan['execution']['phase'] == 'in_session'
    assert plan['execution']['window'] == 'open'
    assert any('现价重算' in c for c in plan['execution']['pending_checks'])


def test_plan_after_close_on_next_session_marks_data_behind():
    """F01：周一 16:00 时周一的日线已该产生 → 仍停在周五才算「数据落后」。"""
    bars = jump(40)
    plan = _plan_at(bars, _dt.datetime(2026, 4, 27, 16, 0, tzinfo=CN))
    ds = plan['context']['data_status']
    assert ds['latest_completed_session'] == '2026-04-27'
    assert ds['sessions_behind'] == 1 and ds['stale'] is True
    assert plan['status'] == 'expired'
    assert plan['execution']['window'] == 'closed'
    assert plan['validity']['expired'] is True
    # 研究模式仍可看，但必须标成历史情景
    research = _plan_at(bars, _dt.datetime(2026, 4, 27, 16, 0, tzinfo=CN), research=True)
    assert research['status'] == 'signal_confirmed'
    assert research['validity']['mode'] == 'research'
    assert research['execution']['historical'] is True


def test_plan_weekend_is_not_stale():
    """F01：周六周日没有新日线，周五的数据就是最新完整数据。"""
    bars = jump(40)
    for now in (_dt.datetime(2026, 4, 25, 10, 0, tzinfo=CN),
                _dt.datetime(2026, 4, 26, 20, 0, tzinfo=CN)):
        plan = _plan_at(bars, now)
        assert plan['context']['data_status']['stale'] is False, now
        assert plan['context']['data_status']['latest_completed_session'] == '2026-04-24'
        assert plan['status'] == 'signal_confirmed', now


def test_plan_declared_holiday_is_not_stale():
    """F01：休市日（holidays 声明）不是「缺一根日线」。"""
    bars = jump(40)
    cal = TradingCalendar([b.date for b in bars], holidays={2026: {_dt.date(2026, 4, 27)}})
    plan = plan_for(bars, 'M4', M4_PARAMS, calendar=cal, account=FLAT_ACCOUNT,
                    now=_dt.datetime(2026, 4, 27, 16, 0, tzinfo=CN))
    ds = plan['context']['data_status']
    assert ds['latest_completed_session'] == '2026-04-24'
    assert ds['stale'] is False
    assert plan['status'] == 'signal_confirmed'
    # 执行日跳过休市日 → 周二，且已被休市安排确认（不是工作日估算）
    assert plan['execution']['earliest_execution_session'] == '2026-04-28'
    assert plan['execution']['execution_session_confirmed'] is True


def test_plan_old_dataset_is_still_stale_before_open():
    """F01 回归：数据集停在很久以前时，盘前也必须判落后（不能被「回退到 as_of」骗过）。"""
    bars = rising(40)[:12]                     # 停在 2026-03-17
    plan = _plan_at(bars, _dt.datetime(2026, 11, 4, 9, 0, tzinfo=CN))
    ds = plan['context']['data_status']
    assert ds['stale'] is True
    assert ds['sessions_behind'] > 100
    assert plan['status'] == 'expired'


def test_plan_date_only_today_means_after_close():
    """F01：只给日期（旧入口）按「该日收盘后」解释——这是研究/回放兼容语义，不表达盘前。"""
    bars = jump(40)
    plan = plan_for(bars, 'M4', M4_PARAMS, account=FLAT_ACCOUNT, today='2026-04-27')
    assert plan['context']['data_status']['now'].startswith('2026-04-27T15:30')
    assert plan['context']['data_status']['stale'] is True
    assert plan['status'] == 'expired'


# ================= F02 指纹：会改变结论的输入都必须在里面 =================
HOLD_ACCOUNT = {'mode': 'manual_actual', 'cash': 50000, 'shares': 100, 'sellable': 100,
                'cost_price': 10.0, 'entry_date': '2026-03-10'}


def test_plan_id_differs_when_allow_addon_changes_outcome():
    """F02：allow_addon 会把 holding 变成 blocked —— 指纹必须不同，否则去重会存出矛盾快照。"""
    bars = rising(40)
    off = plan_for(bars, 'M4', M4_PARAMS, account={**HOLD_ACCOUNT, 'allow_addon': False})
    on = plan_for(bars, 'M4', M4_PARAMS, account={**HOLD_ACCOUNT, 'allow_addon': True})
    assert off['status'] == 'holding'
    assert on['status'] == 'blocked'
    assert off['validity']['plan_id'] != on['validity']['plan_id']


def test_plan_id_covers_entry_date_rules_and_mode():
    """F02：entry_date（影响最小持仓）、执行规则、研究模式都必须改变身份。"""
    bars = rising(40)
    base = plan_for(bars, 'M4', {**M4_PARAMS, 'min_hold': 3}, account=HOLD_ACCOUNT)
    other_entry = plan_for(bars, 'M4', {**M4_PARAMS, 'min_hold': 3},
                           account={**HOLD_ACCOUNT, 'entry_date': '2026-04-01'})
    other_rules = plan_for(bars, 'M4', {**M4_PARAMS, 'min_hold': 3}, account=HOLD_ACCOUNT,
                           rules={'max_position_pct': 50})
    research = plan_for(bars, 'M4', {**M4_PARAMS, 'min_hold': 3}, account=HOLD_ACCOUNT,
                        research=True)
    ids = {p['validity']['plan_id'] for p in (base, other_entry, other_rules, research)}
    assert len(ids) == 4, ids
    same = plan_for(bars, 'M4', {**M4_PARAMS, 'min_hold': 3}, account=HOLD_ACCOUNT)
    assert same['validity']['plan_id'] == base['validity']['plan_id']


def test_saved_plan_snapshot_stays_consistent(client):
    """F02：落库后 status / account / rules 必须与 plan 快照一致；同输入复用不改写快照。"""
    c, ds = client
    import json as _json
    code = _json.loads(ds['codes'])[0]
    body = {'dataset_id': ds['id'], 'code': code, 'model_id': 'M4',
            'params': {**M4_PARAMS, 'min_hold': 3}, 'research': True}
    acct = {'mode': 'manual_actual', 'cash': 50000, 'shares': 100, 'sellable': 100,
            'cost_price': 10, 'entry_date': '2026-03-10'}
    r1 = c.post('/api/plans', json={**body, 'account': {**acct, 'allow_addon': False}})
    r2 = c.post('/api/plans', json={**body, 'account': {**acct, 'allow_addon': True}})
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    d1, d2 = r1.json()['data'], r2.json()['data']
    assert d1['record_id'] != d2['record_id'], 'allow_addon 不同却复用同一条记录'

    for d in (d1, d2):
        rec = c.get(f"/api/plans/{d['record_id']}").json()['data']['record']
        assert rec['status'] == rec['plan']['status']
        assert rec['account'] == rec['plan']['context']['account_snapshot']
        assert rec['rules'] == rec['plan']['context']['exec_rules']
        assert rec['rules']['max_position_pct'] == 100.0      # 规范化后的默认值
        assert rec['plan']['identity']['engine_version']

    again = c.post('/api/plans', json={**body, 'account': {**acct, 'allow_addon': False}}).json()['data']
    assert again['reused'] is True and again['record_id'] == d1['record_id']
    rec = c.get(f"/api/plans/{d1['record_id']}").json()['data']['record']
    assert rec['status'] == rec['plan']['status'] == d1['plan']['status']


# ================= F03 最小持仓：已持仓必须从真实入场日算 =================
def test_holding_min_hold_counts_from_entry_date():
    """F03：早已持满 min_hold 的账户，不能再显示一个未来的到期日。"""
    bars = rising(40)                          # as_of 2026-04-24
    plan = plan_for(bars, 'M4', {**M4_PARAMS, 'min_hold': 3}, account=HOLD_ACCOUNT)
    prot = plan['protection']
    assert prot['basis'] == 'confirmed_fill'
    assert prot['min_hold_basis'] == 'entry_date'
    assert prot['min_hold_entry_date'] == '2026-03-10'
    assert prot['min_hold_satisfied'] is True
    assert prot['min_hold_elapsed_sessions'] > 3
    assert prot['min_hold_until'] < plan['context']['as_of']
    assert '已满足' in prot['min_hold_note']


def test_holding_min_hold_recent_entry_not_yet_satisfied():
    """F03：刚买入的持仓到期日应为入场日之后的第 N 个交易日，且未满足。"""
    bars = jump(40)                            # as_of 2026-04-24（周五）
    plan = plan_for(bars, 'M4', {**M4_PARAMS, 'min_hold': 3},
                    account={**HOLD_ACCOUNT, 'entry_date': '2026-04-23'})
    prot = plan['protection']
    assert prot['min_hold_basis'] == 'entry_date'
    assert prot['min_hold_satisfied'] is False
    assert prot['min_hold_elapsed_sessions'] == 1          # 04-24 一天
    assert prot['min_hold_until'] == '2026-04-28'          # 04-27、04-28 → 第 3 个交易日
    assert '可卖出' in prot['min_hold_note']


def test_holding_without_entry_date_does_not_fake_min_hold():
    """F03：没有入场日期就不猜——不拿「下一笔买入日」冒充旧持仓起点。"""
    bars = rising(40)
    acct = {k: v for k, v in HOLD_ACCOUNT.items() if k != 'entry_date'}
    plan = plan_for(bars, 'M4', {**M4_PARAMS, 'min_hold': 3}, account=acct)
    prot = plan['protection']
    assert prot['min_hold_basis'] == 'unknown_entry_date'
    assert prot['min_hold_until'] is None
    assert prot['min_hold_satisfied'] is None
    assert '入场日期' in prot['min_hold_note']


def test_hypothetical_min_hold_uses_planned_entry():
    """F03：空仓候选仍按「假设买入日」估算，但必须标明这是假设。"""
    bars = rising(40)
    plan = plan_for(bars, 'M4', {**M4_PARAMS, 'min_hold': 3}, account=FLAT_ACCOUNT)
    prot = plan['protection']
    assert prot['min_hold_basis'] == 'hypothetical_entry'
    assert prot['min_hold_entry_date'] == plan['execution']['earliest_execution_session']
    assert prot['min_hold_satisfied'] is False


def test_plan_excludes_unfinished_and_future_bars():
    bars = jump(40)
    now = _dt.datetime(2026, 4, 24, 10, 30, tzinfo=CN)
    plan = _plan_at(bars, now)
    assert plan['as_of_date'] == bars[-2].date
    assert plan['signal']['evaluated_on'] == bars[-2].date
    assert plan['last_close'] == bars[-2].adj_close
