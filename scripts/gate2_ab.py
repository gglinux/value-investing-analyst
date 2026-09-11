#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate2_ab.py — 闸门二新旧口径 A/B 对照表（REQ-P0-04 验证工具）

## 为什么需要

REQ-P0-04 把闸门二从「三项全过」改为「四项参与判定 + 护城河反推门槛为诊断」。
一版验证只有神华一案有存档能对比，其余靠口头「假阳性 0% 不变」——那不是验证。
本脚本对每个含 scenarios 文件的回测案例，用**同一份输入**分别按旧口径与新口径
计算 gate2.pass，并列成表。它回答两个问题：

1. 该出手的案例（茅台/神华/苹果）在新口径下是否翻正？——正向错过是否下降
2. 该拒绝的案例（NFLX/ZM/EK/000898）在新口径下是否仍 fail？——假阳性是否新增

任何 FP 样本在新口径下翻正 = REQ-P0-04 回退条件触发。

用法：
    python3 scripts/gate2_ab.py                    # 扫描 backtest/*/data/scenarios*.json
    python3 scripts/gate2_ab.py --json out.json    # 同时输出机器可读结果
    python3 scripts/gate2_ab.py --assert           # 回归模式：FP 翻正即退出码 1
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reverse_dcf as rd  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 档位序数分界（与 run_backtest_assertions.POSITIVE_ORDINAL / alert_codes.VERDICT_ORDINAL 一致）
POSITIVE_ORDINAL = 3


def old_gate2_pass(g):
    """旧口径：①护城河反推门槛 + ②不收敛下限 + ③悲观 IRR 三项全过。"""
    checks = [g["consistency_expected_irr"]["pass"],
              g["no_convergence_floor"]["pass"],
              g["pessimistic_irr"]["pass"]]
    if any(c is None for c in checks):
        return None
    return all(checks)


def _load_case(case_dir):
    sfs = [f for f in glob.glob(os.path.join(case_dir, "data", "scenarios*.json"))
           if "audit" not in os.path.basename(f)]
    if not sfs:
        return None
    sd = json.load(open(sfs[0], encoding="utf-8"))
    ans_path = os.path.join(case_dir, "answer.json")
    ans = json.load(open(ans_path, encoding="utf-8")) if os.path.exists(ans_path) else {}
    return sd, ans


def _expected_direction(ans):
    """从 answer.json 推断该案例应该「过」还是「拒」。

    优先读 answer.json 的 expected_gate2（True/False）；缺失时按 expected_verdict_set
    的档位序数判定（与 run_backtest_assertions.POSITIVE_ORDINAL 一致：>=3 为正面档位）：
    含 >=3 → should_pass；全 <3 → should_fail。读不出来返回 None（不参与回归断言，只列表）。
    """
    g2 = ans.get("expected_gate2")
    if g2 is True:
        return "should_pass"
    if g2 is False:
        return "should_fail"
    vs = ans.get("expected_verdict_set") or []
    nums = [v for v in vs if isinstance(v, (int, float))]
    if not nums:
        return None
    if any(v >= POSITIVE_ORDINAL for v in nums):
        return "should_pass"
    return "should_fail"


