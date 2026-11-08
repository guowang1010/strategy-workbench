# -*- coding: utf-8 -*-
"""计划复盘（BUY-12）：计划说了什么 → 实际做了什么 → 结果差在哪。

**只读**：本模块绝不修改 `plan_record.plan` 里的计划快照。所有「后来才知道」的信息
（后续行情、实际成交、策略反事实）都放在复盘输出里，与原始计划并列展示，而不是回头
改写当时的判断——否则「这笔为什么买/为什么放弃」就永远查不清了。

三个层次：
  1. `fills_summary`：真金白银的账（买入均价、费用、已实现盈亏、剩余持仓）；
  2. `vs_plan`：计划 vs 实际（价格偏差 bp、数量偏差、费用差、未成交原因）；
  3. `counterfactual`：同一模型同参数在 as_of 之后严格跟随会怎样（对照，不是本计划的成绩）。
"""
from datetime import date

from db import repository
from engine.backtest import run
from engine.config import ExecutionConfig
from engine.models.builtin import MODELS

OUTCOME_TEXT = {
    'pending': '等待执行（执行日尚未到达或行情未更新）',
    'no_action': '未产生买入动作（计划当时就不建议买）',
    'not_filled': '未成交',
    'filled': '已买入（持仓中）',
    'partial': '部分卖出（仍有持仓）',
    'closed': '已结清',
    'cancelled': '已取消',
}

# 未成交原因：不是所有「没成交」都是同一个原因，混在一起就没法改进
NOT_FILLED_TEXT = {
    'price_above_cap': '开盘价超过计划的可接受买价上限，按规则放弃（不追价）',
    'unbuyable': '当日不可买入（涨停封板 / 停牌 / 无成交）',
    'no_session': '执行日在数据集内不存在（行情尚未更新到该日）',
    'not_recorded': '当日可以成交，但没有录入实际成交（也可能确实没下单）',
    'plan_not_actionable': '计划本身不可执行（未确认/被阻断/持仓中/已失效）',
    'expired': '计划已过有效期（仅当日有效），未成交即作废',
    'superseded': '该计划未成交，已按最新行情续期到新计划（旧记录只作存档）',
}


def _r(x, d=2):
    return None if x is None else round(float(x), d)


def _pct(a, b):
    """(a/b - 1) * 100，b 为 0 时返回 None（不编造无穷大）。"""
    try:
        if not b:
            return None
        return round((float(a) / float(b) - 1) * 100, 2)
    except (TypeError, ZeroDivisionError):
        return None


def _sum_fills(fills: list[dict]) -> dict:
    buy = [f for f in fills if f['side'] == 'buy']
    sell = [f for f in fills if f['side'] == 'sell']
    buy_qty = sum(float(f['qty']) for f in buy)
    sell_qty = sum(float(f['qty']) for f in sell)
    buy_amount = sum(float(f['qty']) * float(f['price']) for f in buy)
    sell_amount = sum(float(f['qty']) * float(f['price']) for f in sell)
    buy_fees = sum(float(f['fees'] or 0) for f in buy)
    sell_fees = sum(float(f['fees'] or 0) for f in sell)
    buy_avg = buy_amount / buy_qty if buy_qty else None
    sell_avg = sell_amount / sell_qty if sell_qty else None

    # 已实现盈亏：卖出净额 - 按比例分摊的买入成本（含买入费用）
    sold_cost = (buy_amount + buy_fees) * (sell_qty / buy_qty) if buy_qty else 0.0
    realized = (sell_amount - sell_fees) - sold_cost if sell_qty else 0.0
    open_qty = max(0.0, buy_qty - sell_qty)
    open_cost = (buy_amount + buy_fees) - sold_cost if buy_qty else 0.0

    return {
        'buy_qty': buy_qty, 'buy_avg_price': _r(buy_avg, 4), 'buy_amount': _r(buy_amount),
        'buy_fees': _r(buy_fees),
        'sell_qty': sell_qty, 'sell_avg_price': _r(sell_avg, 4), 'sell_amount': _r(sell_amount),
        'sell_fees': _r(sell_fees),
        'open_qty': open_qty, 'open_cost': _r(open_cost),
        'realized_pnl': _r(realized) if sell_qty else None,
        'realized_pct': (_pct(sell_amount - sell_fees, sold_cost) if sell_qty and sold_cost else None),
        'first_buy_date': buy[0]['trade_date'] if buy else None,
        'last_fill_date': (max(f['trade_date'] for f in fills) if fills else None),
        'fill_count': len(fills),
    }


