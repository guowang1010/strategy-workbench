# -*- coding: utf-8 -*-
"""未来函数检测器：随机截断重算，t 及之前必须完全一致。

改进（2026-11-03）：
- 旧实现 `rng.sample(range(60, n-1), ...)` 在 n<=61 时区间为空 → 直接抛 ValueError；
  现在先算可用截断池，空池返回 skipped 而不是崩。
- 除随机点外**固定覆盖首/末可用截断点**，避免「只抽到靠后区间」的盲区。
"""
import random


def audit(bars, model, params, cost, capital=100000.0, cuts=50, seed=42):
    from .backtest import run
    full = run(bars, model, params, cost, capital)
    n = len(bars)
    # 预热窗口：至少 2 根，长样本用 60 根（与历史口径一致），短样本按 1/4 收缩
    warm = min(60, max(2, n // 4))
    pool = list(range(warm, n - 1))
    if not pool:
        return {'passed': True, 'failed': [], 'cuts': 0,
                'skipped': f'样本过短（{n} 根），不足以做前缀比对'}

    rng = random.Random(seed)
    points = set(rng.sample(pool, min(cuts, len(pool))))
    points.update({pool[0], pool[-1]})       # 边界确定性覆盖
    bad = []
    for t in sorted(points):
        part = run(bars[:t + 1], model, params, cost, capital)
        if part.position[:t] != full.position[:t]:
            bad.append((t, 'position'))
        if any(abs(a - b) > 1e-9 for a, b in zip(part.equity[:t], full.equity[:t])):
            bad.append((t, 'equity'))
    return {'passed': not bad, 'failed': bad, 'cuts': len(points)}
