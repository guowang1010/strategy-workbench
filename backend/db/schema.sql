-- 波段策略回测工作台 · SQLite 结构（单机零部署，备份=复制此文件）

-- 数据集：一次抓取/导入 = 一个版本，绝不覆盖（用新的 id）
CREATE TABLE IF NOT EXISTS dataset (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL,
  codes         TEXT NOT NULL,           -- JSON: ["600186"]
  names         TEXT NOT NULL DEFAULT '{}',  -- JSON: {"600186":"莲花控股"}（导入时从 CSV 提取）
  start_date    TEXT NOT NULL,
  end_date      TEXT NOT NULL,
  bar_count     INTEGER NOT NULL,
  source        TEXT NOT NULL,           -- sohu+tencent / local_csv
  fetched_at    TEXT NOT NULL,
  data_hash     TEXT NOT NULL,           -- sha256(源文件)，用于复现校验
  note          TEXT
);

-- 日线（复权前后都存；回测用 adj_close）
CREATE TABLE IF NOT EXISTS bar (
  dataset_id    INTEGER NOT NULL,
  code          TEXT NOT NULL,
  date          TEXT NOT NULL,
  open          REAL, high REAL, low REAL, close REAL,
  pre_close     REAL,
  volume_shares REAL,
  amount_yuan   REAL,
  turnover_pct  REAL,
  factor        REAL,
  adj_open      REAL, adj_high REAL, adj_low REAL, adj_close REAL,
  limit_up      REAL, limit_down REAL,
  can_buy       INTEGER DEFAULT 1,
  can_sell      INTEGER DEFAULT 1,
  is_suspended  INTEGER DEFAULT 0,
  PRIMARY KEY (dataset_id, code, date)
);
CREATE INDEX IF NOT EXISTS idx_bar_code_date ON bar(code, date);

-- 运行记录
CREATE TABLE IF NOT EXISTS run (
  id            TEXT PRIMARY KEY,        -- run_20261101_094500_a1b2c3
  dataset_id    INTEGER NOT NULL,
  models        TEXT NOT NULL,           -- JSON: [{"id":"M4","params":{...}}]
  params        TEXT NOT NULL,           -- JSON: 全局参数
  params_hash   TEXT NOT NULL,           -- 数据+模型参数+执行配置 的 SHA-256 前 16 位
  config_hash   TEXT NOT NULL DEFAULT '',-- 执行配置（费用/本金/滑点）指纹
  created_at    TEXT NOT NULL,
  duration_ms   INTEGER,
  status        TEXT,                    -- ok / error
  error         TEXT
);

