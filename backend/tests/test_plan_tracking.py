# -*- coding: utf-8 -*-
"""计划跟踪闭环（BUY-12）验收：落库快照不可变 / 成交录入 / 事后复盘 / 跨数据集取后续行情。

关键验收点（对应规格里的「验收」）：
  - 能追溯某计划为何买入或放弃 → `review.not_filled.reason_code` 给可核对的原因；
  - 不能用后来的参数重写原始计划 → 记录里的 plan 快照逐字段不变；
  - 计划与实际的价格/数量/费用偏差可查 → `vs_plan`；
  - 后续行情来自新的数据集版本（数据集是快照，不会自己变长）→ `data_span`。
"""
import json
import os
from datetime import date, timedelta

import pytest

SAMPLE_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'sample_600186.csv')

HEADER = ('序号,交易日期,股票代码,股票名称,交易所,交易状态,开盘价,最高价,最低价,收盘价,前收盘价,'
          '涨跌额,涨跌幅(%),成交量(手),成交量(股),成交额(元),换手率(%),复权因子,ST状态,停牌标记,退市标记')


def _weekdays(start: str, n: int) -> list[str]:
    d = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def write_csv(path, prices, *, opens=None, code='600001', name='测试股', start='2026-01-05'):
    """按真实列结构写一份行情 CSV（复权因子恒为 1，便于核对原始价）。"""
    dates = _weekdays(start, len(prices))
    lines = [HEADER]
    prev = prices[0]
    for i, px in enumerate(prices):
        op = (opens[i] if opens and opens[i] is not None else px)
        hi = max(op, px) * 1.01
        lo = min(op, px) * 0.99
        lines.append(','.join(str(x) for x in (
            i + 1, dates[i], code, name, 'SSE', '正常交易',
            round(op, 2), round(hi, 2), round(lo, 2), round(px, 2), round(prev, 2),
            round(px - prev, 2), round((px / prev - 1) * 100, 2),
            100000, 10000000, round(px * 10000000, 1), 1.5, 1.0, '否', '否', '否')))
        prev = px
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('\n'.join(lines) + '\n')
    return dates


# 前 40 根横盘 10.0（MA20 = 10，收盘 == 均线不满足严格大于），第 41 根跳涨到 11.0 确认信号，
# 第 42 根跳空高开到 12.0（用来验证「开盘超过可接受买价上限 → 未成交」）。
PRICES = [10.0] * 40 + [11.0, 12.2, 12.5, 12.8]
OPENS = [None] * 41 + [12.0, 12.3, 12.6, 12.7]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import core.state as state
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'track.db')
    monkeypatch.setenv('WORKBENCH_DATA_DIR', str(tmp_path))   # seed 接口只允许 data 目录
    state.reset()
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        # 数据集 1：截至「信号确认日」（第 41 根）
        p1 = tmp_path / 'a.csv'
        write_csv(p1, PRICES[:41])          # 数据集 1 只到信号确认日
        assert c.post('/api/datasets/seed', json={'path': str(p1), 'name': 'v1'}).status_code == 200
        ds1 = c.get('/api/datasets').json()['data'][0]
        # 日历用全序列：数据集 1 里还没有「下一交易日」，但测试需要它的日期字符串
        dates = _weekdays('2026-01-05', len(PRICES))
        yield c, ds1, dates, tmp_path
    state.reset()


def save(c, ds, **over):
    body = {'dataset_id': ds['id'], 'code': '600001', 'model_id': 'M4',
            'params': {'entry': 'ma_cross', 'ma_n': 20, 'tp': 9, 'sl': 12,
                       'min_hold': 3, 'max_hold': 12},
            'account': {'mode': 'manual_actual', 'cash': 100000, 'shares': 0,
                        'sellable': 0, 'snapshot_at': '2026-03-06'},
            # 合成数据是 2026 年上半年的，相对「今天」必然落后；研究模式让它保持
            # signal_confirmed（否则会被判 expired，测不到后面的成交与复盘）
            'research': True}
    body.update(over)
    r = c.post('/api/plans', json=body)
    assert r.status_code == 200, r.text
    return r.json()['data']


def add_later_dataset(c, tmp_path):
    """模拟「又抓了一次行情」：新数据集包含更多交易日。"""
    p2 = tmp_path / 'b.csv'
    write_csv(p2, PRICES, opens=OPENS)
    assert c.post('/api/datasets/seed', json={'path': str(p2), 'name': 'v2'}).status_code == 200
    return [x for x in c.get('/api/datasets').json()['data'] if x['name'] == 'v2'][0]


