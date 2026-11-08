import { useState } from 'react'
import { useStore } from '../store'
import type { PlanCondition, PlanInterval, PlanTrigger } from '../types'
import PlanTracking from './PlanTracking'

const f2 = (n?: number | null, d = 2) =>
  n == null || Number.isNaN(n) ? '—' : n.toFixed(d)
const money = (n?: number | null) =>
  n == null ? '—' : n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })

const STATUS_TONE: Record<string, string> = {
  signal_confirmed: 'ok',
  watching: 'wait',
  holding: 'hold',
  blocked: 'bad',
  expired: 'bad',
  data_unavailable: 'bad',
}

const ELIG_LABEL: Record<string, string> = {
  pass: '已知约束通过（仍有盘中检查项）',
  pending: '待确认（关键信息未确定）',
  fail: '不可执行',
  'n/a': '不适用',
}

const CLASS_LABEL: Record<string, string> = {
  single_threshold: '单一价格阈值',
  interval: '价格区间',
  multi_interval: '多区间（不可压成单点）',
  conditional_on_volume: '价格 + 成交量',
  unsatisfiable: '当前条件下不可能触发',
  outside_search_range: '扫描范围外（不把边界当阈值）',
  insufficient_history: '历史不足',
  invalid_rule: '规则非法',
  not_applicable: '无触发价概念',
}

const SESSION_SOURCE: Record<string, string> = {
  dataset: '数据集真实交易日',
  holidays: '交易所休市安排',
  weekday_estimate: '工作日估算（未确认）',
  unknown: '无法判断',
}

function Section({ title, right, children }: { title: string; right?: any; children: any }) {
  return (
    <section className="p-sec">
      <div className="p-sec-head">
        <span>{title}</span>
        {right}
      </div>
      {children}
    </section>
  )
}

function KV({ label, value, hint, tone }: { label: string; value: any; hint?: string; tone?: string }) {
  return (
    <div className="p-kv" data-kv={label}>
      <span className="pl">{label}</span>
      <span className={`pv ${tone || ''}`}>{value}</span>
      {hint && <span className="ph">{hint}</span>}
    </div>
  )
}

function CondTree({ node, depth = 0 }: { node: PlanCondition; depth?: number }) {
  if (node.$type === 'group') {
    if (!node.children?.length) {
      return <div className="p-cond-note">{node.text}</div>
    }
    return (
      <div className="p-cond-group" style={{ marginLeft: depth ? 12 : 0 }}>
        {depth > 0 && (
          <div className="p-cond-op">
            {node.op === 'any' ? '任一满足 (OR)' : '同时满足 (AND)'}
          </div>
        )}
        {node.children.map((c, i) => <CondTree key={i} node={c} depth={depth + 1} />)}
      </div>
    )
  }
  const unknown = !!node.unknown
  const state = unknown ? 'unk' : node.satisfied ? 'ok' : 'no'
  return (
    <div className="p-cond-row">
      <span className="cl">{node.left?.label}</span>
      <span className="cv">{node.left?.value == null ? '—' : f2(node.left.value, 2)}</span>
      <span className="cc">{node.cmp_label}</span>
      <span className="cr">{node.right?.label}</span>
      {node.right?.value != null && <span className="cv">{f2(node.right.value, 2)}</span>}
      <span className={`cst ${state}`}>{unknown ? '未知' : node.satisfied ? '满足' : '不满足'}</span>
    </div>
  )
}

function intervalText(iv: PlanInterval): string {
  if (iv.lo == null && iv.hi == null) return '—'
  if (iv.lo != null && iv.hi == null) return `> ${f2(iv.lo)}`
  if (iv.lo == null && iv.hi != null) return `≤ ${f2(iv.hi)}`
  return `(${f2(iv.lo)}, ${f2(iv.hi)}]`
}

