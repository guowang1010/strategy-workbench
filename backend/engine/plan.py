# -*- coding: utf-8 -*-
"""下一笔交易计划（BUY-01～10）：把「今天有没有信号、下一交易日做什么、多少钱」讲清楚。

与旧版的差别（旧版只有阈值 + 一段文案）：
  - **时序**：收盘确认 → 下一交易日开盘执行。执行日由交易日历解析，日历未确认时
    标 `confirmed=False` 进 pending_checks，绝不用「as_of + 1 个自然日」冒充交易日；
  - **状态机**：data_unavailable / watching / signal_confirmed / blocked / holding / expired，
    每个状态都给 reason_codes + 中文说明，未知不冒充通过；
  - **信号复用**：先用 `{**model.defaults, **params}` 合并参数，再调用模型自己的
    `target()` 求信号，不在这里另推一套阈值语义（旧版 M3 的 ma_n 默认值就与引擎不一致）；
  - **条件可解释**：as_of 条件树（值/比较/是否满足/是否未知）+ 下一根触发（解析阈值或区间），
    见 plan_conditions；
  - **账户隔离**：strategy_simulated 用回放账本，manual_actual 用用户输入快照，
    实际持仓不会被回测结果覆盖；
  - **可复现**：plan_id 由数据哈希 + 代码 + 模型 + 有效参数 + 执行配置 + **整份账户快照**
    + **整份规范化执行规则** + 研究模式 + 数据状态 + 引擎版本决定（F02：任何会改变结论的
    输入都必须在指纹里，否则去重会把两份不同结论当成同一份计划）；
  - **时点**：判断数据是否落后看的是「最近一个已收盘交易日」，不是「今天几号」（F01）。
"""
import hashlib
import json
import math
from datetime import date, datetime

from .backtest import order_size, run
from .plan_conditions import build_condition_tree, build_trigger
from .rules import rule_text
from .signals import entry_condition_text
from .trading_calendar import TradingCalendar, iso, now_cn, to_date
from .version import ENGINE_VERSION

PLAN_VERSION = '2.0.0'
SIZE_TICK = 100                     # 一手

ACCOUNT_MODES = ('strategy_simulated', 'manual_actual')
FOLLOW_MODES = ('allow_existing', 'new_signal_only')

# 计划级状态（BUY-02）
STATUS_TEXT = {
    'data_unavailable': '数据不足，无法判断',
    'watching': '等待信号确认',
    'signal_confirmed': '信号已确认，等待下一交易日执行',
    'blocked': '暂不可执行',
    'holding': '持仓中（无买入计划）',
    'expired': '计划已失效',
}

# 执行窗口（F01）：同一份计划在「开盘前 / 盘中 / 收盘后」含义不同，不能只给一个日期。
WINDOW_TEXT = {
    'open': '执行窗口未关闭：按计划在开盘执行（盘中下单须按现价重算数量）',
    'passed': '执行日已收盘：本次开盘机会已过，未成交须重新生成计划',
    'closed': '计划已失效：行情不是最新已完成日线',
    'n/a': '当前不是待执行计划',
    'unknown': '执行窗口无法确定（缺少交易日历依据）',
}
PHASE_TEXT = {
    'pre_open': '开盘前', 'in_session': '盘中', 'after_close': '已收盘',
    'non_session': '休市日', 'unknown': '未知',
}


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def _plan_id(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode('utf-8')).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


# 估算入场价的口径说明：数量、费用、风险预算、保护价共用一个价格，口径必须写清。
# 注意这些都只是「预算假设价」：实际开盘可能高于也可能低于它，谁也不能承诺成交价（D05）。
ENTRY_BASIS_TEXT = {
    'as_of_close': 'as_of 收盘价（预算假设价：与最新收盘一致；次日开盘可能高于或低于它）',
    'trigger_price': '触发价（预算假设价：下一根收盘需达到该价计划才成立；按它预留资金，'
                     '实际开盘可能高于或低于它，不构成成交价下界）',
    'interval_upper': '回踩区间上沿（预算假设价：按区间内最贵一档预留资金，实际成交可能更便宜也可能更贵）',
    'multi_interval_upper': '多区间最高上沿（预算假设价：按最贵一档预留资金，实际成交价可能不同）',
}


def _estimate_entry_price(trigger: dict | None, ref_price: float,
                          confirmed: bool) -> tuple[float, str]:
    """选一个「估算入场价」，供数量/费用/风险/保护价共用（第九轮）。

    已确认的计划在下一交易日开盘买，最接近的参考就是 as_of 收盘；
    未确认的计划要等**未来收盘**达到触发价才成立，用触发价（或区间上沿）做
    **预算假设价**——宁可按更贵的价预留资金。注意它不是成交价下界：
    确认次日的开盘可能跳空低于触发价，也可能更高。

    返回 `(price, basis)`，basis 进 ENTRY_BASIS_TEXT 与前端文案；不做四舍五入，
    取整只发生在展示层与最小价格单位处理处。
    """
    if confirmed:
        return float(ref_price), 'as_of_close'
    t = trigger or {}
    cls = t.get('classification')
    if cls in ('single_threshold', 'conditional_on_volume'):
        tp = t.get('threshold_price')
        if tp:
            return float(tp), 'trigger_price'
    if cls in ('interval', 'multi_interval'):
        his = [iv.get('hi') for iv in (t.get('intervals') or []) if iv.get('hi') is not None]
        if his:
            return float(max(his)), ('interval_upper' if cls == 'interval' else 'multi_interval_upper')
    return float(ref_price), 'as_of_close'


