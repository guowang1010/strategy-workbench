import {
  Bar, CodeRange, CrawlResult, Dataset, ExperimentDetail, ExperimentList, JobRecord, ModelSpec,
  OptimizeResponse, PlanFill, PlanLast, PlanRecordDetail, PlanRecordList, PlanResponse,
  RunResponse, SummaryResponse, RuleMeta,
} from './types'

const BASE = '/api'

/** 把后端各种错误形态（ok/error、HTTPException.detail、pydantic 数组）统一成可读文本。 */
function errorText(body: any, status: number): string {
  const raw = body?.error ?? body?.detail
  if (typeof raw === 'string' && raw.trim()) return raw
  if (Array.isArray(raw) && raw.length) {
    // pydantic 校验错误：[{loc, msg, type}]
    return raw.map((e: any) => `${(e.loc || []).join('.') || '参数'}: ${e.msg || e.type || ''}`).join('；')
  }
  if (status === 404) return '资源不存在'
  if (status === 422) return '参数校验失败'
  if (status >= 500) return `服务器错误（HTTP ${status}）`
  return `请求失败（HTTP ${status}）`
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  let r: Response
  try {
    r = await fetch(url, init)
  } catch (e: any) {
    throw new Error('无法连接后端服务，请确认已启动（' + (e?.message || 'network error') + '）')
  }
  const text = await r.text()
  let body: any = null
  if (text) {
    try { body = JSON.parse(text) } catch { body = null }
  }
  if (!r.ok) throw new Error(errorText(body, r.status))
  if (!body || body.ok !== true) throw new Error(errorText(body, r.status))
  return body.data as T
}

async function jget<T>(url: string): Promise<T> {
  return call<T>(url)
}

async function jpost<T>(url: string, body: any): Promise<T> {
  return call<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

async function jdel<T>(url: string): Promise<T> {
  return call<T>(url, { method: 'DELETE' })
}

export const api = {
  models: () => jget<ModelSpec[]>('/api/models'),
  ruleMeta: () => jget<RuleMeta>('/api/models/rule-meta'),
  datasets: () => jget<Dataset[]>('/api/datasets'),
  bars: (dsId: number, code: string) =>
    jget<{ dataset: Dataset; code: string; bars: Bar[] }>(
      `/api/datasets/${dsId}/bars?code=${encodeURIComponent(code)}`),
  seed: (path: string, name?: string) =>
    jpost<{ dataset_id: number }>('/api/datasets/seed', { path, name }),
  crawl: (req: { codes: string[]; start?: string; end?: string; days?: number; name?: string; merge_into?: number }) =>
    jpost<CrawlResult>('/api/datasets/crawl', req),
  codeRanges: (ds: number) => jget<CodeRange[]>(`/api/datasets/${ds}/codes`),
  run: (req: any) => jpost<RunResponse>('/api/runs', req),
  submitTiers: (req: any) => jpost<JobRecord>('/api/jobs/tiers', req),
  optimize: (req: any) => jpost<OptimizeResponse>('/api/optimize', req),
  plan: (req: any) => jpost<PlanResponse>('/api/plan', req),
  // 计划跟踪（BUY-12）：生成即落库，之后只追加成交、只读快照
  savePlan: (req: any) => jpost<PlanRecordDetail & { record_id: number; reused: boolean; plan: PlanResponse }>(
    '/api/plans', req),
  plans: (params?: { code?: string; model_id?: string; lifecycle?: string; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.code) q.set('code', params.code)
    if (params?.model_id) q.set('model_id', params.model_id)
    if (params?.lifecycle) q.set('lifecycle', params.lifecycle)
    if (params?.limit) q.set('limit', String(params.limit))
    const s = q.toString()
    return jget<PlanRecordList>(`/api/plans${s ? '?' + s : ''}`)
  },
  planRecord: (id: number) => jget<PlanRecordDetail>(`/api/plans/${id}`),
  // 刷新恢复：先按「股票 + 模型」找，找不到后端会退到该股票最近一条
  lastPlan: (params: { code: string; model_id?: string }) =>
    jget<PlanLast>(`/api/plans/last?code=${encodeURIComponent(params.code)}`
      + (params.model_id ? `&model_id=${encodeURIComponent(params.model_id)}` : '')),
  // 未成交续期：用最新行情重新生成，旧记录只加 superseded_by 关联
  rolloverPlan: (id: number) =>
    jpost<PlanRecordDetail & {
      record_id: number; reused: boolean; unchanged: boolean
      renewed_from: number; plan: PlanResponse; note: string
    }>(`/api/plans/${id}/rollover`, {}),
  addFill: (id: number, body: { side: string; qty: number; price: number; fees?: number;
                                trade_date: string; note?: string }) =>
    jpost<PlanRecordDetail & { fill: PlanFill }>(`/api/plans/${id}/fills`, body),
  deleteFill: (id: number, fillId: number) =>
    jdel<PlanRecordDetail>(`/api/plans/${id}/fills/${fillId}`),
  cancelPlan: (id: number, reason: string) =>
    jpost<PlanRecordDetail>(`/api/plans/${id}/cancel`, { reason }),
  reopenPlan: (id: number) => jpost<PlanRecordDetail>(`/api/plans/${id}/reopen`, {}),
  deletePlan: (id: number) => jdel<{ deleted: number }>(`/api/plans/${id}`),
  summary: (req: any) => jpost<SummaryResponse>('/api/summary', req),
  // 长任务：提交返回 202 + job，前端轮询进度，可取消
  submitOptimizeJob: (req: any) => jpost<JobRecord>('/api/jobs/optimize', req),
  submitSummaryJob: (req: any) => jpost<JobRecord>('/api/jobs/summary', req),
  job: (id: string) => jget<JobRecord>(`/api/jobs/${encodeURIComponent(id)}`),
  cancelJob: (id: string) => jpost<JobRecord>(`/api/jobs/${encodeURIComponent(id)}/cancel`, {}),
  jobs: () => jget<{ jobs: JobRecord[]; stats: any }>('/api/jobs'),
  // 实验记录（A11）
  experiments: (params?: { kind?: string; dataset_id?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.kind) q.set('kind', params.kind)
    if (params?.dataset_id) q.set('dataset_id', String(params.dataset_id))
    if (params?.limit) q.set('limit', String(params.limit))
    const s = q.toString()
    return jget<ExperimentList>(`/api/experiments${s ? '?' + s : ''}`)
  },
  experiment: (id: number) => jget<ExperimentDetail>(`/api/experiments/${id}`),
  replayExperiment: (id: number) => jget<{ kind: string; body: any }>(`/api/experiments/${id}/replay`),
  deleteExperiment: (id: number) => jdel<{ deleted: number }>(`/api/experiments/${id}`),
  exportUrl: (runId: string, fmt: string) => `${BASE}/export`,
}
