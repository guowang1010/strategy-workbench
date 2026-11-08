// 与后端 schema 对齐的类型定义
export interface ParamField {
  key: string
  label: string
  type: 'int' | 'float' | 'bool' | 'select' | 'rule'
  min?: number
  max?: number
  step?: number
  default: number | string | null
  allow_none?: boolean
  options?: { value: string; label: string }[]
}

// ---- 自定义规则 DSL（与 backend/engine/rules.py 结构一致） ----
export interface RuleCondition {
  $type: 'condition'
  left: string
  cmp: 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'neq'
  right_indicator: string | null
  right_constant: number | null
  temporal: 'current' | 'cross_above' | 'cross_below' | 'consecutive'
  periods: number
}

export interface RuleGroup {
  $type: 'group'
  op: 'all' | 'any'
  children: RuleNode[]
}

export type RuleNode = RuleCondition | RuleGroup

export interface RuleMeta {
  indicators: { key: string; label: string; unit: string }[]
  comparisons: { key: string; label: string }[]
  temporals: { key: string; label: string }[]
  templates: { name: string; rule: RuleNode }[]
  default_rule: RuleNode
}

export interface ModelSpec {
  id: string
  name: string
  tradable: boolean
  color: string
  uses_tp_sl: boolean
  exit_on_signal: boolean
  defaults: Record<string, any>
  param_schema: ParamField[]
  grid: Record<string, number[]>
}

export interface Bar {
  date: string
  open: number
  high: number
  low: number
  close: number
  pre_close: number
  volume: number
  amount: number
  turnover: number
  factor: number
  adj_open: number
  adj_high: number
  adj_low: number
  adj_close: number
  limit_up: number
  limit_down: number
  can_buy: boolean
  can_sell: boolean
  suspended: boolean
}

export interface TradeRow {
  seq: number
  buy_date: string
  buy_price: number
  sell_date: string
  sell_price: number
  hold_days: number
  gross_pct: number
  net_pct: number
  exit_reason: string
  /** 买入命中说明：哪个策略的哪条入场条件触发的建仓（含信号日） */
  entry_reason?: string
  mfe_pct: number
  mae_pct: number
}

export interface Markers {
  buy: [string, number][]
  sell: [string, number][]
  segments: [[string, number], [string, number]][]
}

export interface Metrics {
  total_pct: number
  n_trades: number
  win_rate: number
  profit_factor: number | null
  expectancy_pct: number
  avg_win_pct: number
  avg_loss_pct: number
  max_drawdown_pct: number
  exposure_pct: number
  avg_hold_days: number
  ann_return_pct: number
  ann_vol_pct: number | null
  sharpe: number | null
  calmar: number | null
  final_equity: number
  sample_warning: boolean
}

// ---- 统一执行配置（与 backend/engine/config.py 对齐） ----
export interface ExecConfig {
  capital: number
  commission: number
  min_commission: number
  stamp_duty: number
  transfer_fee: number
  slippage_pct: number
}

// ---- 期末组合快照（区分「真实持仓」与「期末结算」） ----
export interface FinalPortfolio {
  policy: 'close_at_end' | 'mark_to_market'
  liquidated: boolean
  blocked_by: 't_plus_1' | 'limit_down' | 'suspended' | null
  holding: boolean
  cash: number
  shares: number
  market_value: number
  equity_value: number
  equity_ratio: number
  entry_date: string | null
  entry_price: number | null
  hold_days: number | null
  unrealized_pct: number | null
}

export interface TierRow {
  id: string
  label: string
  params: Record<string, any>
  keys: string[]
  error: string | null
  result?: RunResultRow
}

export interface RunResultRow {
  params?: Record<string, any>
  tiers?: { version: string; rows: TierRow[]; best_id: string | null }

  code: string
  model_id: string
  name: string
  tradable: boolean
  color: string
  metrics: Metrics
  trades: TradeRow[]
  equity: number[]
  position: number[]
  markers: Markers
  final?: FinalPortfolio
}

export interface RunResponse {
  viewTier?: { code: string; model: string; tier: string } | null

