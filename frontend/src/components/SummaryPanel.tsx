import { useStore } from '../store'
import JobProgress from './JobProgress'
import { SummaryResponse, SummaryRow } from '../types'
import { Fragment } from 'react'

const pct = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : (v >= 0 ? '+' : '') + (v as number).toFixed(d) + '%'

const VMAP: Record<string, string> = { red: '🔴', yellow: '🟡', green: '🟢' }

function Cell({ v }: { v: number | null }) {
  if (v == null) return <td className="center">—</td>
  const cls = v > 0 ? 'pos' : v < 0 ? 'neg' : ''
  return <td className={'center ' + cls}>{pct(v)}</td>
}

export default function SummaryPanel() {
  const summary = useStore(s => s.summary)
  const loading = useStore(s => s.loading)
  const runSummary = useStore(s => s.runSummary)
  const includeOos = useStore(s => s.includeOos)
  const setIncludeOos = useStore(s => s.setIncludeOos)
  const models = useStore(s => s.models)

  const data: SummaryResponse | null = summary
  const modelIds = data?.model_ids ?? []
  const nameOf = (id: string) => models.find(m => m.id === id)?.name ?? id
  const hasM4 = modelIds.includes('M4')

  return (
    <div className="summary">
      <div className="summary-bar">
        <button className="primary" disabled={loading} onClick={() => runSummary()}>
          生成跨股票汇总
        </button>
        <label className="chk">
          <input type="checkbox" checked={includeOos} onChange={e => setIncludeOos(e.target.checked)} />
          含样本外诚实口径（仅 M4，较慢）
        </label>
        <span className="hint">
          对数据集内全部个股跑所选模型全样本回测，拼成「个股 × 模型」对照表。
        </span>
      </div>
      <JobProgress kind="summary" />

      {!data && <p className="empty-row">点击「生成跨股票汇总」开始（默认数据集含 7 只个股）。</p>}

      {data && (
        <div className="table-wrap">
          <table className="summary-table">
            <thead>
              <tr>
                <th className="sticky" rowSpan={2}>代码</th>
                <th className="sticky" rowSpan={2}>名称</th>
                <th className="sticky" rowSpan={2}>交易日</th>
                {modelIds.map(mid => {
                  const span = data.include_oos && mid === 'M4' ? 3 : 1
                  return (
                    <th key={mid} colSpan={span}>
                      {nameOf(mid)}
                      {mid === 'M2' && <span className="sub"> (DP上界)</span>}
                    </th>
                  )
                })}
              </tr>
              <tr>
                {modelIds.map(mid => {
                  if (data.include_oos && mid === 'M4') {
                    return (
                      <Fragment key={mid}>
                        <th className="sub">总收益%</th>
                        <th className="sub">样本外%</th>
                        <th className="sub">灯</th>
                      </Fragment>
                    )
                  }
                  return <th key={mid} className="sub">总收益%</th>
                })}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r: SummaryRow) => (
                <tr key={r.code}>
                  <td className="sticky">{r.code}</td>
                  <td className="sticky">{r.name}</td>
                  <td className="center">{r.n}</td>
                  {modelIds.map(mid => {
                    const m = r.models[mid]
                    const o = r.oos?.[mid]
                    return (
                      <FragmentCell key={mid} m={m} o={o} showOos={data.include_oos && mid === 'M4'} />
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && (
        <p className="note">
          全样本总收益为该模型对每只股票的完整回测结果；样本外（最近 61 日）诚实口径为训练段选参→测试段执行，
          红绿灯：🔴不可用(诚实期望≤0) / 🟡谨慎 / 🟢可用。M2(DP上界) 为事后最优，仅供收益空间参照。
        </p>
      )}
    </div>
  )
}

function FragmentHead({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}

function FragmentCell({ m, o, showOos }: { m: any; o: any; showOos: boolean }) {
  if (!m) return <td className="center">—</td>
  if (!showOos) return <Cell v={m.total_pct} />
  // M4 + 样本外：总收益 / 样本外总收益 / 灯
  return (
    <>
      <Cell v={m.total_pct} />
      <Cell v={o ? o.honest_total : null} />
      <td className="center">{o && o.verdict ? (VMAP[o.verdict] || '—') : '…'}</td>
    </>
  )
}
