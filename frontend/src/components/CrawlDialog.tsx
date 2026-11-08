import { useMemo, useState } from 'react'
import { useStore } from '../store'
import { CrawlResult } from '../types'

interface Props {
  onClose: () => void
}

const NORMALIZE = (s: string) => {
  // 支持 600519 / sh600519 / 600519.SH / 逗号空格换行分隔
  const m = s.trim().toUpperCase().match(/(\d{6})/)
  return m ? m[1] : ''
}

function today() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function shiftDays(n: number) {
  const d = new Date()
  d.setDate(d.getDate() + n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const SAMPLE = ['600519', '000858', '601318']

export default function CrawlDialog({ onClose }: Props) {
  const store = useStore()
  const [draft, setDraft] = useState('')
  const [codes, setCodes] = useState<string[]>([])
  const [mode, setMode] = useState<'days' | 'range'>('days')
  const [days, setDays] = useState(120)
  const [start, setStart] = useState(shiftDays(-365))
  const [end, setEnd] = useState(today())
  const [name, setName] = useState('')
  const [merge, setMerge] = useState(true)
  const [result, setResult] = useState<CrawlResult | null>(null)
  const [err, setErr] = useState('')

  const activeDsMeta = store.datasets.find(d => d.id === store.activeDs)
  const mergeTarget = merge && store.activeDs ? store.activeDs : undefined

  const pending = useMemo(() => {
    const list: string[] = []
    for (const c of draft.split(/[\s,，;；、\n]+/)) {
      const n = NORMALIZE(c)
      if (n && !list.includes(n) && !codes.includes(n)) list.push(n)
    }
    return list
  }, [draft, codes])

  const addDraft = () => {
    if (pending.length) setCodes([...codes, ...pending])
    setDraft('')
  }
  const addMany = (list: string[]) => {
    const next = [...codes]
    for (const c of list) {
      const n = NORMALIZE(c)
      if (n && !next.includes(n)) next.push(n)
    }
    setCodes(next)
  }
  const remove = (c: string) => setCodes(codes.filter(x => x !== c))

  const run = async () => {
    const all = pending.length ? [...codes, ...pending] : codes
    if (!all.length) { setErr('请至少添加 1 只股票代码'); return }
    if (mode === 'range') {
      if (!start) { setErr('请填写起始日期'); return }
      if (end && start > end) { setErr('起始日期不能晚于结束日期'); return }
    }
    setErr('')
    setDraft('')
    setCodes(all)
    // 成功后不关闭：留在弹窗里展示每只股票的拉取摘要，用户看完再关
    // 失败时把后端原因直接显示在弹窗里（顶部提示被弹窗遮罩盖住，看不见）
    const r = await store.crawlStocks({
      codes: all,
      ...(mode === 'days' ? { days } : { start, end }),
      ...(name.trim() && !mergeTarget ? { name: name.trim() } : {}),
      ...(mergeTarget ? { merge_into: mergeTarget } : {}),
    })
    if (!r.ok) setErr('拉取失败：' + r.error)
    else setResult(r.data)
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">在线拉取行情</span>
          <button className="ghost" onClick={onClose}>✕</button>
        </div>

        <div className="crawl-form">
          <div className="crawl-row">
            <label className="crawl-label">股票代码</label>
            <div className="crawl-codes">
              {codes.map(c => (
                <span key={c} className="chip">
                  {store.codeNames[c] ? `${c} ${store.codeNames[c]}` : c}
                  <button onClick={() => remove(c)} title="移除">✕</button>
                </span>
              ))}
              <input
                value={draft}
                placeholder={codes.length ? '继续添加，回车确认' : '如 600519, 000858（支持 6 位代码）'}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' || e.key === ',' || e.key === '，') { e.preventDefault(); addDraft() }
                  if (e.key === 'Backspace' && !draft && codes.length) remove(codes[codes.length - 1])
                }}
              />
            </div>
          </div>
          {pending.length > 0 && (
            <div className="crawl-hint">
              待添加：{pending.join('、')}
              <button className="mini" onClick={addDraft}>加入</button>
            </div>
          )}
          <div className="crawl-row">
            <label className="crawl-label" />
            <div className="crawl-actions">
              <button onClick={() => addMany(store.codes)} disabled={!store.codes.length}>
                从当前数据集载入（{store.codes.length}）
              </button>
              <button onClick={() => addMany(SAMPLE)}>填入示例 3 只</button>
              <button className="danger" onClick={() => { setCodes([]); setDraft('') }} disabled={!codes.length}>清空</button>
            </div>
          </div>

          <div className="crawl-row">
            <label className="crawl-label">时间区间</label>
            <div className="crawl-range">
              <label className="chk">
                <input type="radio" checked={mode === 'days'} onChange={() => setMode('days')} />
                最近交易日数
              </label>
              <input
                type="number" min={30} max={500} step={10} value={days}
                disabled={mode !== 'days'}
                onChange={e => setDays(Math.max(30, Math.min(500, Number(e.target.value) || 120)))}
              />
              <span className="sep" />
              <label className="chk">
                <input type="radio" checked={mode === 'range'} onChange={() => setMode('range')} />
                起止日期
              </label>
              <input type="date" value={start} disabled={mode !== 'range'} onChange={e => setStart(e.target.value)} />
              <span className="to">~</span>
              <input type="date" value={end} disabled={mode !== 'range'} onChange={e => setEnd(e.target.value)} />
            </div>
          </div>
          <div className="crawl-hint">
            {mode === 'days'
              ? `取最近 ${days} 个交易日（含今日，范围 30~500）`
              : `取 ${start || '____'} ~ ${end || '今日'} 之间的交易日，按上证日历对齐`}
          </div>

          <div className="crawl-row">
            <label className="crawl-label">数据集名称</label>
            <input
              className="grow" value={name} placeholder={mergeTarget ? '合并模式下沿用原数据集名称' : '留空则自动生成，如「拉取 3只 2025-11-01~2026-11-01」'}
              disabled={!!mergeTarget}
              onChange={e => setName(e.target.value)}
            />
          </div>

          <div className="crawl-row">
            <label className="crawl-label">入库方式</label>
            <label className="chk">
              <input type="checkbox" checked={!!mergeTarget} disabled={!store.activeDs}
                onChange={e => setMerge(e.target.checked)} />
              合并到当前数据集{activeDsMeta ? `（#${activeDsMeta.id} ${activeDsMeta.name}）` : '（先在左上角选择数据集）'}
            </label>
          </div>
          <div className="crawl-hint">
            {mergeTarget
              ? '已有代码：追加新交易日、重叠日期按最新行情更新；新代码直接加入该数据集。勾掉则另建独立数据集。'
              : '每次拉取另建一个新数据集（历史行为）。想追加行情而不是累积数据集，请勾选「合并到当前数据集」。'}
          </div>

          <div className="crawl-note">
            数据源：搜狐日线 + 腾讯复权因子/名称（与本地 CSV 同口径），拉完直接入库为新数据集并自动切换。
            单只失败不影响其余；同一批代码会覆盖同名 CSV。
            上市晚于窗口起点的新股按实际交易日**有多少拉多少**，不计缺失（只有上市后的停牌才算缺失）。
          </div>

          {err && <div className="crawl-err">{err}</div>}

          <div className="crawl-foot">
            <span className="mut">{result ? (result.merged ? `已合并到数据集 #${result.dataset_id}（更新 ${result.merged_rows} 行）` : `已入库数据集 #${result.dataset_id}`) : `已选 ${codes.length} 只`}</span>
            <span className="spacer" />
            {result
              ? <button className="primary" onClick={onClose}>完成</button>
              : <button onClick={onClose}>取消</button>}
            <button className="primary" disabled={store.loading || !codes.length && !pending.length} onClick={run}>
              {store.loading ? '拉取中…' : '开始拉取'}
            </button>
          </div>
        </div>

        {result && (
          <div className="crawl-result">
            <div className="crawl-res-head">
              完成：{result.summaries.length} 只 / {result.rows} 行 ·
              窗口 {result.window[0]}~{result.window[1]}（{result.trading_days} 个交易日）·
              数据集 #{result.dataset_id}
            </div>
            <table className="crawl-table">
              <thead>
                <tr><th>代码</th><th>名称</th><th>行数</th><th>缺失交易日</th><th>上市日</th></tr>
              </thead>
              <tbody>
                {result.summaries.map(s => (
                  <tr key={s.code}>
                    <td>{s.code}</td>
                    <td>{s.name}</td>
                    <td>{s.rows}</td>
                    <td title={s.missing.length ? `停牌缺失：${s.missing.join('、')}` : undefined}>
                      {s.missing.length ? `${s.missing.length} 天` : '—'}
                    </td>
                    <td>{s.list_date || (s.first_date ? `${s.first_date} 起` : '—')}
                      {!!s.before_listing && <span className="mut">（窗口起点前未上市，{s.rows} 天全量）</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {result.errors.length > 0 && (
              <ul className="crawl-errors">
                {result.errors.map(e => <li key={e.code}>{e.code}：{e.error}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
