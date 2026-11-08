# -*- coding: utf-8 -*-
"""买入触发信号库：给定 bars 与参数，返回每根 K 线的「入场意图」0/1 数组。

严格因果：entry_signal[i] 只依赖 bars[0..i]（含 i），绝不引用未来。
所有信号均可在回测中以 T+1（昨收信号今开执行）撮合，不泄漏未来。
"""
from .data import ma


ENTRY_OPTIONS = [
    {"value": "ma_cross", "label": "均线突破（收盘价>MA）"},
    {"value": "breakout", "label": "N日新高突破"},
    {"value": "pullback", "label": "回踩均线"},
    {"value": "volume_confirm", "label": "放量确认"},
    {"value": "momentum", "label": "动量(ROC>阈值)"},
]

ENTRY_PARAM_SCHEMA = [
    {"key": "entry", "label": "买入触发", "type": "select", "options": ENTRY_OPTIONS, "default": "ma_cross"},
    {"key": "lookback", "label": "突破回望(天)", "type": "int", "min": 5, "max": 120, "step": 1, "default": 20},
    {"key": "ma_long", "label": "长期均线", "type": "int", "min": 5, "max": 120, "step": 1, "default": 20},
    {"key": "vol_n", "label": "量均周期", "type": "int", "min": 3, "max": 60, "step": 1, "default": 20},
    {"key": "vol_ratio", "label": "放量倍数", "type": "float", "min": 1.0, "max": 5.0, "step": 0.1, "default": 1.5},
    {"key": "mom_n", "label": "动量周期(天)", "type": "int", "min": 3, "max": 60, "step": 1, "default": 10},
    {"key": "mom_th", "label": "动量阈值(%)", "type": "float", "min": -20.0, "max": 20.0, "step": 0.5, "default": 0.0},
]


def entry_signal(bars, p: dict) -> list:
    n = len(bars)
    etype = (p.get("entry") or "ma_cross")
    closes = [b.adj_close for b in bars]
    out = [0] * n

    if etype == "ma_cross":
        mn = int(p.get("ma_n", 20))
        m = ma(closes, mn)
        for i in range(n):
            if m[i] is not None and closes[i] > m[i]:
                out[i] = 1

    elif etype == "breakout":
        lb = max(2, int(p.get("lookback", 20)))
        for i in range(n):
            if i < lb:
                continue
            hh = max(b.adj_high for b in bars[i - lb:i])   # 仅用 i 之前的 lb 根，无未来
            if closes[i] > hh:
                out[i] = 1

    elif etype == "pullback":
        sn = int(p.get("ma_n", 5))
        ln = int(p.get("ma_long", 20))
        ms = ma(closes, sn)
        ml = ma(closes, ln)
        for i in range(n):
            if ms[i] is None or ml[i] is None:
                continue
            # 站在长期均线上方，且回踩到短期均线附近（收盘 <= 短期均线 但 > 长期均线）
            if closes[i] > ml[i] and closes[i] <= ms[i]:
                out[i] = 1

    elif etype == "volume_confirm":
        mn = int(p.get("ma_n", 20))
        vn = int(p.get("vol_n", 20))
        vr = float(p.get("vol_ratio", 1.5))
        m = ma(closes, mn)
        vol = [b.volume for b in bars]
        vavg = ma(vol, vn)
        for i in range(n):
            if m[i] is None or vavg[i] is None:
                continue
            if closes[i] > m[i] and vol[i] > vavg[i] * vr:
                out[i] = 1

    elif etype == "momentum":
        mn = int(p.get("mom_n", 10))
        th = float(p.get("mom_th", 0.0))
        for i in range(n):
            if i < mn:
                continue
            roc = closes[i] / closes[i - mn] - 1
            if roc > th / 100.0:          # th 是百分数（1.2.0 修正：旧实现按倍数比较）
                out[i] = 1

    else:  # 未知类型回退到均线突破
        mn = int(p.get("ma_n", 20))
        m = ma(closes, mn)
        for i in range(n):
            if m[i] is not None and closes[i] > m[i]:
                out[i] = 1

    return out


def entry_condition_text(p: dict) -> str:
    """给「下一笔计划」面板用的人类可读触发条件描述。"""
    etype = (p.get("entry") or "ma_cross")
    if etype == "ma_cross":
        return f"收盘价站上 MA{int(p.get('ma_n', 20))}（状态条件，非当日上穿）"
    if etype == "breakout":
        return f"收盘价创近 {int(p.get('lookback', 20))} 日新高（突破前高）"
    if etype == "pullback":
        return f"收盘价回踩至 MA{int(p.get('ma_n', 5))} 且仍在 MA{int(p.get('ma_long', 20))} 上方"
    if etype == "volume_confirm":
        return (f"收盘价>MA{int(p.get('ma_n', 20))} 且成交量>"
                f"{float(p.get('vol_ratio', 1.5))}×近{int(p.get('vol_n', 20))}日均量")
    if etype == "momentum":
        return f"近 {int(p.get('mom_n', 10))} 日动量 ROC > {float(p.get('mom_th', 0.0))}%"
    return "均线突破"