  run_id: string
  duration_ms: number
  results: RunResultRow[]
  comparison: any[]
  config?: ExecConfig
  config_hash?: string
  params_hash?: string
  dataset_id?: number
  codes?: string[]
}

export interface WalkForwardRow {
  fold: number
  train_range: string[]
  test_range: string[]
  /** A10 切分协议：测试段执行时的指标预热根数与区间，评分从 scorable_from 开始 */
  warmup_bars?: number
  warmup_need?: number
  warmup_short?: boolean
  warmup_range?: string[] | null
  scorable_from?: string
  params: Record<string, any> | null
  expectancy_pct: number | null
  total_pct: number | null
  n_trades: number | null
  bh_total_pct: number | null
  error?: string
}

export interface WalkForwardResult {
  folds: number
  test_days: number
  warmup_bars?: number | null
  warmup_need?: number | null
  warmup_short?: boolean
  rows: WalkForwardRow[]
  mean_expectancy: number | null
  mean_total: number | null
  mean_bh: number | null
  pos_folds: number
  error?: string
}

export interface OptimizeResponse {
  model_id: string
  objective: string
  train_range: string[]
  test_range: string[]
  test_days?: number
  warmup_bars?: number
  warmup_need?: number
  warmup_short?: boolean
  warmup_range?: string[] | null
  scorable_from?: string
  n_combos?: number
  space?: Record<string, (number | string)[]>
  config?: ExecConfig
  config_hash?: string
  duration_ms?: number
  best: { params: Record<string, any>; metrics: Metrics } | null
  best_all: { params: Record<string, any>; metrics: Metrics } | null
  honest: Metrics | null
  biased: Metrics | null
  oracle: Metrics | null
  bh_test: number
  optimistic_bias: number
  transfer_loss: number
  verdict: 'red' | 'yellow' | 'green'
  heatmap: { x: string; y: string; tp_values: number[]; sl_values: number[]; z: (number | null)[][] }
  walk_forward?: WalkForwardResult
}

export interface Dataset {
  id: number
  name: string
  codes: string
  names?: string
  start_date: string
  end_date: string
  bar_count: number
  source: string
  fetched_at: string
  data_hash: string
  note: string
}

export interface PlanCondition {
  $type: 'condition' | 'group'
  text: string
  satisfied: boolean
  unknown?: boolean
  op?: 'all' | 'any'
  children?: PlanCondition[]
  left?: { key?: string; label: string; value: number | null }
  right?: { key?: string | null; label: string; value: number | null }
  cmp?: string
  cmp_label?: string
  temporal?: string
  periods?: number
  margin?: number | null
  note?: string
  semantics?: string
}

export interface PlanInterval {
  lo: number | null
  lo_open: boolean | null
  hi: number | null
  hi_open: boolean | null
}

export interface PlanTrigger {
  entry_type: string | null
  classification: string
  threshold_price: number | null
  cmp: string | null
  intervals: PlanInterval[]
  unknown_components: string[]
  requirements: { text: string; cmp: string; value: number | null }[]
  search_range: [number, number] | null
  notes: string[]
  price_condition_only?: boolean
  semantics?: string
  analytic_price?: number
}

export interface PlanAccountInput {
  mode: 'strategy_simulated' | 'manual_actual'
  cash?: number | null
  shares?: number | null
  sellable?: number | null
  cost_price?: number | null
  snapshot_at?: string | null
  allow_addon?: boolean
}

export interface PlanRulesInput {
  max_price: number | null
  max_deviation_pct: number | null
  max_position_pct: number
  risk_pct: number | null
  follow_from: 'allow_existing' | 'new_signal_only'
  day_only: boolean
}

export interface PlanAccountSnapshot {
  mode: string
  source: string
  as_of: string
  cash: number
  shares: number
  sellable: number
  cost_price: number | null
  market_value: number
  equity_value: number
  equity_ratio: number | null
  holding: boolean
  complete: boolean
}

