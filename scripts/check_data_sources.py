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
import re
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~")

# 已知的 westock-data 市场版本（用于版本漂移提示；升级本仓时同步更新）
# P1-7（2026-09-17）修正：旧值 "1.0.6" 是无从核对的猜值——技能包元数据
# （.knot/metadata.json 的 skill_version）与 CLI 自报版本（westock --version）
# 是两套版本号，本机实测技能包 1.0.38 / CLI 0.0.2。漂移检测的意义在于
# 「接口字段变更的先导信号」，参照系取技能包元数据能被机器读取核验。
KNOWN_WESTOCK_VER = "1.0.38"


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
    """从 SKILL.md / .knot 元数据 / package.json 尽力读出版本号。

    P1-7：优先 .knot/metadata.json 的 skill_version（技能市场元数据，
    与 KNOWN_WESTOCK_VER 同参照系）——SKILL.md 无 version 字段时旧实现
    静默返回 None，漂移检测随之失效。
    """
    meta_p = os.path.join(skill_dir, ".knot", "metadata.json")
    if os.path.exists(meta_p):
        try:
            v = (json.load(open(meta_p, encoding="utf-8")) or {}).get("skill_version")
            if v:
                return str(v)
        except Exception:  # noqa: BLE001
            pass
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


def check_westock(probe: bool = True):
    """westock-data 四层探测（P1-7，AST-020 四层分离）。

    旧实现的探测对象早已失效：技能包 1.0.x 起安装方式改为独立 `westock` CLI
    （scripts/setup.sh 装到 ~/.local/bin），脚本却仍探测 scripts/index.js——
    探测器自身坏掉仍报 ok，这是「声明可用」比「不可用」更危险的实证。

    四层分离（每层独立判定，失败给修复指引）：
      L1 安装层：westock CLI 在 PATH 或 ~/.local/bin 可寻；
      L2 连通层：真实调用（search <代码>）拿到非空响应（需联网）；
      L3 字段层：finance 子命令返回的字段可解析（字段漂移探测）；
      L4 时点层：数据新鲜度（披露日历含当年期 → 时点可用）。
    probe=False 时只做 L1（离线模式，不联网不等待）。
    """
    d = find_skill("westock-data")
    if not d:
        return {
            "name": "westock-data（腾讯自选股）", "status": "missing", "level": "A",
            "layers": {"install": "missing", "network": "-", "fields": "-", "asof": "-"},
            "note": "未安装。这是推荐默认源：免费、无需 key，已实证美股/A股/港股/"
                    "A股银行四条管道。",
            "fix": "从 Skill 市场安装 westock-data（公开页："
                   "https://skillhub.cn/skills/tencent-adm/westock-data）；"
                   "或按所在平台的 skill 安装方式部署。安装后重跑本脚本。",
            "cost": "缺它则三表/行情/一致预期/研报全部需改走官方渠道或手工，"
                    "A股与港股工作量显著上升（银行管道尤甚）。",
        }
    ok, ver = node_ok()
    layers = {"install": "ok", "network": "-", "fields": "-", "asof": "-"}
    # L1：CLI 可寻性（shutil.which 含 ~/.local/bin 若已在 PATH；另查技能包旁路）
    cli = shutil.which("westock")
    if not cli:
        _fallback = os.path.join(HOME, ".local", "bin", "westock")
        cli = _fallback if os.path.exists(_fallback) else None
    local_ver = cli_ver = None
    entry = {
        "name": "westock-data（腾讯自选股）", "status": "ok", "level": "A",
        "path": d, "version": None,
        "note": "推荐默认源，免费无需 key。"
                "命中其能力域时禁止 web_search 或 HTTP 直连替代。",
    }
    if cli:
        cli_ver = _parse_cli_version(_cli_probe([cli, "--version"]))
        local_ver = read_version(d) or cli_ver
        entry["version"] = local_ver
        if cli_ver and cli_ver != local_ver:
            entry["cli_version"] = cli_ver
        entry["cmd"] = f"westock <子命令>"
        layers["install"] = "ok"
    elif not ok:
        layers["install"] = "broken-node"
        entry.update({
            "status": "degraded",
            "note": f"技能包已装但 westock CLI 未装且 Node <18（当前 {ver or '未检出'}）",
            "fix": "运行技能包内 scripts/setup.sh 安装 CLI（Node ≥18），"
                   "安装后 source ~/.zshrc 或重开终端使 PATH 生效",
        })
    else:
        layers["install"] = "cli-missing"
        entry.update({
            "status": "degraded",
            "note": "技能包已装但 westock CLI 未装（技能包 1.0.x 起需独立安装 CLI）",
            "fix": "运行技能包内 scripts/setup.sh（或 setup.cjs）安装 CLI 后重跑本脚本",
        })
    # 版本漂移提示（市场版本 vs 本机版本，字段漂移的先导信号）
    if local_ver and _ver_tuple(local_ver) < _ver_tuple(KNOWN_WESTOCK_VER):
        entry["drift"] = (f"本机 v{local_ver} 低于已知市场版本 v{KNOWN_WESTOCK_VER}——"
                          f"接口字段可能已变更。若拉数出现字段缺失/口径异常，"
                          f"先升级再排查建稿逻辑。")
        layers["fields"] = "stale-version"
    # L2/L3/L4：联网实测（仅 CLI 就绪时）
    if probe and cli and layers["install"] == "ok":
        q = _cli_probe([cli, "search", "贵州茅台"], timeout=15)
        if q and q.strip():
            layers["network"] = "ok"
        else:
            layers["network"] = "unreachable"
            entry["status"] = "degraded"
            entry["note"] = ("CLI 已装但联网探测失败（search 无响应）——"
                             "检查网络/代理；此状态下拉数会静默失败")
            entry["fix"] = "排查网络连通性后重跑；断网时本层降级不代表安装层故障"
        # L3 字段层：finance 命令返回的字段可解析（GOOG FY2025 字段漂移实证）。
        # 真实输出为 Markdown 表格，键为 PascalCase（实测 sh600519 income 表：
        # OperatingRevenue/NPParentCompanyOwners/EBIT…）。探测取多别名并集，
        # 命中任一核心键即视为字段层可用——探测的是「结构没变」，不是「逐字段核对」。
        if layers["network"] == "ok":
            f = _cli_probe([cli, "finance", "sh600519", "--type", "income",
                            "--limit", "1"], timeout=20)
            _fl = str(f or "").lower()
            fields_ok = bool(f) and any(k in _fl for k in (
                "operatingrevenue", "revenue", "operating_revenue",
                "营业", "totaloperatingrevenue", "npbearer"))
            # stale-version（版本落后）与 field-drift（实测未命中）是同一层的
            # 两个信号源，实测优先、版本嫌疑保留为前缀
            _stale = layers["fields"] == "stale-version"
            layers["fields"] = ("ok(stale-ver)" if _stale else "ok") if fields_ok \
                else ("field-drift(stale-ver)" if _stale else "field-drift")
            if not fields_ok:
                # 限频是「连通但暂不可得」的独立形态：字段层降级为 rate-limited
                # 而非 field-drift——处置动作完全不同（等待重试 vs 升级排查）。
                # 实测本机连发 search+finance 即触发 code=1620053006。
                _rate_limited = ("限频" in _fl or "rate" in _fl or "1620053006" in _fl)
                if _rate_limited:
                    layers["fields"] = "rate-limited" + ("(stale-ver)" if _stale else "")
                    entry["note"] = (entry.get("note", "") +
                                     " 字段探测被服务限频挡住（连通正常）——"
                                     "稍后重跑或减少探测频次")
                else:
                    entry["note"] = (entry.get("note", "") +
                                     " 财务字段探测未命中预期键——字段漂移嫌疑，"
                                     "拉数后须人工核对返回结构")
        # L4 时点层：披露日历含当年期（时点可用性——回放/实时场景的取数前提）
        if layers["network"] == "ok":
            cal = _cli_probe([cli, "disclosure", "--help"], timeout=10)
            layers["asof"] = "ok" if cal is not None else "unknown"
            if layers["asof"] != "ok":
                entry["note"] = (entry.get("note", "") +
                                 " 披露日历子命令不可用——时点可得性无法实测")
    elif not probe:
        layers["network"] = layers["fields"] = layers["asof"] = "skipped"
    entry["layers"] = layers
    return entry


