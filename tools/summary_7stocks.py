# -*- coding: utf-8 -*-
"""对默认数据集内的全部个股，批量跑「默认参数回测 + M4 寻优三口径」，产出汇总对照表。

输出：
  - stock_data_300d/7股回测寻优汇总.csv
  - stock_data_300d/7股回测寻优汇总.md
  - stock_data_300d/7股回测寻优汇总.xlsx
依赖：backend 引擎（纯标准库）。用法：python tools/summary_7stocks.py
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "backend")
sys.path.insert(0, os.path.abspath(BACKEND))

import core.state as state  # noqa: E402
from db import repository  # noqa: E402
from engine.cost import Cost  # noqa: E402
from engine.models.builtin import MODELS, dp_oracle_result  # noqa: E402
from engine.backtest import run  # noqa: E402
from engine.optimize import optimize  # noqa: E402

CAPITAL = 100000.0
COST = Cost()
TEST_DAYS = 61

# 顶栏默认展示参数（与前端 store 默认一致）
DEFAULT_PARAMS = {
    "M1": {},
    "M3": {"ma_n": 20, "min_hold": 3, "max_hold": 15},
    "M4": {"ma_n": 20, "min_hold": 3, "max_hold": 12, "tp": 9, "sl": 12},
    "M6": {"ma_n": 20, "min_hold": 3},
}

NAMES = {
    "600186": "莲花控股", "000962": "东方钽业", "002896": "中大力德",
    "603220": "中贝通信", "603887": "城地香江", "603757": "大元泵业",
    "300001": "特锐德",
}


def pct(x):
    return "" if x is None else round(x, 2)


def main():
    conn = state.get_conn()
    ds = repository.list_datasets(conn)
    if not ds:
        print("无数据集，请先启动后端种子化")
        return
    ds_id = ds[0]["id"]
    codes = ds[0]["codes"]
    if isinstance(codes, str):
        codes = __import__("json").loads(codes)

    rows = []
    for code in codes:
        bars = repository.get_bars(conn, ds_id, code)
        if not bars:
            continue
        name = NAMES.get(code, code)

        # 默认参数回测（全样本）
        back = {}
        for mid in ("M1", "M3", "M4", "M6"):
            r = run(bars, MODELS[mid], DEFAULT_PARAMS[mid], COST, CAPITAL)
            back[mid] = r.metrics
        # M2 DP 上界（全样本，对照，不可交易）
        m2 = dp_oracle_result(bars, int(DEFAULT_PARAMS["M4"].get("min_hold", 3)), COST, CAPITAL)
        back["M2"] = m2["metrics"]

        # M4 寻优三口径（训练/测试切分）
        opt = optimize(bars, "M4", MODELS["M4"].grid, COST, CAPITAL,
                       test_days=TEST_DAYS, objective="expectancy", min_trades=5)
        honest = opt["honest"] or {}
        oracle = opt["oracle"] or {}
        biased = opt["biased"] or {}
        bh_test = opt.get("bh_test")

        rows.append({
            "code": code,
            "name": name,
            "n": len(bars),
            "bh": pct(back["M1"]["total_pct"]),               # 买入持有 全样本
            "m3": pct(back["M3"]["total_pct"]),
            "m4": pct(back["M4"]["total_pct"]),
            "m4_trades": back["M4"]["n_trades"],
            "m4_win": pct(back["M4"]["win_rate"]),
            "m4_exp": pct(back["M4"]["expectancy_pct"]),
            "m4_mdd": pct(back["M4"]["max_drawdown_pct"]),
            "m6": pct(back["M6"]["total_pct"]),
            "m2": pct(back["M2"]["total_pct"]),               # DP 上界 全样本
            # 样本外（测试段，最近 TEST_DAYS 日）
            "honest_exp": pct(honest.get("expectancy_pct")),
            "honest_total": pct(honest.get("total_pct")),
            "biased_exp": pct(biased.get("expectancy_pct")),
            "oracle_exp": pct(oracle.get("expectancy_pct")),
            "oracle_total": pct(oracle.get("total_pct")),
            "bh_test": pct(bh_test),
            "verdict": opt["verdict"],
            "opt_bias": pct(opt.get("optimistic_bias")),
            "transfer_loss": pct(opt.get("transfer_loss")),
        })

    # ---- 输出目录 ----
    out_dir = os.path.join(HERE, "..", "stock_data_300d")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, "7股回测寻优汇总")

    # CSV
    fields = list(rows[0].keys())
    with open(base + ".csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # Markdown
    md = ["# 7 只个股 · 回测与寻优汇总对照表", "",
          f"> 数据源：默认数据集（{rows[0]['n']} 交易日/只，前复权，本金 10 万，费用：佣金0.025%双向(最低5元)/印花税0.05%卖出/过户费0.001%双向）。",
          "> 回测列为**全样本**默认参数；寻优列为**样本外**（最近 61 日，M4 止盈止损）。红绿灯：🔴不可用 / 🟡谨慎 / 🟢可用。",
          "> M2(DP上界)为事后最优、不可交易，仅作收益空间参照。", "",
          "## 一、核心对照（全样本策略 + 样本外寻优结论）", ""]
    hdr = ["代码", "名称", "买入持有%", "M3均线%", "M4止盈止损%", "M6下跌空仓%",
           "M4样本外诚实期望%", "红绿灯", "M4_Oracle期望%", "M2_DP上界%"]
    keys = ["code", "name", "bh", "m3", "m4", "m6", "honest_exp", "verdict", "oracle_exp", "m2"]
    md.append("| " + " | ".join(hdr) + " |")
    md.append("|" + "|".join(["---"] * len(hdr)) + "|")
    vmap = {"red": "🔴不可用", "yellow": "🟡谨慎", "green": "🟢可用"}
    for r in rows:
        cells = [str(r[k]) for k in keys]
        cells[keys.index("verdict")] = vmap.get(r["verdict"], r["verdict"])
        md.append("| " + " | ".join(cells) + " |")

    md.append("")
    md.append("## 二、明细（笔数 / 胜率 / 期望 / 回撤 / 迁移损失 / 乐观偏差）")
    md.append("")
    hdr2 = ["代码", "名称", "M4笔数", "M4胜率%", "M4期望%", "M4回撤%",
            "诚实总收益%", "乐观期望%", "Oracle总收益%", "测试段买入持有%", "乐观偏差", "迁移损失"]
    keys2 = ["code", "name", "m4_trades", "m4_win", "m4_exp", "m4_mdd",
             "honest_total", "biased_exp", "oracle_total", "bh_test", "opt_bias", "transfer_loss"]
    md.append("| " + " | ".join(hdr2) + " |")
    md.append("|" + "|".join(["---"] * len(hdr2)) + "|")
    for r in rows:
        md.append("| " + " | ".join(str(r[k]) for k in keys2) + " |")
    md.append("")
    md.append("## 三、解读")
    md.append("")
    red = [f"{r['name']}({r['code']})" for r in rows if r["verdict"] == "red"]
    green = [f"{r['name']}({r['code']})" for r in rows if r["verdict"] == "green"]
    md.append(f"- 样本外诚实口径判🔴（不可用）共 {len(red)} 只：{', '.join(red) or '无'}。")
    md.append(f"- 样本外诚实口径判🟢（可用）共 {len(green)} 只：{', '.join(green) or '无'}。")
    md.append("- 普遍现象：M4 全样本（默认参数）收益可观，但样本外诚实期望多为负/偏低——印证全样本最优/默认参数不可轻信，须以诚实口径为决策依据。")
    md.append("- DP 上界（M2）显著高于所有可执行策略，说明区间内存在可盈利波段，但属于事后已知，实战不可达。")
    md.append("")
    md.append("---")
    md.append("> 历史回测与样本外切分均不代表未来收益；本表仅供策略研究与教学，不构成投资建议。")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # xlsx
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = Workbook()
        ws = wb.active
        ws.title = "核心对照"
        ws.append(hdr)
        for r in rows:
            cells = [r[k] for k in keys]
            cells[keys.index("verdict")] = vmap.get(r["verdict"], r["verdict"])
            ws.append(cells)
        ws2 = wb.create_sheet("明细")
        ws2.append(hdr2)
        for r in rows:
            ws2.append([r[k] for k in keys2])
        for sh in (ws, ws2):
            for c in sh[1]:
                c.font = Font(bold=True)
            for col in sh.columns:
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                sh.column_dimensions[col[0].column_letter].width = min(width + 2, 22)
        wb.save(base + ".xlsx")
    except Exception as e:
        print("xlsx skipped:", e)

    print("OK rows=", len(rows))
    print(base + ".csv / .md / .xlsx")


if __name__ == "__main__":
    main()
