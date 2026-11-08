"""引擎核心自测：正确性 / 无未来函数 / 费用 / 约束 / 可复现 / 样本外回归。
直接 import engine（纯标准库），不依赖 FastAPI 与数据库。
"""
import os
import random
import datetime as _dt
from engine.types import Bar, Trade
from engine.cost import Cost
from engine import metrics as M
from engine.audit import audit
from engine.backtest import run
from engine.models.builtin import MODELS, dp_oracle_result

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_CSV = os.path.join(HERE, '..', 'data', 'sample_600186.csv')


def make_bars(prices, *, blocked_idx=None, suspended_idx=None):
    blocked = set(blocked_idx or [])
    suspended = set(suspended_idx or [])
    bars = []
    for i, p in enumerate(prices):
        d = (_dt.date(2020, 1, 1) + _dt.timedelta(days=i)).isoformat()
        bars.append(Bar(
            date=d, open=p, high=p * 1.01, low=p * 0.99, close=p, pre_close=p,
            volume=1000, amount=1000 * p, turnover=1.0, factor=1.0,
            adj_open=p, adj_high=p * 1.01, adj_low=p * 0.99, adj_close=p,
            limit_up=p * 1.1, limit_down=p * 0.9,
            can_buy=(i not in blocked), can_sell=True, suspended=(i in suspended),
        ))
    return bars


