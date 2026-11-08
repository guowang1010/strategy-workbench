import { create } from 'zustand'
import { api } from './api'
import {
  Bar, CodeRange, CrawlResult, Dataset, JobRecord, ModelSpec, OptimizeResponse, PlanAccountInput,
  PlanRecordDetail, PlanRecordSummary, PlanResponse, PlanRulesInput, RunResponse,
  SummaryResponse, RuleMeta,
} from './types'

export interface SelModel {
  id: string
  params: Record<string, any>
}

/** 寻优的可选覆盖项：用于「按实验记录重跑」时还原当时的搜索空间与切分（A11）。 */
export interface OptimizeOpts {
  space?: Record<string, any>
  test_days?: number
  objective?: 'expectancy' | 'total' | 'sharpe'
  folds?: number
  base_params?: Record<string, any>
}

// 请求序号：同一类请求只认最后一次的响应，防止「A 慢 B 快」时迟到响应覆盖新选择
const seq = { bars: 0, run: 0, opt: 0, plan: 0, sum: 0 }

const POLL_MS = 600

/** 轮询后台任务直到终态；alive() 为假（切换了选择）时立即停止，返回 null。 */
async function pollJob(
  jobId: string, alive: () => boolean, onTick: (j: JobRecord) => void,
): Promise<JobRecord | null> {
  for (;;) {
    if (!alive()) return null
    let rec: JobRecord
    try {
      rec = await api.job(jobId)
    } catch {
      await new Promise(r => setTimeout(r, POLL_MS * 2))
      continue                     // 轮询抖动不致命，下次再试
    }
    if (!alive()) return null
    onTick(rec)
    if (rec.status !== 'queued' && rec.status !== 'running') return rec
    await new Promise(r => setTimeout(r, POLL_MS))
  }
}

function jobText(j: JobRecord): string {
  const pct = Math.round((j.progress || 0) * 100)
  return `${j.message || '计算中'} ${pct}%`
}

/** 当前选择（数据集 + 模型参数 + 全局参数）的稳定指纹，用于判断结果是否过期。 */
export function selectionKey(dsId: number | null, selected: SelModel[], gp: Record<string, number>) {
  const models = [...selected].sort((a, b) => a.id.localeCompare(b.id)).map(m => {
    const keys = Object.keys(m.params).sort()
    return m.id + '{' + keys.map(k => `${k}:${JSON.stringify(m.params[k])}`).join(',') + '}'
  }).join('|')
  const g = Object.keys(gp).sort().map(k => `${k}=${gp[k]}`).join(',')
  return `${dsId ?? '-'}#${models}#${g}`
}

interface State {
  models: ModelSpec[]
  ruleMeta: RuleMeta | null
  datasets: Dataset[]
  activeDs: number | null
  codes: string[]
  codeNames: Record<string, string>
  /** 每只股票在当前数据集内的日 K 范围（下拉展示用） */
  codeRanges: CodeRange[]
  activeCode: string | null
  bars: Bar[]
  globalParams: Record<string, number>
  selected: SelModel[]
  activeId: string
  run: RunResponse | null
  runKey: string
  optimize: OptimizeResponse | null
  optimizeModelId: string
  optKey: string
  plan: PlanResponse | null
  /** 计划生成后若参数/账户/模型发生变化，旧计划必须显式失效（BUY-10） */
  planStale: string | null
  planAccount: PlanAccountInput
  planRules: PlanRulesInput
  planResearch: boolean
  /** 跟踪列表（计划落库后的记录摘要） */
  planRecords: PlanRecordSummary[]
  /** 当前选中的跟踪详情（含成交与复盘） */
  planTrack: PlanRecordDetail | null
  /** 当前展示的计划已落库的记录 id（null = 尚未保存） */
  planSavedId: number | null
  /** 当前计划是从跟踪记录恢复的（刷新后），参数/账户可能已与面板不同 */
  planRestored: boolean
  summary: SummaryResponse | null
  job: JobRecord | null
  includeOos: boolean
  wfFolds: number
  theme: 'light' | 'dark'
  loading: boolean
  msg: string
  /** 实验记录版本号：每次回测/寻优完成 +1，实验面板据此自动刷新（A11） */
  expTick: number

