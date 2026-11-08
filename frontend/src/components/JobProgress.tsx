import { useStore } from '../store'

/**
 * 长任务进度条（寻优 / 跨股票汇总）。
 * 任务由后端 job 表驱动：这里只渲染进度 + 取消按钮，轮询在 store 里做。
 */
export default function JobProgress({ kind }: { kind: 'optimize' | 'summary' }) {
  const job = useStore(s => s.job)
  const cancelJob = useStore(s => s.cancelJob)
  if (!job || job.kind !== kind) return null
  if (job.status !== 'running' && job.status !== 'queued') return null

  const pct = Math.round((job.progress || 0) * 100)
  const cancelling = !!job.cancel_requested
  return (
    <div className="job-bar">
      <div className="job-track" title={`任务 ${job.id}`}>
        <div className={'job-fill' + (cancelling ? ' cancelling' : '')} style={{ width: `${pct}%` }} />
      </div>
      <span className="job-text">
        {cancelling ? '取消中…' : (job.message || (job.status === 'queued' ? '排队中' : '计算中'))} · {pct}%
      </span>
      <button className="mini" disabled={cancelling} onClick={() => cancelJob()}>
        {cancelling ? '取消中…' : '取消'}
      </button>
    </div>
  )
}
