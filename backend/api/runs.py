# -*- coding: utf-8 -*-
"""回测执行接口：POST /runs（核心）、GET /runs、GET /runs/{id}。"""
import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import core.state as state
from application.run_service import outcome_summary, params_fingerprint, run_one, trade_to_dict
from db import experiments, repository
from application.tier_service import evaluate_tiers, TIER_VERSION
from .schemas import GlobalReq, config_snapshot

router = APIRouter(prefix='/api')


class ModelReq(BaseModel):
    id: str
    params: dict = Field(default_factory=dict)


class RunReq(BaseModel):
    include_tiers: bool = False
    dataset_id: int
    codes: list[str] = Field(default_factory=list, max_length=50)
    models: list[ModelReq] = Field(min_length=1, max_length=20)
    global_params: GlobalReq = Field(default_factory=GlobalReq)


def _comparison(results):
    agg = {}
    for r in results:
        a = agg.setdefault(r['model_id'], {'total': [], 'exp': [], 'mdd': []})
        a['total'].append(r['metrics']['total_pct'])
        a['exp'].append(r['metrics']['expectancy_pct'])
        a['mdd'].append(r['metrics']['max_drawdown_pct'])
    out = []
    for mid, a in agg.items():
        n = len(a['total'])
        out.append({
            'model_id': mid,
            'avg_total_pct': round(sum(a['total']) / n, 2),
            'avg_expectancy_pct': round(sum(a['exp']) / n, 2),
            'avg_mdd_pct': round(sum(a['mdd']) / n, 2),
            'n': n,
        })
    return out


@router.post('/runs')
def post_run(req: RunReq):
    return {"ok": True, "data": build_run_payload(req)}


def build_run_payload(req: RunReq, ctx=None):
    t0 = time.time()
    with state.db() as conn:
        ds = repository.get_dataset(conn, req.dataset_id)
        if not ds:
            raise HTTPException(404, '数据集不存在')
        if not req.models:
            raise HTTPException(422, '至少选择一个模型')
        cfg = req.global_params.to_config()
        cost = cfg.cost()
        codes = req.codes or json.loads(ds['codes'])
        results = []
        for code in codes:
            bars = repository.get_bars(conn, ds['id'], code)
            if not bars:
                continue
            for mr in req.models:
                if ctx:
                    ctx.check()
                    ctx.progress(len(results) / max(1, len(codes) * len(req.models)), f"评估 {code} / {mr.id}")
                try:
                    payload = run_one(bars, mr.id, mr.params, cost, cfg.capital)
                except ValueError as e:
                    raise HTTPException(422, f'{mr.id}/{code}: {e}')
                except Exception as e:
                    raise HTTPException(500, f'{mr.id}/{code}: {e}')
                results.append({'code': code, 'model_id': payload['model_id'], 'name': payload['name'],
                                'tradable': payload['tradable'], 'color': payload['color'],
                                'metrics': payload['metrics'], 'trades': payload['trades'],
                                'equity': payload['equity'], 'position': payload['position'],
                                'markers': payload['markers'], 'final': payload.get('final', {})})
                if req.include_tiers:
                    results[-1]['tiers'] = evaluate_tiers(bars, mr.id, mr.params, cost, cfg.capital, ctx, payload)
                    results[-1]['params'] = mr.params
        if ctx:
            ctx.check()
        if not results:
            raise HTTPException(422, '所选代码在该数据集内无行情数据')
        duration_ms = int((time.time() - t0) * 1000)
        run_id = 'run_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
        params_hash = params_fingerprint(ds.get('data_hash', ''), req.models, cfg)
        if req.include_tiers:
            params_hash += '_' + TIER_VERSION
        repository.save_run(conn, run_id, ds['id'],
                            json.dumps([m.model_dump() for m in req.models], ensure_ascii=False),
                            json.dumps(req.global_params.model_dump(), ensure_ascii=False), params_hash,
                            duration_ms, 'ok', None, results, config_hash=cfg.fingerprint())
        # 实验记录（A11）：写入可重放规格 + 摘要，前端「实验」页可检索、可一键重跑
        spec = experiments.make_spec(
            kind='backtest', dataset_id=ds['id'], dataset_hash=ds.get('data_hash', ''),
            code=codes[0] if len(codes) == 1 else None, model_id=None,
            body={'include_tiers': req.include_tiers, 'tier_version': TIER_VERSION if req.include_tiers else None, 'codes': codes,
                  'models': [m.model_dump() for m in req.models],
                  'global_params': req.global_params.model_dump()})
        exp_id = experiments.create(
            conn, spec,
            {**outcome_summary(results), 'run_id': run_id, 'params_hash': params_hash},
            duration_ms=duration_ms)
    return {
        'run_id': run_id, 'experiment_id': exp_id, 'duration_ms': duration_ms,
        'params_hash': params_hash,
        **config_snapshot(cfg),
        'dataset_id': ds['id'], 'codes': [r['code'] for r in results],
        'results': results, 'comparison': _comparison(results),
    }


@router.get('/runs')
def list_runs(limit: int = 50):
    with state.db() as conn:
        return {'ok': True, 'data': repository.list_runs(conn, limit)}


@router.get('/runs/{run_id}')
def get_run(run_id: str):
    with state.db() as conn:
        rec = repository.get_run(conn, run_id)
    if not rec:
        raise HTTPException(404, '运行记录不存在')
    return {'ok': True, 'data': rec}
