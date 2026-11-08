"""Three deterministic local parameter variants; no Cartesian search."""
from copy import deepcopy
import math

from engine.models.builtin import MODELS
from .run_service import run_one

TIER_VERSION = 'local-three-v1'


def tier_specs(model_id, params):
    model = MODELS.get(model_id)
    if not model or model_id == 'M1':
        return []
    base = {**deepcopy(model.defaults), **deepcopy(params)}
    if model_id in ('M4', 'M9'):
        keys = ['tp', 'sl']
    elif model_id == 'M10':
        keys = ['max_hold'] if base.get('max_hold') is not None else ['min_hold']
    else:
        keys = {'ma_cross': ['ma_n'], 'breakout': ['lookback'],
                'pullback': ['ma_n', 'ma_long'], 'volume_confirm': ['vol_n', 'vol_ratio'],
                'momentum': ['mom_n', 'mom_th']}.get(base.get('entry', 'ma_cross'), [])
    fields = {f['key']: f for f in model.param_schema}
    for key in keys:
        base.setdefault(key, fields[key]['default'])
    out, seen = [], set()
    for tier_id, label, direction in [('A', '档位 A', -1), ('base', '当前档', 0), ('C', '档位 C', 1)]:
        p, error = deepcopy(base), None
        for key in keys:
            f, value = fields[key], base[key]
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                error = f"{f['label']}不是有效数值"
                break
            step = f.get('step', 1)
            delta = 1 if key in ('tp', 'sl') else max(step, math.floor(abs(value) * .1 / step + .5) * step)
            p[key] = round(value + direction * delta, 8)
            if f['type'] == 'int':
                p[key] = int(p[key])
            if not f.get('min', -math.inf) <= p[key] <= f.get('max', math.inf):
                error = f"{f['label']}超出允许范围"
        if p.get('max_hold') is not None and p.get('min_hold', 1) > p['max_hold']:
            error = '最小持仓天数超过最长持有天数'
        fingerprint = repr(sorted(p.items()))
        if fingerprint in seen:
            error = '与其他档位参数相同'
        seen.add(fingerprint)
        out.append({'id': tier_id, 'label': label, 'params': p, 'keys': keys, 'error': error})
    return out


def evaluate_tiers(bars, model_id, params, cost, capital, ctx=None, base_result=None):
    rows = tier_specs(model_id, params)
    for row in rows:
        if ctx:
            ctx.check()
        if row['error']:
            continue
        try:
            row['result'] = deepcopy(base_result) if row['id'] == 'base' and base_result else run_one(bars, model_id, row['params'], cost, capital)
        except ValueError as exc:
            row['error'] = str(exc)
    valid = [r for r in rows if 'result' in r]
    best = max(valid, key=lambda r: (r['result']['equity'][-1],
               -abs(r['result']['metrics']['max_drawdown_pct']), r['id'] == 'base'), default=None)
    return {'version': TIER_VERSION, 'rows': rows, 'best_id': best['id'] if best else None}
