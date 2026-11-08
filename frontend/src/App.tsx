import { useEffect, useState } from 'react'
import { useStore, resultFor } from './store'
import ModelPanel from './components/ModelPanel'
import KLineChart from './components/KLineChart'
import StatsCards from './components/StatsCards'
import TradeTable from './components/TradeTable'
import ResultSelector from './components/ResultSelector'
import Comparison from './components/Comparison'
import OptimizePanel from './components/OptimizePanel'
import PlanPanel from './components/PlanPanel'
import SummaryPanel from './components/SummaryPanel'
import ExperimentPanel from './components/ExperimentPanel'
import CrawlDialog from './components/CrawlDialog'
import AboutDialog from './components/AboutDialog'

type Tab = 'trades' | 'compare' | 'optimize' | 'plan' | 'summary' | 'experiments'

// 布局显示偏好（图表折叠 / 副图开关 / 左右侧栏折叠）持久化，刷新后保留
const PREF_KEY = 'wb.chart.pref'
type Pref = { collapsed: boolean; vol: boolean; eq: boolean; left: boolean; right: boolean }
const loadPref = (): Pref => {
  try {
    const v = JSON.parse(localStorage.getItem(PREF_KEY) || '{}')
    return {
      collapsed: !!v.collapsed,
      vol: v.vol !== false,
      eq: v.eq !== false,
      left: v.left !== false,   // true = 展开
      right: v.right !== false,
    }
  } catch { return { collapsed: false, vol: true, eq: true, left: true, right: true } }
}