  init: () => Promise<void>
  refreshDatasets: () => Promise<void>
  selectDataset: (id: number) => Promise<void>
  crawlStocks: (req: { codes: string[]; start?: string; end?: string; days?: number; name?: string; merge_into?: number }) =>
    Promise<{ ok: true; data: CrawlResult } | { ok: false; error: string }>
  selectCode: (code: string) => Promise<void>
  toggleModel: (id: string, on: boolean) => void
  setParam: (id: string, key: string, val: any) => void
  setActive: (id: string) => void
  setGlobal: (key: string, val: number) => void
  runBacktest: (opts?: { codes?: string[]; tiers?: boolean }) => Promise<void>
  runOptimize: (modelId: string, opts?: OptimizeOpts) => Promise<void>
  replayExperiment: (id: number) => Promise<void>
  cancelJob: () => Promise<void>
  runPlan: () => Promise<void>
  setPlanAccount: (patch: Partial<PlanAccountInput>) => void
  setPlanRules: (patch: Partial<PlanRulesInput>) => void
  setPlanResearch: (v: boolean) => void
  savePlan: () => Promise<void>
  restorePlan: () => Promise<void>
  rolloverPlan: () => Promise<void>
  loadPlans: () => Promise<void>
  openRecord: (id: number) => Promise<void>
  closeRecord: () => void
  addFill: (body: { side: string; qty: number; price: number; fees?: number;
                    trade_date: string; note?: string }) => Promise<void>
  deleteFill: (fillId: number) => Promise<void>
  cancelPlan: (reason: string) => Promise<void>
  reopenPlan: () => Promise<void>
  deletePlan: (id: number) => Promise<void>
  setIncludeOos: (v: boolean) => void
  setWfFolds: (v: number) => void
  runSummary: () => Promise<void>
  toggleTheme: () => void
  setMsg: (m: string) => void
}

const DEFAULT_PLAN_ACCOUNT: PlanAccountInput = {
  mode: 'strategy_simulated', cash: null, shares: null, sellable: null,
  cost_price: null, snapshot_at: null, allow_addon: false,
}
const DEFAULT_PLAN_RULES: PlanRulesInput = {
  max_price: null, max_deviation_pct: null, max_position_pct: 100,
  risk_pct: null, follow_from: 'allow_existing', day_only: true,
}

const DEFAULT_GLOBAL = {
  capital: 100000, commission: 0.00025, min_commission: 5,
  stamp_duty: 0.0005, transfer_fee: 0.00001, slippage_pct: 0,
}

/** 丢弃当前后台任务：通知后端取消，避免切走后仍在后台空跑。 */
function abandonJob(j: JobRecord | null) {
  if (j && (j.status === 'queued' || j.status === 'running')) {
    api.cancelJob(j.id).catch(() => { /* 已被别的操作收尾，忽略 */ })
  }
}

