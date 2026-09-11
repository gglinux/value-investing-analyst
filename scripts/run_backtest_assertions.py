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
3. **FP/FN 双向统计**（REQ-P0-01，每次运行自动输出）：按 answer.json 的
   `expected_verdict_set` 把案例分为负向（期望 <3）/ 正向（期望 >=3）样本，
   分别算假阳性率与假阴性率，并对 `fp_control=true` 的假阳性对照案例统计
   红灯命中率。负向样本为 0 时假阳性率输出「未被检验」而非 0。基线文件附带
   `_fp_fn` 段，`--baseline` 比对时假阳性集合相对基线新增即红灯。

## 用法

    python3 scripts/run_backtest_assertions.py                # 断言判定（全部案例）
    python3 scripts/run_backtest_assertions.py --batch 1      # 只跑第一批
    python3 scripts/run_backtest_assertions.py --case EK_2011-06-30
    python3 scripts/run_backtest_assertions.py --rerun        # 附带引擎漂移检测
    python3 scripts/run_backtest_assertions.py --baseline b.json --rerun   # 与基线比对
    python3 scripts/run_backtest_assertions.py --lint-verdict <case>/verdict.json
        # Step 3 落盘体检：codes 注册表/provenance/必填字段/档位自洽（无需 answer.json）

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

# 档位序数分界（与 alert_codes.VERDICT_ORDINAL 一致）
POSITIVE_ORDINAL = 3   # >=3 为正面档位（小仓位试探 / 核心买入）
ABSTAIN_ORDINAL = 2    # =2 观察等价格 = 弃权档

# REQ-P0-01 假阳性专项前置：FP / FN 双向统计的目标区间（PROMPT 第八节元问题 4）。
# 刻意不对称——容忍错过（FN）远高于容忍错买（FP），是「不错买优先于不错过」
# 原则的可度量形态。区间可调，但调整须在 backtest/PROMPT.md 同步并登记动因。
FP_RATE_TARGET = 0.10
FN_RATE_TARGET = 0.40

# REQ-P0-05 / P0-08：隔离机器检查与规则版本快照从第几批起强制。
# 执行顺序 1→2→4→3→5，故"下一执行批次"是第四批；第三批因排在第四批之后同样受约束。
RULES_SNAPSHOT_MIN_BATCH = 3

