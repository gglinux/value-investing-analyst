#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crosscheck_official.py — 命门科目对官方源自动核对（A股/港股/美股）

## 为什么需要它

双源核对此前是**纯手工转录**：分析师读年报、把数字敲进底稿 `crosscheck` 区块，
`validate_data.py` 再拿它跟 `annual` 比。这有两个结构性弱点：

1. **同人同眼**：抄错和看错年份不会被发现——手抄进 crosscheck 的值和手填进
   annual 的值来自同一次阅读，比对的是"我抄得一致吗"，不是"接口对不对"。
   本仓已有实证：GOOG 的 FY2025 因 XBRL 概念标签切换整年静默为空，
   10 个案例逐一评审都没看出来。
2. **懒惰路径通畅**：官方值填 None 此前只告警，于是 11 个归档案例里
   6 个从未核对 `shares_diluted`——而它是 eps/每股内在价值的分母。

本工具把美股这条路自动化：直接从 SEC EDGAR companyfacts 取官方 XBRL 值，
与底稿 annual 逐年逐科目比对，**机器取数、机器比对**，不经人手转录。
A股/港股无免鉴权结构化官方源，仍需人工转录，但本工具负责校验其完整性
（哪年哪个科目没核对，一目了然）。

## 与 validate_data.py 的分工

- 本工具：**生成/核验** crosscheck 区块（对外取官方值，需网络或本地 companyfacts）。
- validate_data：**消费** crosscheck 区块做门禁（纯本地、零网络）。

保持这个分界，是为了不破坏"计算层零网络依赖、可离线复现"的设计。

## 用法

    # 美股：从 EDGAR 自动生成 crosscheck 区块（写回底稿或输出到 stdout）
    python3 crosscheck_official.py --financials data/financials_goog.json \\
        --companyfacts data/filings/goog_companyfacts.json --taxonomy us-gaap [--write]

    # 任意市场：只体检现有 crosscheck 的完整性（不联网、不取数）
    python3 crosscheck_official.py --financials data/financials_yili.json --audit

退出码：0 通过；1 发现偏差或强制科目缺核对；2 用法/输入错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 命门四科目（银行口径见 validate_data 的 is_bank 分支）
CORE_FIELDS = ["revenue", "net_income", "ocf", "shares_diluted"]
BANK_FIELDS = ["operating_income", "net_income"]
TOL = 0.01  # 与 validate_data.TOL 保持一致：1%

# REQ-P0-07 源冲突裁决——差异阈值（详细规则见 data-sourcing.md 第九节）
TOL_BALANCE_SHEET = 0.03   # 资产负债表科目 >3% 告警
TOL_OTHER = 0.05           # 其他科目 >5% 登记
BALANCE_SHEET_FIELDS = {"total_assets", "total_equity", "total_debt",
                        "total_liabilities", "non_current_assets", "cash",
                        "cash_and_equivalents", "interest_bearing_debt",
                        "goodwill", "inventory", "receivables"}
# 命门科目扩展：需求正文点名"货币资金、有息负债"也是命门（>1% 阻断）
CORE_EXTRA_FIELDS = {"cash", "cash_and_equivalents", "interest_bearing_debt", "total_debt"}
NON_VALUE_KEYS = {"year", "source", "source_tier", "note", "notes"}

# 源优先级（1=最高）
SOURCE_PRIORITY = {
    "edgar_xbrl": 1, "cninfo_pdf": 1, "hkex_pdf": 1,   # 监管官方原文
    "company_ir": 2,                                      # 公司官网原文
    "westock": 3, "ifind": 3,                             # A 级接口
    "research_report": 4, "wind_screenshot": 4,           # B 级二手
    "web_search": 5, "media": 5,                          # C 级兜底
}
# 自由文本 source → tier 的关键词推断（crosscheck.source 是人写的描述，没有 tier 字段时用）
_TIER_HINTS = [
    (1, ("10-k", "10k", "20-f", "20f", "edgar", "xbrl", "companyfacts", "巨潮", "cninfo",
         "披露易", "hkex", "年报", "年度报告", "审计报告", "annual report", "sec ", "决算短信",
         "tanshin", "业绩公告", "季报", "半年报", "主要会计数据", "对比栏", "对照列", "官方原文",
         "finalpage", "accession", "filed ")),
    (2, ("官网", "投资者关系", "ir.", "investor", "股东信", "shareholder letter", "公告原文",
         "fuyaogroup", "港版")),
    (3, ("westock", "ifind", "wind", "choice", "东方财富", "接口", "api", "tushare", "行情终端")),
    (4, ("研报", "券商", "research", "截图", "screenshot", "wind 截图")),
    (5, ("搜索", "web", "媒体", "新闻", "media", "news", "百度", "google", "新浪", "转引")),
]


