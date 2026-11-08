# -*- coding: utf-8 -*-
"""P0 回归集：执行配置统一 / 期末持仓 / 搜索空间 / 零值排序 / 导入校验 / API 契约。

对应 2026-11-03 架构审查清单 A01–A06、A12、A13。
"""
import datetime as _dt
import json
import math
import os
import sqlite3

import pytest

from db import importer, repository
from engine.backtest import run
from engine.config import ExecutionConfig
from engine.cost import Cost
from engine.models.base import Model
from engine.models.builtin import MODELS
from engine.optimize import _model_space_keys, _objective, optimize
from engine.space import SpaceError, count_combos, expand, parse_space
from engine.types import Bar

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_CSV = os.path.join(HERE, '..', 'data', 'sample_600186.csv')


def make_bars(n=80, base=10.0, amp=1.5):
    """确定性之字形行情：足以让止盈止损模型产生多笔交易。"""
    d0 = _dt.date(2024, 1, 1)
    bars = []
    for i in range(n):
        px = round(base + amp * math.sin(i / 3.0), 2)
        bars.append(Bar(
            date=(d0 + _dt.timedelta(days=i)).isoformat(),
            open=px, high=round(px * 1.02, 2), low=round(px * 0.98, 2), close=px, pre_close=px,
            volume=10000, amount=10000 * px, turnover=1.0, factor=1.0,
            adj_open=px, adj_high=round(px * 1.02, 2), adj_low=round(px * 0.98, 2), adj_close=px,
            limit_up=round(px * 1.1, 2), limit_down=round(px * 0.9, 2),
        ))
    return bars


class LastBarBuy(Model):
    """在倒数第二根发出买入信号 → 末日开盘买入，用于验证 T+1 不得当日卖出。"""
    id = 'T1'
    name = 'test-last-buy'
    tradable = True
    uses_tp_sl = False
    exit_on_signal = False
    defaults: dict = {}
    param_schema: list = []

    def target(self, bars, p):
        n = len(bars)
        return [1 if i >= n - 2 else 0 for i in range(n)]


class LateBuy(Model):
    """倒数第三根买入，用于验证「最小持仓未到期时不得强行结算」。"""
    id = 'T2'
    name = 'test-late-buy'
    tradable = True
    uses_tp_sl = False
    exit_on_signal = False
    defaults: dict = {}
    param_schema: list = []

    def target(self, bars, p):
        n = len(bars)
        return [1 if i >= n - 3 else 0 for i in range(n)]


# ---------------- A04 零值排序 ----------------
def test_objective_keeps_legal_zero():
    assert _objective({'total_pct': 0}, 'total') == 0.0
    assert _objective({'expectancy_pct': 0}, 'expectancy') == 0.0
    assert _objective({'total_pct': None}, 'total') == float('-inf')
    assert _objective({'sharpe': float('nan')}, 'sharpe') == float('-inf')
    rows = [{'total_pct': -1}, {'total_pct': 0}, {'total_pct': 1}]
    ordered = [r['total_pct'] for r in sorted(rows, key=lambda r: -_objective(r, 'total'))]
    assert ordered == [1, 0, -1], ordered


# ---------------- A03 搜索空间 ----------------
@pytest.mark.parametrize('spec', [
    [1, 10, 0],            # 零步长（旧实现会死循环）
    [1, 10, -1],           # 负步长
    [10, 1, 1],            # 下界大于上界
    [1, float('inf'), 1],  # 非有限
    [1, float('nan'), 2],  # 非有限
    [],                    # 空
])
def test_space_rejects_invalid(spec):
    with pytest.raises(SpaceError):
        expand(spec)


def test_space_rejects_unknown_key_and_huge_combo():
    with pytest.raises(SpaceError):
        parse_space({'nope': [1, 3]}, allowed={'tp', 'sl'})
    with pytest.raises(SpaceError):
        parse_space({'a': [1, 500], 'b': [1, 500]})   # 250000 组合 > 上限


def test_space_accepts_default_grids():
    for mid, model in MODELS.items():
        if not model.grid:
            continue
        parsed = parse_space(model.grid, allowed=_model_space_keys(model))
        assert count_combos(parsed) > 0
    parsed = parse_space(MODELS['M4'].grid, allowed=_model_space_keys(MODELS['M4']))
    assert count_combos(parsed) == 4 * 20 * 15 * 15


def test_space_expand_semantics_unchanged():
    assert expand([1, 5]) == [1, 2, 3, 4, 5]
    assert expand([2, 8, 3]) == [2, 5, 8]
    assert expand(['ma_cross', 'breakout']) == ['ma_cross', 'breakout']


def test_optimize_rejects_bad_test_days():
    bars = make_bars(80)
    for td in (0, 4, -3, 70):
        with pytest.raises(SpaceError):
            optimize(bars, 'M4', {'tp': [5, 10, 5]}, Cost(), test_days=td)


# ---------------- A01 执行配置 ----------------
def test_config_validation_and_fingerprint():
    a, b = ExecutionConfig(), ExecutionConfig()
    assert a.fingerprint() == b.fingerprint()
    assert ExecutionConfig(commission=0.001).fingerprint() != a.fingerprint()
    assert a.to_dict()['capital'] == 100000.0
    for bad in ({'commission': -1}, {'capital': 0}, {'slippage_pct': 150}, {'commission': float('nan')}):
        with pytest.raises(ValueError):
            ExecutionConfig(**bad)


