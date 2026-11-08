# -*- coding: utf-8 -*-
"""计划跟踪（BUY-12）：计划快照落库 + 实际成交追加录入。

三条硬规矩：
  1. **计划快照不可变**。`plan` 列存生成那一刻的完整 JSON，之后无论行情/参数怎么变都不改；
     复盘读的是这份快照，所以「不能用后来的参数重写原始计划」是数据结构保证的，不是约定。
  2. **成交只追加**。一笔实际成交 = 一行 `plan_fill`，删错记录再补录，不做原地覆盖，
     否则「为什么这笔没按计划成交」就再也查不清。
  3. **指纹去重**。plan_id 是内容寻址的（数据哈希+代码+模型+有效参数+执行配置+账户快照），
     同一输入重复点「保存」不应堆出十几条一样的记录：未成交的旧记录直接复用。
"""
import json
from datetime import datetime

from engine.version import ENGINE_VERSION
from .repository import write_tx

SIDES = ('buy', 'sell')
LIFECYCLES = ('planned', 'filled', 'closed', 'cancelled')

LIFECYCLE_TEXT = {
    'planned': '已生成，未录入成交',
    'filled': '已买入，持仓中',
    'closed': '已卖出，结清',
    'cancelled': '已取消',
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def _loads(text, default):
    try:
        v = json.loads(text or '')
        return v if isinstance(v, type(default)) else default
    except (TypeError, ValueError):
        return default


def _row_to_rec(row, cols, *, with_plan: bool = True) -> dict:
    rec = dict(zip(cols, row))
    for k, dflt in (('params', {}), ('account', {}), ('rules', {})):
        rec[k] = _loads(rec.get(k), dflt)
    if with_plan and 'plan' in rec:
        rec['plan'] = _loads(rec.get('plan'), {})
    return rec


# ---------------- 写入 ----------------
def create(conn, *, plan: dict, dataset_id: int, code: str, model_id: str, as_of: str,
           params: dict, config_hash: str = '', account: dict | None = None,
           rules: dict | None = None, dedupe: bool = True,
           renewed_from: int | None = None) -> tuple[int, bool]:
    """落库一份计划快照，返回 (record_id, reused)。

    `reused=True` 表示命中同 plan_id 且仍「未成交」的旧记录——只刷新 updated_at，
    不新增行；这样用户反复生成/保存不会把列表刷满重复项。
    已被续期取代的记录不参与复用（否则续期会绕回旧记录）。
    """
    plan_id = (plan.get('validity') or {}).get('plan_id') or (plan.get('identity') or {}).get('plan_id')
    if not plan_id:
        raise ValueError('计划缺少 plan_id，无法落库')
    account = dict(account or (plan.get('context') or {}).get('account_snapshot') or {})
    rules = dict(rules or {})
    now = _now()

    if dedupe:
        cur = conn.cursor()
        cur.execute("SELECT id,status,account,rules FROM plan_record WHERE plan_id=? AND lifecycle='planned' "
                    "AND cancel_reason IS NULL AND superseded_by IS NULL "
                    "ORDER BY id DESC LIMIT 1", (plan_id,))
        row = cur.fetchone()
        if row:
            rid, old_status, old_acct, old_rules = row
            cur.execute('SELECT COUNT(*) FROM plan_fill WHERE record_id=?', (rid,))
            if cur.fetchone()[0] == 0:
                # 同一 plan_id = 影响结论的输入完全一致（F02 已把整份账户/规则/研究模式/数据状态
                # 纳入指纹），所以旧快照就是这次的结果，只需刷新 updated_at，快照保持原样。
                # 例外：旧版本指纹不全留下的脏记录可能「status 与快照互相矛盾」，
                # 这种情况下整体原子替换，宁可覆盖也不再保留一份自相矛盾的快照。
                same = (old_status == (plan.get('status') or '')
                        and _canonical(_loads(old_acct, {})) == _canonical(account)
                        and _canonical(_loads(old_rules, {})) == _canonical(rules))
                with write_tx(conn) as c:
                    if same:
                        c.execute('UPDATE plan_record SET updated_at=? WHERE id=?', (now, rid))
                    else:
                        c.execute('UPDATE plan_record SET updated_at=?, status=?, account=?, rules=?, plan=? '
                                  'WHERE id=?',
                                  (now, plan.get('status') or '', _canonical(account),
                                   _canonical(rules), _canonical(plan), rid))
                return rid, True

    with write_tx(conn) as c:
        cur = c.cursor()
        cur.execute(
            'INSERT INTO plan_record (plan_id,dataset_id,dataset_hash,code,model_id,as_of,status,'
            'lifecycle,cancel_reason,params,config_hash,account,rules,plan,renewed_from,'
            'engine_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (plan_id, int(dataset_id), (plan.get('identity') or {}).get('dataset_hash') or '',
             code, model_id, as_of, plan.get('status') or '', 'planned', None,
             _canonical(params or {}), config_hash or '', _canonical(account), _canonical(rules),
             _canonical(plan), renewed_from, ENGINE_VERSION, now, now))
        return cur.lastrowid, False


