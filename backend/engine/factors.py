# -*- coding: utf-8 -*-
"""量价因子库：15 个日线量价因子 + Spearman IC 检验（严格因果）。

来源：原独立脚本 quant_price_volume.py（未随项目迁移），依据项目 README §5
记录的因子清单与实证结论重建，并按 P2 规划落入引擎（可被 pytest 复用）。

实证基线（7 只 × 300 日样例，勿重复踩坑）：
- 短周期 h=3~5：全部因子 |IC| < 0.09（基本无信息）；
- 中周期 h≈20：资金流因子（CMF/EMV/VWAP 偏离）稳定为负（86% 股票，拥挤反转，可反向用）；
  波动率放大（vola_exp）为正；ATR% 为负（低波动异象）；单纯放量（vol_ratio）无信息量，
  有用的是量的相对位置（turnover_z）与拥挤度。
- 日线量价因子的 IC 天花板约 0.2。

所有因子在 i 日的取值只使用 <= i 的数据（与引擎防未来函数口径一致）。
"""
from .data import ma


# ---------------------------------------------------------------- 工具
def _roc(xs, n, i):
    if i - n < 0:
        return None
    base = xs[i - n]
    return (xs[i] / base - 1.0) if base else None


def _std(xs, i, n):
    lo = i - n + 1
    if lo < 0:
        return None
    seg = xs[lo:i + 1]
    m = sum(seg) / n
    return (sum((x - m) ** 2 for x in seg) / n) ** 0.5


def _ema_series(xs, n):
    """前 n-1 个为 None 的 EMA（与 ma 同风格）。"""
    out = [None] * len(xs)
    if not xs:
        return out
    k = 2.0 / (n + 1)
    prev = None
    for i, x in enumerate(xs):
        prev = x if prev is None else x * k + prev * (1 - k)
        if i >= n - 1:
            out[i] = prev
    return out


def _obv(bars):
    out = [0.0]
    for i in range(1, len(bars)):
        c, pc = bars[i].adj_close, bars[i - 1].adj_close
        out.append(out[-1] + (bars[i].volume if c > pc else -bars[i].volume if c < pc else 0.0))
    return out


def _atr(bars, n):
    """ATR（含前收盘跳空），i < n-1 为 None。"""
    out = [None] * len(bars)
    trs = [bars[0].adj_high - bars[0].adj_low]
    for i in range(1, len(bars)):
        b, pc = bars[i], bars[i - 1].adj_close
        trs.append(max(b.adj_high - b.adj_low, abs(b.adj_high - pc), abs(b.adj_low - pc)))
    acc = 0.0
    for i, tr in enumerate(trs):
        if i < n:
            acc += tr
            if i == n - 1:
                out[i] = acc / n
        else:
            out[i] = (out[i - 1] * (n - 1) + tr) / n
    return out


