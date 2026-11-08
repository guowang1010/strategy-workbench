# -*- coding: utf-8 -*-
"""后台任务接口（A08）：把寻优 / 跨股票汇总这类长任务 job 化。

提交即返回 202 + job_id，前端轮询 /api/jobs/{id} 拿进度与结果；
POST /api/jobs/{id}/cancel 立即置取消标志，计算在下一个组合循环检查点停下。
同步版 /api/optimize、/api/summary 保留（脚本与旧前端兼容），实现完全共用。
"""
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

import core.jobs as jobs
from application.errors import Busy, NotFound
from .optimize import OptimizeReq, build_optimize_payload
from .runs import RunReq, build_run_payload
from .summary import SummaryReq, build_summary_payload

router = APIRouter(prefix='/api')


def _submit(kind: str, params: dict, fn):
    try:
        job_id = jobs.submit(kind, params, fn)
    except jobs.TooManyJobs as e:
        raise Busy(str(e))
    return JSONResponse(status_code=202, content={
        'ok': True, 'data': jobs.get(job_id) or {'id': job_id, 'status': 'queued'}})


@router.post('/jobs/optimize')
def submit_optimize(req: OptimizeReq):
    return _submit('optimize', req.model_dump(),
                   lambda ctx: build_optimize_payload(req, ctx))


@router.post('/jobs/summary')
def submit_summary(req: SummaryReq):
    return _submit('summary', req.model_dump(),
                   lambda ctx: build_summary_payload(req, ctx))


@router.post('/jobs/tiers')
def submit_tiers(req: RunReq):
    req.include_tiers = True
    return _submit('tiers', req.model_dump(), lambda ctx: build_run_payload(req, ctx))


@router.get('/jobs')
def list_jobs(limit: int = Query(default=20, ge=1, le=100)):
    return {'ok': True, 'data': {'jobs': jobs.list_recent(limit), 'stats': jobs.stats()}}


@router.get('/jobs/{job_id}')
def get_job(job_id: str):
    rec = jobs.get(job_id)
    if not rec:
        raise NotFound('任务不存在')
    return {'ok': True, 'data': rec}


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id: str):
    rec = jobs.cancel(job_id)
    if not rec:
        raise NotFound('任务不存在')
    return {'ok': True, 'data': rec}
