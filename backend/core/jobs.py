# -*- coding: utf-8 -*-
"""单机有界任务执行器（A08）：把寻优 / 跨股票汇总这类长任务 job 化。

设计要点
- **有界**：线程池固定 MAX_WORKERS（默认 2），排队上限 MAX_QUEUE（默认 8），
  超限直接拒绝（API 返回 429），不会因为连点就把 CPU 与内存打满。
- **可观测**：状态/进度/结果落 `job` 表，前端轮询 `GET /api/jobs/{id}`；
  进度按 1% 节流，DB 写入再按时间节流（默认 400ms），不拖慢计算。
- **可取消**：取消 = 置标志位，worker 在引擎组合循环的 check() 里抛 Cancelled 立刻停下；
  排队中的任务在真正开始前就会看到标志位，直接标记取消。
- **有超时**：deadline 从任务**开始执行**时算，超时同样经 check() 中断。
- **进程重启**：内存中的任务随进程消失，启动时把残留的 queued/running 标为「已中断」。
"""
import json
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import core.state as state
from db import jobs as job_store
from engine.control import Cancelled, Timeout

MAX_WORKERS = max(1, int(os.environ.get('WORKBENCH_JOB_WORKERS', '2')))
MAX_QUEUE = max(0, int(os.environ.get('WORKBENCH_JOB_QUEUE', '8')))
PROGRESS_WRITE_INTERVAL = float(os.environ.get('WORKBENCH_JOB_PROGRESS_MS', '400')) / 1000.0
DEFAULT_TIMEOUT = {'optimize': 600.0, 'summary': 900.0}


class TooManyJobs(Exception):
    """排队已满。"""


class JobContext:
    """交给任务函数的句柄：上报进度、响应取消/超时。"""

    def __init__(self, job_id: str, timeout_s: float):
        self.job_id = job_id
        self.timeout_s = timeout_s
        self.cancelled = False
        self.deadline = None
        self._last_write = 0.0
        self._last_pct = -1.0
        self._lock = threading.Lock()

    # ---- 引擎回调 ----
    def check(self):
        if self.cancelled:
            raise Cancelled('任务已取消')
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise Timeout(f'任务超时（>{self.timeout_s:.0f}s）')

    # ---- 进度 ----
    def progress(self, value: float, message: str | None = None):
        pct = round(min(1.0, max(0.0, float(value))) * 100)
        now = time.monotonic()
        with self._lock:
            if pct <= self._last_pct and message is None:
                return
            if message is None and (now - self._last_write) < PROGRESS_WRITE_INTERVAL:
                return
            self._last_pct, self._last_write = pct, now
        try:
            with state.db() as conn:
                job_store.update_progress(conn, self.job_id, pct / 100.0, message)
        except Exception:
            pass          # 进度写失败不影响计算

    def request_cancel(self):
        self.cancelled = True


_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()
_ACTIVE: dict[str, JobContext] = {}
_ACTIVE_LOCK = threading.Lock()
_INFLIGHT = 0


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        with _EXECUTOR_LOCK:
            if _EXECUTOR is None:
                _EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS,
                                               thread_name_prefix='wb-job')
    return _EXECUTOR


def _new_id() -> str:
    return ('job_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_'
            + uuid.uuid4().hex[:6])


def inflight() -> int:
    with _ACTIVE_LOCK:
        return _INFLIGHT


def stats() -> dict:
    return {'inflight': inflight(), 'active': len(_ACTIVE),
            'workers': MAX_WORKERS, 'queue_limit': MAX_QUEUE}


def submit(kind: str, params: dict, fn, timeout_s: float | None = None) -> str:
    """提交任务并立即返回 job_id；fn(ctx) 返回可 JSON 序列化的 data。"""
    global _INFLIGHT
    with _ACTIVE_LOCK:
        if _INFLIGHT >= MAX_WORKERS + MAX_QUEUE:
            raise TooManyJobs(f'任务队列已满（上限 {MAX_WORKERS + MAX_QUEUE}），请稍后再试')
        job_id = _new_id()
        ctx = JobContext(job_id, float(timeout_s or DEFAULT_TIMEOUT.get(kind, 600.0)))
        _ACTIVE[job_id] = ctx
        _INFLIGHT += 1

    try:
        with state.db() as conn:
            job_store.create(conn, job_id, kind, json.dumps(params, ensure_ascii=False, default=str))
    except Exception:
        with _ACTIVE_LOCK:
            _ACTIVE.pop(job_id, None)
            _INFLIGHT -= 1
        raise

    _executor().submit(_run, job_id, kind, fn, ctx)
    return job_id