# ---------------------------------------------------------------- 因子表
def compute_factors(bars):
    """返回 {name: [值 or None]}，长度 = len(bars)，第 i 个值只用 <= i 的数据。"""
    n = len(bars)
    closes = [b.adj_close for b in bars]
    vols = [b.volume for b in bars]
    amts = [b.amount for b in bars]
    highs = [b.adj_high for b in bars]
    lows = [b.adj_low for b in bars]
    turns = [b.turnover for b in bars]

    ma5, ma20 = ma(closes, 5), ma(closes, 20)
    vol_ma5, vol_ma20 = ma(vols, 5), ma(vols, 20)
    obv = _obv(bars)
    atr14 = _atr(bars, 14)
    vwap20 = [None] * n
    for i in range(19, n):
        a, v = amts[i - 19:i + 1], vols[i - 19:i + 1]
        s = sum(v)
        vwap20[i] = (sum(a) / s) if s else None

    f = {k: [None] * n for k in
         ('ret1', 'ret5', 'roc10', 'ma5_bias', 'ma20_bias', 'vol_ratio5', 'vol_ratio20',
          'cmf20', 'emv14', 'vwap_bias20', 'atr14_pct', 'vola_exp', 'turnover_z',
          'obv_slope10', 'range_pos20')}

    for i in range(n):
        c = closes[i]
        f['ret1'][i] = _roc(closes, 1, i)
        f['ret5'][i] = _roc(closes, 5, i)
        f['roc10'][i] = _roc(closes, 10, i)
        f['ma5_bias'][i] = (c / ma5[i] - 1) if (i >= 4 and ma5[i]) else None
        f['ma20_bias'][i] = (c / ma20[i] - 1) if (i >= 19 and ma20[i]) else None
        f['vol_ratio5'][i] = (vols[i] / vol_ma5[i]) if (i >= 4 and vol_ma5[i]) else None
        f['vol_ratio20'][i] = (vols[i] / vol_ma20[i]) if (i >= 19 and vol_ma20[i]) else None
        # CMF20：资金流（近 20 日 CLV 加权量的和 / 量的和）
        if i >= 19:
            num = den = 0.0
            for j in range(i - 19, i + 1):
                rng = highs[j] - lows[j]
                clv = ((closes[j] - lows[j]) - (highs[j] - closes[j])) / rng if rng else 0.0
                num += clv * vols[j]
                den += vols[j]
            f['cmf20'][i] = num / den if den else None
            # EMV14：简易波动（dm/br 归一的 14 日均值）
            dm_br = []
            for j in range(i - 13, i + 1):
                mid_move = (highs[j] + lows[j]) / 2 - (highs[j - 1] + lows[j - 1]) / 2
                br = (vols[j] / 1e6) / (highs[j] - lows[j]) if highs[j] > lows[j] else None
                dm_br.append(mid_move / br if br else None)
            ok = [x for x in dm_br if x is not None]
            f['emv14'][i] = sum(ok) / len(ok) if ok else None
        if vwap20[i]:
            f['vwap_bias20'][i] = c / vwap20[i] - 1
        if atr14[i] and c:
            f['atr14_pct'][i] = atr14[i] / c
        # 波动率放大：5 日收益波动 / 20 日收益波动
        rets = [closes[t] / closes[t - 1] - 1 for t in range(1, i + 1)]
        if len(rets) >= 20:
            s5, s20 = _std(rets, i - 1, 5), _std(rets, i - 1, 20)
            f['vola_exp'][i] = s5 / s20 if s20 else None
        # 换手率标准分（相对近 20 日）
        if i >= 19:
            seg = turns[i - 19:i + 1]
            mu = sum(seg) / 20
            sd = (sum((x - mu) ** 2 for x in seg) / 20) ** 0.5
            f['turnover_z'][i] = (turns[i] - mu) / sd if sd else None
        # OBV 10 日斜率（归一）
        if i >= 10:
            d = obv[i] - obv[i - 10]
            f['obv_slope10'][i] = d / (sum(vols[i - 9:i + 1]) or 1)
        # 收盘在 20 日高低区间的位置
        if i >= 19:
            hi, lo = max(highs[i - 19:i + 1]), min(lows[i - 19:i + 1])
            f['range_pos20'][i] = (c - lo) / (hi - lo) if hi > lo else None
    return f


FACTOR_NAMES = ['ret1', 'ret5', 'roc10', 'ma5_bias', 'ma20_bias', 'vol_ratio5', 'vol_ratio20',
                'cmf20', 'emv14', 'vwap_bias20', 'atr14_pct', 'vola_exp', 'turnover_z',
                'obv_slope10', 'range_pos20']


# ---------------------------------------------------------------- Spearman IC
def _rank(xs):
    """平均秩（处理并列）。None 直接不支持，调用方先过滤。"""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs, ys):
    """Spearman 秩相关；样本 < 3 返回 None。"""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    rx, ry = _rank([p[0] for p in pairs]), _rank([p[1] for p in pairs])
    n = len(pairs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def factor_ic(bars, factor_name=None, horizon=20):
    """单股票因子 IC：逐日「因子值 t」vs「t+1 开盘买入持有 horizon 日的前复权收益」。

    horizon 的收益从 t+1 开盘起算（避免用 t 收盘造假 lookahead）。
    返回 {factor: ic}；factor_name 为 None 时对全部 15 因子计算。
    """
    facs = compute_factors(bars)
    names = [factor_name] if factor_name else FACTOR_NAMES
    n = len(bars)
    opens = [b.adj_open for b in bars]
    out = {}
    for name in names:
        xs, ys = [], []
        series = facs[name]
        for t in range(n):
            j = t + horizon          # t+1 买入，持 horizon 根到 t+horizon
            if j >= n or series[t] is None:
                continue
            base = opens[t + 1] if t + 1 < n else None
            if not base:
                continue
            xs.append(series[t])
            ys.append(opens[j] / base - 1 if j < n else None)
        out[name] = spearman(xs, ys)
    return out
