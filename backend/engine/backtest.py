# -*- coding: utf-8 -*-
"""撮合引擎：T+1、涨停不买、跌停不卖、停牌不动、最小持仓、止盈止损、费用

期末结算（A02）：
  - liquidation_policy='close_at_end'（默认，保持历史口径）：末根按收盘价平掉未平仓持仓，
    但必须同时满足 can_sell 且未停牌，且**当日买入不得当日卖出**（T+1）；
  - liquidation_policy='mark_to_market'：不清算，按末根收盘价盯市，用于「下一笔计划」
    这类需要知道真实持仓状态的场景；
  - 无论哪种口径，都输出 final_portfolio（现金/股数/市值/是否仍持仓/浮盈），
    且 position 末值与 trades 账本保持一致。
"""
from .types import Trade, Result
from . import metrics as M
from .signals import entry_condition_text
from .rules import rule_text

POLICIES = ('close_at_end', 'mark_to_market')

SIZE_BUFFER = 0.02      # 目标仓位留 2% 缓冲（保持历史口径）；硬约束见 _affordable_qty


def entry_reason_text(model, p: dict) -> str:
    """买入命中说明：这次建仓是「哪个策略的哪条条件」触发的。

    交易明细只显示成交价与盈亏，读者无法判断「为什么买」——这里把模型的入场
    规则渲染成人话。M1 是首日建仓后一直持有；M3/M4/M6 走 5 种入场信号库；
    M9/M10 是用户自定义规则树（rule_text 渲染 AND/OR 嵌套条件）。
    """
    if model.id == 'M1':
        return '买入持有（首根K线建仓后一直持有）'
    if model.id in ('M9', 'M10'):
        return '自定义规则：' + rule_text(p.get('rule'))
    return entry_condition_text(p)


