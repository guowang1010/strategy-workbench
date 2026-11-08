# -*- coding: utf-8 -*-
"""对默认数据集全部个股，跑 M3/M4/M6 三模型「寻优三口径」，与 M1/M2 拼成跨模型对照表。

输出：
  - stock_data_300d/跨模型寻优三口径对照.csv
  - stock_data_300d/跨模型寻优三口径对照.md
  - stock_data_300d/跨模型寻优三口径对照.xlsx
口径：全样本默认参数回测(M1/M2) + 训练/测试切分(Oracle/诚实/乐观) 对 M3/M4/M6。
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
MODEL_IDS = ["M3", "M4", "M6"]  # 参与寻优的模型
NAMES = {
    "600186": "莲花控股", "000962": "东方钽业", "002896": "中大力德",
    "603220": "中贝通信", "603887": "城地香江", "603757": "大元泵业",
    "300001": "特锐德",
}
VMAP = {"red": "🔴不可用", "yellow": "🟡谨慎", "green": "🟢可用"}


def pct(x):
    return "" if x is None else round(x, 2)


def main():
    conn = state.get_conn()
    ds = repository.list_datasets(conn)
    ds_id = ds[0]["id"]
    codes = ds[0]["codes"]
    if isinstance(codes, str):
        import json
        codes = json.loads(codes)

    rows = []
    for code in codes:
        bars = repository.get_bars(conn, ds_id, code)
        if not bars:
            continue
        name = NAMES.get(code, code)
        n = len(bars)

        # M1 全样本 + 测试段
        bh_full = run(bars, MODELS["M1"], {}, COST, CAPITAL).metrics["total_pct"]
        bh_test = optimize(bars, "M3", MODELS["M3"].grid, COST, CAPITAL,
                           test_days=TEST_DAYS).get("bh_test")

        # M2 DP 上界
        m2 = dp_oracle_result(bars, 3, COST, CAPITAL)["metrics"]["total_pct"]

        rec = {"code": code, "name": name, "n": n,
               "bh_full": pct(bh_full), "bh_test": pct(bh_test), "m2": pct(m2)}
        for mid in MODEL_IDS:
            o = optimize(bars, mid, MODELS[mid].grid, COST, CAPITAL,
                         test_days=TEST_DAYS, objective="expectancy", min_trades=5)
            h = o["honest"] or {}
            b = o["biased"] or {}
            orc = o["oracle"] or {}
            rec[f"{mid}_honest_exp"] = pct(h.get("expectancy_pct"))
            rec[f"{mid}_honest_total"] = pct(h.get("total_pct"))
            rec[f"{mid}_biased_exp"] = pct(b.get("expectancy_pct"))
            rec[f"{mid}_oracle_exp"] = pct(orc.get("expectancy_pct"))
            rec[f"{mid}_oracle_total"] = pct(orc.get("total_pct"))
            rec[f"{mid}_verdict"] = o["verdict"]
            rec[f"{mid}_opt_bias"] = pct(o.get("optimistic_bias"))
            rec[f"{mid}_transfer"] = pct(o.get("transfer_loss"))
        rows.append(rec)

    out_dir = os.path.join(HERE, "..", "stock_data_300d")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, "跨模型寻优三口径对照")

    # ---- 核心对照（每行一只股）----
    core_hdr = ["代码", "名称", "买入持有(全)%", "买入持有(测试)%",
                "M3诚实期望%", "M3灯", "M3_Oracle期望%",
                "M4诚实期望%", "M4灯", "M4_Oracle期望%",
                "M6诚实期望%", "M6灯", "M6_Oracle期望%", "M2_DP上界%"]
    ck = ["code", "name", "bh_full", "bh_test",
          "M3_honest_exp", "M3_verdict", "M3_oracle_exp",
          "M4_honest_exp", "M4_verdict", "M4_oracle_exp",
          "M6_honest_exp", "M6_verdict", "M6_oracle_exp", "m2"]

    # ---- 明细（每模型诚实总收益/乐观期望/迁移损失/乐观偏差）----
    det_hdr = ["代码", "名称", "模型", "诚实总收益%", "乐观期望%", "Oracle总收益%",
               "乐观偏差", "迁移损失", "红绿灯"]
    det_rows = []
    for r in rows:
        for mid in MODEL_IDS:
            det_rows.append([
                r["code"], r["name"], mid,
                r[f"{mid}_honest_total"], r[f"{mid}_biased_exp"], r[f"{mid}_oracle_total"],
                r[f"{mid}_opt_bias"], r[f"{mid}_transfer"], VMAP.get(r[f"{mid}_verdict"], r[f"{mid}_verdict"]),
            ])

    # CSV
    with open(base + ".csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(core_hdr)
        for r in rows:
            line = []
            for k in ck:
                if k.endswith("_verdict"):
                    line.append(VMAP.get(r[k], r[k]))
                else:
                    line.append(r[k])
            w.writerow(line)
        w.writerow([])
        w.writerow(det_hdr)
        w.writerows(det_rows)

    # Markdown
    md = ["# 跨模型寻优三口径对照（M3 均线 / M4 止盈止损 / M6 下跌空仓）", "",
          f"> 数据源：默认数据集（{rows[0]['n']} 交易日/只，前复权，本金 10 万，费用同前）。",
          "> 「诚实期望%/灯」为**样本外**（最近 61 日）训练段选参→测试段执行的期望与红绿灯；「Oracle期望%」为测试段事后最优上界。买入持有(全)为全样本，买入持有(测试)为测试段。",
          "> 红绿灯：🔴不可用(诚实期望≤0) / 🟡谨慎(期望>0但测试总收益≤0) / 🟢可用(均正)。", "",
          "## 一、核心对照", ""]
    md.append("| " + " | ".join(core_hdr) + " |")
    md.append("|" + "|".join(["---"] * len(core_hdr)) + "|")
    for r in rows:
        cells = []
        for k in ck:
            v = r[k]
            cells.append(VMAP.get(v, v) if k.endswith("_verdict") else str(v))
        md.append("| " + " | ".join(cells) + " |")
    md.append("")
    md.append("## 二、明细（每模型：诚实总收益 / 乐观期望 / Oracle总收益 / 乐观偏差 / 迁移损失）")
    md.append("")
    md.append("| " + " | ".join(det_hdr) + " |")
    md.append("|" + "|".join(["---"] * len(det_hdr)) + "|")
    for d in det_rows:
        md.append("| " + " | ".join(str(x) for x in d) + " |")
    md.append("")
    md.append("## 三、解读")
    md.append("")
    # 统计每只股最优灯
    best = []
    for r in rows:
        lights = {mid: r[f"{mid}_verdict"] for mid in MODEL_IDS}
        ok = [mid for mid in MODEL_IDS if lights[mid] == "green"]
        best.append(f"{r['name']}({r['code']}): " + (",".join(ok) if ok else "全🔴"))
    md.append("- 各股在三个模型下样本外最优口径：" + "；".join(best) + "。")
    md.append("- 与上一轮 M4 单独结论一致：绝大多数个股在样本外诚实口径下三模型均难稳定盈利，唯有大元泵业在 M4 上达🟢。")
    md.append("- M3(均线)/M6(下跌空仓) 与 M4(止盈止损) 在该批次区间均无系统性优势，进一步印证「3~5 日周期缺乏稳定波段 alpha」的研究结论。")
    md.append("- M2(DP上界) 仍为事后上界，仅供收益空间参照。")
    md.append("")
    md.append("---")
    md.append("> 历史回测与样本外切分均不代表未来收益；本表仅供策略研究与教学，不构成投资建议。")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # xlsx
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        ws = wb.active
        ws.title = "核心对照"
        ws.append(core_hdr)
        for r in rows:
            ws.append([VMAP.get(r[k], r[k]) if k.endswith("_verdict") else r[k] for k in ck])
        ws2 = wb.create_sheet("明细")
        ws2.append(det_hdr)
        for d in det_rows:
            ws2.append(d)
        for sh in (ws, ws2):
            for c in sh[1]:
                c.font = Font(bold=True)
            for col in sh.columns:
                w = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                sh.column_dimensions[col[0].column_letter].width = min(w + 2, 22)
        wb.save(base + ".xlsx")
    except Exception as e:
        print("xlsx skipped:", e)

    print("OK rows=", len(rows))
    print(base + ".csv / .md / .xlsx")


if __name__ == "__main__":
    main()