def run(case_dirs):
    rows = []
    for d in case_dirs:
        loaded = _load_case(d)
        if not loaded:
            continue
        sd, ans = loaded
        name = os.path.basename(d.rstrip("/"))
        try:
            scen = [{"name": s["name"], "value_per_share": float(s["value_per_share"]),
                     "probability": float(s["probability"])} for s in sd["scenarios"]]
            res = rd.expected_return(
                float(sd["price"]), scen, int(sd.get("hold_years", 5)), 0.09,
                float(sd.get("dividend_yield", 0.0)), float(sd.get("discount_rate", 0.10)),
                moat=sd.get("moat"), iv_growth=sd.get("intrinsic_value_growth"))
        except SystemExit as e:
            rows.append({"case": name, "error": str(e)[:80]})
            continue
        g = res["gate2"]
        old = old_gate2_pass(g)
        if sd.get("moat") == "none":
            old = False
        new = g["pass"]
        rows.append({
            "case": name,
            "expected": _expected_direction(ans),
            "old_pass": old, "new_pass": new,
            "changed": old != new,
            "expected_irr": res["expected_annualized_irr"],
            "discount_rate": res["discount_rate"],
            "moat_hurdle": g["consistency_expected_irr"]["hurdle"],
            "floor": g["no_convergence_floor"]["value"],
            "pessimistic_irr": res["pessimistic_irr"],
            "loss_probability": res["loss_probability"],
            "fail_codes": [c for c in g["codes"]
                           if c.startswith("GATE2_") and c not in ("GATE2_PASS", "GATE2_FAIL")],
        })
    return rows


def classify(rows):
    """FP 新增 = should_fail 且新口径 pass；FN 修复 = should_pass 且旧 fail 新 pass。"""
    fp_new = [r for r in rows if r.get("expected") == "should_fail" and r.get("new_pass") is True]
    fn_fixed = [r for r in rows if r.get("expected") == "should_pass"
                and r.get("old_pass") is False and r.get("new_pass") is True]
    fn_remaining = [r for r in rows if r.get("expected") == "should_pass"
                    and r.get("new_pass") is not True]
    return fp_new, fn_fixed, fn_remaining


def _f(v, pct=True):
    if v is None:
        return "—"
    return f"{v:.1%}" if pct else str(v)


def main():
    ap = argparse.ArgumentParser(description="闸门二新旧口径 A/B 对照")
    ap.add_argument("--root", default=os.path.join(ROOT, "backtest"))
    ap.add_argument("--json", help="输出 JSON")
    ap.add_argument("--assert", dest="do_assert", action="store_true",
                    help="回归模式：任何 should_fail 案例在新口径下 pass → 退出码 1")
    args = ap.parse_args()

    dirs = sorted(d for d in glob.glob(os.path.join(args.root, "*/"))
                  if os.path.isdir(os.path.join(d, "data")))
    rows = run(dirs)

    print(f"{'案例':<26}{'应':<12}{'旧':>6}{'新':>6}  {'期望IRR':>7} {'r':>4} {'护城河门槛':>8} "
          f"{'下限':>6} {'悲观IRR':>7} {'亏损P':>5}  未过项")
    for r in rows:
        if r.get("error"):
            print(f"{r['case']:<26}引擎拒绝：{r['error']}")
            continue
        mark = " ←变" if r["changed"] else ""
        print(f"{r['case']:<26}{(r['expected'] or '?'):<12}{str(r['old_pass']):>6}{str(r['new_pass']):>6}"
              f"  {_f(r['expected_irr']):>7} {_f(r['discount_rate']):>4} {_f(r['moat_hurdle']):>8} "
              f"{_f(r['floor']):>6} {_f(r['pessimistic_irr']):>7} {_f(r['loss_probability']):>5}"
              f"  {r['fail_codes']}{mark}")

    fp_new, fn_fixed, fn_remaining = classify(rows)
    print()
    print(f"新增假阳性（should_fail 但新口径 pass）：{len(fp_new)} {[r['case'] for r in fp_new]}")
    print(f"修复假阴性（should_pass 旧 fail → 新 pass）：{len(fn_fixed)} {[r['case'] for r in fn_fixed]}")
    print(f"仍未修复的假阴性：{len(fn_remaining)} {[r['case'] for r in fn_remaining]}")
    if fp_new:
        print("⛔ REQ-P0-04 回退条件触发：新口径放行了应拒绝的案例")

    if args.json:
        json.dump({"rows": rows, "fp_new": [r["case"] for r in fp_new],
                   "fn_fixed": [r["case"] for r in fn_fixed],
                   "fn_remaining": [r["case"] for r in fn_remaining]},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    if args.do_assert and fp_new:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
