# -*- coding: utf-8 -*-
"""实验记录接口（A11）：列表 / 详情 / 重放请求体 / 删除。

每条记录都带 spec_hash（数据 + 请求体 + 引擎版本的内容哈希）：
同样的输入得到同样的指纹，因此可以判断「这次结果是不是上次那次的重跑」。
"""
from fastapi import APIRouter, Query

import core.state as state
from application.errors import NotFound
from db import experiments

router = APIRouter(prefix='/api')


@router.get('/experiments')
def list_experiments(kind: str | None = Query(default=None),
                     dataset_id: int | None = Query(default=None),
                     limit: int = Query(default=50, ge=1, le=500)):
    if kind and kind not in experiments.KINDS:
        raise NotFound(f'未知实验类型: {kind}')
    with state.db() as conn:
        items = experiments.list_experiments(conn, kind, dataset_id, limit)
        counts = experiments.stats(conn)
    return {'ok': True, 'data': {'items': items, 'stats': counts}}


@router.get('/experiments/{exp_id}')
def get_experiment(exp_id: int):
    with state.db() as conn:
        rec = experiments.get_experiment(conn, exp_id)
    if not rec:
        raise NotFound('实验记录不存在')
    return {'ok': True, 'data': rec}


@router.get('/experiments/{exp_id}/replay')
def replay_experiment(exp_id: int):
    """返回 {kind, body}：body 可直接 POST 到 /runs 或 /api/jobs/optimize。"""
    with state.db() as conn:
        rec = experiments.replay_body(conn, exp_id)
    if rec is None:
        raise NotFound('实验记录不存在')
    return {'ok': True, 'data': rec}


@router.delete('/experiments/{exp_id}')
def delete_experiment(exp_id: int):
    with state.db() as conn:
        ok = experiments.delete_experiment(conn, exp_id)
    if not ok:
        raise NotFound('实验记录不存在')
    return {'ok': True, 'data': {'deleted': exp_id}}
