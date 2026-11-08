import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { useStore, selectionKey } from '../store'
import { OptimizeResponse } from '../types'
import JobProgress from './JobProgress'

const pct = (v: number | null | undefined, d = 2) => (v == null ? '—' : (v >= 0 ? '+' : '') + (v as number).toFixed(d) + '%')

function Heatmap({ opt, theme }: { opt: OptimizeResponse; theme: 'light' | 'dark' }) {
  const el = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  useEffect(() => {
    if (!el.current) return
    chart.current = echarts.init(el.current)
    return () => { chart.current?.dispose(); chart.current = null }
  }, [])
  useEffect(() => {
    if (!chart.current) return
    const tps = opt.heatmap.tp_values, sls = opt.heatmap.sl_values, z = opt.heatmap.z
    const data: any[] = []
    let mx = 0
    for (let r = 0; r < sls.length; r++)
      for (let c = 0; c < tps.length; c++) {
        const v = z[r]?.[c]
        if (v == null) { data.push([c, r, '-']); continue }
        mx = Math.max(mx, Math.abs(v))
        data.push([c, r, v])
      }
    chart.current!.setOption({
      animation: false,
      tooltip: { position: 'top', formatter: (p: any) => `止盈 ${tps[p.value[0]]}% / 止损 ${sls[p.value[1]]}%<br/>期望 ${typeof p.value[2] === 'number' ? p.value[2].toFixed(2) + '%' : '样本不足'}` },
      grid: { left: 50, right: 12, top: 12, bottom: 40 },
      xAxis: { type: 'category', data: tps, name: '止盈%', axisLabel: { color: theme === 'dark' ? '#c9d1d9' : '#333' } },
      yAxis: { type: 'category', data: sls, name: '止损%', axisLabel: { color: theme === 'dark' ? '#c9d1d9' : '#333' } },
      visualMap: { min: -mx, max: mx, calculable: true, orient: 'horizontal', left: 'center', bottom: 0,
        inRange: { color: ['#d93f3a', '#f6f6f6', '#1f9d6b'] }, textStyle: { color: theme === 'dark' ? '#c9d1d9' : '#333' } },
      series: [{ type: 'heatmap', data, label: { show: false }, emphasis: { itemStyle: { borderColor: '#333', borderWidth: 1 } } }],
    })
  }, [opt, theme])
  return <div ref={el} style={{ width: '100%', height: 260 }} />
}