def test_optimize_uses_given_cost_and_capital():
    """寻优的 honest 口径必须与「同一配置直接回测（同预热口径）」完全一致（不得有隐藏默认值）。"""
    bars = make_bars(120)
    cfg = ExecutionConfig(capital=50000, commission=0.001, min_commission=1, slippage_pct=0.2)
    space = {'tp': [6, 9, 3], 'sl': [3, 6, 3]}
    o = optimize(bars, 'M4', space, cfg.cost(), cfg.capital, test_days=30, min_trades=1)
    assert o['best'] is not None
    cut = max(3 + 5, len(bars) - 30)
    warm = o['warmup_bars']
    assert 0 < warm <= cut and o['scorable_from'] == bars[cut].date
    # A10：测试段执行带训练尾部预热，直接复现也必须走同一口径
    direct = run(bars[cut - warm:], MODELS['M4'], o['best']['params'],
                 cfg.cost(), cfg.capital, trade_from=warm)
    assert o['honest']['total_pct'] == direct.metrics['total_pct']
    assert o['honest']['n_trades'] == direct.metrics['n_trades']
    # 预热段不产生权益/持仓，权益曲线从可评分首日开始
    assert all(x is None for x in direct.equity[:warm])


# ---------------- A02 期末持仓语义 ----------------
def test_final_portfolio_balances():
    bars = make_bars(90)
    for mid, params in (('M4', {'tp': 8, 'sl': 5, 'min_hold': 3, 'max_hold': 10}), ('M1', {})):
        r = run(bars, MODELS[mid], params, Cost())
        f = r.final
        assert f['equity_value'] == pytest.approx(f['cash'] + f['shares'] * bars[-1].adj_close, abs=0.05)
        assert f['holding'] == (r.position[-1] == 1)
        # 已清算 → 末位持仓 0 且最后一笔是 eod；仍持仓 → 末位持仓 1 且无 eod；
        # 盘中已平（非 eod）→ 末位持仓 0 且无 eod。三者互斥且必须自洽。
        if f['liquidated']:
            assert r.position[-1] == 0 and r.trades[-1].exit_reason == 'eod'
        elif f['holding']:
            assert r.position[-1] == 1
            assert all(t.exit_reason != 'eod' for t in r.trades)
        else:
            assert r.position[-1] == 0
            assert all(t.exit_reason != 'eod' for t in r.trades)


def test_eod_respects_t_plus_1():
    bars = make_bars(30)
    r = run(bars, LastBarBuy(), {}, Cost())
    assert r.final['blocked_by'] == 't_plus_1'
    assert r.final['holding'] is True
    assert r.position[-1] == 1
    assert all(t.exit_reason != 'eod' for t in r.trades)


def test_eod_respects_min_hold():
    bars = make_bars(30)
    # 倒数第三根买入（末日时持有 2 根），min_hold=5 → 不得强行结算
    r = run(bars, LateBuy(), {'min_hold': 5}, Cost())
    assert r.final['blocked_by'] == 'min_hold'
    assert r.final['holding'] is True and r.position[-1] == 1
    assert all(t.exit_reason != 'eod' for t in r.trades)
    # min_hold 放宽到 1（末端持有 1 根）→ 允许按收盘价结算
    r2 = run(bars, LateBuy(), {'min_hold': 1}, Cost())
    assert r2.final['liquidated'] is True and r2.position[-1] == 0


def test_eod_blocked_by_limit_down_and_mark_to_market():
    bars = make_bars(60)
    bars[-1].can_sell = False
    r = run(bars, MODELS['M1'], {}, Cost())
    if r.final['holding']:
        assert r.final['blocked_by'] in ('limit_down', 'suspended', 't_plus_1')
        assert no_eod(r)

    bars2 = make_bars(60)
    r2 = run(bars2, MODELS['M1'], {}, Cost(), liquidation_policy='mark_to_market')
    assert r2.final['policy'] == 'mark_to_market'
    assert r2.final['liquidated'] is False
    assert r2.final['holding'] is True
    assert no_eod(r2)
    assert r2.final['unrealized_pct'] is not None


def no_eod(r):
    return all(t.exit_reason != 'eod' for t in r.trades)


def test_invalid_policy_rejected():
    with pytest.raises(ValueError):
        run(make_bars(10), MODELS['M1'], {}, Cost(), liquidation_policy='whatever')


def test_markers_use_fill_price():
    bars = make_bars(90)
    cost = Cost(slippage_pct=0.3)
    r = run(bars, MODELS['M4'], {'tp': 6, 'sl': 4, 'min_hold': 2, 'max_hold': 8}, cost)
    assert r.trades, '样本应产生交易'
    for t, m in zip(r.trades, r.markers['buy']):
        assert m[0] == t.buy_date and m[1] == t.buy_price
    for t, m in zip(r.trades, r.markers['sell']):
        assert m[0] == t.sell_date and m[1] == t.sell_price


# ---------------- 审计器鲁棒性 ----------------
def test_audit_short_sample_safe():
    from engine.audit import audit
    for n in (5, 20, 61, 62):
        r = audit(make_bars(n), MODELS['M4'], {'tp': 8, 'sl': 5, 'min_hold': 2}, Cost())
        assert r['passed'] is True, (n, r)
        assert 'cuts' in r
    # 合法长样本：至少覆盖首尾截断点
    r = audit(make_bars(150), MODELS['M4'], {'tp': 8, 'sl': 5, 'min_hold': 2}, Cost(), cuts=10)
    assert r['passed'] is True and r['cuts'] >= 2


