# -*- coding: utf-8 -*-
"""任务表读写：长任务的状态、进度与结果持久化（重启后仍可查历史）。"""
import json
from datetime import datetime, timezone

from .repository import write_tx


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def create(conn, job_id, kind, params_json):
    with write_tx(conn) as c:
        c.execute('INSERT INTO job (id,kind,status,progress,message,params,created_at) '
                  'VALUES (?,?,?,?,?,?,?)',
                  (job_id, kind, 'queued', 0.0, '排队中…', params_json, _now()))


def mark_running(conn, job_id, message='运行中…'):
    with write_tx(conn) as c:
        c.execute("UPDATE job SET status='running', started_at=?, message=? WHERE id=?",
                  (_now(), message, job_id))


def update_progress(conn, job_id, progress, message=None):
    with write_tx(conn) as c:
        if message is None:
            c.execute('UPDATE job SET progress=? WHERE id=?', (float(progress), job_id))
        else:
            c.execute('UPDATE job SET progress=?, message=? WHERE id=?',
                      (float(progress), message, job_id))


def finish(conn, job_id, status, duration_ms, result=None, error=None):
    with write_tx(conn) as c:
        c.execute('UPDATE job SET status=?, message=?, result=?, error=?, '
                  'finished_at=?, duration_ms=?, progress=COALESCE(?, progress) WHERE id=?',
                  (status,
                   {'done': '完成', 'error': '失败', 'cancelled': '已取消'}.get(status, status),
                   json.dumps(result, ensure_ascii=False) if result is not None else None,
                   error, _now(), duration_ms,
                   1.0 if status == 'done' else None, job_id))


def get(conn, job_id):
    cur = conn.execute('SELECT id,kind,status,progress,message,params,result,error,created_at,'
                       'started_at,finished_at,duration_ms FROM job WHERE id=?', (job_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def list_recent(conn, limit=20):
    cur = conn.execute('SELECT id,kind,status,progress,message,created_at,started_at,finished_at,'
                       'duration_ms,error FROM job ORDER BY created_at DESC, rowid DESC LIMIT ?',
                       (limit,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def reap_interrupted(conn):
    """启动时把上次进程残留的 queued/running 任务标为 error（进程内任务已不存在）。"""
    with write_tx(conn) as c:
        cur = c.execute("UPDATE job SET status='error', error='服务重启，任务已中断', "
                        "finished_at=?, message='已中断' WHERE status IN ('queued','running')",
                        (_now(),))
    return cur.rowcount
