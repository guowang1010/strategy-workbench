# -*- coding: utf-8 -*-
"""长任务的协作式中断信号。

引擎层不依赖 Web / 线程：只接受一个无参 `check()` 回调，回调通过抛异常中断计算。
`core/jobs.py` 把「用户取消」和「超时」翻译成这两种异常，引擎无需感知。
"""
import time


class Cancelled(Exception):
    """任务被用户取消。"""


class Timeout(Exception):
    """任务超出允许时长。"""


def make_check(is_cancelled, deadline=None, clock=None):
    """构造 check()：取消优先于超时；deadline 为 monotonic 时间戳（None 表示不限时）。"""
    now_fn = clock or time.monotonic

    def check():
        if is_cancelled and is_cancelled():
            raise Cancelled('任务已取消')
        if deadline is not None and now_fn() > deadline:
            raise Timeout('任务超时')
    return check