def source_tier(entry_or_text) -> int:
    """crosscheck 条目（或 source 文本）→ 源优先级 tier。显式 `source_tier` 优先，
    其次按关键词推断；推不出记 5（最低），逼执行者写清出处。"""
    if isinstance(entry_or_text, dict):
        st = entry_or_text.get("source_tier")
        if st in SOURCE_PRIORITY:
            return SOURCE_PRIORITY[st]
        if isinstance(st, int) and 1 <= st <= 5:
            return st
        text = str(entry_or_text.get("source") or "")
    else:
        text = str(entry_or_text or "")
    low = text.lower()
    for tier, hints in _TIER_HINTS:
        if any(h in low for h in hints):
            return tier
    return 5


def tol_for(field: str, core_fields) -> tuple[float, str]:
    """字段 → (容差, 严重度)。命门 >1% block；资产负债表 >3% warn；其他 >5% register。"""
    if field in core_fields or field in CORE_EXTRA_FIELDS:
        return TOL, "block"
    if field in BALANCE_SHEET_FIELDS:
        return TOL_BALANCE_SHEET, "warn"
    return TOL_OTHER, "register"


EXEMPT_REQUIRED_KEYS = ("adopted_value", "adopted_source", "rejected_value",
                        "rejected_source", "reason")


def exempt_detail(exempt: dict, field: str):
    """crosscheck_exempt[field] → (是否有效豁免, 描述, 结构问题列表)。

    结构化格式 {adopted_value, adopted_source, rejected_value, rejected_source, reason}
    为 data-sourcing.md 第九节要求；纯字符串为 legacy 格式（接受但告警，逼迁移）。
    """
    e = (exempt or {}).get(field)
    if not e:
        return False, "", []
    if isinstance(e, dict):
        miss = [k for k in EXEMPT_REQUIRED_KEYS if e.get(k) in (None, "")]
        desc = e.get("reason") or json.dumps(e, ensure_ascii=False)[:80]
        return True, desc, ([f"crosscheck_exempt.{field} 缺 {miss}（结构化豁免五要素）"] if miss else [])
    return True, str(e)[:120], [f"crosscheck_exempt.{field} 为 legacy 字符串格式——"
                                 "请迁移为 {adopted_value, adopted_source, rejected_value, rejected_source, reason}"]

# EDGAR XBRL 概念候选，与 extract_edgar_annual.py 同源（逐年独立回退）
CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "Revenue", "RevenueFromContractsWithCustomers"],
    "net_income": ["NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent",
                   "ProfitLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "CashFlowsFromUsedInOperatingActivities"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding",
                       "WeightedAverageShares",
                       "WeightedAverageNumberOfDilutedSharesOutstandingIfrs"],
}


def rel_diff(a, b):
    if a is None or b is None:
        return None
    a, b = float(a), float(b)
    denom = max(abs(a), abs(b))
    return 0.0 if denom == 0 else abs(a - b) / denom