export interface PlanExecution {
  as_of: string
  signal_status: string
  /** 计划生成时的真实时点（Asia/Shanghai）：盘前/盘中/收盘后是三种不同结论 */
  now?: string
  timezone?: string | null
  /** pre_open / in_session / after_close / non_session / unknown */
  phase?: string
  phase_text?: string
  /** open / passed / closed / n/a / unknown —— 执行窗口是否还开着 */
  window?: string
  window_text?: string
  confirmation_time: string | null
  confirmation_basis: string
  next_check_session: string | null
  next_check_confirmed: boolean
  earliest_execution_session: string | null
  execution_session_confirmed: boolean
  execution_session_source: string | null
  mode: string
  /** 研究模式下回放的是历史情景（执行日/有效期都是过去时），不是当前待执行订单 */
  historical?: boolean
  eligibility: 'pass' | 'pending' | 'fail' | 'n/a'
  pending_checks: string[]
  price_bounds: {
    max_price: number | null
    deviation_cap: number | null
    effective_cap: number | null
    reference_price: number
    violated: boolean
    day_only: boolean
    on_violation: string
  }
  quantity_estimate: {
    shares: number
    lots: number
    reference_price: number
    /** 数量/费用/风险/保护价共用的估算入场价（不是收盘价） */
    entry_price_estimate?: number
    entry_price_basis?: string
    price_basis: string
    estimated: boolean
    hypothetical?: boolean
    amount: number
    estimated_fees: number
    cash_after: number
    budget: number
    binding_constraint: string | null
    risk: { risk_pct: number; risk_amount?: number; per_share_risk?: number; qty_cap?: number; note: string } | null
    /** 收盘价 vs 估算入场价两种情景（价差大时才有多条） */
    scenarios?: PlanPriceScenario[]
    note: string
  }
  trigger: PlanTrigger
  lifecycle: string
  lifecycle_reason: string[]
}

export interface PlanPriceScenario {
  key: 'close' | 'estimate'
  label: string
  price: number
  /** 应用全部约束（仓位预算 + 风险预算）后，该价下可执行的计划数量 */
  shares: number
  lots: number
  /** 纯现金购买力（不含风险预算）：只作对照，不是下单数量 */
  cash_power_shares?: number
  amount: number
  fees: number
  cash_after: number
  /** 按「计划数量」在该价下买入需要的钱（含费用） */
  plan_qty_cost: number
  plan_qty_over_budget: boolean
  used: boolean
}

export interface PlanProtection {
  basis: string
  reference_price: number
  estimated_entry_price?: number
  entry_price_basis?: string
  tp_pct: number | null
  sl_pct: number | null
  tp_price: number | null
  sl_price: number | null
  min_hold: number | null
  max_hold: number | null
  min_hold_until?: string | null
  min_hold_until_confirmed?: boolean
  /** entry_date（已持仓，按真实入场日）/ hypothetical_entry（空仓假设）/ unknown_entry_date */
  min_hold_basis?: string
  min_hold_entry_date?: string | null
  min_hold_elapsed_sessions?: number | null
  min_hold_satisfied?: boolean | null
  min_hold_note?: string
  exit_rules: string[]
  confirmed_fill_price: number | null
  note: string
}

export interface PlanValidity {
  plan_id: string
  generated_at: string
  as_of: string
  data_hash: string
  valid_until_session: string | null
  data_state?: string
  latest_completed_session?: string | null
  expired: boolean
  expired_reasons: { code: string; text: string }[]
  invalidated_by: string[]
  research_mode: boolean
  mode: string
}

export interface PlanEvidence {
  status: string
  note?: string
  in_sample_only?: boolean
  oos?: any
  oos_note?: string
  records: {
    experiment_id: number
    created_at: string
    spec_hash: string
    run_id?: string
    in_sample_only: boolean
    metrics: {
      code: string; model_id: string
      total_pct: number | null; n_trades: number | null
      expectancy_pct: number | null; max_drawdown_pct: number | null
      sharpe: number | null
    } | null
  }[]
}

