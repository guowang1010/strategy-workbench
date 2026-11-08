# -*- coding: utf-8 -*-
"""计划跟踪接口（BUY-12）：生成并落库 / 列表 / 详情复盘 / 成交录入 / 取消 / 删除。

与 `/api/plan`（纯计算、不落库）分开：
  - `/api/plan` 用于「快速看一眼现在该不该买」，不产生任何记录；
  - `/api/plans` 用于「把这一笔记下来，之后对账」——计划快照落库后不可改写。
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

import core.state as state
from application.errors import InvalidRequest, NotFound
from application.plan_review import review
from application.plan_service import build_plan
from api.plan import PlanReq, resolve
from api.schemas import config_snapshot
from db import plans as plans_db
from db import repository
from engine.config import ExecutionConfig

router = APIRouter(prefix='/api')


class SavePlanReq(PlanReq):
    """生成并保存：除计划本身外，`dedupe` 控制是否复用未成交的同 plan_id 记录。"""
    dedupe: bool = True


class FillReq(BaseModel):
    side: str = 'buy'                          # buy / sell
    qty: float = Field(gt=0)
    price: float = Field(gt=0)
    fees: float = Field(default=0.0, ge=0)
    trade_date: str
    note: str | None = None


class CancelReq(BaseModel):
    reason: str = '用户取消'


def _snapshot(conn, record_id: int) -> dict:
    """记录 + 成交 + 复盘（复盘是实时算的，不落库，避免与快照不一致）。"""
    rec = plans_db.get_record(conn, record_id)
    if not rec:
        raise NotFound('计划记录不存在')
    rec['fills'] = plans_db.list_fills(conn, record_id)
    return {'record': rec, 'review': review(conn, rec)}


@router.post('/plans')
def save_plan(req: SavePlanReq):
    """生成计划并落库。返回 `reused=True` 表示命中了同 plan_id 的未成交旧记录。"""
    cfg, account, rules = resolve(req)
    try:
        with state.db() as conn:
            plan = build_plan(conn, dataset_id=req.dataset_id, code=req.code,
                              model_id=req.model_id, params=req.params, cfg=cfg,
                              account=account, rules=rules, research=req.research)
            plan.update(config_snapshot(cfg))
            rid, reused = plans_db.create(
                conn, plan=plan, dataset_id=req.dataset_id, code=req.code,
                model_id=req.model_id, as_of=plan.get('as_of_date') or '',
                params=(plan.get('identity') or {}).get('effective_params') or {},
                config_hash=cfg.fingerprint(),
                account=(plan.get('context') or {}).get('account_snapshot') or {},
                # 存**规范化后**的规则（引擎已补默认值），否则「没传」与「显式传默认值」
                # 会被当成两套输入，续期时还会丢掉默认项。
                rules=(plan.get('context') or {}).get('exec_rules') or rules or {},
                dedupe=req.dedupe)
            data = _snapshot(conn, rid)
    except LookupError as e:
        raise NotFound(str(e))
    except ValueError as e:
        raise InvalidRequest(f'计划保存失败: {e}')
    data.update({'record_id': rid, 'reused': reused, 'plan': plan})
    return {'ok': True, 'data': data}


@router.get('/plans')
def list_plans(code: str | None = Query(default=None),
               model_id: str | None = Query(default=None),
               lifecycle: str | None = Query(default=None),
               limit: int = Query(default=50, ge=1, le=500)):
    if lifecycle and lifecycle not in plans_db.LIFECYCLES:
        raise NotFound(f'未知生命周期: {lifecycle}')
    with state.db() as conn:
        items = plans_db.list_records(conn, code=code, model_id=model_id,
                                     lifecycle=lifecycle, limit=limit)
        counts = plans_db.stats(conn)
    return {'ok': True, 'data': {'items': items, 'stats': counts}}


@router.get('/plans/last')
def last_plan(code: str = Query(...), model_id: str | None = Query(default=None)):
    """该股票最近一次保存的计划（BUY-10：刷新后恢复，不必重新生成）。

    先按模型找，找不到再退到该股票任意模型（刷新后前端会回到默认模型）。
    声明在 `/plans/{record_id}` 之前：否则 'last' 会被当成 record_id 去做整数校验。
    """
    with state.db() as conn:
        rec = plans_db.last_record(conn, code=code, model_id=model_id)
        if not rec:
            return {'ok': True, 'data': {'found': False, 'record': None, 'review': None}}
        rec['fills'] = plans_db.list_fills(conn, rec['id'])
        return {'ok': True, 'data': {'found': True, 'record': rec, 'review': review(conn, rec)}}


@router.get('/plans/{record_id}')
def get_plan(record_id: int):
    with state.db() as conn:
        return {'ok': True, 'data': _snapshot(conn, record_id)}


@router.post('/plans/{record_id}/rollover')
def rollover_plan(record_id: int):
    """未成交续期（BUY-08）：用**最新行情**按同一模型/参数/账户/约束重新生成一条计划。

    三个刻意的边界：
      1. 续期不是「延长旧计划的有效期」——信号可能已经变了，所以是新记录，旧快照原样保留；
      2. 旧记录只写 `superseded_by`，不改 lifecycle、不改 plan，复盘仍然读它当时的判断；
      3. 已有成交/已取消的记录不允许续期（前者该加仓或卖出，后者是用户主动放弃的决定）。
    """
    try:
        with state.db() as conn:
            old = plans_db.get_record(conn, record_id)
            if not old:
                raise NotFound('计划记录不存在')
            if old['fills_count']:
                raise InvalidRequest('该计划已有实际成交，无需续期；如需加仓请重新生成计划')
            if old.get('superseded_by'):
                raise InvalidRequest(f"该计划已续期到 #{old['superseded_by']}，请直接打开新记录")
            if old.get('cancel_reason'):
                raise InvalidRequest('该计划已取消；如需继续，请重新生成并保存')

            snap = old.get('plan') or {}
            try:
                cfg = ExecutionConfig(**(snap.get('config') or {}))
            except (TypeError, ValueError) as e:
                raise InvalidRequest(f'计划快照中的执行配置不可用，无法续期：{e}')
            research = bool((snap.get('validity') or {}).get('research_mode'))
            # 续期要的是「现在能看到的最新行情」：数据集是版本化快照，原数据集不会自己变长，
            # 所以按代码找最新数据集（找不到才退回原数据集）。
            newest = repository.latest_dataset_for_code(conn, old['code'])
            ds_id = newest['id'] if newest else old['dataset_id']
            ds_name = newest['name'] if newest else ''
            plan = build_plan(conn, dataset_id=ds_id, code=old['code'],
                              model_id=old['model_id'], params=old.get('params') or {},
                              cfg=cfg, account=old.get('account') or None,
                              rules=old.get('rules') or None, research=research)
            plan.update(config_snapshot(cfg))
            rid, reused = plans_db.create(
                conn, plan=plan, dataset_id=ds_id, code=old['code'],
                model_id=old['model_id'], as_of=plan.get('as_of_date') or '',
                params=(plan.get('identity') or {}).get('effective_params') or {},
                config_hash=cfg.fingerprint(),
                account=(plan.get('context') or {}).get('account_snapshot') or {},
                rules=old.get('rules') or {}, dedupe=True, renewed_from=record_id)
            unchanged = reused and rid == record_id
            if not unchanged:
                plans_db.mark_superseded(conn, record_id, rid)
            data = _snapshot(conn, rid)
    except LookupError as e:
        raise NotFound(str(e))
    except ValueError as e:
        raise InvalidRequest(f'续期失败: {e}')

    if unchanged:
        note = f'行情未更新：计划与 #{record_id} 完全一致，未生成新记录'
    else:
        basis = f"（依据 {plan.get('as_of_date')} 收盘确认"
        basis += f"，数据集「{ds_name}」）" if ds_name and ds_id != old['dataset_id'] else '）'
        if plan.get('status') != 'signal_confirmed':
            note = (f"已按最新行情续期到 #{rid}，但信号变成「{plan.get('status_text')}」——"
                    f'续期不等于继续有效，请按新计划执行{basis}')
        else:
            note = f'已按最新行情续期到 #{rid}{basis}'
    data.update({'record_id': rid, 'reused': reused, 'unchanged': unchanged,
                 'renewed_from': record_id, 'plan': plan, 'note': note})
    return {'ok': True, 'data': data}


@router.post('/plans/{record_id}/fills')
def add_fill(record_id: int, body: FillReq):
    try:
        with state.db() as conn:
            fill = plans_db.add_fill(conn, record_id, **body.model_dump())
            data = _snapshot(conn, record_id)
    except LookupError as e:
        raise NotFound(str(e))
    except ValueError as e:
        raise InvalidRequest(str(e))
    data['fill'] = fill
    return {'ok': True, 'data': data}


@router.delete('/plans/{record_id}/fills/{fill_id}')
def delete_fill(record_id: int, fill_id: int):
    with state.db() as conn:
        ok = plans_db.delete_fill(conn, record_id, fill_id)
        if not ok:
            raise NotFound('成交记录不存在')
        data = _snapshot(conn, record_id)
    return {'ok': True, 'data': data}


@router.post('/plans/{record_id}/cancel')
def cancel_plan(record_id: int, body: CancelReq):
    try:
        with state.db() as conn:
            plans_db.cancel(conn, record_id, body.reason)
            data = _snapshot(conn, record_id)
    except LookupError as e:
        raise NotFound(str(e))
    except ValueError as e:
        raise InvalidRequest(str(e))
    return {'ok': True, 'data': data}


@router.post('/plans/{record_id}/reopen')
def reopen_plan(record_id: int):
    """撤销取消（误点时用）。"""
    try:
        with state.db() as conn:
            plans_db.reopen(conn, record_id)
            data = _snapshot(conn, record_id)
    except LookupError as e:
        raise NotFound(str(e))
    return {'ok': True, 'data': data}


@router.delete('/plans/{record_id}')
def delete_plan(record_id: int):
    with state.db() as conn:
        ok = plans_db.delete_record(conn, record_id)
    if not ok:
        raise NotFound('计划记录不存在')
    return {'ok': True, 'data': {'deleted': record_id}}