def mark_superseded(conn, old_id: int, new_id: int) -> None:
    """把旧计划标记为「已被新计划取代」。

    只写 `superseded_by` 一列：不动 `lifecycle`、更不动 `plan` 快照。
    续期不是取消（用户没有放弃，只是行情变了要重新算），也不是成交，
    所以生命周期保持原样，由关联字段表达「这条已经不用再看了」。
    """
    with write_tx(conn) as c:
        c.execute('UPDATE plan_record SET superseded_by=?, updated_at=? WHERE id=?',
                  (int(new_id), _now(), int(old_id)))


def recompute_lifecycle(conn, record_id: int) -> str:
    """按成交汇总重算生命周期（取消状态优先，其次是买入/卖出）。"""
    cur = conn.cursor()
    cur.execute('SELECT cancel_reason FROM plan_record WHERE id=?', (record_id,))
    row = cur.fetchone()
    if not row:
        raise LookupError('计划记录不存在')
    cancel_reason = row[0]
    cur.execute("SELECT side, COALESCE(SUM(qty),0) FROM plan_fill WHERE record_id=? GROUP BY side",
                (record_id,))
    agg = {s: q for s, q in cur.fetchall()}
    buy, sell = float(agg.get('buy') or 0), float(agg.get('sell') or 0)
    if cancel_reason:
        lc = 'cancelled'
    elif buy <= 0:
        lc = 'planned'
    elif sell >= buy - 1e-9:
        lc = 'closed'
    else:
        lc = 'filled'
    with write_tx(conn) as c:
        c.execute('UPDATE plan_record SET lifecycle=?, updated_at=? WHERE id=?', (lc, _now(), record_id))
    return lc


def add_fill(conn, record_id: int, *, side: str, qty: float, price: float, fees: float = 0.0,
             trade_date: str, note: str | None = None) -> dict:
    """录入一笔**实际**成交。校验比「能不能买」宽松，但拒绝无法解释的输入。"""
    if side not in SIDES:
        raise ValueError(f'未知成交方向: {side!r}（应为 buy/sell）')
    qty, price, fees = float(qty), float(price), float(fees or 0)
    if qty <= 0:
        raise ValueError('成交数量必须为正')
    if price <= 0:
        raise ValueError('成交价格必须为正')
    if fees < 0:
        raise ValueError('费用不能为负')
    rec = get_record(conn, record_id)
    if not rec:
        raise LookupError('计划记录不存在')
    if rec['lifecycle'] == 'cancelled':
        raise ValueError('该计划已标记取消；如需补录成交，请先撤销取消')
    if not trade_date:
        raise ValueError('缺少成交日期')
    if trade_date <= rec['as_of']:
        raise ValueError(f'成交日 {trade_date} 不晚于计划依据日 {rec["as_of"]}：'
                         f'信号确认后才能成交，请检查日期')
    if side == 'sell':
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(CASE WHEN side='buy' THEN qty ELSE -qty END),0) "
                    "FROM plan_fill WHERE record_id=?", (record_id,))
        held = float(cur.fetchone()[0] or 0)
        if qty > held + 1e-9:
            raise ValueError(f'卖出 {qty:g} 股超过已录入持仓 {held:g} 股；请先补录买入')
    with write_tx(conn) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO plan_fill (record_id,side,qty,price,fees,trade_date,note,created_at) '
                    'VALUES (?,?,?,?,?,?,?,?)',
                    (record_id, side, qty, price, fees, trade_date, note, _now()))
        fid = cur.lastrowid
    lifecycle = recompute_lifecycle(conn, record_id)
    out = get_fill(conn, fid) or {}
    out['lifecycle'] = lifecycle
    return out


def delete_fill(conn, record_id: int, fill_id: int) -> bool:
    with write_tx(conn) as c:
        cur = c.cursor()
        cur.execute('DELETE FROM plan_fill WHERE id=? AND record_id=?', (fill_id, record_id))
        ok = cur.rowcount > 0
    if ok:
        recompute_lifecycle(conn, record_id)
    return ok