# ---------------- A06 导入校验 ----------------
HEAD = ('序号,交易日期,股票代码,股票名称,交易所,交易状态,开盘价,最高价,最低价,收盘价,'
        '前收盘价,涨跌额,涨跌幅(%),成交量(手),成交量(股),成交额(元),换手率(%),复权因子,ST状态,停牌标记,退市标记')


def _row(date='2024-01-02', code='600000', o=10, h=10.5, lo=9.8, c=10.2, vol=1000):
    return f'1,{date},{code},测试,SSE,正常交易,{o},{h},{lo},{c},{o},0,0,{vol//100},{vol},{vol*10},1,1,否,否,否'


def _write(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text(HEAD + '\n' + '\n'.join(lines) + '\n', encoding='utf-8-sig')
    return str(p)


@pytest.fixture()
def conn(tmp_path):
    return repository.init_db(str(tmp_path / 'test.db'))


def _empty(conn):
    return (conn.execute('select count(*) from dataset').fetchone()[0] == 0
            and conn.execute('select count(*) from bar').fetchone()[0] == 0)


@pytest.mark.parametrize('lines,frag', [
    ([_row(date='2024-13-01')], '非法日期'),
    ([_row(h=9.0, lo=10.0)], '最高价'),
    ([_row(o=0)], '必须为正数'),
    ([_row(), _row()], '重复'),
    ([_row(code='6000')], '6 位数字'),
])
def test_import_rejects_bad_rows(conn, tmp_path, lines, frag):
    path = _write(tmp_path, 'bad.csv', lines)
    with pytest.raises(importer.ImportValidationError) as ei:
        repository.ingest_csv(conn, path, 'bad')
    assert frag in str(ei.value)
    assert _empty(conn), '校验失败不得写入任何记录'


def test_import_missing_column(conn, tmp_path):
    p = tmp_path / 'c.csv'
    p.write_text('交易日期,股票代码,开盘价,最高价,最低价\n2024-01-02,600000,10,11,9\n', encoding='utf-8-sig')
    with pytest.raises(importer.ImportValidationError) as ei:
        repository.ingest_csv(conn, str(p), 'x')
    assert '缺少必需列' in str(ei.value)


def test_import_rolls_back_on_write_failure(conn, tmp_path):
    """写入阶段失败（触发器模拟）时，dataset 与 bar 必须一起回滚。"""
    conn.execute("CREATE TRIGGER boom BEFORE INSERT ON bar WHEN NEW.code='600000' "
                 "BEGIN SELECT RAISE(ABORT,'boom'); END")
    conn.commit()
    path = _write(tmp_path, 'two.csv', [_row(code='600000'), _row(code='600001')])
    with pytest.raises(sqlite3.Error):
        repository.ingest_csv(conn, path, 'x')
    assert _empty(conn), '失败后不得残留 dataset 记录'


def test_import_ok_and_dataset_range_covers_all_codes(conn, tmp_path):
    path = _write(tmp_path, 'ok.csv', [
        _row(date='2024-01-02', code='600000'),
        _row(date='2024-01-05', code='600000'),
        _row(date='2023-12-28', code='600001'),   # 另一只更早
        _row(date='2024-01-08', code='600001'),
    ])
    ds_id = repository.ingest_csv(conn, path, 'ok')
    ds = repository.get_dataset(conn, ds_id)
    assert ds['bar_count'] == 4
    assert ds['start_date'] == '2023-12-28'      # 取全部股票的最早
    assert ds['end_date'] == '2024-01-08'
    assert len(repository.get_bars(conn, ds_id, '600000')) == 2


# ---------------- 在线拉取合并入库 ----------------
def test_upsert_merges_extends_and_updates(conn, tmp_path):
    """合并三件事一起验：① 既有代码追加新日期 ② 重叠日期覆盖更新（不产生重复行）
    ③ 新代码加入同一数据集；且未覆盖的旧行情保留。"""
    ds_id = repository.ingest_csv(conn, _write(tmp_path, 'base.csv', [
        _row(date='2024-01-02', code='600000'),
        _row(date='2024-01-03', code='600000'),
        _row(date='2024-01-03', code='600001'),
    ]), 'base')
    old = repository.get_dataset(conn, ds_id)
    assert old['data_hash']

    stats = repository.upsert_csv(conn, _write(tmp_path, 'more.csv', [
        _row(date='2024-01-03', code='600000', o=99, h=100.5, lo=98.5, c=99.9),   # 重叠：覆盖更新
        _row(date='2024-01-08', code='600000'),           # 追加新日期
        _row(date='2024-01-04', code='601091'),           # 新代码进同一数据集
    ]), ds_id)

    bars = repository.get_bars(conn, ds_id, '600000')
    assert [(b.date, b.close) for b in bars] == \
        [('2024-01-02', 10.2), ('2024-01-03', 99.9), ('2024-01-08', 10.2)]   # 旧值保留 + 覆盖生效
    assert len(repository.get_bars(conn, ds_id, '600001')) == 1             # 未覆盖的旧行情不丢
    assert len(repository.get_bars(conn, ds_id, '601091')) == 1
    assert stats['merged_rows'] == 3 and stats['total_rows'] == 5
    assert stats['new_codes'] == ['601091']

    ds = repository.get_dataset(conn, ds_id)
    assert ds['bar_count'] == 5
    assert ds['start_date'] == '2024-01-02' and ds['end_date'] == '2024-01-08'
    assert json.loads(ds['codes']) == ['600000', '600001', '601091']
    assert ds['data_hash'] != old['data_hash']      # 数据变了，内容哈希必须变
    # 哈希口径：同样内容再合并一次（幂等），哈希不变
    repository.upsert_csv(conn, _write(tmp_path, 'same.csv', [
        _row(date='2024-01-03', code='600000', o=99, h=100.5, lo=98.5, c=99.9),
        _row(date='2024-01-08', code='600000'),
        _row(date='2024-01-04', code='601091'),
    ]), ds_id)
    assert repository.get_dataset(conn, ds_id)['data_hash'] == ds['data_hash']


def test_upsert_missing_dataset(conn, tmp_path):
    with pytest.raises(ValueError, match='不存在'):
        repository.upsert_csv(conn, _write(tmp_path, 'x.csv', [_row()]), 999)


def test_code_ranges(conn, tmp_path):
    ds_id = repository.ingest_csv(conn, _write(tmp_path, 'r.csv', [
        _row(date='2024-01-02', code='600000'),
        _row(date='2024-01-05', code='600000'),
        _row(date='2024-01-03', code='600001'),
    ]), 'r')
    assert repository.code_ranges(conn, ds_id) == [
        {'code': '600000', 'first': '2024-01-02', 'last': '2024-01-05', 'rows': 2},
        {'code': '600001', 'first': '2024-01-03', 'last': '2024-01-03', 'rows': 1},
    ]


def test_import_sample_file(conn):
    ds_id = repository.ingest_csv(conn, SAMPLE_CSV, 'sample')
    ds = repository.get_dataset(conn, ds_id)
    assert ds['bar_count'] > 100
    assert len(repository.get_bars(conn, ds_id)) == ds['bar_count']


# ---------------- A12 / A13 API 契约 ----------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    import core.state as state
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'api.db')
    state.reset()                     # 忘掉「已建库」标记，让 lifespan 为新库建表
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        # 准备一份样例数据集，供 /runs 与 /optimize 契约测试使用
        assert c.post('/api/datasets/seed',
                      json={'path': str(SAMPLE_CSV), 'name': 'seed'}).status_code == 200
        yield c
    state.reset()


