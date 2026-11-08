# -*- coding: utf-8 -*-
"""统计指标：全部基于净收益与日频权益"""
import math
import statistics


def max_drawdown(equity: list[float]) -> float:
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd


def compute(bars, equity, trades, position, capital=100000.0, start_index: int = 0) -> dict:
    """start_index（A10）：指标只统计该下标之后的权益段，之前的预热段（None）不参与。

    年化的起止日期也随之从可评分首日算起，避免预热把年化/波动稀释。
    """
    eq = equity[start_index:]
    pos = position[start_index:]
    n = len(eq)
    total = eq[-1] - 1.0
    nets = [t.net_pct for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]

    avg_win = statistics.fmean(wins) if wins else 0.0
    avg_loss = statistics.fmean(losses) if losses else 0.0
    pf = (avg_win / abs(avg_loss)) if wins and losses and avg_loss != 0 else None

    daily = [eq[i] / eq[i - 1] - 1 for i in range(1, n) if eq[i - 1] > 0]
    vol = statistics.stdev(daily) * math.sqrt(252) if len(daily) > 2 else None
    sharpe = (statistics.fmean(daily) * 252 / vol) if vol else None

    mdd = max_drawdown(eq)
    from datetime import date
    d0 = date.fromisoformat(bars[start_index].date)
    d1 = date.fromisoformat(bars[-1].date)
    years = max((d1 - d0).days / 365.25, 1e-9)
    ann = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0

    return {
        'total_pct': round(total * 100, 2),
        'n_trades': len(trades),
        'win_rate': round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        'profit_factor': round(pf, 2) if pf else None,
        'expectancy_pct': round(statistics.fmean(nets), 2) if nets else 0.0,
        'avg_win_pct': round(avg_win, 2),
        'avg_loss_pct': round(avg_loss, 2),
        'max_drawdown_pct': round(mdd * 100, 2),
        'exposure_pct': round(sum(pos) / n * 100, 1),
        'avg_hold_days': round(statistics.fmean([t.hold_days for t in trades]), 1) if trades else 0.0,
        'ann_return_pct': round(ann * 100, 2),
        'ann_vol_pct': round(vol * 100, 2) if vol else None,
        'sharpe': round(sharpe, 2) if sharpe else None,
        'calmar': round(ann / abs(mdd), 2) if mdd < 0 else None,
        'final_equity': round(eq[-1] * capital, 2),
        'sample_warning': n < 30,
    }
