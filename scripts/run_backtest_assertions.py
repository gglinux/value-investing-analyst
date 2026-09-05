#!/usr/bin/env python3
"""回放断言一键重跑 —— 把历史案例从「读完就存档的报告」变成「可反复运行的考试」。

## 为什么需要这个脚本

`backtest/PROMPT.md` 第九节承诺：「每次改引擎都应能一键重跑全部断言，立刻知道
有没有把修好的东西弄坏。」第一批 6 案例复核发现该承诺无实现——想确认
「改完折现率之后，柯达/康美/海控/福耀四个该拒绝的案例还是被拒绝吗」，
只能人工重读 6 份报告，每改一次代码重做一遍。于是回测成果无法复用。

本脚本做两件事：

1. **断言判定**（快，永远可用）：读 `answer.json` 的断言 vs `verdict.json` 的
   `codes`，做集合运算。排雷轨与档位轨**分开计分**，互不抵扣——福耀案正是
   「排雷轨命中 + 档位轨官方不约束」，第一批曾把它同时计入「命中」与
   「错误拒绝」两个互斥的桶。
2. **漂移检测**（`--rerun`）：对存在底稿的案例重跑引擎，把引擎实际产出的代号
   与 `verdict.json` 记录的 `engine_derived` 比对。改引擎后代号集合发生变化
   即为漂移——这才是「有没有把修好的东西弄坏」的直接答案。

## 用法

    python3 scripts/run_backtest_assertions.py                # 断言判定（全部案例）
    python3 scripts/run_backtest_assertions.py --batch 1      # 只跑第一批
    python3 scripts/run_backtest_assertions.py --case EK_2011-06-30
    python3 scripts/run_backtest_assertions.py --rerun        # 附带引擎漂移检测
    python3 scripts/run_backtest_assertions.py --baseline b.json --rerun   # 与基线比对

退出码：0 全部通过；1 有断言失败或漂移。
"""

import argparse
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alert_codes import (ASSERTIONS, ORDINAL_TO_VERDICT, assertion_satisfied,
                         matched_codes, unknown_assertions, unknown_codes)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKTEST = os.path.join(REPO, "backtest")

# 重跑引擎所需的逐案参数（护城河档位与内在价值增速取自各案 verdict/scenarios，
# 不是自由参数——改动它等于改动案例本身，须走案例修订而非脚本调参）。
RERUN_PARAMS = {
    "600519.SH_2015-08-31": {"moat": "wide", "iv_growth": "0.06"},
    "AAPL_2016-04-30": {"moat": "wide", "iv_growth": "0.07"},
    "EK_2011-06-30": {"moat": "none", "iv_growth": "-0.055"},
    "600660.SH_2018-12-31": {"moat": "narrow", "iv_growth": "0.025"},
    "601919.SH_2021-07-31": {"moat": "none", "iv_growth": "0.0"},
}


def _py():
    return sys.executable


def load_case(case_dir):
    name = os.path.basename(case_dir.rstrip("/"))
    vp, ap = os.path.join(case_dir, "verdict.json"), os.path.join(case_dir, "answer.json")
    mp = os.path.join(case_dir, "meta.json")
    if not os.path.exists(vp) or not os.path.exists(ap):
        return None
    v = json.load(open(vp, encoding="utf-8"))
    a = json.load(open(ap, encoding="utf-8"))
    meta = json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else {}
    return {"name": name, "dir": case_dir, "verdict": v, "answer": a, "meta": meta}


def rerun_engine(case):
    """重跑引擎，返回实际产出的代号集合（仅覆盖有脚本层的部分）。"""
    d, name = case["dir"], case["name"]
    data = os.path.join(d, "data")
    codes, notes = [], []
    tmp = os.path.join(data, ".rerun_tmp")
    os.makedirs(tmp, exist_ok=True)
    try:
        fin = sorted(glob.glob(os.path.join(data, "financials_*.json")))
        if fin:
            o = os.path.join(tmp, "m.json")
            r = subprocess.run([_py(), os.path.join(REPO, "scripts", "compute_metrics.py"),
                                fin[0], "-o", o], capture_output=True, text=True)
            if os.path.exists(o):
                codes += json.load(open(o, encoding="utf-8")).get("alert_codes", [])
            else:
                notes.append(f"compute_metrics 失败：{(r.stderr or '')[:120]}")
        scen = sorted(glob.glob(os.path.join(data, "scenarios_*.json")))
        if scen:
            met = sorted(glob.glob(os.path.join(data, "metrics_*.json")))
            o = os.path.join(tmp, "s.json")
            cmd = [_py(), os.path.join(REPO, "scripts", "check_scenarios.py"),
                   scen[0], "-o", o]
            if met:
                cmd += ["--metrics", met[0]]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if os.path.exists(o):
                j = json.load(open(o, encoding="utf-8"))
                codes += j.get("codes", [])
                if j.get("unmapped_messages"):
                    notes.append(f"未映射消息 {len(j['unmapped_messages'])} 条（断言会漏判）")
            else:
                notes.append(f"check_scenarios 失败：{(r.stderr or '')[:120]}")
            p = RERUN_PARAMS.get(name)
            if p:
                o2 = os.path.join(tmp, "e.json")
                r = subprocess.run([_py(), os.path.join(REPO, "scripts", "reverse_dcf.py"),
                                    "expected-return", "--scenarios-file", scen[0],
                                    "--moat", p["moat"], "--iv-growth", p["iv_growth"],
                                    "-o", o2], capture_output=True, text=True)
                if os.path.exists(o2):
                    codes += json.load(open(o2, encoding="utf-8"))["gate2"].get("codes", [])
                else:
                    notes.append(f"expected-return 失败：{(r.stderr or r.stdout or '')[:120]}")
    finally:
        for f in glob.glob(os.path.join(tmp, "*")):
            os.remove(f)
        if os.path.isdir(tmp):
            os.rmdir(tmp)
    return sorted(set(codes)), notes


