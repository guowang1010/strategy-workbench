import { useEffect, useState } from 'react'
import { useStore } from '../store'

const f2 = (n?: number | null, d = 2) =>
  n == null || Number.isNaN(n) ? '—' : n.toFixed(d)
const money = (n?: number | null) =>
  n == null ? '—' : n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const pct = (n?: number | null) => (n == null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(2)}%`)
const tone = (n?: number | null) => (n == null ? '' : n > 0 ? 'up' : n < 0 ? 'down' : '')

const LC_TEXT: Record<string, string> = {
  planned: '待成交',
  filled: '持仓中',
  closed: '已结清',
  cancelled: '已取消',
}
const LC_TONE: Record<string, string> = {
  planned: 'wait', filled: 'ok', closed: 'hold', cancelled: 'bad',
}
const OUTCOME_TONE: Record<string, string> = {
  pending: 'wait', no_action: 'hold', not_filled: 'bad',
  filled: 'ok', partial: 'ok', closed: 'hold', cancelled: 'bad',
}
const REASON_TAG: Record<string, string> = {
  price_above_cap: '价格超上限',
  unbuyable: '当日不可买',
  no_session: '执行日无行情',
  not_recorded: '未录入成交',
  plan_not_actionable: '计划本不可执行',
  expired: '已过期',
  superseded: '已续期',
}

function Row({ k, v, t, hint }: { k: string; v: any; t?: string; hint?: string }) {
  return (
    <div className="pt-kv">
      <span className="pl">{k}</span>
      <span className={`pv ${t || ''}`}>{v}</span>
      {hint && <span className="ph">{hint}</span>}
    </div>
  )
}

function FillForm({ onAdd, today }: { onAdd: (b: any) => void; today: string }) {
  const [side, setSide] = useState('buy')
  const [qty, setQty] = useState('')
  const [price, setPrice] = useState('')
  const [fees, setFees] = useState('')
  const [d, setD] = useState(today)
  const [note, setNote] = useState('')

  const submit = () => {
    if (!qty || !price || !d) return
    onAdd({
      side, qty: Number(qty), price: Number(price), fees: Number(fees || 0),
      trade_date: d, note: note || undefined,
    })
    setQty(''); setPrice(''); setFees(''); setNote('')
  }

  return (
    <div className="pt-form">
      <label>方向
        <select value={side} onChange={e => setSide(e.target.value)}>
          <option value="buy">买入</option>
          <option value="sell">卖出</option>
        </select>
      </label>
      <label>数量(股)<input type="number" value={qty} placeholder="如 5900"
        onChange={e => setQty(e.target.value)} /></label>
      <label>成交价<input type="number" value={price} step="0.01"
        onChange={e => setPrice(e.target.value)} /></label>
      <label>费用<input type="number" value={fees} placeholder="佣金+过户费+印花税"
        onChange={e => setFees(e.target.value)} /></label>
      <label>成交日<input type="date" value={d} onChange={e => setD(e.target.value)} /></label>
      <label className="grow">备注<input value={note} placeholder="如：开盘价成交 / 分批买入"
        onChange={e => setNote(e.target.value)} /></label>
      <button className="primary" onClick={submit} disabled={!qty || !price || !d}>录入成交</button>
    </div>
  )
}

export default function PlanTracking() {
  const plan = useStore(s => s.plan)
  const savedId = useStore(s => s.planSavedId)
  const records = useStore(s => s.planRecords)
  const track = useStore(s => s.planTrack)
  const loading = useStore(s => s.loading)
  const savePlan = useStore(s => s.savePlan)
  const openRecord = useStore(s => s.openRecord)
  const closeRecord = useStore(s => s.closeRecord)
  const addFill = useStore(s => s.addFill)
  const deleteFill = useStore(s => s.deleteFill)
  const cancelPlan = useStore(s => s.cancelPlan)
  const reopenPlan = useStore(s => s.reopenPlan)
  const rolloverPlan = useStore(s => s.rolloverPlan)
  const deletePlan = useStore(s => s.deletePlan)
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')

  useEffect(() => { if (savedId) setOpen(true) }, [savedId])

  const rec = track?.record
  const rv = track?.review

  return (
    <section className="p-sec pt">
      <div className="p-sec-head">
        <span>计划跟踪与复盘</span>
        <div className="pt-head-right">
          <span className="p-sub" style={{ margin: 0 }}>
            {records.length ? `${records.length} 条记录` : '尚无记录'}
          </span>
          <button className="ghost" onClick={() => setOpen(v => !v)}>{open ? '收起' : '展开'}</button>
          {plan && (
            <button className="primary" disabled={loading} onClick={() => savePlan()}>
              {savedId ? `已保存 #${savedId}（再次保存会更新）` : '保存计划并跟踪'}
            </button>
          )}
        </div>
      </div>

      {!open && (
        <div className="p-cond-note">
          保存后计划快照不再改写；实际成交只追加录入，复盘对照计划与实际，不重算历史结论。
        </div>
      )}

      {open && (
        <>
          <div className="pt-note">
            计划快照保存后不会被后来的行情或参数改写；实际成交只追加，录错可删。
            「策略对照」是同一模型同参数在后续行情的模拟结果，不是本计划的成绩。
          </div>

          {records.length === 0 && <div className="p-cond-note">还没有跟踪记录。生成计划后点「保存计划并跟踪」。</div>}

          {records.length > 0 && (
            <div className="pt-list">
              <div className="pt-row pt-hd">
                <span>#</span><span>截至</span><span>模型</span><span>计划状态</span>
                <span>跟踪</span><span>成交</span><span>保存时间</span><span />
              </div>
              {records.map(r => (
                <div key={r.id} className={`pt-row ${rec?.id === r.id ? 'on' : ''}`}
                  onClick={() => openRecord(r.id)}>
                  <span>#{r.id}</span>
                  <span>{r.as_of}</span>
                  <span>{r.model_id}</span>
                  <span>{r.status === 'signal_confirmed' ? '信号已确认' : r.status === 'watching' ? '等待确认' : r.status}</span>
                  <span className={`p-tag ${LC_TONE[r.lifecycle] || ''}`}>
                    {LC_TEXT[r.lifecycle] || r.lifecycle}
                    {r.superseded_by ? ` →#${r.superseded_by}` : ''}
                  </span>
                  <span>{r.fills_count} 笔</span>
                  <span>{(r.created_at || '').slice(0, 16).replace('T', ' ')}</span>
                  <span>
                    <button className="pt-del" disabled={loading}
                      onClick={e => { e.stopPropagation(); if (confirm(`删除记录 #${r.id}？成交记录一并删除`)) deletePlan(r.id) }}>
                      删除
                    </button>
                  </span>
                </div>
              ))}
            </div>
          )}

          {rec && rv && (
            <div className="pt-detail">
              <div className={`p-status ${OUTCOME_TONE[rv.outcome] || ''}`}>
                <span className="p-badge">{rv.outcome_text}</span>
                <span className="p-reasons">
                  #{rec.id} · {rec.code} · {rec.model_id} · 依据 {rec.as_of}
                  {rv.not_filled ? ` —— ${rv.not_filled.text}` : ''}
                </span>
                <button className="ghost" style={{ marginLeft: 'auto' }} onClick={closeRecord}>关闭</button>
              </div>

              <div className="p-cols">
                <div className="pt-card">
                  <div className="pt-card-title">执行与数据</div>
                  <Row k="计划生成" v={(rec.created_at || '').slice(0, 19).replace('T', ' ')} />
                  <Row k="最早执行" v={rv.execution_window.earliest_execution_session || '—'}
                    hint={rv.execution_window.execution_session_confirmed ? '交易日已确认' : '交易日未确认'} />
                  <Row k="有效至" v={rv.execution_window.valid_until_session || '—'} />
                  <Row k="复盘行情" v={`${rv.data_span.start || '—'} ~ ${rv.data_span.end || '—'}`}
                    hint={`${rv.data_span.bar_count} 根`} />
                  {rv.not_filled && (
                    <Row k="未成交原因" t="warn" v={REASON_TAG[rv.not_filled.reason_code] || rv.not_filled.reason_code} />
                  )}
                  {rec.renewed_from && (
                    <Row k="续期来源" v={`#${rec.renewed_from}`} hint="按更新后的行情重新生成" />
                  )}
                  {rec.superseded_by && (
                    <Row k="已被取代" t="warn" v={`#${rec.superseded_by}`} hint="未成交后按最新行情续期" />
                  )}
                </div>

                <div className="pt-card">
                  <div className="pt-card-title">计划 vs 实际</div>
                  <Row k="计划数量" v={`${money(rv.vs_plan.planned_shares)} 股`}
                    hint={`参考价 ${f2(rv.vs_plan.planned_reference_price)}`} />
                  <Row k="实际买入" v={rv.vs_plan.actual_shares ? `${money(rv.vs_plan.actual_shares)} 股` : '未成交'}
                    hint={rv.vs_plan.actual_avg_price ? `均价 ${f2(rv.vs_plan.actual_avg_price, 4)}` : ''} />
                  <Row k="价格偏差" t={tone(rv.vs_plan.price_deviation_pct)}
                    v={rv.vs_plan.price_deviation_pct == null ? '—' : `${rv.vs_plan.price_deviation_pct > 0 ? '+' : ''}${rv.vs_plan.price_deviation_pct}%`}
                    hint={rv.vs_plan.planned_fees != null
                      ? `费用差 ${rv.vs_plan.fees_diff == null ? '—' : money(rv.vs_plan.fees_diff)}` : ''} />
                  <Row k="已实现" t={tone(rv.fills_summary.realized_pnl)}
                    v={rv.fills_summary.realized_pnl == null ? '—' : money(rv.fills_summary.realized_pnl)}
                    hint={rv.fills_summary.realized_pct == null ? '' : pct(rv.fills_summary.realized_pct)} />
                  <Row k="浮动" t={tone(rv.market.open_pnl)}
                    v={rv.market.valuable ? (rv.market.open_pnl == null ? '—' : money(rv.market.open_pnl)) : '待行情更新'}
                    hint={rv.market.valuable ? pct(rv.market.open_pct) : rv.market.valuation_date || ''} />
                  <Row k="持仓" v={`${money(rv.fills_summary.open_qty)} 股`}
                    hint={rv.market.mfe_pct != null
                      ? `MFE ${pct(rv.market.mfe_pct)} / MAE ${pct(rv.market.mae_pct)}` : ''} />
                </div>
              </div>

              {rv.vs_plan.checks.some(c => !c.ok) && (
                <div className="pt-warn">
                  {rv.vs_plan.checks.filter(c => !c.ok).map((c, i) => (
                    <div key={i}>· {c.text}{c.note ? `（${c.note}）` : ''}</div>
                  ))}
                </div>
              )}

              <div className="pt-card">
                <div className="pt-card-title">实际成交（只追加，录错可删）</div>
                {rv.fills.length === 0
                  ? <div className="p-cond-note">尚未录入成交。若已下单，请在下方补录；若放弃，请点「标记取消」。</div>
                  : (
                    <div className="pt-fills">
                      <div className="pt-frow pt-fhd">
                        <span>方向</span><span>数量</span><span>价格</span><span>费用</span>
                        <span>成交日</span><span>备注</span><span />
                      </div>
                      {rv.fills.map(f => (
                        <div key={f.id} className="pt-frow">
                          <span className={f.side === 'buy' ? 'up' : 'down'}>
                            {f.side === 'buy' ? '买入' : '卖出'}
                          </span>
                          <span>{money(f.qty)}</span>
                          <span>{f2(f.price, 4)}</span>
                          <span>{money(f.fees)}</span>
                          <span>{f.trade_date}</span>
                          <span className="mut">{f.note || '—'}</span>
                          <span>
                            <button className="pt-del" onClick={() => deleteFill(f.id)}>删除</button>
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                {rec.lifecycle !== 'cancelled' && (
                  <FillForm onAdd={addFill} today={rv.execution_window.earliest_execution_session || rec.as_of} />
                )}
              </div>

              <div className="pt-card">
                <div className="pt-card-title">策略对照（同参数在后续行情严格跟随）</div>
                {!rv.counterfactual.available ? (
                  <div className="p-cond-note">{rv.counterfactual.note || '暂不可用'}</div>
                ) : (
                  <>
                    <Row k="策略入场" v={rv.counterfactual.entry_date || '尚未入场'}
                      hint={rv.counterfactual.entry_price ? f2(rv.counterfactual.entry_price) : ''} />
                    <Row k="策略退出" v={rv.counterfactual.exit_date || '仍持仓'}
                      hint={rv.counterfactual.exit_reason || ''} />
                    <Row k="策略收益" t={tone(rv.counterfactual.net_pct)}
                      v={pct(rv.counterfactual.net_pct)}
                      hint={rv.counterfactual.hold_days != null ? `持有 ${rv.counterfactual.hold_days} 根` : ''} />
                    <div className="p-note-line">· {rv.counterfactual.note || '仅作对照，不代表本计划的执行结果'}</div>
                  </>
                )}
              </div>

              <div className="pt-card">
                <div className="pt-card-title">时间线</div>
                <ul className="pt-tl">
                  {rv.timeline.map((t, i) => (
                    <li key={i}><span className="pt-at">{(t.at || '').slice(0, 16).replace('T', ' ')}</span>
                      <span>{t.text}</span></li>
                  ))}
                </ul>
              </div>

              <div className="pt-ops">
                {rec.lifecycle === 'cancelled' ? (
                  <button className="ghost" onClick={() => reopenPlan()}>撤销取消</button>
                ) : (
                  <>
                    <input value={reason} placeholder="取消原因（可留空）"
                      onChange={e => setReason(e.target.value)} />
                    <button className="ghost" onClick={() => { cancelPlan(reason || '用户取消'); setReason('') }}>
                      标记取消
                    </button>
                  </>
                )}
                {!rec.cancel_reason && !rec.fills_count && !rec.superseded_by && (
                  <button className="ghost" disabled={loading}
                    title="用最新行情按同一模型/参数/账户/约束重新生成一条计划；旧记录仅作存档，不会被改写"
                    onClick={() => rolloverPlan()}>
                    按最新行情续期
                  </button>
                )}
                {rec.superseded_by && (
                  <button className="ghost" onClick={() => openRecord(rec.superseded_by as number)}>
                    打开续期后的 #{rec.superseded_by}
                  </button>
                )}
                {rec.renewed_from && (
                  <button className="ghost" onClick={() => openRecord(rec.renewed_from as number)}>
                    查看被续期的 #{rec.renewed_from}
                  </button>
                )}
                <button className="pt-del" onClick={() => { if (confirm(`删除记录 #${rec.id}？`)) deletePlan(rec.id) }}>
                  删除该记录
                </button>
              </div>

              <div className="p-foot">{rv.notes.map((n, i) => <div key={i}>· {n}</div>)}</div>
            </div>
          )}
        </>
      )}
    </section>
  )
}
