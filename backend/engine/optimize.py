# -*- coding: utf-8 -*-
"""参数寻优：网格搜索 + 训练/测试切分 + 三口径过拟合诊断

防过拟合流水线（严格顺序）：
  1) train = bars[:cut], test = bars[cut:]
  2) 训练段寻优 -> best（唯一允许的选参依据）
  3) best 在 test 跑 -> 诚实口径
  4) 全样本寻优 -> best_all，在 test 跑 -> 乐观口径
  5) test 段再寻优 -> oracle 口径（仅作上界展示）
  6) 输出迁移损失 / 乐观偏差 / 红绿灯
"""
import itertools
import math

from .backtest import run
from .models.builtin import MODELS
from .space import MAX_COMBOS, SpaceError, count_combos, parse_space

_NEG_INF = float('-inf')


def _expand(spec):
    """grid 描述 -> 值列表（保留旧入口，实际校验交给 engine.space）。"""
    from .space import expand
    return expand(spec)


def _objective(metrics, objective):
    """取目标值。0 是合法结果，不能当缺失值处理（A04）。"""
    key = {'total': 'total_pct', 'sharpe': 'sharpe'}.get(objective, 'expectancy_pct')
    v = metrics.get(key)
    if v is None:
        return _NEG_INF
    try:
        f = float(v)
    except (TypeError, ValueError):
        return _NEG_INF
    return f if math.isfinite(f) else _NEG_INF


def _model_space_keys(model):
    keys = {f.get('key') for f in getattr(model, 'param_schema', []) if f.get('key')}
    keys.add('entry')
    return keys


def grid_search(bars, model, space, cost, capital=100000.0,
                objective='expectancy', min_trades=5, base=None, max_combos=MAX_COMBOS,
                on_progress=None, check=None, trade_from: int = 0):
    """在给定 bars 上做网格搜索，返回 [(score, params, metrics), ...] 降序。

    base：固定不变的基础参数（如 M9/M10 的规则树 rule），与网格参数合并后回测，
          使「寻优出场参数」时不会丢掉入场规则。
    搜索空间在入口处严格校验（step、有限值、维度、组合上限），非法直接抛 SpaceError。
    on_progress(done, total)：组合级进度回调；check()：协作式中断点（取消/超时）。
    trade_from：透传给 run()（A10 预热口径，评分只统计正式段）。
    """
    parsed = parse_space(space, allowed=_model_space_keys(model), max_combos=max_combos)
    keys = list(parsed.keys())
    ranges = [parsed[k] for k in keys]
    total = 1
    for r in ranges:
        total *= max(1, len(r))
    results = []
    for i, combo in enumerate(itertools.product(*ranges), 1):
        if check:
            check()
        p = {**(base or {}), **dict(zip(keys, combo))}
        r = run(bars, model, p, cost, capital, trade_from=trade_from)
        m = r.metrics
        if on_progress:
            on_progress(i, total)
        if m['n_trades'] < min_trades:
            continue
        results.append((_objective(m, objective), p, m))
    results.sort(key=lambda x: -x[0])
    return results


# 预热窗口（A10）：至少 60 根覆盖 MA60；空间/固定参数里出现更长窗口时取最大值。
WARMUP_MIN = 60
_WINDOW_KEYS = ('ma_n', 'ma_long', 'lookback')


def warmup_need(space: dict | None, base: dict | None) -> int:
    """测试段评分**需要**的预热根数（不按可用历史截断）。

    取搜索空间与固定参数里最长的指标窗口（ma_n / ma_long / lookback），下限 WARMUP_MIN。
    """
    from .space import expand

    need = WARMUP_MIN
    for src in (space or {}, base or {}):
        for k in _WINDOW_KEYS:
            if k not in src:
                continue
            spec = src[k]
            if isinstance(spec, (int, float)) and not isinstance(spec, bool):
                need = max(need, int(spec))     # 固定参数（base）通常是标量
                continue
            try:
                vals = expand(spec, k)
            except Exception:          # 空间非法由 parse_space 统一报错，这里只求窗口
                continue
            nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if nums:
                need = max(need, int(max(nums)))
    return need