def test_api_health_reports_db(client):
    r = client.get('/api/health')
    assert r.status_code == 200 and r.json()['data']['db'] == 'ok'


def test_api_seed_accepts_json_body(client):
    r = client.post('/api/datasets/seed', json={'path': str(SAMPLE_CSV), 'name': '契约测试'})
    assert r.status_code == 200, r.text
    assert r.json()['data']['dataset_id'] >= 1
    ds = client.get('/api/datasets').json()['data']
    assert ds and ds[0]['bar_count'] > 100


def test_api_seed_rejects_outside_data_dir(client, tmp_path):
    outside = tmp_path / 'x.csv'
    outside.write_text(HEAD + '\n' + _row() + '\n', encoding='utf-8-sig')
    r = client.post('/api/datasets/seed', json={'path': str(outside)})
    assert r.status_code == 403


def test_api_seed_rejects_bad_csv(client):
    r = client.post('/api/datasets/seed', json={'path': str(SAMPLE_CSV), 'name': 'x'})
    assert r.status_code == 200
    r = client.post('/api/datasets/seed', json={'path': '/nope/none.csv'})
    assert r.status_code in (400, 403)


def test_api_delete_dataset_clears_bars_and_refuses_when_referenced(client):
    """删除数据集：被计划引用时 409，删掉引用后连 bar 一起清掉（验收脚本靠它自清理）。"""
    ds_id = client.post('/api/datasets/seed',
                        json={'path': str(SAMPLE_CSV), 'name': '待删'}).json()['data']['dataset_id']
    plan = client.post('/api/plans', json={'dataset_id': ds_id, 'code': '600186',
                                           'model_id': 'M4', 'params': {}})
    assert plan.status_code == 200, plan.text
    rid = plan.json()['data']['record_id']

    bad = client.delete(f'/api/datasets/{ds_id}')
    assert bad.status_code == 409, bad.text
    assert bad.json()['code'] == 'DATASET_IN_USE'

    assert client.delete(f'/api/plans/{rid}').status_code == 200
    ok = client.delete(f'/api/datasets/{ds_id}')
    assert ok.status_code == 200, ok.text
    assert ok.json()['data']['bars'] > 0
    assert [d for d in client.get('/api/datasets').json()['data'] if d['id'] == ds_id] == []
    assert client.get(f'/api/datasets/{ds_id}/bars').status_code == 404
    assert client.delete(f'/api/datasets/{ds_id}').status_code == 404


def test_api_optimize_rejects_bad_space(client):
    ds = client.get('/api/datasets').json()['data']
    body = {'dataset_id': ds[0]['id'], 'code': '600186', 'model_id': 'M4',
            'space': {'tp': [1, 10, 0]}, 'test_days': 61}
    r = client.post('/api/optimize', json=body)
    assert r.status_code == 422, r.text
    assert '步长' in str(r.json())