def check_case(case, do_rerun=False):
    v, a = case["verdict"], case["answer"]
    fired = set(v.get("codes") or [])
    res = {"name": case["name"], "fired": sorted(fired), "failures": [], "notes": [],
           "known": [], "regressions": []}

    bad = unknown_codes(fired)
    if bad:
        res["failures"].append(f"verdict.codes 含未注册代号 {bad}")
    names = (a.get("must_trigger") or []) + (a.get("must_not_trigger") or []) + \
            [x for g in (a.get("must_trigger_any") or []) for x in g]
    bad = unknown_assertions(names)
    if bad:
        res["failures"].append(f"answer 含未注册断言 {bad}")
        return res

    # ---- 排雷/告警轨 ----
    hits, misses, false_fires = [], [], []
    for aid in a.get("must_trigger") or []:
        if assertion_satisfied(aid, fired):
            hits.append(f"{aid}←{'/'.join(matched_codes(aid, fired))}")
        else:
            misses.append(aid)
    for group in a.get("must_trigger_any") or []:
        ok = [g for g in group if assertion_satisfied(g, fired)]
        if ok:
            hits.append(f"任一({'|'.join(group)})←{'/'.join(matched_codes(ok[0], fired))}")
        else:
            misses.append(f"任一({'|'.join(group)})")
    for aid in a.get("must_not_trigger") or []:
        if assertion_satisfied(aid, fired):
            false_fires.append(f"{aid}←{'/'.join(matched_codes(aid, fired))}")
    res["assert_hits"], res["assert_misses"], res["assert_false_fires"] = hits, misses, false_fires
    if misses:
        res["failures"].append(f"must_trigger 未命中：{misses}")
    if false_fires:
        res["failures"].append(f"must_not_trigger 被误触发：{false_fires}")

    # ---- 档位轨（与告警轨独立计分，不得互相抵扣）----
    exp = a.get("expected_verdict_set")
    got = v.get("verdict_ordinal")
    if exp is None:
        res["verdict_track"] = "不计分（官方不约束档位）"
    elif got is None:
        res["verdict_track"] = "无法判定（verdict.json 缺 verdict_ordinal）"
        res["failures"].append("缺 verdict_ordinal")
    elif got in exp:
        res["verdict_track"] = f"命中（{ORDINAL_TO_VERDICT.get(got)} ∈ {[ORDINAL_TO_VERDICT.get(e) for e in exp]}）"
    else:
        res["verdict_track"] = f"未命中（实际 {ORDINAL_TO_VERDICT.get(got)}，期望 {[ORDINAL_TO_VERDICT.get(e) for e in exp]}）"
        res["failures"].append("档位轨未命中")

    # ---- 引擎漂移检测 ----
    if do_rerun:
        actual, notes = rerun_engine(case)
        recorded = sorted(set((v.get("codes_provenance") or {}).get("engine_derived") or []))
        res["notes"] += notes
        added = sorted(set(actual) - set(recorded))
        removed = sorted(set(recorded) - set(actual))
        res["drift"] = {"added": added, "removed": removed}
        if added or removed:
            res["failures"].append(f"引擎代号漂移：新增 {added} / 消失 {removed}")

    # ---- 已知失败 vs 新增回归 ----
    # 茅台档位轨未命中是第一批**记录在案**的真实假阴性（`backtest/REPORT.md` 元问题 3）。
    # 若把它一并算作红灯，本脚本就永远是红的、无法当回归门禁用。故 answer.json 可登记
    # `known_failures`，只有**未登记**的失败才算回归。这不是掩盖问题——已知失败在输出中
    # 单独列示，且一旦被修好（失败消失）会提示更新登记。
    known_kinds = set(a.get("known_failures") or [])
    for f in res["failures"]:
        kind = ("verdict_track" if "档位轨" in f or "verdict_ordinal" in f else
                "must_trigger" if "must_trigger 未命中" in f else
                "must_not_trigger" if "误触发" in f else
                "engine_drift" if "漂移" in f else "other")
        (res["known"] if kind in known_kinds else res["regressions"]).append(f)
    res["stale_known"] = sorted(
        k for k in known_kinds
        if not any(k == ("verdict_track" if "档位轨" in f or "verdict_ordinal" in f else
                         "must_trigger" if "must_trigger 未命中" in f else
                         "must_not_trigger" if "误触发" in f else
                         "engine_drift" if "漂移" in f else "other")
                   for f in res["failures"]))
    return res