-- 后台任务（长任务 job 化：寻优 / 跨股票汇总）
CREATE TABLE IF NOT EXISTS job (
  id            TEXT PRIMARY KEY,        -- job_20261103_101500_ab12cd
  kind          TEXT NOT NULL,           -- optimize / summary
  status        TEXT NOT NULL,           -- queued / running / done / error / cancelled
  progress      REAL NOT NULL DEFAULT 0, -- 0~1
  message       TEXT,
  params        TEXT NOT NULL DEFAULT '{}',   -- JSON: 请求快照（可重放）
  result        TEXT,                    -- JSON: 完成时的返回体
  error         TEXT,
  created_at    TEXT NOT NULL,
  started_at    TEXT,
  finished_at   TEXT,
  duration_ms   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_job_created ON job(created_at DESC);

-- 实验记录（A11）：每次回测/寻优的「可重放规格 + 结果摘要」
-- spec 里存完整请求体，spec_hash 是规范化 JSON 的 SHA-256 前 16 位；
-- 同一数据（data_hash）+ 同一请求体 + 同一引擎版本 → 同一 hash，可去重、可比对。
CREATE TABLE IF NOT EXISTS experiment (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  kind           TEXT NOT NULL,            -- backtest / optimize
  dataset_id     INTEGER NOT NULL,
  dataset_hash   TEXT NOT NULL DEFAULT '',
  code           TEXT,                     -- 单票实验（回测多票时为空）
  model_id       TEXT,
  spec           TEXT NOT NULL,            -- JSON: {dataset_id, dataset_hash, code, model_id, body, engine_version}
  spec_hash      TEXT NOT NULL,            -- 规范化 spec 的 SHA-256 前 16 位
  outcome        TEXT NOT NULL DEFAULT '{}',-- JSON: 结果摘要（best_params/verdict/metrics...）
  engine_version TEXT NOT NULL DEFAULT '',
  status         TEXT NOT NULL DEFAULT 'ok',-- ok / error / cancelled
  error          TEXT,
  job_id         TEXT,
  created_at     TEXT NOT NULL,
  duration_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_exp_created ON experiment(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_exp_hash ON experiment(spec_hash);
CREATE INDEX IF NOT EXISTS idx_exp_kind_ds ON experiment(kind, dataset_id);

-- 运行结果（每只股票 × 每个模型一行，payload 含 trades/equity/position/markers）
CREATE TABLE IF NOT EXISTS run_result (
  run_id        TEXT NOT NULL,
  code          TEXT NOT NULL,
  model_id      TEXT NOT NULL,
  metrics       TEXT NOT NULL,           -- JSON
  payload       TEXT NOT NULL,           -- JSON: trades/equity/position/markers
  PRIMARY KEY (run_id, code, model_id)
);

-- 计划跟踪（BUY-12）：生成计划时把**完整快照**原样落库（plan 列），事后只追加成交记录，
-- 绝不用后来的参数/行情改写原始计划——复盘读的是当时那份 plan，保证「可追溯为何买入/放弃」。
-- lifecycle：planned（已生成未成交）/ filled（已买入）/ closed（已卖出结清）/ cancelled（人工取消）
CREATE TABLE IF NOT EXISTS plan_record (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id        TEXT NOT NULL,              -- 内容寻址：同数据+同参数+同账户 → 同 id
  dataset_id     INTEGER NOT NULL,
  dataset_hash   TEXT NOT NULL DEFAULT '',
  code           TEXT NOT NULL,
  model_id       TEXT NOT NULL,
  as_of          TEXT NOT NULL,              -- 计划所依据的最后一根已完成日线
  status         TEXT NOT NULL,              -- 生成时的计划状态（watching/signal_confirmed/...）
  lifecycle      TEXT NOT NULL DEFAULT 'planned',
  cancel_reason  TEXT,
  params         TEXT NOT NULL DEFAULT '{}', -- 合并 defaults 后的有效参数（冻结）
  config_hash    TEXT NOT NULL DEFAULT '',
  account        TEXT NOT NULL DEFAULT '{}', -- 生成时的账户快照（冻结）
  rules          TEXT NOT NULL DEFAULT '{}', -- 生成时的执行约束（冻结）
  plan           TEXT NOT NULL,              -- 完整计划 JSON（只读快照）
  renewed_from   INTEGER,                    -- 由哪条记录续期生成（BUY-08 未成交续期）
  superseded_by  INTEGER,                    -- 已被哪条新计划取代（≠取消：行情更新后重新生成）
  engine_version TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_created ON plan_record(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_plan_pid ON plan_record(plan_id);
CREATE INDEX IF NOT EXISTS idx_plan_scope ON plan_record(code, model_id, as_of);

-- 实际成交录入：**只追加**，与策略模拟成交严格分开（这里一行 = 用户真实下单的一笔）。
-- 部分成交录多行即可；卖出与买入同表，用 side 区分，复盘按 side 汇总。
CREATE TABLE IF NOT EXISTS plan_fill (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id   INTEGER NOT NULL,
  side        TEXT NOT NULL,                 -- buy / sell
  qty         REAL NOT NULL,
  price       REAL NOT NULL,
  fees        REAL NOT NULL DEFAULT 0,
  trade_date  TEXT NOT NULL,                 -- 成交日（交易日）
  note        TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fill_rec ON plan_fill(record_id);