def test_api_run_returns_config_and_final(client):
    ds = client.get('/api/datasets').json()['data']
    body = {'dataset_id': ds[0]['id'], 'codes': ['600186'],
            'models': [{'id': 'M4', 'params': {'tp': 9, 'sl': 12, 'min_hold': 3, 'max_hold': 12}}],
            'global_params': {'capital': 200000, 'commission': 0.0003, 'min_commission': 5,
                              'stamp_duty': 0.0005, 'transfer_fee': 0.00001, 'slippage_pct': 0.1}}
    r = client.post('/api/runs', json=body)
    assert r.status_code == 200, r.text
    d = r.json()['data']
    assert d['config']['capital'] == 200000
    assert d['config_hash'] and len(d['config_hash']) == 16
    assert d['params_hash'] and len(d['params_hash']) == 16
    res = d['results'][0]
    assert 'final' in res and res['final']['policy'] == 'close_at_end'
    # 交易明细必须带「买入命中说明」：哪条入场条件、哪天收盘确认
    trades = res['trades']
    assert trades, '默认样例应产生交易'
    assert trades[0]['entry_reason'], trades[0]
    assert '收盘信号' in trades[0]['entry_reason'], trades[0]['entry_reason']
    assert trades[0]['exit_reason'] in ('tp', 'sl', 'timeout', 'signal', 'eod')
    # 同输入同指纹（可重放）
    r2 = client.post('/api/runs', json=body)
    assert r2.json()['data']['params_hash'] == d['params_hash']


# ---------------- A08 后台任务（HTTP 契约） ----------------
def _wait_job(client, job_id, timeout=20.0):
    import time
    t0 = time.time()
    rec = None
    while time.time() - t0 < timeout:
        r = client.get(f'/api/jobs/{job_id}')
        assert r.status_code == 200, r.text
        rec = r.json()['data']
        if rec['status'] in ('done', 'error', 'cancelled'):
            return rec
        time.sleep(0.05)
    raise AssertionError(f'任务 {job_id} 未在 {timeout}s 内结束：{rec}')


def test_api_job_optimize_lifecycle(client):
    ds = client.get('/api/datasets').json()['data'][0]
    body = {'dataset_id': ds['id'], 'code': '600186', 'model_id': 'M4',
            'space': {'tp': [6, 8], 'sl': [5, 7]}, 'test_days': 61}
    r = client.post('/api/jobs/optimize', json=body)
    assert r.status_code == 202, r.text
    rec = r.json()['data']
    assert rec['status'] in ('queued', 'running')

    rec = _wait_job(client, rec['id'])
    assert rec['status'] == 'done', rec
    assert rec['progress'] == 1.0
    assert rec['result']['verdict'] in ('red', 'yellow', 'green')
    assert rec['result']['n_combos'] == 9        # [6,8]=[6,7,8] × [5,7]=[5,6,7]

    lst = client.get('/api/jobs').json()['data']
    assert any(j['id'] == rec['id'] for j in lst['jobs'])
    assert lst['stats']['inflight'] >= 0


def test_api_job_unknown_returns_404(client):
    assert client.get('/api/jobs/nope').status_code == 404
    assert client.post('/api/jobs/nope/cancel').status_code == 404


def test_api_job_rejects_bad_space_before_queuing(client):
    ds = client.get('/api/datasets').json()['data'][0]
    body = {'dataset_id': ds['id'], 'code': '600186', 'model_id': 'M4',
            'space': {'tp': [1, 10, 0]}}
    job_id = client.post('/api/jobs/optimize', json=body).json()['data']['id']
    rec = _wait_job(client, job_id)
    assert rec['status'] == 'error'
    assert '步长' in (rec['error'] or '')


# ---------------- A11 实验记录 ----------------
def _exp_list(client, **params):
    r = client.get('/api/experiments', params=params)
    assert r.status_code == 200, r.text
    return r.json()['data']


def test_experiment_spec_hash_is_content_addressed():
    from db import experiments
    a = experiments.make_spec(kind='optimize', dataset_id=1, dataset_hash='h1',
                              code='600186', model_id='M4', body={'test_days': 61})
    b = experiments.make_spec(kind='optimize', dataset_id=99, dataset_hash='h1',
                              code='600186', model_id='M4', body={'test_days': 61})
    assert a['spec_hash'] == b['spec_hash'], '同内容不同 dataset_id 应同指纹'
    c = experiments.make_spec(kind='optimize', dataset_id=1, dataset_hash='h2',
                              code='600186', model_id='M4', body={'test_days': 61})
    assert c['spec_hash'] != a['spec_hash'], '数据变了指纹必须变'
    d = experiments.make_spec(kind='optimize', dataset_id=1, dataset_hash='h1',
                              code='600186', model_id='M4', body={'test_days': 30})
    assert d['spec_hash'] != a['spec_hash'], '参数变了指纹必须变'


