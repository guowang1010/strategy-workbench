# -*- coding: utf-8 -*-
"""SQLite 仓储：数据集导入、行情读取、运行记录持久化。仅依赖标准库 sqlite3。"""
import csv
import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from engine.bars import RawRow, build_bars
from engine.types import Bar
from . import importer


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def open_db(path: str, busy_timeout_ms: int = 5000, probe: bool = True) -> sqlite3.Connection:
    """打开独立连接：autocommit + busy_timeout，事务由 write_tx 显式控制。

    autocommit（isolation_level=None）是为了让写操作统一走 `BEGIN IMMEDIATE`：
    并发导入时立刻拿写锁排队，而不是「先读后写」时才发现锁升级失败。

    `probe=True` 时在建连阶段做一次可写探测（BEGIN IMMEDIATE + ROLLBACK）：
    Windows 上若连接恰好在别的连接重建 -wal/-shm 的瞬间打开，句柄会「只读」，
    之后第一次写必然失败（`attempt to write a readonly database`）；探测不通过就
    立刻关掉重开，把这个问题挡在建连阶段，不留给业务代码。
    """
    in_memory = path == ':memory:'
    last_err = None
    for attempt in range(3):
        conn = sqlite3.connect(path, timeout=busy_timeout_ms / 1000.0,
                               isolation_level=None, check_same_thread=False)
        try:
            conn.execute(f'PRAGMA busy_timeout = {int(busy_timeout_ms)};')
            conn.execute('PRAGMA foreign_keys = ON;')
            if not in_memory:              # 内存库不支持 WAL
                conn.execute('PRAGMA synchronous = NORMAL;')
            if probe and not in_memory:
                _probe_writable(conn)
            return conn
        except sqlite3.OperationalError as e:
            last_err = e
            try:
                conn.close()
            except Exception:
                pass
            if not _is_transient(e) or attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise last_err          # pragma: no cover - 循环内已 raise


def _probe_writable(conn):
    """探测写权限：拿一次写锁再回滚（不写数据，不留事务）。"""
    conn.execute('BEGIN IMMEDIATE')
    conn.execute('ROLLBACK')


# 单进程内的写事务串行锁：SQLite 本身只允许一个写者，提前排队比让内核抢锁更稳，
# 也避免 Windows 上并发建 -wal/-shm 时的 "attempt to write a readonly database"。
_WRITE_LOCK = threading.RLock()


def _is_transient(e: Exception) -> bool:
    msg = str(e).lower()
    return 'readonly' in msg or 'locked' in msg or 'busy' in msg


@contextmanager
def write_tx(conn, retries: int = 4):
    """写事务：BEGIN IMMEDIATE 先拿写锁，成功提交、异常整段回滚。

    瞬时锁错误（readonly/locked/busy）做有限退避重试；仍失败则抛出，由调用方回滚。
    """
    with _WRITE_LOCK:
        for attempt in range(retries):
            try:
                conn.execute('BEGIN IMMEDIATE')
                break
            except sqlite3.OperationalError as e:
                if attempt == retries - 1 or not _is_transient(e):
                    raise
                time.sleep(0.05 * (attempt + 1))
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()