# ---------------- 落库与快照 ----------------
def test_save_creates_record_and_plan_endpoint_stays_pure(env):
    c, ds, dates, _ = env
    d = save(c, ds)
    assert d['reused'] is False
    rec = d['record']
    assert rec['as_of'] == dates[40]
    assert rec['lifecycle'] == 'planned'
    assert rec['plan']['status'] == 'signal_confirmed'
    assert rec['plan']['execution']['earliest_execution_session'] == dates[41]
    assert json.loads(json.dumps(rec['params']))['ma_n'] == 20

    # /api/plan 只算不存：不能因为「看一眼」就在跟踪列表里堆垃圾
    n = len(c.get('/api/plans').json()['data']['items'])
    r = c.post('/api/plan', json={'dataset_id': ds['id'], 'code': '600001', 'model_id': 'M4',
                                  'params': {'entry': 'ma_cross', 'ma_n': 20}})
    assert r.status_code == 200
    assert len(c.get('/api/plans').json()['data']['items']) == n


def test_snapshot_is_never_rewritten_by_later_data(env):
    c, ds, dates, tmp_path = env
    d = save(c, ds)
    rid = d['record_id']
    before = d['record']['plan']['execution']['quantity_estimate']['shares']
    before_ref = d['record']['plan']['execution']['quantity_estimate']['reference_price']

    add_later_dataset(c, tmp_path)          # 行情变长、收盘价变高
    rec = c.get(f'/api/plans/{rid}').json()['data']['record']
    assert rec['plan']['execution']['quantity_estimate']['shares'] == before
    assert rec['plan']['execution']['quantity_estimate']['reference_price'] == before_ref
    assert rec['as_of'] == dates[40]
    # 但复盘用的是最新行情：data_span 必须往后延伸
    rv = c.get(f'/api/plans/{rid}').json()['data']['review']
    assert rv['data_span']['end'] == dates[43]
    assert rv['data_span']['bar_count'] == 44


def test_save_dedupes_only_while_untouched(env):
    c, ds, dates, _ = env
    a = save(c, ds)
    b = save(c, ds)
    assert b['record_id'] == a['record_id'] and b['reused'] is True

    # 一旦录了成交，再保存就是新的一次决策，不能覆盖已对账的记录
    c.post(f"/api/plans/{a['record_id']}/fills",
           json={'side': 'buy', 'qty': 8000, 'price': 11.05, 'fees': 12.0, 'trade_date': dates[41]})
    d = save(c, ds)
    assert d['record_id'] != a['record_id'] and d['reused'] is False


# ---------------- 成交录入 ----------------
def test_fill_rejects_unexplainable_input(env):
    c, ds, dates, _ = env
    rid = save(c, ds)['record_id']
    u = f'/api/plans/{rid}/fills'
    assert c.post(u, json={'side': 'buy', 'qty': 0, 'price': 11, 'trade_date': dates[41]}).status_code == 422
    assert c.post(u, json={'side': 'buy', 'qty': 100, 'price': -1, 'trade_date': dates[41]}).status_code == 422
    # 成交日不能早于/等于计划依据日：那是「未来函数」，必须拒绝
    assert c.post(u, json={'side': 'buy', 'qty': 100, 'price': 11, 'trade_date': dates[40]}).status_code == 422
    # 没买就卖
    r = c.post(u, json={'side': 'sell', 'qty': 100, 'price': 11, 'trade_date': dates[41]})
    assert r.status_code == 422 and '超过已录入持仓' in r.json()['error']


def test_buy_then_sell_closes_and_computes_pnl(env):
    c, ds, dates, _ = env
    rid = save(c, ds)['record_id']
    d = c.post(f'/api/plans/{rid}/fills',
               json={'side': 'buy', 'qty': 8000, 'price': 11.0, 'fees': 12.0,
                     'trade_date': dates[41]}).json()['data']
    assert d['record']['lifecycle'] == 'filled'
    s = d['review']['fills_summary']
    assert s['buy_qty'] == 8000 and s['buy_avg_price'] == 11.0
    assert d['review']['vs_plan']['price_deviation_pct'] == 0.0     # 恰好等于计划参考价

    d = c.post(f'/api/plans/{rid}/fills',
               json={'side': 'sell', 'qty': 8000, 'price': 12.0, 'fees': 28.0,
                     'trade_date': dates[42]}).json()['data']
    assert d['record']['lifecycle'] == 'closed'
    assert d['review']['outcome'] == 'closed'
    s = d['review']['fills_summary']
    # 卖出净额 96000-28 = 95972；买入成本 88000+12 = 88012 → +7960
    assert s['realized_pnl'] == pytest.approx(7960.0, abs=0.01)
    assert s['open_qty'] == 0