def test_api_run_records_experiment(client):
    ds = client.get('/api/datasets').json()['data'][0]
    body = {'dataset_id': ds['id'], 'codes': ['600186'],
            'models': [{'id': 'M4', 'params': {'tp': 9, 'sl': 12, 'min_hold': 3}}]}
    d = client.post('/api/runs', json=body).json()['data']
    assert d['experiment_id'], '回测必须落实验记录'

    lst = _exp_list(client, kind='backtest')
    assert lst['stats']['by_kind'].get('backtest', 0) >= 1
    rec = next(x for x in lst['items'] if x['id'] == d['experiment_id'])
    assert rec['code'] == '600186' and rec['status'] == 'ok'
    assert len(rec['spec_hash']) == 16
    assert rec['engine_version'] and rec['outcome']['rows'], '摘要里要有每次回测的指标行'

    detail = client.get(f"/api/experiments/{d['experiment_id']}").json()['data']
    assert detail['spec']['body']['models'][0]['id'] == 'M4'

    # 同一输入再跑一次：指纹一致，但各自留一条记录（可比耗时）
    d2 = client.post('/api/runs', json=body).json()['data']
    l2 = _exp_list(client, kind='backtest')
    recs = [x for x in l2['items'] if x['spec_hash'] == rec['spec_hash']]
    assert len(recs) >= 2
    assert d2['experiment_id'] != d['experiment_id']


def test_api_optimize_records_experiment_and_replay(client):
    ds = client.get('/api/datasets').json()['data'][0]
    body = {'dataset_id': ds['id'], 'code': '600186', 'model_id': 'M4',
            'space': {'tp': [8], 'sl': [5]}, 'test_days': 61}
    d = client.post('/api/optimize', json=body).json()['data']
    assert d['experiment_id']

    detail = client.get(f"/api/experiments/{d['experiment_id']}").json()['data']
    assert detail['spec']['body']['space'] == {'tp': [8], 'sl': [5]}
    assert detail['outcome']['best_params']['tp'] == 8

    rep = client.get(f"/api/experiments/{d['experiment_id']}/replay").json()['data']['body']
    assert rep['dataset_id'] == ds['id'] and rep['code'] == '600186'
    assert rep['space'] == {'tp': [8], 'sl': [5]}
    # 重放请求体可直接提交（不再手工拼参数）
    d3 = client.post('/api/optimize', json=rep).json()['data']
    assert d3['experiment_id'] and d3['experiment_id'] != d['experiment_id']


def test_api_experiment_delete_and_404(client):
    ds = client.get('/api/datasets').json()['data'][0]
    d = client.post('/api/runs', json={
        'dataset_id': ds['id'], 'codes': ['600186'],
        'models': [{'id': 'M4', 'params': {}}]}).json()['data']
    assert client.delete(f"/api/experiments/{d['experiment_id']}").status_code == 200
    assert client.get(f"/api/experiments/{d['experiment_id']}").status_code == 404
    assert client.delete(f"/api/experiments/{d['experiment_id']}").status_code == 404
    assert client.get('/api/experiments/999999').status_code == 404
    assert client.get('/api/experiments', params={'kind': 'nope'}).status_code == 404


# ---------------- A10 切分预热 ----------------
def test_run_trade_from_blocks_early_trades():
    """trade_from：预热段不交易、权益为 None，全部交易从正式段开始。"""
    from engine.optimize import warmup_bars
    bars = make_bars(80)
    p = {'tp': 8, 'sl': 5, 'min_hold': 3}
    r0 = run(bars, MODELS['M4'], p, Cost(), 100000.0)
    k = 30
    r = run(bars, MODELS['M4'], p, Cost(), 100000.0, trade_from=k)
    assert all(t.buy_i >= k for t in r.trades), '预热段不得产生交易'
    assert all(x is None for x in r.equity[:k])
    assert r.equity[k] is not None and r.position[:k] == [0] * k
    assert r.metrics['n_trades'] <= r0.metrics['n_trades']
    with pytest.raises(ValueError):
        run(bars, MODELS['M4'], p, Cost(), 100000.0, trade_from=80)
    with pytest.raises(ValueError):
        run(bars, MODELS['M4'], p, Cost(), 100000.0, trade_from=-1)
    # 预热窗：至少 60，空间里有更长窗口取更大，且不超过可用历史
    assert warmup_bars({}, {}, 300) == 60
    assert warmup_bars({'ma_n': [5, 60]}, {'lookback': 90}, 300) == 90
    assert warmup_bars({}, {}, 40) == 40


def test_walk_forward_warmup_scores_from_test_start():
    """A10 核心回归：61 天测试段 + 预热后，指标/信号在测试首日即可评分，
    且每折结果可按报告里的预热参数精确复现（切分协议显式化）。"""
    from engine.optimize import walk_forward
    bars = make_bars(300)
    space = {'tp': [8], 'sl': [5]}
    wf = walk_forward(bars, 'M4', space, Cost(), 100000.0,
                      test_days=61, folds=2, min_trades=1)
    assert wf['folds'] == 2 and not wf.get('error')
    for rec in wf['rows']:
        assert rec['warmup_bars'] >= 60, '每折必须声明预热根数'
        assert rec['warmup_range'] and rec['scorable_from'] == rec['test_range'][0]
        assert rec['n_trades'] and rec['n_trades'] > 0
        # 协议复现：seg = 预热段 + 测试段，trade_from = warmup → 与折内结果完全一致
        test_start = bars.index(next(b for b in bars if b.date == rec['test_range'][0]))
        warm = rec['warmup_bars']
        seg = bars[test_start - warm:bars.index(next(
            b for b in bars if b.date == rec['test_range'][-1])) + 1]
        r = run(seg, MODELS['M4'], rec['params'], Cost(), 100000.0, trade_from=warm)
        assert r.metrics['total_pct'] == rec['total_pct']
        assert r.metrics['n_trades'] == rec['n_trades']
        # 预热段无交易、无权益：评分确实只从测试段首日开始
        assert all(x is None for x in r.equity[:warm])
        assert all(t.buy_i >= warm for t in r.trades)


