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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edgar_facts import annual_series  # noqa: E402  # P0-3 统一内核

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
    "edgar_xbrl": 1, "cninfo_pdf": 1, "hkex_pdf": 1,   # 监管官方原文（美/A股/港股）
    "edinet_pdf": 1,                                      # 日股：EDINET 有价证券報告書/決算短信
    "company_ir": 2,                                      # 公司官网原文
    "westock": 3, "ifind": 3,                             # A 级接口
    "research_report": 4, "wind_screenshot": 4,           # B 级二手
    "web_search": 5, "media": 5,                          # C 级兜底
}
# 自由文本 source → tier 的关键词推断（crosscheck.source 是人写的描述，没有 tier 字段时用）
#
# P0-1（2026-09-17）结构性重写：原实现从 tier1 起「首次命中即返回」，而「年报/
# 年度报告」在任何二手描述里都必然出现——媒体报道的本来就是年报数据，等于通配符，
# tier3/4 的 ifind/研报/券商关键词永远抢不到（实测 13 个转引变体 9 个误判 tier1，
# 「ifind接口返回的年报数据」「券商研报整理的年报数据」「百度搜索到的年报数据」
# 全被判官方源）。修法（三层判定，配合下方 veto 结构）：
#   ① 强锚词（监管渠道/申报凭证：10-K/EDGAR/cninfo/上交所…）命中且无 veto → 锁 tier1；
#   ② 弱官方词（年报/业绩公告/对比栏…仅说明被引对象是官方披露，不证明取数渠道）
#     与渠道词（官网/接口/研报/搜索…）同时在场 → 取全部命中档位的最保守档（数字最大）；
#   ③ 无任何命中 → 5（推不出记 5，逼执行者写清出处）。
_TIER_HINTS = [
    (1, ("10-k", "10k", "20-f", "20f", "edgar", "xbrl", "companyfacts", "巨潮", "cninfo",
         "披露易", "hkex", "年报", "年度报告", "审计报告", "annual report", "sec ", "决算短信",
         "tanshin", "业绩公告", "季报", "半年报", "主要会计数据", "对比栏", "对照列", "官方原文",
         "finalpage", "accession", "filed ",
         # 报表体裁词（REQ-P0-09 补）：TSM「审计报表」原命中不到任何档、走「推不出记 5」
         # 兜底，把经审计原文误当兜底源。体裁词只在官方披露文件里出现。
         "审计报表", "经审核", "合并利润表", "合并资产负债表", "合并现金流量表",
         "综合损益表", "綜合損益表", "业绩快报", "内含价值", "有价证券报告书")),
    (2, ("官网", "投资者关系", "ir.", "investor", "股东信", "shareholder letter", "公告原文",
         "fuyaogroup", "港版")),
    (3, ("westock", "ifind", "wind", "choice", "东方财富", "接口", "api", "tushare", "行情终端")),
    (4, ("研报", "券商", "research", "截图", "screenshot", "wind 截图")),
    (5, ("搜索", "web", "媒体", "新闻", "media", "news", "百度", "google", "新浪", "转引")),
]
# 强锚词：出现即证明取数经过监管渠道/申报凭证（命中且无 veto → 锁 tier1）。
# 与 _TIER_HINTS[0] 的差别：把「任何二手描述里也必然出现」的弱官方词（年报/业绩公告/
# 体裁词等）排除出去——它们只说明被引对象，不说明取数渠道。
_TIER1_ANCHORS = ("10-k", "10k", "20-f", "20f", "edgar", "xbrl", "companyfacts",
                  "cninfo", "巨潮", "披露易", "hkex", "edinet", "决算短信", "tanshin",
                  "finalpage", "accession", "sec ", "sec.gov", "filed ",
                  "上交所", "深交所", "sse.com", "szse.com")
# 弱官方词 = _TIER_HINTS[0] 去掉强锚与体裁词后的剩余（年报/年度报告/审计报告/
# annual report/业绩公告/季报/半年报/主要会计数据/对比栏/对照列/官方原文/业绩快报）。