def warmup_bars(space: dict | None, base: dict | None, avail: int) -> int:
    """测试段应使用的预热根数：需要的窗口与可用历史取小（≤ avail）。"""
    return max(0, min(warmup_need(space, base), avail))


class _Tracker:
    """把若干阶段的局部进度映射到全局 0~1，并限流回调（≥1% 或完成才报）。"""

    def __init__(self, total, cb):
        self.total = max(1.0, float(total))
        self.done = 0.0
        self.cb = cb
        self._last = -1.0

    def set(self, v):
        v = min(1.0, max(0.0, v))
        if self.cb and (v - self._last >= 0.01 or v >= 1.0):
            self._last = v
            self.cb(v)

    def span(self, width):
        """返回一个绑定到当前偏移量的组合级回调，并推进偏移量。

        注意：base/width 是「组合数」，回调里要除以 total 才是一段 0~1 的比例。
        """
        base, self.done = self.done, self.done + width
        return lambda i, t: self.set((base + width * (i / max(1, t))) / self.total)

    def add(self, k=1.0):
        self.done += k
        self.set(self.done / self.total)


def _check_split(n, test_days, min_train=30):
    if isinstance(test_days, bool) or not isinstance(test_days, int):
        raise SpaceError('test_days 必须是整数')
    if test_days < 5:
        raise SpaceError(f'test_days 过小（{test_days}），至少 5 根')
    if test_days > n - min_train:
        raise SpaceError(f'样本不足：共 {n} 根，测试段 {test_days} 根，训练段至少需 {min_train} 根')


def walk_forward(bars, model_id, space, cost, capital=100000.0,
                 test_days=61, objective='expectancy', min_trades=5, folds=3,
                 min_hold_default=3, base=None, on_progress=None, check=None):
    """K 折滚动走查（anchored 扩张训练窗）：测试段连续铺在尾部，逐折
    「训练段选参 → 紧随的测试段执行」，输出每折诚实口径 vs 买入持有。
    比 optimize() 的单次切分更能暴露参数不稳定的时段。

    切分协议（A10，与 optimize 一致）：测试段的指标用**训练段尾部**预热（默认 ≥60 根），
    交易与评分只从测试段首日开始；**持仓在切分处重启**（不继承训练段的仓位与资金曲线），
    因此每折是「同一笔启动资金 + 同一组参数」在测试窗内的独立执行。
    """
    model = MODELS[model_id]
    n = len(bars)
    # 走查允许「样本不足」走软错误（返回 error 字段给前端展示），只硬校验参数类型
    if isinstance(test_days, bool) or not isinstance(test_days, int) or test_days < 5:
        raise SpaceError(f'test_days 需为 ≥5 的整数，收到 {test_days!r}')
    if isinstance(folds, bool) or not isinstance(folds, int) or folds < 1:
        raise SpaceError('folds 必须为 ≥1 的整数')
    min_train = max(min_hold_default + 5, 60)          # 训练窗至少 60 根
    room = (n - min_train) // test_days                # 最多能铺多少个测试段
    if room < 1:
        return {'folds': 0, 'rows': [], 'error': f'样本不足（{n} 根，需 ≥ {min_train + test_days}），无法滚动走查'}
    folds = max(1, min(folds, room))

    rows = []
    for k in range(folds):
        if check:
            check()
        test_start = n - test_days * (folds - k)
        test_end = min(test_start + test_days, n)
        train, test = bars[:test_start], bars[test_start:test_end]
        # A10 预热：用训练段尾部作为指标历史，交易只从测试段首日开始
        need = warmup_need(space, base)
        warm = min(need, len(train))
        seg = bars[test_start - warm:test_end]
        rec = {'fold': k + 1,
               'train_range': [train[0].date, train[-1].date],
               'test_range': [test[0].date, test[-1].date],
               'warmup_bars': warm,
               'warmup_need': need,
               'warmup_short': warm < need,
               'warmup_range': [bars[test_start - warm].date, bars[test_start - 1].date] if warm else None,
               'scorable_from': test[0].date,
               'params': None, 'expectancy_pct': None, 'total_pct': None,
               'n_trades': None, 'bh_total_pct': None}
        try:
            res = grid_search(train, model, space, cost, capital, objective, min_trades, base,
                              on_progress=(lambda i, t, k=k: on_progress((k + i / max(1, t)) / folds))
                              if on_progress else None, check=check)
            if res:
                _, p, _m = res[0]
                r = run(seg, model, p, cost, capital, trade_from=warm)
                bh = run(seg, MODELS['M1'], {}, cost, capital, trade_from=warm)
                rec.update(params=p,
                           expectancy_pct=r.metrics['expectancy_pct'],
                           total_pct=r.metrics['total_pct'],
                           n_trades=r.metrics['n_trades'],
                           bh_total_pct=bh.metrics['total_pct'])
        except Exception as e:      # 单折失败不中断整体走查
            from .control import Cancelled, Timeout
            if isinstance(e, (Cancelled, Timeout)):
                raise
            rec['error'] = str(e)
        rows.append(rec)

    exps = [r['expectancy_pct'] for r in rows if r.get('expectancy_pct') is not None]
    tots = [r['total_pct'] for r in rows if r.get('total_pct') is not None]
    bhs = [r['bh_total_pct'] for r in rows if r.get('bh_total_pct') is not None]
    return {
        'folds': len(rows),
        'test_days': test_days,
        'warmup_bars': rows[0].get('warmup_bars') if rows else None,
        'warmup_need': rows[0].get('warmup_need') if rows else None,
        'warmup_short': bool(rows and rows[0].get('warmup_short')),
        'rows': rows,
        'mean_expectancy': round(sum(exps) / len(exps), 2) if exps else None,
        'mean_total': round(sum(tots) / len(tots), 2) if tots else None,
        'mean_bh': round(sum(bhs) / len(bhs), 2) if bhs else None,
        'pos_folds': sum(1 for e in exps if e > 0),
    }


