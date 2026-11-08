# -*- coding: utf-8 -*-
"""统一执行配置：本金 + 费用 + 滑点。

回测 / 寻优 / 计划 / 汇总必须使用**同一份** ExecutionConfig，否则用户改了佣金后
寻优选出的参数、普通回测与下一笔计划不是同一口径，横向比较无意义。
"""
import hashlib
import json
import math
from dataclasses import dataclass

from .cost import Cost

_FIELDS = ('capital', 'commission', 'min_commission', 'stamp_duty', 'transfer_fee', 'slippage_pct')


@dataclass(frozen=True)
class ExecutionConfig:
    capital: float = 100000.0
    commission: float = 0.00025
    min_commission: float = 5.0
    stamp_duty: float = 0.0005
    transfer_fee: float = 0.00001
    slippage_pct: float = 0.0

    def __post_init__(self):
        for k in _FIELDS:
            v = getattr(self, k)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f'执行配置 {k} 必须为数值，收到 {v!r}')
            if not math.isfinite(float(v)):
                raise ValueError(f'执行配置 {k} 必须为有限数值，收到 {v!r}')
            if v < 0:
                raise ValueError(f'执行配置 {k} 不能为负，收到 {v!r}')
        if self.capital <= 0:
            raise ValueError('本金必须大于 0')
        if self.slippage_pct >= 100:
            raise ValueError('单边滑点需小于 100%')
        if self.commission + self.stamp_duty + self.transfer_fee >= 1:
            raise ValueError('费用率之和需小于 100%')

    def cost(self) -> Cost:
        return Cost(self.commission, self.min_commission, self.stamp_duty,
                    self.transfer_fee, self.slippage_pct)

    def to_dict(self) -> dict:
        # 统一保留 8 位小数，避免浮点尾差导致同一配置算出不同指纹
        return {k: round(float(getattr(self, k)), 8) for k in _FIELDS}

    def fingerprint(self) -> str:
        raw = json.dumps(self.to_dict(), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


DEFAULT_CONFIG = ExecutionConfig()