# ---------------- A09 单一转换器 ----------------
def _bars_via_db(csv_path, code, conn):
    ds_id = repository.ingest_csv(conn, csv_path, 'a09')
    return repository.get_bars(conn, ds_id, code)


def _bars_via_cli(csv_path, code):
    from db.importer import parse_csv
    from engine.bars import build_bars
    return build_bars(parse_csv(csv_path)['groups'][code])


def test_csv_to_bar_single_converter(tmp_path, conn):
    """A09 验收：同一 CSV 经「落库再读」与「CLI 直接转换」得到完全相同的 Bar。"""
    import dataclasses
    path = _write(tmp_path, 'a09.csv', [
        _row(code='600000'), _row(code='000001'),
    ])
    a = _bars_via_db(path, '600000', conn)
    b = _bars_via_cli(path, '600000')
    assert len(a) == len(b) == 1
    for x, y in zip(a, b):
        for f in Bar.__dataclass_fields__:
            assert getattr(x, f) == getattr(y, f), f'{f}: {getattr(x, f)} != {getattr(y, f)}'

    # 多股 CSV：每只股票各自以自己的最新因子为前复权基准（旧 load_bars 在这里拿错基准）
    from db.importer import parse_csv
    from engine.bars import build_bars
    parsed = parse_csv(path)
    assert set(parsed['groups']) == {'600000', '000001'}
    for code, rows in parsed['groups'].items():
        assert len(build_bars(rows)) == len(rows)


def test_build_bars_rejects_mixed_codes_by_contract():
    """build_bars 只接受同一股票的行；多股混入时前复权基准取自末行——文档化这个契约。"""
    from db.importer import parse_csv, Row
    from engine.bars import build_bars
    import os
    path = os.path.join(HERE, '..', 'data', 'sample_all_300d.csv')
    if not os.path.exists(path):
        import pytest
        pytest.skip('无多股样例')
    parsed = parse_csv(path)
    rows = parsed['groups']['600186']
    good = build_bars(rows)
    # 按各自末行因子做基准：最新一日 adj_close == close
    assert abs(good[-1].adj_close - rows[-1].close) < 1e-6


# ---------------- R1 买入预算硬约束 ----------------
def test_buy_never_overdraws_cash_under_extreme_costs():
    """R1：手数按 amount+buy_fee(amount)<=cash 取，极高佣金/最低佣金都不得出现负现金。"""
    from engine.backtest import _affordable_qty

    bars = make_bars(120)
    p = {'tp': 8, 'sl': 5, 'min_hold': 1}
    for kw in (dict(capital=100000, commission=0.05, min_commission=5),
               dict(capital=1000, commission=0.00025, min_commission=500),
               dict(capital=5000, commission=0.1, min_commission=100),
               dict(capital=100000, commission=0.00025, min_commission=5)):
        cfg = ExecutionConfig(**kw)
        r = run(bars, MODELS['M4'], p, cfg.cost(), cfg.capital)
        assert r.final['cash'] >= -1e-9, (kw, r.final['cash'])
        assert r.final['equity_value'] >= 0, (kw, r.final['equity_value'])

    # 单元级：5% 佣金下 10 万买 10 元股票，旧算法 9800 股 → 现金 -2900，新算法 9500 股
    c = ExecutionConfig(commission=0.05, min_commission=5).cost()
    assert _affordable_qty(100000, 10, c) == 9500
    assert 9500 * 10 + c.buy_fee(9500 * 10) <= 100000
    assert 9600 * 10 + c.buy_fee(9600 * 10) > 100000
    # 最低佣金 500 + 现金 1000 + 1 元股票：只能买 4 手
    assert _affordable_qty(1000, 1, ExecutionConfig(min_commission=500).cost()) == 400
    # 一手都付不起 / 不够手续费 / 非法入参 → 0 股
    assert _affordable_qty(1000, 10, c) == 0
    assert _affordable_qty(4, 10, c) == 0
    assert _affordable_qty(0, 10, c) == 0
    assert _affordable_qty(10, 0, c) == 0


def test_buy_exactly_one_lot_and_too_little_cash():
    """现金刚好够一手必须成交；差一分钱则不成交（不得因 2% 缓冲放弃）。"""
    bars = make_bars(4)
    px = bars[3].adj_open                             # LastBarBuy 在第 4 根开盘买入
    cost = Cost()

    one_lot = px * 100 + cost.buy_fee(px * 100)
    r = run(bars, LastBarBuy(), {}, cost, one_lot)
    # 末日买入受 T+1 限制不平仓，持仓以 final 为准
    assert r.final['shares'] == 100 and abs(r.final['cash']) < 1e-6
    assert r.final['entry_price'] == round(px, 4)

    r2 = run(bars, LastBarBuy(), {}, cost, one_lot - 1.0)
    assert r2.final['shares'] == 0 and r2.metrics['n_trades'] == 0
    assert abs(r2.final['cash'] - round(one_lot - 1.0, 2)) < 1e-9   # final 保留 2 位

    # 现金不够最低佣金：不成交，现金不变
    r3 = run(bars, LastBarBuy(), {}, ExecutionConfig(min_commission=500).cost(), 400)
    assert r3.metrics['n_trades'] == 0 and r3.final['cash'] == 400