def optimize(bars, model_id, space, cost, capital=100000.0,
             test_days=61, objective='expectancy', min_trades=5, min_hold_default=3,
             base=None, on_progress=None, check=None):
    """完整诊断流水线，返回结构化结果（含热力图矩阵）。

    on_progress(0~1)：跨阶段（训练网格→全样本网格→测试网格→热力图）的总体进度；
    check()：协作式中断点，取消/超时由调用方翻译成异常。

    切分协议（A10）：测试段执行 = 「训练段尾部预热 + 测试段」，指标使用切分边界**之前**
    的已知数据（MA20/MA60 等在测试段首日即有值），交易/权益/指标只统计测试段内，
    **持仓在切分处重启**——不继承训练段的仓位与资金曲线，也不允许「全量回测后截取指标」
    （那会把训练期的持仓与资金带进测试段，等于偷看未来）。
    """
    model = MODELS[model_id]
    n = len(bars)
    if isinstance(min_trades, bool) or not isinstance(min_trades, int) or min_trades < 1:
        raise SpaceError(f'min_trades 需为 ≥1 的整数，收到 {min_trades!r}')
    _check_split(n, test_days)
    # 先校验空间（非法直接拒绝，避免跑一半才报错）
    checked = parse_space(space, allowed=_model_space_keys(model))

    # 热力图只扫 tp×sl（模型含这两个维度时）
    tp_vals = checked.get('tp', []) if ('tp' in checked and 'sl' in checked) else []
    sl_vals = checked.get('sl', []) if ('tp' in checked and 'sl' in checked) else []
    ncomb = count_combos(checked)
    tracker = _Tracker(ncomb * 3 + len(tp_vals) * len(sl_vals), on_progress)

    cut = max(min_hold_default + 5, n - test_days)
    train = bars[:cut]
    test = bars[cut:]

    # A10 切分协议：测试段执行时用训练段尾部预热指标，交易只从测试段首日开始。
    # 选参（grid_search）仍在纯训练段进行；oracle 口径的选参在预热后的测试段进行，
    # 但评分同样只统计正式段，四个口径的「可评分区间」完全一致。
    need = warmup_need(space, base)
    warm = min(need, len(train))
    short = warm < need
    seg = bars[cut - warm:]

    train_res = grid_search(train, model, space, cost, capital, objective, min_trades, base,
                            on_progress=tracker.span(ncomb), check=check)
    full_res = grid_search(bars, model, space, cost, capital, objective, min_trades, base,
                           on_progress=tracker.span(ncomb), check=check)

    def pack(res):
        if not res:
            return None
        score, p, m = res[0]
        return {'params': p, 'metrics': m, 'score': round(score, 4)}

    best = pack(train_res)                 # 诚实选参（训练段最优）
    best_all = pack(full_res)              # 全样本最优（乐观口径选参）

    # 三口径在测试段执行（统一带预热，trade_from 之后才允许交易）
    honest = run(seg, model, best['params'], cost, capital, trade_from=warm) if best else None
    biased = run(seg, model, best_all['params'], cost, capital, trade_from=warm) if best_all else None
    oracle_res = pack(grid_search(seg, model, space, cost, capital, objective, min_trades, base,
                                  on_progress=tracker.span(ncomb), check=check, trade_from=warm))
    oracle = run(seg, model, oracle_res['params'], cost, capital, trade_from=warm) if oracle_res else None

    # 买入持有（同口径，测试段）
    bh_test = run(seg, MODELS['M1'], {}, cost, capital, trade_from=warm)

    # 热力图：固定其他参数，扫描 止盈×止损（仅对含 tp/sl 的模型有意义）
    z = []
    if tp_vals and sl_vals and best:
        base_p = dict(best['params'])
        for sl in sl_vals:
            row = []
            for tp in tp_vals:
                if check:
                    check()
                cand = dict(base_p)
                cand['tp'] = tp
                cand['sl'] = sl
                try:
                    rr = run(train, model, cand, cost, capital)
                    e = rr.metrics['expectancy_pct'] if rr.metrics['n_trades'] >= min_trades else None
                except Exception:
                    e = None
                row.append(e)
                tracker.add(1)
            z.append(row)

    # 三档判定
    if honest is None or honest.metrics['expectancy_pct'] <= 0:
        verdict = 'red'
    elif honest.metrics['total_pct'] <= 0:
        verdict = 'yellow'
    else:
        verdict = 'green'

    return {
        'model_id': model_id, 'objective': objective,
        'train_range': [bars[0].date, bars[cut - 1].date],
        'test_range': [bars[cut].date, bars[-1].date],
        'test_days': test_days,
        'warmup_bars': warm,
        'warmup_need': need,
        'warmup_short': short,
        'warmup_range': [bars[cut - warm].date, bars[cut - 1].date] if warm else None,
        'scorable_from': test[0].date,
        'n_combos': count_combos(checked),
        'space': checked,
        'best': best, 'best_all': best_all,
        'honest': honest.metrics if honest else None,
        'biased': biased.metrics if biased else None,
        'oracle': oracle.metrics if oracle else None,
        'bh_test': bh_test.metrics['total_pct'],
        'optimistic_bias': round(
            (biased.metrics['expectancy_pct'] - honest.metrics['expectancy_pct']), 2)
            if (biased and honest) else 0.0,
        'transfer_loss': round(
            (oracle.metrics['expectancy_pct'] - honest.metrics['expectancy_pct']), 2)
            if (oracle and honest) else 0.0,
        'verdict': verdict,
        'heatmap': {'x': 'tp', 'y': 'sl', 'tp_values': tp_vals, 'sl_values': sl_vals, 'z': z},
    }