def ensure_schema(conn):
    """建表 + 轻量迁移（幂等，可反复调用）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, 'schema.sql'), encoding='utf-8') as f:
        sql = f.read()
    conn.executescript(sql)
    # 轻量迁移：旧库补列（CREATE IF NOT EXISTS 不会为已有表加列）
    cols = [r[1] for r in conn.execute('PRAGMA table_info(dataset)')]
    if 'names' not in cols:
        conn.execute("ALTER TABLE dataset ADD COLUMN names TEXT NOT NULL DEFAULT '{}'")
    cols = [r[1] for r in conn.execute('PRAGMA table_info(run)')]
    if 'config_hash' not in cols:
        conn.execute("ALTER TABLE run ADD COLUMN config_hash TEXT NOT NULL DEFAULT ''")
    # 计划续期（BUY-08）：老库没有这两列，补上；NULL 表示「没有续期关系」
    cols = [r[1] for r in conn.execute('PRAGMA table_info(plan_record)')]
    if cols and 'renewed_from' not in cols:
        conn.execute('ALTER TABLE plan_record ADD COLUMN renewed_from INTEGER')
    if cols and 'superseded_by' not in cols:
        conn.execute('ALTER TABLE plan_record ADD COLUMN superseded_by INTEGER')
    # WAL 是持久属性，建库时设一次即可（每条连接重复设置会抢独占锁）
    try:
        conn.execute('PRAGMA journal_mode = WAL;')
    except sqlite3.OperationalError:
        pass


def init_db(path: str):
    """兼容入口：建库并返回连接（调用方负责 close；测试会用到）。"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    conn = open_db(path)
    ensure_schema(conn)
    return conn


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def _bars_from_rows(rows):
    """rows: 同一股票已校验的 Row 列表（按日期升序）。转换统一走 engine.bars（A09）。"""
    return build_bars(rows)


