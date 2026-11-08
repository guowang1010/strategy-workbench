# -*- coding: utf-8 -*-
"""导出接口：POST /export，支持 xlsx / csv / md。"""
import csv
import io
import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel

import core.state as state
from db import repository

router = APIRouter(prefix='/api')

OUT_DIR = os.path.join(state.BACKEND_DIR, 'exports')
os.makedirs(OUT_DIR, exist_ok=True)


class ExportReq(BaseModel):
    run_id: str
    fmt: str = 'csv'          # xlsx | csv | md


def build_csv(run):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['code', 'model_id', 'seq', 'buy_date', 'buy_price', 'sell_date', 'sell_price',
                'hold_days', 'gross_pct', 'net_pct', 'entry_reason', 'exit_reason',
                'mfe_pct', 'mae_pct'])
    for r in run['results']:
        for t in r['trades']:
            w.writerow([r['code'], r['model_id'], t['seq'], t['buy_date'], t['buy_price'],
                        t['sell_date'], t['sell_price'], t['hold_days'], t['gross_pct'],
                        t['net_pct'], t.get('entry_reason', ''), t['exit_reason'],
                        t['mfe_pct'], t['mae_pct']])
    return buf.getvalue()


def build_md(run):
    lines = [f'# 回测结果导出  {run["id"]}', '', '## 指标汇总', '',
             '| 代码 | 模型 | 总收益% | 笔数 | 胜率% | 期望% | 回撤% | 暴露% |',
             '|---|---|---|---|---|---|---|---|']
    for r in run['results']:
        m = r['metrics']
        lines.append(f'| {r["code"]} | {r["model_id"]} | {m["total_pct"]} | {m["n_trades"]} | '
                     f'{m["win_rate"]} | {m["expectancy_pct"]} | {m["max_drawdown_pct"]} | {m["exposure_pct"]} |')
    lines += ['', '## 交易明细', '',
              '| # | 买入日 | 买价 | 卖出日 | 卖价 | 持天 | 净收益% | 入场策略 | 出场 |',
              '|---|---|---|---|---|---|---|---|---|']
    for r in run['results']:
        for t in r['trades']:
            lines.append(f'| {t["seq"]} | {t["buy_date"]} | {t["buy_price"]} | {t["sell_date"]} | '
                         f'{t["sell_price"]} | {t["hold_days"]} | {t["net_pct"]} | '
                         f'{t.get("entry_reason", "")} | {t["exit_reason"]} |')
    return '\n'.join(lines)


def build_xlsx(run):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active
    ws.title = '指标汇总'
    head = ['代码', '模型', '总收益%', '笔数', '胜率%', '盈亏比', '期望%', '平均盈%', '平均亏%',
            '回撤%', '暴露%', '平均持仓', '年化%', '夏普', 'Calmar', '期末权益']
    ws.append(head)
    for r in run['results']:
        m = r['metrics']
        ws.append([r['code'], r['model_id'], m['total_pct'], m['n_trades'], m['win_rate'],
                   m['profit_factor'], m['expectancy_pct'], m['avg_win_pct'], m['avg_loss_pct'],
                   m['max_drawdown_pct'], m['exposure_pct'], m['avg_hold_days'], m['ann_return_pct'],
                   m['sharpe'], m['calmar'], m['final_equity']])
    ws2 = wb.create_sheet('交易明细')
    h2 = ['代码', '模型', '#', '买入日', '买价', '卖出日', '卖价', '持天', '毛收益%', '净收益%',
          '入场策略', '出场', 'MFE%', 'MAE%']
    ws2.append(h2)
    for r in run['results']:
        for t in r['trades']:
            ws2.append([r['code'], r['model_id'], t['seq'], t['buy_date'], t['buy_price'],
                        t['sell_date'], t['sell_price'], t['hold_days'], t['gross_pct'], t['net_pct'],
                        t.get('entry_reason', ''), t['exit_reason'], t['mfe_pct'], t['mae_pct']])
    for sh in (ws, ws2):
        for c in sh[1]:
            c.font = Font(bold=True)
        for col in sh.columns:
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            sh.column_dimensions[col[0].column_letter].width = min(width + 2, 40)
    path = os.path.join(OUT_DIR, f'{run["id"]}.xlsx')
    wb.save(path)
    return path


@router.post('/export')
def export(req: ExportReq):
    with state.db() as conn:
        run = repository.get_run(conn, req.run_id)
    if not run:
        raise HTTPException(404, '运行记录不存在')
    if req.fmt == 'csv':
        data = build_csv(run).encode('utf-8-sig')
        return Response(content=data, media_type='text/csv',
                        headers={'Content-Disposition': f'attachment; filename={run["id"]}.csv'})
    if req.fmt == 'md':
        data = build_md(run).encode('utf-8')
        return Response(content=data, media_type='text/markdown',
                        headers={'Content-Disposition': f'attachment; filename={run["id"]}.md'})
    if req.fmt == 'xlsx':
        path = build_xlsx(run)
        return FileResponse(path, filename=os.path.basename(path),
                           media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    raise HTTPException(400, '不支持的格式')