# ── 转引否决词（REQ-P0-09：源分级的乐观偏置修复）──
# 原实现从 tier1 起「首次命中即返回」，而「年报/年度报告」这类词在**任何**转引描述里
# 都必然出现——媒体报道的本来就是年报数据。后果是越低质量的源越容易被判成最高等级：
#   实测「新浪财经转引年报数据」→ tier 1（命中「年报」），而既有测试用不含官方词的
#   短串「新浪转引」→ tier 5，恰好绕过了这个缺陷，使其长期不可见。
# 修法不是删官方词（会误伤「2014年报对比栏官方原文」这类真官方源），而是让**转引结构**
# 优先于**被转引对象**：出现下列词说明该描述的取数动作经过了第三方之手。
# 边界（刻意窄，避免假警报）：
#   ① 只认转引动作词，不认媒体机构名——「hkexnews」含 news 子串却是港交所官方站，
#      「新浪转引值经查与官方不符，弃用并记录差异」是正确的排除记录而非引用；
#   ② 描述里若同时出现官方原文指针（[E:] 指向 cninfo/hkexnews/sec 等），说明官方源是
#      主源、二手仅作交叉验证，不降级——「官方原文为主要源；另有行情终端为第二独立源」
#      是双源交叉的优良实践，降级它等于惩罚做了额外验证的人。
_RESTATED_MARKERS = ("转引", "转载", "援引", "引自", "摘自", "报道：", "报道:",
                     "报道（", "报道(", "报道了", "报道的", "年报报道")
_OFFICIAL_PTR = ("cninfo.com.cn", "hkexnews", "www1.hkexnews", "sec.gov", "static.cninfo",
                 "finalpage", "accession",
                 # P0-1 补：交易所披露渠道名同样是「官方原文在场」的凭证——神华 2014
                 # 实证「上交所2015-03-21披露」是与「中证网报道」并存的官方主源，
                 # 旧词表只认监管站点域名漏了它，导致该条目被标 media。
                 "上交所", "深交所", "sse.com", "szse.com")
# 弃用语义：描述里明说该二手值已被查证否决/未采纳——这是主动排除，不是依赖。
# 000898 实证：「新浪vFD转引值1,453经查与官方不符，弃用并记录差异」是优良实践，
# 降级它等于惩罚做了额外验证的人。
_REJECTED_MARKERS = ("弃用", "不符", "未采纳", "已排除", "剔除", "废弃", "存疑弃")
# 报表体裁词：说明引用体裁，但不证明取数渠道。孤证出现时不足以判定官方源。
_REPORT_GENRE = ("审计报表", "经审核", "合并利润表", "合并资产负债表", "合并现金流量表",
                 "综合损益表", "綜合損益表", "内含价值", "有价证券报告书")
_REPORT_GENRE_LOW = tuple(g.lower() for g in _REPORT_GENRE)


def _has_official_pointer(low: str) -> bool:
    """描述内是否含指向监管官方原文的 URL/凭证——有则二手词只是交叉验证的补充源。"""
    return any(p in low for p in _OFFICIAL_PTR)


def source_tier(entry_or_text) -> int:
    """crosscheck 条目（或 source 文本）→ 源优先级 tier。显式 `source_tier` 优先，
    其次按关键词推断；推不出记 5（最低），逼执行者写清出处。

    判定顺序（P0-1 结构重写，2026-09-17）：
      ① 转引结构否决优先于官方词（REQ-P0-09）：描述含转引动作且无官方原文
         指针/弃用词时，无论是否提到「年报」，一律按二手源（tier 5）计；
      ② 强锚词（监管渠道/申报凭证）在场 → 锁 tier1，弱词与渠道词不再争抢；
      ③ 否则扫描全部档位、命中取**最保守**（数字最大）——弱官方词（年报/业绩公告）
         只说明被引对象是官方披露，遇到「接口/研报/搜索」等渠道词时让位。
    """
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
    # 转引结构否决：先判「怎么拿到的」，再判「拿的是什么」
    if (any(m in text for m in _RESTATED_MARKERS)
            and not _has_official_pointer(low)
            and not any(r in text for r in _REJECTED_MARKERS)):
        return 5
    # 强锚词：监管渠道/申报凭证在场 → 锁 tier1（hkexnews 含 news 子串也在此拦截，
    # 不会被 tier5 的「新闻」误抢）
    if any(a in low for a in _TIER1_ANCHORS):
        return 1
    # 主源声明豁免：官方词在场 + 「为主要源」的双源交叉结构——官方源是主源、
    # 二手仅作交叉验证（茅台实证：「官方原文…为主要源；另有行情终端数据为第二
    # 独立源」是优良实践，降级它等于惩罚做了额外验证的人）。
    weak_hit = any(h in low for h in _TIER_HINTS[0][1] if h not in _REPORT_GENRE_LOW)
    if weak_hit and ("为主要源" in text or "主要来源为" in text):
        return 1
    # 全档位扫描取最保守：弱官方词只贡献 tier1 候选，渠道词（官网/接口/研报/搜索）
    # 与体裁词各贡献其档位，最终取数字最大——「ifind接口返回的年报数据」命中
    # {1弱, 3} → 3；「百度搜索到的年报数据」命中 {1弱, 5} → 5。
    hits = set()
    if weak_hit:
        hits.add(1)
    for tier, hints in _TIER_HINTS:
        if tier == 1:
            continue  # tier1 候选已由 weak_hit 贡献（体裁词除外——孤证不升官方）
        if any(h in low for h in hints):
            hits.add(tier)
    return max(hits) if hits else 5


