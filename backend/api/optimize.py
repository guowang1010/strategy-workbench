# -*- coding: utf-8 -*-
"""参数寻优接口：POST /optimize（同步），返回三口径诊断 + 热力图 + 可选 K 折滚动走查。

长跑请走 POST /jobs/optimize（返回 job_id，可查进度/取消），计算实现完全一致。
"""
import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from application.optimize_service import run_optimize
from .schemas import GlobalReq, config_snapshot

router = APIRouter(prefix='/api')


class OptimizeReq(BaseModel):
    dataset_id: int
    code: str
    model_id: str
    test_days: int = Field(default=61, ge=5, le=5000)
    objective: Literal['expectancy', 'total', 'sharpe'] = 'expectancy'
    space: dict = Field(default_factory=dict)   # 为空则用模型默认 grid
    walk_forward_folds: int = Field(default=0, ge=0, le=12)
    global_params: GlobalReq = Field(default_factory=GlobalReq)
    slippage_pct: float | None = None           # 旧字段兼容：给了就覆盖 global_params.slippage_pct
    base_params: dict = Field(default_factory=dict)  # 固定参数（如 M9/M10 的规则树）


def resolve_config(req: OptimizeReq):
    gp = req.global_params
    if req.slippage_pct is not None:
        gp = gp.model_copy(update={'slippage_pct': req.slippage_pct})
    return gp.to_config()


def build_optimize_payload(req: OptimizeReq, ctx=None) -> dict:
    """一次完整寻优（同步接口与后台任务共用；ctx 提供 progress/check）。"""
    cfg = resolve_config(req)
    t0 = time.time()
    res = run_optimize(
        dataset_id=req.dataset_id, code=req.code, model_id=req.model_id, cfg=cfg,
        space=req.space or None, test_days=req.test_days, objective=req.objective,
        folds=req.walk_forward_folds, base_params=req.base_params or None, ctx=ctx,
        job_id=getattr(ctx, 'job_id', None),      # 实验记录关联到后台任务（A11）
    )
    res.update(config_snapshot(cfg))
    res['duration_ms'] = int((time.time() - t0) * 1000)
    return res


@router.post('/optimize')
def post_optimize(req: OptimizeReq):
    return {'ok': True, 'data': build_optimize_payload(req)}
