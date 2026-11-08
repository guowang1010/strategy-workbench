# -*- coding: utf-8 -*-
"""A07（请求级连接 / 事务边界）+ A08（长任务 job 化）回归测试。"""
import json
import os
import time

import pytest

import core.jobs as jobs
import core.state as state
from db import repository, jobs as job_store
from db.repository import write_tx

SAMPLE_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'data', 'sample_all_300d.csv')


@pytest.fixture()
def db_file(tmp_path, monkeypatch):
    """把全局 DB_PATH 指向临时库，并建表。"""
    monkeypatch.setattr(state, 'DB_PATH', tmp_path / 'a07.db')
    state.reset()
    state.init()
    yield str(state.DB_PATH)
    state.reset()
    jobs.shutdown()


def _write_csv(path, code, n=40, base=10.0):
    """生成一份最小可用行情 CSV（满足 importer 必需列，日期唯一）。"""
    from datetime import date, timedelta

    lines = ['交易日期,股票代码,开盘价,最高价,最低价,收盘价,成交量(股),成交额(元),'
             '换手率(%),复权因子,前收盘价,股票名称,ST状态,停牌标记,交易状态']
    d0 = date(2024, 1, 1)
    for i in range(n):
        px = round(base + (i % 7) * 0.1, 2)
        day = (d0 + timedelta(days=i)).isoformat()
        lines.append(f'{day},{code},{px},{px + 0.1},{px - 0.1},{px},'
                     f'10000,{px * 10000:.0f},1.0,1.0,{px},测试股,否,否,正常交易')
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(lines))
    return path


# ---------------- A07 连接与事务 ----------------

def test_session_reuses_thread_connection_without_leaking_transactions(db_file):
    """同线程复用自己的连接（省去逐请求开关的 WAL 竞争），且退出后不留悬空事务。"""
    with state.db() as c1:
        c1.execute('SELECT 1').fetchone()
    with state.db() as c2:
        assert c2 is c1                  # 线程内复用
        assert c2.in_transaction is False

    # 业务代码抛异常也不能让事务悬着
    with pytest.raises(RuntimeError):
        with state.db() as c3:
            c3.execute('BEGIN IMMEDIATE')
            raise RuntimeError('boom')
    with state.db() as c4:
        assert c4 is c1 and c4.in_transaction is False

    state.close_all()                    # 退出释放：之后再取会新建连接
    with state.db() as c5:
        assert c5 is not c1 and c5.execute('SELECT 1').fetchone()[0] == 1


def test_write_tx_rolls_back_on_error(db_file):
    with state.db() as conn:
        with pytest.raises(RuntimeError):
            with write_tx(conn) as c:
                c.execute("INSERT INTO dataset (name,codes,names,start_date,end_date,bar_count,"
                          "source,fetched_at,data_hash,note) VALUES ('x','[]','{}','2024-01-01',"
                          "'2024-01-02',1,'t','now','h','')")
                raise RuntimeError('boom')
        assert repository.list_datasets(conn) == []


def test_concurrent_imports_both_commit(db_file, tmp_path):
    """两个线程同时导入不同文件：都成功，数据不串、不丢。"""
    import threading

    a = _write_csv(tmp_path / 'a.csv', '600001', n=30)
    b = _write_csv(tmp_path / 'b.csv', '600002', n=35, base=20.0)
    errs, ids = [], []

    def work(path, name):
        try:
            with state.db() as conn:
                ids.append(repository.ingest_csv(conn, path, name))
        except Exception as e:          # noqa: BLE001 - 测试里要看到真实异常
            errs.append(e)

    t1 = threading.Thread(target=work, args=(a, 'A'))
    t2 = threading.Thread(target=work, args=(b, 'B'))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert errs == []
    with state.db() as conn:
        ds = {d['name']: d for d in repository.list_datasets(conn)}
        assert set(ds) == {'A', 'B'}
        assert json.loads(ds['A']['codes']) == ['600001']
        assert json.loads(ds['B']['codes']) == ['600002']
        assert ds['A']['bar_count'] == len(repository.get_bars(conn, ds['A']['id'], '600001')) == 30
        assert ds['B']['bar_count'] == len(repository.get_bars(conn, ds['B']['id'], '600002')) == 35


