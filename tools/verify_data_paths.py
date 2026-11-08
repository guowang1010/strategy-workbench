# -*- coding: utf-8 -*-
"""数据链路验收（2026-11-04 第十二轮）：滚动走查、导出内容、异常 CSV、并发一致性。

这些项此前只有「人工看过」没有可复现证据。这里全部走真实 HTTP：
  1. 滚动走查：提交 3 折任务 → 结果必须带 folds=3 与各折统计（不是空对象）；
  2. 导出：xlsx 能被 openpyxl 打开且有 2 个工作表、行数对得上；csv 行数 = 交易数；md 有标题；
  3. 异常 CSV：写入非法行情（收盘 > 最高）导入必须 4xx 且不产生新数据集（不能半导入）；
  4. 并发：同一计划/回测并发提交，结果一致（plan_id 相同）且全部 200。

用法（后端需先启动）：
    .venv\\Scripts\\python.exe tools\\verify_data_paths.py [http://127.0.0.1:8765]
"""
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8765').rstrip('/')
GLOBAL = {'capital': 100000, 'commission': 0.00025, 'min_commission': 5,
          'stamp_duty': 0.0005, 'transfer_fee': 0.00001, 'slippage_pct': 0}
DATASET, CODE = 1, '600186'


def req(method, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, {'raw': payload[:200].decode('utf-8', 'replace')}


def check(name, cond, detail=''):
    mark = 'PASS' if cond else 'FAIL'
    print(f'[{mark}] {name}' + (f' | {detail}' if detail and not cond else ''))
    if not cond:
        raise AssertionError(name + (f' | {detail}' if detail else ''))


def dataset_count():
    _, d = req('GET', '/api/datasets')
    return len(d['data'])


def test_walk_forward():
    space = {'entry': ['ma_cross'], 'min_hold': [1, 3, 1], 'tp': [5, 9, 2], 'sl': [5, 12, 3]}
    body = {'dataset_id': DATASET, 'code': CODE, 'model_id': 'M4', 'test_days': 61,
            'walk_forward_folds': 3, 'objective': 'expectancy', 'space': space,
            'global_params': GLOBAL}
    st, d = req('POST', '/api/jobs/optimize', body)
    check('滚动走查：任务提交', st in (200, 202) and d.get('ok'), str(d)[:200])
    job_id = d['data']['id']
    deadline = time.time() + 180
    rec = None
    while time.time() < deadline:
        _, r = req('GET', f'/api/jobs/{job_id}')
        rec = r['data']
        if rec['status'] not in ('queued', 'running'):
            break
        time.sleep(0.3)
    check('滚动走查：任务完成', rec and rec['status'] == 'done', str(rec)[:300])
    res = rec.get('result') or {}
    wf = res.get('walk_forward') or {}
    check('滚动走查：folds=3', wf.get('folds') == 3, str(wf)[:300])
    check('滚动走查：各折统计齐全',
          all(k in wf for k in ('mean_total', 'mean_bh', 'pos_folds')), str(wf)[:300])
    check('滚动走查：寻优本体有组合数', int(res.get('n_combos') or 0) > 0, str(res.get('n_combos')))
    print(f'     走查 {wf.get("folds")} 折 · 均值 {wf.get("mean_total")}% · 正收益折数 {wf.get("pos_folds")}')


def test_export():
    body = {'dataset_id': DATASET, 'codes': [CODE],
            'models': [{'id': 'M4', 'params': {}}], 'global_params': GLOBAL}
    st, d = req('POST', '/api/runs', body)
    check('导出：先跑一次回测', st == 200 and d.get('ok'), str(d)[:200])
    run = d['data']
    run_id, trades = run['run_id'], sum(len(r['trades']) for r in run['results'])
    check('导出：回测有交易', trades > 0, str(trades))

    st, blob = req('POST', '/api/export', {'run_id': run_id, 'fmt': 'xlsx'}, raw=True)
    check('导出 xlsx：HTTP 200', st == 200, str(st))
    check('导出 xlsx：是 zip 容器', blob[:2] == b'PK', repr(blob[:4]))
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(blob))
    check('导出 xlsx：两个工作表', wb.sheetnames == ['指标汇总', '交易明细'], str(wb.sheetnames))
    summary_rows = wb['指标汇总'].max_row - 1
    trade_rows = wb['交易明细'].max_row - 1
    check('导出 xlsx：汇总行数 = 结果组数', summary_rows == len(run['results']),
          f'{summary_rows} vs {len(run["results"])}')
    check('导出 xlsx：交易行数 = 交易明细数', trade_rows == trades, f'{trade_rows} vs {trades}')
    hdr2 = [c.value for c in wb['交易明细'][1]]
    check('导出 xlsx：交易明细含「入场策略」列', '入场策略' in hdr2, str(hdr2))
    ei = hdr2.index('入场策略') if '入场策略' in hdr2 else -1
    first_entry = (wb['交易明细'].cell(row=2, column=ei + 1).value
                   if ei >= 0 and trade_rows > 0 else None)
    check('导出 xlsx：入场策略已写入命中说明', bool(first_entry), repr(first_entry))

    st, text = req('POST', '/api/export', {'run_id': run_id, 'fmt': 'csv'}, raw=True)
    rows = list(csv.reader(io.StringIO(text.decode('utf-8-sig'))))
    lines = text.decode('utf-8-sig').strip().splitlines()
    check('导出 csv：表头正确', lines[0].startswith('code,model_id,seq,buy_date'), lines[0][:80])
    check('导出 csv：数据行 = 交易数', len(rows) - 1 == trades, f'{len(rows)-1} vs {trades}')
    check('导出 csv：含 entry_reason 列且有值',
          'entry_reason' in rows[0] and bool(rows[1][rows[0].index('entry_reason')]),
          rows[0][:6] if rows else '')

    st, md = req('POST', '/api/export', {'run_id': run_id, 'fmt': 'md'}, raw=True)
    check('导出 md：有标题与交易表', md.decode('utf-8').startswith('# 回测结果导出')
          and '交易明细' in md.decode('utf-8'), md[:60])


