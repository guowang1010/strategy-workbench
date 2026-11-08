# -*- coding: utf-8 -*-
"""下一笔交易计划接口：POST /api/plan。

请求兼容旧字段（params / capital / slippage_pct），新增：
  - account：strategy_simulated（默认，回放账本）| manual_actual（手动快照，不被回测覆盖）
  - exec_rules：最大仓位、单笔风险预算、可接受买价上限、偏离上限、是否只等新信号
  - research：行情已陈旧时是否按研究模式查看（不冒充当前待执行计划）
"""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import core.state as state
from application.plan_service import build_plan
from .schemas import GlobalReq, config_snapshot

router = APIRouter(prefix='/api')


class AccountReq(BaseModel):
    mode: str = 'strategy_simulated'          # strategy_simulated | manual_actual
    cash: float | None = None
    shares: float | None = None
    sellable: float | None = None
    cost_price: float | None = None
    snapshot_at: str | None = None
    entry_date: str | None = None
    allow_addon: bool = False


class ExecRulesReq(BaseModel):
    """执行约束。边界在 API 层就拒绝（D01/D03）：

    - max_position_pct=0 曾因 `or 100.0` 被静默当成满仓 → 现在 422；
    - 负数/±inf/NaN 一律 422，不允许进入引擎后再产生「奇怪但可算」的结果。
    """
    max_price: float | None = Field(None, gt=0, allow_inf_nan=False)
    max_deviation_pct: float | None = Field(None, ge=0, allow_inf_nan=False)
    max_position_pct: float = Field(100.0, gt=0, le=100, allow_inf_nan=False)
    risk_pct: float | None = Field(None, ge=0, allow_inf_nan=False)
    follow_from: Literal['allow_existing', 'new_signal_only'] = 'allow_existing'
    day_only: bool = True


class PlanReq(BaseModel):
    dataset_id: int
    code: str
    model_id: str
    params: dict = Field(default_factory=dict)
    global_params: GlobalReq = Field(default_factory=GlobalReq)
    capital: float | None = None              # 旧字段兼容：给了就覆盖 global_params.capital
    slippage_pct: float | None = None         # 旧字段兼容
    account: AccountReq | None = None
    exec_rules: ExecRulesReq | None = None
    research: bool = False
    # 诊断用：覆盖「现在」（ISO 8601，无时区按 Asia/Shanghai 解释）。
    # 盘前/盘中/收盘后是三种不同结论，留这个口子才能复现 F01 的边界，而不必改系统时钟。
    now: str | None = None


def resolve(req: 'PlanReq') -> tuple:
    """把请求解析成 (cfg, account, rules)，生成与落库共用同一份解析（避免两处漂移）。"""
    gp = req.global_params
    patch = {}
    if req.capital is not None:
        patch['capital'] = req.capital
    if req.slippage_pct is not None:
        patch['slippage_pct'] = req.slippage_pct
    if patch:
        gp = gp.model_copy(update=patch)
    cfg = gp.to_config()
    account = req.account.model_dump() if req.account else None
    rules = req.exec_rules.model_dump() if req.exec_rules else None
    return cfg, account, rules


@router.post('/plan')
def post_plan(req: PlanReq):
    cfg, account, rules = resolve(req)

    try:
        with state.db() as conn:
            plan = build_plan(conn, dataset_id=req.dataset_id, code=req.code,
                              model_id=req.model_id, params=req.params, cfg=cfg,
                              account=account, rules=rules, research=req.research,
                              now=req.now)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, f'计划生成失败: {e}')
    except Exception as e:                      # 不把堆栈抛给前端
        raise HTTPException(500, f'计划生成失败: {e}')

    plan.update(config_snapshot(cfg))
    return {'ok': True, 'data': plan}