def test_bars_are_visible_across_connections(db_file, tmp_path):
    path = _write_csv(tmp_path / 'c.csv', '600003', n=12)
    with state.db() as c1:
        ds_id = repository.ingest_csv(c1, path, 'C')
    with state.db() as c2:                # 另一个连接立刻能读到已提交数据
        assert len(repository.get_bars(c2, ds_id, '600003')) == 12


# ---------------- A08 任务执行器 ----------------

def _wait(job_id, timeout=8.0, statuses=('done', 'error', 'cancelled'), interval=0.02):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rec = jobs.get(job_id)
        assert rec is not None
        if rec['status'] in statuses:
            return rec
        time.sleep(interval)
    raise AssertionError(f'任务 {job_id} 未在 {timeout}s 内结束，当前 {rec["status"]}')


def test_job_runs_and_records_progress(db_file):
    seen = []

    def work(ctx):
        for i in range(5):
            ctx.check()
            ctx.progress((i + 1) / 5, f'{i + 1}/5')
            seen.append(i)
        return {'answer': 42}

    job_id = jobs.submit('optimize', {'k': 1}, work, timeout_s=10)
    rec = _wait(job_id)
    assert rec['status'] == 'done'
    assert rec['result'] == {'answer': 42}
    assert rec['progress'] == 1.0
    assert seen == [0, 1, 2, 3, 4]
    assert rec['duration_ms'] is not None


def test_job_cancel_stops_computation(db_file):
    started = []

    def work(ctx):
        started.append(time.time())
        for _ in range(2000):
            ctx.check()                # 组合循环里的检查点
            time.sleep(0.005)
        return {'should': 'not reach'}

    job_id = jobs.submit('optimize', {}, work, timeout_s=30)
    time.sleep(0.15)
    rec = jobs.cancel(job_id)
    assert rec['cancel_requested'] is True
    rec = _wait(job_id, timeout=5)
    assert rec['status'] == 'cancelled'
    assert rec['result'] is None


def test_job_timeout_marks_error(db_file):
    def work(ctx):
        for _ in range(1000):
            ctx.check()
            time.sleep(0.02)
        return {}

    job_id = jobs.submit('optimize', {}, work, timeout_s=0.2)
    rec = _wait(job_id, timeout=6)
    assert rec['status'] == 'error'
    assert '超时' in (rec['error'] or '')


def test_queue_limit_rejects_extra_jobs(db_file, monkeypatch):
    monkeypatch.setattr(jobs, 'MAX_WORKERS', 1)
    monkeypatch.setattr(jobs, 'MAX_QUEUE', 0)

    def slow(ctx):
        for _ in range(200):
            ctx.check()
            time.sleep(0.01)
        return {}

    first = jobs.submit('optimize', {}, slow, timeout_s=30)
    try:
        with pytest.raises(jobs.TooManyJobs):
            jobs.submit('optimize', {}, slow, timeout_s=30)
    finally:
        jobs.cancel(first)
        _wait(first, timeout=6)


def test_reap_marks_leftover_jobs_interrupted(db_file):
    with state.db() as conn:
        job_store.create(conn, 'job_old', 'optimize', '{}')
        job_store.mark_running(conn, 'job_old')
    assert jobs.reap_interrupted() == 1
    rec = jobs.get('job_old')
    assert rec['status'] == 'error' and '中断' in rec['error']


def test_job_list_shows_recent(db_file):
    jobs.submit('summary', {'x': 1}, lambda ctx: {'ok': True}, timeout_s=5)
    time.sleep(0.4)
    recs = jobs.list_recent(5)
    assert recs and recs[0]['kind'] == 'summary'
    assert jobs.stats()['workers'] >= 1