export default function OptimizePanel() {
  const optimize = useStore(s => s.optimize)
  const optKey = useStore(s => s.optKey)
  const activeDs = useStore(s => s.activeDs)
  const globalParams = useStore(s => s.globalParams)
  const runOptimize = useStore(s => s.runOptimize)
  const optimizeModelId = useStore(s => s.optimizeModelId)
  const selected = useStore(s => s.selected)
  const setParam = useStore(s => s.setParam)
  const setActive = useStore(s => s.setActive)
  const loading = useStore(s => s.loading)
  const theme = useStore(s => s.theme)
  const wfFolds = useStore(s => s.wfFolds)
  const setWfFolds = useStore(s => s.setWfFolds)

  if (!optimize) {
    return (
      <div className="opt-empty">
        <p>对当前股票做参数寻优（训练段选参 → 测试段验证），给出过拟合诊断。</p>
        <div className="wf-toggle">
          <label>
            滚动走查折数
            <select value={wfFolds} onChange={e => setWfFolds(Number(e.target.value))}>
              <option value={0}>关闭</option>
              <option value={2}>2 折</option>
              <option value={3}>3 折</option>
              <option value={4}>4 折</option>
            </select>
          </label>
          <span className="hint">K 折滚动：训练窗扩张，逐折「训练选参 → 紧随后测试段执行」，比单次切分更能暴露参数不稳定。开启后耗时成倍增加。</span>
        </div>
        <JobProgress kind="optimize" />
        <button disabled={loading} onClick={() => runOptimize(optimizeModelId)}>
          运行寻优（{optimizeModelId} · 测试段 61 日{wfFolds > 0 ? ` · ${wfFolds} 折走查` : ''}）
        </button>
      </div>
    )
  }

  const fill = () => {
    const best = optimize.best
    if (!best) return
    setActive(optimize.model_id)
    for (const [k, v] of Object.entries(best.params)) {
      if (v == null) continue
      setParam(optimize.model_id, k, v)
    }
  }

  const stale = !!optKey && optKey !== selectionKey(
    activeDs, selected.filter(m => m.id === optimize.model_id), globalParams)

  return (
    <div className="opt">
      {stale && (
        <div className="stale-hint">参数或数据集已变更，本页诊断对应旧配置 —— 请重新寻优。</div>
      )}
      <JobProgress kind="optimize" />
      <div className={'opt-verdict ' + optimize.verdict}>
        结论：{optimize.verdict === 'red' ? '🔴 不可用（样本外期望≤0）' : optimize.verdict === 'yellow' ? '🟡 需谨慎（期望>0 但总收益≤0）' : '🟢 可用'}
      </div>
      <div className="opt-cals">
        <div className="cal">
          <div className="cal-h">诚实口径（训练段选参→测试段执行）</div>
          <div>期望 {pct(optimize.honest?.expectancy_pct)} · 总收益 {pct(optimize.honest?.total_pct)} · {optimize.honest?.n_trades} 笔</div>
        </div>
        <div className="cal">
          <div className="cal-h">乐观口径（全样本最优参数→测试段）</div>
          <div>期望 {pct(optimize.biased?.expectancy_pct)} · 偏差 {pct(optimize.optimistic_bias)}</div>
        </div>
        <div className="cal">
          <div className="cal-h">Oracle（测试段事后最优，不可交易）</div>
          <div>期望 {pct(optimize.oracle?.expectancy_pct)} · 总收益 {pct(optimize.oracle?.total_pct)}</div>
        </div>
      </div>
      <div className="opt-best">
        训练段 {optimize.train_range[0]} ~ {optimize.train_range[1]} ·
        测试段 {optimize.test_range[0]} ~ {optimize.test_range[1]}
        （预热 {optimize.warmup_bars ?? 60} / 需要 {optimize.warmup_need ?? 60} 根，
        评分自 {optimize.scorable_from || optimize.test_range[0]} 起）
      </div>
      {optimize.warmup_short && (
        <div className="warmup-warn">
          训练段只有 {optimize.warmup_bars} 根，不足 {optimize.warmup_need} 根的指标预热：
          窗口较长的指标（如 MA60）在测试段前几天仍不可用，本期结果偏保守，建议加长样本或缩短测试段。
        </div>
      )}
      <div className="opt-best">
        训练段最优参数：{optimize.best ? JSON.stringify(optimize.best.params) : '—'} ·
        全样本最优：{optimize.best_all ? JSON.stringify(optimize.best_all.params) : '—'}
        <button className="mini" onClick={fill}>回填最优参数</button>
        <button className="mini" disabled={loading} onClick={() => runOptimize(optimize.model_id)}>重新寻优</button>
      </div>
      <div className="opt-heat-title">训练段单笔期望热力图（止盈 × 止损，固定其余参数）</div>
      <Heatmap opt={optimize} theme={theme} />
      {optimize.walk_forward && optimize.walk_forward.folds > 0 && (
        <>
          <div className="opt-heat-title">K 折滚动走查（训练窗扩张 · 诚实口径 vs 买入持有）</div>
          <table className="wf-table">
            <thead>
              <tr>
                <th>折</th><th>训练段</th><th>测试段</th><th>预热</th><th>训练段最优参数</th>
                <th>期望%</th><th>总收益%</th><th>笔数</th><th>买入持有%</th>
              </tr>
            </thead>
            <tbody>
              {optimize.walk_forward.rows.map(r => (
                <tr key={r.fold} className={r.expectancy_pct == null ? '' : r.expectancy_pct > 0 ? 'pos' : 'neg'}>
                  <td className="center">{r.fold}</td>
                  <td>{r.train_range[0]} ~ {r.train_range[1]}</td>
                  <td>{r.test_range[0]} ~ {r.test_range[1]}</td>
                  <td className="center">{r.warmup_bars ? `${r.warmup_bars} 根${r.warmup_short ? ' ⚠' : ''}` : '—'}</td>
                  <td>{r.params ? JSON.stringify(r.params) : '—'}</td>
                  <td className="center">{pct(r.expectancy_pct)}</td>
                  <td className="center">{pct(r.total_pct)}</td>
                  <td className="center">{r.n_trades ?? '—'}</td>
                  <td className="center">{pct(r.bh_total_pct)}</td>
                </tr>
              ))}
              <tr className="wf-summary">
                <td colSpan={5}>均值 / 为正折数</td>
                <td className="center">{pct(optimize.walk_forward.mean_expectancy)}</td>
                <td className="center">{pct(optimize.walk_forward.mean_total)}</td>
                <td className="center">{optimize.walk_forward.pos_folds}/{optimize.walk_forward.folds}</td>
                <td className="center">{pct(optimize.walk_forward.mean_bh)}</td>
              </tr>
            </tbody>
          </table>
          <div className="note">
            滚动走查 = 逐折在训练段选参、紧随的 {optimize.walk_forward.test_days} 日测试段执行。
            测试段执行带 <b>{optimize.walk_forward.warmup_bars ?? 60} 根</b>训练尾部预热（A10 切分协议）：
            指标用切分边界<i>之前</i>的已知数据算起，交易与评分只从测试段首日开始；
            <b>持仓在切分处重启</b>（不继承训练段仓位与资金曲线），每折是独立的一笔启动资金。
            若多数折期望为负或明显跑输买入持有，说明参数在时间上不稳定，勿用于实盘决策。
          </div>
        </>
      )}
      <div className="note">
        迁移损失 = Oracle期望 − 诚实期望；乐观偏差 = 乐观期望 − 诚实期望。若诚实口径单笔期望 ≤ 0，直接判红。
      </div>
    </div>
  )
}
