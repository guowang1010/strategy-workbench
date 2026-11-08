# -*- coding: utf-8 -*-
"""跨股票汇总接口：POST /summary（同步）/ POST /jobs/summary（后台任务）。

对数据集内全部个股，按所选模型跑全样本回测，拼成一张「个股 × 模型」对照表。
include_oos：额外对 M4 做样本外诚实口径诊断（训练/测试切分），给出红绿灯。
"""
import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from application.summary_service import run_summary
from .runs import ModelReq
from .schemas import GlobalReq, config_snapshot

router = APIRouter(prefix='/api')


class SummaryReq(BaseModel):
    dataset_id: int
    models: list[ModelReq] = Field(min_length=1, max_length=20)
    global_params: GlobalReq = Field(default_factory=GlobalReq)
    include_oos: bool = False
    test_days: int = Field(default=61, ge=5, le=5000)


def build_summary_payload(req: SummaryReq, ctx=None) -> dict:
    cfg = req.global_params.to_config()
    t0 = time.time()
    data = run_summary(
        dataset_id=req.dataset_id,
        models=[{'id': m.id, 'params': m.params} for m in req.models],
        cfg=cfg, include_oos=req.include_oos, test_days=req.test_days, ctx=ctx,
    )
    data.update(config_snapshot(cfg))
    data['duration_ms'] = int((time.time() - t0) * 1000)
    return data


@router.post('/summary')
def post_summary(req: SummaryReq):
    return {'ok': True, 'data': build_summary_payload(req)}
