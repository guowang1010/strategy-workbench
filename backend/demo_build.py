# -*- coding: utf-8 -*-
"""生成高保真原型所需数据：真实 300 日行情 + 4 个模型回测 + 寻优网格"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.importer import parse_csv
from engine.bars import build_bars
from engine.data import ma, donchian, bollinger
from engine.config import DEFAULT_CONFIG
from engine.backtest import run
from engine.models.builtin import MODELS, dp_oracle
from engine import metrics as M
from engine.audit import audit

_HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(_HERE, 'data', 'sample_600186.csv')
OUT = os.path.join(_HERE, '..', 'frontend', 'demo_data.json')
CAP = DEFAULT_CONFIG.capital

# A09：CSV→Bar 与生产落库同一条转换路径（importer 校验 + bars 复权）
_parsed = parse_csv(CSV)
_code = next(iter(_parsed['groups']))
bars = build_bars(_parsed['groups'][_code])
cost = DEFAULT_CONFIG.cost()
n = len(bars)
print('bars', n, bars[0].date, '->', bars[-1].date)

# ---------- 指标 ----------
closes = [b.adj_close for b in bars]
ind = {
    'ma5': ma(closes, 5), 'ma10': ma(closes, 10),
    'ma20': ma(closes, 20), 'ma60': ma(closes, 60),
}
du, dm, dl = donchian(bars, 20)
ind.update(don_up=du, don_mid=dm, don_lo=dl)
bu, bm, bl = bollinger(bars, 20, 2.0)
ind.update(boll_up=bu, boll_mid=bm, boll_lo=bl)

# ---------- 回测 ----------
runs = []
for mid in ['M1', 'M4', 'M3', 'M6']:
    m = MODELS[mid]
    # 与前端参数面板语义严格对齐：M4 用面板默认值；M3 用 ma5 + 面板的 min_hold/max_hold；M6 用 ma20 + 无最长持有
    prm = {'tp': 9, 'sl': 12, 'min_hold': 3, 'max_hold': 12} if mid == 'M4' else (
        {'ma_n': 5, 'min_hold': 3, 'max_hold': 12} if mid == 'M3' else (
            {'ma_n': 20, 'min_hold': 3, 'max_hold': None} if mid == 'M6' else {}))
    r = run(bars, m, prm, cost, CAP)
    runs.append({
        'model_id': r.model_id, 'name': r.name, 'tradable': r.tradable, 'color': r.color,
        'metrics': r.metrics, 'trades': [t.__dict__ if hasattr(t, '__dict__') else {
            'seq': t.seq, 'buy_date': t.buy_date, 'buy_price': t.buy_price,
            'sell_date': t.sell_date, 'sell_price': t.sell_price, 'hold_days': t.hold_days,
            'gross_pct': t.gross_pct, 'net_pct': t.net_pct, 'exit_reason': t.exit_reason,
            'entry_reason': t.entry_reason,
            'mfe_pct': t.mfe_pct, 'mae_pct': t.mae_pct} for t in r.trades],
        'equity': r.equity, 'position': r.position, 'markers': r.markers,
    })
    print(f"  {r.model_id} {r.name}: {r.metrics['total_pct']:+.2f}%  "
          f"{r.metrics['n_trades']}笔 胜率{r.metrics['win_rate']}% 期望{r.metrics['expectancy_pct']:+.2f}%")

oracle = dp_oracle(bars, 3, cost, CAP)
print('  DP oracle:', oracle['total_pct'], '%', oracle['n_trades'], '笔')

# ---------- 寻优（训练/测试切分）----------
TEST_DAYS = 61
cut = n - TEST_DAYS
tps = list(range(3, 19))
sls = list(range(2, 13))
z, best = [], None
for sl in sls:
    row = []
    for tp in tps:
        tr = run(bars[:cut], MODELS['M4'], {'tp': tp, 'sl': sl, 'min_hold': 3, 'max_hold': 12}, cost, CAP)
        e = tr.metrics['expectancy_pct'] if tr.metrics['n_trades'] >= 5 else None
        row.append(e)
        if e is not None and (best is None or e > best['train_exp']):
            best = {'tp': tp, 'sl': sl, 'train_exp': e,
                    'train_total': tr.metrics['total_pct'], 'train_trades': tr.metrics['n_trades']}
    z.append(row)

bsp = {'tp': best['tp'], 'sl': best['sl'], 'min_hold': 3, 'max_hold': 12}
test = run(bars[cut:], MODELS['M4'], bsp, cost, CAP)
full = run(bars, MODELS['M4'], bsp, cost, CAP)
bh_test = run(bars[cut:], MODELS['M1'], {}, cost, CAP)

# 全样本最优（用于展示乐观偏差）
zall, best_all = [], None
for sl in sls:
    row = []
    for tp in tps:
        tr = run(bars, MODELS['M4'], {'tp': tp, 'sl': sl, 'min_hold': 3, 'max_hold': 12}, cost, CAP)
        e = tr.metrics['expectancy_pct'] if tr.metrics['n_trades'] >= 5 else None
        row.append(e)
        if e is not None and (best_all is None or e > best_all[0]):
            best_all = (e, tp, sl)
    zall.append(row)

biased = run(bars[cut:], MODELS['M4'],
             {'tp': best_all[1], 'sl': best_all[2], 'min_hold': 3, 'max_hold': 12}, cost, CAP)

# 三档判定：单笔期望<=0 直接红；期望为正但样本外总收益仍为负 -> 黄（边际正向但不足以覆盖成本/回撤）；
# 期望与总收益同时为正才判绿。
if test.metrics['expectancy_pct'] <= 0:
    verdict = 'red'
elif test.metrics['total_pct'] <= 0:
    verdict = 'yellow'
else:
    verdict = 'green'

opt = {
    'tp_values': tps, 'sl_values': sls, 'z': z, 'z_all': zall,
    'train_range': [bars[0].date, bars[cut - 1].date],
    'test_range': [bars[cut].date, bars[-1].date],
    'best': best,
    'bh_test': bh_test.metrics['total_pct'],
    'test': test.metrics, 'full': full.metrics,
    'optimistic': {'tp': best_all[1], 'sl': best_all[2], 'exp': best_all[0],
                   'test_exp': biased.metrics['expectancy_pct'],
                   'bias': round(biased.metrics['expectancy_pct'] - test.metrics['expectancy_pct'], 2)},
    'verdict': verdict,
}

# 未来函数自检（对主模型 M4）
aud = audit(bars, MODELS['M4'], {'tp': 9, 'sl': 12, 'min_hold': 3, 'max_hold': 12}, cost, CAP, cuts=50)
print('audit', aud['passed'], '/', aud['cuts'], 'failed:', aud['failed'][:3])

payload = {
    'meta': {'code': '600186', 'name': '莲花控股', 'start': bars[0].date, 'end': bars[-1].date,
             'n': n, 'capital': CAP, 'test_days': TEST_DAYS},
    'dates': [b.date for b in bars],
    'kline': [[b.adj_open, b.adj_close, b.adj_low, b.adj_high] for b in bars],
    'volume': [b.volume for b in bars],
    'amount': [b.amount for b in bars],
    'can_buy': [1 if b.can_buy else 0 for b in bars],
    'can_sell': [1 if b.can_sell else 0 for b in bars],
    'indicators': ind,
    'runs': runs,
    'oracle': oracle,
    'optimize': opt,
    'audit': aud,
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
print('written', OUT, os.path.getsize(OUT), 'bytes')

JS = OUT.replace('demo_data.json', 'demo_data.js')
with open(JS, 'w', encoding='utf-8') as f:
    f.write('window.DEMO_DATA = ')
    json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
    f.write(';\n')
print('written', JS, os.path.getsize(JS), 'bytes')
print('best train', best, '| test', test.metrics['total_pct'], test.metrics['expectancy_pct'],
      '| verdict', verdict)