def _affordable_qty(cash: float, px: float, cost) -> int:
    """「成交金额 + 买入费用 ≤ 现金」下可买的最大整手股数（1 手 = 100 股）。

    买入费用是分段函数 max(amount*commission, min_commission) + amount*transfer_fee，
    所以不能用 amount*0.98 估手数再扣费：高佣金会把预算吃穿，最低佣金在小资金上同理，
    两种情形都会让现金变负（R1）。这里按两个分段各解一次闭式上界，取可行者，
    再用硬约束逐手回退兜住浮点尾差；不足一手返回 0（不成交）。
    """
    if px <= 0 or cash <= 0:
        return 0
    rate = cost.commission + cost.transfer_fee
    fixed = cost.min_commission
    cands = []
    # 分段 A：比例佣金已超过最低佣金 → total = amount * (1 + rate)
    if cost.commission > 0:
        amt = cash / (1 + rate)
        if amt * cost.commission >= fixed - 1e-12:
            cands.append(amt)
    # 分段 B：最低佣金主导 → total = amount * (1 + transfer_fee) + min_commission
    amt_b = (cash - fixed) / (1 + cost.transfer_fee)
    if amt_b > 0 and (cost.commission == 0 or amt_b * cost.commission <= fixed + 1e-12):
        cands.append(amt_b)
    if not cands:                       # 两段都不自洽（极小资金）：退回不含固定费的估计
        cands.append(max(0.0, cash / (1 + rate)))
    amount = max(cands)
    lots = int(amount // (px * 100))
    while lots > 0 and px * lots * 100 + cost.buy_fee(px * lots * 100) > cash + 1e-9:
        lots -= 1                       # 闭式解已很接近，最多回退几手
    return lots * 100


def _sell_proceeds(cost, px: float, shares: float, cash: float) -> float:
    """卖出净收款。费用以「成交额 + 可用现金」为上限，保证现金永不为负。

    只有在极端配置下（最低佣金高于持仓市值）才会触发：正常配置的卖出费用
    远小于成交额，这里的上限不改变任何历史结果。
    """
    amount = px * shares
    fee = min(cost.sell_fee(amount), amount + max(cash, 0.0))
    return amount - fee


def order_size(cash: float, px: float, cost) -> tuple[int, float, float]:
    """按「目标仓位留 2% 缓冲」+「成交额含费用 ≤ 现金」算买入手数。

    计划（BUY-07）与撮合共用这一份实现，避免「计划说 1000 股、回测只买 900 股」。
    返回 (qty, amount, fee)；qty=0 表示买不起一手（费用已计入）。
    """
    if cash <= 0 or px <= 0:
        return 0, 0.0, 0.0
    max_qty = _affordable_qty(cash, px, cost)
    qty = min(int(cash * (1 - SIZE_BUFFER) / (px * 100)) * 100, max_qty)
    if qty == 0:                        # 现金刚够一手时按硬约束成交，不因缓冲放弃
        qty = max_qty
    if qty <= 0:
        return 0, 0.0, 0.0
    amount = px * qty
    fee = cost.buy_fee(amount)
    if amount + fee > cash + 1e-9:      # 双保险：绝不出现负现金
        return 0, 0.0, 0.0
    return qty, amount, fee


def run(bars, model, params: dict, cost, capital: float = 100000.0,
        liquidation_policy: str = 'close_at_end', trade_from: int = 0) -> Result:
    """trade_from（A10 切分预热）：前 trade_from 根只用于计算指标/信号（预热），不交易。

    传入的 bars 应为「预热段 + 正式段」拼接，trade_from = 预热段长度；
    权益曲线在预热段为 None（前端断线显示），全部指标只统计正式段。
    trade_from=0（默认）行为与旧版完全一致。
    """
    if liquidation_policy not in POLICIES:
        raise ValueError(f'未知期末结算口径: {liquidation_policy}')
    if not isinstance(trade_from, int) or isinstance(trade_from, bool) or not 0 <= trade_from < len(bars):
        raise ValueError(f'trade_from 非法: {trade_from!r}（需 0 <= trade_from < {len(bars)}）')
    n = len(bars)
    p = {**model.defaults, **params}
    target = model.target(bars, p)          # 0/1，长度 n，因果：只用 <= i 的数据
    entry_text = entry_reason_text(model, p)  # 买入命中说明（每个模型一份文案）

    cash = capital
    shares = 0.0
    entry = None
    trades: list[Trade] = []
    equity: list = [None] * n
    pos_arr = [0] * n

    for i in range(n):
        b = bars[i]
        if i < trade_from:
            continue                      # 预热段：只算指标，不交易、不盯市
        want = target[i - 1] if i > 0 else 0   # T+1：昨收信号，今开执行
        block_entry = False                    # 盘中卖出后，当日不可再按开盘价回买

        # ---------- 卖出 ----------
        if shares > 0:
            hold = i - entry['i']
            px, reason = None, None
            at_open = True
            if hold >= p.get('min_hold', 1):
                tp, sl = p.get('tp'), p.get('sl')
                if model.uses_tp_sl and tp and sl:
                    up_px = entry['px'] * (1 + tp / 100.0)
                    dn_px = entry['px'] * (1 - sl / 100.0)
                    if b.adj_open <= dn_px:
                        px, reason = b.adj_open, 'sl'
                    elif b.adj_open >= up_px:
                        px, reason = b.adj_open, 'tp'
                    elif b.adj_low <= dn_px:
                        px, reason, at_open = dn_px, 'sl', False
                    elif b.adj_high >= up_px:
                        px, reason, at_open = up_px, 'tp', False
                mh = p.get('max_hold')
                if px is None and mh and hold >= mh:
                    px, reason = b.adj_open, 'timeout'
                if px is None and want == 0 and model.exit_on_signal:
                    px, reason = b.adj_open, 'signal'

            if px is not None and b.can_sell and not b.suspended:
                block_entry = not at_open      # 盘中成交 → 当日禁止回买
                fill_px = cost.slip_sell(px)   # 滑点：卖出成交价下压
                proceeds = _sell_proceeds(cost, fill_px, shares, cash)
                cash += proceeds
                gross = fill_px / entry['px'] - 1
                net = proceeds / entry['cost'] - 1
                trades.append(Trade(
                    seq=len(trades) + 1, buy_i=entry['i'], buy_date=entry['date'],
                    buy_price=round(entry['px'], 2), sell_i=i, sell_date=b.date,
                    sell_price=round(fill_px, 2), hold_days=i - entry['i'],
                    gross_pct=round(gross * 100, 2), net_pct=round(net * 100, 2),
                    exit_reason=reason,
                    mfe_pct=round((entry['mfe'] / entry['px'] - 1) * 100, 2),
                    mae_pct=round((entry['mae'] / entry['px'] - 1) * 100, 2),
                    entry_reason=entry['reason'],
                ))
                shares, entry = 0.0, None

        # ---------- 买入 ----------
        if shares == 0 and want == 1 and b.can_buy and not b.suspended and not block_entry:
            px = cost.slip_buy(b.adj_open)    # 滑点：买入成交价上浮
            # 目标仓位（含 2% 缓冲）与「付得起」取小；口径见 order_size（与计划共用）
            qty, amount, fee = order_size(cash, px, cost)
            if qty > 0:
                cash -= amount + fee
                shares = qty
                # 信号在昨日收盘确认、今日开盘执行（T+1）：把信号日一并写进命中说明
                sig_date = bars[i - 1].date if i > 0 else b.date
                reason = (entry_text if model.id == 'M1'
                          else f'{entry_text}（{sig_date} 收盘信号）')
                entry = {'i': i, 'date': b.date, 'px': px, 'cost': amount + fee,
                         'mfe': b.adj_high, 'mae': b.adj_low, 'reason': reason}

        # ---------- 盯市 ----------
        if shares > 0:
            entry['mfe'] = max(entry['mfe'], b.adj_high)
            entry['mae'] = min(entry['mae'], b.adj_low)
        equity[i] = (cash + shares * b.adj_close) / capital
        pos_arr[i] = 1 if shares > 0 else 0

    # 期末结算：默认按收盘价平掉未平仓持仓（保持历史口径），但受三条硬约束限制
    liquidated = False
    blocked_reason = None
    if shares > 0 and liquidation_policy == 'close_at_end':
        b = bars[-1]
        if entry['i'] >= n - 1:
            blocked_reason = 't_plus_1'      # 末日买入，A 股 T+1 不允许当日卖出
        elif (n - 1 - entry['i']) < p.get('min_hold', 1):
            blocked_reason = 'min_hold'      # 未满足策略自身的最小持仓，不强行卖出
        elif b.suspended:
            blocked_reason = 'suspended'     # 停牌不可卖
        elif not b.can_sell:
            blocked_reason = 'limit_down'    # 跌停不可卖
        else:
            fill_px = cost.slip_sell(b.adj_close)  # 滑点：卖出成交价下压
            proceeds = _sell_proceeds(cost, fill_px, shares, cash)
            cash += proceeds
            gross = fill_px / entry['px'] - 1
            net = proceeds / entry['cost'] - 1
            trades.append(Trade(
                seq=len(trades) + 1, buy_i=entry['i'], buy_date=entry['date'],
                buy_price=round(entry['px'], 2), sell_i=n - 1, sell_date=b.date,
                sell_price=round(fill_px, 2), hold_days=n - 1 - entry['i'],
                gross_pct=round(gross * 100, 2), net_pct=round(net * 100, 2),
                exit_reason='eod',
                mfe_pct=round((entry['mfe'] / entry['px'] - 1) * 100, 2),
                mae_pct=round((entry['mae'] / entry['px'] - 1) * 100, 2),
                entry_reason=entry['reason'],
            ))
            equity[-1] = cash / capital
            pos_arr[-1] = 0                  # 与账本一致：已平仓
            shares = 0.0
            liquidated = True

    last_close = bars[-1].adj_close
    holding = shares > 0
    final = {
        'policy': liquidation_policy,
        'liquidated': liquidated,
        'blocked_by': blocked_reason,
        'holding': holding,
        'cash': round(cash, 2),
        'shares': round(shares, 2),
        'market_value': round(shares * last_close, 2),
        'equity_value': round(cash + shares * last_close, 2),
        'equity_ratio': round(equity[-1], 6),
        'entry_date': entry['date'] if holding else None,
        'entry_price': round(entry['px'], 4) if holding else None,
        'entry_i': entry['i'] if holding else None,
        'hold_days': (n - 1 - entry['i']) if holding else None,
        'unrealized_pct': round((last_close / entry['px'] - 1) * 100, 2) if holding else None,
    }

    met = M.compute(bars, equity, trades, pos_arr, capital, start_index=trade_from)
    return Result(
        model_id=model.id, name=model.name, tradable=model.tradable, color=model.color,
        trades=trades, equity=[None if x is None else round(x, 6) for x in equity],
        position=pos_arr,
        metrics=met, markers=_markers(bars, trades), final=final,
    )


def _markers(bars, trades):
    """买卖标记统一用**实际成交价**，与交易明细一致（滑点后不再用开盘价）。"""
    buy, sell, seg = [], [], []
    for t in trades:
        bd = bars[t.buy_i].date
        sd = bars[t.sell_i].date
        buy.append([bd, round(t.buy_price, 2)])
        sell.append([sd, round(t.sell_price, 2)])
        seg.append([[bd, round(t.buy_price, 2)],
                    [sd, round(t.sell_price, 2)]])
    return {'buy': buy, 'sell': sell, 'segments': seg}