def test_partial_exit_stays_filled_with_open_position(env):
    c, ds, dates, _ = env
    rid = save(c, ds)['record_id']
    c.post(f'/api/plans/{rid}/fills',
           json={'side': 'buy', 'qty': 8000, 'price': 11.0, 'fees': 12.0, 'trade_date': dates[41]})
    d = c.post(f'/api/plans/{rid}/fills',
               json={'side': 'sell', 'qty': 3000, 'price': 12.0, 'fees': 10.0,
                     'trade_date': dates[42]}).json()['data']
    assert d['review']['outcome'] == 'partial'
    assert d['review']['fills_summary']['open_qty'] == 5000


def test_delete_fill_rolls_back_lifecycle(env):
    c, ds, dates, _ = env
    rid = save(c, ds)['record_id']
    f = c.post(f'/api/plans/{rid}/fills',
               json={'side': 'buy', 'qty': 8000, 'price': 11.0, 'trade_date': dates[41]}
               ).json()['data']['fill']
    d = c.delete(f"/api/plans/{rid}/fills/{f['id']}").json()['data']
    assert d['record']['lifecycle'] == 'planned'
    assert d['review']['fills_summary']['buy_qty'] == 0
    assert d['review']['fills'] == []


def test_cancel_blocks_fill_and_reopen_restores(env):
    c, ds, dates, _ = env
    rid = save(c, ds)['record_id']
    d = c.post(f'/api/plans/{rid}/cancel', json={'reason': '手工放弃：不想追高'}).json()['data']
    assert d['record']['lifecycle'] == 'cancelled'
    assert d['review']['outcome'] == 'cancelled'
    r = c.post(f'/api/plans/{rid}/fills',
               json={'side': 'buy', 'qty': 100, 'price': 11, 'trade_date': dates[41]})
    assert r.status_code == 422 and '撤销取消' in r.json()['error']

    d = c.post(f'/api/plans/{rid}/reopen').json()['data']
    assert d['record']['lifecycle'] == 'planned'
    assert c.post(f'/api/plans/{rid}/fills',
                  json={'side': 'buy', 'qty': 100, 'price': 11,
                        'trade_date': dates[41]}).status_code == 200


def test_cancel_rejected_after_buy(env):
    c, ds, dates, _ = env
    rid = save(c, ds)['record_id']
    c.post(f'/api/plans/{rid}/fills',
           json={'side': 'buy', 'qty': 100, 'price': 11, 'trade_date': dates[41]})
    r = c.post(f'/api/plans/{rid}/cancel', json={'reason': 'x'})
    assert r.status_code == 422 and '录入卖出' in r.json()['error']


# ---------------- 复盘 ----------------
def test_review_pending_then_not_filled_without_fill(env):
    c, ds, dates, tmp_path = env
    d = save(c, ds)
    rid = d['record_id']
    rv = d['review']
    assert rv['outcome'] == 'pending'                       # 执行日还没有行情
    assert rv['counterfactual']['available'] is False

    add_later_dataset(c, tmp_path)
    rv = c.get(f'/api/plans/{rid}').json()['data']['review']
    assert rv['outcome'] == 'not_filled'
    assert rv['not_filled']['reason_code'] == 'not_recorded'
    assert rv['not_filled']['execution_session'] == dates[41]
    assert rv['not_filled']['session_open'] == 12.0


def test_review_flags_price_above_cap(env):
    c, ds, dates, tmp_path = env
    # 参考价 11.0，上限 11.5 能过；次日跳空到 12.0 就超上限了 → 规则要求放弃，不是"忘了买"
    d = save(c, ds, exec_rules={'max_price': 11.5, 'max_position_pct': 100, 'day_only': True})
    assert d['plan']['status'] == 'signal_confirmed'
    add_later_dataset(c, tmp_path)
    rv = c.get(f"/api/plans/{d['record_id']}").json()['data']['review']
    assert rv['outcome'] == 'not_filled'
    assert rv['not_filled']['reason_code'] == 'price_above_cap'
    assert rv['not_filled']['cap'] == 11.5
    checks = {x['text']: x for x in rv['vs_plan']['checks']}
    assert checks['成交价是否在计划的可接受上限内']['ok'] is False