export const useStore = create<State>((set, get) => ({
  models: [], ruleMeta: null, datasets: [], activeDs: null, codes: [], codeNames: {}, codeRanges: [], activeCode: null, bars: [],
  globalParams: { ...DEFAULT_GLOBAL },
  selected: [
    { id: 'M1', params: {} },
    { id: 'M4', params: { tp: 9, sl: 12, min_hold: 3, max_hold: 12, ma_n: 20 } },
    { id: 'M2', params: { min_hold: 3 } },
  ],
  activeId: 'M4',
  run: null, runKey: '', optimize: null, optimizeModelId: 'M4', optKey: '', plan: null, summary: null,
  planStale: null, planAccount: { ...DEFAULT_PLAN_ACCOUNT }, planRules: { ...DEFAULT_PLAN_RULES }, planResearch: false,
  planRecords: [], planTrack: null, planSavedId: null, planRestored: false,
  job: null, includeOos: false, wfFolds: 0,
  theme: 'light', loading: false, msg: '', expTick: 0,

  async init() {
    const models = await api.models()
    const datasets = await api.datasets()
    let ruleMeta: RuleMeta | null = null
    try { ruleMeta = await api.ruleMeta() } catch { ruleMeta = null }
    let theme: 'light' | 'dark' = 'light'
    try { theme = localStorage.getItem('wb.theme') === 'dark' ? 'dark' : 'light' } catch { /* 忽略 */ }
    set({ models, datasets, ruleMeta, theme })
    api.plans({ limit: 100 }).then(d => set({ planRecords: d.items })).catch(() => { /* 后端未就绪时忽略 */ })
    if (datasets.length) {
      await get().selectDataset(datasets[0].id)
    }
    // 默认勾选的模型若无对应 spec（理论上都有），过滤掉
    set(s => ({ selected: s.selected.filter(m => models.find(x => x.id === m.id)) }))
  },

  async refreshDatasets() {
    const datasets = await api.datasets()
    set({ datasets })
  },

  async selectDataset(id) {
    const ds = get().datasets.find(d => d.id === id)
    const codes: string[] = ds ? JSON.parse(ds.codes) : []
    let codeNames: Record<string, string> = {}
    if (ds) {
      try { codeNames = JSON.parse(ds.names || '{}') } catch { codeNames = {} }
    }
    // 切数据集必须让旧结果失效：不同数据集的同一代码不能共用结果（A05）
    abandonJob(get().job)
    seq.run++; seq.opt++; seq.plan++; seq.sum++
    set({
      activeDs: id, codes, codeNames, codeRanges: [], activeCode: codes[0] || null,
      bars: [], run: null, runKey: '', optimize: null, optKey: '', plan: null,
      summary: null, job: null, msg: '',
    })
    // 个股日 K 范围异步补上（失败不影响主流程；快速切数据集只认最后一次）
    api.codeRanges(id)
      .then(rs => { if (get().activeDs === id) set({ codeRanges: rs }) })
      .catch(() => { /* 区间展示是增强信息，拿不到就退化为只显示名称 */ })
    if (codes[0]) await get().selectCode(codes[0])
  },

  async selectCode(code) {
    const dsId = get().activeDs
    if (!dsId) return
    const my = ++seq.bars               // 只认最后一次代码切换
    abandonJob(get().job)
    seq.opt++; seq.plan++
    // 先清空旧行情，避免慢请求或失败时把上一只股票的数据挂在新代码名下。
    set({ activeCode: code, bars: [], optimize: null, optKey: '', plan: null, planStale: null,
          planSavedId: null, planTrack: null, planRestored: false, job: null, msg: '' })
    try {
      const { bars } = await api.bars(dsId, code)
      if (my !== seq.bars || get().activeDs !== dsId) return
      set({ bars })
    } catch (e) {
      if (my === seq.bars && get().activeDs === dsId) {
        set({ msg: `行情加载失败：${e instanceof Error ? e.message : '未知错误'}` })
      }
      return
    }
    // 该股票以前保存过计划就恢复出来（BUY-10）：刷新页面不等于从零开始
    await get().restorePlan()
  },

  toggleModel(id, on) {
    const spec = get().models.find(m => m.id === id)
    set(s => {
      const exists = s.selected.find(m => m.id === id)
      if (on && !exists && spec) {
        return { selected: [...s.selected, { id, params: { ...spec.defaults } }] }
      }
      if (!on && exists) {
        return { selected: s.selected.filter(m => m.id !== id) }
      }
      return {}
    })
  },

  setParam(id, key, val) {
    set(s => ({
      selected: s.selected.map(m =>
        m.id === id ? { ...m, params: { ...m.params, [key]: val } } : m),
      planStale: s.plan && s.plan.identity?.model_id === id ? '策略参数已修改，需重新生成' : s.planStale,
    }))
  },

  setActive(id) {
    set(s => ({
      activeId: id,
      run: s.run ? { ...s.run, viewTier: null } : null,
      planStale: s.plan && s.plan.identity?.model_id !== id ? '已切换到其他模型，该计划不再对应当前展示' : s.planStale,
    }))
  },

  setGlobal(key, val) {
    set(s => ({
      globalParams: { ...s.globalParams, [key]: val },
      planStale: s.plan ? `执行配置（${key}）已修改，需重新生成` : s.planStale,
    }))
  },

  setPlanAccount(patch) {
    set(s => ({ planAccount: { ...s.planAccount, ...patch },
                planStale: s.plan ? '账户快照已修改，需重新生成' : s.planStale }))
  },

  setPlanRules(patch) {
    set(s => ({ planRules: { ...s.planRules, ...patch },
                planStale: s.plan ? '执行约束已修改，需重新生成' : s.planStale }))
  },

  setPlanResearch(v) {
    set(s => ({ planResearch: v,
                planStale: s.plan ? '研究模式切换，需重新生成' : s.planStale }))
  },

  async crawlStocks(req) {
    const codes = req.codes.filter(Boolean)
    if (!codes.length) { set({ msg: '请先填写股票代码' }); return { ok: false as const, error: '请先填写股票代码' } }
    set({ loading: true, msg: `正在拉取 ${codes.length} 只股票行情，请稍候…` })
    try {
      const r = await api.crawl({ ...req, codes })
      const datasets = await api.datasets()
      set({ datasets })
      const err = r.errors.length ? `（${r.errors.length} 只失败）` : ''
      if (r.merged) {
        const newCodes = r.new_codes?.length ? `，新加入 ${r.new_codes.length} 只` : ''
        set({
          loading: false,
          msg: `已合并到数据集 #${r.dataset_id}：更新 ${r.merged_rows} 行${newCodes} · ${r.window[0]}~${r.window[1]}${err}`,
        })
      } else {
        set({
          loading: false,
          msg: `已拉取 ${r.summaries.length} 只 / ${r.rows} 行 · ${r.window[0]}~${r.window[1]}${err}，已切到新数据集`,
        })
      }
      await get().selectDataset(r.dataset_id)
      return { ok: true as const, data: r }
    } catch (e: any) {
      const error = e?.message || '未知错误'
      set({ loading: false, msg: '拉取失败：' + error })
      // 返回原因给弹窗内展示——顶部提示被弹窗遮罩盖住，只让用户「看顶部」等于没说
      return { ok: false as const, error }
    }
  },

  async runBacktest(opts) {
    const { activeDs, activeCode, selected, globalParams } = get()
    const codes = opts?.codes?.length ? opts.codes : (activeCode ? [activeCode] : [])
    if (!activeDs || !codes.length || !selected.length) {
      set({ msg: '请先选择数据集 / 股票 / 模型' }); return
    }
    const my = ++seq.run
    set({ loading: true, msg: '回测中…' })
    try {
      const request = {
        dataset_id: activeDs, codes,
        models: selected, global_params: globalParams, include_tiers: !!opts?.tiers,
      }
      let run: RunResponse
      if (opts?.tiers) {
        const started = await api.submitTiers(request)
        if (my !== seq.run) { abandonJob(started); return }
        set({ job: started })
        const final = await pollJob(started.id, () => my === seq.run, j => set({ job: j, msg: jobText(j) }))
        if (!final || my !== seq.run) return
        if (final.status !== "done" || !final.result) throw new Error(final.error || "评估已取消")
        run = final.result as RunResponse
      } else {
        run = await api.run(request)
      }
      if (my !== seq.run) return      // 期间又发起了新回测，丢弃本次结果
      set({
        run, loading: false,
        runKey: selectionKey(activeDs, selected, globalParams),
        expTick: get().expTick + 1,
        msg: `完成：${run.results.length} 组，耗时 ${run.duration_ms}ms`,
      })
    } catch (e: any) {
      if (my === seq.run) set({ loading: false, msg: '回测失败：' + e.message })
    }
  },

  async runOptimize(modelId, opts) {
    const { activeDs, activeCode, wfFolds, globalParams, selected } = get()
    if (!activeDs || !activeCode) { set({ msg: '请先选择数据集 / 股票' }); return }
    const sel = selected.find(m => m.id === modelId)
    const my = ++seq.opt
    set({ loading: true, msg: '正在提交寻优任务…', job: null })
    try {
      const started = await api.submitOptimizeJob({
        dataset_id: activeDs, code: activeCode, model_id: modelId,
        test_days: opts?.test_days ?? 61,
        walk_forward_folds: opts?.folds ?? wfFolds,
        objective: opts?.objective,
        space: opts?.space,                   // 重放时还原当时的搜索空间（A11）
        global_params: globalParams,          // 与回测同一份执行配置（A01）
        // M9/M10 的规则树不在网格内，作为固定参数；重放时按记录里的来
        base_params: opts?.base_params ?? (sel ? sel.params : {}),
      })
      if (my !== seq.opt) { abandonJob(started); return }
      set({ job: started, msg: `寻优已排队（任务 ${started.id}）` })

      const final = await pollJob(started.id, () => my === seq.opt,
        j => set({ job: j, msg: jobText(j) }))
      if (!final || my !== seq.opt) return
      if (final.status === 'done' && final.result) {
        set({
          optimize: final.result as OptimizeResponse, optimizeModelId: modelId,
          loading: false, job: final,
          expTick: get().expTick + 1,
          msg: `寻优完成：${final.duration_ms || 0}ms`,
          optKey: selectionKey(activeDs, sel ? [sel] : [], globalParams),
        })
      } else if (final.status === 'cancelled') {
        set({ loading: false, job: final, msg: '寻优已取消' })
      } else {
        set({ loading: false, job: final, msg: '寻优失败：' + (final.error || '未知错误') })
      }
    } catch (e: any) {
      if (my === seq.opt) set({ loading: false, msg: '寻优失败：' + e.message })
    }
  },

  /** 按实验记录重跑（A11）：还原数据集、股票、执行配置与搜索空间，再走同一条执行链路。 */
  async replayExperiment(id) {
    let rec: { kind: string; body: any }
    try {
      rec = await api.replayExperiment(id)
    } catch (e: any) {
      set({ msg: '重放失败：' + e.message }); return
    }
    const { kind, body } = rec
    const dsId = Number(body.dataset_id)
    if (!get().datasets.find(d => d.id === dsId)) {
      set({ msg: '原实验所属数据集已不存在，无法重放' }); return
    }
    if (get().activeDs !== dsId) await get().selectDataset(dsId)
    if (body.global_params) {
      set(s => ({ globalParams: { ...s.globalParams, ...body.global_params } }))
    }
    if (kind === 'backtest') {
      const models: SelModel[] = (body.models || []).map((m: any) => ({ id: m.id, params: m.params || {} }))
      if (models.length) {
        set({
          selected: models,
          activeId: models.some(m => m.id === get().activeId) ? get().activeId : models[0].id,
        })
      }
      if (body.codes?.length) await get().selectCode(body.codes[0])
      await get().runBacktest({ codes: body.codes, tiers: !!body.include_tiers })
    } else {
      if (body.code) await get().selectCode(body.code)
      await get().runOptimize(body.model_id, {
        space: body.space, test_days: body.test_days, objective: body.objective,
        folds: body.folds, base_params: body.base_params,
      })
    }
  },

  async cancelJob() {
    const j = get().job
    if (!j || (j.status !== 'running' && j.status !== 'queued')) return
    set({ msg: '正在取消…' })
    try {
      const rec = await api.cancelJob(j.id)
      set(s => ({ job: { ...(s.job || rec), ...rec, cancel_requested: true }, msg: '已请求取消，等待当前组合跑完…' }))
    } catch (e: any) {
      set({ msg: '取消失败：' + e.message })
    }
  },

  async runPlan() {
    const { activeDs, activeCode, selected, activeId, globalParams,
            planAccount, planRules, planResearch } = get()
    if (!activeDs || !activeCode) { set({ msg: '请先选择数据集 / 股票' }); return }
    const sel = selected.find(m => m.id === activeId)
    if (!sel) { set({ msg: '请先勾选当前展示的模型' }); return }
    if (planAccount.mode === 'manual_actual' && (planAccount.cash == null || planAccount.shares == null)) {
      set({ msg: '手动账户需填写现金与持仓数量' }); return
    }
    const my = ++seq.plan
    set({ loading: true, msg: '生成下一笔计划…' })
    try {
      const plan = await api.plan({
        dataset_id: activeDs, code: activeCode, model_id: activeId,
        params: sel.params, global_params: globalParams,   // 与回测同一份执行配置
        account: planAccount, exec_rules: planRules, research: planResearch,
      })
      if (my !== seq.plan) return
      // 新计划 ≠ 已保存的那条：清掉跟踪选中态，避免把旧记录的复盘当成新计划的
      set({ plan, planStale: null, loading: false, msg: '下一笔计划已生成',
            planSavedId: null, planTrack: null, planRestored: false })
    } catch (e: any) {
      if (my === seq.plan) set({ loading: false, msg: '计划生成失败：' + e.message })
    }
  },

  async savePlan() {
    const { activeDs, activeCode, selected, activeId, globalParams,
            planAccount, planRules, planResearch } = get()
    if (!activeDs || !activeCode) { set({ msg: '请先选择数据集 / 股票' }); return }
    const sel = selected.find(m => m.id === activeId)
    if (!sel) { set({ msg: '请先勾选当前展示的模型' }); return }
    if (planAccount.mode === 'manual_actual' && (planAccount.cash == null || planAccount.shares == null)) {
      set({ msg: '手动账户需填写现金与持仓数量' }); return
    }
    set({ loading: true, msg: '保存计划…' })
    try {
      const d = await api.savePlan({
        dataset_id: activeDs, code: activeCode, model_id: activeId,
        params: sel.params, global_params: globalParams,
        account: planAccount, exec_rules: planRules, research: planResearch,
      })
      set(s => ({
        plan: d.plan, planStale: null, planSavedId: d.record_id, planRestored: false,
        planTrack: { record: d.record, review: d.review },
        planRecords: [d.record, ...s.planRecords.filter(r => r.id !== d.record_id)],
        loading: false,
        msg: d.reused ? `已更新跟踪记录 #${d.record_id}（未成交，复用原记录）`
                      : `已保存到跟踪列表 #${d.record_id}`,
      }))
    } catch (e: any) {
      set({ loading: false, msg: '保存失败：' + e.message })
    }
  },

  /**
   * 恢复该股票 + 该模型最近一次保存的计划（BUY-10）。
   * 只回填 plan 快照，不重新计算：面板上的参数/账户可能已经变了，所以用 planStale
   * 明确说出「这份计划依据哪天、当前行情到哪天」，而不是假装它是最新的。
   */
  async restorePlan() {
    const { activeCode, activeId } = get()
    if (!activeCode || get().plan) return
    try {
      const d = await api.lastPlan({ code: activeCode, model_id: activeId })
      if (!d.found || !d.record?.plan || get().plan) return
      const p = d.record.plan
      const bars = get().bars
      const latest = bars.length ? bars[bars.length - 1].date : null
      const asOf = p.context?.as_of
      let stale: string | null = null
      if (d.record.superseded_by) {
        stale = `该计划已续期到 #${d.record.superseded_by}，请打开新记录`
      } else if (latest && asOf && asOf < latest) {
        stale = `恢复的计划依据 ${asOf}，当前行情已到 ${latest}：按最新数据重新生成`
      } else if (latest && asOf && asOf > latest) {
        stale = `该计划依据 ${asOf}，比你当前选的数据集（截至 ${latest}）更新，请切换数据集`
      }
      const modelId = d.record.model_id
      set(s => {
        const has = s.selected.some(m => m.id === modelId)
        const spec = s.models.find(m => m.id === modelId)
        return {
          plan: p, planSavedId: d.record!.id, planRestored: true, planTrack: null,
          planStale: stale, activeId: modelId,
          // 恢复的是这个模型的计划：模型没勾选就补上，否则面板参数与计划对不上
          selected: has || !spec ? s.selected
            : [...s.selected, { id: modelId, params: { ...spec.defaults } }],
          msg: `已恢复 #${d.record!.id} 保存的计划（${modelId}）`,
        }
      })
    } catch { /* 后端未就绪 / 没有记录：保持空白，不打扰用户 */ }
  },

  /**
   * 未成交续期（BUY-08）：用最新行情重新生成计划。
   * 后端会新增一条记录并把旧记录标 superseded_by——旧快照不动，续期不是改历史。
   */
  async rolloverPlan() {
    const t = get().planTrack
    if (!t) { set({ msg: '请先在跟踪列表里选中要续期的记录' }); return }
    const oldId = t.record.id
    set({ loading: true, msg: '按最新行情续期…' })
    try {
      const d = await api.rolloverPlan(oldId)
      set(s => ({
        plan: d.plan, planSavedId: d.record_id, planRestored: false, planStale: null,
        planTrack: { record: d.record, review: d.review }, loading: false, msg: d.note,
        planRecords: [
          d.record,
          ...s.planRecords
            .filter(r => r.id !== d.record_id)
            .map(r => (r.id === oldId && d.record_id !== oldId
              ? { ...r, superseded_by: d.record_id } : r)),
        ],
      }))
      if (d.unchanged) await get().loadPlans()   // 没有新记录：刷新一下关联字段
    } catch (e: any) {
      set({ loading: false, msg: '续期失败：' + e.message })
    }
  },

  async loadPlans() {
    try {
      const d = await api.plans({ limit: 100 })
      set({ planRecords: d.items })
    } catch (e: any) {
      set({ msg: '跟踪列表加载失败：' + e.message })
    }
  },

  async openRecord(id) {
    try {
      const d = await api.planRecord(id)
      set({ planTrack: d, planSavedId: d.record.id, msg: '' })
    } catch (e: any) {
      set({ msg: '加载记录失败：' + e.message })
    }
  },

  closeRecord() { set({ planTrack: null }) },

  async addFill(body) {
    const t = get().planTrack
    if (!t) return
    set({ loading: true, msg: '录入成交…' })
    try {
      const d = await api.addFill(t.record.id, body)
      set(s => ({
        planTrack: { record: d.record, review: d.review },
        planRecords: s.planRecords.map(r => (r.id === d.record.id
          ? { ...r, lifecycle: d.record.lifecycle, fills_count: r.fills_count + 1 } : r)),
        loading: false, msg: '成交已录入',
      }))
    } catch (e: any) {
      set({ loading: false, msg: '录入失败：' + e.message })
    }
  },

  async deleteFill(fillId) {
    const t = get().planTrack
    if (!t) return
    try {
      const d = await api.deleteFill(t.record.id, fillId)
      set(s => ({
        planTrack: { record: d.record, review: d.review },
        planRecords: s.planRecords.map(r => (r.id === d.record.id
          ? { ...r, lifecycle: d.record.lifecycle, fills_count: Math.max(0, r.fills_count - 1) } : r)),
        msg: '成交记录已删除',
      }))
    } catch (e: any) {
      set({ msg: '删除失败：' + e.message })
    }
  },

  async cancelPlan(reason) {
    const t = get().planTrack
    if (!t) return
    try {
      const d = await api.cancelPlan(t.record.id, reason)
      set(s => ({
        planTrack: { record: d.record, review: d.review },
        planRecords: s.planRecords.map(r => (r.id === d.record.id
          ? { ...r, lifecycle: d.record.lifecycle, cancel_reason: d.record.cancel_reason } : r)),
        msg: '已标记取消',
      }))
    } catch (e: any) {
      set({ msg: '取消失败：' + e.message })
    }
  },

  async reopenPlan() {
    const t = get().planTrack
    if (!t) return
    try {
      const d = await api.reopenPlan(t.record.id)
      set(s => ({
        planTrack: { record: d.record, review: d.review },
        planRecords: s.planRecords.map(r => (r.id === d.record.id
          ? { ...r, lifecycle: d.record.lifecycle, cancel_reason: null } : r)),
        msg: '已撤销取消',
      }))
    } catch (e: any) {
      set({ msg: '撤销失败：' + e.message })
    }
  },

  async deletePlan(id) {
    try {
      await api.deletePlan(id)
      set(s => ({
        planRecords: s.planRecords.filter(r => r.id !== id),
        planTrack: s.planTrack?.record.id === id ? null : s.planTrack,
        planSavedId: s.planSavedId === id ? null : s.planSavedId,
        msg: '记录已删除',
      }))
    } catch (e: any) {
      set({ msg: '删除失败：' + e.message })
    }
  },

  toggleTheme() {
    set(s => {
      const theme = s.theme === 'dark' ? 'light' : 'dark'
      try { localStorage.setItem('wb.theme', theme) } catch { /* 隐私模式忽略 */ }
      return { theme }
    })
  },

  setMsg(m) { set({ msg: m }) },

  setIncludeOos(v) { set({ includeOos: v }) },
  setWfFolds(v) { set({ wfFolds: Math.max(0, Math.min(6, v | 0)) }) },
  async runSummary() {
    const { activeDs, selected, globalParams, includeOos } = get()
    if (!activeDs) { set({ msg: '请先选择数据集' }); return }
    if (!selected.length) { set({ msg: '请先勾选模型' }); return }
    const my = ++seq.sum
    set({ loading: true, msg: '正在提交汇总任务…', job: null })
    try {
      const started = await api.submitSummaryJob({
        dataset_id: activeDs,
        models: selected.map(m => ({ id: m.id, params: m.params })),
        global_params: globalParams,
        include_oos: includeOos,
        test_days: 61,
      })
      if (my !== seq.sum) { abandonJob(started); return }
      set({ job: started, msg: `汇总已排队（任务 ${started.id}）` })

      const final = await pollJob(started.id, () => my === seq.sum,
        j => set({ job: j, msg: jobText(j) }))
      if (!final || my !== seq.sum) return
      if (final.status === 'done' && final.result) {
        const s = final.result as SummaryResponse
        set({
          summary: s, loading: false, job: final,
          msg: `汇总完成：${s.rows.length} 只个股 × ${s.model_ids.length} 模型（${final.duration_ms || 0}ms）`,
        })
      } else if (final.status === 'cancelled') {
        set({ loading: false, job: final, msg: '汇总已取消' })
      } else {
        set({ loading: false, job: final, msg: '汇总失败：' + (final.error || '未知错误') })
      }
    } catch (e: any) {
      if (my === seq.sum) set({ loading: false, msg: '汇总失败：' + e.message })
    }
  },
}))

export function resultFor(run: RunResponse | null, modelId: string, code: string | null) {
  if (!run || !code) return null
  const base = run.results.find(r => r.model_id === modelId && r.code === code)
  const view = run.viewTier
  if (base && view?.code === code && view.model === modelId) {
    const tier = base.tiers?.rows.find(t => t.id === view.tier)
    if (tier?.result) return { ...tier.result, code, name: `${base.name} · ${tier.label}` }
  }
  return base || null
}
