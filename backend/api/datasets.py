# -*- coding: utf-8 -*-
"""数据集接口：列表 / 行情 / 本地 CSV 导入 / 在线拉取 / 本地 seed。"""
import os
import uuid
from datetime import date

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import core.state as state
from db import importer, repository
from engine import crawler
from .schemas import SeedReq

router = APIRouter(prefix='/api')

MAX_UPLOAD_BYTES = 20 * 1024 * 1024      # 单次上传上限 20 MB


class CrawlReq(BaseModel):
    codes: list[str] = Field(min_length=1)
    start: str = ''            # YYYY-MM-DD，与 days 二选一，start 优先
    end: str = ''              # YYYY-MM-DD，可选（配合 start）
    days: int = 120            # 未给 start 时：最近 N 个交易日
    name: str = ''             # 数据集名称（新建时用；合并时数据集沿用原名）
    merge_into: int | None = None   # 合并到既有数据集 id：同(代码,日期)更新、新日期/新代码追加


def _import_error(e: importer.ImportValidationError, path_hint: str = ''):
    return JSONResponse(status_code=422, content={
        'ok': False,
        'error': str(e),
        'code': 'IMPORT_INVALID',
        'details': {'errors': [{'line': ln, 'field': f, 'message': m} for ln, f, m in e.errors],
                    'path': path_hint},
    })


@router.get('/datasets')
def list_datasets():
    with state.db() as conn:
        return {'ok': True, 'data': repository.list_datasets(conn)}


@router.delete('/datasets/{ds_id}')
def delete_dataset(ds_id: int):
    """删除临时/无用数据集（bar + dataset 行）。被计划/回测/实验引用时 409。

    没有这个入口时，验收脚本只能手工改 SQLite 清库——测试库越积越多，
    同名旧数据集还会让「行情未变 → 不新增记录」的断言失真。
    """
    try:
        with state.db() as conn:
            info = repository.delete_dataset(conn, ds_id)
    except LookupError as e:
        return JSONResponse(status_code=404, content={'ok': False, 'error': str(e)})
    except ValueError as e:
        return JSONResponse(status_code=409, content={'ok': False, 'error': str(e), 'code': 'DATASET_IN_USE'})
    return {'ok': True, 'data': info}


@router.get('/datasets/{ds_id}/codes')
def get_code_ranges(ds_id: int):
    """每只股票在该数据集内的日 K 范围（下拉展示用：首日~末日 + 行数）。"""
    with state.db() as conn:
        if not repository.get_dataset(conn, ds_id):
            return JSONResponse(status_code=404, content={'ok': False, 'error': '数据集不存在'})
        return {'ok': True, 'data': repository.code_ranges(conn, ds_id)}


@router.get('/datasets/{ds_id}/bars')
def get_bars(ds_id: int, code: str = None,
             max_bars: int = Query(default=5000, ge=1, le=20000)):
    with state.db() as conn:
        ds = repository.get_dataset(conn, ds_id)
        if not ds:
            return JSONResponse(status_code=404, content={'ok': False, 'error': '数据集不存在'})
        bars = repository.get_bars(conn, ds_id, code)
    if max_bars and len(bars) > max_bars:
        bars = bars[-max_bars:]
    out = []
    for b in bars:
        out.append({
            'date': b.date, 'open': b.open, 'high': b.high, 'low': b.low, 'close': b.close,
            'pre_close': b.pre_close, 'volume': b.volume, 'amount': b.amount,
            'turnover': b.turnover, 'factor': b.factor,
            'adj_open': b.adj_open, 'adj_high': b.adj_high, 'adj_low': b.adj_low, 'adj_close': b.adj_close,
            'limit_up': b.limit_up, 'limit_down': b.limit_down,
            'can_buy': b.can_buy, 'can_sell': b.can_sell, 'suspended': b.suspended,
        })
    return {'ok': True, 'data': {'dataset': ds, 'code': code, 'bars': out}}


def _ingest_file(path: str, name: str, source: str = 'local_csv', note: str = '') -> int:
    """导入落库统一入口：独立连接 + 单事务（失败整体回滚）。"""
    with state.db() as conn:
        return repository.ingest_csv(conn, path, name, source=source, note=note)


@router.post('/datasets/import')
async def import_csv(file: UploadFile = File(...), name: str = None):
    tmp = os.path.join(state.BACKEND_DIR, 'data', f'_upload_{uuid.uuid4().hex}.csv')
    try:
        # 分块写入并在读取过程中执行上限检查。不能先 file.read() 再判断，
        # 否则攻击者仍可用超大 multipart 请求耗尽进程内存。
        total = 0
        with open(tmp, 'wb') as f:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    return JSONResponse(status_code=413, content={
                        'ok': False, 'error': '文件过大（上限 20 MB）',
                        'code': 'FILE_TOO_LARGE'})
                f.write(chunk)
        if total == 0:
            return JSONResponse(status_code=422, content={
                'ok': False, 'error': '文件为空', 'code': 'IMPORT_INVALID'})
        # 解析/校验/写库都放到线程池，避免 20MB CSV 阻塞事件循环
        ds_id = await run_in_threadpool(
            _ingest_file, tmp, name or (file.filename or '导入数据集'), 'local_csv', '用户上传')
    except importer.ImportValidationError as e:
        return _import_error(e, file.filename or '')
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return {'ok': True, 'data': {'dataset_id': ds_id}}