def test_review_counterfactual_uses_later_sessions(env):
    c, ds, dates, tmp_path = env
    rid = save(c, ds)['record_id']
    add_later_dataset(c, tmp_path)
    rv = c.get(f'/api/plans/{rid}').json()['data']['review']
    cf = rv['counterfactual']
    assert cf['available'] is True
    assert cf['entry_date'] == dates[41]                 # 策略在下一交易日开盘买入
    assert cf['entry_price'] == pytest.approx(12.0, abs=0.02)
    assert cf['status'] in ('open', 'closed')


def test_review_market_valuation_waits_for_data_after_fill(env):
    c, ds, dates, tmp_path = env
    rid = save(c, ds)['record_id']
    # 先录一笔成交，但行情还没更新到成交日 → 不得用成交日之前的价格估浮盈（时间倒流）
    d = c.post(f'/api/plans/{rid}/fills',
               json={'side': 'buy', 'qty': 8000, 'price': 12.0, 'trade_date': dates[41]}
               ).json()['data']
    m = d['review']['market']
    assert m['valuable'] is False and m['open_pnl'] is None
    assert '尚未更新到成交日' in m['note']

    add_later_dataset(c, tmp_path)
    m = c.get(f'/api/plans/{rid}').json()['data']['review']['market']
    assert m['valuable'] is True
    assert m['valuation_date'] == dates[43]
    assert m['valuation_close'] == 12.8
    assert m['open_pct'] == pytest.approx(6.67, abs=0.02)


def test_review_no_action_when_plan_was_not_confirmed(env):
    """计划当时就没让买（历史不足）→ no_action，不能算成"未成交"（那是两回事）。"""
    c, ds, dates, _ = env
    d = save(c, ds, params={'entry': 'ma_cross', 'ma_n': 80, 'tp': 9, 'sl': 12})
    assert d['plan']['status'] == 'data_unavailable'
    assert d['review']['outcome'] == 'no_action'
    assert d['review']['not_filled']['reason_code'] == 'plan_not_actionable'
    assert '80' in d['review']['not_filled']['text']


def test_record_delete_cascades_fills(env):
    c, ds, dates, _ = env
    rid = save(c, ds)['record_id']
    c.post(f'/api/plans/{rid}/fills',
           json={'side': 'buy', 'qty': 100, 'price': 11, 'trade_date': dates[41]})
    assert c.delete(f'/api/plans/{rid}').status_code == 200
    assert c.get(f'/api/plans/{rid}').status_code == 404
    assert len(c.get('/api/plans').json()['data']['items']) == 0


def test_list_filters_and_stats(env):
    c, ds, dates, _ = env
    rid = save(c, ds)['record_id']
    c.post(f'/api/plans/{rid}/cancel', json={'reason': 'x'})
    data = c.get('/api/plans?lifecycle=cancelled').json()['data']
    assert len(data['items']) == 1 and data['stats']['by_lifecycle']['cancelled'] == 1
    assert c.get('/api/plans?lifecycle=nope').status_code == 404


# ---------------- 刷新恢复（BUY-10） ----------------
LAST = '/api/plans/last'


def last(c, ds=None, code='600001', model_id='M4'):
    """恢复接口只按「股票 + 模型」定位（数据集是版本化快照，不能锁死旧 dataset_id）。"""
    r = c.get(LAST, params={'code': code, 'model_id': model_id})
    assert r.status_code == 200, r.text
    return r.json()['data']


def test_last_plan_restores_saved_snapshot(env):
    """刷新页面后能拿回「上次保存的那份计划」，而不是从空白开始（旧行为：刷新即失效）。"""
    c, ds, dates, _ = env
    assert last(c, ds)['found'] is False            # 还没保存过：明确说不存在

    d = save(c, ds)
    data = last(c, ds)
    assert data['found'] is True
    assert data['record']['id'] == d['record_id']
    assert data['record']['plan']['as_of_date'] == dates[40]
    assert data['record']['plan']['validity']['plan_id'] == d['record']['plan_id']
    assert data['review']['outcome'] in ('pending', 'not_filled')

    # 模型对不上时退到「该股票最近一条」：刷新后前端回到默认模型，不能让计划凭空消失
    fb = last(c, ds, model_id='M1')
    assert fb['found'] is True and fb['record']['model_id'] == 'M4'
    assert last(c, ds, code='600999')['found'] is False
    # 不传模型：直接取该股票最近一条
    assert c.get(LAST, params={'code': '600001'}).json()['data']['record']['id'] == d['record_id']