# ---------------- R2 M1 计划不得虚报持仓 ----------------
def _bars_no_buy(n=60, base=10.0):
    bars = make_bars(n, base=base)
    for b in bars:                    # 每日一字涨停：开盘即涨停，全天不可买
        b.can_buy = False
    return bars


def test_plan_m1_holding_follows_ledger():
    """R2：M1 的 holding 必须来自回放账本，不能用 last>=1 覆盖。"""
    from engine.plan import next_plan

    # 两根行情：T+1 尚未成交
    p2 = next_plan(make_bars(2), MODELS['M1'], {}, Cost(), 100000.0)
    assert p2['holding'] is False and p2['position']['shares'] == 0
    assert 'T+1' in p2['entry_condition']
    assert p2['threshold_price'] is None

    # 三根行情：第 3 根开盘建仓
    p3 = next_plan(make_bars(3), MODELS['M1'], {}, Cost(), 100000.0)
    assert p3['holding'] is True and p3['position']['shares'] > 0

    # 资金不足一手
    p4 = next_plan(make_bars(30), MODELS['M1'], {}, Cost(), 50.0)
    assert p4['holding'] is False and p4['position']['shares'] == 0 and p4['position']['cash'] == 50.0
    assert p4.get('note')

    # 全程不可买（涨停/停牌）
    p5 = next_plan(_bars_no_buy(30), MODELS['M1'], {}, Cost(), 100000.0)
    assert p5['holding'] is False and p5['position']['shares'] == 0


# ---------------- R3 切分预热协议 ----------------
def test_test_segment_indicators_have_history_before_split():
    """R3：测试段首日的 MA20 及其信号必须已有值（预热来自边界之前的数据）。"""
    from engine.optimize import warmup_bars

    bars = make_bars(200)
    cut = len(bars) - 10                    # 测试段只有 10 根
    warm = warmup_bars({'ma_n': [20]}, None, cut)
    assert warm >= 20

    from engine.data import ma

    p = {**MODELS['M3'].defaults, 'ma_n': 20, 'min_hold': 3}
    seg = bars[cut - warm:]
    warm_ma = ma([b.adj_close for b in seg], 20)
    assert warm_ma[warm] is not None, '测试段首日的 MA20 必须由边界之前的历史算出'
    cold_ma = ma([b.adj_close for b in bars[cut:]], 20)
    assert all(x is None for x in cold_ma[:19]), '对照：不预热时测试段前 19 天没有 MA20'
    assert MODELS['M3'].target(seg, p)[warm] is not None


def test_optimize_reports_warmup_shortfall():
    """训练段不够长时，预热根数被截断必须显式报告，不能静默降级。"""
    bars = make_bars(70)
    o = optimize(bars, 'M4', {'tp': [8], 'sl': [5]}, Cost(), 100000.0,
                 test_days=30, min_trades=1)
    assert o['warmup_need'] == 60
    assert o['warmup_bars'] == 40 and o['warmup_short'] is True
    assert o['warmup_range'][1] == o['train_range'][1]
    assert o['scorable_from'] == o['test_range'][0]

    big = optimize(make_bars(300), 'M4', {'tp': [8], 'sl': [5]}, Cost(), 100000.0,
                   test_days=61, min_trades=1)
    assert big['warmup_bars'] == big['warmup_need'] == 60 and big['warmup_short'] is False


# ---------------- R4 搜索空间显式语义 ----------------
def test_space_explicit_values_and_range_forms():
    """R4：显式 values/range 语义确定；三字符串枚举不再被当区间。"""
    entry = ['ma_cross', 'breakout', 'momentum']
    assert expand(entry) == entry                       # 裸数组：全字符串 → 枚举
    assert expand({'values': entry}) == entry
    assert expand({'values': [2, 6, 10]}) == [2, 6, 10]  # 三数值枚举
    assert expand({'values': [2, 6, 6, 10]}) == [2, 6, 10]
    assert expand({'range': [2, 10, 2]}) == [2, 4, 6, 8, 10]
    assert expand({'range': [1, 3]}) == [1, 2, 3]
    # 旧语义保持不变
    assert expand([1, 5]) == [1, 2, 3, 4, 5]
    assert expand([2, 8, 3]) == [2, 5, 8]


def test_space_rejects_ambiguous_or_malformed_specs():
    """旧数组「步长大于区间宽度」只会得到 1 个值 → 报错并提示改显式写法。"""
    with pytest.raises(SpaceError):
        expand([2, 6, 10])
    with pytest.raises(SpaceError):
        expand({'values': []})
    with pytest.raises(SpaceError):
        expand({'values': [1], 'range': [1, 2]})
    with pytest.raises(SpaceError):
        expand({'range': [3]})
    with pytest.raises(SpaceError):
        expand({'nope': [1, 2]})
    with pytest.raises(SpaceError):
        expand({'values': ['a', '']})
    # 三字符串枚举进 parse_space 后组合数正确（旧实现会直接报错）
    parsed = parse_space({'entry': ['ma_cross', 'breakout', 'momentum'],
                          'tp': {'values': [6, 9, 12]}}, allowed={'entry', 'tp'})
    assert count_combos(parsed) == 9