def min_history(model, p: dict) -> int:
    """该模型产出第一个有效信号所需的最少 K 线根数（用于 data_unavailable 判断）。"""
    if model.id == 'M1':
        return 1
    if p.get('rule'):
        return 1
    etype = p.get('entry') or 'ma_cross'
    if etype == 'ma_cross':
        return max(2, int(p.get('ma_n', 20)))
    if etype == 'breakout':
        return max(2, int(p.get('lookback', 20))) + 1
    if etype == 'pullback':
        return max(int(p.get('ma_n', 5)), int(p.get('ma_long', 20)))
    if etype == 'volume_confirm':
        return max(int(p.get('ma_n', 20)), int(p.get('vol_n', 20)))
    if etype == 'momentum':
        return int(p.get('mom_n', 10)) + 1
    return 2


def _account(req: dict | None, bars, model, p, cost, capital) -> dict:
    """账户快照。simulated 走回放账本；manual 完全采信用户输入。"""
    req = dict(req or {})
    mode = req.get('mode') or 'strategy_simulated'
    if mode not in ACCOUNT_MODES:
        raise ValueError(f'未知账户模式: {mode!r}（应为 {"/".join(ACCOUNT_MODES)}）')
    last_close = bars[-1].adj_close

    if mode == 'strategy_simulated':
        r = run(bars, model, p, cost, capital, liquidation_policy='mark_to_market')
        f = r.final or {}
        shares = float(f.get('shares') or 0)
        cash = float(f.get('cash') or 0)
        return {
            'mode': mode,
            'source': '回放账本（与回测同一撮合规则，期末盯市）',
            'as_of': bars[-1].date,
            'cash': round(cash, 2),
            'shares': shares,
            'sellable': shares,
            'cost_price': f.get('entry_price'),
            'entry_date': f.get('entry_date'),
            'hold_days': f.get('hold_days'),
            'market_value': round(shares * last_close, 2),
            'equity_value': round(cash + shares * last_close, 2),
            'equity_ratio': f.get('equity_ratio'),
            'holding': shares > 0,
            'allow_addon': False,
            'complete': True,
        }

    shares = req.get('shares')
    cash = req.get('cash')
    missing = []
    if cash is None:
        missing.append('cash')
    if shares is None:
        missing.append('shares')
    if missing:
        raise ValueError('手动账户缺少必填项：' + '、'.join(missing))
    cash = float(cash)
    shares = float(shares)
    if cash < 0 or shares < 0:
        raise ValueError('手动账户的现金与持仓不能为负')
    sellable = req.get('sellable')
    sellable = shares if sellable is None else float(sellable)
    if sellable < 0 or sellable > shares:
        raise ValueError(f'可卖数量非法：{sellable}（应在 0~{shares:g} 之间）')
    cost_price = req.get('cost_price')
    if cost_price is not None and float(cost_price) <= 0:
        raise ValueError('成本价必须为正')
    holding = shares > 0
    return {
        'mode': mode,
        'source': '用户手动输入（不随回测变化）',
        'as_of': req.get('snapshot_at') or bars[-1].date,
        'snapshot_at': req.get('snapshot_at'),
        'cash': round(cash, 2),
        'shares': shares,
        'sellable': sellable,
        'cost_price': round(float(cost_price), 4) if cost_price else None,
        'entry_date': req.get('entry_date'),
        'hold_days': None,
        'market_value': round(shares * last_close, 2),
        'equity_value': round(cash + shares * last_close, 2),
        'equity_ratio': round((cash + shares * last_close) / capital, 6) if capital else None,
        'holding': holding,
        'allow_addon': bool(req.get('allow_addon')),
        'complete': True,
    }


def _price_mapping(bars) -> dict:
    """前复权价 → 可委托参考价的映射依据（BUY-09）。

    build_bars 用 `adj = raw × factor / base`（base = 最新一根因子），所以最新一根
    adj == raw，ratio = 1.0；历史 bar 的 ratio = base / factor_i。未来若发生除权，
    因子变化会让旧映射失效——这里只能给出「截至 as_of 有效」的结论。
    """
    last = bars[-1]
    base = last.factor or 1.0
    last_action = None
    for i in range(len(bars) - 1, 0, -1):
        if abs((bars[i].factor or 1.0) - (bars[i - 1].factor or 1.0)) > 1e-9:
            last_action = bars[i].date
            break
    return {
        'signal_price_basis': '前复权（adj_close，基准=最新交易日复权因子）',
        'base_factor': round(base, 6),
        'as_of_factor': round(last.factor or 1.0, 6),
        'adj_to_raw_ratio': 1.0,
        'order_ref_ready': True,
        'corporate_action': {
            'detected_in_history': last_action is not None,
            'last_action_date': last_action,
            'note': ('执行日前若发生除权除息，复权因子变化，参考价与阈值必须重算'
                     if last_action else '历史区间内未检测到复权因子变化'),
        },
        'note': '参考价按 as_of 收盘（adj==raw）给出；不代表下一交易日开盘价',
    }


def _next_n_sessions(cal: TradingCalendar, start: str, n: int) -> tuple[str | None, bool]:
    """start 之后第 n 个交易日（n=1 即下一交易日）及其是否已确认。"""
    cur, ok = start, True
    for _ in range(max(1, n)):
        cur, info = cal.next_session(cur)
        ok = ok and bool(info.get('confirmed'))
        if cur is None:
            return None, False
    return cur, ok


