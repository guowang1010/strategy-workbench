# -*- coding: utf-8 -*-
"""在线行情拉取：多股票 + 时间区间 → 与 ingest_csv 兼容的 CSV 落盘。

数据源（与 stock-daily-quote-crawler skill 同源）：
  - 搜狐证券：日线 OHLC / 量 / 额 / 换手率
  - 腾讯财经：前复权与后复权 K 线（推复权因子）、股票静态信息（名称/股本/市值）、上证指数交易日历
  - 同花顺：上市日期（可选，失败不中断）

窗口规则：给 start（YYYY-MM-DD）则取 [start, end] 区间交易日；否则取最近 days 个交易日。
输出列与 backend/data/*.csv 完全一致，可直接 ingest_csv 入库。
仅用标准库 urllib，不新增依赖。
"""
import csv
import json
import os
import re
import urllib.request

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

# 与 ingest_csv 兼容的表头（HEAD 同 crawl_stocks.py）
HEAD = ['序号', '交易日期', '股票代码', '股票名称', '交易所', '交易状态',
        '开盘价', '最高价', '最低价', '收盘价', '前收盘价', '涨跌额', '涨跌幅(%)',
        '成交量(手)', '成交量(股)', '成交额(元)', '换手率(%)', '复权因子',
        'ST状态', '停牌标记', '退市标记']


def _get(u, ref=None, timeout=40):
    h = {'User-Agent': UA}
    if ref:
        h['Referer'] = ref
    return urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=timeout).read()


def _num(x):
    x = str(x).replace('%', '')
    return 0.0 if x in ('-', '', 'None') else float(x)


# 腾讯行情域名按序兜底：2026-11 起 web.ifzq.gtimg.cn 对本接口返回 501，
# 主域 ifzq.gtimg.cn 正常；旧域名保留在后，接口恢复时自动切回。
_TX_HOSTS = ('https://ifzq.gtimg.cn', 'https://web.ifzq.gtimg.cn')


def _tx(param):
    last = None
    for host in _TX_HOSTS:
        try:
            raw = _get(f'{host}/appstock/app/fqkline/get?param={param}')
            return json.loads(raw.decode())['data']
        except Exception as e:          # 单域名故障（超时/5xx/结构变化）不中断，换下一个
            last = e
    raise RuntimeError(f'腾讯行情接口不可用（{_TX_HOSTS}）: {last}')


def _parse_kline(arr):
    return {r[0]: {'open': float(r[1]), 'close': float(r[2]), 'high': float(r[3]),
                   'low': float(r[4]), 'vol_hand': float(r[5])} for r in arr}


def _window(start=None, end=None, days=120):
    """交易日窗口：start 给定取 [start,end]，否则取最近 days 个交易日。"""
    idx = sorted(_tx('sh000001,day,,,600,')['sh000001']['day'], key=lambda r: r[0])
    dates = [r[0] for r in idx]
    if end:
        dates = [d for d in dates if d <= end]
    if start:
        w = [d for d in dates if d >= start]
        if w:
            return w
    return dates[-days:] if len(dates) >= days else dates


def _fetch_sohu(code):
    u = (f'https://q.stock.sohu.com/hisHq?code=cn_{code}&start=20200101&end=20991231'
         '&stat=1&order=D&period=d&rt=json')
    d = json.loads(_get(u, 'https://q.stock.sohu.com/').decode('gbk', 'ignore'))
    d = d[0] if isinstance(d, list) else d
    out = [{'date': r[0], 'open': _num(r[1]), 'close': _num(r[2]), 'change': _num(r[3]),
            'pct': _num(r[4]), 'low': _num(r[5]), 'high': _num(r[6]),
            'vol_hand': _num(r[7]), 'amount_wan': _num(r[8]), 'turnover': _num(r[9])}
           for r in d.get('hq', [])]
    out.sort(key=lambda x: x['date'])
    return out