def is_official_source(src) -> bool:
    """source 文本是否指向官方披露原文（统一内核，P0-1）。

    语义 = source_tier(text) <= 2：tier1 监管原文（10-K/巨潮/披露易/上交所…）
    + tier2 公司 IR 原文（官网/投资者关系）。此前 validate_data /
    verification_strength 各自携带第三份 OFFICIAL_SOURCE_HINTS 词表副本、
    无转引否决（「新浪财经转引年报数据」因含「年报」被判官方源，A2 哨兵
    被媒体转引穿透），本函数是唯一实现，两个模块均 import 此处。

    语义变化声明（vs 旧 validate_data 词表）：体裁词孤证（「合并利润表」无
    渠道凭证）不再判官方——TSM「审计报表」悬空指针实证那是自欺；转引结构
    （无官方指针/弃用词）一律非官方。
    """
    return source_tier(str(src or "")) <= 2


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


def edgar_annual(cf: dict, taxonomy: str, field: str, scale: float,
                 prefer: str = "latest", cutoff: str | None = None):
    """从 companyfacts 抽某科目的年度值（P0-3 起转发统一内核 edgar_facts）。

    筛选五件套（form 白名单/期间 300-400/end 归年/单位剔除/filed 口径）单点
    定义在 edgar_facts.annual_series——此前本函数「首见即取」与抽取器
    「最新 filed 优先」五处口径分裂，同一份 companyfacts 两个答案（AST-005：
    同一源两个算法不等于独立双源）。现两者共用 latest 口径；RESTATED 审计
    可传 prefer="first" 取原始申报值。

    按 `end` 日期归年（不用 fy 标签——TSM 实证）；逐年独立尝试全部候选概念
    （GOOG 实证：概念标签中途切换）。单位缩放：金额与股本用同一 scale
    （百万口径），此前误将 shares 视为无需缩放导致三年全部误报 100% 偏差，
    反而掩盖真正写错的那一年。
    """
    series = annual_series(cf, taxonomy, field, prefer=prefer, cutoff=cutoff,
                           concepts=CONCEPTS.get(field, []))
    return {y: (float(val) / scale, concept)
            for y, (val, concept, _filed) in series.items()}


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
            # 显式 source_tier 独立校验（OBS-META-12：显式值短路必须被独立校验，
            # 否则写错的标签经短路返回后永远无人复核——30 条错标实证）。
            # 只在显式值与文本推断**冲突**时报错：推断更保守（数字更大）说明
            # 标签高估了源等级；推断更乐观不报错（词表覆盖有限，推断偏 1 未必是错）。
            st_explicit = entry.get("source_tier")
            tier_text = source_tier(src)
            if st_explicit in SOURCE_PRIORITY:
                st_num = SOURCE_PRIORITY[st_explicit]
                if st_num < tier_text:
                    print(f"  ❌ {y} 显式 source_tier={st_explicit}（{st_num}）高估了源等级："
                          f"source 文本推断为 tier {tier_text}——按文本修正标签或改写出处")
                    errors += 1
                elif st_num > tier_text:
                    print(f"  ⚠️  {y} 显式 source_tier={st_explicit}（{st_num}）比文本推断"
                          f"（tier {tier_text}）更保守，可接受")
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