def test_last_plan_returns_newest_record(env):
    c, ds, dates, tmp_path = env
    old_id = save(c, ds)['record_id']
    add_later_dataset(c, tmp_path)
    new_id = c.post(f'/api/plans/{old_id}/rollover').json()['data']['record_id']
    data = last(c, ds)
    assert data['record']['id'] == new_id           # 恢复最新那条，不是最早的
    assert data['record']['renewed_from'] == old_id


# ---------------- 未成交续期（BUY-08） ----------------
def test_rollover_replans_with_latest_data_and_links_old(env):
    """续期 = 用最新行情重新生成计划（不是延长旧有效期）：新记录 + 旧记录只加关联。"""
    c, ds, dates, tmp_path = env
    d = save(c, ds)
    old_id = d['record_id']
    old_plan = json.dumps(d['record']['plan'], sort_keys=True)

    add_later_dataset(c, tmp_path)                  # 行情延长到 dates[43]
    r = c.post(f'/api/plans/{old_id}/rollover')
    assert r.status_code == 200, r.text
    data = r.json()['data']
    assert data['record_id'] != old_id
    assert data['renewed_from'] == old_id and data['unchanged'] is False
    assert data['record']['as_of'] == dates[43]     # 依据最新行情重算
    assert data['record']['renewed_from'] == old_id
    assert data['record']['plan']['execution']['earliest_execution_session'] is None \
        or data['record']['plan']['execution']['earliest_execution_session'] >= dates[43]

    old = c.get(f'/api/plans/{old_id}').json()['data']
    assert old['record']['superseded_by'] == data['record_id']
    assert old['record']['lifecycle'] == 'planned'                    # 续期不是“取消”
    assert json.dumps(old['record']['plan'], sort_keys=True) == old_plan   # 快照一个字节都不改
    assert old['record']['as_of'] == dates[40]

    nf = old['review']['not_filled']
    assert nf['reason_code'] == 'superseded' and nf['superseded_by'] == data['record_id']
    assert any('续期' in t['text'] for t in old['review']['timeline'])
    assert any(str(data['record_id']) in n for n in old['review']['notes'])


def test_rollover_without_new_data_reuses_same_record(env):
    """行情没变时续期不该凭空造一条重复记录：直接告诉你“计划没有变化”。"""
    c, ds, dates, _ = env
    old_id = save(c, ds)['record_id']
    data = c.post(f'/api/plans/{old_id}/rollover').json()['data']
    assert data['unchanged'] is True and data['record_id'] == old_id
    assert data['record']['superseded_by'] is None
    assert '完全一致' in data['note']
    assert len(c.get('/api/plans').json()['data']['items']) == 1


def test_rollover_rejects_filled_cancelled_and_already_renewed(env):
    c, ds, dates, tmp_path = env
    rid = save(c, ds)['record_id']
    c.post(f'/api/plans/{rid}/fills',
           json={'side': 'buy', 'qty': 100, 'price': 11, 'trade_date': dates[41]})
    r = c.post(f'/api/plans/{rid}/rollover')
    assert r.status_code == 422 and '无需续期' in r.json()['error']

    other = save(c, ds, params={'entry': 'ma_cross', 'ma_n': 20, 'tp': 10, 'sl': 12})
    c.post(f"/api/plans/{other['record_id']}/cancel", json={'reason': '改主意了'})
    r = c.post(f"/api/plans/{other['record_id']}/rollover")
    assert r.status_code == 422 and '已取消' in r.json()['error']

    # 续期链不能分叉：旧记录再次续期要指回已有的新记录，而不是又生成一条
    add_later_dataset(c, tmp_path)
    fresh = save(c, ds, params={'entry': 'ma_cross', 'ma_n': 20, 'tp': 8, 'sl': 12},
                 dedupe=False)
    first_new = c.post(f"/api/plans/{fresh['record_id']}/rollover").json()['data']['record_id']
    r = c.post(f"/api/plans/{fresh['record_id']}/rollover")
    assert r.status_code == 422 and '已续期到' in r.json()['error']
    assert str(first_new) in r.json()['error']


def test_list_exposes_renewal_links(env):
    c, ds, dates, tmp_path = env
    old_id = save(c, ds)['record_id']
    add_later_dataset(c, tmp_path)
    new_id = c.post(f'/api/plans/{old_id}/rollover').json()['data']['record_id']
    items = {x['id']: x for x in c.get('/api/plans').json()['data']['items']}
    assert items[old_id]['superseded_by'] == new_id
    assert items[new_id]['renewed_from'] == old_id
    assert items[new_id]['superseded_by'] is None