export interface PlanDataStatus {
  as_of: string
  bar_count: number
  min_bars_required: number
  sufficient: boolean
  today: string
  /** 真实时点与「最近一个已收盘交易日」：判陈旧看后者，不看 today */
  now?: string
  timezone?: string | null
  latest_completed_session?: string | null
  latest_completed_confirmed?: boolean
  latest_completed_source?: string | null
  sessions_behind: number
  stale: boolean
  sessions_behind_confirmed: boolean
  calendar: { start: string | null; end: string | null; sessions: number; holiday_years: number[] }
  mode: string
}

export interface PlanResponse {
  version: string
  status: 'data_unavailable' | 'watching' | 'signal_confirmed' | 'blocked' | 'holding' | 'expired'
  status_text: string
  reason_codes: { code: string; text: string }[]
  identity: {
    plan_id: string
    strategy_version: string
    engine_version?: string
    data_state?: string
    model_id: string
    model_name: string
    effective_params: Record<string, any>
    config_hash: string
    dataset_id?: number
    dataset_hash: string
    code?: string
  }
  context: {
    as_of: string
    data_status: PlanDataStatus
    account_mode: string
    account_snapshot: PlanAccountSnapshot
    /** 规范化后的执行约束（含默认值），续期/落库共用这份 */
    exec_rules?: PlanRulesInput & { max_position_pct?: number }
  }
  signal: {
    status: 'confirmed' | 'not_confirmed' | 'unknown'
    confirmed_at: string | null
    evaluated_on: string
    semantics: string
    entry_type: string | null
    entry_condition: string
    condition_tree: PlanCondition | null
    missing_conditions: { text: string; reason: string }[]
    error?: string | null
  }
  execution: PlanExecution
  protection: PlanProtection
  validity: PlanValidity
  evidence: PlanEvidence
  dataset?: { id: number; name: string; start_date: string; end_date: string; bar_count: number; data_hash: string }
  code?: string
  // 兼容字段
  as_of_date: string
  tradable: boolean
  holding: boolean | null
  entry_type: string | null
  entry_condition: string
  threshold_price: number | null
  last_close: number | null
  buy_hold?: boolean
  note?: string
  position?: {
    cash: number | null
    shares: number | null
    market_value: number | null
    equity_value: number | null
    equity_ratio: number | null
  }
  config?: ExecConfig
  config_hash?: string
}

export interface SummaryModelMetrics {
  total_pct: number | null
  n_trades: number | null
  expectancy_pct: number | null
  win_rate: number | null
  max_drawdown_pct: number | null
}

export interface SummaryRow {
  code: string
  name: string
  n: number
  models: Record<string, SummaryModelMetrics>
  oos: Record<string, { honest_exp: number | null; honest_total: number | null; verdict: string | null }>
}

export interface SummaryResponse {
  rows: SummaryRow[]
  model_ids: string[]
  include_oos: boolean
  test_days: number
}

// ---- 在线行情拉取 ----
export interface CrawlSummary {
  code: string
  name: string
  rows: number
  missing: string[]
  /** 窗口起点早于首根行情的交易日数（新股上市晚，不算缺失） */
  before_listing?: number
  /** 窗口内第一根行情日期 */
  first_date?: string
  list_date: string
}

/** 每只股票在数据集内的日 K 范围（下拉展示用） */
export interface CodeRange {
  code: string
  first: string
  last: string
  rows: number
}

export interface CrawlResult {
  dataset_id: number
  /** true = 合并进了既有数据集（未新建） */
  merged?: boolean
  merged_rows?: number
  new_codes?: string[]
  window: string[]
  trading_days: number
  rows: number
  summaries: CrawlSummary[]
  errors: { code: string; error: string }[]
  csv_path: string
}

// ---- 后台任务（长任务 job 化） ----
export type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

export interface JobRecord {
  id: string
  kind: 'optimize' | 'summary' | string
  status: JobStatus
  progress: number
  message: string | null
  error: string | null
  result: any | null
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  cancel_requested?: boolean
}

// ---- 实验记录（A11） ----
export interface ExperimentRow {
  id: number
  kind: 'backtest' | 'optimize' | string
  dataset_id: number
  dataset_hash: string
  code: string | null
  model_id: string | null
  spec_hash: string
  outcome: any
  engine_version: string
  status: 'ok' | 'error' | 'cancelled' | string
  error: string | null
  job_id: string | null
  created_at: string
  duration_ms: number | null
}