function TriggerBox({ t }: { t: PlanTrigger }) {
  const cls = t.classification
  let headline: any = null
  if (cls === 'single_threshold' && t.threshold_price != null) {
    headline = <span className="p-trig-price">下一根<b>收盘价 &gt; {f2(t.threshold_price)}</b></span>
  } else if (cls === 'interval' && t.intervals[0]) {
    headline = <span className="p-trig-price">下一根<b>收盘价 ∈ {intervalText(t.intervals[0])}</b></span>
  } else if (cls === 'multi_interval') {
    headline = <span className="p-trig-price">触发区间（{t.intervals.length} 段）：{' '}
      {t.intervals.map(x => intervalText(x)).join(' 或 ')}</span>
  } else if (cls === 'conditional_on_volume') {
    headline = <span className="p-trig-price">必要条件：收盘价 &gt; {f2(t.threshold_price)}
      <em className="p-tag-unk">成交量未知，不构成已触发</em></span>
  } else if (cls === 'unsatisfiable') {
    headline = <span className="p-trig-none">当前参数下，下一根不可能同时满足所有条件</span>
  } else if (cls === 'outside_search_range') {
    headline = <span className="p-trig-none">无法给出可靠阈值（扫描范围内未找到边界）</span>
  } else if (cls === 'insufficient_history') {
    headline = <span className="p-trig-none">历史不足，无法求触发价</span>
  } else if (cls === 'not_applicable') {
    headline = <span className="p-trig-none">该模型没有「触发价」概念</span>
  } else {
    headline = <span className="p-trig-none">无法可靠表达（{cls}）</span>
  }
  return (
    <div className="p-trig">
      <div className="p-trig-head">
        <span className={`p-tag ${cls === 'single_threshold' || cls === 'conditional_on_volume' ? 'ok' : 'warn'}`}>
          {CLASS_LABEL[cls] || cls}
        </span>
        {headline}
      </div>
      {t.requirements.length > 0 && (
        <div className="p-req">
          {t.requirements.map((r, i) => (
            <div key={i} className="p-req-row">
              <span>{r.text}</span>
              {r.value != null && <b>{r.cmp === 'gt' ? '>' : r.cmp === 'lte' ? '≤' : ''} {money(r.value)}</b>}
            </div>
          ))}
        </div>
      )}
      {t.unknown_components.length > 0 && (
        <div className="p-unk">未知量：{t.unknown_components.join('、')} —— 不视为满足</div>
      )}
      {t.notes.map((n, i) => <div key={i} className="p-note-line">· {n}</div>)}
    </div>
  )
}

