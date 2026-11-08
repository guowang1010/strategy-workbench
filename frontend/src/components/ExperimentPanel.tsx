import { useCallback, useEffect, useState } from 'react'
import { useStore } from '../store'
import { api } from '../api'
import { ExperimentDetail, ExperimentRow } from '../types'

type Filter = 'all' | 'backtest' | 'optimize'

const KIND: Record<string, string> = { backtest: '回测', optimize: '寻优' }
const VERDICT: Record<string, string> = { red: '红灯', yellow: '黄灯', green: '绿灯' }

const pct = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : (v >= 0 ? '+' : '') + (v as number).toFixed(d) + '%'

const fmtTime = (s: string) => (s || '').replace('T', ' ').slice(0, 19)

const paramsText = (p: Record<string, any> | null | undefined) =>
  p && Object.keys(p).length
    ? Object.entries(p).map(([k, v]) => `${k}=${typeof v === 'object' ? '…' : v}`).join(' ')
    : '—'

/** 一句话结论：寻优看灯与最优参数，回测看各模型收益。 */
function summaryOf(r: ExperimentRow): string {
  const o = r.outcome || {}
  if (r.kind === 'optimize') {
    const verdict = VERDICT[o.verdict] || '—'
    return `${verdict} · ${paramsText(o.best_params)}`
  }
  const rows: any[] = o.rows || []
  if (!rows.length) return '—'
  return rows.map(x => `${x.model_id} ${pct(x.total_pct)}`).join(' / ')
}

export default function ExperimentPanel() {
  const expTick = useStore(s => s.expTick)
  const replay = useStore(s => s.replayExperiment)
  const setMsg = useStore(s => s.setMsg)
  const datasets = useStore(s => s.datasets)

  const [filter, setFilter] = useState<Filter>('all')
  const [rows, setRows] = useState<ExperimentRow[]>([])
  const [stats, setStats] = useState<{ total: number; by_kind: Record<string, number> }>({ total: 0, by_kind: {} })
  const [detail, setDetail] = useState<ExperimentDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setBusy(true); setErr('')
    try {
      const d = await api.experiments({ kind: filter === 'all' ? undefined : filter, limit: 200 })
      setRows(d.items); setStats(d.stats)
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }, [filter])

  useEffect(() => { load() }, [load, expTick])

  const dsName = (id: number) => {
    const d = datasets.find(x => x.id === id)
    return d ? d.name : `#${id}`
  }

  const open = async (id: number) => {
    try {
      setDetail(await api.experiment(id))
    } catch (e: any) {
      setErr(e.message)
    }
  }

  const del = async (id: number) => {
    try {
      await api.deleteExperiment(id)
      if (detail?.id === id) setDetail(null)
      setMsg(`已删除实验记录 #${id}`)
      await load()
    } catch (e: any) {
      setErr(e.message)
    }
  }

  const doReplay = async (r: ExperimentRow) => {
    setMsg(`正在按实验 #${r.id} 重跑…`)
    await replay(r.id)
  }

  const tab = (k: Filter, label: string, n: number) => (
    <button className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>
      {label}<span className="exp-count">{n}</span>
    </button>
  )

  return (
    <div className="exp">
      <div className="exp-bar">
        <div className="exp-tabs">
          {tab('all', '全部', stats.total)}
          {tab('backtest', '回测', stats.by_kind.backtest || 0)}
          {tab('optimize', '寻优', stats.by_kind.optimize || 0)}
        </div>
        <button className="mini" disabled={busy} onClick={load}>{busy ? '刷新中…' : '刷新'}</button>
        <span className="hint">
          每条记录都带内容指纹（数据 + 请求体 + 引擎版本）；指纹相同即同一实验的重跑。
        </span>
      </div>

      {err && <div className="exp-err">{err}</div>}

      {!rows.length && !busy && (
        <p className="empty-row">还没有实验记录。运行一次回测或参数寻优就会自动留档。</p>
      )}

      {!!rows.length && (
        <div className="table-wrap">
          <table className="exp-table">
            <thead>
              <tr>
                <th>时间</th><th>类型</th><th>数据集</th><th>股票</th><th>模型</th>
                <th>结论</th><th>指纹</th><th className="num">耗时</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} className={detail?.id === r.id ? 'on' : ''} onClick={() => open(r.id)}>
                  <td className="mono">{fmtTime(r.created_at)}</td>
                  <td><span className={'exp-kind ' + r.kind}>{KIND[r.kind] || r.kind}</span></td>
                  <td className="ellipsis" title={dsName(r.dataset_id)}>{dsName(r.dataset_id)}</td>
                  <td className="mono">{r.code || `${(r.outcome?.codes || []).length} 只`}</td>
                  <td className="mono">{r.model_id || '—'}</td>
                  <td className="ellipsis" title={summaryOf(r)}>{summaryOf(r)}</td>
                  <td className="mono">{r.spec_hash}</td>
                  <td className="num">{r.duration_ms == null ? '—' : `${r.duration_ms} ms`}</td>
                  <td className="exp-ops" onClick={e => e.stopPropagation()}>
                    <button className="mini" onClick={() => doReplay(r)}>重跑</button>
                    <button className="mini ghost" onClick={() => del(r.id)}>删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detail && (
        <div className="exp-detail">
          <div className="exp-detail-head">
            <strong>实验 #{detail.id}</strong>
            <span className="mono">{detail.engine_version} · {detail.spec_hash}</span>
            <div className="spacer" />
            <button className="mini" onClick={() => doReplay(detail)}>按此重跑</button>
            <button className="mini ghost" onClick={() => setDetail(null)}>关闭</button>
          </div>
          <div className="exp-detail-body">
            <div>
              <div className="exp-sub">可重放规格</div>
              <pre className="exp-json">{JSON.stringify(detail.spec, null, 2)}</pre>
            </div>
            <div>
              <div className="exp-sub">结果摘要</div>
              <pre className="exp-json">{JSON.stringify(detail.outcome, null, 2)}</pre>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
