# -*- coding: utf-8 -*-
"""跨股票汇总用例：数据集内逐票回测，可选对 M4 做样本外诚实口径诊断。

同步接口 POST /summary 与后台任务 POST /jobs/summary 共用。
汇总是最容易跑成分钟级的入口（N 只 × M 模型 + OOS 寻优），因此提供
progress（按「股票 × 模型」推进）与 check（取消/超时）。
"""
import json

from core.state import db
from db import repository
from engine.models.builtin import MODELS
from engine.optimize import optimize
from engine.space import SpaceError
from .errors import InvalidRequest, NotFound
from .run_service import run_one

# 已知个股名称回退表：数据集 names 列（JSON）缺失或不含该代码时的兜底
NAME_MAP = {
    '600186': '莲花控股', '000962': '东方钽业', '002896': '中大力德', '603220': '中贝通信',
    '603887': '城地香江', '603757': '大元泵业', '300001': '特锐德',
}

# 汇总页样本外诊断用的轻量网格：固定 entry=ma_cross，仅搜 min_hold/tp/sl
_OOS_LIGHT_GRID = {'min_hold': [1, 20], 'tp': [2, 30, 2], 'sl': [1, 15, 1]}

_METRIC_KEYS = ['total_pct', 'n_trades', 'expectancy_pct', 'win_rate', 'max_drawdown_pct']


def _metrics_only(payload):
    m = payload.get('metrics') or {}
    return {k: m.get(k) for k in _METRIC_KEYS}


def run_summary(*, dataset_id: int, models: list, cfg, include_oos: bool = False,
                test_days: int = 61, ctx=None) -> dict:
    """models: [{'id','params'}] 形式的普通 dict 列表（不依赖 pydantic 模型）。"""
    with db() as conn:
        ds = repository.get_dataset(conn, dataset_id)
        if not ds:
            raise NotFound('数据集不存在')
        codes = json.loads(ds['codes'])
        try:
            names = json.loads(ds.get('names') or '{}')
        except (json.JSONDecodeError, TypeError):
            names = {}
        all_bars = {c: repository.get_bars(conn, dataset_id, c) for c in codes}

    cost, capital = cfg.cost(), cfg.capital
    # 进度按「股票 × 模型」推进；OOS 另算一格，权重按 1:2（OOS 明显更贵）
    total_units = max(1, len(codes) * len(models) + (len(codes) if include_oos else 0) * 2)
    done = 0.0
    progress = ctx.progress if ctx is not None else None
    check = ctx.check if ctx is not None else None

    rows = []
    for code in codes:
        bars = all_bars.get(code) or []
        if not bars:
            continue
        if check:
            check()
        rec = {'code': code, 'name': names.get(code) or NAME_MAP.get(code, code),
               'n': len(bars), 'models': {}, 'oos': {}}
        for mr in models:
            if check:
                check()
            try:
                payload = run_one(bars, mr['id'], mr['params'], cost, capital)
            except ValueError as e:
                raise InvalidRequest(f"{mr['id']}/{code}: {e}")
            rec['models'][mr['id']] = _metrics_only(payload)
            done += 1
            if progress:
                progress(done / total_units, f'{code} {mr["id"]} 完成')
            if include_oos and mr['id'] == 'M4' and MODELS['M4'].grid:
                try:
                    o = optimize(bars, mr['id'], _OOS_LIGHT_GRID, cost, capital,
                                 test_days=test_days,
                                 on_progress=(lambda v: progress(done / total_units + v * 2 / total_units))
                                 if progress else None, check=check)
                    h = o.get('honest') or {}
                    rec['oos'][mr['id']] = {
                        'honest_exp': h.get('expectancy_pct'),
                        'honest_total': h.get('total_pct'),
                        'verdict': o.get('verdict'),
                    }
                except SpaceError as e:
                    rec['oos'][mr['id']] = {'honest_exp': None, 'honest_total': None,
                                            'verdict': None, 'error': str(e)}
                except Exception as e:
                    from engine.control import Cancelled, Timeout
                    if isinstance(e, (Cancelled, Timeout)):
                        raise
                    rec['oos'][mr['id']] = {'honest_exp': None, 'honest_total': None,
                                            'verdict': None, 'error': str(e)}
                done += 2
        rows.append(rec)
        if progress:
            progress(done / total_units, f'已完成 {len(rows)}/{len(codes)} 只')

    return {
        'rows': rows,
        'model_ids': [m['id'] for m in models],
        'include_oos': include_oos,
        'test_days': test_days,
    }