def _inside(path: str, root: str) -> bool:
    try:
        # realpath 同时解析符号链接和 Windows junction；否则允许目录中的链接
        # 可以指向任意文件，绕过本地文件读取边界。
        path = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([path, root]) == root
    except ValueError:      # 不同盘符
        return False


def _allowed_seed_roots():
    roots = [os.path.abspath(os.path.join(str(state.BACKEND_DIR), 'data'))]
    extra = os.environ.get('WORKBENCH_DATA_DIR')
    if extra:
        roots.append(os.path.abspath(extra))
    return roots


@router.post('/datasets/seed')
def seed_from_path(req: SeedReq):
    """从本机已有 CSV 导入（仅允许项目 data 目录或 WORKBENCH_DATA_DIR 内，防任意路径读取）。"""
    path = os.path.abspath(os.path.expanduser(req.path))
    if not any(_inside(path, r) for r in _allowed_seed_roots()):
        return JSONResponse(status_code=403, content={
            'ok': False, 'error': '仅允许导入项目 data 目录（或 WORKBENCH_DATA_DIR）下的文件',
            'code': 'PATH_FORBIDDEN'})
    if not os.path.isfile(path):
        return JSONResponse(status_code=400, content={'ok': False, 'error': '文件不存在', 'code': 'FILE_NOT_FOUND'})
    if os.path.getsize(path) > MAX_UPLOAD_BYTES:
        return JSONResponse(status_code=413, content={'ok': False, 'error': '文件过大（> 20 MB）', 'code': 'FILE_TOO_LARGE'})
    try:
        ds_id = _ingest_file(path, req.name or os.path.basename(path), 'local_csv', '本地文件导入')
    except importer.ImportValidationError as e:
        return _import_error(e, path)
    return {'ok': True, 'data': {'dataset_id': ds_id}}


@router.post('/datasets/crawl')
def crawl_stocks(req: CrawlReq):
    """在线拉取日线行情并直接入库为新数据集（多股票 + 时间区间）。"""
    codes = [c.strip() for c in req.codes if c.strip()]
    if not codes:
        return JSONResponse(status_code=400, content={'ok': False, 'error': '股票代码列表为空'})
    bad = [c for c in codes if not (len(c) == 6 and c.isdigit())]
    if bad:
        return JSONResponse(status_code=400, content={'ok': False, 'error': f'代码格式错误（需 6 位数字）: {bad}'})
    if len(codes) > 50:
        return JSONResponse(status_code=422, content={'ok': False, 'error': '单次最多拉取 50 只股票'})
    parsed = {}
    for field, value in (('start', req.start), ('end', req.end)):
        if not value:
            continue
        try:
            parsed[field] = date.fromisoformat(value)
        except ValueError:
            return JSONResponse(status_code=422, content={
                'ok': False, 'error': f'{field} 需为有效的 YYYY-MM-DD 日期',
                'code': 'DATE_INVALID'})
    if parsed.get('start') and parsed.get('end') and parsed['start'] > parsed['end']:
        return JSONResponse(status_code=422, content={
            'ok': False, 'error': 'start 不能晚于 end', 'code': 'DATE_RANGE_INVALID'})
    try:
        result = crawler.crawl(codes, start=req.start or None, end=req.end or None,
                               days=max(30, min(req.days, 500)))
    except ValueError as e:
        return JSONResponse(status_code=502, content={'ok': False, 'error': str(e)})
    except Exception as e:
        return JSONResponse(status_code=502, content={'ok': False, 'error': f'行情源请求失败: {e}'})

    ok_codes = [s['code'] for s in result['summaries']]
    # 列表 UI 已把起止日期拼在名称后面，自动名里不再重复窗口
    ok_names = [s['name'] for s in result['summaries'] if s.get('name')]
    label = '、'.join(ok_names[:3]) + ('等' if len(ok_names) > 3 else '')
    auto_name = req.name or (f"在线拉取 {len(ok_codes)}只：{label}" if ok_names else '在线拉取数据集')

    if req.merge_into:
        # 合并入既有数据集：同 (代码, 日期) 覆盖更新、新日期/新代码追加；
        # 未被本次拉取覆盖的旧行情原样保留。数据集名称/来源沿用原值。
        try:
            with state.db() as conn:
                stats = repository.upsert_csv(conn, result['path'], req.merge_into)
        except importer.ImportValidationError as e:
            return _import_error(e, result['path'])
        except ValueError as e:
            return JSONResponse(status_code=404, content={'ok': False, 'error': str(e)})
        return {'ok': True, 'data': {
            'dataset_id': req.merge_into, 'merged': True,
            'window': result['window'], 'trading_days': result['trading_days'],
            'rows': stats['total_rows'], 'merged_rows': stats['merged_rows'],
            'new_codes': stats['new_codes'],
            'summaries': result['summaries'], 'errors': result['errors'],
            'csv_path': result['path'],
        }}

    try:
        ds_id = _ingest_file(result['path'], auto_name, 'online_crawl', '在线拉取（搜狐日线+腾讯复权）')
    except importer.ImportValidationError as e:
        return _import_error(e, result['path'])
    return {'ok': True, 'data': {
        'dataset_id': ds_id, 'merged': False,
        'window': result['window'],
        'trading_days': result['trading_days'], 'rows': result['rows'],
        'summaries': result['summaries'], 'errors': result['errors'],
        'csv_path': result['path'],
    }}