def _static_info(code, prefix):
    """腾讯静态信息：名称/股本/市值/ST。失败返回占位。"""
    info = {'name': code, 'total_share': 0, 'float_share': 0,
            'total_mv': 0.0, 'float_mv': 0.0, 'st': '否'}
    try:
        qt = _tx(f'{prefix}{code},day,,,1,')[f'{prefix}{code}']['qt'][f'{prefix}{code}']
        # 腾讯返回的名称常带排版空格（如「五 粮 液」），去掉以便下拉/汇总里对齐显示
        info['name'] = str(qt[1]).replace(' ', '')
        info['total_share'] = int(qt[73])
        info['float_share'] = int(qt[72])
        info['total_mv'] = float(qt[45]) * 1e8
        info['float_mv'] = float(qt[44]) * 1e8
        info['st'] = '是' if 'ST' in qt[1].upper() else '否'
    except Exception:
        pass
    return info


def _list_date(code):
    try:
        raw = _get(f'http://d.10jqka.com.cn/v6/line/hs_{code}/00/all.js',
                   'http://stockpage.10jqka.com.cn/').decode('gbk', 'ignore')
        m = re.search(r'"start":"(\d{8})"', raw)
        if m:
            s = m.group(1)
            return f'{s[:4]}-{s[4:6]}-{s[6:]}'
    except Exception:
        pass
    return ''


def crawl_one(code, window):
    """拉取单只股票，返回 (HEAD 行列表, 摘要)。失败抛异常。"""
    prefix = 'sh' if code.startswith('6') else 'sz'
    sohu = _fetch_sohu(code)
    raw = _parse_kline(_tx(f'{prefix}{code},day,,,600,')[f'{prefix}{code}']['day'])
    hfq_raw = _tx(f'{prefix}{code},day,,,600,hfq')[f'{prefix}{code}']
    hfq = _parse_kline(hfq_raw.get('hfqday', hfq_raw.get('day', [])))
    start, end = window[0], window[-1]
    rows = [r for r in sohu if start <= r['date'] <= end]
    if not rows:
        raise ValueError(f'{code}: 窗口 {start}~{end} 内无行情（上市日晚于窗口起点或代码错误）')
    factors = {r['date']: round(hfq[r['date']]['close'] / raw[r['date']]['close'], 6)
               for r in rows if r['date'] in hfq and r['date'] in raw}
    info = _static_info(code, prefix)
    out = []
    for i, r in enumerate(rows, 1):
        prev = round(r['close'] - r['change'], 2)
        vh = int(r['vol_hand'])
        out.append([i, r['date'], code, info['name'],
                    'SSE' if prefix == 'sh' else 'SZSE',
                    '正常交易' if vh else '停牌',
                    r['open'], r['high'], r['low'], r['close'], prev, r['change'], r['pct'],
                    vh, vh * 100, round(r['amount_wan'] * 10000, 2), r['turnover'],
                    factors.get(r['date'], 1.0), info['st'], '否', '否'])
    # 缺失口径：只把「上市/首根行情之后」没数据的日子算缺失（停牌）；
    # 上市晚于窗口起点是正常情况（新股），不冒充数据缺失。
    first_date = rows[0]['date']
    dates_in = {r['date'] for r in rows}
    summary = {'code': code, 'name': info['name'], 'rows': len(out),
               'missing': [d for d in window if d >= first_date and d not in dates_in],
               'before_listing': sum(1 for d in window if d < first_date),
               'first_date': first_date,
               'list_date': _list_date(code)}
    return out, summary


def crawl(codes, start=None, end=None, days=120, out_dir=None, csv_name=None):
    """批量拉取：多股票 + 区间。返回 {csv 路径, 每股摘要, 失败列表}。

    单只失败不中断整体（记入 errors），至少一只成功才落 CSV。
    """
    window = _window(start, end, days)
    all_rows, summaries, errors = [], [], []
    for code in codes:
        try:
            rows, summary = crawl_one(code, window)
            all_rows.extend(rows)
            summaries.append(summary)
        except Exception as e:
            errors.append({'code': code, 'error': str(e)})
    if not all_rows:
        raise ValueError('全部股票拉取失败: ' + '; '.join(e['error'] for e in errors))
    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    os.makedirs(out_dir, exist_ok=True)
    csv_name = csv_name or f"crawl_{'_'.join(codes[:8])}_{window[0]}_{window[-1]}.csv"
    path = os.path.join(out_dir, csv_name)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        w.writerows(all_rows)
    return {'path': path, 'window': [window[0], window[-1]], 'trading_days': len(window),
            'summaries': summaries, 'errors': errors, 'rows': len(all_rows)}
