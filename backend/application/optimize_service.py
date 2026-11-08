# -*- coding: utf-8 -*-
"""寻优用例：读行情 → 三口径诊断（+ 可选滚动走查）。

同步接口 POST /optimize 与后台任务 POST /jobs/optimize 共用这一份实现，
ctx 只提供 progress(v) / check()，不感知线程或 HTTP。
"""
import time

from core.state import db
from db import experiments, repository
from engine.models.builtin import MODELS
from engine.optimize import optimize, walk_forward
from engine.space import SpaceError
from .errors import InvalidRequest, NotFound


def load_dataset_bars(dataset_id: int, code: str):
    """读行情后立即关连接：计算阶段不占数据库连接（A07）。

    同时带回数据集元信息（data_hash 要进实验指纹，A11），避免再开一次连接。
    """
    with db() as conn:
        ds = repository.get_dataset(conn, dataset_id)
        return repository.get_bars(conn, dataset_id, code), ds


def _outcome(res: dict) -> dict:
    """把寻优结果压成摘要（热力图 n×n 与完整指标不重复存，重跑即可复现）。"""
    wf = res.get('walk_forward') or {}
    return {
        'verdict': res.get('verdict'),
        'best_params': (res.get('best') or {}).get('params'),
        'best_metrics': (res.get('best') or {}).get('metrics'),
        'honest': res.get('honest'), 'biased': res.get('biased'), 'oracle': res.get('oracle'),
        'bh_test': res.get('bh_test'),
        'optimistic_bias': res.get('optimistic_bias'),
        'transfer_loss': res.get('transfer_loss'),
        'n_combos': res.get('n_combos'),
        'train_range': res.get('train_range'), 'test_range': res.get('test_range'),
        'walk_forward': ({'folds': wf.get('folds'), 'mean_total': wf.get('mean_total'),
                          'mean_bh': wf.get('mean_bh'), 'pos_folds': wf.get('pos_folds')}
                         if wf else None),
    }


def run_optimize(*, dataset_id: int, code: str, model_id: str, cfg, space: dict | None = None,
                 test_days: int = 61, objective: str = 'expectancy', folds: int = 0,
                 base_params: dict | None = None, ctx=None, job_id: str | None = None) -> dict:
    """cfg: engine.config.ExecutionConfig（费用/本金/滑点的唯一来源）。"""
    t0 = time.time()
    if model_id not in MODELS:
        raise InvalidRequest(f'未知模型: {model_id}')
    bars, ds = load_dataset_bars(dataset_id, code)
    if not bars:
        raise NotFound('无行情数据')
    space = space or MODELS[model_id].grid
    if not space:
        raise InvalidRequest('该模型无默认搜索空间，请通过 space 指定')

    on_progress = ctx.progress if ctx is not None else None
    check = ctx.check if ctx is not None else None
    # 含走查时按 7:3 分配进度，避免进度条跑满后还要等一折
    opt_share = 0.7 if folds > 0 else 1.0
    opt_cb = (lambda v: on_progress(v * opt_share)) if on_progress else None
    wf_cb = (lambda v: on_progress(opt_share + (1 - opt_share) * v)) if on_progress else None

    try:
        res = optimize(bars, model_id, space, cfg.cost(), cfg.capital,
                       test_days=test_days, objective=objective,
                       base=base_params or None,
                       on_progress=opt_cb, check=check)
        if folds > 0:
            if on_progress:
                on_progress(opt_share, f'滚动走查 {folds} 折…')
            res['walk_forward'] = walk_forward(
                bars, model_id, space, cfg.cost(), cfg.capital,
                test_days=test_days, objective=objective,
                folds=folds, base=base_params or None,
                on_progress=wf_cb, check=check)
    except SpaceError as e:
        raise InvalidRequest(str(e), code='SPACE_INVALID')

    # 实验记录（A11）：规格可重放，结果摘要可对比；写库用独立连接，失败不影响返回结果
    spec = experiments.make_spec(
        kind='optimize', dataset_id=dataset_id, dataset_hash=(ds or {}).get('data_hash', ''),
        code=code, model_id=model_id,
        body={'space': space, 'objective': objective, 'test_days': test_days,
              'folds': folds, 'base_params': base_params or {},
              'global_params': cfg.to_dict()})
    try:
        with db() as conn:
            res['experiment_id'] = experiments.create(
                conn, spec, _outcome(res), job_id=job_id,
                duration_ms=int((time.time() - t0) * 1000))
    except Exception:                      # 记录失败不能把一次成功的寻优变成失败
        res['experiment_id'] = None
    return res
