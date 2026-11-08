# -*- coding: utf-8 -*-
"""A 股费用模型：佣金（双边，最低 5 元）、印花税（卖出）、过户费（双边）、滑点（可选）

滑点 slippage_pct：单边百分比（如 0.1 = 0.1%）。买入成交价上浮、卖出成交价下压，
用于近似「按信号价精确成交」的乐观假设。默认 0，行为与旧版完全一致。
"""


class Cost:
    def __init__(self, commission=0.00025, min_commission=5.0,
                 stamp_duty=0.0005, transfer_fee=0.00001, slippage_pct=0.0):
        self.commission = commission
        self.min_commission = min_commission
        self.stamp_duty = stamp_duty
        self.transfer_fee = transfer_fee
        self.slippage_pct = max(0.0, float(slippage_pct or 0.0))

    def slip_buy(self, px: float) -> float:
        """买入成交价：上浮 slippage_pct。"""
        return px * (1 + self.slippage_pct / 100.0)

    def slip_sell(self, px: float) -> float:
        """卖出成交价：下压 slippage_pct。"""
        return px * (1 - self.slippage_pct / 100.0)

    def buy_fee(self, amount: float) -> float:
        return max(amount * self.commission, self.min_commission) + amount * self.transfer_fee

    def sell_fee(self, amount: float) -> float:
        return (max(amount * self.commission, self.min_commission)
                + amount * self.stamp_duty + amount * self.transfer_fee)

    def net_return(self, buy_px: float, sell_px: float, shares: float) -> float:
        cost = buy_px * shares + self.buy_fee(buy_px * shares)
        proc = sell_px * shares - self.sell_fee(sell_px * shares)
        return proc / cost - 1