# 重跑引擎所需的逐案参数（三类键独立可选，改动它等于改动案例本身，须走案例
# 修订而非脚本调参）：
#   moat + iv_growth —— reverse_dcf expected-return 传参，**两键齐备才跑反推**。
#     值与各案 scenarios 文件一致（scenarios 是单一事实源，CLI 只是回显）。
#     软银（9984.T）故意不设：其 GATE 判定经 ADJ2 人工裁决，反推会产出
#     engine_derived 之外的 GATE2_UNRATED——假漂移，不接。
#   market_cap_million —— compute_metrics 市值传参（百万，见 --market-cap-million）。
#     取值以「复现该案 verdict.codes_provenance.engine_derived」为准：
#     engine_derived 含市值依赖码（M_UNIT_SUSPECT / M_OWNER_YIELD_NOT_CASH_BACKED）
#     的案例传原运行值；不含的不传——今日引擎的市值依赖码对冻结 verdict 是
#     「新增漂移」而非复现（福耀/海控实证：传参会新增 engine_derived 之外的码）。
#     batch-3 起新案例可从冻结 metrics 的 provenance 块自读本值，不再手工维护。
RERUN_PARAMS = {
    "600519.SH_2015-08-31": {"moat": "wide", "iv_growth": "0.06",
                             "market_cap_million": 245433},
    "AAPL_2016-04-30": {"moat": "wide", "iv_growth": "0.07",
                        "market_cap_million": 519403},
    "EK_2011-06-30": {"moat": "none", "iv_growth": "-0.055",
                      "market_cap_million": 962.7},
    "600660.SH_2018-12-31": {"moat": "narrow", "iv_growth": "0.025"},
    "601919.SH_2021-07-31": {"moat": "none", "iv_growth": "0.0"},
    "601088.SH_2015-12-31": {"moat": "narrow", "iv_growth": "0.02"},
    "000898.SZ_2015-12-31": {"moat": "none", "iv_growth": "0.0",
                             "market_cap_million": 32187},
    "000895.SZ_2019-06-30": {"moat": "narrow", "iv_growth": "0.02",
                             "market_cap_million": 82126},
    "NFLX_2016-12-31": {"moat": "narrow", "iv_growth": "0.0",
                        "market_cap_million": 53128},
    "ZM_2021-10-31": {"moat": "narrow", "iv_growth": "0.0",
                      "market_cap_million": 83948},
    "9984.T_2019-06-30": {"market_cap_million": 10886263},
    # B3-16/B3-17 补登（批 3 闭环复盘发现：--rerun 模式下两案 GATE 族码
    # 假性消失——反推未配置即被跳过）。值抄自各案 scenarios.json（单一
    # 事实源）；两案均无 market_cap_million 键（原跑未传市值，metrics
    # provenance.market_cap_million=null 为证）。
    "META_2022-11-30": {"moat": "wide", "iv_growth": "0.06"},
    "3333.HK_2020-06-30": {"moat": "none", "iv_growth": "0.0"},
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
        # 多 financials 文件案例（metrics 映射视图方案，B3-17 恒大先例）：
        # 视图才是 compute_metrics 的合法输入（同字段双用途解耦——底稿勾稽
        # 要求 total_equity=总权益，metrics 语义要求归母；capex=0 显式语义
        # 亦只存在于视图）。glob 排序会选中底稿 → M 族码假性消失。
        # 冻结 metrics 的 provenance.argv 记录了原跑输入，自读钉定（单文件
        # 案例零行为变化；provenance 缺失/解析失败回退 glob 排序）。
        if len(fin) > 1:
            _fm = sorted(glob.glob(os.path.join(data, "metrics_*.json")))
            if _fm:
                try:
                    _argv = (json.load(open(_fm[0], encoding="utf-8"))
                             .get("provenance") or {}).get("argv") or []
                    _names = {os.path.basename(a) for a in _argv
                              if os.path.basename(a).startswith("financials_")}
                    _pinned = [f for f in fin if os.path.basename(f) in _names]
                    if _pinned:
                        fin = _pinned
                except Exception:
                    pass
        if fin:
            # 金融类路由（第三批招行/平安）：通用管道对 bank/保险硬拒绝，
            # 按 company_type 分发到专属管道。银行/保险管道不产注册表告警码
            # （输出字段无 alert_codes），对 codes 的贡献为空属预期——
            # 金融案的 engine_derived 主要来自 check_scenarios 侧。
            try:
                _fin_head = json.load(open(fin[0], encoding="utf-8"))
            except Exception:
                _fin_head = {}
            _ctype = str(_fin_head.get("company_type", "")).strip().lower()
            if _ctype in ("bank", "银行"):
                _engine = "compute_metrics_bank.py"
            elif _ctype in ("保险", "保险集团", "财险", "寿险", "insurance"):
                _engine = "compute_metrics_insurance.py"
            else:
                _engine = "compute_metrics.py"
            o = os.path.join(tmp, "m.json")
            cmd = [_py(), os.path.join(REPO, "scripts", _engine),
                   fin[0]]
            # P2-8：市值传参以复现 engine_derived 为准（见 RERUN_PARAMS 注释）
            _p = RERUN_PARAMS.get(name) or {}
            if _p.get("market_cap_million") is not None:
                cmd += ["--market-cap-million", str(_p["market_cap_million"])]
            cmd += ["-o", o]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if os.path.exists(o):
                codes += json.load(open(o, encoding="utf-8")).get("alert_codes", [])
            else:
                notes.append(f"compute_metrics 失败：{(r.stderr or '')[:120]}")
        # 两类命名并存于库内：scenarios_*.json（规范）与 scenarios.json
        # （鞍钢/神华/NFLX 等第二批早期案例）——后者曾使 check_scenarios/
        # reverse_dcf 在 rerun 路径被静默跳过，GATE 码全部误判为不可复现
        scen = sorted(glob.glob(os.path.join(data, "scenarios_*.json"))
                      + glob.glob(os.path.join(data, "scenarios.json")))
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
            # moat+iv_growth 两键齐备才跑反推：软银类经人工裁决的案例只有
            # market_cap_million 键，反推会产生 engine_derived 之外的假漂移
            if p and "moat" in p and "iv_growth" in p:
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


def _classify_failures(res, answer, do_rerun=False):
    """已知失败 vs 新增回归 归桶。

    提前 return 的路径（如 answer 含未注册断言）也必须走这步——否则失败
    既不算回归也不算已知，结果列显示「通过」、退出码 0，门禁被静默绕过
    （Netflix 案 B2-09 实证：该缺陷让两处未注册问题全部漏报）。
    """
    known_kinds = set(answer.get("known_failures") or [])

    def _kind(f):
        return ("verdict_track" if "档位轨" in f or "verdict_ordinal" in f else
                "must_trigger" if "must_trigger 未命中" in f else
                "must_not_trigger" if "误触发" in f else
                "engine_drift" if "漂移" in f else "other")

    for f in res["failures"]:
        (res["known"] if _kind(f) in known_kinds else res["regressions"]).append(f)
    res["stale_known"] = sorted(
        k for k in known_kinds
        if not any(k == _kind(f) for f in res["failures"])
        # engine_drift 只在 --rerun 模式产生失败；非 rerun 模式下登记着它
        # 不算过期，否则会诱导误删登记、下次 rerun 变红。
        and (k != "engine_drift" or do_rerun))


REQUIRED_DELIVERABLES = ["meta.json", "verdict.json", "answer.json", "diff.md"]


def _git_first_commit_time(path):
    """文件首次进入 git 的提交时间戳（int）；未被追踪 / 不在仓库返回 None。"""
    r = subprocess.run(["git", "log", "--diff-filter=A", "--format=%ct", "--", path],
                       capture_output=True, text=True, cwd=REPO)
    if r.returncode != 0:
        return None
    lines = [l for l in r.stdout.split() if l.strip()]
    return int(lines[-1]) if lines else None  # 输出按新→旧排列，末行 = 最早提交


_HEAD_CACHE = {}


def _git_head():
    if "head" not in _HEAD_CACHE:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO)
        _HEAD_CACHE["head"] = r.stdout.strip() if r.returncode == 0 else ""
    return _HEAD_CACHE["head"]


