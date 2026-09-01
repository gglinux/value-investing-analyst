# -*- coding: utf-8 -*-
"""为勘误表中其余 6 个案例补齐 v2.9/v2.10 口径的落盘制品。

背景：v2.9 重跑只为 4 个"闸门翻转"案例落盘了 expected_return_v29_rerun.json，
其余 6 例的修正 IRR 仅存在于 cases/README.md 勘误表的表格里 —— 数字虽可复算，
但没有制品，复现得自己写脚本。本脚本用引擎逐一重算并落盘，使勘误表全表可追溯。

口径：沿用各案例存档 expected_return.json 的三情景每股价值、概率、现价与持有期，
仅把 IRR 公式换成 v2.9 修正式（并继承 v2.10 的非正价值/口径标记）。
现价不更新（与 4 案例不同）——这 6 例闸门结论未翻转，重跑目的是补齐可追溯性，
不是重做决策，故保持原口径以保留决策历史真实性。
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import reverse_dcf as rd

CASES = {
    "yili": "伊利股份 600887", "tsm": "台积电 TSM", "goog": "谷歌 GOOGL",
    "nvidia": "英伟达 NVDA", "popmart": "泡泡玛特 09992", "weibo": "微博 WB",
}
ROOT = os.path.join(os.path.dirname(__file__), "..", "cases")
HURDLE_BY_CASE = {"yili": 0.13, "tsm": 0.13, "goog": 0.13,
                  "nvidia": 0.13, "popmart": 0.13, "weibo": 0.13}

for slug, label in CASES.items():
    src = os.path.join(ROOT, slug, "data", "expected_return.json")
    if not os.path.exists(src):
        print(f"⚠️  {label}: 无存档 expected_return.json，跳过")
        continue
    a = json.load(open(src))
    scen = [{"name": s["name"], "value_per_share": s["value_per_share"],
             "probability": s["probability"]} for s in a["scenarios"]]
    price = a["price"]
    hy = a.get("hold_years", 5)
    r = a.get("discount_rate", 0.10)
    hurdle = HURDLE_BY_CASE.get(slug, 0.13)
    res = rd.expected_return(price, scen, hy, hurdle,
                             a.get("dividend_yield", 0.0) or 0.0, r)
    base = next((s["value_per_share"] for s in scen if s["name"] == "基准"), None)
    mos = (1 - price / base) if base else None
    res["v210_rerun_meta"] = {
        "label": label,
        "purpose": "补齐勘误表可追溯性（闸门结论未翻转，故不更新现价、不重做决策）",
        "engine_version": "v2.10",
        "formula": "IRR=(1+r)*(V0/P)^(1/H)-1",
        "price_basis": "沿用存档 expected_return.json 现价（未更新）",
        "current_price": price,
        "gate_check": {
            "gate1_mos": mos,
            "gate1_pass": (mos is not None and mos >= 0.25),
            "gate2_exp_irr": res["expected_annualized_irr"],
            "gate2_hurdle": hurdle,
            "gate2_pass": res["expected_annualized_irr"] >= hurdle,
            "pess_irr": res["pessimistic_irr"],
            "downside_pass": (res["pessimistic_irr"] >= 0
                              and res["loss_probability"] <= 0.30),
        },
    }
    dst = os.path.join(ROOT, slug, "data", "expected_return_v210_rerun.json")
    json.dump(res, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    g = res["v210_rerun_meta"]["gate_check"]
    print(f"{label:16s} IRR={g['gate2_exp_irr']:>7.2%} "
          f"MOS={(g['gate1_mos'] or 0):>6.2%} "
          f"悲观IRR={g['pess_irr']:>7.2%} "
          f"闸门一{'✓' if g['gate1_pass'] else '✗'} "
          f"闸门二{'✓' if g['gate2_pass'] else '✗'} "
          f"下行{'✓' if g['downside_pass'] else '✗'} → {os.path.basename(dst)}")
