import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { Bar, RunResultRow } from '../types'
import { buildIndicators } from '../indicators'

interface Props {
  bars: Bar[]
  active: RunResultRow | null
  baseline: RunResultRow | null
  theme: 'light' | 'dark'
  showVolume?: boolean   // 成交量副图（默认显示）
  showEquity?: boolean   // 权益曲线副图（默认显示）
}

const TEXT = { light: '#333', dark: '#c9d1d9' }
const AXIS = { light: '#888', dark: '#555' }
const UP = '#ec4d4d'    // A 股习惯：涨为红
const DOWN = '#21a675'  // 跌为绿

// 均线固定配色（图线与 tooltip 一一对应）：MA5 蓝 / MA10 橙 / MA20 黄 / MA60 紫
const MA_COLORS = { ma5: '#4e9bff', ma10: '#ff9f40', ma20: '#ffd666', ma60: '#b07cf0' }

// tooltip 弹窗视觉规范（行情软件风格，深色半透，亮/暗主题统一）
const TIP = {
  up: '#ff4d4f', down: '#00b42a',
  fg: '#ffffff', fgDim: 'rgba(255,255,255,0.65)',
}

// 副图可单独隐藏：隐藏后主图 K 线自动吃满释放出来的高度（百分比布局，随窗口自适应）
function gridLayout(showVol: boolean, showEq: boolean) {
  if (showVol && showEq) return [{ top: 34, height: '45%' }, { top: '57%', height: '11%' }, { top: '71%', height: '16%' }]
  if (showVol) return [{ top: 34, height: '62%' }, { top: '70%', height: '15%' }]
  if (showEq) return [{ top: 34, height: '56%' }, { top: '64%', height: '22%' }]
  return [{ top: 34, height: '78%' }]
}

export default function KLineChart({ bars, active, baseline, theme, showVolume = true, showEquity = true }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const latest = useRef({ bars, active, baseline, theme, eqIdx: 2, nAxes: 3 })
  latest.current = { ...latest.current, bars, active, baseline, theme }

  useEffect(() => {
    if (!el.current) return
    chart.current = echarts.init(el.current)
    chart.current.on('datazoom', () => applyBounds())
    // 测试钩子：供 CDP/Playwright 断言「图上标记数 == 交易明细行数」
    ;(window as any).__chart = chart.current
    const ro = new ResizeObserver(() => chart.current?.resize())
    ro.observe(el.current)
    return () => { ro.disconnect(); chart.current?.dispose(); chart.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!chart.current) return
    chart.current.setOption({ backgroundColor: 'transparent' })
    // 副图开关变化会改变 grid 数量，必须整体重建（notMerge），否则旧副图残留
    const opt = buildOption(bars, active, baseline, theme, showVolume, showEquity)
    chart.current.setOption(opt, true)
    const built = opt as any
    latest.current.eqIdx = built.__eqIdx ?? 2
    latest.current.nAxes = built.__nAxes ?? 3
    applyBounds()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, active, baseline, theme, showVolume, showEquity])

  function applyBounds() {
    const ch = chart.current
    if (!ch) return
    const o = ch.getOption() as any
    const dz = o.dataZoom && o.dataZoom[0]
    const n = latest.current.bars.length
    if (!n) return
    const start = (dz && dz.start != null) ? dz.start : 42
    const end = (dz && dz.end != null) ? dz.end : 100
    const i0 = Math.floor((start / 100) * n)
    const i1 = Math.min(n - 1, Math.ceil((end / 100) * n) - 1)
    let mn = Infinity, mx = -Infinity
    for (const r of [latest.current.active, latest.current.baseline]) {
      if (!r) continue
      for (let i = i0; i <= i1; i++) {
        const v = r.equity[i]
        if (v == null) continue
        if (v < mn) mn = v
        if (v > mx) mx = v
      }
    }
    if (!isFinite(mn) || !isFinite(mx)) return
    const pad = Math.max((mx - mn) * 0.12, 0.03)
    const gi = latest.current.eqIdx
    const nAxes = latest.current.nAxes
    if (gi == null || gi >= nAxes) return
    const patch = Array.from({ length: nAxes }, (_, i) =>
      i === gi ? { gridIndex: gi, min: Math.max(0, mn - pad), max: mx + pad } : {})
    ch.setOption({ yAxis: patch })
  }

  return <div ref={el} className="chart" style={{ width: '100%', height: '100%' }} />
}

