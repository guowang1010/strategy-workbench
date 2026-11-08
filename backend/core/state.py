# -*- coding: utf-8 -*-
"""全局状态：数据库路径、连接管理、启动种子数据。

A07：不再共享单连接，也不是「每个请求开关一次连接」。

- **每线程一条连接**（thread-local，按 DB 路径缓存）：FastAPI 同步端点跑在线程池里，
  每个工作线程复用自己的连接，既避免了请求间的事务串味，也没有逐请求开关连接的开销，
  更躲开了 Windows 上「连接恰好在 -wal 重建瞬间打开 → 句柄只读」的坑（见 repository.open_db）。
- **autocommit + 显式写事务**：读不占锁；写统一走 `repository.write_tx()`（BEGIN IMMEDIATE），
  单进程内再加一把写锁排队，失败整段回滚。
- **退出释放**：`close_all()` 在应用关闭时关掉所有连接；`db()` 退出时若发现残留事务立即回滚。
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / 'data' / 'workbench.db'
# 启动时可设 WORKBENCH_SEED_CSV 指向一个本地行情 CSV，首次运行自动导入为默认数据集
SEED_CSV = os.environ.get('WORKBENCH_SEED_CSV', '')

BUSY_TIMEOUT_MS = int(os.environ.get('WORKBENCH_DB_BUSY_TIMEOUT_MS', '5000'))

_SCHEMA_READY_FOR = None
_LOCAL = threading.local()
_ALL_CONNS: dict[int, sqlite3.Connection] = {}
_ALL_LOCK = threading.Lock()


def open_conn(path: str | None = None) -> sqlite3.Connection:
    """新开一条独立连接（不进线程缓存，调用方负责 close）。"""
    from db import repository

    p = str(path or DB_PATH)
    if p != ':memory:':
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    return repository.open_db(p, busy_timeout_ms=BUSY_TIMEOUT_MS)


def _thread_conn() -> sqlite3.Connection:
    p = str(DB_PATH)
    if p == ':memory:':                 # 内存库不能跨请求复用（每次都是新库）
        return open_conn(p)
    conn = getattr(_LOCAL, 'conn', None)
    if conn is not None and getattr(_LOCAL, 'path', None) == p:
        return conn
    if conn is not None:                # 换了库：先关掉旧连接
        _drop(conn)
    conn = open_conn(p)
    _LOCAL.conn, _LOCAL.path = conn, p
    with _ALL_LOCK:
        _ALL_CONNS[id(conn)] = conn
    return conn


def _drop(conn: sqlite3.Connection):
    with _ALL_LOCK:
        _ALL_CONNS.pop(id(conn), None)
    try:
        conn.close()
    except Exception:
        pass
    if getattr(_LOCAL, 'conn', None) is conn:
        _LOCAL.conn, _LOCAL.path = None, None


@contextmanager
def db():
    """请求级会话：拿到本线程的连接，退出时保证没有悬空事务。"""
    conn = _thread_conn()
    try:
        yield conn
    finally:
        if conn.in_transaction:         # 业务异常中断时不留半个事务
            try:
                conn.rollback()
            except Exception:
                pass


def connect():
    """兼容旧入口：返回一条新连接（调用方负责 close）。新代码请用 `with db() as conn`。"""
    return open_conn()


def close_all():
    """应用退出 / 测试切换库时释放所有连接。"""
    with _ALL_LOCK:
        conns = list(_ALL_CONNS.values())
        _ALL_CONNS.clear()
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
    _LOCAL.conn, _LOCAL.path = None, None


def init(force: bool = False):
    """建表（幂等）。同一路径只做一次；`force=True` 用于测试换库。"""
    global _SCHEMA_READY_FOR
    p = str(DB_PATH)
    if not force and _SCHEMA_READY_FOR == p:
        return
    from db import repository

    with db() as conn:
        repository.ensure_schema(conn)
    _SCHEMA_READY_FOR = p


def reset():
    """测试用：忘记已建库标记并释放连接（配合 monkeypatch DB_PATH）。"""
    global _SCHEMA_READY_FOR
    _SCHEMA_READY_FOR = None
    close_all()


def seed_if_empty():
    """若库内无数据集，且配置了 SEED_CSV，则导入为默认数据集。"""
    if not SEED_CSV or not os.path.exists(SEED_CSV):
        return
    from db import repository

    init()
    with db() as conn:
        if repository.list_datasets(conn):
            return
        try:
            repository.ingest_csv(conn, SEED_CSV, name='默认样本(莲花控股300日)',
                                  source='local_csv', note='自动种子数据')
        except Exception as e:  # 种子失败不应阻塞服务
            print('[seed] 跳过:', e)