export default function PlanPanel() {
  const plan = useStore(s => s.plan)
  const stale = useStore(s => s.planStale)
  const restored = useStore(s => s.planRestored)
  const savedId = useStore(s => s.planSavedId)
  const loading = useStore(s => s.loading)
  const runPlan = useStore(s => s.runPlan)
  const activeId = useStore(s => s.activeId)
  const models = useStore(s => s.models)
  const account = useStore(s => s.planAccount)
  const rules = useStore(s => s.planRules)
  const research = useStore(s => s.planResearch)
  const setAccount = useStore(s => s.setPlanAccount)
  const setRules = useStore(s => s.setPlanRules)
  const setResearch = useStore(s => s.setPlanResearch)
  const [adv, setAdv] = useState(false)

  const mname = models.find(m => m.id === activeId)?.name || activeId
  const num = (v: string) => (v.trim() === '' ? null : Number(v))
  const q = plan?.execution.quantity_estimate
  const hist = !!plan?.execution.historical
  const scn = q?.scenarios || []
  const estPx = q?.entry_price_estimate
  const priceDiffers = estPx != null && q != null && Math.abs(estPx - q.reference_price) > 1e-9
  const BINDING_LABEL: Record<string, string> = {
    risk_budget: '受风险预算约束',
    max_position: '受最大仓位约束',
    price_bound: '受买价上限约束（本计划不下单）',
    insufficient_cash: '可用现金不足一手',
  }

  return (
    <div className="plan-panel">
      <div className="plan-head">
        <div>
          <div className="panel-title">下一笔交易计划 · {mname}</div>
          <div className="p-sub">收盘确认 → 下一交易日开盘执行；未知项一律标注，不冒充可成交</div>
        </div>
        <button className="primary" disabled={loading} onClick={() => runPlan()}>
          生成计划
        </button>
      </div>

      {/* 账户与执行约束 */}
      <div className="p-bar">
        <div className="p-seg">
          <button className={account.mode === 'strategy_simulated' ? 'on' : ''}
            onClick={() => setAccount({ mode: 'strategy_simulated' })}>策略模拟账户</button>
          <button className={account.mode === 'manual_actual' ? 'on' : ''}
            onClick={() => setAccount({ mode: 'manual_actual' })}>我的实际账户</button>
        </div>
        {account.mode === 'manual_actual' && (
          <div className="p-acct">
            <label>现金<input type="number" value={account.cash ?? ''} placeholder="可用现金"
              onChange={e => setAccount({ cash: num(e.target.value) })} /></label>
            <label>持仓<input type="number" value={account.shares ?? ''} placeholder="股"
              onChange={e => setAccount({ shares: num(e.target.value) })} /></label>
            <label>可卖<input type="number" value={account.sellable ?? ''} placeholder="默认=持仓"
              onChange={e => setAccount({ sellable: num(e.target.value) })} /></label>
            <label>成本<input type="number" value={account.cost_price ?? ''} placeholder="元"
              onChange={e => setAccount({ cost_price: num(e.target.value) })} /></label>
            <label>快照日<input type="date" value={account.snapshot_at ?? ''}
              onChange={e => setAccount({ snapshot_at: e.target.value || null })} /></label>
          </div>
        )}
        <div className="p-bar-right">
          <label className="chk"><input type="checkbox" checked={research}
            onChange={e => setResearch(e.target.checked)} />研究模式</label>
          <button className="ghost" onClick={() => setAdv(v => !v)}>{adv ? '收起约束' : '执行约束'}</button>
        </div>
      </div>

      {adv && (
        <div className="p-adv">
          <label>最大仓位%<input type="number" value={rules.max_position_pct}
            onChange={e => {
              // 0 不能被静默改成 100：原样传给后端，由后端 422 说明「须在 (0,100]」（D01）
              const v = Number(e.target.value)
              setRules({ max_position_pct: Number.isFinite(v) ? v : 0 })
            }} /></label>
          <label>单笔风险%<input type="number" value={rules.risk_pct ?? ''}
            onChange={e => setRules({ risk_pct: num(e.target.value) })} /></label>
          <label>最大买价<input type="number" value={rules.max_price ?? ''}
            onChange={e => setRules({ max_price: num(e.target.value) })} /></label>
          <label>偏离上限%<input type="number" value={rules.max_deviation_pct ?? ''}
            onChange={e => setRules({ max_deviation_pct: num(e.target.value) })} /></label>
          <label className="chk"><input type="checkbox" checked={rules.day_only}
            onChange={e => setRules({ day_only: e.target.checked })} />仅当日有效</label>
          <label className="chk"><input type="checkbox" checked={rules.follow_from === 'new_signal_only'}
            onChange={e => setRules({ follow_from: e.target.checked ? 'new_signal_only' : 'allow_existing' })} />
            只等新信号</label>
          <div className="p-note-line" style={{ flexBasis: '100%' }}>
            · 部分成交：不支持自动拆分。开盘只成交一部分时，请按实际数量在跟踪里补录多笔，
            工具不会把「部分成交」当成已按计划成交，也不会替你补足剩余数量。
          </div>
          {(rules.max_position_pct <= 0 || rules.max_position_pct > 100) && (
            <div className="p-note-line down" style={{ flexBasis: '100%' }}>
              · 最大仓位须在 (0, 100] 内：0 表示「输入有误」，不会被当作 100% 满仓，请修正后再生成计划。
            </div>
          )}
        </div>
      )}

      {!plan && (
        <div className="plan-empty">
          选择账户模式后点击「生成计划」：将给出信号是否已确认、下一步何时检查/执行、需要多少钱，
          以及哪些条件仍然未知。不含任何未来数据。
        </div>
      )}

      {plan && (
        <div className="plan-body">
          {restored && (
            <div className="p-restored">
              已从跟踪记录 #{savedId} 恢复上次保存的计划（保存于
              {' '}{plan.validity.generated_at.slice(0, 16).replace('T', ' ')}）。
              面板上的参数/账户可能已变，点「生成计划」按当前设置重算；恢复不会改写原快照。
            </div>
          )}
          {stale && <div className="p-stale">⚠ 该计划已失效：{stale}。请重新生成，勿据此下单。</div>}
          {plan.context.data_status.stale && plan.validity.research_mode && (
            <div className="p-stale">
              研究模式：行情截至 {plan.context.as_of}，落后 {plan.context.data_status.sessions_behind} 个交易日。
              以下结论仅供研究复盘，不可作为当前待执行计划。
            </div>
          )}

          <div className={`p-status ${STATUS_TONE[plan.status] || ''}`}
            data-plan-status={plan.status} data-historical={hist ? '1' : '0'}>
            <span className="p-badge">{plan.status_text}</span>
            <span className="p-reasons">
              {plan.reason_codes.length === 0 ? '无' : plan.reason_codes.map(r => r.text).join('；')}
            </span>
          </div>

          <div className="p-cols">
            <Section title="时序与执行">
              <KV label="截至" value={plan.context.as_of}
                hint={plan.context.data_status.stale
                  ? `落后 ${plan.context.data_status.sessions_behind} 个交易日（最新已完成 ${plan.context.data_status.latest_completed_session || '未知'}）`
                  : `数据最新（${plan.context.data_status.bar_count} 根已完成日线）`} />
              <KV label="当前时点"
                value={`${(plan.execution.now || '').slice(0, 16).replace('T', ' ')} ${plan.execution.phase_text || ''}`.trim()}
                hint={plan.execution.window_text
                  || `最新已完成日线 ${plan.context.data_status.latest_completed_session || '未知'}`} />
              <KV label="信号" tone={plan.signal.status === 'confirmed' ? 'up' : ''}
                value={plan.signal.status === 'confirmed' ? '已确认' : plan.signal.status === 'not_confirmed' ? '未确认' : '未知'}
                hint={plan.signal.confirmed_at || ''} />
              <KV label="入场条件" value={plan.signal.entry_condition} />
              {plan.execution.mode === 'next_open' ? (
                <KV label={hist ? '历史执行日' : '最早执行'}
                  value={`${plan.execution.earliest_execution_session || '—'} 开盘`}
                  hint={hist ? '历史情景：这一天早已过去，不是当前委托'
                    : (SESSION_SOURCE[plan.execution.execution_session_source || ''] || '')} />
              ) : (
                <KV label={hist ? '历史检查日' : '下一步'}
                  value={`${plan.execution.next_check_session || '—'} 收盘再检查`}
                  hint={hist ? '历史情景：按当时行情回放'
                    : (plan.execution.earliest_execution_session
                      ? `最早 ${plan.execution.earliest_execution_session} 执行` : '')} />
              )}
              <KV label="确认口径" value={plan.execution.confirmation_basis} />
              <KV label="执行资格" value={ELIG_LABEL[plan.execution.eligibility] || plan.execution.eligibility}
                tone={plan.execution.eligibility === 'pass' ? 'up' : plan.execution.eligibility === 'fail' ? 'down' : ''} />
              <KV label="账户" value={account.mode === 'manual_actual' ? '实际账户（手动快照）' : '策略模拟账户'}
                hint={`现金 ${money(plan.context.account_snapshot.cash)} · 持仓 ${plan.context.account_snapshot.shares} 股`} />
            </Section>

            <Section title="数量与费用">
              <KV label="as_of 收盘" value={f2(q?.reference_price)}
                hint="对照基准，不是下单价" />
              {priceDiffers && (
                <KV label="估算入场价" value={f2(estPx)} tone="up"
                  hint={q?.price_basis || '数量/费用/保护价共用此价格'} />
              )}
              <KV label="预计数量" tone="up"
                value={`${money(q?.shares)} 股（${q?.lots} 手）`}
                hint={q?.hypothetical ? '假设触发后，按预算假设价估算' : '按预算假设价估算'} />
              <KV label="预计金额" value={money(q?.amount)} />
              <KV label="预计费用" value={money(q?.estimated_fees)} hint="佣金+过户费" />
              <KV label="成交后现金" value={money(q?.cash_after)}
                hint={BINDING_LABEL[q?.binding_constraint || ''] || ''} />
              <KV label="保护价" value={`止盈 ${f2(plan.protection.tp_price)} / 止损 ${f2(plan.protection.sl_price)}`}
                hint={plan.protection.basis === 'confirmed_fill'
                  ? '按持仓成本'
                  : `按预算假设价 ${f2(plan.protection.estimated_entry_price ?? estPx)} 估算（与数量同一价格）`} />
              {(plan.protection.min_hold_until || plan.protection.min_hold_basis === 'unknown_entry_date') && (
                <KV label="最小持仓至"
                  value={plan.protection.min_hold_basis === 'unknown_entry_date'
                    ? '未知（缺少入场日期）'
                    : plan.protection.min_hold_satisfied
                      ? `已满足（${plan.protection.min_hold_entry_date || ''} 起 ${plan.protection.min_hold_elapsed_sessions ?? '?'} 个交易日）`
                      : (plan.protection.min_hold_until || '—')}
                  hint={plan.protection.min_hold_note
                    || (plan.protection.min_hold_until_confirmed ? '' : '日历未确认')} />
              )}
            </Section>
          </div>

          {scn.length > 1 && (
            <Section title="价格情景（同一笔钱在两种价格下的差别）"
              right={<span className="p-mini ok">本计划按 {f2(estPx)} 估算</span>}>
              <div className="p-scn">
                <div className="p-scn-hd">
                  <span>情景</span><span>价格</span><span>纯现金可买</span><span>约束后可买</span>
                  <span>金额</span><span>按计划数量需</span><span>预算</span>
                </div>
                {scn.map(s => (
                  <div key={s.key} className={`p-scn-row ${s.used ? 'on' : ''}`}
                    data-scn={s.key} data-used={s.used ? '1' : '0'}>
                    <span>{s.label}{s.used ? '（采用）' : ''}</span>
                    <span>{f2(s.price)}</span>
                    <span className="ph">{money(s.cash_power_shares ?? s.shares)} 股</span>
                    <span>{money(s.shares)} 股</span>
                    <span>{money(s.amount)}</span>
                    <span className={s.plan_qty_over_budget ? 'warn' : ''}>{money(s.plan_qty_cost)}</span>
                    <span className={s.plan_qty_over_budget ? 'warn' : ''}>
                      {s.plan_qty_over_budget ? '超预算' : '预算内'}
                    </span>
                  </div>
                ))}
              </div>
              <div className="p-note-line">
                · 「纯现金可买」只说明这笔钱最多能买多少股；「约束后可买」才是应用最大仓位与风险预算后的计划数量，
                「采用」行与主计划一致。估算入场价是<b>预算假设价</b>，开盘可能高于或低于它，
                不构成成交价下界，成交后须按实际成交价重算。
              </div>
            </Section>
          )}

          <Section title="信号条件（as_of 收盘求值）"
            right={<span className={`p-mini ${plan.signal.missing_conditions.length ? 'no' : 'ok'}`}>
              {plan.signal.missing_conditions.length ? `缺 ${plan.signal.missing_conditions.length} 项` : '全部满足'}
            </span>}>
            {plan.signal.condition_tree
              ? <CondTree node={plan.signal.condition_tree} />
              : <div className="p-cond-note">无可用条件</div>}
            {plan.signal.missing_conditions.length > 0 && (
              <div className="p-missing">
                尚缺：{plan.signal.missing_conditions.map(m => m.text).join('；')}
              </div>
            )}
            {plan.signal.semantics === 'above_ma' && (
              <div className="p-note-line">· 引擎口径是「收盘价站上均线」的状态，不是当日上穿</div>
            )}
          </Section>

          <Section title="下一根触发条件">
            <TriggerBox t={plan.execution.trigger} />
          </Section>

          <div className="p-cols">
            <Section title="仍需盘中确认">
              <ul className="p-checks">
                {plan.execution.pending_checks.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
              {plan.execution.price_bounds.effective_cap != null && (
                <KV label="可接受上限" value={f2(plan.execution.price_bounds.effective_cap)}
                  hint={plan.execution.price_bounds.violated ? '参考价已超上限' : plan.execution.price_bounds.on_violation}
                  tone={plan.execution.price_bounds.violated ? 'down' : ''} />
              )}
            </Section>

            <Section title={hist ? '历史有效期（仅供复盘）' : '有效期与依据'}
              right={<span className={`p-mini ${hist ? 'no' : plan.validity.expired ? 'no' : 'ok'}`}>
                {hist ? '历史情景' : plan.validity.expired ? '已失效' : '有效'}
              </span>}>
              <KV label="计划编号" value={plan.validity.plan_id} hint={`生成于 ${plan.validity.generated_at.slice(0, 19)}`} />
              <KV label={hist ? '历史有效至' : '有效至'} value={plan.validity.valid_until_session || '—'}
                hint={hist ? '历史情景：该日期早已过去，不能作为当前委托依据'
                  : plan.validity.valid_until_session && plan.validity.valid_until_session < plan.context.data_status.today
                    ? '该日期已过（数据陈旧）' : '该交易日开盘前；开盘后须按当时价格重算'} />
              <KV label="策略版本" value={plan.identity.strategy_version}
                hint={`执行配置 ${plan.identity.config_hash?.slice(0, 8) || '—'}`} />
              {plan.validity.expired_reasons.map((r, i) => (
                <div key={i} className="p-note-line down">· {r.text}</div>
              ))}
              <div className="p-note-line">· 失效触发：{plan.validity.invalidated_by.join('、')}</div>
              <div className="p-note-line">· 研究模式：{plan.validity.research_mode ? '是（仅供研究）' : '否'}</div>
            </Section>
          </div>

          <Section title="同参数验证记录"
            right={<span className={`p-mini ${plan.evidence.status === 'matched' ? 'ok' : 'no'}`}>
              {plan.evidence.status === 'matched' ? `${plan.evidence.records.length} 条` : '未验证'}
            </span>}>
            {plan.evidence.status === 'matched' ? (
              <>
                <div className="p-evi">
                  <div className="p-evi-head">
                    <span>实验</span><span>时间</span><span>总收益</span><span>交易数</span><span>最大回撤</span><span>口径</span>
                  </div>
                  {plan.evidence.records.map(r => (
                    <div className="p-evi-row" key={r.experiment_id}>
                      <span>#{r.experiment_id}</span>
                      <span>{(r.created_at || '').slice(0, 16).replace('T', ' ')}</span>
                      <span>{r.metrics?.total_pct != null ? `${r.metrics.total_pct}%` : '—'}</span>
                      <span>{r.metrics?.n_trades ?? '—'}</span>
                      <span>{r.metrics?.max_drawdown_pct != null ? `${r.metrics.max_drawdown_pct}%` : '—'}</span>
                      <span className="p-tag warn">样本内</span>
                    </div>
                  ))}
                </div>
                <div className="p-note-line">· {plan.evidence.note}</div>
                {plan.evidence.oos_note && <div className="p-note-line">· {plan.evidence.oos_note}</div>}
              </>
            ) : (
              <div className="p-cond-note">{plan.evidence.note || '未找到匹配的验证记录'}</div>
            )}
          </Section>

          <div className="p-foot">
            参考价与触发价都只是<b>预算假设价</b>，不是成交价：下一交易日开盘可能高于或低于它们，
            触发价不构成成交价下界，也不承诺能成交。
            历史回测与样本内记录不代表未来收益，本工具仅供策略研究与教学，不构成投资建议。
          </div>

          <PlanTracking />
        </div>
      )}
    </div>
  )
}
