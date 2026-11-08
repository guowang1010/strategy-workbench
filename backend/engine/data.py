# -*- coding: utf-8 -*-
"""技术指标：滑动窗口均值 / 唐奇安通道 / 布林带（因果：只用 <= i 的数据）。

CSV → Bar 的转换已统一到 engine/bars.py（A09）：本模块不再提供 load_bars，
CLI 与测试一律走 db.importer.parse_csv + engine.bars.build_bars，
与生产落库路径共用同一套复权/涨跌停口径。
"""
from .types import Bar


def ma(values: list[float], n: int) -> list:
    out = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = round(s / n, 4)
    return out


def donchian(bars: list[Bar], n: int = 20):
    up, lo, mid = [None] * len(bars), [None] * len(bars), [None] * len(bars)
    for i in range(len(bars)):
        if i + 1 < n:
            continue
        win = bars[i + 1 - n:i + 1]
        hi = max(b.adj_high for b in win)
        lw = min(b.adj_low for b in win)
        up[i], lo[i], mid[i] = round(hi, 4), round(lw, 4), round((hi + lw) / 2, 4)
    return up, mid, lo


def bollinger(bars: list[Bar], n: int = 20, k: float = 2.0):
    closes = [b.adj_close for b in bars]
    up, mid, lo = [None] * len(bars), [None] * len(bars), [None] * len(bars)
    for i in range(n - 1, len(bars)):
        win = closes[i + 1 - n:i + 1]
        m = sum(win) / n
        var = sum((x - m) ** 2 for x in win) / n
        sd = var ** 0.5
        mid[i], up[i], lo[i] = round(m, 4), round(m + k * sd, 4), round(m - k * sd, 4)
    return up, mid, lo
