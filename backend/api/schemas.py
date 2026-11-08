# -*- coding: utf-8 -*-
"""API 共享请求模型与配置解析（单一真源）。

所有入口（/runs、/optimize、/plan、/summary）都必须走 GlobalReq.to_config()，
避免出现「寻优只传滑点、计划只传本金」这类隐藏默认值（A01）。
"""
from fastapi import HTTPException
from pydantic import BaseModel, Field

from engine.config import ExecutionConfig


class GlobalReq(BaseModel):
    capital: float = 100000.0
    commission: float = 0.00025
    min_commission: float = 5.0
    stamp_duty: float = 0.0005
    transfer_fee: float = 0.00001
    slippage_pct: float = 0.0          # 单边滑点 %，默认 0（不启用）

    def to_config(self) -> ExecutionConfig:
        try:
            return ExecutionConfig(**self.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f'执行配置非法: {e}')


def config_snapshot(cfg: ExecutionConfig) -> dict:
    """响应里的配置快照：前端据此判断「当前参数是否与结果一致」。"""
    return {'config': cfg.to_dict(), 'config_hash': cfg.fingerprint()}


class SeedReq(BaseModel):
    path: str
    name: str | None = None


class CrawlReq(BaseModel):
    codes: list[str] = Field(min_length=1)
    start: str = ''
    end: str = ''
    days: int = 120
    name: str = ''
