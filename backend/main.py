# -*- coding: utf-8 -*-
"""波段策略回测工作台 · FastAPI 入口

启动： uvicorn main:app --host 127.0.0.1 --port 8765
前端（生产）： 将 frontend/dist 交由本服务静态托管；
前端（开发）： vite 跑在 5173，已配置代理把 /api 转发到本服务。
"""
import os
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import core.jobs as jobs
import core.state as state
from api import (models, datasets, runs, optimize, export, plan, plans, summary,
                 jobs as jobs_api, experiments)
from application.errors import AppError
from db.importer import ImportValidationError
from engine.control import Cancelled, Timeout
from engine.space import SpaceError
from engine.version import ENGINE_VERSION

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'dist')


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.init()              # 建表（幂等）
    state.seed_if_empty()     # 首次运行自动导入种子数据
    jobs.reap_interrupted()   # 上次进程残留的 queued/running 标为「已中断」
    yield
    jobs.shutdown(wait=True, timeout=3.0)   # 退出时取消在跑任务、回收线程池
    state.close_all()                       # 释放所有数据库连接


app = FastAPI(title='波段策略回测工作台', version='1.0.0', lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8765'],
    allow_methods=['*'], allow_headers=['*'],
)

app.include_router(models.router)
app.include_router(datasets.router)
app.include_router(runs.router)
app.include_router(optimize.router)
app.include_router(export.router)
app.include_router(plan.router)
app.include_router(plans.router)
app.include_router(summary.router)
app.include_router(jobs_api.router)
app.include_router(experiments.router)


@app.get('/api/health')
def health():
    """存活 + 数据库就绪分开报：db 不通时 status=degraded，便于排查而不是假装 up。"""
    try:
        with state.db() as conn:
            conn.execute('SELECT 1').fetchone()
        db = 'ok'
    except Exception as e:
        return JSONResponse(status_code=503, content={
            'ok': False, 'data': {'status': 'degraded', 'db': f'error: {e}'}})
    return {'ok': True, 'data': {'status': 'up', 'db': db, 'version': app.version,
                                 'engine_version': ENGINE_VERSION, 'jobs': jobs.stats()}}


if os.path.isdir(STATIC_DIR):
    app.mount('/', StaticFiles(directory=STATIC_DIR, html=True), name='static')


@app.exception_handler(AppError)
async def _app_error(request, exc: AppError):
    """应用层异常 → 稳定状态码 + 错误码（不再出现 200 + ok:false）。"""
    body = {'ok': False, 'error': exc.message, 'code': exc.code}
    return JSONResponse(status_code=exc.status, content=body)


@app.exception_handler(SpaceError)
async def _space_error(request, exc: SpaceError):
    return JSONResponse(status_code=422, content={
        'ok': False, 'error': str(exc), 'code': 'SPACE_INVALID'})


@app.exception_handler(Cancelled)
async def _cancelled(request, exc: Cancelled):
    return JSONResponse(status_code=409, content={
        'ok': False, 'error': str(exc) or '任务已取消', 'code': 'CANCELLED'})


@app.exception_handler(Timeout)
async def _timeout(request, exc: Timeout):
    return JSONResponse(status_code=504, content={
        'ok': False, 'error': str(exc) or '任务超时', 'code': 'TIMEOUT'})


@app.exception_handler(ImportValidationError)
async def _import_error(request, exc: ImportValidationError):
    return JSONResponse(status_code=422, content={
        'ok': False, 'error': str(exc), 'code': 'IMPORT_INVALID',
        'details': {'errors': [{'line': ln, 'field': f, 'message': m} for ln, f, m in exc.errors]}})


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    # 内部异常写日志，对外只回稳定错误码（不把 str(exc) / 堆栈暴露给前端）
    traceback.print_exc()
    return JSONResponse(status_code=500, content={
        'ok': False, 'error': '服务器内部错误，请查看后端日志', 'code': 'INTERNAL_ERROR'})


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8765)