def run_as_of(commit, passthrough_args):
    """REQ-P0-08 `--as-of <hash>`：按 verdict 记录的 skill 版本重跑本脚本。

    实现不用 stash/checkout（会扰动工作区），用 `git worktree` 把目标版本检出到
    临时目录，在该目录下以**当前工作区的 backtest/ 案例数据**跑 rerun：
      1. worktree add /tmp/via-<hash> <hash>
      2. 把当前 backtest/ 目录 symlink 进 worktree（案例数据不随版本变，规则随版本变）
      3. 在 worktree 下执行 run_backtest_assertions.py <passthrough_args> --rerun
      4. worktree remove
    验收语义：「任一历史案例可按其记录的版本重跑并复现原档位」——档位由 verdict.json
    冻结，这里复现的是**引擎代号集合**（engine_derived），rerun 漂移为空即复现成功。
    """
    import shutil
    import tempfile
    r = subprocess.run(["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
                       capture_output=True, text=True, cwd=REPO)
    if r.returncode != 0:
        print(f"❌ --as-of：{commit} 不是有效 commit")
        return 2
    full = r.stdout.strip()
    wt = os.path.join(tempfile.gettempdir(), f"via-asof-{full[:12]}")
    if os.path.exists(wt):
        subprocess.run(["git", "worktree", "remove", "--force", wt], capture_output=True, cwd=REPO)
        shutil.rmtree(wt, ignore_errors=True)
    add = subprocess.run(["git", "worktree", "add", "--detach", wt, full],
                         capture_output=True, text=True, cwd=REPO)
    if add.returncode != 0:
        print(f"❌ --as-of：worktree 创建失败：{add.stderr.strip()[:200]}")
        return 2
    try:
        # 案例数据用当前工作区的（版本化的是规则，不是案例）
        wt_bt = os.path.join(wt, "backtest")
        if os.path.isdir(wt_bt):
            shutil.rmtree(wt_bt)
        os.symlink(BACKTEST, wt_bt)
        runner = os.path.join(wt, "scripts", "run_backtest_assertions.py")
        if not os.path.exists(runner):
            print(f"❌ --as-of：{full[:8]} 版本没有 run_backtest_assertions.py，无法按该版本重跑")
            return 2
        print(f"[as-of] 按 skill 版本 {full[:8]} 重跑（worktree {wt}）\n")
        cmd = [_py(), runner] + passthrough_args
        if "--rerun" not in cmd:
            cmd.append("--rerun")
        p = subprocess.run(cmd, cwd=wt)
        return p.returncode
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", wt], capture_output=True, cwd=REPO)
        shutil.rmtree(wt, ignore_errors=True)


def check_isolation_evidence(case, res):
    """PROMPT 第七节 B 档隔离协议的机器可验痕迹（第三批起硬校验）。

    第二批的教训：口头宣称「subagent 隔离」与实际执行脱节（6 案仅 1 案真隔离），
    事后只能靠证据强度降级补救。第三批起隔离证据本身进门禁——「宣称隔离」与
    「机器可验的隔离」从此是两回事：
      ① answer_source.md 存在——答案必须经 prepare_case.py --reveal 揭示落地
         （G1/G2/G3 闸门在揭示时已机器校验 verdict 提交时序），手写 answer.json
         无 reveal 痕迹，按未隔离处理；
      ② verdict.json 首次提交早于 answer.json 首次提交——「先落盘结论再看答案」
         的 git 时序证据（第七节共同兜底条款）。
    本项不接受 known_failures 登记（与假阳性轨同属不对称设计）——隔离违规的
    案例结论不得采信，登记豁免等于允许污染计分。
    批次 <3 的历史案例不适用（已按污染降级处理，见 BATCH2_FINDINGS 战绩表）。
    """
    try:
        batch = int(case["meta"].get("batch") or 0)
    except (TypeError, ValueError):
        batch = 0
    if batch < 3:
        return
    d = case["dir"]
    if not os.path.exists(os.path.join(d, "answer_source.md")):
        res["failures"].append("隔离证据缺失：answer_source.md 不存在——第三批起答案必须"
                               "经 prepare_case.py --reveal 揭示（B 档协议），手写 answer.json"
                               "视为未隔离")
    tv = _git_first_commit_time(os.path.join(d, "verdict.json"))
    ta = _git_first_commit_time(os.path.join(d, "answer.json"))
    if tv is None or ta is None:
        res["failures"].append("隔离证据缺失：verdict/answer 无 git 首次提交记录，"
                               "时序证据不可验")
    elif tv > ta:
        res["failures"].append("隔离证据违规：answer.json 首次提交早于 verdict.json——"
                               "「先落盘结论再看答案」时序被破坏，该案结论不得采信")


def check_deliverables(case, res):
    """PROMPT 第十节交付物完整性。双汇 B2-12 缺 report.html 跑完全流程未被
    发现——此类缺口必须由机器拦截而非事后审查。"""
    d = case["dir"]
    missing = [f for f in REQUIRED_DELIVERABLES
               if not os.path.exists(os.path.join(d, f))]
    if not glob.glob(os.path.join(d, "*.html")):
        missing.append("report.html（Step 2 标准 HTML 报告）")
    if missing:
        res["failures"].append(f"交付物缺失：{missing}")


def check_case(case, do_rerun=False):
    v, a = case["verdict"], case["answer"]
    fired = set(v.get("codes") or [])
    res = {"name": case["name"], "fired": sorted(fired), "failures": [], "notes": [],
           "known": [], "regressions": []}

    # ---- REQ-P0-05：contaminated 案例不计分 ----
    # 隔离失效的案例结论是"执行者记忆力"而非"系统能力"的度量，三轨全部不计。
    # 仍做交付物检查（污染案例也要有完整档案），但不进 FP/FN 分母、不产生回归红灯。
    if v.get("contaminated"):
        res["contaminated"] = True
        res["verdict_track"] = "不计分（contaminated：隔离失效）"
        res["false_positive_track"] = "不计分（contaminated）"
        res["false_positive"] = res["false_negative"] = False
        res["sample_role"] = "unscored"
        res["assert_hits"] = res["assert_misses"] = res["assert_false_fires"] = []
        res["notes"].append("verdict.contaminated=true：隔离失效，三轨不计分，从战绩表排除（REQ-P0-05）")
        check_deliverables(case, res)
        _classify_failures(res, a, do_rerun)
        return res

    # ---- REQ-P0-08：规则版本漂移披露 ----
    # verdict 记录的 skill_commit 与当前 HEAD 不同 → 今日引擎重跑 ≠ 当时结论，
    # rerun 漂移须与规则漂移一起读（是规则变了还是引擎回归了）。
    snap = v.get("rules_snapshot") or {}
    res["skill_commit"] = (snap.get("skill_commit") or "")[:12] or None
    if not snap:
        res["rules_version"] = "unknown（verdict 无 rules_snapshot，历史批次）"
    else:
        head = _git_head()
        if snap.get("skill_commit") == head:
            res["rules_version"] = f"current（{head[:8]}）"
        else:
            res["rules_version"] = f"drifted（verdict {snap.get('skill_commit', '')[:8]} → HEAD {head[:8]}）"
            if do_rerun:
                res["notes"].append(f"规则版本漂移：verdict 落盘于 {snap.get('skill_commit', '')[:8]}，"
                                    f"当前 HEAD {head[:8]}——rerun 漂移可能来自规则变更而非引擎回归，"
                                    f"用 `--as-of {snap.get('skill_commit', '')[:8]}` 复现原档位")

    check_deliverables(case, res)
    check_isolation_evidence(case, res)

    bad = unknown_codes(fired)
    if bad:
        res["failures"].append(f"verdict.codes 含未注册代号 {bad}")
    names = (a.get("must_trigger") or []) + (a.get("must_not_trigger") or []) + \
            [x for g in (a.get("must_trigger_any") or []) for x in g]
    bad = unknown_assertions(names)
    if bad:
        res["failures"].append(f"answer 含未注册断言 {bad}")
        res["verdict_track"] = "未执行（answer 含未注册断言）"
        _classify_failures(res, a, do_rerun)
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

    # ---- 假阳性轨（第三轨：独立计分，不接受 known_failures 豁免）----
    # 只统计「官方期望拒绝/观察，系统却给出正面档位（序数 >=3）」。
    # 单独成轨的理由见 PROMPT.md 第九节：错买与错过的代价不对称，合并进总分会让
    # 「救回 2 个错过 + 新增 2 个错买」显示为战绩不变，而系统实际已显著变危险。
    # 与另两轨的**不对称设计**：假阳性一旦出现即红灯，刻意不允许登记豁免。
    #
    # REQ-P0-01：同时给每个案例贴样本角色标签，供批次级 FP/FN 率统计——
    #   negative  官方期望最高档位 <3（该拒/该观察）→ 假阳性轨的分母
    #   positive  官方期望最低档位 >=3（该买）        → 假阴性率的分母
    #   mixed     期望集跨越 3（如 [2,3]）             → 两侧都不计入分母
    #   unscored  官方不约束档位或缺 verdict_ordinal
    if exp is None or got is None:
        res["false_positive_track"] = "不计分（无档位期望或缺 verdict_ordinal）"
        res["false_positive"] = False
        res["false_negative"] = False
        res["sample_role"] = "unscored"
    else:
        if max(exp) < POSITIVE_ORDINAL:
            res["sample_role"] = "negative"
        elif min(exp) >= POSITIVE_ORDINAL:
            res["sample_role"] = "positive"
        else:
            res["sample_role"] = "mixed"
        res["false_negative"] = (res["sample_role"] == "positive"
                                 and got < POSITIVE_ORDINAL)
        if got >= POSITIVE_ORDINAL and max(exp) < POSITIVE_ORDINAL:
            res["false_positive_track"] = (
                f"假阳性（实际 {ORDINAL_TO_VERDICT.get(got)}，官方期望最高 "
                f"{ORDINAL_TO_VERDICT.get(max(exp))}）")
            res["false_positive"] = True
        else:
            res["false_positive_track"] = "无假阳性"
            res["false_positive"] = False
    res["abstained"] = (got == ABSTAIN_ORDINAL) if got is not None else False
    # 假阳性对照案例（answer.json 的 fp_control=true）：Phase 0 / 闸门红灯
    # 是否命中——「刹车片有没有踩下」。命中 = must_trigger 全部命中且无假阳性。
    res["fp_control"] = bool(a.get("fp_control"))
    if res["fp_control"]:
        res["fp_control_redlight_hit"] = (not misses) and (not res["false_positive"])

    # ---- 引擎漂移检测 ----
    if do_rerun:
        actual, notes = rerun_engine(case)
        prov = v.get("codes_provenance") or {}
        recorded = sorted(set(prov.get("engine_derived") or []))
        if not prov.get("engine_derived"):
            # 神华 B2-07 教训：verdict 缺 codes_provenance 时 recorded 为空，
            # 全部重跑代号都被误报为「新增」。回退到 codes 全量比对并降级提示。
            recorded = sorted(set(v.get("codes") or []))
            res["notes"].append(
                "verdict 缺 codes_provenance.engine_derived：漂移比对回退到 codes 全量"
                "（Step 3 schema 不合规，未来案例由 --lint-verdict 在落盘时拦截）")
        res["notes"] += notes
        added = sorted(set(actual) - set(recorded))
        removed = sorted(set(recorded) - set(actual))
        res["drift"] = {"added": added, "removed": removed}
        if added or removed:
            res["failures"].append(f"引擎代号漂移：新增 {added} / 消失 {removed}")

    # ---- answer schema 咨询性检查（不阻塞：字段名规范化）----
    # 第二批出现 actual_5y_total_return（模板字段）与 actual_5y_price_total_return
    # （自定字段）并存——事后回报是元问题 1/2 的统计输入，缺位时提示。
    if a.get("actual_5y_total_return") is None and a.get("actual_5y_price_total_return") is None:
        res["notes"].append(
            "answer 缺 actual_5y_total_return/actual_5y_price_total_return"
            "（事后回报是元问题统计输入，建议按模板字段补齐）")

    # ---- price_basis 口径声明咨询性检查（P0-2，data-sourcing.md 复权口径纪律）----
    # 含事后回报数值的 answer 必须声明收益计算口径（等比后复权渠道+抓取日期），
    # 否则回报断言不可事后审计（第一批茅台/福耀先例：口径未声明导致不可精确复现）。
    has_return_value = any(
        a.get(k) is not None for k in
        ("actual_5y_total_return", "actual_3y_total_return",
         "actual_5y_price_total_return", "actual_3y_price_total_return"))
    if has_return_value and not a.get("price_basis"):
        res["notes"].append(
            "answer 含事后回报数值但缺 price_basis 口径声明"
            "（收益计算唯一合法口径=等比后复权，见 data-sourcing.md 复权口径纪律）")

    # ---- 已知失败 vs 新增回归 ----
    # 茅台档位轨未命中是第一批**记录在案**的真实假阴性（`backtest/REPORT.md` 元问题 3）。
    # 若把它一并算作红灯，本脚本就永远是红的、无法当回归门禁用。故 answer.json 可登记
    # `known_failures`（kind 短名：verdict_track / must_trigger / must_not_trigger /
    # engine_drift / other），只有**未登记**的失败才算回归。这不是掩盖问题——已知失败在
    # 输出中单独列示，且一旦被修好（失败消失）会提示更新登记。
    _classify_failures(res, a, do_rerun)

    # 假阳性绕过 known_failures 豁免：无论是否登记，一律计入 regressions（红灯）。
    # 这是刻意的不对称——错买不可逆，不允许用"已知"把它变成不阻塞。
    if res.get("false_positive"):
        msg = f"假阳性轨红灯：{res['false_positive_track']}"
        res["failures"].append(msg)
        res["regressions"].append(msg)
    return res


def fp_fn_summary(results):
    """REQ-P0-01：批次级假阳性 / 假阴性双向统计。

    只在样本角色明确的案例上计算（见 check_case 的 sample_role）：
      fp_rate = 假阳性数 / 负向样本数（官方期望 <3 的案例）
      fn_rate = 假阴性数 / 正向样本数（官方期望 >=3 的案例）
      abstain_rate = 档位=2 的案例 / 全部有档位的案例（系统弃权率，健康度体温计）
    负向样本数为 0 时 fp_rate 为 None——此时**不得**把「0 假阳性」表述为「无假阳性」，
    只能表述为「未被检验」（PROMPT 第八节元问题 4 的硬约束）。
    """
    neg = [r for r in results if r.get("sample_role") == "negative"]
    pos = [r for r in results if r.get("sample_role") == "positive"]
    scored = [r for r in results if r.get("sample_role") in ("negative", "positive", "mixed")]
    fps = [r["name"] for r in neg if r.get("false_positive")]
    fns = [r["name"] for r in pos if r.get("false_negative")]
    abst = [r["name"] for r in scored if r.get("abstained")]
    ctrl = [r for r in results if r.get("fp_control")]
    ctrl_hit = [r["name"] for r in ctrl if r.get("fp_control_redlight_hit")]
    out = {
        "negative_n": len(neg), "positive_n": len(pos), "scored_n": len(scored),
        "false_positives": fps, "false_negatives": fns, "abstained": abst,
        "fp_rate": (len(fps) / len(neg)) if neg else None,
        "fn_rate": (len(fns) / len(pos)) if pos else None,
        "abstain_rate": (len(abst) / len(scored)) if scored else None,
        "fp_rate_target": FP_RATE_TARGET, "fn_rate_target": FN_RATE_TARGET,
        "fp_control_n": len(ctrl), "fp_control_redlight_hits": ctrl_hit,
        "fp_control_redlight_hit_rate": (len(ctrl_hit) / len(ctrl)) if ctrl else None,
    }
    if out["fp_rate"] is not None and out["fn_rate"] is not None and out["fp_rate"] > 0:
        out["fp_fn_ratio"] = out["fn_rate"] / out["fp_rate"]
    else:
        out["fp_fn_ratio"] = None
    return out


def _fmt_rate(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def print_fp_fn_summary(s):
    """三轨分行陈述之外的第四段：双向错误率。禁止合并为单一数字。"""
    print("\n[FP/FN 双向统计]（REQ-P0-01；两者不得抵扣、不得合并）")
    if s["negative_n"] == 0:
        print("  假阳性率：未被检验（本轮无官方期望 <3 的负向样本）——不得表述为『无假阳性』")
    else:
        flag = "⛔ 超标" if s["fp_rate"] > s["fp_rate_target"] else "达标"
        print(f"  假阳性率：{_fmt_rate(s['fp_rate'])}（{len(s['false_positives'])}/{s['negative_n']}，"
              f"目标 ≤{_fmt_rate(s['fp_rate_target'])}）{flag}"
              + (f" → {s['false_positives']}" if s["false_positives"] else ""))
    if s["positive_n"] == 0:
        print("  假阴性率：未被检验（本轮无官方期望 >=3 的正向样本）")
    else:
        flag = "⚠ 超标（保守度过高）" if s["fn_rate"] > s["fn_rate_target"] else "达标"
        print(f"  假阴性率：{_fmt_rate(s['fn_rate'])}（{len(s['false_negatives'])}/{s['positive_n']}，"
              f"目标 ≤{_fmt_rate(s['fn_rate_target'])}）{flag}"
              + (f" → {s['false_negatives']}" if s["false_negatives"] else ""))
    if s["abstain_rate"] is not None:
        print(f"  弃权率（观察等价格）：{_fmt_rate(s['abstain_rate'])}"
              f"（{len(s['abstained'])}/{s['scored_n']}）")
    if s["fp_control_n"]:
        print(f"  假阳性对照红灯命中率：{_fmt_rate(s['fp_control_redlight_hit_rate'])}"
              f"（{len(s['fp_control_redlight_hits'])}/{s['fp_control_n']}）")
    else:
        print("  假阳性对照案例：0（fp_control=true 的 answer 尚无）——"
              "放松性改动前须先建立对照，见 PROMPT 第五之二节")


def lint_verdict(path):
    """Step 3 落盘体检——answer.json 尚不存在时，单验 verdict.json。

    第二批两个教训都来自「Step 3 没有机器校验、Step 4 才被 runner 看到」：
    - Zoom B2-11 落盘时杜撰了 NORM_ADJ_MEAN_REJECTED 等未注册代号（断言 ID
      铁律违反，靠事后 fix commit 撤出）；
    - 神华 B2-07 缺 codes_provenance（漂移检测失去比对基准）；
    - Zoom/Netflix 的 final_verdict 与 verdict_ordinal 文案自相矛盾
      （「观察等价格(档位1)」）。
    """
    v = json.load(open(path, encoding="utf-8"))
    problems = []
    advisories = []  # 不阻塞、但要看见（历史批次的 P0-08 缺失等）
    fired = set(v.get("codes") or [])
    bad = unknown_codes(fired)
    if bad:
        problems.append(f"codes 含未注册代号 {sorted(bad)}——断言 ID 铁律：禁止杜撰，"
                        "无注册表等价物的裁决语义移入 codes_note 留痕")
    for k in ("final_verdict", "verdict_ordinal", "gate1", "gate2",
              "codes", "frozen_before_diff", "frozen_at"):
        if v.get(k) is None:
            problems.append(f"缺必填字段 {k}")
    prov = v.get("codes_provenance") or {}
    eng, man = set(prov.get("engine_derived") or []), set(prov.get("manually_recorded") or [])
    if not eng and not man:
        problems.append("缺 codes_provenance（engine_derived/manually_recorded 区分是"
                        "漂移检测的比对基准，缺失时 rerun 比对回退到 codes 全量并降级）")
    elif fired and (eng | man) != fired:
        problems.append(f"codes_provenance 两类之并 ≠ codes 全集："
                        f"多出 {sorted((eng | man) - fired)} / 缺 {sorted(fired - (eng | man))}")
    ov = ORDINAL_TO_VERDICT.get(v.get("verdict_ordinal"))
    fv = v.get("final_verdict") or ""
    if ov and ov not in fv:
        problems.append(f"final_verdict『{fv}』与 verdict_ordinal={v.get('verdict_ordinal')}"
                        f"（{ov}）不自洽——复合表述也应包含档位词，如『拒绝（观察等价格）』")

    # ---- REQ-P0-08 规则版本钉死（第四批起强制；批次由同目录 meta.json 判定）----
    case_dir = os.path.dirname(os.path.abspath(path))
    meta = {}
    mp = os.path.join(case_dir, "meta.json")
    if os.path.exists(mp):
        try:
            meta = json.load(open(mp, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
    try:
        batch = int(meta.get("batch") or 0)
    except (TypeError, ValueError):
        batch = 0
    enforce_new = batch >= RULES_SNAPSHOT_MIN_BATCH
    snap = v.get("rules_snapshot")
    if not snap:
        (problems if enforce_new else advisories).append(
            "缺 rules_snapshot（REQ-P0-08）：运行 `prepare_case.py --snapshot-rules` 并写入——"
            "无版本快照的战绩无法区分『系统变好』与『规则变松』")
    else:
        if not snap.get("skill_commit"):
            problems.append("rules_snapshot 缺 skill_commit（git hash）")
        if snap.get("dirty"):
            problems.append("rules_snapshot.dirty=true：落 verdict 时工作区有未提交改动，"
                            "skill_commit 不代表实际运行的代码——先提交规则改动再落 verdict")
        if snap.get("missing"):
            problems.append(f"rules_snapshot.missing 非空 {snap['missing'][:3]}——"
                            "RULES_REGISTRY 与引擎常量不一致，快照不完整")
        th = snap.get("thresholds") or {}
        # mos_wide 是 mos_requirement 的兼容旧键（prepare_case 派生写入），只能豁免
        # MoS 一项，不能短路另两个关键阈值——原版 `and "mos_wide" not in th` 绑定
        # 在整个循环上，而快照只要 reverse_dcf 可导入就必有 mos_wide，导致三项
        # 非空校验恒不触发（REVIEW-REQ-P0-08 §1，实测 thresholds={"mos_wide":0.25}
        # 可原样放行）。拆开：折现率/悲观门槛各自独立校验，MoS 二者取其一。
        for k in ("discount_rate_default", "pessimistic_hurdle_default"):
            if th.get(k) is None:
                problems.append(f"rules_snapshot.thresholds 缺关键阈值 {k}")
        if not (th.get("mos_requirement") or th.get("mos_wide")):
            problems.append("rules_snapshot.thresholds 缺关键阈值 mos_requirement/mos_wide")

    # ---- REQ-P0-05 隔离检查交叉校验（第四批起强制）----
    # 执行者自觉写 contaminated 不可靠：这里用 seal-check 的 pre 模式（当前工作区不应有
    # answer 文件）反向核对。seal-check 失败而 verdict 未标 contaminated → lint 不过。
    if enforce_new:
        r = subprocess.run([_py(), os.path.join(REPO, "scripts", "prepare_case.py"),
                            "--seal-check", case_dir], capture_output=True, text=True, cwd=REPO)
        if r.returncode != 0 and not v.get("contaminated"):
            problems.append("prepare_case.py --seal-check 未通过但 verdict 未标 `contaminated: true`"
                            f"（REQ-P0-05）：\n      " + "\n      ".join(
                                l.strip() for l in r.stdout.splitlines() if l.strip().startswith("❌")))
        elif r.returncode == 0 and v.get("contaminated"):
            advisories.append("verdict 标了 contaminated 但 seal-check 通过——确认是否误标")

    if problems:
        print(f"❌ {path} 落盘体检未通过：")
        for p in problems:
            print(f"   - {p}")
        for a in advisories:
            print(f"   ⚠ {a}")
        return 1
    print(f"✅ {path} 落盘体检通过（codes 注册表/provenance/必填字段/档位自洽"
          f"{'/规则快照/隔离交叉' if enforce_new else ''}）")
    for a in advisories:
        print(f"   ⚠ {a}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="回放断言一键重跑")
    ap.add_argument("--batch", type=int, help="只跑指定批次（读 meta.json 的 batch）")
    ap.add_argument("--case", help="只跑指定案例目录名")
    ap.add_argument("--rerun", action="store_true", help="附带重跑引擎做漂移检测")
    ap.add_argument("--baseline", help="基线 JSON 路径：存在则比对，不存在则写入")
    ap.add_argument("--lint-verdict", metavar="VERDICT_JSON",
                    help="Step 3 落盘体检：只验 verdict.json（answer.json 尚不存在时用）")
    ap.add_argument("--as-of", metavar="COMMIT", dest="as_of",
                    help="REQ-P0-08：按指定 skill 版本（verdict.rules_snapshot.skill_commit）在临时 "
                         "worktree 中重跑本脚本（自动附加 --rerun），复现当时的引擎代号集合")
    ap.add_argument("-o", "--output", help="结果 JSON 输出路径")
    args = ap.parse_args()

    if args.lint_verdict:
        sys.exit(lint_verdict(args.lint_verdict))
    if args.as_of:
        passthrough = [x for x in sys.argv[1:] if x not in ("--as-of", args.as_of)]
        sys.exit(run_as_of(args.as_of, passthrough))

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

    results_all = [check_case(c, do_rerun=args.rerun) for c in cases]
    # REQ-P0-05：contaminated 案例从战绩表排除——单独列示，不进任何分母
    contaminated = [r for r in results_all if r.get("contaminated")]
    results = [r for r in results_all if not r.get("contaminated")]

    w = max(len(r["name"]) for r in results_all) + 2
    print(f"{'案例':<{w}} {'档位轨':<34} {'告警轨':<26} {'假阳性轨':<12} {'规则版本':<10} 结果")
    print("-" * (w + 100))
    for r in results_all:
        at = f"命中{len(r.get('assert_hits', []))} 漏{len(r.get('assert_misses', []))} 误触发{len(r.get('assert_false_fires', []))}"
        fp = "假阳性" if r.get("false_positive") else "-"
        rv = (r.get("rules_version") or "-").split("（")[0]
        if r.get("contaminated"):
            ok = "污染排除"
        elif r["regressions"]:
            ok = "回归失败"
        elif r["known"]:
            ok = "已知失败"
        else:
            ok = "通过"
        print(f"{r['name']:<{w}} {r.get('verdict_track', '-'):<34} {at:<26} {fp:<12} {rv:<10} {ok}")
    print("-" * (w + 100))
    if contaminated:
        print(f"⛔ {len(contaminated)} 例 contaminated（隔离失效）已从战绩表排除：{[r['name'] for r in contaminated]}")
    if not results:
        print("全部案例均为 contaminated，无可计分案例")
        sys.exit(1)
    clean = sum(1 for r in results if not r["failures"])
    known_only = sum(1 for r in results if r["known"] and not r["regressions"])
    regressed = [r for r in results if r["regressions"]]
    fps = [r["name"] for r in results if r.get("false_positive")]
    print(f"{clean}/{len(results)} 全绿" +
          (f"，{known_only} 已知失败（登记在 answer.json known_failures）" if known_only else "") +
          (f"，{len(regressed)} 回归失败" if regressed else ""))
    # 三轨分行陈述，禁止合并为单一战绩数字（PROMPT.md 第九节权重纪律）
    scored = sum(1 for r in results if r.get("false_positive_track", "").startswith(("无假阳性", "假阳性")))
    if fps:
        print(f"假阳性轨：{len(fps)} 例红灯 → {fps}")
        print("  ⛔ 放松性改动出现假阳性即否决，不得用『救回 N 个假阴性』抵扣。")
    elif scored:
        print(f"假阳性轨：0 例（本轮 {scored} 个案例参与该轨判定）")
    else:
        print("假阳性轨：未被检验（本轮无『官方期望拒绝/观察』的可判定案例）"
              "——不得表述为『无假阳性』。")

    fpfn = fp_fn_summary(results)
    print_fp_fn_summary(fpfn)

    # REQ-P0-08 规则版本汇总 / REQ-P0-05 隔离执行率（第 RULES_SNAPSHOT_MIN_BATCH 批起）
    versions = {}
    for r in results_all:
        versions.setdefault((r.get("rules_version") or "unknown").split("（")[0], []).append(r["name"])
    print("\n[规则版本] " + "；".join(f"{k}: {len(v)} 例" for k, v in sorted(versions.items())))
    if versions.get("drifted"):
        print("  规则已漂移的案例，其 rerun 漂移须与 `--as-of <commit>` 结果对读，不得直接归为引擎回归")
    scored_iso = [c for c in cases if int(c["meta"].get("batch") or 0) >= RULES_SNAPSHOT_MIN_BATCH]
    if scored_iso:
        iso_ok = sum(1 for c in scored_iso
                     if not any(f.startswith("隔离证据") for f in
                                next(r for r in results_all if r["name"] == c["name"])["failures"])
                     and not next(r for r in results_all if r["name"] == c["name"]).get("contaminated"))
        print(f"[隔离执行率] 第{RULES_SNAPSHOT_MIN_BATCH}批起 {iso_ok}/{len(scored_iso)}"
              f"（{iso_ok / len(scored_iso):.0%}，git 时序机器可验；详表 `prepare_case.py --isolation-report`）")

    for r in results_all:
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

    payload = {"results": results_all, "clean": clean, "known_only": known_only,
               "regressed": [r["name"] for r in regressed], "total": len(results),
               "contaminated": [r["name"] for r in contaminated],
               "rules_versions": versions,
               "fp_fn": fpfn}
    if args.output:
        json.dump(payload, open(args.output, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"\n已写入 {args.output}")

    if args.baseline:
        snap = {r["name"]: r["fired"] for r in results}
        # REQ-P0-01：基线附带 FP/FN 轨指标快照（下划线键，不参与代号集合比对）。
        # 放松性改动前后比较 _fp_fn.false_positives 集合，新增 ≥1 即红灯。
        snap_meta = {"fp_rate": fpfn["fp_rate"], "fn_rate": fpfn["fn_rate"],
                     "abstain_rate": fpfn["abstain_rate"],
                     "false_positives": fpfn["false_positives"],
                     "false_negatives": fpfn["false_negatives"],
                     "fp_control_n": fpfn["fp_control_n"],
                     "fp_control_redlight_hit_rate": fpfn["fp_control_redlight_hit_rate"]}
        if os.path.exists(args.baseline):
            base = json.load(open(args.baseline, encoding="utf-8"))
            base_meta = base.get("_fp_fn") or {}
            diffs = []
            for k in sorted(set(base) | set(snap)):
                if k.startswith("_"):
                    continue
                b, s = set(base.get(k, [])), set(snap.get(k, []))
                if b != s:
                    diffs.append(f"  {k}: 新增 {sorted(s - b)} / 消失 {sorted(b - s)}")
            new_fp = sorted(set(fpfn["false_positives"]) - set(base_meta.get("false_positives") or []))
            if new_fp:
                diffs.append(f"  ⛔ 假阳性轨相对基线新增：{new_fp}（红灯规则：直接否决，不得抵扣）")
            if diffs:
                print("\n与基线不一致：")
                print("\n".join(diffs))
                sys.exit(1)
            print(f"\n与基线一致（{len([k for k in snap if not k.startswith('_')])} 案例代号集合无变化"
                  + (f"，假阳性轨 {len(fpfn['false_positives'])} 例无新增" if base_meta else "") + "）")
            if not base_meta:
                print("  注意：基线缺 _fp_fn 段（旧版基线）——下次以 --baseline 写入新基线时自动补齐")
        else:
            snap["_fp_fn"] = snap_meta
            json.dump(snap, open(args.baseline, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            print(f"\n已写入基线 {args.baseline}（含 _fp_fn 段）")

    # 回归失败才是红灯；已知失败不阻塞（但会单独列示）
    sys.exit(1 if regressed else 0)


if __name__ == "__main__":
    main()
