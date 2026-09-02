#!/usr/bin/env python3
"""数据源探测（Phase 1 前置关卡）

## 为什么需要它

此前文档只写"用结构化金融数据接口"，从不说明**怎么装、装了有什么、没装缺什么**——
新用户拿到仓库其实跑不起来，而且缺源时会静默降级：分析师以为自己在用 A 级数据，
实际在用搜索来的 C 级数字。

纯文档纪律没有执行力，这一点已被实证：对立面检索写在文档里，10 个归档案例
只有 1 个留痕，最后靠 validate_data.py 加哨兵才解决。所以"提示安装"必须是
脚本行为，不能指望每次都记得说。

本脚本只做三件事：探测本机可用源 → 报告覆盖能力与缺口 → 给出降级成本。
**不联网、不改文件**，纯本地探测，可随时重复运行。

## 用法

    python3 scripts/check_data_sources.py            # 探测并报告
    python3 scripts/check_data_sources.py --manifest <公司>_analysis/data/manifest.json
                                                    # 额外校验 manifest 是否登记了 data_sources
    python3 scripts/check_data_sources.py --strict   # 无推荐源时以退出码 1 阻断

退出码：0 可继续（可含缺口提示）、1 严格模式下缺推荐源、3 脚本自身异常。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~")

# 已知的 westock-data 市场版本（用于版本漂移提示；升级本仓时同步更新）
KNOWN_WESTOCK_VER = "1.0.6"


def _ver_tuple(v: str):
    """"1.0.6" -> (1,0,6)，非数字段按 0 处理，便于比较。"""
    out = []
    for part in str(v).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)

# 各 Agent 平台的 skill 安装根目录（westock-data / ifind 都是平级 skill）
SKILL_ROOTS = [
    os.path.join(HOME, ".bg-agent", "config-with-app", "skills"),
    os.path.join(HOME, ".claude", "skills"),
    os.path.join(HOME, ".cursor", "skills"),
    os.path.join(HOME, ".agents", "skills"),
    os.path.join(HOME, ".windsurf", "skills"),
    os.path.join(HOME, ".trae", "skills"),
    os.path.join(HOME, ".config", "agents", "skills"),
]


def find_skill(name: str):
    """在各平台 skill 根目录中查找同名 skill，返回绝对路径或 None。"""
    for root in SKILL_ROOTS:
        p = os.path.join(root, name)
        if os.path.isdir(p):
            return p
    return None


def read_version(skill_dir: str):
    """从 SKILL.md 或 package.json 尽力读出版本号。"""
    for rel in ("SKILL.md", os.path.join("scripts", "package.json")):
        p = os.path.join(skill_dir, rel)
        if not os.path.exists(p):
            continue
        try:
            txt = open(p, encoding="utf-8").read()
        except Exception:  # noqa: BLE001
            continue
        if rel.endswith(".json"):
            try:
                return json.loads(txt).get("version")
            except Exception:  # noqa: BLE001
                pass
        else:
            for line in txt.splitlines():
                if line.strip().startswith("version:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
    return None


def node_ok():
    """westock-data 需要 Node ≥ 18。"""
    exe = shutil.which("node")
    if not exe:
        return False, None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
        major = int(out.lstrip("v").split(".")[0])
        return major >= 18, out
    except Exception:  # noqa: BLE001
        return False, None


def check_westock():
    d = find_skill("westock-data")
    if not d:
        return {
            "name": "westock-data（腾讯自选股）", "status": "missing", "level": "A",
            "note": "未安装。这是推荐默认源：免费、无需 key，已实证美股/A股/港股/"
                    "A股银行四条管道。",
            "fix": "从 Skill 市场安装 westock-data（公开页："
                   "https://skillhub.cn/skills/tencent-adm/westock-data）；"
                   "或按所在平台的 skill 安装方式部署。安装后重跑本脚本。",
            "cost": "缺它则三表/行情/一致预期/研报全部需改走官方渠道或手工，"
                    "A股与港股工作量显著上升（银行管道尤甚）。",
        }
    ok, ver = node_ok()
    entry = os.path.join(d, "scripts", "index.js")
    local_ver = read_version(d)
    r = {"name": "westock-data（腾讯自选股）", "status": "ok", "level": "A",
         "path": d, "version": local_ver,
         "note": "推荐默认源，免费无需 key。"
                 "命中其能力域时禁止 web_search 或 HTTP 直连替代。",
         "cmd": f"node {entry} <子命令>"}
    # 版本漂移提示：接口字段变更是"换源类静默错误"的常见来源
    # （本仓已实证同类事故：FY2025 收入标签从 RevenueFromContract... 改为 Revenues，
    #  抽取脚本沿用旧概念导致整年静默为空）。故本机版本落后即提示。
    if local_ver and _ver_tuple(local_ver) < _ver_tuple(KNOWN_WESTOCK_VER):
        r["drift"] = (f"本机 v{local_ver} 低于已知市场版本 v{KNOWN_WESTOCK_VER}——"
                      f"接口字段可能已变更。若拉数出现字段缺失/口径异常，"
                      f"先升级再排查建稿逻辑。")
    if not os.path.exists(entry):
        r["status"] = "broken"
        r["note"] = f"目录存在但入口缺失：{entry}"
        r["fix"] = "重新安装 westock-data。"
    elif not ok:
        r["status"] = "degraded"
        r["note"] = (f"已安装但 Node 环境不满足（需 ≥18，当前 {ver or '未检出'}）"
                     f"——脚本无法执行。")
        r["fix"] = "安装或升级 Node.js 到 18 以上。"
    return r


def check_ifind():
    d = find_skill("ifind-finance-data")
    if not d:
        return {
            "name": "ifind-finance-data（同花顺）", "status": "optional-missing",
            "level": "A", "note": "未安装。**付费且需自备 key**，非必需。",
            "fix": "仅当需要补 A股 capex/D&A 或银行专属科目（不良/拨备/NIM）时考虑："
                   "https://mcp.51ifind.com/gwstatic/static/ds_web/"
                   "ifind-mcp-web/skills/SKILL_INSTALL_GUIDE.md",
            "cost": "不影响主流程；上述两个缺口改由年报原文/研报补齐（B 级）。",
        }
    cfg = os.path.join(d, "mcp_config.json")
    keyed = False
    if os.path.exists(cfg):
        try:
            tok = (json.load(open(cfg, encoding="utf-8")).get("auth_token") or "").strip()
            keyed = bool(tok) and "your" not in tok.lower()
        except Exception:  # noqa: BLE001
            keyed = False
    return {
        "name": "ifind-finance-data（同花顺）",
        "status": "ok" if keyed else "no-key", "level": "A", "path": d,
        "version": read_version(d),
        "note": ("已安装且密钥已配置，可用于补 A股 capex/D&A 与银行专属科目。"
                 if keyed else
                 "已安装但密钥未配置（占位符或为空），调用会 401。"),
        "fix": None if keyed else
        "到 https://mcp.51ifind.com 个人中心→密钥管理取 key，写入 mcp_config.json 的 auth_token。",
    }


def check_official():
    """官方渠道无需安装，恒可用（联网前提下），此处只做提示登记。"""
    return [
        {"name": "SEC EDGAR（美股官方）", "status": "always", "level": "A",
         "note": "免费免鉴权（需 User-Agent 头）。美股永久兜底，仍算 A 级。"
                 "本仓 scripts/extract_edgar_annual.py 直连。"},
        {"name": "巨潮资讯网（A股官方）", "status": "always", "level": "A",
         "note": "A股年报原文/处罚/问询函。**命门科目双源核对强制走原文**。"},
        {"name": "港交所披露易（港股官方）", "status": "always", "level": "A",
         "note": "港股年报公告原文、合股供股配售史（老千股排查）。"},
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="数据源探测（Phase 1 前置）")
    ap.add_argument("--manifest", help="校验该 manifest.json 是否登记 data_sources")
    ap.add_argument("--strict", action="store_true",
                    help="无可用推荐源时以退出码 1 阻断")
    args = ap.parse_args()

    ws, fi = check_westock(), check_ifind()
    rows = [ws, fi] + check_official()

    icon = {"ok": "✅", "always": "✅", "missing": "❌", "broken": "❌",
            "degraded": "⚠️ ", "no-key": "⚠️ ", "optional-missing": "⚪"}

    print("=" * 64)
    print("数据源探测（本 skill 不绑定单一源；唯一契约是 data/*.json 底稿）")
    print("=" * 64)
    for r in rows:
        ver = f" v{r['version']}" if r.get("version") else ""
        print(f"\n{icon.get(r['status'], '?')} [{r['level']}] {r['name']}{ver}")
        print(f"   {r['note']}")
        if r.get("path"):
            print(f"   路径：{r['path']}")
        if r.get("cmd"):
            print(f"   调用：{r['cmd']}")
        if r.get("drift"):
            print(f"   ⚠️  版本漂移：{r['drift']}")
        if r.get("cost"):
            print(f"   降级成本：{r['cost']}")
        if r.get("fix"):
            print(f"   → 处理：{r['fix']}")

    # 已知缺口提醒：即使推荐源可用，这几项仍需手工补
    print("\n" + "-" * 64)
    print("已知缺口（推荐源也覆盖不到，须按 data-sources.md 应对）：")
    print("  · A股 capex/D&A 无独立科目 → 年报现金流量表原文 或 研报序列（B级）")
    print("  · A股 TotalAssets 缺失 → 负债+全口径权益 推导")
    print("  · 银行不良/拨备/NIM/资本充足率 → 评级报告附录（B级）+ 年报转引")
    print("  · 港股接口为港币口径 → 按期末汇率反推，fx_basis 必填")
    print("  · 美股早年 capex 标签可能缺失 → 回 10-K 原文补")

    errors = 0
    if args.manifest:
        print("\n" + "-" * 64)
        if not os.path.exists(args.manifest):
            print(f"[错误] manifest 不存在：{args.manifest}")
            errors += 1
        else:
            try:
                mf = json.load(open(args.manifest, encoding="utf-8"))
                ds = mf.get("data_sources")
                if not ds:
                    print("[错误] manifest 未登记 `data_sources`——"
                          "换源重跑时数字对不上将无从追溯。"
                          '格式：[{"source":"westock-data","version":"1.0.6",'
                          '"used_for":["三表","行情"],"level":"A"}]')
                    errors += 1
                else:
                    print(f"[通过] manifest 已登记 {len(ds)} 个数据源："
                          f"{[x.get('source') for x in ds if isinstance(x, dict)]}")
            except Exception as e:  # noqa: BLE001
                print(f"[错误] manifest 解析失败：{e}")
                errors += 1

    print("\n" + "=" * 64)
    blocked = args.strict and ws["status"] not in ("ok",) and fi["status"] != "ok"
    if errors or blocked:
        if blocked:
            print("结果：阻断（严格模式且无可用推荐源）。"
                  "可装 westock-data 后重跑，或去掉 --strict 走官方渠道降级路径。")
        else:
            print(f"结果：不合格（{errors} 项错误）")
        return 1
    print("结果：可继续。实际使用的源必须登记进 manifest.json 的 data_sources。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[异常] 探测器自身错误：{exc}")
        sys.exit(3)