function buildOption(
  bars: Bar[], active: RunResultRow | null, baseline: RunResultRow | null,
  theme: 'light' | 'dark', showVolume: boolean, showEquity: boolean,
) {
  const text = TEXT[theme], axis = AXIS[theme]
  const dates = bars.map(b => b.date)
  const candle = bars.map(b => [b.adj_open, b.adj_close, b.adj_low, b.adj_high])
  const ind = buildIndicators(bars)
  const vol = bars.map(b => ({
    value: b.volume,
    itemStyle: { color: b.adj_close >= b.adj_open ? UP : DOWN },
  }))

  // 网格 / 坐标轴：主图 + 可选成交量 + 可选权益，索引随开关动态分配
  const layout = gridLayout(showVolume, showEquity)
  const K = 0
  const volIdx = showVolume ? 1 : null
  const eqIdx = showEquity ? (showVolume ? 2 : 1) : null
  const axisIdx = [K, ...(volIdx != null ? [volIdx] : []), ...(eqIdx != null ? [eqIdx] : [])]

  const mkX = (g: number) => ({
    type: 'category', data: dates, gridIndex: g,
    boundaryGap: true, axisLine: { lineStyle: { color: axis } },
    axisLabel: { show: false }, axisTick: { show: false },
    axisPointer: { z: 100 },
  })
  const xAxes = axisIdx.map(mkX)
  const yAxes: any[] = [{
    gridIndex: K, scale: true, axisLabel: { color: text, formatter: (v: number) => v.toFixed(2) },
    splitLine: { lineStyle: { color: axis, opacity: 0.25 } },
  }]
  if (volIdx != null) yAxes.push({ gridIndex: volIdx, scale: true, axisLabel: { show: false }, splitLine: { show: false } })
  if (eqIdx != null) yAxes.push({
    gridIndex: eqIdx, scale: true,
    axisLabel: { color: text, formatter: (v: number) => (v * 100).toFixed(2) + '%' },
    splitLine: { lineStyle: { color: axis, opacity: 0.25 } },
  })

  const grids = layout.map((g, i) => ({ left: 56, right: 18, ...g }))

  const series: any[] = [
    { name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: candle,
      itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN } },
    { name: 'MA5', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ind.ma5, smooth: true, showSymbol: false, lineStyle: { width: 1, color: MA_COLORS.ma5 }, itemStyle: { color: MA_COLORS.ma5 }, color: MA_COLORS.ma5 },
    { name: 'MA10', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ind.ma10, smooth: true, showSymbol: false, lineStyle: { width: 1, color: MA_COLORS.ma10 }, itemStyle: { color: MA_COLORS.ma10 }, color: MA_COLORS.ma10 },
    { name: 'MA20', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ind.ma20, smooth: true, showSymbol: false, lineStyle: { width: 1.2, color: MA_COLORS.ma20 }, itemStyle: { color: MA_COLORS.ma20 }, color: MA_COLORS.ma20 },
    { name: 'MA60', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ind.ma60, smooth: true, showSymbol: false, lineStyle: { width: 1.2, type: 'dashed', color: MA_COLORS.ma60 }, itemStyle: { color: MA_COLORS.ma60 }, color: MA_COLORS.ma60 },
    { name: '唐奇安上轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ind.don.upper, smooth: false, showSymbol: false, lineStyle: { width: 1, type: 'dotted', opacity: 0.7 } },
    { name: '唐奇安下轨', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ind.don.lower, smooth: false, showSymbol: false, lineStyle: { width: 1, type: 'dotted', opacity: 0.7 } },
    { name: '布林上', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ind.bol.up, showSymbol: false, lineStyle: { width: 1, opacity: 0.6 } },
    { name: '布林下', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ind.bol.dn, showSymbol: false, lineStyle: { width: 1, opacity: 0.6 } },
  ]
  if (volIdx != null) {
    series.push({ name: '成交量', type: 'bar', xAxisIndex: volIdx, yAxisIndex: volIdx, data: vol })
  }

  if (eqIdx != null && baseline) {
    series.push({ name: '买入持有', type: 'line', xAxisIndex: eqIdx, yAxisIndex: eqIdx, data: baseline.equity,
      showSymbol: false, lineStyle: { width: 1.5, type: 'dashed', color: '#8c8c8c' }, color: '#8c8c8c' })
  }
  if (eqIdx != null && active) {
    series.push({ name: active.name, type: 'line', xAxisIndex: eqIdx, yAxisIndex: eqIdx, data: active.equity,
      showSymbol: false, lineStyle: { width: 2, color: active.color }, color: active.color,
      areaStyle: { color: active.color, opacity: 0.12 } })
  }
  if (active) {
    const segData: any[] = []
    for (const seg of active.markers.segments) {
      segData.push([seg[0][0], seg[0][1]])
      segData.push([seg[1][0], seg[1][1]])
      segData.push(null)
    }
    series.push({ name: '持仓段', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: segData,
      showSymbol: false, connectNulls: false, lineStyle: { width: 2, color: active.color }, color: active.color })
    series.push({ name: '买点', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, data: active.markers.buy,
      symbol: 'triangle', symbolSize: 11, itemStyle: { color: '#2f9e44' }, z: 10 })
    series.push({ name: '卖点', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, data: active.markers.sell,
      symbol: 'triangle', symbolRotate: 180, symbolSize: 11, itemStyle: { color: '#d93f3a' }, z: 10 })
  }

  return {
    animation: false,
    legend: { top: 4, textStyle: { color: text }, type: 'scroll', data: (series as any[]).map(s => s.name) },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(20,24,32,0.88)',
      borderColor: 'rgba(255,255,255,0.12)', borderWidth: 1,
      padding: [12, 14],
      textStyle: { color: TIP.fg, fontFamily: 'system-ui, -apple-system, sans-serif', fontSize: 13 },
      extraCssText: 'border-radius:8px; box-shadow:0 4px 16px rgba(0,0,0,0.35); min-width:180px; backdrop-filter:blur(4px);',
      position: (pos: any, params: any, dom: any, rect: any, size: any) => {
        // 防溢出：贴边时翻转到另一侧；pos 缺失（程序化 showTip）时回退顶部居中
        if (!pos || !Array.isArray(pos) || pos.length < 2) {
          return [Math.max(8, (size.viewSize[0] - size.contentSize[0]) / 2), 8]
        }
        const x = pos[0] < size.viewSize[0] / 2 ? pos[0] + 18 : pos[0] - size.contentSize[0] - 18
        const y = Math.max(8, pos[1] - size.contentSize[1] - 12)
        return [Math.max(8, Math.min(x, size.viewSize[0] - size.contentSize[0] - 8)), y]
      },
      formatter: (params: any) => {
        if (!Array.isArray(params) || !params.length) return ''
        const i = params[0].dataIndex
        const b = bars[i]
        if (!b) return ''
        const n2 = (v: number | null | undefined) => v == null ? '—' : v.toFixed(2)
        const isUp = b.adj_close >= b.adj_open
        const pc = isUp ? TIP.up : TIP.down          // 涨跌语义色
        const equity = (r: RunResultRow | null) => (r && r.equity[i] != null ? (r.equity[i] * 100).toFixed(2) + '%' : '—')
        // 行：标签左对齐 + 数值右对齐（弹性两栏）
        const row = (label: string, value: string, vColor?: string) =>
          `<div style="display:flex;justify-content:space-between;align-items:baseline;gap:16px;line-height:1.85;">
             <span style="color:${TIP.fgDim};font-size:13px;">${label}</span>
             <span style="color:${vColor || TIP.fg};font-size:${vColor ? 14 : 13}px;font-weight:${vColor ? 600 : 400};font-variant-numeric:tabular-nums;">${value}</span>
           </div>`
        const maRow = (label: string, v: number | null | undefined, c: string) =>
          v == null ? '' : row(label, v.toFixed(2), c)
        const sep = '<div style="height:1px;background:rgba(255,255,255,0.10);margin:7px 0;"></div>'
        let html = `<div style="font-size:14px;font-weight:600;color:${TIP.fg};letter-spacing:0.3px;">${b.date}</div>` + sep
        html += row('开', n2(b.adj_open))
        html += row('高', n2(b.adj_high), isUp ? TIP.up : undefined)
        html += row('低', n2(b.adj_low), !isUp ? TIP.down : undefined)
        html += row('收', n2(b.adj_close), pc)
        html += sep
        html += maRow('MA5', ind.ma5[i], MA_COLORS.ma5)
        html += maRow('MA10', ind.ma10[i], MA_COLORS.ma10)
        html += maRow('MA20', ind.ma20[i], MA_COLORS.ma20)
        html += maRow('MA60', ind.ma60[i], MA_COLORS.ma60)
        html += sep
        if (showVolume) html += row('量', (b.volume / 10000).toFixed(2) + '万手')
        if (showEquity && baseline) html += row('买入持有', equity(baseline))
        if (showEquity && active && active.name !== baseline?.name) html += row(active.name, equity(active))
        return html
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: grids,
    xAxis: xAxes, yAxis: yAxes,
    dataZoom: [
      { type: 'inside', xAxisIndex: axisIdx, start: 42, end: 100 },
      { type: 'slider', xAxisIndex: axisIdx, start: 42, end: 100, bottom: 6, height: 18 },
    ],
    series,
    // 供 applyBounds 定位权益曲线的轴（私有字段，ECharts 忽略未知 key）
    __eqIdx: eqIdx, __nAxes: yAxes.length,
  } as any
}