def main():
    ap = argparse.ArgumentParser(description="回放断言一键重跑")
    ap.add_argument("--batch", type=int, help="只跑指定批次（读 meta.json 的 batch）")
    ap.add_argument("--case", help="只跑指定案例目录名")
    ap.add_argument("--rerun", action="store_true", help="附带重跑引擎做漂移检测")
    ap.add_argument("--baseline", help="基线 JSON 路径：存在则比对，不存在则写入")
    ap.add_argument("-o", "--output", help="结果 JSON 输出路径")
    args = ap.parse_args()

    cases = []
    for d in sorted(glob.glob(os.path.join(BACKTEST, "*") + os.sep)):
        c = load_case(d)
        if not c:
            continue
        if args.case and c["name"] != args.case:
            continue
        if args.batch is not None and c["meta"].get("batch") != args.batch:
            continue
        cases.append(c)

    if not cases:
        print("没有可跑的案例（需同时存在 verdict.json 与 answer.json）")
        sys.exit(1)

    results = [check_case(c, do_rerun=args.rerun) for c in cases]

    w = max(len(r["name"]) for r in results) + 2
    print(f"{'案例':<{w}} {'档位轨':<34} {'告警轨':<26} 结果")
    print("-" * (w + 74))
    for r in results:
        at = f"命中{len(r.get('assert_hits', []))} 漏{len(r.get('assert_misses', []))} 误触发{len(r.get('assert_false_fires', []))}"
        if r["regressions"]:
            ok = "回归失败"
        elif r["known"]:
            ok = "已知失败"
        else:
            ok = "通过"
        print(f"{r['name']:<{w}} {r.get('verdict_track', '-'):<34} {at:<26} {ok}")
    print("-" * (w + 74))
    clean = sum(1 for r in results if not r["failures"])
    known_only = sum(1 for r in results if r["known"] and not r["regressions"])
    regressed = [r for r in results if r["regressions"]]
    print(f"{clean}/{len(results)} 全绿" +
          (f"，{known_only} 已知失败（登记在 answer.json known_failures）" if known_only else "") +
          (f"，{len(regressed)} 回归失败" if regressed else ""))

    for r in results:
        if r["failures"] or r["notes"] or r.get("stale_known"):
            print(f"\n[{r['name']}]")
            for f in r["regressions"]:
                print(f"   回归失败：{f}")
            for f in r["known"]:
                print(f"   已知失败：{f}")
            for n in r["notes"]:
                print(f"   注意：{n}")
            for k in r.get("stale_known", []):
                print(f"   登记过期：known_failures 含 `{k}` 但该失败已不存在——"
                      f"若确已修好，请从 answer.json 移除该登记")
            if r.get("assert_hits"):
                print(f"  命中明细：{'; '.join(r['assert_hits'])}")

    payload = {"results": results, "clean": clean, "known_only": known_only,
               "regressed": [r["name"] for r in regressed], "total": len(results)}
    if args.output:
        json.dump(payload, open(args.output, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"\n已写入 {args.output}")

    if args.baseline:
        snap = {r["name"]: r["fired"] for r in results}
        if os.path.exists(args.baseline):
            base = json.load(open(args.baseline, encoding="utf-8"))
            diffs = []
            for k in sorted(set(base) | set(snap)):
                b, s = set(base.get(k, [])), set(snap.get(k, []))
                if b != s:
                    diffs.append(f"  {k}: 新增 {sorted(s - b)} / 消失 {sorted(b - s)}")
            if diffs:
                print("\n与基线不一致：")
                print("\n".join(diffs))
                sys.exit(1)
            print(f"\n与基线一致（{len(snap)} 案例代号集合无变化）")
        else:
            json.dump(snap, open(args.baseline, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            print(f"\n已写入基线 {args.baseline}")

    # 回归失败才是红灯；已知失败不阻塞（但会单独列示）
    sys.exit(1 if regressed else 0)


if __name__ == "__main__":
    main()
