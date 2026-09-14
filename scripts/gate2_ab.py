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

## 样本角色与"应过/应拒"的唯一来源（2026-09-14 修订，B3-14 平安假红灯）

假阳性的正式定义是**档位级**（PROMPT 第九节 / runner `POSITIVE_ORDINAL`）：
「官方期望最高档位 <3，系统却给出 ≥3」。因此本脚本的方向标签只能从
`answer.json.expected_verdict_set` 推——与 run_backtest_assertions 同一口径：

    negative  官方最高档位 <3  → should_fail（假阳性轨分母）
    positive  官方最低档位 ≥3  → should_pass（假阴性分母）
    mixed     期望集跨越 3     → 不入 FP/FN 分母，只列表
    None      官方不约束档位   → 回退读 expected_gate2（True/False），仍读不出则只列表

`expected_gate1/expected_gate2` 是执行者的派生注记而非官方原文——平安 B3-14 官方集
{3,2} 含正面档位 3（要求双闸门全过），执行者却按"与系统输出形态相容"登记了
`expected_gate2=false`，旧版本脚本让该字段优先于档位集，把一个 mixed 样本判成
should_fail，制造了一盏假红灯。派生注记与档位集冲突时本脚本按档位集判定并
打印冲突警告；runner 的 answer lint 同时把这种自相矛盾判为失败（见 P0-04 进展）。

## 两层假阳性检验（对 negative 样本）

- 闸门级代理（严）：新口径 gate2.pass=True。REQ-P0-04 改的是闸门二，这是对改动最
  敏感的检验，也是 `--assert` 的主判据。
- 档位级复核（正式定义）：同一份 scenarios 模拟闸门一（基准价值 vs 现价的 MoS 是否
  达护城河门槛），闸门一、二双过 ⇒ 隐含正面档位。任一层命中都算 FP 新增。

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


def _pick_scenarios_file(case_dir):
    """优先取精确的 data/scenarios.json；否则取字母序首个非 audit 的 scenarios*.json。

    三版审查 ④：原实现依赖 glob 字母序，同目录多份 scenarios 文件时有误取风险。
    """
    exact = os.path.join(case_dir, "data", "scenarios.json")
    if os.path.exists(exact):
        return exact
    sfs = sorted(f for f in glob.glob(os.path.join(case_dir, "data", "scenarios*.json"))
                 if "audit" not in os.path.basename(f))
    return sfs[0] if sfs else None


def _load_case(case_dir):
    sf = _pick_scenarios_file(case_dir)
    if not sf:
        return None
    sd = json.load(open(sf, encoding="utf-8"))
    ans_path = os.path.join(case_dir, "answer.json")
    ans = json.load(open(ans_path, encoding="utf-8")) if os.path.exists(ans_path) else {}
    return sd, ans


def sample_role(ans):
    """与 run_backtest_assertions 同口径的样本角色：negative / positive / mixed / None。"""
    vs = ans.get("expected_verdict_set")
    if not vs:
        return None
    nums = [v for v in vs if isinstance(v, (int, float))]
    if not nums:
        return None
    if max(nums) < POSITIVE_ORDINAL:
        return "negative"
    if min(nums) >= POSITIVE_ORDINAL:
        return "positive"
    return "mixed"


def gate_label_conflict(ans):
    """派生闸门注记与官方档位集是否自相矛盾。

    - 档位集含 ≥3（positive/mixed）：正面档位要求双闸门全过，expected_gate1/2 任一为 False 即矛盾
    - 档位集全 <3（negative）：expected_gate1 与 expected_gate2 同时为 True 即矛盾（双过 ⇒ 正面档位）
    返回冲突描述字符串或 None。
    """
    role = sample_role(ans)
    g1, g2 = ans.get("expected_gate1"), ans.get("expected_gate2")
    if role in ("positive", "mixed"):
        bad = [n for n, g in (("expected_gate1", g1), ("expected_gate2", g2)) if g is False]
        if bad:
            return (f"expected_verdict_set={ans.get('expected_verdict_set')} 含正面档位（≥{POSITIVE_ORDINAL}，"
                    f"要求双闸门全过），但 {'/'.join(bad)}=false")
    elif role == "negative" and g1 is True and g2 is True:
        return (f"expected_verdict_set={ans.get('expected_verdict_set')} 全为非正面档位，"
                f"但 expected_gate1/expected_gate2 同时为 true（双过 ⇒ 正面档位）")
    return None


def _expected_direction(ans):
    """从 answer.json 推断该案例应该「过」还是「拒」。

    唯一真值源是 expected_verdict_set（假阳性的正式定义是档位级）：
      negative → should_fail；positive → should_pass；mixed → "mixed"（不参与回归断言）。
    仅当官方不约束档位（集合缺失）时回退读 expected_gate2（True/False）。
    读不出来返回 None（只列表）。
    """
    role = sample_role(ans)
    if role == "negative":
        return "should_fail"
    if role == "positive":
        return "should_pass"
    if role == "mixed":
        return "mixed"
    g2 = ans.get("expected_gate2")
    if g2 is True:
        return "should_pass"
    if g2 is False:
        return "should_fail"
    return None


