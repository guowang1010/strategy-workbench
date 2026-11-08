# -*- coding: utf-8 -*-
"""单票回测用例：模型分发 + 结果序列化 + 参数指纹（供 runs / summary / plan 复用）。"""
import hashlib
import json

from engine.backtest import run as backtest_run
from engine.models.builtin import MODELS, dp_oracle_result
from engine.version import ENGINE_VERSION


def trade_to_dict(t):
    return {
        'seq': t.seq, 'buy_date': t.buy_date, 'buy_price': t.buy_price,
        'sell_date': t.sell_date, 'sell_price': t.sell_price, 'hold_days': t.hold_days,
        'gross_pct': t.gross_pct, 'net_pct': t.net_pct, 'exit_reason': t.exit_reason,
        'entry_reason': t.entry_reason,
        'mfe_pct': t.mfe_pct, 'mae_pct': t.mae_pct,
    }


def run_one(bars, model_id, params, cost, capital):
    if model_id == 'M2':
        return dp_oracle_result(bars, int(params.get('min_hold', 3) or 3), cost, capital)
    if model_id not in MODELS:
        raise ValueError(f'未知模型: {model_id}')
    r = backtest_run(bars, MODELS[model_id], params, cost, capital)
    return {
        'model_id': r.model_id, 'name': r.name, 'tradable': r.tradable, 'color': r.color,
        'metrics': r.metrics,
        'trades': [trade_to_dict(t) for t in r.trades],
        'equity': r.equity, 'position': r.position, 'markers': r.markers,
        'final': r.final,
    }


def params_fingerprint(dataset_hash: str, models: list, cfg) -> str:
    """内容指纹：同一数据 + 同一参数 + 同一执行配置 + 同一引擎版本 → 同一哈希。

    引擎版本进哈希（A11）：撮合/费用/指标口径一变，旧结果就不该再被当成同一次实验。
    """
    payload = {
        'dataset': dataset_hash or '',
        'models': sorted(
            [{'id': m.id, 'params': json.loads(json.dumps(m.params, sort_keys=True))} for m in models],
            key=lambda x: x['id']),
        'config': cfg.to_dict(),
        'engine_version': ENGINE_VERSION,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def outcome_summary(results: list) -> dict:
    """回测结果摘要（A11）：每只股票 × 每个模型一行关键指标，用于实验列表展示。"""
    rows = []
    for r in results:
        m = r.get('metrics') or {}
        rows.append({
            'code': r.get('code'), 'model_id': r.get('model_id'),
            'total_pct': m.get('total_pct'), 'n_trades': m.get('n_trades'),
            'expectancy_pct': m.get('expectancy_pct'),
            'max_drawdown_pct': m.get('max_drawdown_pct'),
            'sharpe': m.get('sharpe'),
        })
    return {
        'codes': sorted({r['code'] for r in rows if r.get('code')}),
        'rows': rows,
    }

