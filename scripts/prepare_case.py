#!/usr/bin/env python3
"""prepare_case.py — 回测案例答案密封/揭示工具（隔离协议 B 档的文件闸门）

背景：执行环境的 subagent 支持不稳定，A 档（独立子上下文）不可依赖。
B 档协议 = 双会话 + 文件闸门：答案在 Step 3 verdict commit 之前**物理上不存在**
于工作区明文中；由本脚本在 verdict 提交后才解码落地。

用法：
  python3 scripts/prepare_case.py --seal backtest/ANSWERS.md
      # 把第三/四批答案从 ANSWERS.md 抽出，逐案例写入 backtest/sealed_answers/
  python3 scripts/prepare_case.py --reveal backtest/<ticker>_<date>/
      # 机器校验该案例 verdict.json 已被 git 提交后，解码落地 answer_source.md
  python3 scripts/prepare_case.py --status
      # 查看密封库状态（哪些案例已密封/已揭示）

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
}

SEALED_BATCHES = [3, 4]  # 这两批未执行，答案必须密封；一/二批已执行，保留明文


def _git(args, cwd=REPO_ROOT):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def _seal(answers_md: Path) -> int:
    text = answers_md.read_text(encoding="utf-8")
    SEALED_DIR.mkdir(parents=True, exist_ok=True)
    sealed, unmatched = [], []

    for batch in SEALED_BATCHES:
        # 段落标记形如 **第三批** 或 **第四批（假阳性专项——…）**
        m = re.search(rf"^\*\*第{'三四'[batch - 3]}批.*?\*\*\s*$", text, re.M)
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


def main():
    ap = argparse.ArgumentParser(description="回测答案密封/揭示（B 档文件闸门）")
    ap.add_argument("--seal", metavar="ANSWERS_MD", help="从 ANSWERS.md 抽出第三/四批答案密封")
    ap.add_argument("--reveal", metavar="CASE_DIR", help="verdict 提交后揭示该案例答案")
    ap.add_argument("--status", action="store_true", help="查看密封库状态")
    args = ap.parse_args()
    if args.seal:
        sys.exit(_seal(Path(args.seal)))
    if args.reveal:
        sys.exit(_reveal(Path(args.reveal).resolve()))
    if args.status:
        sys.exit(_status())
    ap.print_help()
    sys.exit(0)


if __name__ == "__main__":
    main()
