#!/usr/bin/env python3
"""prepare_case.py — 回测案例答案密封/揭示工具（隔离协议 B 档的文件闸门）

背景：执行环境的 subagent 支持不稳定，A 档（独立子上下文）不可依赖。
B 档协议 = 双会话 + 文件闸门：答案在 Step 3 verdict commit 之前**物理上不存在**
于工作区明文中；由本脚本在 verdict 提交后才解码落地。

用法：
  python3 scripts/prepare_case.py --seal backtest/ANSWERS.md
      # 把未执行批次（第三/四/五批）答案从 ANSWERS.md 抽出，逐案例写入 backtest/sealed_answers/
  python3 scripts/prepare_case.py --reveal backtest/<ticker>_<date>/
      # 机器校验该案例 verdict.json 已被 git 提交后，解码落地 answer_source.md
  python3 scripts/prepare_case.py --status
      # 查看密封库状态（哪些案例已密封/已揭示）
  python3 scripts/prepare_case.py --seal-check backtest/<ticker>_<date>/
      # 进入 verdict 阶段前扫描该案例隔离是否完好（REQ-P0-05）
  python3 scripts/prepare_case.py --seal-check backtest/<ticker>_<date>/ --audit
      # 事后审计：只看 git 时序证据（answer 文件此时理应存在，不算污染）
  python3 scripts/prepare_case.py --isolation-report
      # 按批次输出隔离执行率（REQ-P0-05 验收指标）
  python3 scripts/prepare_case.py --snapshot-rules
      # 输出规则版本快照 JSON（REQ-P0-08，写入 verdict.json.rules_snapshot）

密封格式：base64(json)。这不是加密——目的是：
  1. grep/glob 扫工作区时不会把答案明文带进执行上下文；
  2. 「揭示」留下可验证的 git 时序（answer_source.md 的 commit 必须晚于 verdict commit）；
  3. 揭示动作本身有机器闸门（verdict 未提交则拒绝），不靠自觉。

揭示闸门（机器可验，全部满足才落地）：
  G1  案例目录存在 verdict.json；
  G2  verdict.json 已被 git 提交（git log 有记录）且工作区无未提交修改——
      防止「先写 verdict 再看答案」被绕成「verdict 还攥在手里随时可改」；
  G3  案例目录尚未存在 answer_source.md / answer.json（防重复揭示）。
任一不满足即拒绝并退出非零。

注意：第一/二批答案仍在 git 历史的 ANSWERS.md 明文中。执行第三批及之后的
上下文**禁止用 git log / git show 回取任何批次答案**（PROMPT 第七节明文禁令）。
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEALED_DIR = REPO_ROOT / "backtest" / "sealed_answers"

# ANSWERS.md 别名 → 案例目录名（目录名 = <ticker>_<replay_date>，与 PROMPT 案例表一致）
ALIAS_TO_CASE = {
    "招行": "600036.SH_2014-12-31",
    "平安": "601318.SH_2018-12-31",
    "长和": "0001.HK_2015-12-31",
    "Meta": "META_2022-11-30",
    "恒大": "3333.HK_2020-06-30",
    "伊利": "600887.SH_2013-12-31",
    "GE": "GE_2016-12-31",
    "中石油": "601857.SH_2007-11-05",
    "Valeant": "VRX_2015-07-31",
    "分众": "002027.SZ_2018-05-31",
    "分众传媒": "002027.SZ_2018-05-31",
    "福特": "F_2005-06-30",
    "新城": "601155.SH_2019-06-30",
    "新城控股": "601155.SH_2019-06-30",
    # 第五批（类型卡阈值锚 + 豁免收窄验证，2026-09-10 设计）
    "长电": "600900.SH_2013-12-31",
    "长江电力": "600900.SH_2013-12-31",
    "格力": "000651.SZ_2015-09-30",
    "腾讯": "0700.HK_2018-10-30",
    "诺基亚": "NOK_2007-10-31",
    "康师傅": "0322.HK_2014-01-31",
    "牧原": "002714.SZ_2021-02-22",
}

SEALED_BATCHES = [3, 4, 5]  # 未执行批次，答案必须密封；一/二批已执行，保留明文
_BATCH_CN = {3: "三", 4: "四", 5: "五"}


def _git(args, cwd=REPO_ROOT):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def _seal(answers_md: Path) -> int:
    text = answers_md.read_text(encoding="utf-8")
    SEALED_DIR.mkdir(parents=True, exist_ok=True)
    sealed, unmatched = [], []

    for batch in SEALED_BATCHES:
        # 段落标记形如 **第三批** 或 **第四批（假阳性专项——…）**
        m = re.search(rf"^\*\*第{_BATCH_CN[batch]}批.*?\*\*\s*$", text, re.M)
        if not m:
            print(f"⚠ 未找到第{batch}批段落标记，跳过")
            continue
        seg = text[m.end():]
        nxt = re.search(r"^(\*\*第.{1,3}批|---|# )", seg, re.M)
        if nxt:
            seg = seg[:nxt.start()]
        # 段内条目形如 "- 招行 2014-12：核心买入。……"
        for line in seg.splitlines():
            line = line.strip()
            em = re.match(r"^-\s+(\S[^：:]*?)\s+(\d{4}-\d{2})[：:](.+)$", line)
            if not em:
                continue
            alias, ym, body = em.group(1), em.group(2), em.group(3)
            case = ALIAS_TO_CASE.get(alias)
            if not case or not case.startswith(case.split("_")[0]):
                unmatched.append(line[:40])
                continue
            if ym not in case:
                unmatched.append(f"{line[:40]}（日期 {ym} 与目录 {case} 不符）")
                continue
            payload = {"case": case, "batch": batch, "alias": alias,
                       "official_answer": f"{alias} {ym}：{body}"}
            enc = SEALED_DIR / f"{case}.enc"
            enc.write_text(base64.b64encode(
                json.dumps(payload, ensure_ascii=False).encode()).decode(), encoding="utf-8")
            sealed.append(f"{case}（第{batch}批）")

    if unmatched:
        print("⚠ 以下条目未能映射到案例目录，须人工处理：")
        for u in unmatched:
            print(f"   - {u}")
    print(f"✅ 已密封 {len(sealed)} 案：")
    for s in sealed:
        print(f"   - {s}")
    print(f"密封库：{SEALED_DIR}")
    print("下一步（人工）：从 ANSWERS.md 删除第三/四批段落，加密封指针说明后提交。")
    return 1 if unmatched else 0


def _reveal(case_dir: Path) -> int:
    if not case_dir.is_dir():
        print(f"❌ 案例目录不存在：{case_dir}")
        return 1
    name = case_dir.name
    verdict = case_dir / "verdict.json"

    # G1
    if not verdict.exists():
        print(f"❌ G1 不满足：{verdict} 不存在——先完成 Step 3 结论落盘")
        return 1

    # G2：已被提交 且 无未提交修改
    log = _git(["log", "--format=%H %ci", "--", str(verdict.relative_to(REPO_ROOT))])
    if not log.stdout.strip():
        print(f"❌ G2 不满足：{verdict} 尚未被 git 提交——"
              f"先 `git add + commit`（单独提交，PROMPT Step 3），再揭示答案")
        return 1
    st = _git(["status", "--porcelain", "--", str(verdict.relative_to(REPO_ROOT))])
    if st.stdout.strip():
        print(f"❌ G2 不满足：{verdict} 有未提交修改：{st.stdout.strip()}")
        return 1

    # G3：防重复揭示
    for f in ("answer_source.md", "answer.json"):
        if (case_dir / f).exists():
            print(f"❌ G3 不满足：{f} 已存在——本案答案已揭示过，禁止重复执行")
            return 1

    enc = SEALED_DIR / f"{name}.enc"
    if not enc.exists():
        print(f"❌ 密封库中无此案例：{enc}")
        return 1

    payload = json.loads(base64.b64decode(enc.read_text(encoding="utf-8")))
    out = case_dir / "answer_source.md"
    out.write_text(
        f"# 官方答案（密封揭示，第{payload['batch']}批 {payload['alias']}）\n\n"
        f"> 揭示闸门：verdict commit {log.stdout.strip().splitlines()[0]}\n"
        f"> 先于本次揭示（PROMPT 第七节 B 档文件闸门）。\n\n"
        f"{payload['official_answer']}\n", encoding="utf-8")
    print(f"✅ 答案已落地：{out}")
    print(f"   verdict commit：{log.stdout.strip().splitlines()[0]}")
    print("下一步（Step 4）：读 answer_source.md 创建 answer.json，跑断言，写 diff.md。")
    return 0


def _status() -> int:
    if not SEALED_DIR.exists():
        print("密封库不存在（尚未 --seal）")
        return 0
    rows = []
    for enc in sorted(SEALED_DIR.glob("*.enc")):
        case = enc.stem
        cd = REPO_ROOT / "backtest" / case
        revealed = (cd / "answer_source.md").exists() or (cd / "answer.json").exists()
        rows.append((case, "已揭示" if revealed else "密封中"))
    for case, s in rows:
        print(f"  {s}  {case}")
    print(f"共 {len(rows)} 案")
    return 0


# ── REQ-P0-05 隔离协议机器检查 ─────────────────────────────────────
# 第二批 6 个案例中真正执行隔离的只有 1 例。靠自觉不行，靠机器。
# 两种运行时机、两套检查项（审查发现：原版把"工作区有 answer 文件"当污染，
# 于是 Step 4 之后对任何案例都必报污染，事后无法区分"当时真隔离了"与"没隔离"）：
#   pre   verdict 落盘前（Step 2.5）：工作区不得有答案明文 + git 时序 + ANSWERS.md 明文
#   audit 事后审计（收官/战绩统计）：只看 git 时序证据，不看工作区文件——
#         answer 文件此时理应存在，存在本身不是污染
# 任一命中输出 contaminated=True 并返回非零退出码。

_ANSWER_FILES = ("answer.json", "answer_source.md", "diff.md")


def _git_commit_times(path: str, diff_filter: str | None = None) -> list[int]:
    """文件的提交时间戳列表（新→旧）。diff_filter='A' 只取新增提交。"""
    args = ["log", "--format=%ct"]
    if diff_filter:
        args.append(f"--diff-filter={diff_filter}")
    args += ["--", path]
    out = _git(args).stdout.split()
    return [int(x) for x in out if x.strip()]


def _case_aliases(case_name: str) -> list[str]:
    """目录名 → ANSWERS.md 可能使用的全部别名（反查 ALIAS_TO_CASE）+ ticker 本体。

    原版直接用 ticker 子串搜 ANSWERS.md：福特 ticker 是 `F`，任何含 F 的行都命中
    （假阳性）；中石油 ticker `601857.SH` 而 ANSWERS.md 写的是"中石油"（假阴性）。
    """
    aliases = [a for a, c in ALIAS_TO_CASE.items() if c == case_name]
    ticker = case_name.split("_")[0]
    if len(ticker) >= 4:  # 单字母/双字母 ticker 不做子串搜索，避免误命中
        aliases.append(ticker)
    return aliases


def _seal_check(case_dir: Path, audit: bool = False) -> int:
    """隔离完好性检查。audit=False 为 verdict 落盘前模式，True 为事后审计模式。"""
    if not case_dir.is_dir():
        print(f"❌ 案例目录不存在：{case_dir}")
        return 1
    name = case_dir.name
    issues = []
    rel = lambda f: str((case_dir / f).relative_to(REPO_ROOT))  # noqa: E731

    # ── 检查 1（仅 pre 模式）：工作区答案明文 ──
    # 密封库"已被揭示"的判据与本项完全相同（answer_source.md 存在），不再单列。
    if not audit:
        for f in _ANSWER_FILES:
            if (case_dir / f).exists():
                issues.append(f"工作区存在答案文件 {f}——verdict 落盘前不应出现")

    # ── 检查 2：git 时序 ──
    # 2a  verdict.json 首次提交 < 每个 answer 文件首次提交（先落盘结论再看答案）
    # 2b  verdict.json **最后一次**提交 ≤ answer 文件首次提交——原版只比首次 commit，
    #     "先落盘、看完答案再改 verdict 再提交一次"检不出来
    # 2c  meta.json 首次提交 < verdict.json 首次提交（Step 1 先验冻结先于结论，
    #     PROMPT 神华教训，此前无人机器检查）
    v_adds = _git_commit_times(rel("verdict.json"), "A")
    v_all = _git_commit_times(rel("verdict.json"))
    v_first = v_adds[-1] if v_adds else None
    v_last = v_all[0] if v_all else None
    m_adds = _git_commit_times(rel("meta.json"), "A")
    m_first = m_adds[-1] if m_adds else None

    for af in _ANSWER_FILES:
        a_adds = _git_commit_times(rel(af), "A")
        if not a_adds:
            if audit and af in ("answer.json", "answer_source.md"):
                issues.append(f"{af} 无 git 提交记录——时序证据不可验（audit 模式要求答案文件已提交）")
            continue
        a_first = a_adds[-1]
        if v_first is None:
            issues.append(f"{af} 已提交但 verdict.json 从未提交——时序证据不支持隔离")
        elif a_first <= v_first:
            issues.append(f"{af} 首次提交（epoch {a_first}）不晚于 verdict.json 首次提交"
                          f"（epoch {v_first}）——先看答案后落盘，或同 commit 提交")
        elif v_last is not None and v_last > a_first:
            issues.append(f"verdict.json 在 {af} 首次提交（epoch {a_first}）之后仍被修改并提交"
                          f"（epoch {v_last}）——结论在看到答案后被改动，隔离失效")
    if v_first is not None and m_first is not None and m_first >= v_first:
        # 同 commit 视为违规：先验冻结与结论落盘必须是两次提交（PROMPT Step 1/3）
        issues.append(f"meta.json 首次提交（epoch {m_first}）不早于 verdict.json 首次提交"
                      f"（epoch {v_first}）——先验冻结应先于结论落盘")
    if audit and v_first is None:
        issues.append("verdict.json 无 git 提交记录——无法审计时序")

    # ── 检查 3：ANSWERS.md 密封批次段落是否仍含该案例明文 ──
    answers_md = REPO_ROOT / "backtest" / "ANSWERS.md"
    if answers_md.exists():
        content = answers_md.read_text(encoding="utf-8")
        aliases = _case_aliases(name)
        for batch in SEALED_BATCHES:
            m = re.search(rf"^\*\*第{_BATCH_CN[batch]}批.*?\*\*\s*$", content, re.M)
            if not m:
                continue
            seg = content[m.end():]
            nxt = re.search(r"^(\*\*第.{1,3}批|---|# )", seg, re.M)
            if nxt:
                seg = seg[:nxt.start()]
            hit = [a for a in aliases if re.search(rf"^-\s+{re.escape(a)}\s+\d{{4}}-\d{{2}}", seg, re.M)]
            if hit:
                issues.append(f"ANSWERS.md 第{batch}批段落仍含 {hit[0]} 明文答案——"
                              f"应先 --seal 密封并从明文段落删除")
                break

    mode = "audit（事后审计，只看 git 时序）" if audit else "pre（verdict 落盘前）"
    if issues:
        print(f"⛔ 隔离检查失败（{name}，模式 {mode}）：")
        for iss in issues:
            print(f"   ❌ {iss}")
        print("\n  verdict.json 须标注 `\"contaminated\": true`（lint-verdict 会交叉校验）")
        print("  contaminated 案例从战绩表排除（run_backtest_assertions 跳过判定）。")
        return 1
    print(f"✅ 隔离检查通过（{name}，模式 {mode}）")
    return 0


# ── REQ-P0-08 规则版本钉死 ──────────────────────────────────────────
# verdict.json 不记录当时的规则版本 → 历史战绩失去参照。
# 本函数生成 rules_snapshot 字典，供 Step 3 写入 verdict.json。
#
# 阈值来源登记表：(快照键, 模块, 属性名)。审查发现原版用 getattr(..., None) 静默
# 吞掉不存在的属性，折现率/悲观门槛在快照里记成 null 而无人察觉。现在：
#   - 属性缺失 → 记入 `missing` 列表并在 stdout 警告，快照不再"看起来完整"
#   - 覆盖面从 6 项扩到双闸门 + 排雷 + 情景门禁的全部模块级阈值
RULES_REGISTRY = [
    # 双闸门 / 估值
    ("mos_requirement", "reverse_dcf", "MOAT_MOS_REQUIREMENT"),
    ("moat_score_wide_min", "reverse_dcf", "MOAT_SCORE_WIDE_MIN"),
    ("moat_score_narrow_min", "reverse_dcf", "MOAT_SCORE_NARROW_MIN"),
    ("discount_rate_default", "reverse_dcf", "DEFAULT_DISCOUNT_RATE"),
    ("terminal_growth_default", "reverse_dcf", "DEFAULT_TERMINAL_GROWTH"),
    ("terminal_growth_cap", "reverse_dcf", "DEFAULT_TERMINAL_GROWTH_CAP"),
    ("min_spread", "reverse_dcf", "DEFAULT_MIN_SPREAD"),
    ("hold_years_default", "reverse_dcf", "DEFAULT_HOLD_YEARS"),
    ("index_hurdle_default", "reverse_dcf", "DEFAULT_INDEX_HURDLE"),
    ("floor_hurdle_default", "reverse_dcf", "DEFAULT_FLOOR_HURDLE"),
    ("industry_risk_premium", "reverse_dcf", "INDUSTRY_RISK_PREMIUM"),
    ("market_floor_hurdles", "reverse_dcf", "MARKET_FLOOR_HURDLES"),
    ("prob_anchors", "reverse_dcf", "PROB_ANCHORS"),
    ("prob_deviation_free", "reverse_dcf", "PROB_DEVIATION_FREE"),
    ("prob_deviation_max", "reverse_dcf", "PROB_DEVIATION_MAX"),
    ("pessimistic_hurdle_default", "reverse_dcf", "DEFAULT_PESSIMISTIC_HURDLE"),
    ("loss_prob_hurdle_default", "reverse_dcf", "DEFAULT_LOSS_PROB_HURDLE"),
    # 情景门禁
    ("scen_dispersion_max", "check_scenarios", "DISPERSION_MAX"),
    ("scen_s2b_tolerance", "check_scenarios", "S2B_TOLERANCE"),
    ("scen_non_op_material", "check_scenarios", "NON_OP_MATERIAL"),
    ("scen_value_trap_mos", "check_scenarios", "VALUE_TRAP_MOS"),
    ("scen_value_trap_decline_years", "check_scenarios", "VALUE_TRAP_DECLINE_YEARS"),
    # 排雷（forensic_screen 全部 TH_* 由下方动态收集）
    ("forensic_veto_weight", "forensic_screen", "VETO_WEIGHT"),
    ("forensic_redflag_weight", "forensic_screen", "REDFLAG_WEIGHT"),
    # 数据门禁
    ("crosscheck_tol_core", "crosscheck_official", "TOL"),
    ("crosscheck_tol_balance_sheet", "crosscheck_official", "TOL_BALANCE_SHEET"),
    ("crosscheck_tol_other", "crosscheck_official", "TOL_OTHER"),
    ("spike_threshold", "validate_data", "SPIKE_THRESHOLD"),
    # 回测计分
    ("positive_ordinal", "run_backtest_assertions", "POSITIVE_ORDINAL"),
    ("fp_rate_target", "run_backtest_assertions", "FP_RATE_TARGET"),
    ("fn_rate_target", "run_backtest_assertions", "FN_RATE_TARGET"),
]


def snapshot_rules() -> dict:
    """返回当前 skill 的关键阈值快照与 git commit hash。

    用途：写入 verdict.json 的 `rules_snapshot`，让任何一次"系统改善了"
    的声明都可按旧版本重跑验证。`dirty=True` 时 commit hash 不代表实际运行
    的代码，lint-verdict 会拒绝这样的 verdict。
    """
    import importlib
    commit = _git(["rev-parse", "HEAD"])
    short = _git(["rev-parse", "--short", "HEAD"])
    dirty = bool(_git(["status", "--porcelain"]).stdout.strip())

    if str(REPO_ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
    thresholds, missing, mods = {}, [], {}
    for key, mod_name, attr in RULES_REGISTRY:
        try:
            mod = mods.get(mod_name) or importlib.import_module(mod_name)
            mods[mod_name] = mod
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{key}（模块 {mod_name} 导入失败：{exc}）")
            continue
        if not hasattr(mod, attr):
            missing.append(f"{key}（{mod_name}.{attr} 不存在）")
            continue
        thresholds[key] = getattr(mod, attr)
    # forensic_screen 的全部 TH_* 阈值：排雷 11 条的算术化参数，动态收集免得漏登记
    fs = mods.get("forensic_screen")
    if fs is not None:
        thresholds["forensic_thresholds"] = {
            k: getattr(fs, k) for k in sorted(dir(fs)) if k.startswith("TH_")}
    # 派生：护城河反推 IRR 门槛（默认 r/H 下），便于人读；mos_wide/mos_narrow 为兼容旧键
    rd = mods.get("reverse_dcf")
    if rd is not None and hasattr(rd, "moat_irr_hurdle"):
        mos = thresholds.get("mos_requirement") or {}
        thresholds["mos_wide"], thresholds["mos_narrow"] = mos.get("wide"), mos.get("narrow")
        try:
            r, h = thresholds.get("discount_rate_default", 0.10), thresholds.get("hold_years_default", 5)
            thresholds["irr_hurdle_derived"] = {
                m: (rd.moat_irr_hurdle(m, r, h) or (None, None))[1] for m in ("wide", "narrow")}
        except Exception:  # noqa: BLE001
            pass
    if missing:
        print(f"⚠ 规则快照有 {len(missing)} 项阈值未取到（RULES_REGISTRY 与引擎不一致，须修）：",
              file=sys.stderr)
        for m in missing:
            print(f"   - {m}", file=sys.stderr)

    return {
        "skill_commit": commit.stdout.strip(),
        "skill_commit_short": short.stdout.strip(),
        "dirty": dirty,
        "thresholds": thresholds,
        "missing": missing,
    }


def _isolation_report(min_batch: int = 3) -> int:
    """REQ-P0-05 验收：按批次输出隔离执行率（audit 模式逐案例跑 git 时序检查）。"""
    import io
    import contextlib
    rows = []
    for cd in sorted((REPO_ROOT / "backtest").iterdir()):
        if not cd.is_dir() or not (cd / "verdict.json").exists():
            continue
        meta = {}
        if (cd / "meta.json").exists():
            try:
                meta = json.loads((cd / "meta.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
        try:
            batch = int(meta.get("batch") or 0)
        except (TypeError, ValueError):
            batch = 0
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _seal_check(cd, audit=True)
        try:
            v = json.loads((cd / "verdict.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            v = {}
        rows.append({"case": cd.name, "batch": batch, "isolated": rc == 0,
                     "contaminated_flag": bool(v.get("contaminated")),
                     "legacy": batch < min_batch})
    by_batch: dict[int, list] = {}
    for r in rows:
        by_batch.setdefault(r["batch"], []).append(r)
    print(f"{'批次':<6}{'案例数':<8}{'隔离通过':<10}{'执行率':<10}备注")
    for b in sorted(by_batch):
        rs = by_batch[b]
        ok = sum(1 for r in rs if r["isolated"])
        note = "legacy（旧流程，不计入验收）" if b < min_batch else ""
        print(f"{b:<6}{len(rs):<8}{ok:<10}{ok / len(rs):<10.0%}{note}")
    bad = [r for r in rows if not r["legacy"] and not r["isolated"] and not r["contaminated_flag"]]
    if bad:
        print("\n⛔ 以下案例隔离审计未通过且 verdict.json 未标 contaminated：")
        for r in bad:
            print(f"   - {r['case']}（第{r['batch']}批）")
        return 1
    scored = [r for r in rows if not r["legacy"]]
    if scored:
        print(f"\n第{min_batch}批起隔离执行率："
              f"{sum(1 for r in scored if r['isolated'])}/{len(scored)}（机器可验，git 时序）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="回测答案密封/揭示（B 档文件闸门）")
    ap.add_argument("--seal", metavar="ANSWERS_MD", help="从 ANSWERS.md 抽出第三/四批答案密封")
    ap.add_argument("--reveal", metavar="CASE_DIR", help="verdict 提交后揭示该案例答案")
    ap.add_argument("--seal-check", metavar="CASE_DIR", dest="seal_check",
                    help="verdict 落盘前扫描隔离完好性（REQ-P0-05）")
    ap.add_argument("--audit", action="store_true",
                    help="配合 --seal-check：事后审计模式，只看 git 时序、不把 answer 文件存在当污染")
    ap.add_argument("--isolation-report", action="store_true", dest="isolation_report",
                    help="按批次输出隔离执行率（REQ-P0-05 验收指标）")
    ap.add_argument("--snapshot-rules", action="store_true", dest="snapshot_rules",
                    help="输出当前规则版本快照 JSON（REQ-P0-08，写入 verdict.json）")
    ap.add_argument("--status", action="store_true", help="查看密封库状态")
    args = ap.parse_args()
    if args.seal:
        sys.exit(_seal(Path(args.seal)))
    if args.reveal:
        sys.exit(_reveal(Path(args.reveal).resolve()))
    if args.seal_check:
        sys.exit(_seal_check(Path(args.seal_check).resolve(), audit=args.audit))
    if args.isolation_report:
        sys.exit(_isolation_report())
    if args.snapshot_rules:
        print(json.dumps(snapshot_rules(), ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.status:
        sys.exit(_status())
    ap.print_help()
    sys.exit(0)


if __name__ == "__main__":
    main()