def validated_rules(rules: dict | None) -> dict:
    """执行约束的边界校验（D01/D03）：API 与引擎双保险，直连引擎的调用方也逃不掉。

    关键纪律：**只有 None/缺失才采用默认值**。旧实现 `rules.get(...) or 100.0` 会把
    max_position_pct=0 静默替换成满仓——0 是用户输入，必须按 (0,100] 约定报错，
    不能假装用户想要 100%。负数、±inf、NaN 一律拒绝，不允许参与后续计算。
    """
    r = dict(rules or {})
    out: dict = {}

    mpp = r.get('max_position_pct')
    mpp = 100.0 if mpp is None else float(mpp)
    if not math.isfinite(mpp) or not (0 < mpp <= 100):
        raise ValueError('max_position_pct 须在 (0, 100] 内：0 不是「不限制」，'
                         '不允许当作 100% 满仓')
    out['max_position_pct'] = mpp

    mp = r.get('max_price')
    if mp is not None:
        mp = float(mp)
        if not math.isfinite(mp) or mp <= 0:
            raise ValueError('max_price 必须为正数（元）')
    out['max_price'] = mp

    dev = r.get('max_deviation_pct')
    if dev is not None:
        dev = float(dev)
        if not math.isfinite(dev) or dev < 0:
            raise ValueError('max_deviation_pct 不能为负')
    out['max_deviation_pct'] = dev

    risk = r.get('risk_pct')
    if risk is not None:
        risk = float(risk)
        if not math.isfinite(risk) or risk < 0:
            raise ValueError('risk_pct 不能为负（0 表示零风险预算：将买不进一手，属合法配置）')
    out['risk_pct'] = risk

    follow = r.get('follow_from')
    follow = 'allow_existing' if follow is None else str(follow)
    if follow not in FOLLOW_MODES:
        raise ValueError(f'follow_from 应为 {"/".join(FOLLOW_MODES)} 之一')
    out['follow_from'] = follow
    out['day_only'] = bool(r.get('day_only', True))
    return out


