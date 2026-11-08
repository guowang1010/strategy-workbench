# -*- coding: utf-8 -*-
"""核心数据结构"""
from dataclasses import dataclass, field


@dataclass(slots=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    volume: float          # 股
    amount: float          # 元
    turnover: float        # %
    factor: float
    adj_open: float
    adj_high: float
    adj_low: float
    adj_close: float
    limit_up: float
    limit_down: float
    can_buy: bool = True
    can_sell: bool = True
    suspended: bool = False


@dataclass(slots=True)
class Trade:
    seq: int
    buy_i: int
    buy_date: str
    buy_price: float
    sell_i: int
    sell_date: str
    sell_price: float
    hold_days: int
    gross_pct: float
    net_pct: float
    exit_reason: str
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    # 买入命中说明：哪个策略的哪条入场条件触发了这次建仓（含信号日）。
    # 卖出侧对应 exit_reason（sl/tp/timeout/signal/eod/dp）。
    entry_reason: str = ''


@dataclass
class Result:
    model_id: str
    name: str
    tradable: bool
    color: str
    trades: list = field(default_factory=list)
    equity: list = field(default_factory=list)
    position: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    markers: dict = field(default_factory=dict)
    final: dict = field(default_factory=dict)   # 期末组合快照（持仓/现金/市值/结算口径）