def edgar_annual(cf: dict, taxonomy: str, field: str, scale: float):
    """从 companyfacts 抽某科目的年度值。

    按 `end` 日期归年（不用 fy 标签——TSM 实证：FPI 的 fy 是申报财年而非
    期间所属年，按 fy 聚合会整体错位一年）。逐年独立尝试全部候选概念
    （GOOG 实证：概念标签中途切换，"第一个非空概念用到底"会静默丢年）。
    """
    facts = (cf.get("facts") or {}).get(taxonomy) or {}
    out: dict[int, tuple[float, str]] = {}
    for concept in CONCEPTS.get(field, []):
        node = facts.get(concept)
        if not node:
            continue
        for unit_key, rows in (node.get("units") or {}).items():
            for r in rows:
                # 年报口径：优先 10-K/20-F 的 FY 期间数据
                if r.get("form") not in ("10-K", "20-F", "10-K/A", "20-F/A"):
                    continue
                end, val = r.get("end"), r.get("val")
                if not end or val is None:
                    continue
                # 期间长度过滤（避免季度/半年数据混入年度）
                start = r.get("start")
                if start:
                    try:
                        from datetime import date
                        ds = date.fromisoformat(start)
                        de = date.fromisoformat(end)
                        if (de - ds).days < 300:
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                y = int(end[:4])
                # 财年末在 1-5 月的（如 NVDA 1月、TSM），归前一年
                if int(end[5:7]) <= 5:
                    y -= 1
                # 单位缩放：EDGAR 金额为元、股本为股；底稿统一为「百万」口径
                # （百万元 / 百万股）。此前误将 shares 视为无需缩放，导致三年
                # 全部误报 100% 偏差，反而掩盖了真正写错的那一年——单位错误
                # 会淹没真实信号，故此处金额与股本用同一 scale。
                if unit_key.lower() in ("pure", "usd/shares", "eur/shares"):
                    continue  # 比率/每股类单位不参与金额核对
                if y not in out:
                    out[y] = (float(val) / scale, concept)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="命门科目对官方源自动核对")
    ap.add_argument("--financials", required=True, help="底稿 financials_*.json")
    ap.add_argument("--companyfacts", help="EDGAR companyfacts.json（美股自动核对）")
    ap.add_argument("--taxonomy", default="us-gaap",
                    choices=["us-gaap", "ifrs-full"])
    ap.add_argument("--scale", type=float, default=1e6,
                    help="金额缩放：EDGAR 为元，底稿多为百万，默认 1e6")
    ap.add_argument("--years", type=int, default=3, help="核对最近 N 年，默认 3")
    ap.add_argument("--audit", action="store_true",
                    help="仅体检现有 crosscheck 完整性，不联网取数")
    ap.add_argument("--write", action="store_true",
                    help="将自动取到的官方值写回底稿 crosscheck 区块")
    args = ap.parse_args()

    if not os.path.exists(args.financials):
        print(f"[错误] 底稿不存在：{args.financials}")
        return 2
    data = json.load(open(args.financials, encoding="utf-8"))
    rows = data.get("annual") or []
    if not rows:
        print("[错误] 底稿无 annual 区块")
        return 2

    is_bank = (data.get("company_type") or "").lower() in ("bank", "银行")
    fields = BANK_FIELDS if is_bank else CORE_FIELDS
    by_year = {r.get("year"): r for r in rows}
    target_years = sorted(by_year)[-args.years:]
    cc_raw = data.get("crosscheck") or []
    cc_legacy = [c for c in cc_raw if not isinstance(c, dict)]
    cc = {r.get("year"): r for r in cc_raw if isinstance(r, dict)}
    exempt = data.get("crosscheck_exempt") or {}
    # 底稿 annual 的来源 tier（meta.source_ref 是人写的描述，按关键词推断）
    annual_tier = source_tier((data.get("meta") or {}).get("source_ref") or data.get("source") or "")

    print("=" * 70)
    print(f"命门科目核对：{os.path.basename(args.financials)}"
          f"{'（银行口径）' if is_bank else ''}")
    print(f"强制科目：{fields}    容差：命门 {TOL:.0%} / 资产负债表 {TOL_BALANCE_SHEET:.0%} / 其他 {TOL_OTHER:.0%}"
          f"    核对年度：{target_years}")
    print("=" * 70)

    errors, warns, auto = 0, 0, 0
    conflicts = []  # REQ-P0-07 差异表（含已裁决与未裁决）

    def judge(y, f, dv, ov, src_text, src_tier, concept=None):
        """统一裁决：返回 ('ok'|'exempt'|'conflict', severity)。副作用：打印、登记 conflicts。"""
        nonlocal errors, warns, auto
        tol, sev = tol_for(f, fields)
        d = rel_diff(ov, dv)
        tag = f"  [{concept}]" if concept else ""
        if d is None:
            print(f"  ❌ {y} {f:16} 底稿缺值，官方 {ov}")
            errors += 1
            return "conflict", "block"
        if d <= tol:
            print(f"  ✅ {y} {f:16} {dv} ≈ {ov} ({d:.2%}){tag}")
            auto += 1
            return "ok", sev
        ok_ex, desc, problems = exempt_detail(exempt, f)
        # 裁决方向：高优先级源为准。tier 相同或底稿更高时，仍以 crosscheck 侧（官方原文）为准——
        # crosscheck 的语义就是"对官方源核对"，annual 侧 tier 只用于报告披露。
        adopted_side = "crosscheck" if src_tier <= annual_tier else "annual"
        row = {"year": y, "field": f, "annual_value": dv, "official_value": ov,
               "annual_tier": annual_tier, "official_tier": src_tier,
               "source": (concept or src_text or "")[:160], "diff_pct": round(d, 4),
               "severity": sev, "adopted_side": adopted_side,
               "resolved": ok_ex, "resolution": desc if ok_ex else None}
        conflicts.append(row)
        if ok_ex:
            print(f"  ⚠️  {y} {f:16} 底稿 {dv} vs 官方 {ov} 偏差 {d:.1%}{tag}——已豁免（{desc[:60]}）")
            warns += 1
            for p in problems:
                print(f"      ↳ {p}")
                warns += 1
            return "exempt", sev
        icon = {"block": "❌", "warn": "⚠️ ", "register": "📝"}[sev]
        print(f"  {icon} {y} {f:16} 底稿 {dv} vs 官方 {ov} 偏差 {d:.1%} > {tol:.0%}{tag}"
              f"  → 以 {'官方源' if adopted_side == 'crosscheck' else '底稿源'}（tier {min(src_tier, annual_tier)}）为准")
        if sev == "block":
            errors += 1
        else:
            warns += 1
        return "conflict", sev

    # ---- 模式一：EDGAR 自动取数比对（机器取数，不经人手转录）----
    if args.companyfacts and not args.audit:
        if not os.path.exists(args.companyfacts):
            print(f"[错误] companyfacts 不存在：{args.companyfacts}")
            return 2
        cf = json.load(open(args.companyfacts, encoding="utf-8"))
        print("\n[模式] EDGAR 自动核对（机器取数 → 机器比对，无人工转录）\n")
        official = {f: edgar_annual(cf, args.taxonomy, f, args.scale)
                    for f in fields}
        for y in target_years:
            for f in fields:
                ov = official.get(f, {}).get(y)
                dv = by_year[y].get(f)
                if ov is None:
                    print(f"  ⚠️  {y} {f:16} EDGAR 未取到（概念标签可能变更）")
                    warns += 1
                    continue
                val, concept = ov
                st, _ = judge(y, f, dv, round(val, 2), "EDGAR XBRL", SOURCE_PRIORITY["edgar_xbrl"], concept)
                if st == "ok" and args.write:
                    cc.setdefault(y, {"year": y})
                    cc[y][f] = round(val, 2)
                    cc[y]["source"] = (
                        f"[E:{os.path.basename(args.companyfacts)}] "
                        f"EDGAR XBRL 自动核对")
                    cc[y]["source_tier"] = "edgar_xbrl"
        if args.write and cc:
            data["crosscheck"] = [cc[k] for k in sorted(cc)]

    # ---- 模式二：完整性体检 + 逐科目比对（A股/港股人工转录路径的守卫）----
    else:
        print("\n[模式] crosscheck 完整性体检 + 分级阈值比对"
              "（A股/港股无免鉴权官方结构化源，须人工转录）\n")
        if cc_legacy:
            print(f"  ❌ crosscheck 含 {len(cc_legacy)} 条非结构化（纯文本）条目——机器无法比对，"
                  "请改为 {{year, source, <科目>: 值}} 结构（双汇 000895 legacy 形态）")
            errors += 1
        if not cc:
            # 竞对公司免原文核对（与 validate_data 的 --skip-crosscheck 一致）：
            # 命门核对只强制主公司。误把竞对底稿当主公司体检会产生假警报。
            if data.get("is_peer") or data.get("role") == "peer":
                print("  ⚪ 竞对底稿（is_peer）：命门核对只强制主公司，跳过。"
                      "报告脚注须披露该公司未做原文核对")
                print("\n" + "=" * 70)
                print("结果：竞对底稿，跳过核对。")
                return 0
            print("  ❌ 无 crosscheck 区块——命门科目完全未核对")
            print("     （若这是竞对底稿，请在底稿标记 `\"is_peer\": true`）")
            errors += 1
        for y in target_years:
            entry = cc.get(y)
            if not entry:
                print(f"  ❌ {y} 未登记核对")
                errors += 1
                continue
            src = str(entry.get("source") or "")
            if not src:
                print(f"  ❌ {y} 缺 source 出处")
                errors += 1
            tier = source_tier(entry)
            if tier >= 4:
                print(f"  ⚠️  {y} source tier {tier}（B/C 级二手源）——命门核对应以监管原文/公司原文为准")
                warns += 1
            miss = [f for f in fields if entry.get(f) is None]
            for f in miss:
                ok_ex, desc, problems = exempt_detail(exempt, f)
                if ok_ex:
                    print(f"  ⚠️  {y} {f:16} 未核对，已豁免（{desc[:60]}）")
                    warns += 1
                    for p in problems:
                        print(f"      ↳ {p}")
                        warns += 1
                else:
                    print(f"  ❌ {y} {f:16} 强制科目缺官方值＝该科目未被核对")
                    errors += 1
            # 命门科目 + crosscheck 条目里登记的**任何其他数值科目**都进分级比对
            # （原版只比命门四科目，资产负债表/其他阈值是死常量）
            extra = [k for k, v in entry.items()
                     if k not in NON_VALUE_KEYS and k not in fields and isinstance(v, (int, float))]
            for f in fields + extra:
                ov, dv = entry.get(f), by_year[y].get(f)
                if ov is None:
                    continue
                judge(y, f, dv, ov, src, tier)

    # ---- 差异表落盘（两模式共用；--write 时写回底稿 crosscheck_conflicts 供报告附录引用）----
    if args.write:
        data["crosscheck_conflicts"] = conflicts
        json.dump(data, open(args.financials, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"\n  → 已写回底稿：crosscheck {len(cc)} 个年度，crosscheck_conflicts {len(conflicts)} 条")

    print("\n" + "=" * 70)
    print(f"结果：{errors} 错误 / {warns} 警告"
          + (f" / {auto} 项核对通过" if auto else ""))
    if conflicts:
        unresolved = [c for c in conflicts if not c["resolved"]]
        print(f"\n差异表（REQ-P0-07，{len(conflicts)} 条，其中 {len(unresolved)} 条未裁决）：")
        print(f"  {'年度':<6}{'科目':<18}{'底稿值':>14}{'官方值':>14}{'偏差':>8}  严重度  裁决")
        for c in conflicts:
            sev = {"block": "⛔阻断", "warn": "⚠告警", "register": "📝登记"}[c["severity"]]
            res = f"已豁免：{c['resolution'][:40]}" if c["resolved"] else \
                  f"以 {'官方' if c['adopted_side'] == 'crosscheck' else '底稿'}源为准（tier {min(c['annual_tier'], c['official_tier'])}）"
            print(f"  {c['year']:<6}{c['field']:<18}{str(c['annual_value']):>14}{str(c['official_value']):>14}"
                  f"{c['diff_pct']:>8.1%}  {sev}  {res}")
        print("  → 阻断项须以高优先级源为准更新底稿，或在 crosscheck_exempt 写五要素结构化豁免；"
              "差异表须出现在报告数据附录（verify_report 校验）。")
    if errors:
        print("命门科目核对未通过——禁止进入 Phase 2。"
              "确无法取得官方值时在底稿写 crosscheck_exempt 显式豁免并在报告披露。")
        return 1
    print("命门科目核对通过。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[异常] {exc}")
        sys.exit(2)