def cancel(conn, record_id: int, reason: str) -> dict:
    rec = get_record(conn, record_id)
    if not rec:
        raise LookupError('计划记录不存在')
    if rec['lifecycle'] == 'filled':
        raise ValueError('该计划已有买入成交，取消没有意义；如需结束请录入卖出')
    if rec['lifecycle'] == 'closed':
        raise ValueError('该计划已结清，无需取消')
    with write_tx(conn) as c:
        c.execute('UPDATE plan_record SET cancel_reason=?, lifecycle=?, updated_at=? WHERE id=?',
                  (reason or '用户取消', 'cancelled', _now(), record_id))
    return get_record(conn, record_id) or {}


def reopen(conn, record_id: int) -> dict:
    """撤销取消（误点恢复）。"""
    rec = get_record(conn, record_id)
    if not rec:
        raise LookupError('计划记录不存在')
    with write_tx(conn) as c:
        c.execute('UPDATE plan_record SET cancel_reason=NULL, updated_at=? WHERE id=?',
                  (_now(), record_id))
    recompute_lifecycle(conn, record_id)
    return get_record(conn, record_id) or {}


def delete_record(conn, record_id: int) -> bool:
    with write_tx(conn) as c:
        cur = c.cursor()
        cur.execute('DELETE FROM plan_fill WHERE record_id=?', (record_id,))
        cur.execute('DELETE FROM plan_record WHERE id=?', (record_id,))
        return cur.rowcount > 0


# ---------------- 读取 ----------------
def list_records(conn, *, code: str | None = None, model_id: str | None = None,
                 lifecycle: str | None = None, include_plan: bool = False,
                 limit: int = 50) -> list[dict]:
    """列表默认不带 plan 全文（一条计划 JSON 可能几十 KB），只给摘要 + 成交汇总。"""
    cols = ('id,plan_id,dataset_id,dataset_hash,code,model_id,as_of,status,lifecycle,'
            'cancel_reason,params,config_hash,account,rules,renewed_from,superseded_by,'
            'engine_version,created_at,updated_at')
    if include_plan:
        cols += ',plan'
    sql = f'SELECT {cols} FROM plan_record'
    where, args = [], []
    for col, val in (('code', code), ('model_id', model_id), ('lifecycle', lifecycle)):
        if val:
            where.append(f'{col}=?')
            args.append(val)
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(max(1, min(int(limit), 500)))
    cur = conn.cursor()
    cur.execute(sql, args)
    names = [d[0] for d in cur.description]
    recs = [_row_to_rec(r, names, with_plan=include_plan) for r in cur.fetchall()]
    for rec in recs:
        rec['fills_count'] = _fill_count(conn, rec['id'])
    return recs


def _fill_count(conn, record_id: int) -> int:
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM plan_fill WHERE record_id=?', (record_id,))
    return int(cur.fetchone()[0])


def get_record(conn, record_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute('SELECT * FROM plan_record WHERE id=?', (record_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    rec = _row_to_rec(row, cols)
    rec['fills_count'] = _fill_count(conn, record_id)   # 详情与列表字段保持一致，前端不必兜底
    return rec


def last_record(conn, *, code: str, model_id: str | None = None) -> dict | None:
    """该股票最近一次保存的计划（BUY-10：刷新页面后恢复，而不是从空白开始）。

    两层匹配：先找「同股票 + 同模型」，找不到再退到「同股票任意模型」——
    刷新后前端回到默认模型，如果严格要求模型一致，用户刚保存的计划就“消失”了。
    不锁 dataset_id 同理：重新抓取会生成新数据集，旧数据集不会自己变长。
    """
    cur = conn.cursor()
    if model_id:
        cur.execute('SELECT id FROM plan_record WHERE code=? AND model_id=? '
                    'ORDER BY id DESC LIMIT 1', (code, model_id))
        row = cur.fetchone()
        if row:
            return get_record(conn, row[0])
    cur.execute('SELECT id FROM plan_record WHERE code=? ORDER BY id DESC LIMIT 1', (code,))
    row = cur.fetchone()
    return get_record(conn, row[0]) if row else None

def list_fills(conn, record_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM plan_fill WHERE record_id=? ORDER BY trade_date, id', (record_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_fill(conn, fill_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute('SELECT * FROM plan_fill WHERE id=?', (fill_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def stats(conn) -> dict:
    cur = conn.cursor()
    cur.execute('SELECT lifecycle, COUNT(*) FROM plan_record GROUP BY lifecycle')
    by = {k: n for k, n in cur.fetchall()}
    cur.execute('SELECT COUNT(*) FROM plan_record')
    return {'total': cur.fetchone()[0], 'by_lifecycle': by}