def test_bad_csv():
    data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend', 'data'))
    path = os.path.join(data_dir, '_bad_import_check.csv')
    # 收盘价 > 最高价：importer 必须拒绝，不允许半条数据入库
    with open(path, 'w', encoding='utf-8') as f:
        f.write('date,code,open,high,low,close,volume\n')
        f.write('2026-01-05,600186,10.0,9.5,9.0,10.5,1000\n')
        f.write('2026-01-06,600186,10.0,10.1,9.9,10.0,1200\n')
    before = dataset_count()
    try:
        st, d = req('POST', '/api/datasets/seed', {'path': path, 'name': 'bad-import'})
        check('异常 CSV：返回 4xx', 400 <= st < 500, f'{st} {str(d)[:200]}')
        check('异常 CSV：给出原因', bool(d.get('error')), str(d)[:200])
        after = dataset_count()
        check('异常 CSV：没有产生半成品数据集', after == before, f'{before} -> {after}')
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_concurrency():
    plan_body = {'dataset_id': DATASET, 'code': CODE, 'model_id': 'M4',
                 'params': {'entry': 'ma_cross', 'ma_n': 3},
                 'global_params': GLOBAL, 'research': True,
                 'account': {'mode': 'manual_actual', 'cash': 50000, 'shares': 0}}
    with ThreadPoolExecutor(max_workers=8) as ex:
        outs = list(ex.map(lambda _: req('POST', '/api/plan', plan_body), range(8)))
    check('并发计划：全部 200', all(st == 200 for st, _ in outs),
          str([st for st, _ in outs]))
    ids = {d['data']['identity']['plan_id'] for st, d in outs}
    check('并发计划：plan_id 一致（确定性）', len(ids) == 1, str(ids))

    run_body = {'dataset_id': DATASET, 'codes': [CODE],
                'models': [{'id': 'M4', 'params': {}}], 'global_params': GLOBAL}
    with ThreadPoolExecutor(max_workers=4) as ex:
        outs = list(ex.map(lambda _: req('POST', '/api/runs', run_body), range(4)))
    check('并发回测：全部 200', all(st == 200 for st, _ in outs), str([st for st, _ in outs]))
    totals = {round((d['data']['results'][0]['metrics']['total_pct']), 6) for st, d in outs}
    check('并发回测：结果一致', len(totals) == 1, str(totals))


def main():
    print(f'目标：{BASE}')
    test_walk_forward()
    test_export()
    test_bad_csv()
    test_concurrency()
    print('\nALL PASS')


if __name__ == '__main__':
    try:
        main()
    except AssertionError as e:
        print(f'\n✗ 失败：{e}')
        sys.exit(1)
    except Exception as e:
        print(f'\n✗ 异常：{type(e).__name__}: {e}')
        sys.exit(1)