def simulate_gate1(sd):
    """用同一份 scenarios 模拟闸门一：基准每股价值 vs 现价的 MoS 是否达护城河门槛。

    返回 (gate1_pass, mos_actual, mos_requirement)；缺基准情景或无护城河返回 (False/None, ...)。
    moat_score 存在时用 REQ-P1-03 平滑门槛，否则用评级词 legacy 常数。
    """
    base_v = next((float(s["value_per_share"]) for s in sd.get("scenarios", [])
                   if s.get("name") in ("基准", "base")), None)
    price = float(sd["price"])
    mos_actual = (1.0 - price / base_v) if base_v else None
    moat = sd.get("moat")
    score = sd.get("moat_score")
    try:
        req = (rd.mos_requirement_from_score(score) if score is not None
               else rd.MOAT_MOS_REQUIREMENT.get(moat))
    except ValueError:
        req = rd.MOAT_MOS_REQUIREMENT.get(moat)
    if moat == "none" or req is None:
        return False, mos_actual, req
    if mos_actual is None:
        return None, mos_actual, req
    return mos_actual >= req, mos_actual, req


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
        g1_pass, mos_actual, mos_req = simulate_gate1(sd)
        implied_positive = (g1_pass is True and new is True)
        rows.append({
            "case": name,
            "role": sample_role(ans),
            "expected": _expected_direction(ans),
            "label_conflict": gate_label_conflict(ans),
            "old_pass": old, "new_pass": new,
            "changed": old != new,
            "gate1_pass": g1_pass,
            "gate1_mos": mos_actual,
            "gate1_requirement": mos_req,
            "implied_positive_tier": implied_positive,
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
    """FP 新增 = should_fail 且（新口径 gate2 pass 或 双闸门双过隐含正面档位）；
    FN 修复 = should_pass 且旧 fail 新 pass；mixed 翻正只作信息披露、不入 FP。"""
    fp_new = [r for r in rows if r.get("expected") == "should_fail"
              and (r.get("new_pass") is True or r.get("implied_positive_tier"))]
    fn_fixed = [r for r in rows if r.get("expected") == "should_pass"
                and r.get("old_pass") is False and r.get("new_pass") is True]
    fn_remaining = [r for r in rows if r.get("expected") == "should_pass"
                    and r.get("new_pass") is not True]
    mixed_flipped = [r for r in rows if r.get("expected") == "mixed"
                     and r.get("old_pass") is False and r.get("new_pass") is True]
    return fp_new, fn_fixed, fn_remaining, mixed_flipped


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

    print(f"{'案例':<26}{'应':<12}{'旧':>6}{'新':>6}{'闸1':>6}  {'期望IRR':>7} {'r':>4} {'护城河门槛':>8} "
          f"{'下限':>6} {'悲观IRR':>7} {'亏损P':>5}  未过项")
    for r in rows:
        if r.get("error"):
            print(f"{r['case']:<26}引擎拒绝：{r['error']}")
            continue
        mark = " ←变" if r["changed"] else ""
        if r.get("implied_positive_tier") and r.get("expected") == "should_fail":
            mark += " ⛔双闸门双过"
        print(f"{r['case']:<26}{(r['expected'] or '?'):<12}{str(r['old_pass']):>6}{str(r['new_pass']):>6}"
              f"{str(r['gate1_pass']):>6}"
              f"  {_f(r['expected_irr']):>7} {_f(r['discount_rate']):>4} {_f(r['moat_hurdle']):>8} "
              f"{_f(r['floor']):>6} {_f(r['pessimistic_irr']):>7} {_f(r['loss_probability']):>5}"
              f"  {r['fail_codes']}{mark}")

    conflicts = [r for r in rows if r.get("label_conflict")]
    if conflicts:
        print()
        print("⚠ answer.json 派生闸门注记与官方档位集自相矛盾（方向已按档位集判定；"
              "runner lint 同时判失败，须修正注记）：")
        for r in conflicts:
            print(f"   - {r['case']}：{r['label_conflict']}")

    fp_new, fn_fixed, fn_remaining, mixed_flipped = classify(rows)
    print()
    print(f"新增假阳性（should_fail 但新口径 pass / 双闸门双过）：{len(fp_new)} {[r['case'] for r in fp_new]}")
    print(f"修复假阴性（should_pass 旧 fail → 新 pass）：{len(fn_fixed)} {[r['case'] for r in fn_fixed]}")
    print(f"仍未修复的假阴性：{len(fn_remaining)} {[r['case'] for r in fn_remaining]}")
    if mixed_flipped:
        print(f"mixed 样本闸门二翻正（官方集跨越 {POSITIVE_ORDINAL}，不入 FP/FN 分母，仅披露）："
              f"{len(mixed_flipped)} {[r['case'] for r in mixed_flipped]}")
    if fp_new:
        print("⛔ REQ-P0-04 回退条件触发：新口径放行了应拒绝的案例")

    if args.json:
        json.dump({"rows": rows, "fp_new": [r["case"] for r in fp_new],
                   "fn_fixed": [r["case"] for r in fn_fixed],
                   "fn_remaining": [r["case"] for r in fn_remaining],
                   "mixed_flipped": [r["case"] for r in mixed_flipped],
                   "label_conflicts": {r["case"]: r["label_conflict"] for r in conflicts}},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    if args.do_assert and fp_new:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
