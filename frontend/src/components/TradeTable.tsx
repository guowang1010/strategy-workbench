import { useEffect, useMemo, useState } from 'react'
import { useStore, resultFor } from '../store'
import { TradeRow } from '../types'

const pct = (v: number) => (v >= 0 ? '+' : '') + v.toFixed(2) + '%'

// 卖出命中原因（后端 exit_reason 代码 → 中文）。未收录的直接显示原值。
const EXIT_LABEL: Record<string, string> = {
  tp: '止盈', sl: '止损', timeout: '到期', signal: '信号消失', eod: '期末平仓', dp: 'DP 收盘',
}

type ResultFilter = '' | 'win' | 'loss'

export default function TradeTable() {
  const run = useStore(s => s.run)
  const activeId = useStore(s => s.activeId)
  const activeCode = useStore(s => s.activeCode)
  const active = resultFor(run, activeId, activeCode)

  const [exit, setExit] = useState('')
  const [result, setResult] = useState<ResultFilter>('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [kw, setKw] = useState('')
  // 分页：默认第 1 页、每页 20 条；列表本身按买入日倒序，所以第 1 页就是最近记录
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(20)

  // 倒序排列：最新交易显示在最上面（seq 保持原始序号，便于与导出文件对照）
  const trades: TradeRow[] = active ? [...active.trades].reverse() : []
  const modelName = active ? active.name : ''

  // 出场原因是有限集合：只把本模型实际出现过的列进下拉，避免噪音
  const exitOptions = useMemo(() => {
    const seen: string[] = []
    for (const t of trades) if (!seen.includes(t.exit_reason)) seen.push(t.exit_reason)
    return seen
  }, [trades])

  const filtered = useMemo(() => {
    const q = kw.trim().toLowerCase()
    return trades.filter(t => {
      if (exit && t.exit_reason !== exit) return false
      if (result === 'win' && t.net_pct <= 0) return false
      if (result === 'loss' && t.net_pct > 0) return false
      if (from && t.buy_date < from) return false
      if (to && t.buy_date > to) return false
      if (q && !(t.entry_reason || modelName).toLowerCase().includes(q)) return false
      return true
    })
  }, [trades, exit, result, from, to, kw, modelName])

  // 汇总：只统计「筛选后」的交易，并明确标注口径
  const sum = useMemo(() => {
    const n = filtered.length
    if (!n) return null
    const wins = filtered.filter(t => t.net_pct > 0)
    const losses = filtered.filter(t => t.net_pct <= 0)
    const winSum = wins.reduce((a, t) => a + t.net_pct, 0)
    const lossSum = losses.reduce((a, t) => a + t.net_pct, 0)
    // 累计净收益按逐笔复利连乘（仓位未满时与「直接相加减」不同，两者都给）
    const compound = (filtered.reduce((a, t) => a * (1 + t.net_pct / 100), 1) - 1) * 100
    const simple = winSum + lossSum
    return {
      n, wins: wins.length, losses: losses.length,
      winRate: (wins.length / n) * 100,
      compound, simple, avg: simple / n,
      pf: lossSum < 0 ? winSum / Math.abs(lossSum) : null,
      hold: filtered.reduce((a, t) => a + t.hold_days, 0) / n,
      best: Math.max(...filtered.map(t => t.net_pct)),
      worst: Math.min(...filtered.map(t => t.net_pct)),
      gross: filtered.reduce((a, t) => a + t.gross_pct, 0),
    }
  }, [filtered])

  const dirty = !!(exit || result || from || to || kw.trim())
  const reset = () => { setExit(''); setResult(''); setFrom(''); setTo(''); setKw('') }

  // 筛选条件、每页条数或当前模型变化时回到第 1 页（避免停在越界页看到空表）
  useEffect(() => { setPage(1) }, [exit, result, from, to, kw, size, activeId, activeCode])

  useEffect(() => { reset(); setPage(1) }, [activeId, activeCode, run?.run_id, run?.viewTier?.tier])

  const pageCount = Math.max(1, Math.ceil(filtered.length / size))
  const cur = Math.min(page, pageCount)          // 记录被筛选减少时夹紧，不渲染空页
  const start = (cur - 1) * size
  const rows = filtered.slice(start, start + size)

  return (
    <div className="trade-wrap">
      <div className="tt-bar">
        <label>出场
          <select value={exit} onChange={e => setExit(e.target.value)}>
            <option value="">全部</option>
            {exitOptions.map(k => (
              <option key={k} value={k}>{EXIT_LABEL[k] || k}</option>
            ))}
          </select>
        </label>
        <label>结果
          <select value={result} onChange={e => setResult(e.target.value as ResultFilter)}>
            <option value="">全部</option>
            <option value="win">仅盈利</option>
            <option value="loss">仅亏损</option>
          </select>
        </label>
        <label>买入日
          <input type="date" value={from} onChange={e => setFrom(e.target.value)} /> 至
          <input type="date" value={to} onChange={e => setTo(e.target.value)} />
        </label>
        <label className="tt-kw">入场策略
          <input placeholder="条件 / 信号日关键字" value={kw}
            onChange={e => setKw(e.target.value)} />
        </label>
        <button className="tt-reset" onClick={reset} disabled={!dirty}>重置筛选</button>
        <span className="tt-count">
          显示 <b>{filtered.length}</b> / {trades.length} 笔
        </span>
      </div>

      {sum && (
        <div className="tt-sum">
          <div className="tt-stat main" title="按当前筛选结果逐笔复利连乘：∏(1+净收益)">
            <span>累计净收益</span>
            <b className={sum.compound >= 0 ? 'up' : 'down'}>{pct(sum.compound)}</b>
            <i>复利连乘</i>
          </div>
          <div className="tt-stat" title="各笔净收益直接相加减（不复利），与复利的差异来自仓位未满与复利效应">
            <span>单笔累加</span>
            <b className={sum.simple >= 0 ? 'up' : 'down'}>{pct(sum.simple)}</b>
            <i>不复利</i>
          </div>
          <div className="tt-stat"><span>笔数</span><b>{sum.n}</b><i>盈 {sum.wins} / 亏 {sum.losses}</i></div>
          <div className="tt-stat"><span>胜率</span><b>{sum.winRate.toFixed(1)}%</b><i>按笔数</i></div>
          <div className="tt-stat"><span>平均单笔</span>
            <b className={sum.avg >= 0 ? 'up' : 'down'}>{pct(sum.avg)}</b><i>净收益均值</i></div>
          <div className="tt-stat" title="总盈利 ÷ 总亏损（按净收益百分比近似）">
            <span>盈亏比</span><b>{sum.pf === null ? '—' : sum.pf.toFixed(2)}</b><i>总盈/总亏</i></div>
          <div className="tt-stat"><span>平均持天</span><b>{sum.hold.toFixed(1)}</b><i>交易日</i></div>
          <div className="tt-stat"><span>最大单笔</span>
            <div className="pair"><b className="up">{pct(sum.best)}</b>
              <b className="down">{pct(sum.worst)}</b></div><i>盈 / 亏</i></div>
        </div>
      )}

      <div className="trade-table">
        <table>
          <thead>
            <tr>
              <th>#</th><th>买入日</th><th>买价</th><th>卖出日</th><th>卖价</th>
              <th>持天</th><th>毛收益</th><th>净收益</th>
              <th>入场策略（买入命中）</th><th>出场</th><th>MFE</th><th>MAE</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(t => (
              <tr key={t.seq} className={t.net_pct >= 0 ? 'win' : 'loss'}>
                <td>{t.seq}</td><td>{t.buy_date}</td><td>{t.buy_price}</td>
                <td>{t.sell_date}</td><td>{t.sell_price}</td><td>{t.hold_days}</td>
                <td>{pct(t.gross_pct)}</td><td>{pct(t.net_pct)}</td>
                <td className="entry" title={t.entry_reason || modelName}>
                  {t.entry_reason || modelName || '—'}
                </td>
                <td title={t.exit_reason}>{EXIT_LABEL[t.exit_reason] || t.exit_reason}</td>
                <td>{pct(t.mfe_pct)}</td><td>{pct(t.mae_pct)}</td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={12} className="empty">
                {trades.length === 0 ? (active ? '该模型在本区间没有交易' : '当前模型或股票没有回测结果，请先运行回测')
                  : `没有符合筛选条件的交易（共 ${trades.length} 笔，点「重置筛选」查看全部）`}
              </td></tr>
            )}
          </tbody>
          {filtered.length > 0 && sum && (
            <tfoot>
              <tr className="tt-foot">
                <td colSpan={6}>合计（当前筛选 {sum.n} 笔，跨页统计）</td>
                <td>{pct(sum.gross)}</td>
                <td>{pct(sum.simple)}</td>
                <td colSpan={4}>累计净收益 {pct(sum.compound)}（复利）</td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      {filtered.length > 0 && (
        <div className="tt-page">
          <span>共 <b>{filtered.length}</b> 笔 · 第 <b>{start + 1}-{Math.min(start + size, filtered.length)}</b> 笔
            （按买入日倒序，第 1 页为最近记录）</span>
          <label>每页
            <select value={size} onChange={e => setSize(Number(e.target.value))}>
              {[20, 50, 100].map(n => <option key={n} value={n}>{n} 条</option>)}
            </select>
          </label>
          <button className="tt-reset" onClick={() => setPage(1)} disabled={cur <= 1}>首页</button>
          <button className="tt-reset" onClick={() => setPage(cur - 1)} disabled={cur <= 1}>上一页</button>
          <span className="tt-pg">第 {cur} / {pageCount} 页</span>
          <button className="tt-reset" onClick={() => setPage(cur + 1)} disabled={cur >= pageCount}>下一页</button>
          <button className="tt-reset" onClick={() => setPage(pageCount)} disabled={cur >= pageCount}>末页</button>
        </div>
      )}
    </div>
  )
}