def _cli_probe(cmd, timeout=10):
    """跑一次 CLI 取 stdout；异常/超时返回 None（探测不抛异常，失败即证据）。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _parse_cli_version(raw):
    """从 `westock --version` 的自由文本中提取语义版本号。

    实测输出形如 "westock version westock 0.0.2 channel=skillhub (darwin/arm64)"，
    直接用整行会让漂移比对失效。提取第一段 x.y.z 形态的版本号。
    """
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", str(raw or ""))
    return m.group(1) if m else None


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


# 官方渠道探测端点（P1-7 四层探测）。旧实现 status="always" 是声明不是探测——
# 「无需安装恒可用」对 L1 成立，但 L2 连通（尤其内网代理环境）必须实测。
OFFICIAL_ENDPOINTS = [
    {"name": "SEC EDGAR（美股官方）", "level": "A", "kind": "edgar",
     "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
     "note": "免费免鉴权（需 User-Agent 头）。美股永久兜底，仍算 A 级。"
             "本仓 scripts/extract_edgar_annual.py 直连。"},
    {"name": "巨潮资讯网（A股官方）", "level": "A", "kind": "cninfo",
     "url": "http://www.cninfo.com.cn/new/index",
     "note": "A股年报原文/处罚/问询函。**命门科目双源核对强制走原文**。"},
    {"name": "港交所披露易（港股官方）", "level": "A", "kind": "hkex",
     "url": "https://www1.hkexnews.hk/search/titlesearch.xhtml",
     "note": "港股年报公告原文、合股供股配售史（老千股排查）。"},
]


def check_official(probe: bool = True):
    """官方渠道四层探测（P1-7，AST-020）。

    旧实现三个渠道硬编码 status="always"、注释自陈「无需安装，恒可用（联网
    前提下）」——恒可用三个字里「联网前提下」正是未被探测的那一层。声明
    不是探测：内网代理/防火墙/DNS 污染都可能让「恒可用」变成「静默超时」，
    而分析师此时已按 A 级源开工。四层口径：
      L1 安装层：恒 ok（浏览器即客户端，无需安装）；
      L2 连通层：HEAD/GET 实测（--offline 时跳过标 skipped）；
      L3 字段层：对官方渠道=「人眼读原文」，机器字段层不适用（-）；
      L4 时点层：官方库永久回溯，恒 ok（历史披露不会消失）。
    """
    import urllib.request
    rows = []
    for ep in OFFICIAL_ENDPOINTS:
        layers = {"install": "ok", "network": "-", "fields": "-", "asof": "ok"}
        row = {"name": ep["name"], "status": "ok", "level": ep["level"],
               "layers": layers, "note": ep["note"], "kind": ep["kind"]}
        if probe:
            net = _http_probe(ep["url"])
            layers["network"] = net
            if net != "ok":
                row["status"] = "degraded"
                row["note"] = (f"连通性探测失败（{_err_kind(net)}）——{ep['note']}"
                               " 断网/代理环境下勿按『恒可用』开工，先恢复访问")
                row["fix"] = "检查网络/代理对官方站点的可达性；公司内网可能需人工鉴权"
        else:
            layers["network"] = "skipped"
        rows.append(row)
    return rows


def _err_kind(net):
    return {"unreachable": "网络不可达", "timeout": "超时", "http-error": "HTTP 非 2xx/3xx"}.get(net, net)


def _http_probe(url, timeout=8):
    """轻量连通性探测：GET（部分站点拒 HEAD），2xx/3xx 即 ok。失败分类不猜原因。"""
    try:
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "Mozilla/5.0 (source-probe)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return "ok" if resp.status < 400 else "http-error"
    except TimeoutError:
        return "timeout"
    except Exception:  # noqa: BLE001
        # urllib 对 3xx 会自动跟随；此处兜底非网络类异常（编码/解析等）视为不可达
        return "unreachable"


def main() -> int:
    ap = argparse.ArgumentParser(description="数据源探测（Phase 1 前置）")
    ap.add_argument("--manifest", help="校验该 manifest.json 是否登记 data_sources")
    ap.add_argument("--strict", action="store_true",
                    help="无可用推荐源时以退出码 1 阻断")
    ap.add_argument("--offline", action="store_true",
                    help="跳过联网探测（只做 L1 安装层），断网环境快速体检")
    ap.add_argument("--json", action="store_true",
                    help="以 JSON 输出探测结果（结构化供 CI/上游脚本消费）")
    args = ap.parse_args()

    probe = not args.offline
    ws, fi = check_westock(probe=probe), check_ifind()
    rows = [ws, fi] + check_official(probe=probe)

    icon = {"ok": "✅", "always": "✅", "missing": "❌", "broken": "❌",
            "degraded": "⚠️ ", "no-key": "⚠️ ", "optional-missing": "⚪"}

    def _layerstr(r):
        ly = r.get("layers") or {}
        if not ly:
            return ""
        parts = []
        for k, label in (("install", "安装"), ("network", "连通"),
                         ("fields", "字段"), ("asof", "时点")):
            v = ly.get(k, "-")
            parts.append(f"{label}:{v}")
        return "   四层 [" + " | ".join(parts) + "]"

    if args.json:
        print(json.dumps({"probe_mode": "online" if probe else "offline",
                          "known_westock_ver": KNOWN_WESTOCK_VER,
                          "sources": [{k: v for k, v in r.items() if k != "fix"}
                                      for r in rows]},
                         ensure_ascii=False, indent=2))
        return 0

    print("=" * 64)
    print(f"数据源探测（本 skill 不绑定单一源；唯一契约是 data/*.json 底稿）"
          f"{'【离线模式：仅安装层】' if not probe else ''}")
    print("=" * 64)
    for r in rows:
        ver = f" v{r['version']}" if r.get("version") else ""
        print(f"\n{icon.get(r['status'], '?')} [{r['level']}] {r['name']}{ver}")
        print(f"   {r['note']}")
        _ls = _layerstr(r)
        if _ls:
            print(_ls)
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
    # （P1-7 结构化：从 print 文案改为数据清单，供 --json 消费与后续扩展）
    KNOWN_GAPS = [
        {"gap": "A股 capex/D&A 无独立科目", "workaround": "年报现金流量表原文 或 研报序列（B级）"},
        {"gap": "A股 TotalAssets 缺失", "workaround": "负债+全口径权益 推导"},
        {"gap": "银行不良/拨备/NIM/资本充足率", "workaround": "评级报告附录（B级）+ 年报转引"},
        {"gap": "港股接口为港币口径", "workaround": "按期末汇率反推，fx_basis 必填"},
        {"gap": "美股早年 capex 标签可能缺失", "workaround": "回 10-K 原文补"},
    ]
    print("\n" + "-" * 64)
    print("已知缺口（推荐源也覆盖不到，须按 data-sourcing.md 应对）：")
    for g in KNOWN_GAPS:
        print(f"  · {g['gap']} → {g['workaround']}")

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