def _not_filled_reason(plan: dict, row: dict | None, *, expired: bool) -> tuple[str, str]:
    """未成交的**首要原因**：先说清执行当天发生了什么，「过期」只是补充说明。

    顺序很重要：如果把「计划已过期」当主因，用户就永远看不到「那天开盘价超了上限」
    这种真正可改进的信息。
    """
    status = plan.get('status')
    if status == 'watching':
        return 'plan_not_actionable', '计划生成时信号尚未确认：本就没有下单动作'
    if status != 'signal_confirmed':
        return 'plan_not_actionable', NOT_FILLED_TEXT['plan_not_actionable']
    if row is None:
        if expired:
            return 'expired', NOT_FILLED_TEXT['expired']
        return 'no_session', NOT_FILLED_TEXT['no_session']
    if row.suspended or not row.can_buy:
        return 'unbuyable', NOT_FILLED_TEXT['unbuyable']
    cap = ((plan.get('execution') or {}).get('price_bounds') or {}).get('effective_cap')
    if cap is not None and row.open > cap + 1e-9:
        return 'price_above_cap', (
            f'开盘价 {row.open:.2f} 高于可接受上限 {cap:.2f}：{NOT_FILLED_TEXT["price_above_cap"]}')
    if expired:
        return 'not_recorded', ('执行当日可以成交，但没有录入成交；该计划「仅当日有效」，现已过期')
    return 'not_recorded', NOT_FILLED_TEXT['not_recorded']


def _counterfactual(bars, plan: dict, rec: dict, cost, capital: float) -> dict:
    """同一模型 + 同参数，在 as_of 之后严格跟随策略会怎样。

    用 `trade_from` 把 as_of 之前设为预热段（不交易、不产生持仓），等价于「今天空仓开始跟」。
    结果只作对照：它假设每根开盘都能按规则成交，不考虑用户真实资金/情绪/迟疑。
    """
    model_id = rec.get('model_id')
    model = MODELS.get(model_id)
    if model is None:
        return {'available': False, 'note': f'模型 {model_id} 不在当前版本中，无法对照'}
    if not model.tradable:
        return {'available': False, 'note': '该模型为事后最优上界，没有可执行的对照轨迹'}
    as_of = rec['as_of']
    idx = next((i for i, b in enumerate(bars) if b.date == as_of), None)
    if idx is None or idx >= len(bars) - 1:
        return {'available': False, 'note': 'as_of 之后没有新的行情，无法对照'}
    try:
        r = run(bars, model, dict(rec.get('params') or {}), cost, capital,
                liquidation_policy='mark_to_market', trade_from=idx)
    except Exception as e:                      # 参数非法等：对照不可用，但不该让复盘崩掉
        return {'available': False, 'note': f'对照回放失败：{e}'}

    trades = [t for t in r.trades if t.buy_i > idx]
    f = r.final or {}
    out = {'available': True, 'model_id': model_id,
           'entry_date': None, 'entry_price': None, 'exit_date': None, 'exit_price': None,
           'hold_days': None, 'net_pct': None, 'exit_reason': None,
           'status': 'no_entry', 'note': '按策略规则，as_of 之后尚未出现入场信号'}
    if trades:
        t = trades[0]
        out.update(entry_date=t.buy_date, entry_price=_r(t.buy_price, 4),
                   exit_date=t.sell_date, exit_price=_r(t.sell_price, 4),
                   hold_days=t.hold_days, net_pct=_r(t.net_pct),
                   exit_reason=t.exit_reason, status='closed')
    elif f.get('holding'):
        out.update(entry_date=f.get('entry_date'), entry_price=_r(f.get('entry_price'), 4),
                   hold_days=f.get('hold_days'), net_pct=_r(f.get('unrealized_pct')),
                   exit_reason='未平仓', status='open',
                   note='策略仍持仓，收益按最新收盘盯市')
    return out


