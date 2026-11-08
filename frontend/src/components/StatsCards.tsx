import { useStore, resultFor, selectionKey } from '../store'
import { Metrics, RunResultRow } from '../types'

const pct = (v: number | null | undefined, d = 2) => (v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(d) + '%')
const fnum = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' | 'warn' }) {
  return (
    <div className={'kpi' + (tone ? ' ' + tone : '')}>
      <div className="kpi-v">{value}</div>
      <div className="kpi-l">{label}</div>
    </div>
  )
}

function cardCls(m: Metrics) {
  if (m.expectancy_pct <= 0) return ' down'
  if (m.total_pct <= 0) return ' warn'
  return ' up'
}

export default function StatsCards() {
  const run = useStore(s => s.run)
  const runKey = useStore(s => s.runKey)
  const activeId = useStore(s => s.activeId)
  const activeCode = useStore(s => s.activeCode)
  const optimize = useStore(s => s.optimize)
  const activeDs = useStore(s => s.activeDs)
  const selected = useStore(s => s.selected)
  const globalParams = useStore(s => s.globalParams)

  const active = resultFor(run, activeId, activeCode)
  const m1 = resultFor(run, 'M1', activeCode)
  const dp = resultFor(run, 'M2', activeCode)

  const verdict = optimize && optimize.model_id === activeId ? optimize : null
  // 参数/数据集改了但没重跑：旧结果不能冒充新口径（A05）
  const stale = !!run && !!runKey && runKey !== selectionKey(activeDs, selected, globalParams)

  return (
    <div className="stats">
      {stale && (
        <div className="stale-hint">
          参数或数据集已变更，下方结果仍对应旧配置 —— 请重新运行回测。
        </div>
      )}
      {active ? (
        <div className={'stat-card' + cardCls(active.metrics)}>
          <div className="stat-title">
            <span className="dot" style={{ background: active.color }} /> {active.name}
          </div>
          <div className="big">{pct(active.metrics.total_pct)}</div>
          <div className="sub">总收益 · {active.metrics.n_trades} 笔</div>
          {active.final?.holding && (
            <div className="sub" title="期末未平仓，按收盘价盯市计入权益（非卖出实现）">
              期末仍持仓 · 浮盈 {pct(active.final.unrealized_pct)}
            </div>
          )}
          <div className="kpi-row">
            <Metric label="单笔期望" value={pct(active.metrics.expectancy_pct)} tone={active.metrics.expectancy_pct > 0 ? 'up' : 'down'} />
            <Metric label="胜率" value={fnum(active.metrics.win_rate) + '%'} />
            <Metric label="盈亏比" value={fnum(active.metrics.profit_factor)} />
            <Metric label="最大回撤" value={pct(active.metrics.max_drawdown_pct)} tone="down" />
            <Metric label="暴露度" value={fnum(active.metrics.exposure_pct) + '%'} />
            <Metric label="平均持仓" value={active.metrics.avg_hold_days + '天'} />
            <Metric label="夏普" value={fnum(active.metrics.sharpe)} />
            <Metric label="Calmar" value={fnum(active.metrics.calmar)} />
          </div>
        </div>
      ) : (
        <div className="stat-card empty">尚未回测 · 勾选模型后点「运行回测」</div>
      )}

      <div className="baseline">
        <div className="bcard">
          <div className="bl">买入持有（同口径）</div>
          <div className="bv">{m1 ? pct(m1.metrics.total_pct) : '—'}</div>
        </div>
        <div className="bcard dp">
          <div className="bl">DP 最优上界 🚫 不可交易</div>
          <div className="bv">{dp ? pct(dp.metrics.total_pct) : '—'}</div>
          <div className="bnote">事后最优路径，仅作空间参考</div>
        </div>
      </div>

      {verdict && (
        <div className={'verdict-card ' + verdict.verdict}>
          <div className="v-title">过拟合诊断：{verdict.verdict === 'red' ? '🔴 不可用' : verdict.verdict === 'yellow' ? '🟡 需谨慎' : '🟢 可用'}</div>
          <div className="v-row"><span>训练段最优</span><span>{verdict.best ? pct(verdict.best.metrics.expectancy_pct) + ' 期望' : '—'}</span></div>
          <div className="v-row"><span>诚实口径（测试段）</span><span>{verdict.honest ? pct(verdict.honest.expectancy_pct) + ' 期望' : '—'}</span></div>
          <div className="v-row"><span>乐观偏差</span><span>{pct(verdict.optimistic_bias)}</span></div>
          <div className="v-row"><span>迁移损失</span><span>{pct(verdict.transfer_loss)}</span></div>
          <div className="v-row"><span>同期买入持有</span><span>{pct(verdict.bh_test)}</span></div>
        </div>
      )}
      <div className="note">
        口径说明：买入持有与策略同口径（T+1 次日开盘买入，含佣金/印花税/过户费）。
        DP 上界为事后最优路径（收盘价、可反复进出），仅用于判断「还有多少空间」，不可作为策略。
      </div>
    </div>
  )
}