def _run(job_id: str, kind: str, fn, ctx: JobContext):
    global _INFLIGHT
    t0 = time.monotonic()
    try:
        ctx.check()                      # 排队期间可能已被取消
        ctx.deadline = t0 + ctx.timeout_s
        with state.db() as conn:
            job_store.mark_running(conn, job_id)
        ctx.progress(0.0, '开始计算…')
        result = fn(ctx)
        duration = int((time.monotonic() - t0) * 1000)
        with state.db() as conn:
            job_store.finish(conn, job_id, 'done', duration, result=result)
    except Cancelled:
        with state.db() as conn:
            job_store.finish(conn, job_id, 'cancelled', int((time.monotonic() - t0) * 1000),
                             error='任务已取消')
    except Timeout as e:
        with state.db() as conn:
            job_store.finish(conn, job_id, 'error', int((time.monotonic() - t0) * 1000),
                             error=str(e))
    except BaseException as e:               # 任务内部异常：记录但不拖垮进程
        traceback.print_exc()
        with state.db() as conn:
            job_store.finish(conn, job_id, 'error', int((time.monotonic() - t0) * 1000),
                             error=f'{type(e).__name__}: {e}')
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.pop(job_id, None)
            _INFLIGHT = max(0, _INFLIGHT - 1)


def _load(rec):
    if rec and rec.get('result'):
        try:
            rec['result'] = json.loads(rec['result'])
        except (TypeError, json.JSONDecodeError):
            rec['result'] = None
    if rec is not None:
        rec['params'] = json.loads(rec.get('params') or '{}')
        ctx = _ACTIVE.get(rec['id'])
        rec['cancel_requested'] = bool(ctx and ctx.cancelled)
    return rec


def get(job_id: str):
    with state.db() as conn:
        return _load(job_store.get(conn, job_id))


def list_recent(limit: int = 20):
    with state.db() as conn:
        recs = job_store.list_recent(conn, limit)
    for r in recs:
        ctx = _ACTIVE.get(r['id'])
        r['cancel_requested'] = bool(ctx and ctx.cancelled)
    return recs


def cancel(job_id: str):
    rec = get(job_id)
    if rec is None:
        return None
    if rec['status'] in ('done', 'error', 'cancelled'):
        rec['cancel_requested'] = rec['status'] == 'cancelled'
        return rec
    ctx = _ACTIVE.get(job_id)
    if ctx is not None:
        ctx.request_cancel()
        rec['cancel_requested'] = True
    return rec


def reap_interrupted():
    """进程启动时调用：上次残留的 queued/running 一律标为中断。"""
    try:
        with state.db() as conn:
            return job_store.reap_interrupted(conn)
    except Exception as e:
        print('[jobs] 清理残留任务失败:', e)
        return 0


def drain(timeout: float = 5.0):
    """等待在跑/排队的任务结束（测试收尾与优雅停机用）。"""
    t0 = time.monotonic()
    while inflight() > 0 and time.monotonic() - t0 < timeout:
        time.sleep(0.02)
    return inflight() == 0


def shutdown(wait: bool = True, timeout: float = 5.0):
    """优雅停机：先让在跑任务看到取消标志，再回收线程池。

    注意不要用 cancel_futures 直接掐掉排队任务 —— 那样它们的 `_run` 永远不会执行，
    inflight 计数就漏了，后续提交会被误判为「队列已满」。
    """
    global _EXECUTOR, _INFLIGHT
    with _ACTIVE_LOCK:
        for ctx in _ACTIVE.values():
            ctx.request_cancel()
    if wait:
        drain(timeout)
    ex, _EXECUTOR = _EXECUTOR, None
    if ex is not None:
        ex.shutdown(wait=False, cancel_futures=False)
    with _ACTIVE_LOCK:                     # 进程/测试收尾：内存态一并清零
        _ACTIVE.clear()
        _INFLIGHT = 0
