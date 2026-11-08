# -*- coding: utf-8 -*-
"""模型插件协议：所有模型只负责「目标仓位」，撮合与风控由引擎统一处理"""
from ..data import ma


class Model:
    id = 'M0'
    name = 'base'
    tradable = True
    color = '#5470c6'
    uses_tp_sl = False
    exit_on_signal = True
    defaults: dict = {}
    param_schema: list = []

    def target(self, bars, p) -> list[int]:
        """返回长度 n 的 0/1 数组；target[i] 只能使用 bars[:i+1]。"""
        raise NotImplementedError