def ingest_csv(conn, csv_path: str, name: str, source: str = 'local_csv', note: str = '') -> int:
    """导入 CSV：先整体校验（db.importer），再在单个事务里写入；失败回滚不留残余。"""
    parsed = importer.parse_csv(csv_path)
    data_hash = _sha256_file(csv_path)

    bar_rows = []
    for code, rlist in parsed['groups'].items():
        for b in _bars_from_rows(rlist):
            bar_rows.append((
                code, b.date, b.open, b.high, b.low, b.close, b.pre_close,
                b.volume, b.amount, b.turnover, b.factor,
                b.adj_open, b.adj_high, b.adj_low, b.adj_close,
                b.limit_up, b.limit_down,
                1 if b.can_buy else 0, 1 if b.can_sell else 0, 1 if b.suspended else 0))

    with write_tx(conn) as c:          # 原子提交：失败时 dataset 与 bar 一起回退
        cur = c.cursor()
        cur.execute(
            'INSERT INTO dataset (name, codes, names, start_date, end_date, bar_count, source, fetched_at, data_hash, note) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)',
            (name, json.dumps(list(parsed['groups'].keys()), ensure_ascii=False),
             json.dumps(parsed['names'], ensure_ascii=False), parsed['start'], parsed['end'],
             len(bar_rows), source, _now(), data_hash, note))
        ds_id = cur.lastrowid
        cur.executemany(
            'INSERT INTO bar (dataset_id,code,date,open,high,low,close,pre_close,'
            'volume_shares,amount_yuan,turnover_pct,factor,adj_open,adj_high,adj_low,adj_close,'
            'limit_up,limit_down,can_buy,can_sell,is_suspended) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [(ds_id,) + r for r in bar_rows])
    return ds_id


def upsert_csv(conn, csv_path: str, ds_id: int) -> dict:
    """把 CSV 合并进**既有**数据集：同 (code, date) 覆盖更新，新日期/新代码追加。

    合并只增不减：数据集中未被新 CSV 覆盖的行情原样保留（拉取窗口之外的旧历史不丢）。
    data_hash 重算为合并后全部 bar 的内容哈希——数据变了，版本指纹必须跟着变，
    否则实验/计划里「同数据同代码」的验证绑定会失真。
    """
    parsed = importer.parse_csv(csv_path)
    ds = get_dataset(conn, ds_id)
    if not ds:
        raise ValueError(f'数据集 #{ds_id} 不存在，无法合并')

    bar_rows = []
    for code, rlist in parsed['groups'].items():
        for b in _bars_from_rows(rlist):
            bar_rows.append((
                code, b.date, b.open, b.high, b.low, b.close, b.pre_close,
                b.volume, b.amount, b.turnover, b.factor,
                b.adj_open, b.adj_high, b.adj_low, b.adj_close,
                b.limit_up, b.limit_down,
                1 if b.can_buy else 0, 1 if b.can_sell else 0, 1 if b.suspended else 0))

    with write_tx(conn) as c:          # 原子合并：失败时数据集保持原状
        cur = c.cursor()
        cur.executemany(
            'INSERT OR REPLACE INTO bar (dataset_id,code,date,open,high,low,close,pre_close,'
            'volume_shares,amount_yuan,turnover_pct,factor,adj_open,adj_high,adj_low,adj_close,'
            'limit_up,limit_down,can_buy,can_sell,is_suspended) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [(ds_id,) + r for r in bar_rows])
        total, start_d, end_d = cur.execute(
            'SELECT COUNT(*), MIN(date), MAX(date) FROM bar WHERE dataset_id=?', (ds_id,)).fetchone()
        codes = [r[0] for r in cur.execute(
            'SELECT DISTINCT code FROM bar WHERE dataset_id=? ORDER BY code', (ds_id,))]
        content_hash = _content_hash(cur, ds_id)
        names = json.loads(ds['names'] or '{}')
        names.update(parsed['names'])
        cur.execute(
            'UPDATE dataset SET codes=?, names=?, start_date=?, end_date=?, bar_count=?, '
            'fetched_at=?, data_hash=? WHERE id=?',
            (json.dumps(codes, ensure_ascii=False), json.dumps(names, ensure_ascii=False),
             start_d, end_d, total, _now(), content_hash, ds_id))
    return {'dataset_id': ds_id, 'merged_rows': len(bar_rows),
            'new_codes': [c_ for c_ in parsed['groups'] if c_ not in set(json.loads(ds['codes'] or '[]'))],
            'total_rows': total}


def _content_hash(conn_cursor, ds_id: int) -> str:
    """合并后全部行情的内容指纹（code|date|close|factor 逐行）——与文件哈希口径不同，
    但保证「数据变 → 哈希变」。行数与单行长度都有界（≤50 只 × ≤500 日），开销可忽略。"""
    import hashlib
    h = hashlib.sha256()
    for code, date, close, factor in conn_cursor.execute(
            'SELECT code,date,close,factor FROM bar WHERE dataset_id=? ORDER BY code,date', (ds_id,)):
        h.update(f'{code}|{date}|{close}|{factor}\n'.encode())
    return h.hexdigest()


def code_ranges(conn, ds_id) -> list[dict]:
    """每只股票在该数据集内的日 K 范围（首日/末日/行数）——下拉里显示用。"""
    cur = conn.cursor()
    cur.execute('SELECT code, MIN(date), MAX(date), COUNT(*) FROM bar WHERE dataset_id=? '
                'GROUP BY code ORDER BY code', (ds_id,))
    return [{'code': r[0], 'first': r[1], 'last': r[2], 'rows': r[3]} for r in cur.fetchall()]


def list_datasets(conn):
    cur = conn.cursor()
    cur.execute('SELECT id,name,codes,names,start_date,end_date,bar_count,source,fetched_at,data_hash,note '
                'FROM dataset ORDER BY id DESC')
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_dataset(conn, ds_id):
    cur = conn.cursor()
    cur.execute('SELECT id,name,codes,names,start_date,end_date,bar_count,source,fetched_at,data_hash,note '
                'FROM dataset WHERE id=?', (ds_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def delete_dataset(conn, ds_id: int) -> dict:
    """删除一个数据集（bar 行 + dataset 行）。被计划/回测/实验引用时拒绝。

    数据集是**不可变版本快照**，正常使用不需要删；这个入口是给「清理临时导入」用的
    （验收脚本每次导入一份临时行情，不清理的话测试库会越积越多，同名旧数据集还会
    让「行情未变 → 不新增记录」这类断言失真）。
    引用检查故意不提供 force：计划复盘的浮动盈亏要按 dataset 读行情，
    悄悄删掉会让历史记录变成查不到行情的孤儿。
    """
    ds = get_dataset(conn, ds_id)
    if not ds:
        raise LookupError('数据集不存在')
    cur = conn.cursor()
    refs = []
    for table, label in (('plan_record', '计划'), ('run', '回测'), ('experiment', '实验')):
        cur.execute(f'SELECT COUNT(*) FROM {table} WHERE dataset_id=?', (ds_id,))
        n = int(cur.fetchone()[0] or 0)
        if n:
            refs.append(f'{label} {n} 条')
    if refs:
        raise ValueError(f'数据集「{ds.get("name")}」仍被引用（{"、".join(refs)}），'
                         f'请先删除相关记录再删数据集')
    cur.execute('SELECT COUNT(*) FROM bar WHERE dataset_id=?', (ds_id,))
    bars = int(cur.fetchone()[0] or 0)
    with write_tx(conn) as c:
        cur = c.cursor()
        cur.execute('DELETE FROM bar WHERE dataset_id=?', (ds_id,))
        cur.execute('DELETE FROM dataset WHERE id=?', (ds_id,))
    return {'id': ds_id, 'name': ds.get('name'), 'bars': bars}


def latest_dataset_for_code(conn, code: str):
    """包含该股票的**最新**数据集（先比结束日，再比 id）。

    数据集是版本化快照：重新抓取会得到新的 dataset_id，旧数据集不会自己变长。
    续期要用「现在能看到的最新行情」，所以按代码找最新数据集，而不是死守原记录的数据集。
    """
    cur = conn.cursor()
    cur.execute('SELECT DISTINCT d.id FROM dataset d JOIN bar b ON b.dataset_id = d.id '
                'WHERE b.code=? ORDER BY d.end_date DESC, d.id DESC LIMIT 1', (code,))
    row = cur.fetchone()
    return get_dataset(conn, row[0]) if row else None


def list_dates(conn, ds_id) -> list[str]:
    """数据集内出现过的全部交易日（各股并集，升序）。计划用它还原交易日历。"""
    cur = conn.cursor()
    cur.execute('SELECT DISTINCT date FROM bar WHERE dataset_id=? ORDER BY date', (ds_id,))
    return [r[0] for r in cur.fetchall()]


def _name_is_st(conn, code: str) -> bool:
    """从各数据集的 names 里判断该股是否 ST（bar 表没存这个标记）。"""
    cur = conn.cursor()
    cur.execute('SELECT names FROM dataset ORDER BY id DESC')
    for (names,) in cur.fetchall():
        try:
            nm = (json.loads(names or '{}') or {}).get(code) or ''
        except (TypeError, ValueError):
            continue
        if 'ST' in str(nm).upper():
            return True
    return False


def get_bars_covering(conn, base_ds_id: int, code: str) -> list[Bar]:
    """该股票**跨数据集**最全的行情（按日期合并），用于复盘「计划之后发生了什么」。

    数据集是版本化快照：重新抓取会得到新的 dataset_id，旧数据集的 bar 永远不会增加。
    如果复盘只读原数据集，就永远看不到后续行情，「事后结果」也就无从谈起。

    两个必须处理的细节：
      1. 同一交易日可能存在于多个版本里（重复导入），取 dataset_id 最大的一份（最新导入）；
      2. 各版本的前复权基准不同（base=各自末行因子），直接拼 adj 会让曲线在接缝处断裂，
         所以这里重新走 `build_bars`，把基准统一到合并后的最新一根（A09 的同一教训）。
    """
    cur = conn.cursor()
    cur.execute('SELECT dataset_id,date,open,high,low,close,pre_close,volume_shares,'
                'amount_yuan,turnover_pct,factor,is_suspended FROM bar WHERE code=? '
                'ORDER BY date, dataset_id', (code,))
    rows = cur.fetchall()
    if not rows:
        return get_bars(conn, base_ds_id, code)
    by_date = {}
    for r in rows:                      # ORDER BY date, dataset_id → 同日后写的覆盖先写的
        by_date[r[1]] = r
    st = _name_is_st(conn, code)
    raw = [RawRow(date=r[1], open=r[2], high=r[3], low=r[4], close=r[5], pre_close=r[6],
                  volume=r[7], amount=r[8], turnover=r[9], factor=r[10], st=st,
                  suspended=bool(r[11]))
           for r in (by_date[d] for d in sorted(by_date))]
    return build_bars(raw)


def get_bars(conn, ds_id, code=None):
    cur = conn.cursor()
    if code:
        cur.execute('SELECT * FROM bar WHERE dataset_id=? AND code=? ORDER BY date', (ds_id, code))
    else:
        cur.execute('SELECT * FROM bar WHERE dataset_id=? ORDER BY code, date', (ds_id,))
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        out.append(Bar(
            date=d['date'], open=d['open'], high=d['high'], low=d['low'], close=d['close'],
            pre_close=d['pre_close'], volume=d['volume_shares'], amount=d['amount_yuan'],
            turnover=d['turnover_pct'], factor=d['factor'],
            adj_open=d['adj_open'], adj_high=d['adj_high'], adj_low=d['adj_low'], adj_close=d['adj_close'],
            limit_up=d['limit_up'], limit_down=d['limit_down'],
            can_buy=bool(d['can_buy']), can_sell=bool(d['can_sell']), suspended=bool(d['is_suspended']),
        ))
    return out


def save_run(conn, run_id, ds_id, models_json, params_json, params_hash, duration_ms, status, error, results,
             config_hash: str = ''):
    with write_tx(conn) as c:
        cur = c.cursor()
        cur.execute('INSERT OR REPLACE INTO run '
                    '(id,dataset_id,models,params,params_hash,config_hash,created_at,duration_ms,status,error) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (run_id, ds_id, models_json, params_json, params_hash, config_hash, _now(),
                     duration_ms, status, error))
        for r in results:
            payload = {'trades': r.get('trades', []), 'equity': r.get('equity', []),
                       'position': r.get('position', []), 'markers': r.get('markers', {}),
                       'final': r.get('final', {}), 'tiers': r.get('tiers'), 'params': r.get('params'),
                       'name': r.get('name'), 'color': r.get('color'), 'tradable': r.get('tradable')}
            cur.execute('INSERT OR REPLACE INTO run_result (run_id,code,model_id,metrics,payload) '
                        'VALUES (?,?,?,?,?)',
                        (run_id, r['code'], r['model_id'], json.dumps(r['metrics'], ensure_ascii=False),
                         json.dumps(payload, ensure_ascii=False)))


def list_runs(conn, limit=50):
    cur = conn.cursor()
    cur.execute('SELECT id,dataset_id,models,created_at,duration_ms,status FROM run ORDER BY created_at DESC LIMIT ?',
                (limit,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_run(conn, run_id):
    cur = conn.cursor()
    cur.execute('SELECT id,dataset_id,models,params,created_at,duration_ms,status,error FROM run WHERE id=?', (run_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    rec = dict(zip(cols, row))
    cur.execute('SELECT code,model_id,metrics,payload FROM run_result WHERE run_id=?', (run_id,))
    rec['results'] = []
    for code, mid, metrics, payload in cur.fetchall():
        p = json.loads(payload)
        # 与 /runs 实时返回结构保持一致：trades/equity/position/markers 提到结果顶层，
        # 以便 /export 与 /runs/{id} 直接复用同一套构建逻辑。
        rec['results'].append({
            'code': code, 'model_id': mid,
            'metrics': json.loads(metrics),
            'trades': p.get('trades', []),
            'equity': p.get('equity', []),
            'position': p.get('position', []),
            'markers': p.get('markers', {}),
            'payload': p, 'tiers': p.get('tiers'), 'params': p.get('params'),
            'name': p.get('name'), 'color': p.get('color'), 'tradable': p.get('tradable'), 'final': p.get('final', {}),
        })
    return rec
