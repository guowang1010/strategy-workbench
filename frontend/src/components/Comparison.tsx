import { Fragment, useState } from 'react'
import { selectionKey, useStore } from '../store'
import { RunResultRow, TierRow } from '../types'

const pct = (v: number) => (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
const f2 = (v: number | null) => v == null ? '—' : v.toFixed(2)

export default function Comparison({ onDetails }: { onDetails: () => void }) {
  const s = useStore()
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [more, setMore] = useState(false)
  const run = s.run
  const stale = !!run && s.runKey !== selectionKey(s.activeDs, s.selected, s.globalParams)
  const view = async (r: RunResultRow, t?: TierRow) => {
    if (s.activeCode !== r.code) await s.selectCode(r.code)
    if (useStore.getState().run?.run_id !== run?.run_id) return
    s.setActive(r.model_id)
    useStore.setState(state => ({ run: state.run ? { ...state.run,
      viewTier: t ? { code: r.code, model: r.model_id, tier: t.id } : null } : null }))
  }
  const apply = (r: RunResultRow, t: TierRow) => {
    if (!s.selected.some(m => m.id === r.model_id)) s.toggleModel(r.model_id, true)
    Object.entries(t.params).forEach(([k, v]) => s.setParam(r.model_id, k, v))
    s.setActive(r.model_id)
    s.setMsg('已应用档位参数，请重新运行回测；历史评估结果保持原值')
  }
  return <div className="cmp-table">
    <div className="tt-bar">
      <button disabled={s.loading} onClick={() => s.runBacktest({ tiers: true, codes: run?.codes ? [...new Set(run.codes)] : undefined })}>评估三档</button>
      {s.loading && s.job && <button onClick={() => s.cancelJob()}>取消评估</button>}
      <label><input type="checkbox" checked={more} onChange={e => setMore(e.target.checked)} />更多指标</label>
      <span>三组参数 · 扣费后收益比较</span>
    </div>
    {stale && <div className="note">参数已变化，以下为历史结果，请重新评估。</div>}
    {!run ? <div className="empty">尚未回测，可点击“评估三档”运行。</div> : <div className="comparison-scroll"><table>
      <thead><tr><th>明细</th><th>代码</th><th>模型</th><th>当前收益</th><th>本区间最佳档位</th><th>最佳收益</th><th>提升(百分点)</th><th>笔数</th><th>回撤</th>{more && <><th>胜率</th><th>夏普</th><th>期望</th><th>暴露</th><th>Calmar</th></>}</tr></thead>
      <tbody>{run.results.map(r => {
        const key = r.code + r.model_id
        const best = r.tiers?.rows.find(t => t.id === r.tiers?.best_id)
        const bm = best?.result?.metrics
        const noTrades = !!r.tiers?.rows.length && r.tiers.rows.every(t => !t.result || t.result.metrics.n_trades === 0)
        return <Fragment key={key}>
          <tr className={r.model_id === s.activeId && r.code === s.activeCode ? 'sel' : ''} onClick={() => view(r)}>
            <td><button disabled={!r.tiers?.rows.length} onClick={e => { e.stopPropagation(); setExpanded({ ...expanded, [key]: !expanded[key] }) }}>{expanded[key] ? '收起' : '展开'}</button></td>
            <td>{r.code}</td><td><span className="dot" style={{ background: r.color }} />{r.name}</td>
            <td>{pct(r.metrics.total_pct)}</td><td>{noTrades ? '均无交易' : best?.label || (['M1', 'M2'].includes(r.model_id) ? '基准，不微调' : '待评估')}</td>
            <td>{bm ? pct(bm.total_pct) : '—'}</td><td>{bm ? (bm.total_pct - r.metrics.total_pct).toFixed(2) : '—'}</td>
            <td>{r.metrics.n_trades}</td><td>{pct(r.metrics.max_drawdown_pct)}</td>{more && <><td>{f2(r.metrics.win_rate)}%</td><td>{f2(r.metrics.sharpe)}</td><td>{f2(r.metrics.expectancy_pct)}</td><td>{f2(r.metrics.exposure_pct)}%</td><td>{f2(r.metrics.calmar)}</td></>}
          </tr>
          {expanded[key] && <tr className="tier-detail"><td colSpan={more ? 14 : 9}>
            <div className="tier-cards">{r.tiers?.rows.map(t => {
              const m = t.result?.metrics
              const spec = s.models.find(x => x.id === r.model_id)
              return <article className={'tier-card' + (!noTrades && best?.id === t.id ? ' best' : '')} key={t.id}>
                <div className="tier-heading"><b>{t.label}</b>{!noTrades && best?.id === t.id && <span>本区间最佳</span>}</div>
                <div className={'tier-return ' + (m && m.total_pct > 0 ? 'up' : m && m.total_pct < 0 ? 'down' : '')}>{m ? pct(m.total_pct) : '—'}</div>
                <div className="tier-params">{t.keys.map(k => <div key={k}>{spec?.param_schema.find(f => f.key === k)?.label || k}<b>{t.params[k]}</b></div>)}</div>
                <div className="tier-meta">回撤 {m ? pct(m.max_drawdown_pct) : '—'} · {m?.n_trades ?? '—'} 笔 · 胜率 {m ? f2(m.win_rate) + '%' : '—'}</div>
                {t.error ? <div className="note">{t.error}</div> : <div className="tier-actions"><button onClick={async () => { await view(r, t); onDetails() }}>查看明细</button><button disabled={stale} onClick={() => apply(r, t)}>应用参数</button></div>}
                {m && m.n_trades < 5 && <div className="note">{m.n_trades === 0 ? '无交易' : '交易样本少'}</div>}
              </article>
            })}</div>
            {noTrades && <div className="note">三档均无交易，无法比较交易表现。</div>}
            {bm && bm.total_pct < 0 && <div className="note">三档均亏损。</div>}
            <details><summary>完整参数快照</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(r.tiers?.rows.map(t => ({ 档位: t.label, 参数: t.params })), null, 2)}</pre></details>
          </td></tr>}
        </Fragment>
      })}</tbody>
    </table></div>}
    <div className="note">点击模型行切换当前模型；查看档位明细会同步主图、指标卡和交易明细。交易明细上方可切回当前模型的原始回测。最佳档位仅代表本段历史结果，未来表现需另行验证。</div>
  </div>
}
