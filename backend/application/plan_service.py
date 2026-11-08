# -*- coding: utf-8 -*-
"""计划用例：装配数据集/日历/账户/验证记录，再交给纯函数引擎（engine.plan）。

分层理由：引擎只做「给定 bars + 参数 + 账户 → 计划」的纯计算；要查库的东西
（数据集哈希、交易日并集、同参数实验记录）都在这里组装，前端不再自行拼结论。
"""
from engine.models.builtin import MODELS
from engine.plan import next_plan
from engine.trading_calendar import calendar_from_dates, load_holidays
from db import experiments, repository


def _dataset_calendar(conn, ds_id: int):
    dates = repository.list_dates(conn, ds_id)
    return calendar_from_dates(dates, load_holidays())


def _matching_evidence(conn, ds: dict, code: str, model_id: str,
                       effective_params: dict, config: dict, limit: int = 3) -> dict:
    """找「同数据 + 同代码 + 同模型 + 同有效参数 + 同执行配置」的回测记录。

    注意这些记录都是**样本内**全样本回测：可以证明「这套参数跑过」，不能证明
    「样本外有效」。所以一律带 in_sample_only=True，前端不得渲染成置信度。
    """
    try:
        rows = experiments.list_experiments(conn, kind='backtest', dataset_id=ds['id'],
                                           limit=200, include_spec=True)
    except Exception as e:                       # 实验表异常不应阻断计划
        return {'status': 'unavailable', 'records': [], 'note': f'实验记录不可用：{e}'}
    hits = []
    for rec in rows:
        spec = rec.get('spec') or {}
        if (spec.get('dataset_hash') or '') != (ds.get('data_hash') or ''):
            continue
        body = spec.get('body') or {}
        if (spec.get('code') or (body.get('codes') or [None])[0]) != code:
            continue
        if (spec.get('model_id') or '') not in ('', model_id):
            continue
        matched_model = None
        for m in body.get('models') or []:
            if m.get('id') != model_id:
                continue
            merged = {**MODELS[model_id].defaults, **(m.get('params') or {})}
            if merged == effective_params:
                matched_model = m
                break
        if matched_model is None:
            continue
        if (body.get('global_params') or {}) != config:
            continue
        outcome = rec.get('outcome') or {}
        rows_out = [r for r in (outcome.get('rows') or [])
                    if r.get('code') == code and r.get('model_id') == model_id]
        hits.append({
            'experiment_id': rec.get('id'),
            'created_at': rec.get('created_at'),
            'spec_hash': rec.get('spec_hash'),
            'run_id': outcome.get('run_id'),
            'params_hash': outcome.get('params_hash'),
            'metrics': rows_out[0] if rows_out else None,
            'in_sample_only': True,
        })
        if len(hits) >= limit:
            break
    if not hits:
        return {'status': 'not_found', 'records': [],
                'note': '未找到同参数的回测记录：该计划尚未被任何研究记录覆盖'}
    return {
        'status': 'matched',
        'records': hits,
        'in_sample_only': True,
        'note': '同参数全样本回测（样本内）。这**不是**样本外验证，也不代表下一笔盈利概率',
        'oos': None,
        'oos_note': '暂无与「该 as_of 时点 + 该参数」匹配的样本外记录',
    }


def build_plan(conn, *, dataset_id: int, code: str, model_id: str, params: dict,
               cfg, account: dict | None = None, rules: dict | None = None,
               research: bool = False, now=None) -> dict:
    """`now`（F01）：诊断/复现用的时点覆盖；缺省取真实当前时刻（Asia/Shanghai）。

    「数据是否落后」必须按**最新已完成日线**判断，而不是按日期差——盘前/盘中
    都还看不到当天的日线，把「今天」当成「今天的数据」会让周五的计划在周一开盘前作废。
    """
    ds = repository.get_dataset(conn, dataset_id)
    if not ds:
        raise LookupError('数据集不存在')
    if model_id not in MODELS:
        raise ValueError(f'未知模型: {model_id}')
    bars = repository.get_bars(conn, dataset_id, code)
    if not bars:
        raise LookupError('无行情数据')

    model = MODELS[model_id]
    p = {**model.defaults, **(params or {})}
    cal = _dataset_calendar(conn, dataset_id)
    evidence = _matching_evidence(conn, ds, code, model_id, p, cfg.to_dict())

    plan = next_plan(
        bars, model, params or {}, cfg.cost(), cfg.capital,
        calendar=cal, account=account, rules=rules,
        dataset={'id': ds['id'], 'name': ds.get('name'), 'data_hash': ds.get('data_hash', ''), 'code': code},
        evidence=evidence, research=research, config_hash=cfg.fingerprint(), now=now,
    )
    plan['dataset'] = {'id': ds['id'], 'name': ds.get('name'),
                       'start_date': ds.get('start_date'), 'end_date': ds.get('end_date'),
                       'bar_count': ds.get('bar_count'), 'data_hash': ds.get('data_hash', '')}
    plan['code'] = code
    return plan