export interface ExperimentDetail extends ExperimentRow {
  spec: any
}

export interface ExperimentList {
  items: ExperimentRow[]
  stats: { total: number; by_kind: Record<string, number> }
}

/* ===== 计划跟踪与复盘（BUY-12） ===== */

export interface PlanFill {
  id: number
  record_id: number
  side: 'buy' | 'sell'
  qty: number
  price: number
  fees: number
  trade_date: string
  note: string | null
  created_at: string
}

export interface PlanRecordSummary {
  id: number
  plan_id: string
  dataset_id: number
  dataset_hash: string
  code: string
  model_id: string
  as_of: string
  status: string
  lifecycle: 'planned' | 'filled' | 'closed' | 'cancelled' | string
  cancel_reason: string | null
  params: Record<string, any>
  config_hash: string
  account: Record<string, any>
  rules: Record<string, any>
  engine_version: string
  created_at: string
  updated_at: string
  fills_count: number
  /** 由哪条记录续期生成（BUY-08） */
  renewed_from: number | null
  /** 已被哪条新计划取代：续期 ≠ 取消，旧快照与生命周期都不动 */
  superseded_by: number | null
  plan?: PlanResponse          // 详情接口才带（列表为省流量不带）
}

export interface PlanFillsSummary {
  buy_qty: number
  buy_avg_price: number | null
  buy_amount: number | null
  buy_fees: number | null
  sell_qty: number
  sell_avg_price: number | null
  sell_amount: number | null
  sell_fees: number | null
  open_qty: number
  open_cost: number | null
  realized_pnl: number | null
  realized_pct: number | null
  first_buy_date: string | null
  last_fill_date: string | null
  fill_count: number
}

export interface PlanVsActual {
  planned_shares: number
  planned_reference_price: number | null
  planned_amount: number | null
  planned_fees: number | null
  actual_shares: number
  actual_avg_price: number | null
  actual_fees: number | null
  price_deviation_pct: number | null
  qty_deviation_pct: number | null
  fees_diff: number | null
  checks: { text: string; ok: boolean; note: string }[]
}

export interface PlanReview {
  record_id: number
  plan_id: string
  as_of: string
  plan_status: string
  outcome: 'pending' | 'no_action' | 'not_filled' | 'filled' | 'partial' | 'closed' | 'cancelled' | string
  outcome_text: string
  lifecycle: string
  data_span: { start: string | null; end: string | null; bar_count: number; note: string }
  execution_window: {
    earliest_execution_session: string | null
    valid_until_session: string | null
    execution_session_confirmed: boolean | null
  }
  fills: PlanFill[]
  fills_summary: PlanFillsSummary
  vs_plan: PlanVsActual
  not_filled: {
    reason_code: string
    text: string
    execution_session: string | null
    session_open: number | null
    session_can_buy: boolean | null
    cap: number | null
    expired: boolean
    superseded_by?: number | null
  } | null
  market: {
    data_latest_date: string | null
    data_latest_close: number | null
    valuation_date: string | null
    valuation_close: number | null
    open_pnl: number | null
    open_pct: number | null
    total_pnl: number | null
    mfe_pct: number | null
    mae_pct: number | null
    bars_held: number
    valuable: boolean
    note: string
  }
  counterfactual: {
    available: boolean
    model_id?: string
    entry_date?: string | null
    entry_price?: number | null
    exit_date?: string | null
    exit_price?: number | null
    hold_days?: number | null
    net_pct?: number | null
    exit_reason?: string | null
    status?: 'no_entry' | 'open' | 'closed' | string
    note?: string
  }
  timeline: { at: string; event: string; text: string }[]
  notes: string[]
  today: string
}

export interface PlanRecordDetail {
  record: PlanRecordSummary
  review: PlanReview
}

export interface PlanRecordList {
  items: PlanRecordSummary[]
  stats: { total: number; by_lifecycle: Record<string, number> }
}

export interface PlanLast {
  found: boolean
  record: PlanRecordSummary | null
  review: PlanReview | null
}
