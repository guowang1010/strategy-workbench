# -*- coding: utf-8 -*-
"""领域转换器（A09）：原始行情行 → 可回测 Bar，全项目唯一实现。

此前 `engine/data.py::load_bars` 与 `db/repository.py::_bars_from_rows` 各写了一遍
「前复权 + 涨跌停 + 可交易判定」，且前者以**整个文件最后一行**的复权因子当基准，
多股 CSV 会拿错基准（实测 600186 首根 adj_close 偏差 27%）。现在：

    CSV ──(db.importer 校验/解析)──> RawRow ──(本模块 build_bars)──> Bar

`repository` 落库前与 CLI/demo 加载都走同一条转换，口径不再分叉。
本模块无 IO、无校验职责——坏数据应在 importer 里被拒绝，不会静默置零。
"""
from dataclasses import dataclass

from .types import Bar

ST_PCT = 0.05       # ST 涨跌幅 ±5%
MAIN_PCT = 0.10     # 主板涨跌幅 ±10%


@dataclass
class RawRow:
    """一条已校验的原始日线（复权前）。字段与 db.importer.Row 保持一致（除 code/name）。"""
    date: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    volume: float
    amount: float
    turnover: float
    factor: float
    st: bool
    suspended: bool


def _pct_of(st: bool) -> float:
    return ST_PCT if st else MAIN_PCT


def build_bars(rows, *, base_factor: float | None = None) -> list[Bar]:
    """按日期升序的**同一股票**行列表 → Bar 列表。

    base_factor：前复权基准因子，缺省取最后一行（最新一日）的因子，使最新价与原始价一致。
    禁止把多只股票混在一起调用——那样会拿错基准（这正是旧 load_bars 的 bug）。
    """
    rows = list(rows)
    if not rows:
        return []
    base = (base_factor if base_factor is not None else (rows[-1].factor or 1.0)) or 1.0
    out = []
    for r in rows:
        k = ((r.factor or 1.0) or 1.0) / base
        pc = r.pre_close if r.pre_close and r.pre_close > 0 else r.close
        pct = _pct_of(r.st)
        lu, ld = round(pc * (1 + pct), 2), round(pc * (1 - pct), 2)
        suspended = bool(r.suspended)
        out.append(Bar(
            date=r.date,
            open=r.open, high=r.high, low=r.low, close=r.close, pre_close=pc,
            volume=r.volume, amount=r.amount, turnover=r.turnover, factor=r.factor or 1.0,
            adj_open=round(r.open * k, 4), adj_high=round(r.high * k, 4),
            adj_low=round(r.low * k, 4), adj_close=round(r.close * k, 4),
            limit_up=lu, limit_down=ld,
            can_buy=(not suspended) and r.open < lu - 1e-9,
            can_sell=(not suspended) and r.open > ld + 1e-9,
            suspended=suspended,
        ))
    return out