def review(conn, rec: dict, *, cost=None, capital: float | None = None) -> dict:
    """生成复盘。`rec` 为 db.plans 的记录（含 plan 快照）。

    费用参数不额外查库：计划快照里已经带了生成时的 `config` 快照，复盘必须用**当时**的
    费用口径，否则用户后来改了佣金，复盘出来的偏差就不可比了。
    """
    plan = rec.get('plan') or {}
    fills = rec.get('fills') or []
    if cost is None:
        try:
            cost = ExecutionConfig(**(plan.get('config') or {})).cost()
        except (TypeError, ValueError):
            cost = ExecutionConfig().cost()
    # 跨数据集合并：复盘要看「计划之后发生了什么」，而原数据集是快照、不会自动变长
    bars = repository.get_bars_covering(conn, rec['dataset_id'], rec['code'])
    as_of = rec['as_of']
    timeline: list[dict] = []

    timeline.append({'at': rec['created_at'], 'event': 'generated',
                     'text': f"生成计划（{plan.get('status_text') or rec['status']}）"})
    if rec.get('renewed_from'):
        timeline.append({'at': rec['created_at'], 'event': 'renewed',
                         'text': f"由 #{rec['renewed_from']} 续期生成（按更新后的行情重算）"})
    if rec.get('superseded_by'):
        timeline.append({'at': rec['updated_at'], 'event': 'superseded',
                         'text': f"已被 #{rec['superseded_by']} 取代（未成交，行情更新后续期）"})
    earliest = ((plan.get('execution') or {}).get('earliest_execution_session'))
    valid_until = ((plan.get('validity') or {}).get('valid_until_session'))
    if earliest:
        timeline.append({'at': earliest, 'event': 'planned_execution',
                         'text': f'计划最早执行日（开盘）'})
    for f in fills:
        timeline.append({'at': f['trade_date'], 'event': 'fill',
                         'text': f"{'买入' if f['side'] == 'buy' else '卖出'} "
                                 f"{float(f['qty']):g} 股 @ {float(f['price']):.2f}"})
    if rec.get('cancel_reason'):
        timeline.append({'at': rec['updated_at'], 'event': 'cancelled',
                         'text': f"取消：{rec['cancel_reason']}"})

    summary = _sum_fills(fills)
    account = rec.get('account') or {}
    try:
        acct_equity = float(account.get('equity_value') or 0) or None
    except (TypeError, ValueError):
        acct_equity = None
    cfg_capital = float((plan.get('config') or {}).get('capital') or 0) or None
    cap_for_cf = capital or acct_equity or cfg_capital or 100000.0

    future = [b for b in bars if b.date > as_of]
    latest = bars[-1] if bars else None
    exec_row = next((b for b in future if b.date == earliest), None)
    latest_date = latest.date if latest else None

    # ---------------- 结果判定 ----------------
    # 「没买」有三种完全不同的原因，混成一个 not_filled 会误导：
    #   no_action   —— 计划本身没让买（未确认/被阻断/持仓中/数据不足）；
    #   pending     —— 该买，但执行日还没到（或行情还没更新到那天）；
    #   not_filled  —— 执行日过了，确实没成交。
    plan_status = plan.get('status') or rec.get('status')
    if rec.get('cancel_reason'):
        outcome = 'cancelled'
    elif summary['buy_qty'] > 0:
        if summary['open_qty'] <= 0:
            outcome = 'closed'
        elif summary['sell_qty'] > 0:
            outcome = 'partial'
        else:
            outcome = 'filled'
    elif plan_status != 'signal_confirmed':
        outcome = 'no_action'
    elif future and latest_date and valid_until and latest_date >= valid_until:
        outcome = 'not_filled'
    else:
        outcome = 'pending'

    # ---------------- 未成交 / 未执行原因 ----------------
    not_filled = None
    if outcome in ('not_filled', 'pending', 'no_action') and not rec.get('cancel_reason'):
        reason, text = _not_filled_reason(
            plan, exec_row, expired=bool(valid_until and latest_date and latest_date > valid_until))
        if outcome == 'pending' and not future:
            reason, text = 'no_session', NOT_FILLED_TEXT['no_session']
        elif outcome == 'no_action':
            reason = 'plan_not_actionable'
            text = '；'.join(r.get('text', '') for r in (plan.get('reason_codes') or [])) \
                or NOT_FILLED_TEXT['plan_not_actionable']
        not_filled = {'reason_code': reason, 'text': text,
                      'execution_session': earliest,
                      'session_open': _r(exec_row.open, 2) if exec_row else None,
                      'session_can_buy': bool(exec_row.can_buy) if exec_row else None,
                      'cap': ((plan.get('execution') or {}).get('price_bounds') or {}).get('effective_cap'),
                      'expired': reason == 'expired',
                      'superseded_by': rec.get('superseded_by')}
        sup = rec.get('superseded_by')
        if sup:
            # 续期不抹掉真实原因：如果当天确实是超上限/不可买，那句话比「已续期」有用得多，
            # 只是补一句「这条已经不用再看了」。没有更具体原因时才把「已续期」当主因。
            if reason in ('not_recorded', 'expired'):
                not_filled.update(reason_code='superseded',
                                  text=f'未成交，已按最新行情续期到 #{sup}；本记录仅作存档')
            else:
                not_filled['text'] = f'{text}；该记录已按最新行情续期到 #{sup}'

    # ---------------- 计划 vs 实际 ----------------
    qe = ((plan.get('execution') or {}).get('quantity_estimate') or {})
    planned_qty = float(qe.get('shares') or 0)
    planned_price = float(qe.get('reference_price') or 0) or None
    planned_fees = qe.get('estimated_fees')
    actual_qty = summary['buy_qty']
    actual_price = summary['buy_avg_price']
    within_cap = True
    if not_filled:
        within_cap = not_filled['reason_code'] != 'price_above_cap'
    checks = [
        {'text': '成交价是否在计划的可接受上限内',
         'ok': (not not_filled) or within_cap,
         'note': (f"上限 {not_filled['cap']:.2f}，开盘 {not_filled['session_open']:.2f}"
                  if (not_filled and not_filled.get('cap') is not None
                      and not_filled.get('session_open') is not None) else '')},
        {'text': '数量是否为合法整手',
         'ok': (actual_qty <= 0) or abs(actual_qty % 100) < 1e-9,
         'note': '卖出零股不受整手限制' if actual_qty > 0 else ''},
        {'text': '成交日是否不早于计划依据日',
         'ok': all(f['trade_date'] > as_of for f in fills), 'note': f'计划依据日 {as_of}'},
    ]
    vs_plan = {
        'planned_shares': planned_qty, 'planned_reference_price': planned_price,
        'planned_amount': qe.get('amount'), 'planned_fees': planned_fees,
        'actual_shares': actual_qty, 'actual_avg_price': actual_price,
        'actual_fees': summary['buy_fees'] if actual_qty else None,
        'price_deviation_pct': (_pct(actual_price, planned_price)
                                if actual_price and planned_price else None),
        'qty_deviation_pct': (_pct(actual_qty, planned_qty)
                              if actual_qty and planned_qty else None),
        'fees_diff': (_r(float(summary['buy_fees']) - float(planned_fees))
                      if (actual_qty and planned_fees is not None) else None),
        'checks': checks,
    }

    # ---------------- 市场结果 ----------------
    # 估值必须发生在成交**之后**：数据集可能还没更新到成交日（用户先补录了成交），
    # 此时拿成交日之前的收盘价去估浮动盈亏等于时间倒流，宁可留空并说明原因。
    market = {
        'data_latest_date': latest_date,
        'data_latest_close': _r(latest.close if latest else None, 2),
        'valuation_date': None, 'valuation_close': None,
        'open_pnl': None, 'open_pct': None, 'total_pnl': None,
        'mfe_pct': None, 'mae_pct': None, 'bars_held': 0,
        'valuable': False, 'note': '',
    }
    if summary['buy_qty'] > 0:
        start = summary['first_buy_date']
        window = [b for b in bars if start and b.date >= start]
        if window:
            val = window[-1]
            last_px = val.close                       # 原始价：与用户录入的成交价同一口径
            open_pnl = ((last_px - float(summary['buy_avg_price'])) * summary['open_qty']
                        if summary['open_qty'] > 0 else 0.0)
            hi = max(b.high for b in window)
            lo = min(b.low for b in window)
            market.update(
                valuation_date=val.date, valuation_close=_r(last_px, 2), valuable=True,
                open_pnl=_r(open_pnl),
                open_pct=(_pct(last_px, summary['buy_avg_price'])
                          if summary['open_qty'] > 0 else None),
                total_pnl=_r((summary['realized_pnl'] or 0.0) + open_pnl),
                mfe_pct=_pct(hi, summary['buy_avg_price']),
                mae_pct=_pct(lo, summary['buy_avg_price']),
                bars_held=len(window),
            )
        else:
            market['note'] = (f'行情尚未更新到成交日 {start}，浮动盈亏待数据更新后计算；'
                              f'当前已实现盈亏仍按成交记录结算')

    counterfactual = _counterfactual(bars, plan, rec, cost, cap_for_cf)

    # ---------------- 说明 ----------------
    notes = [
        '复盘只读取当时的计划快照与实际成交，不会用后来的行情或参数改写原始计划。',
        '策略对照（counterfactual）是「同参数严格跟随」的模拟结果，不是本计划的成绩。',
    ]
    if outcome == 'not_filled':
        notes.append('计划未成交不等于策略失效：可能是价格上限/涨跌停，也可能只是没有下单。')
    if rec.get('lifecycle') == 'planned' and outcome == 'filled':
        notes.append('成交记录与生命周期不一致（可能刚录入），以成交汇总为准。')
    if rec.get('superseded_by'):
        notes.append(f"该记录已由 #{rec['superseded_by']} 取代：续期是用最新行情**重新生成**计划，"
                     f'不是延长旧计划的有效期，旧快照原样留存。')
    if rec.get('renewed_from'):
        notes.append(f"本计划由 #{rec['renewed_from']} 续期生成；两条记录各自保留生成当时的判断，互不覆盖。")

    return {
        'record_id': rec.get('id'),
        'plan_id': rec.get('plan_id'),
        'as_of': as_of,
        'plan_status': plan_status,
        'outcome': outcome,
        'outcome_text': OUTCOME_TEXT.get(outcome, outcome),
        'lifecycle': rec.get('lifecycle'),
        'data_span': {'start': bars[0].date if bars else None,
                      'end': latest_date, 'bar_count': len(bars),
                      'note': '复盘用全库同代码行情拼接（跨数据集），复权基准已统一'},
        'execution_window': {'earliest_execution_session': earliest,
                             'valid_until_session': valid_until,
                             'execution_session_confirmed':
                                 (plan.get('execution') or {}).get('execution_session_confirmed')},
        'fills': fills,
        'fills_summary': summary,
        'vs_plan': vs_plan,
        'not_filled': not_filled,
        'market': market,
        'counterfactual': counterfactual,
        'timeline': timeline,
        'notes': notes,
        'today': date.today().isoformat(),
    }
