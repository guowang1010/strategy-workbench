import { useEffect, useState } from 'react'

interface Props { onClose: () => void }

const GROUP = '1131751562'

export default function AboutDialog({ onClose }: Props) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    const closeOnEscape = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  const copyGroup = async () => {
    try {
      await navigator.clipboard.writeText(GROUP)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch { setCopied(false) }
  }

  return (
    <div className="modal-mask about-mask" onClick={onClose}>
      <section className="modal about-modal" role="dialog" aria-modal="true" aria-labelledby="about-title"
        onClick={e => e.stopPropagation()}>
        <button className="about-close" aria-label="关闭关于窗口" onClick={onClose}>×</button>

        <div className="about-hero">
          <div className="about-logo" aria-hidden="true"><span>↗</span></div>
          <div>
            <div className="about-eyebrow">SWING STRATEGY WORKBENCH</div>
            <h2 id="about-title">波段策略回测工作台</h2>
            <p>把策略构思、历史验证、参数研究和下一笔交易计划，放进同一个清晰、可复现的研究流程。</p>
          </div>
        </div>

        <div className="about-content">
          <div className="about-features">
            <article><b>策略回测</b><span>统一交易成本与执行口径，查看收益、回撤、胜率和完整交易明细。</span></article>
            <article><b>参数研究</b><span>网格寻优、样本外切分与跨股票汇总，帮助识别稳健性而非只看最优值。</span></article>
            <article><b>计划跟踪</b><span>从信号确认到成交复盘，保留可追溯的计划快照和实际执行记录。</span></article>
          </div>

          <div className="about-community">
            <div>
              <span className="about-community-label">交流与反馈</span>
              <strong>加入项目讨论 QQ 群</strong>
              <small>交流策略研究、使用经验，反馈问题与改进建议</small>
            </div>
            <button className="qq-group" onClick={copyGroup} title="复制 QQ 群号">
              <span className="qq-mark">Q</span>
              <span><small>QQ群</small><b>{GROUP}</b></span>
              <em>{copied ? '已复制 ✓' : '复制群号'}</em>
            </button>
          </div>

          <div className="about-meta">
            <span>FastAPI · React · TypeScript · SQLite</span>
            <span>仅供策略研究与教学，不构成投资建议</span>
          </div>
        </div>
      </section>
    </div>
  )
}
