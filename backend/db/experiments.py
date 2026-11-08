# -*- coding: utf-8 -*-
"""实验记录（A11）：写入可重放规格 + 结果摘要，支持按类型/数据集检索。

设计要点：
  - **指纹用 data_hash 不用 dataset_id**：同一份 CSV 导入两次会得到两个 dataset_id，
    但内容相同，指纹应相同，否则「同输入可去重/可对比」就失效；
  - spec 里保留 dataset_id，是为了「一键重跑」时能直接定位到库里的数据集；
  - outcome 只存摘要（不存 trades/equity 全文），完整明细仍在 run/run_result 表。
"""
import hashlib
import json
from datetime import datetime

from engine.version import ENGINE_VERSION
from .repository import write_tx

KINDS = ('backtest', 'optimize')


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def canonical(obj) -> str:
    """规范 JSON：key 排序 + 紧凑分隔符，保证同样内容得到同样字符串。"""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def fingerprint(spec: dict) -> str:
    """规格指纹：内容哈希，不含 id / 时间 / 耗时这类易变字段。"""
    payload = {
        'kind': spec.get('kind'),
        'dataset_hash': spec.get('dataset_hash') or '',
        'code': spec.get('code') or '',
        'model_id': spec.get('model_id') or '',
        'body': spec.get('body') or {},
        'engine_version': spec.get('engine_version') or ENGINE_VERSION,
    }
    return hashlib.sha256(canonical(payload).encode('utf-8')).hexdigest()[:16]


def make_spec(*, kind: str, dataset_id: int, dataset_hash: str, code: str | None,
              model_id: str | None, body: dict) -> dict:
    """构造标准 spec（写入库与计算指纹共用这一份结构）。"""
    if kind not in KINDS:
        raise ValueError(f'未知实验类型: {kind}')
    spec = {
        'kind': kind,
        'dataset_id': dataset_id,
        'dataset_hash': dataset_hash or '',
        'code': code,
        'model_id': model_id,
        'body': body,
        'engine_version': ENGINE_VERSION,
    }
    spec['spec_hash'] = fingerprint(spec)
    return spec


def create(conn, spec: dict, outcome: dict, *, status: str = 'ok', error: str | None = None,
           job_id: str | None = None, duration_ms: int | None = None) -> int:
    """写入一条实验记录，返回自增 id。指纹相同的重复运行会各自保留一行（可对比耗时）。"""
    with write_tx(conn) as c:
        cur = c.cursor()
        cur.execute(
            'INSERT INTO experiment (kind,dataset_id,dataset_hash,code,model_id,spec,spec_hash,'
            'outcome,engine_version,status,error,job_id,created_at,duration_ms) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (spec['kind'], spec['dataset_id'], spec.get('dataset_hash') or '', spec.get('code'),
             spec.get('model_id'), canonical(spec), spec['spec_hash'], canonical(outcome),
             spec.get('engine_version') or ENGINE_VERSION, status, error, job_id, _now(), duration_ms))
        return cur.lastrowid


def _row_to_rec(row, cols) -> dict:
    rec = dict(zip(cols, row))
    for k in ('spec', 'outcome'):          # 列表查询不带 spec 全文，只解析存在的字段
        if k in rec:
            try:
                rec[k] = json.loads(rec[k] or '{}')
            except (TypeError, ValueError):
                rec[k] = {}
    return rec


def list_experiments(conn, kind: str | None = None, dataset_id: int | None = None,
                     limit: int = 50, include_spec: bool = False) -> list[dict]:
    """列表只回摘要字段 + outcome；`include_spec=True` 时才带 spec 全文。

    计划面板要按「数据集哈希 + 代码 + 模型 + 参数 + 执行配置」比对验证记录，
    必须拿到 spec，所以给一个显式开关，而不是让调用方逐条再查一遍。
    """
    cols = ('id,kind,dataset_id,dataset_hash,code,model_id,spec_hash,outcome,engine_version,'
            'status,error,job_id,created_at,duration_ms')
    if include_spec:
        cols += ',spec'
    sql = f'SELECT {cols} FROM experiment'
    where, args = [], []
    if kind:
        where.append('kind=?')
        args.append(kind)
    if dataset_id:
        where.append('dataset_id=?')
        args.append(dataset_id)
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(max(1, min(int(limit), 500)))
    cur = conn.cursor()
    cur.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [_row_to_rec(r, cols) for r in cur.fetchall()]


def get_experiment(conn, exp_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute('SELECT * FROM experiment WHERE id=?', (exp_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return _row_to_rec(row, cols)


def replay_body(conn, exp_id: int) -> dict | None:
    """返回 {kind, body}：body 可直接 POST 到 /runs 或 /api/jobs/optimize。"""
    rec = get_experiment(conn, exp_id)
    if not rec:
        return None
    body = dict(rec['spec'].get('body') or {})
    body['dataset_id'] = rec['dataset_id']
    if rec.get('code'):
        body['code'] = rec['code']
    if rec.get('model_id'):
        body['model_id'] = rec['model_id']
    return {'kind': rec['kind'], 'body': body}


def delete_experiment(conn, exp_id: int) -> bool:
    with write_tx(conn) as c:
        cur = c.cursor()
        cur.execute('DELETE FROM experiment WHERE id=?', (exp_id,))
        return cur.rowcount > 0


def stats(conn) -> dict:
    """按类型计数 + 总数，供前端标签页显示。"""
    cur = conn.cursor()
    cur.execute('SELECT kind, COUNT(*) FROM experiment GROUP BY kind')
    by_kind = {k: n for k, n in cur.fetchall()}
    cur.execute('SELECT COUNT(*) FROM experiment')
    return {'total': cur.fetchone()[0], 'by_kind': by_kind}