export default function App() {
  const store = useStore()
  const [tab, setTab] = useState<Tab>('trades')
  const [showCrawl, setShowCrawl] = useState(false)
  const [showAbout, setShowAbout] = useState(false)
  const [beforeFocus, setBeforeFocus] = useState<Pref | null>(null)
  const [pref, setPrefState] = useState(loadPref)

  const setPref = (patch: Partial<Pref>) => {
    setPrefState(p => {
      const next = { ...p, ...patch }
      try { localStorage.setItem(PREF_KEY, JSON.stringify(next)) } catch { /* 忽略隐私模式 */ }
      return next
    })
  }

  useEffect(() => { store.init() }, [])
  useEffect(() => {
    document.documentElement.dataset.theme = store.theme
  }, [store.theme])

  // 快捷键：C 图表区折叠，L 左栏，R 右栏（输入框内不拦截）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement | null
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return
      const k = e.key.toLowerCase()
      if (k === 'c') setPref({ collapsed: !pref.collapsed })
      else if (k === 'l') setPref({ left: !pref.left })
      else if (k === 'r') setPref({ right: !pref.right })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pref.collapsed, pref.left, pref.right])

  const active = resultFor(store.run, store.activeId, store.activeCode)
  const baseline = resultFor(store.run, 'M1', store.activeCode)

  const download = async (fmt: string) => {
    if (!store.run) { store.setMsg('请先运行回测'); return }
    try {
      const r = await fetch('/api/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: store.run.run_id, fmt }),
      })
      if (!r.ok) {
        const body = await r.json().catch(() => null)
        throw new Error(body?.error || body?.detail || `HTTP ${r.status}`)
      }
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      try {
        const a = document.createElement('a')
        a.href = url
        a.download = `${store.run.run_id}.${fmt}`
        a.click()
      } finally {
        URL.revokeObjectURL(url)
      }
    } catch (e) {
      store.setMsg(`导出失败：${e instanceof Error ? e.message : '未知错误'}`)
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">波段策略回测工作台</div>
        <select value={store.activeDs ?? ''} onChange={e => store.selectDataset(Number(e.target.value))}>
          {store.datasets.map(d => <option key={d.id} value={d.id}>{d.name}（{d.start_date}~{d.end_date}）</option>)}
        </select>
        <select value={store.activeCode ?? ''} onChange={e => store.selectCode(e.target.value)}>
          {store.codes.map(c => {
            const r = store.codeRanges.find(x => x.code === c)
            const name = store.codeNames[c] ? `${store.codeNames[c]}（${c}）` : c
            // 日 K 范围：同年省略第二个年份，尽量短；拿不到区间时退化为只显示名称
            const range = r
              ? (r.first.slice(0, 4) === r.last.slice(0, 4)
                  ? ` ${r.first.slice(5)}~${r.last.slice(5)}` : ` ${r.first}~${r.last}`) + ` · ${r.rows}日`
              : ''
            return <option key={c} value={c}>{name}{range}</option>
          })}
        </select>
        <div className="spacer" />
        <button onClick={() => setShowCrawl(true)} title="在线拉取日线行情">拉取行情</button>
        <button className="primary" disabled={store.loading} onClick={() => store.runBacktest()}>运行回测</button>
        <button disabled={store.loading} onClick={() => store.runOptimize(store.activeId)}>参数寻优</button>
        <button onClick={() => download('xlsx')}>导出 xlsx</button>
        <button onClick={() => download('csv')}>导出 csv</button>
        <button className="about-trigger" onClick={() => setShowAbout(true)}>关于</button>
        <button className="ghost" onClick={() => store.toggleTheme()}>{store.theme === 'light' ? '🌙' : '☀'}</button>
      </header>

      {store.msg && <div className="msg">{store.msg}</div>}

      <div className="body">
        <aside className={`side left${pref.left ? '' : ' collapsed'}`}>
          <div className="side-head">
            <span className="side-title" title="模型与参数">模型与参数</span>
            <button className="side-fold" onClick={() => setPref({ left: !pref.left })}
              title={pref.left ? '收起左侧面板（快捷键：L）' : '展开左侧面板（快捷键：L）'}>
              {pref.left ? '«' : '»'}
            </button>
          </div>
          <div className="side-body"><ModelPanel /></div>
        </aside>
        <main className={`center${pref.collapsed ? ' chart-off' : ''}`}>
          <div className={`chart-card${pref.collapsed ? ' collapsed' : ''}`}>
            <div className="chart-bar">
              <button
                className="fold"
                onClick={() => setPref({ collapsed: !pref.collapsed })}
                title={pref.collapsed ? '展开图表（快捷键：C）' : '收起图表，给下方表格让位（快捷键：C）'}
              >{pref.collapsed ? '▸' : '▾'}</button>
              <span className="chart-title">
                {store.activeCode ? (store.codeNames[store.activeCode] || store.activeCode) : 'K 线'}
                {store.activeCode ? `（${store.activeCode}）` : ''}
              </span>
              {pref.collapsed
                ? <span className="chart-sub">图表已收起</span>
                : <span className="chart-sub">K线 · 均线 · 买卖点</span>}
              <div className="spacer" />
              {!pref.collapsed && <>
                <label className="chk"><input type="checkbox" checked={pref.vol}
                  onChange={e => setPref({ vol: e.target.checked })} />成交量</label>
                <label className="chk"><input type="checkbox" checked={pref.eq}
                  onChange={e => setPref({ eq: e.target.checked })} />权益曲线</label>
              </>}
              <span className="chart-range">{store.bars.length ? `${store.bars[0]?.date} ~ ${store.bars[store.bars.length - 1]?.date}` : ''}</span>
            </div>
            <div className="chart-body">
              <KLineChart bars={store.bars} active={active} baseline={baseline}
                theme={store.theme} showVolume={pref.vol} showEquity={pref.eq} />
            </div>
          </div>
          <div className="tabs">
            <button className={tab === 'trades' ? 'on' : ''} onClick={() => setTab('trades')}>交易明细</button>
            <button className={tab === 'compare' ? 'on' : ''} onClick={() => setTab('compare')}>模型对比</button>
            <button className={tab === 'optimize' ? 'on' : ''} onClick={() => { if (!store.optimize) store.runOptimize(store.activeId); setTab('optimize') }}>参数寻优</button>
            <button className={tab === 'plan' ? 'on' : ''} onClick={() => setTab('plan')}>下一笔计划</button>
            <button className={tab === 'summary' ? 'on' : ''} onClick={() => setTab('summary')}>跨股票汇总</button>
            <button className={tab === 'experiments' ? 'on' : ''} onClick={() => setTab('experiments')}>实验记录</button>
          </div>
          {(tab === 'trades' || tab === 'compare') && <ResultSelector focused={!!beforeFocus} onFocus={() => {
            if (beforeFocus) { setPref(beforeFocus); setBeforeFocus(null) }
            else { setBeforeFocus({ ...pref }); setPref({ left: false, right: false, collapsed: true }) }
          }} />}
          <div className="tab-body">
            {tab === 'trades' && <TradeTable />}
            {tab === 'compare' && <Comparison onDetails={() => setTab('trades')} />}
            {tab === 'optimize' && <OptimizePanel />}
            {tab === 'plan' && <PlanPanel />}
            {tab === 'summary' && <SummaryPanel />}
            {tab === 'experiments' && <ExperimentPanel />}
          </div>
        </main>
        <aside className={`side right${pref.right ? '' : ' collapsed'}`}>
          <div className="side-head">
            <span className="side-title" title="绩效指标">绩效指标</span>
            <button className="side-fold" onClick={() => setPref({ right: !pref.right })}
              title={pref.right ? '收起右侧指标（快捷键：R）' : '展开右侧指标（快捷键：R）'}>
              {pref.right ? '»' : '«'}
            </button>
          </div>
          <div className="side-body"><StatsCards /></div>
        </aside>
      </div>
      <footer className="foot">
        历史回测 / 样本外切分，不代表未来收益。本工具仅供策略研究与教学，不构成任何投资建议。
      </footer>
      {showCrawl && <CrawlDialog onClose={() => setShowCrawl(false)} />}
      {showAbout && <AboutDialog onClose={() => setShowAbout(false)} />}
    </div>
  )
}