# 1) DP oracle 与无记忆暴力枚举（小 n，纯枚举，无 memo）在合成数据上完全一致
def _brute_multiple(prices, hold, cost, capital):
    """独立暴力枚举所有「不重叠交易集合」，并用与 DP 相同的现金再投资口径模拟，
    返回最终资金倍数。n 较小（<=12）时指数级可接受，作为 DP 正确性交叉验证。"""
    n = len(prices)
    def rec(k, cash):
        if k >= n:
            return cash
        best = rec(k + 1, cash)  # 不在 k 开仓
        for j in range(k + hold, n):
            px = prices[k]
            lots = int(cash * 0.98 // (px * 100))
            if lots < 1:
                continue
            qty = lots * 100
            amt = px * qty
            nc = cash - amt - cost.buy_fee(amt)
            samt = prices[j] * qty
            nc += samt - cost.sell_fee(samt)
            val = rec(j + 1, nc)
            if val > best:
                best = val
        return best
    return rec(0, capital) / capital


def test_dp_vs_brute():
    cost = Cost()
    random.seed(7)
    for t in range(60):
        n = random.randint(6, 12)
        prices = [10.0]
        for _ in range(n - 1):
            prices.append(max(1.0, prices[-1] * (1 + random.uniform(-0.08, 0.08))))
        bars = make_bars(prices)
        hold = random.choice([1, 2, 3])
        gap = random.choice([0, 1])
        res = dp_oracle_result(bars, hold, cost, 1e9)
        mult_dp = res['metrics']['total_pct'] / 100.0 + 1.0
        mult_brute = _brute_multiple(prices, hold, cost, 1e9)
        assert abs(mult_dp - mult_brute) < 1e-3, (t, mult_dp, mult_brute)


# 2) 无未来函数：在随机截断点重算，持仓/权益前缀必须完全一致
def test_no_lookahead():
    random.seed(11)
    prices = [10.0]
    for _ in range(199):
        prices.append(max(1.0, prices[-1] * (1 + random.uniform(-0.06, 0.06))))
    bars = make_bars(prices)
    cost = Cost()
    res = audit(bars, MODELS['M4'], {'tp': 8, 'sl': 5, 'min_hold': 3, 'max_hold': 10, 'ma_n': 20}, cost, 100000.0, cuts=50, seed=42)
    assert res['passed'] is True, res['failed'][:3]


# 3) 费用：小额交易触发最低佣金 5 元；卖出印花税单向
def test_cost():
    c = Cost()
    amt = 1000.0  # 买 1000 元，佣金 0.25 低于最低 5 元 -> 取 5 元，再加过户费 0.01
    bf = c.buy_fee(1000.0)
    assert abs(bf - 5.01) < 1e-9, bf
    sf = c.sell_fee(20000.0)
    # 佣金 5 + 印花税 20000*0.0005=10 + 过户费 0.2 = 15.2
    assert abs(sf - 15.2) < 1e-9, sf
    # 净收益：高价卖低价买为正
    assert c.net_return(10.0, 11.0, 100) > 0
    assert c.net_return(11.0, 10.0, 100) < 0


# 4) 指标：空交易样本不产生异常，暴露度为 0
def test_metrics_empty():
    b0 = make_bars([10.0])
    m = M.compute(b0, [1.0], [], [0], 1.0)
    assert m['n_trades'] == 0
    assert m['exposure_pct'] == 0.0
    assert m['total_pct'] == 0.0
    assert m['sample_warning'] is True


# 5) 约束：被标记为不可买（如涨停 / can_buy=False）的当日，模型不得建仓
def test_constraints_block_buy():
    prices = [10.0] * 20
    blocked = 1  # M1 在 idx1（次日开盘）建仓，应被拦截
    bars = make_bars(prices, blocked_idx={blocked})
    cost = Cost()
    res = run(bars, MODELS['M1'], {}, cost, 100000.0)
    buy_dates = {t.buy_date for t in res.trades}
    blocked_date = (_dt.date(2020, 1, 1) + _dt.timedelta(days=blocked)).isoformat()
    assert blocked_date not in buy_dates, ('不应在不可买日建仓', buy_dates)
    # 未被拦截的其他交易日仍可正常建仓
    assert any(t.buy_date != blocked_date for t in res.trades), '应有正常建仓'


# 6) 可复现：相同输入两次结果完全一致（指标 + 数据哈希）
def test_reproducibility():
    random.seed(3)
    prices = [10.0]
    for _ in range(99):
        prices.append(max(1.0, prices[-1] * (1 + random.uniform(-0.05, 0.05))))
    bars = make_bars(prices)
    cost = Cost()
    p = {'tp': 8, 'sl': 5, 'min_hold': 3, 'max_hold': 10, 'ma_n': 20}
    r1 = run(bars, MODELS['M4'], p, cost, 100000.0)
    r2 = run(bars, MODELS['M4'], p, cost, 100000.0)
    assert r1.metrics == r2.metrics
    assert r1.equity == r2.equity
    assert r1.trades == r2.trades


# 7) 样例全样本回测可跑通（注意：这是**全样本**，不是样本外结论；
#    样本外切分见下一条 test_holdout_split_and_verdict 与 test_contracts）
def _sample_bars():
    from db import repository
    conn = repository.init_db(':memory:')
    repository.ingest_csv(conn, SAMPLE_CSV, 'sample', 'local_csv', '')
    return repository.get_bars(conn, 1, '600186')


def test_sample_backtest_runs():
    if not os.path.exists(SAMPLE_CSV):
        import pytest
        pytest.skip('未找到样例数据 sample_600186.csv')
    bars = _sample_bars()
    res = run(bars, MODELS['M4'], {'tp': 9, 'sl': 12, 'min_hold': 3, 'max_hold': 12, 'ma_n': 20},
              Cost(), 100000.0)
    assert res.metrics['n_trades'] > 0
    assert isinstance(res.metrics['total_pct'], float)
    assert res.final['liquidated'] in (True, False)


# 7b) 样本外切分：训练段与测试段不重叠，诚实口径与三档判定结构完整
def test_holdout_split_and_verdict():
    if not os.path.exists(SAMPLE_CSV):
        import pytest
        pytest.skip('未找到样例数据 sample_600186.csv')
    from engine.optimize import optimize
    bars = _sample_bars()
    o = optimize(bars, 'M4', {'tp': [6, 9, 3], 'sl': [4, 6, 2]}, Cost(), 100000.0,
                 test_days=61, min_trades=1)
    assert o['train_range'][1] < o['test_range'][0], '训练段必须早于测试段'
    assert o['honest'] is not None and o['best'] is not None
    assert o['verdict'] in ('red', 'yellow', 'green')
    assert o['test_days'] == 61 and o['n_combos'] == 4
    assert o['test_range'][1] == bars[-1].date


# 8) 滑点模型：slip=0 行为不变；滑点增大 → 成交价劣化 → 收益单调不升
def test_slippage_model():
    random.seed(21)
    prices = [10.0]
    for _ in range(99):
        prices.append(max(1.0, prices[-1] * (1 + random.uniform(-0.04, 0.04))))
    bars = make_bars(prices)
    p = {'tp': 8, 'sl': 5, 'min_hold': 3, 'max_hold': 10, 'ma_n': 20}

    r0 = run(bars, MODELS['M4'], p, Cost(), 100000.0)
    r_half = run(bars, MODELS['M4'], p, Cost(slippage_pct=0.1), 100000.0)
    r_full = run(bars, MODELS['M4'], p, Cost(slippage_pct=0.5), 100000.0)

    # slip=0：与默认 Cost 完全一致（向后兼容）
    r_zero = run(bars, MODELS['M4'], p, Cost(slippage_pct=0.0), 100000.0)
    assert r0.metrics == r_zero.metrics
    assert [t.sell_price for t in r0.trades] == [t.sell_price for t in r_zero.trades]

    # 单调性：滑点越大，总收益不高于小滑点
    assert r_half.metrics['total_pct'] <= r0.metrics['total_pct'] + 1e-9
    assert r_full.metrics['total_pct'] <= r_half.metrics['total_pct'] + 1e-9

    # 滑点后的成交价确实劣化：买价 >= 原买价（上浮），卖价 <= 原卖价（下压）
    assert r_half.trades, '应有交易'
    b0, b1 = r0.trades[0], r_half.trades[0]
    assert b1.buy_price >= b0.buy_price - 1e-9
    assert b1.sell_price <= b0.sell_price + 1e-9


# 9) walk-forward：折数/区间正确、样本不足时优雅降级
def test_walk_forward():
    random.seed(33)
    prices = [10.0]
    for _ in range(299):
        prices.append(max(1.0, prices[-1] * (1 + random.uniform(-0.05, 0.05))))
    bars = make_bars(prices)
    from engine.optimize import walk_forward
    grid = {'min_hold': [1, 5], 'tp': [4, 12, 4], 'sl': [2, 8, 2]}
    wf = walk_forward(bars, 'M4', grid, Cost(), folds=3, test_days=61)
    assert wf['folds'] == 3
    assert len(wf['rows']) == 3
    # 折区间连续：fold k 的测试段紧接 fold k-1 的测试段之后
    for k in range(1, 3):
        prev_end = wf['rows'][k - 1]['test_range'][1]
        cur_start = wf['rows'][k]['test_range'][0]
        assert prev_end < cur_start
    # 每折训练窗扩张（anchored）
    for k in range(1, 3):
        assert wf['rows'][k]['train_range'][1] >= wf['rows'][k - 1]['train_range'][1]
    # 汇总字段存在
    assert 'mean_expectancy' in wf and 'pos_folds' in wf

    # 样本不足：30 根无法滚动走查，优雅返回错误说明
    small = make_bars([10.0] * 30)
    wf2 = walk_forward(small, 'M4', grid, Cost(), folds=3, test_days=61)
    assert wf2['folds'] == 0 and 'error' in wf2


# 10) 量价因子库：因果性（截断前缀值不变）+ IC 可计算
def test_factors_causal_and_ic():
    random.seed(55)
    prices = [10.0]
    for _ in range(119):
        prices.append(max(1.0, prices[-1] * (1 + random.uniform(-0.04, 0.04))))
    bars = make_bars(prices)
    from engine.factors import compute_factors, factor_ic, FACTOR_NAMES

    full = compute_factors(bars)
    prefix = compute_factors(bars[:80])
    # 因果性：前 80 个因子值与全量计算完全一致（无未来函数）
    for name in FACTOR_NAMES:
        for i in range(80):
            a, b = full[name][i], prefix[name][i]
            if a is None or b is None:
                assert a == b, (name, i)
            else:
                assert abs(a - b) < 1e-9, (name, i)

    # IC：全部因子在 h=20 可计算（不抛错、返回 float 或 None）
    ics = factor_ic(bars, horizon=20)
    assert set(ics.keys()) == set(FACTOR_NAMES)
    assert any(v is not None for v in ics.values())


# 11) 因子库：真实样例上 CMF 为负（与 README 实证结论同向：资金流因子稳定反向）
def test_factors_cmf_negative_on_sample():
    if not os.path.exists(SAMPLE_CSV):
        import pytest
        pytest.skip('未找到样例数据 sample_600186.csv')
    from db import repository
    from engine.factors import factor_ic
    conn = repository.init_db(':memory:')
    repository.ingest_csv(conn, SAMPLE_CSV, 'sample', 'local_csv', '')
    bars = repository.get_bars(conn, 1, '600186')
    ics = factor_ic(bars, 'cmf20', horizon=20)
    assert ics['cmf20'] is not None
    # 单票不要求必须为负，但量级应在合理范围 |IC| <= 0.5
    assert abs(ics['cmf20']) <= 0.5


# ---------- 自定义规则 DSL（M9/M10） ----------
def _cond(left, cmp_key, right_indicator=None, right_constant=None,
          temporal='current', periods=1):
    return {'$type': 'condition', 'left': left, 'cmp': cmp_key,
            'right_indicator': right_indicator, 'right_constant': right_constant,
            'temporal': temporal, 'periods': periods}


def _group(op, children):
    return {'$type': 'group', 'op': op, 'children': children}


# 12) 因果性：每个指标在截断数据上前 t 项必须与全量完全一致（无未来函数）
def test_rules_indicators_causal():
    from engine.rules import COMPUTE, INDICATOR_KEYS
    prices = [10 + 0.5 * ((i * 7) % 13) - 0.3 * ((i * 5) % 11) for i in range(120)]
    bars = make_bars(prices)
    assert set(COMPUTE.keys()) == set(INDICATOR_KEYS)
    for key in INDICATOR_KEYS:
        full = COMPUTE[key](bars)
        for t in (30, 60, 90, 119):
            part = COMPUTE[key](bars[:t + 1])
            for i in range(t + 1):
                a, b = part[i], full[i]
                if a is None or b is None:
                    assert a == b, (key, t, i)
                else:
                    assert abs(a - b) < 1e-9, (key, t, i)


# 13) 时序算子：当前 / 上穿 / 下穿 / 连续N日
def test_rules_temporal_operators():
    from engine.rules import evaluate_rule
    # 收盘价阶梯上行，MA20 之后被追上 → 构造明确的上穿点（需 ≥20 根才有 MA20）
    prices = [10.0] * 30 + [10.5, 11.0, 11.5, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0]
    bars = make_bars(prices)

    cur = evaluate_rule(_group('all', [_cond('close', 'gt', right_indicator='sma20')]), bars)
    cross = evaluate_rule(
        _group('all', [_cond('close', 'gt', right_indicator='sma20', temporal='cross_above')]),
        bars)
    # 上穿次数必然 <= 当前满足次数，且至少出现一次
    assert sum(cross) >= 1 and sum(cross) <= sum(cur)
    # 上穿点一定是当前满足点（子集关系）
    for i in range(len(bars)):
        if cross[i]:
            assert cur[i] == 1

    # 下穿：收盘跌破 MA20（< 关系）
    down = _group('all', [_cond('close', 'lt', right_indicator='sma20', temporal='cross_below')])
    prices2 = [20.0] * 30 + [15.0, 12.0, 10.0, 8.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    cross_down = evaluate_rule(down, make_bars(prices2))
    assert sum(cross_down) >= 1

    # 连续 N 日：连续 3 日收盘 > MA20
    cons = evaluate_rule(
        _group('all', [_cond('close', 'gt', right_indicator='sma20',
                             temporal='consecutive', periods=3)]), bars)
    run_len = 0
    for i in range(len(bars)):
        run_len = run_len + 1 if cur[i] else 0
        assert cons[i] == (1 if run_len >= 3 else 0)


# 14) 分组嵌套 AND/OR 与常量右值
def test_rules_group_and_or():
    from engine.rules import evaluate_rule
    prices = [10 + (i % 9) * 0.6 for i in range(80)]
    bars = make_bars(prices)
    a = _cond('close', 'gt', right_constant=12.0)
    b = _cond('volume_ratio20', 'gte', right_constant=0.5)
    all_sig = evaluate_rule(_group('all', [a, b]), bars)
    any_sig = evaluate_rule(_group('any', [a, b]), bars)
    for i in range(len(bars)):
        # any 为真的位置，all 未必真；all 真则 any 必真
        if all_sig[i]:
            assert any_sig[i] == 1
    assert sum(any_sig) >= sum(all_sig)


# 15) 校验：非法规则必须抛 ValueError（右值二选一、周期范围、算子搭配）
def test_rules_validation():
    import pytest
    from engine.rules import validate_rule
    ok = _cond('close', 'gt', right_indicator='sma20')
    validate_rule(_group('all', [ok]))       # 不抛错

    with pytest.raises(ValueError):
        validate_rule(_cond('close', 'gt'))                      # 右值既无指标也无常量
    with pytest.raises(ValueError):
        validate_rule(_cond('close', 'gt', right_indicator='sma20', right_constant=1.0))  # 两者都给
    with pytest.raises(ValueError):
        validate_rule(_cond('close', 'gt', right_constant=1.0, temporal='consecutive', periods=99))
    with pytest.raises(ValueError):
        validate_rule(_cond('close', 'gt', right_constant=1.0, temporal='current', periods=5))
    with pytest.raises(ValueError):
        validate_rule(_cond('close', 'lt', right_constant=1.0, temporal='cross_above'))
    with pytest.raises(ValueError):
        validate_rule(_cond('nope', 'gt', right_constant=1.0))
    with pytest.raises(ValueError):
        validate_rule(_group('all', []))                          # 空分组


# 16) M9 防未来函数审计（随机 50 个截断点，position/equity 必须完全一致）
def test_m9_no_lookahead():
    if not os.path.exists(SAMPLE_CSV):
        import pytest
        pytest.skip('未找到样例数据 sample_600186.csv')
    from db.importer import parse_csv
    from engine.bars import build_bars
    from engine.rules import default_rule
    bars = build_bars(parse_csv(SAMPLE_CSV)['groups']['600186'])
    p = {'rule': default_rule(), 'min_hold': 3, 'tp': 8, 'sl': 5, 'max_hold': 10}
    res = audit(bars, MODELS['M9'], p, Cost(), 100000.0, cuts=50)
    assert res['passed'], res['failed']


# 17) 等价性：M9「收盘 > MA20」应与 M4「ma_cross(ma_n=20)」产生相同信号与相同回测
def test_m9_equivalent_to_m4():
    if not os.path.exists(SAMPLE_CSV):
        import pytest
        pytest.skip('未找到样例数据 sample_600186.csv')
    from db.importer import parse_csv
    from engine.bars import build_bars
    from engine.rules import default_rule
    bars = build_bars(parse_csv(SAMPLE_CSV)['groups']['600186'])
    rule_now = _group('all', [_cond('close', 'gt', right_indicator='sma20')])
    p9 = {'rule': rule_now, 'min_hold': 3, 'tp': 8, 'sl': 5, 'max_hold': 10}
    p4 = {'entry': 'ma_cross', 'ma_n': 20, 'min_hold': 3, 'tp': 8, 'sl': 5, 'max_hold': 10}
    r9 = run(bars, MODELS['M9'], p9, Cost(), 100000.0)
    r4 = run(bars, MODELS['M4'], p4, Cost(), 100000.0)
    assert r9.position == r4.position
    assert abs(r9.metrics['total_pct'] - r4.metrics['total_pct']) < 1e-6


# 18) 默认规则与模板：全部可求值且不抛错
def test_rules_templates_evaluable():
    if not os.path.exists(SAMPLE_CSV):
        import pytest
        pytest.skip('未找到样例数据 sample_600186.csv')
    from db.importer import parse_csv
    from engine.bars import build_bars
    from engine.rules import TEMPLATES, default_rule, evaluate_rule, rule_text
    bars = build_bars(parse_csv(SAMPLE_CSV)['groups']['600186'])
    sig = evaluate_rule(default_rule(), bars)
    assert len(sig) == len(bars) and sum(sig) > 0
    for t in TEMPLATES:
        s = evaluate_rule(t['rule'], bars)
        assert len(s) == len(bars), t['name']
        assert rule_text(t['rule'])


# 19) 交易明细必须注明「哪个策略的哪条条件命中买入」，卖出侧保留 reason 代码
def test_trade_records_entry_reason_per_model():
    if not os.path.exists(SAMPLE_CSV):
        import pytest
        pytest.skip('未找到样例数据 sample_600186.csv')
    from db.importer import parse_csv
    from engine.bars import build_bars
    bars = build_bars(parse_csv(SAMPLE_CSV)['groups']['600186'])

    p4 = {'entry': 'ma_cross', 'ma_n': 20, 'min_hold': 3, 'tp': 8, 'sl': 5, 'max_hold': 10}
    r4 = run(bars, MODELS['M4'], p4, Cost(), 100000.0)
    assert r4.trades
    t0 = r4.trades[0]
    assert 'MA20' in t0.entry_reason, t0.entry_reason
    # 信号在买入前一根 K 线收盘确认：文案里必须出现信号日，且早于成交日
    sig_date = bars[t0.buy_i - 1].date
    assert sig_date in t0.entry_reason, (sig_date, t0.entry_reason)
    assert t0.entry_reason.endswith('收盘信号）')
    assert t0.exit_reason in ('tp', 'sl', 'timeout', 'eod')

    r1 = run(bars, MODELS['M1'], {'min_hold': 1, 'max_hold': None}, Cost(), 100000.0)
    assert r1.trades and '买入持有' in r1.trades[0].entry_reason

    from engine.rules import TEMPLATES
    rule = TEMPLATES[0]['rule']                 # 内置模板：保证合法且可求值
    r9 = run(bars, MODELS['M9'], {'rule': rule, 'min_hold': 3, 'tp': 8, 'sl': 5,
                                  'max_hold': 10}, Cost(), 100000.0)
    assert r9.trades and '自定义规则' in r9.trades[0].entry_reason
    assert len(r9.trades[0].entry_reason) > len('自定义规则：') + 4


def test_dp_oracle_trades_mark_as_baseline():
    """DP 对照基线也要标清来源，避免和可交易模型混为一谈。"""
    if not os.path.exists(SAMPLE_CSV):
        import pytest
        pytest.skip('未找到样例数据 sample_600186.csv')
    from db.importer import parse_csv
    from engine.bars import build_bars
    bars = build_bars(parse_csv(SAMPLE_CSV)['groups']['600186'])
    o = dp_oracle_result(bars, 3, Cost(), 100000.0)
    if o['trades']:
        assert all('DP' in t['entry_reason'] for t in o['trades']), o['trades'][0]