def next_plan(bars, model, params: dict, cost, capital: float = 100000.0, *,
              calendar: TradingCalendar | None = None, account: dict | None = None,
              rules: dict | None = None, dataset: dict | None = None,
              evidence: dict | None = None,
              today: str | date | None = None,
              now: str | datetime | None = None,
              research: bool = False, config_hash: str = '') -> dict:
    """生成下一笔计划。返回结构见模块头注释；同时保留旧字段以便渐进升级。

    `now`（F01）是判断「数据是否落后」的唯一时点来源，缺省取真实当前时刻（Asia/Shanghai）。
    `today` 是旧入口，只保留给研究/回放：**只给日期时按该日收盘后解释**，
    因为只有日期无法表达「还没开盘」，硬按日期判陈旧会在盘前把周五的计划作废。
    """
    rules = validated_rules(rules)                   # D01/D03：0、负数、非有限值在此拦截
    dataset = dict(dataset or {})
    if not bars:
        return _empty_plan(model, rules, dataset, 'no_bars', '该股票在数据集内无行情')

    p = {**model.defaults, **params}                  # 与 backtest.run 完全一致的合并顺序
    cal = calendar or TradingCalendar([b.date for b in bars])
    # Uploaded/crawled data may contain today's unfinished bar or future rows.
    # Never let those rows participate in signals or simulated account replay.
    now_dt = now_cn(now if now is not None else today)
    completed, _ = cal.latest_completed_session(now_dt)
    bars = [b for b in bars if completed is not None and b.date <= completed]
    if not bars:
        return _empty_plan(model, rules, dataset, 'no_completed_bars',
                           '当前时点没有已完成日线，不能确认买入信号')
    last_i = len(bars) - 1
    last_bar = bars[last_i]
    as_of = last_bar.date
    now_dt = now_cn(now if now is not None else today)
    today_s = now_dt.date().isoformat()
    # 「已完成的日线」到哪一天为止：盘前/盘中都还是上一个交易日，收盘后才算当天（F01）。
    latest_done, latest_info = cal.latest_completed_session(now_dt)
    sessions_behind, cal_confirmed = (cal.sessions_between(as_of, latest_done)
                                      if latest_done else ([], False))
    stale = bool(latest_done) and len(sessions_behind) > 0

    # ---------------- 账户 ----------------
    acct = _account(account, bars, model, p, cost, capital)

    # ---------------- 数据状态 ----------------
    need = min_history(model, p)
    sufficient = len(bars) >= need
    data_status = {
        'as_of': as_of,
        'bar_count': len(bars),
        'min_bars_required': need,
        'sufficient': sufficient,
        # F01：时点与「最新已完成日线」分开说清楚，不再用「今天是几号」冒充收盘状态
        'now': now_dt.isoformat(timespec='seconds'),
        'timezone': latest_info.get('timezone'),
        'today': today_s,
        'latest_completed_session': latest_done,
        'latest_completed_confirmed': bool(latest_info.get('confirmed')),
        'latest_completed_source': latest_info.get('source'),
        'sessions_behind': len(sessions_behind),
        'stale': stale,
        'sessions_behind_confirmed': cal_confirmed,
        'calendar': cal.coverage,
        'mode': 'research' if (stale and research) else ('live' if not stale else 'stale'),
    }

    # ---------------- 信号 ----------------
    confirmed = False
    prev_confirmed = False
    sig_err = None
    try:
        sig = model.target(bars, p)
        confirmed = bool(sig[last_i]) if sig else False
        prev_confirmed = bool(sig[last_i - 1]) if last_i >= 1 else False
    except Exception as e:                              # 规则非法/参数越界等
        sig_err = str(e)

    cond = build_condition_tree(bars, model, p) if (sufficient and not sig_err) else \
        {'tree': {'$type': 'group', 'op': 'all', 'text': '历史不足或参数非法，无法求值',
                  'satisfied': False, 'children': []}, 'missing': []}
    trigger = build_trigger(bars, model, p) if sufficient else {
        'entry_type': p.get('entry'), 'classification': 'insufficient_history',
        'threshold_price': None, 'cmp': None, 'intervals': [], 'unknown_components': [],
        'requirements': [], 'search_range': None, 'notes': ['历史不足'], 'price_basis': 'adj_close',
    }

    signal = {
        'status': ('confirmed' if confirmed else 'not_confirmed') if (sufficient and not sig_err) else 'unknown',
        'confirmed_at': f'{as_of} 收盘（已完成日线）' if confirmed else None,
        'evaluated_on': as_of,
        'semantics': trigger.get('semantics') or ('rule' if p.get('rule') else 'state_condition'),
        'entry_type': trigger.get('entry_type'),
        'entry_condition': (rule_text(p.get('rule')) if p.get('rule')
                            else entry_condition_text(p)),
        'condition_tree': cond['tree'],
        'missing_conditions': cond['missing'],
        'error': sig_err,
    }
    if model.id == 'M1':
        signal['semantics'] = 'always_on'
        signal['status'] = 'confirmed'
        signal['confirmed_at'] = f'{as_of}（买入持有：首个可交易日建仓）'
        signal['entry_condition'] = ('买入持有：首个可交易日（T+1）开盘满仓；'
                                     '资金不足一手或当日不可买则不成交')

    # ---------------- 执行时点 ----------------
    next_session, next_info = cal.next_session(as_of)
    if confirmed:
        check_session = as_of
        earliest, exec_info = next_session, next_info
    else:
        check_session, check_info = next_session, next_info
        earliest, exec_info = cal.next_session(check_session) if check_session else (None, {})
    session_confirmed = bool(exec_info.get('confirmed')) and bool(next_info.get('confirmed'))

    # ---------------- 状态机 ----------------
    reason_codes: list[dict] = []
    status = 'watching'
    if not model.tradable:
        status = 'blocked'
        reason_codes.append({'code': 'model_not_tradable', 'text': '该模型为事后最优上界，不可落地交易'})
    elif not sufficient:
        status = 'data_unavailable'
        reason_codes.append({'code': 'insufficient_history',
                             'text': f'需要至少 {need} 根已完成日线，当前 {len(bars)} 根'})
    elif sig_err:
        status = 'data_unavailable'
        reason_codes.append({'code': 'signal_error', 'text': f'信号求值失败：{sig_err}'})
    elif acct['holding']:
        status = 'holding'
        if acct['mode'] == 'manual_actual' and acct.get('allow_addon'):
            status = 'blocked'
            reason_codes.append({'code': 'addon_not_supported',
                                 'text': '当前版本不支持加仓：请先在账户中把持仓清零，或等待清仓后再跟随策略'})
        else:
            reason_codes.append({'code': 'holding',
                                 'text': ('手动账户已有持仓，默认不加仓' if acct['mode'] == 'manual_actual'
                                          else '回放账本显示当前持仓，无买入计划')})
    elif stale and not research:
        status = 'expired'
        reason_codes.append({'code': 'stale_data',
                             'text': f'行情截至 {as_of}，最新已完成日线为 {latest_done}，'
                                     f'缺少 {len(sessions_behind)} 个交易日；'
                                     f'历史数据不得当作当前待执行计划（如需研究请开启研究模式）'})
    elif confirmed:
        status = 'signal_confirmed'
        follow = rules.get('follow_from') or 'allow_existing'
        if follow == 'new_signal_only' and prev_confirmed:
            status = 'watching'
            reason_codes.append({'code': 'existing_signal_not_allowed',
                                 'text': '该信号在 as_of 之前已持续成立；配置为「只等新信号」，本次不入场'})
    else:
        status = 'watching'
        reason_codes.append({'code': 'signal_not_confirmed',
                             'text': 'as_of 收盘未满足入场条件，等待后续收盘确认'})

    # ---------------- 价格口径 ----------------
    ref_price = float(last_bar.adj_close)
    signal_price = trigger.get('threshold_price')
    # 统一价格情景（第九轮）：数量、费用、风险预算与保护价必须用**同一个**估算入场价。
    # 旧实现数量按 as_of 收盘算、保护价按触发价算，同一张计划里出现两套互不相容的账：
    # 收盘价 8.26 买 5900 股没问题，但按触发价 9.96 买同样 5900 股就超出预算。
    entry_est_price, entry_basis = _estimate_entry_price(trigger, ref_price, confirmed)

    # ---------------- 价格边界（D02：必须先于数量——被上限阻止的计划不得保留非零下单数量） ----------------
    max_price = rules['max_price']
    dev = rules['max_deviation_pct']
    base_for_dev = signal_price if signal_price else ref_price
    dev_cap = base_for_dev * (1 + dev / 100.0) if dev is not None else None
    caps = [x for x in (max_price, dev_cap) if x is not None]
    price_cap = min(caps) if caps else None
    # 上限比的是「你实际会付的价」：已确认→收盘价、未确认→触发价，与数量/保护价同一口径
    bound_violated = price_cap is not None and entry_est_price > price_cap + 1e-9
    # 只有「本来要下单、却被上限拦住」才算价格阻止；watching/expired 只是提示，不能伪装成已取消
    blocked_by_price = bound_violated and status == 'signal_confirmed'
    if blocked_by_price:
        status = 'blocked'
        reason_codes.append({'code': 'price_bound_exceeded',
                             'text': f'估算入场价 {entry_est_price:.2f} 已超过可接受上限 {price_cap:.2f}'})

    # ---------------- 数量与费用 ----------------
    # 预算口径与回测一致：默认用**全部可用现金**（回放账本可能已盈利，不能拿初始本金封顶）；
    # 只有当用户显式设置 max_position_pct < 100 时才按比例压缩（校验已在 validated_rules 完成）。
    budget = float(acct['cash']) * rules['max_position_pct'] / 100.0
    qty, amount, fee = order_size(budget, entry_est_price, cost)
    # eligibility 全部检查完毕后才算「执行数量」：不打算买（含被价格上限阻止）→ 数量必须为 0。
    # 「这笔钱本来能买多少」另放情景表的 cash_power_shares，不冒充下单数量（D02）。
    if status in ('holding', 'blocked', 'data_unavailable'):
        # 不打算买的时候不能显示「可买 9800 股」——那是自相矛盾的输出。
        qty, amount, fee = 0, 0.0, 0.0
    binding = None
    if blocked_by_price:
        binding = 'price_bound'          # 数量为 0 的原因是价格上限，不是现金不足
    elif qty > 0 and rules['max_position_pct'] < 100.0:
        binding = 'max_position'
    elif qty == 0 and status == 'signal_confirmed':
        binding = 'insufficient_cash'
    # 其余「本来就不买」的状态（holding / blocked / data_unavailable）保持 None：
    # 归因为「现金不足」会误导用户去充值，而真正原因是持仓或数据/模型不可交易。

    # ---------------- 风险预算（D03/D04） ----------------
    risk = rules['risk_pct']
    sl_pct = float(p.get('sl') or 0)
    risk_info = None
    risk_cap = None                      # (risk_amount, sl_pct)：情景表也要套同一约束
    if risk is not None:
        risk_amount = capital * risk / 100.0
        if sl_pct > 0:
            risk_cap = (risk_amount, sl_pct)
            # 单股风险同样按估算入场价算——否则「按 8.26 算风险、按 9.96 下单」又是一笔糊涂账
            per_share = entry_est_price * sl_pct / 100.0
            qty_by_risk = int(risk_amount / per_share // SIZE_TICK) * SIZE_TICK if per_share > 0 else 0
            qty_before_risk = qty
            if qty > 0 and qty_by_risk < qty:
                qty = qty_by_risk
                amount = entry_est_price * qty
                fee = cost.buy_fee(amount) if qty else 0.0
                binding = 'risk_budget'
            if qty > 0 or (qty_before_risk > 0 and qty == 0):
                # 即使风险预算把数量压到 0 也要留下解释：否则面板只显示「0 股」没有原因
                risk_info = {
                    'risk_pct': float(risk), 'risk_amount': round(risk_amount, 2),
                    'per_share_risk': round(per_share, 4),
                    'qty_cap': qty_by_risk,
                    'note': ('按止损幅度估算的风险预算；跳空/停牌/跌停无法卖出时实际亏损可能超出预算'
                             if qty > 0 else '风险预算按止损幅度折算不足一手，本次不买入'),
                }
        else:
            # 用户设了风险预算但策略没有止损幅度：说不清楚就不装作生效
            risk_info = {
                'risk_pct': float(risk), 'risk_amount': round(risk_amount, 2),
                'note': '该参数组合未设置止损幅度（sl=0），风险预算无法折算数量，本次未生效',
            }

    # 零数量的原因必须归因正确（D03）：风险预算折不出一手 ≠ 现金不足
    if qty <= 0 and status == 'signal_confirmed':
        status = 'blocked'
        if binding == 'risk_budget' and risk_cap is not None:
            ra, _ = risk_cap
            ps = entry_est_price * sl_pct / 100.0
            reason_codes.append({'code': 'risk_budget_below_one_lot',
                                 'text': f'风险预算 {ra:.2f} 元（risk_pct={risk:g}%）按止损 {sl_pct:g}% '
                                         f'折算不足一手（每股风险 {ps:.4f} 元 × {SIZE_TICK} 股 > 预算）'})
        else:
            reason_codes.append({'code': 'insufficient_cash',
                                 'text': f'可用资金 {acct["cash"]:.2f} 不足一手（估算入场价 {entry_est_price:.2f}，'
                                         f'含费用需 {entry_est_price * SIZE_TICK + cost.buy_fee(entry_est_price * SIZE_TICK):.2f}）'})

    # ---------------- 执行段 ----------------
    lifecycle = {
        'signal_confirmed': 'planned',
        'holding': 'not_applicable',
        'watching': 'watching',
        'blocked': 'cancelled' if blocked_by_price else 'blocked',
        'expired': 'expired',
        'data_unavailable': 'unknown',
    }.get(status, 'unknown')

    # ---------------- 执行时点（F01：开盘前 / 盘中 / 收盘后不是同一个结论） ----------------
    exec_anchor = earliest if confirmed else check_session
    timing = (cal.session_timing(exec_anchor, now_dt) if exec_anchor else
              {'date': None, 'phase': 'unknown', 'known': False,
               'now': now_dt.isoformat(timespec='seconds'), 'timezone': latest_info.get('timezone')})
    phase = timing['phase']
    if status in ('data_unavailable', 'holding'):
        exec_window = 'n/a'
    elif stale and not research:
        exec_window = 'closed'
    elif phase == 'after_close':
        exec_window = 'passed'
    elif phase in ('pre_open', 'in_session'):
        exec_window = 'open'
    else:
        exec_window = 'unknown'

    pending = [
        '开盘价未确定：数量按参考价估算，开盘后须按实际成交价重算',
        '开盘涨停 / 跌停封板 / 停牌将不成交',
        '实际可用资金与可卖数量以券商为准',
    ]
    if phase == 'in_session':
        pending.append('执行日已开盘：集合竞价已过，数量须按现价重算（本计划不追价）')
    if exec_window == 'passed':
        pending.append('执行日已收盘：本次开盘机会已过，请按最新行情重新生成')
    if not session_confirmed:
        pending.append('执行日未获交易所日历确认（按工作日估算，需按休市公告核对）')
    if rules.get('day_only', True):
        pending.append('仅当日有效：未成交则本计划作废，需重新生成')
    if acct['mode'] == 'manual_actual':
        pending.append('手动账户为快照，需与实际券商持仓一致')

    # ---------------- 价格情景（收盘 vs 估算入场） ----------------
    # 用户看到的是一张计划，所以两种价格下的差别必须摆出来：
    # 该价能买多少股、买「计划数量」需要多少钱、会不会超预算。
    # D04：情景表不能只按「纯现金购买力」算——风险预算若缩减了主计划数量，
    # 情景表的「采用」行必须与主计划同数量，否则又是两张互不相容的账。
    # 因此每个情景都套用与主计划相同的约束（仓位预算 + 风险预算），
    # 纯现金购买力另存 cash_power_shares，供用户理解「不设风险约束能买多少」。
    def _scenario(key: str, label: str, price: float) -> dict:
        cash_qty, _, _ = order_size(budget, price, cost)          # 纯现金购买力
        s_qty = cash_qty
        if risk_cap is not None and s_qty > 0:                    # 应用风险预算（与主计划同一规则）
            ra, slp = risk_cap
            per_share_s = price * slp / 100.0
            if per_share_s > 0:
                s_qty = min(s_qty, int(ra / per_share_s // SIZE_TICK) * SIZE_TICK)
        if status in ('holding', 'blocked', 'data_unavailable'):
            s_qty = 0                                             # 已阻止：下单数量为 0
        s_amount = price * s_qty
        s_fee = cost.buy_fee(s_amount) if s_qty else 0.0
        plan_cost = qty * price
        plan_fee = cost.buy_fee(plan_cost) if qty else 0.0
        return {
            'key': key, 'label': label, 'price': round(price, 2),
            'shares': s_qty, 'lots': s_qty // SIZE_TICK,
            'cash_power_shares': cash_qty,       # 纯现金购买力（不含风险预算，不是下单数量）
            'amount': round(s_amount, 2), 'fees': round(s_fee, 2),
            'cash_after': (round(float(acct['cash']) - s_amount - s_fee, 2) if s_qty
                           else round(float(acct['cash']), 2)),
            'plan_qty_cost': round(plan_cost + plan_fee, 2),
            'plan_qty_over_budget': bool(qty) and (plan_cost + plan_fee) > budget + 1e-6,
            'used': abs(round(price, 2) - round(entry_est_price, 2)) < 1e-9,
        }

    scenarios = [_scenario('close', f'按 as_of 收盘价 {ref_price:.2f}', ref_price)]
    if abs(entry_est_price - ref_price) > 1e-9:
        scenarios.append(_scenario('estimate', f'按估算入场价 {entry_est_price:.2f}', entry_est_price))

    execution = {
        'as_of': as_of,
        'signal_status': signal['status'],
        'now': now_dt.isoformat(timespec='seconds'),
        'timezone': timing.get('timezone'),
        'phase': phase,                       # pre_open / in_session / after_close / non_session / unknown
        'phase_text': PHASE_TEXT.get(phase, phase),
        'window': exec_window,                # open / passed / closed / n/a / unknown
        'window_text': WINDOW_TEXT.get(exec_window, ''),
        # 研究模式 + 行情落后 = 回放历史情景：执行日/有效期都是过去时，
        # 前端据此把「下一交易日执行」改成「历史情景」，不冒充当前待执行订单。
        'historical': bool(stale and research),
        'confirmation_time': f'{as_of} 15:00 收盘后' if confirmed else None,
        'confirmation_basis': '已完成日线收盘价（未完成日线不得确认）',
        'next_check_session': check_session if not confirmed else None,
        'next_check_confirmed': bool((next_info or {}).get('confirmed')),
        'earliest_execution_session': earliest,
        'execution_session_confirmed': session_confirmed,
        'execution_session_source': exec_info.get('source'),
        'mode': 'next_open' if confirmed else 'awaiting_confirmation',
        'eligibility': ('n/a' if status in ('holding', 'data_unavailable', 'expired', 'watching')
                        else ('fail' if status == 'blocked' else ('pending' if not session_confirmed else 'pass'))),
        'pending_checks': pending,
        'price_bounds': {
            'max_price': round(float(max_price), 2) if max_price is not None else None,
            'deviation_cap': round(dev_cap, 2) if dev_cap is not None else None,
            'effective_cap': round(price_cap, 2) if price_cap is not None else None,
            'reference_price': round(ref_price, 2),
            'violated': bound_violated,
            'day_only': bool(rules.get('day_only', True)),
            'on_violation': '取消本次下单（不追价）；未成交不顺延',
        },
        'quantity_estimate': {
            'shares': qty, 'lots': qty // SIZE_TICK,
            'reference_price': round(ref_price, 2),
            'entry_price_estimate': round(entry_est_price, 2),
            'entry_price_basis': entry_basis,
            'price_basis': ENTRY_BASIS_TEXT.get(entry_basis, entry_basis),
            'estimated': True,
            'hypothetical': status == 'watching',
            'amount': round(amount, 2),
            'estimated_fees': round(fee, 2),
            'cash_after': round(float(acct['cash']) - amount - fee, 2) if qty else round(float(acct['cash']), 2),
            'budget': round(budget, 2),
            'binding_constraint': binding,
            'risk': risk_info,
            'scenarios': scenarios,
            'note': ('估算入场价是**预算假设价**，不是成交价：开盘可能高于或低于它。'
                     '数量、费用、风险与保护价都按这同一个价格计算，成交后须按实际成交价重算'),
        },
        'trigger': trigger,
        'lifecycle': lifecycle,
        'lifecycle_reason': [r['code'] for r in reason_codes],
    }

    # ---------------- 保护价 ----------------
    protection = {
        'basis': 'confirmed_fill' if acct['holding'] else 'estimated_from_reference',
        'reference_price': round(ref_price, 2),
        'tp_pct': p.get('tp') if model.uses_tp_sl else None,
        'sl_pct': p.get('sl') if model.uses_tp_sl else None,
        'tp_price': None, 'sl_price': None,
        'min_hold': p.get('min_hold'),
        'max_hold': p.get('max_hold'),
        'exit_rules': (['止盈', '止损', '到期'] if model.uses_tp_sl else
                       (['信号失效'] if model.exit_on_signal else [])),
        'confirmed_fill_price': None,
        'note': ('止盈/止损按与数量、费用同一个估算入场价计算，成交后应按实际成交价重算'
                 if not acct['holding'] else '按持仓成本计算，实际成交价以上次成交为准'),
    }
    if model.uses_tp_sl and p.get('tp') and p.get('sl'):
        if acct['holding']:
            ep = acct.get('cost_price')
            if ep:
                protection.update(
                    confirmed_fill_price=round(float(ep), 4),
                    tp_price=round(float(ep) * (1 + p['tp'] / 100.0), 2),
                    sl_price=round(float(ep) * (1 - p['sl'] / 100.0), 2),
                )
                protection['basis'] = 'confirmed_fill'
        else:
            # 尚未成交：按「估算入场价」估算——与数量/费用/风险同一个价格（第九轮统一口径）。
            # 成交后必须按实际成交价重算（BUY-09）。
            protection.update(
                basis=('estimated_from_reference' if entry_basis == 'as_of_close'
                       else 'estimated_from_trigger'),
                estimated_entry_price=round(entry_est_price, 2),
                entry_price_basis=entry_basis,
                tp_price=round(entry_est_price * (1 + p['tp'] / 100.0), 2),
                sl_price=round(entry_est_price * (1 - p['sl'] / 100.0), 2),
            )
        # 最小持仓到期日（F03）：已持仓必须从**真实入场日**算。
        # 旧实现无条件用「下一笔买入日」当起点，手动实际账户早已持满也会显示一个未来到期日，
        # 等于把旧持仓当成今天刚买——用户会以为还不能卖。
        min_hold = p.get('min_hold')
        if min_hold:
            mh = int(min_hold)
            entry = acct.get('entry_date')
            entry_ok = False
            if entry:
                try:
                    to_date(entry)
                    entry_ok = True
                except (TypeError, ValueError):
                    entry_ok = False
            if acct['holding'] and entry_ok:
                until, ok = _next_n_sessions(cal, entry, mh)
                elapsed, _ = cal.sessions_between(entry, as_of)
                satisfied = len(elapsed) >= mh
                protection.update(
                    min_hold_basis='entry_date',
                    min_hold_entry_date=iso(entry),
                    min_hold_until=until,
                    min_hold_until_confirmed=ok,
                    min_hold_elapsed_sessions=len(elapsed),
                    min_hold_satisfied=satisfied,
                    min_hold_note=(f'已满足最小持仓：自 {iso(entry)} 起已 {len(elapsed)} 个交易日'
                                   if satisfied else
                                   f'自 {iso(entry)} 起已持有 {len(elapsed)} 个交易日，满 {mh} 个交易日后可卖出'),
                )
            elif acct['holding']:
                protection.update(
                    min_hold_basis='unknown_entry_date',
                    min_hold_entry_date=None, min_hold_until=None,
                    min_hold_until_confirmed=False, min_hold_elapsed_sessions=None,
                    min_hold_satisfied=None,
                    min_hold_note='账户显示已持仓，但没有可用的入场日期：不能用「下一笔买入日」代替旧持仓日期，'
                                  '请补录入场日期后重算',
                )
            elif earliest:
                until, ok = _next_n_sessions(cal, earliest, mh)
                protection.update(
                    min_hold_basis='hypothetical_entry',
                    min_hold_entry_date=earliest,
                    min_hold_until=until,
                    min_hold_until_confirmed=ok,
                    min_hold_elapsed_sessions=None,
                    min_hold_satisfied=False,
                    min_hold_note=f'假设在 {earliest} 买入，需持满 {mh} 个交易日',
                )

    # ---------------- 有效期 / 身份 ----------------
    valid_until = earliest if confirmed else check_session
    data_state = 'research' if (stale and research) else ('stale' if stale else 'live')
    # F02：指纹必须覆盖**所有会改变结论的输入**。旧实现只挑 6 个账户字段，
    # allow_addon=false（holding）与 true（blocked）算出了同一个 plan_id，
    # 落库去重后就会出现「状态是 blocked、快照里是 holding」的矛盾记录。
    # 这里改为：整份账户快照 + 整份规范化规则 + 研究模式 + 数据状态 + 引擎版本。
    identity_payload = {
        'plan_version': PLAN_VERSION,
        'engine_version': ENGINE_VERSION,
        'dataset_id': dataset.get('id'),
        'dataset_hash': dataset.get('data_hash') or '',
        'code': dataset.get('code'),
        'model_id': model.id,
        'effective_params': p,
        'config_hash': config_hash,
        'account': {k: acct[k] for k in sorted(acct)},
        'as_of': as_of,
        'rules': {k: rules[k] for k in sorted(rules)},
        'research': bool(research),
        'data_state': data_state,
    }
    plan_id = _plan_id(identity_payload)
    validity = {
        'plan_id': plan_id,
        'generated_at': _now_iso(),
        'as_of': as_of,
        'data_hash': dataset.get('data_hash') or '',
        'data_state': data_state,
        'latest_completed_session': latest_done,
        'valid_until_session': valid_until,
        'expired': bool(stale and not research) or status == 'expired',
        'expired_reasons': ([{'code': 'stale_data',
                              'text': f'行情截至 {as_of}，最新已完成日线为 {latest_done}，'
                                      f'缺少 {len(sessions_behind)} 个交易日'}]
                            if (stale and not research) else []),
        'invalidated_by': ['新交易日线到达', '策略参数修改', '账户快照变化', '数据集内容变化'],
        'research_mode': bool(research),
        'mode': 'live' if not stale else ('research' if research else 'expired'),
    }

    # ---------------- 兼容字段（旧面板/旧脚本） ----------------
    legacy_holding = bool(acct['holding'])
    plan = {
        'as_of_date': as_of,
        'tradable': bool(model.tradable),
        'holding': legacy_holding,
        'entry_type': 'custom_rule' if p.get('rule') else p.get('entry', 'ma_cross'),
        'entry_condition': signal['entry_condition'],
        'threshold_price': trigger.get('threshold_price'),
        'last_close': round(ref_price, 2),
        'buy_hold': model.id == 'M1',
        'position': {k: acct.get(k) for k in
                     ('cash', 'shares', 'market_value', 'equity_value', 'equity_ratio')},
        # 新契约
        'version': PLAN_VERSION,
        'status': status,
        'status_text': (f'历史情景（仅供复盘）· {STATUS_TEXT.get(status, status)}'
                        if (stale and research) else STATUS_TEXT.get(status, status)),
        'reason_codes': reason_codes,
        'identity': {
            'plan_id': plan_id,
            'strategy_version': PLAN_VERSION,
            'engine_version': ENGINE_VERSION,
            'model_id': model.id,
            'model_name': model.name,
            'effective_params': p,
            'config_hash': config_hash,
            'dataset_id': dataset.get('id'),
            'dataset_hash': dataset.get('data_hash') or '',
            'code': dataset.get('code'),
            'data_state': data_state,
        },
        'context': {'as_of': as_of, 'data_status': data_status,
                    'account_mode': acct['mode'], 'account_snapshot': acct,
                    # 规范化后的执行约束（不是原始请求）：落库与续期都读这份，
                    # 避免「第一次没传 max_position_pct、第二次传了 100」被当成两套输入。
                    'exec_rules': dict(rules)},
        'signal': signal,
        'execution': execution,
        'protection': protection,
        'validity': validity,
        'evidence': evidence or {'status': 'not_checked', 'records': [],
                                 'note': '未查询验证记录'},
    }

    if model.id == 'M1':
        plan['threshold_price'] = None
        if not legacy_holding:
            plan['note'] = '当前尚未成交（现金不足一手或当日不可买入），后续可买日仍会建仓。'
    if sig_err:
        plan['note'] = f'信号求值失败：{sig_err}'
    return plan


def _empty_plan(model, rules, dataset, code, text) -> dict:
    return {
        'as_of_date': None, 'tradable': bool(model.tradable), 'holding': None,
        'entry_type': None, 'entry_condition': text, 'threshold_price': None,
        'last_close': None, 'status': 'data_unavailable', 'status_text': STATUS_TEXT['data_unavailable'],
        'reason_codes': [{'code': code, 'text': text}],
        'signal': {'status': 'unknown', 'condition_tree': None, 'missing_conditions': []},
        'execution': {'earliest_execution_session': None, 'eligibility': 'n/a',
                      'pending_checks': ['缺少行情数据'], 'mode': 'unknown'},
        'protection': {}, 'validity': {'expired': True, 'expired_reasons': [{'code': code, 'text': text}]},
        'evidence': {'status': 'not_checked', 'records': []},
        'identity': {'model_id': model.id, 'model_name': model.name},
        'context': {'account_mode': (rules or {}).get('mode')},
    }


# ---------------- 兼容旧接口 ----------------
def entry_threshold(bars, p: dict):
    """旧接口：返回下一根触发所需的最低收盘价（不可单值表达时 None）。"""
    from .models.base import Model as _M
    from .plan_conditions import build_trigger
    class _Shim(_M):
        id = 'M4'
        def target(self, b, q):
            from .signals import entry_signal
            return entry_signal(b, q)
    t = build_trigger(bars, _Shim(), p)
    return t.get('threshold_price')
