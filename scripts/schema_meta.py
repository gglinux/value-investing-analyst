#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schema_meta.py — 底稿元数据强类型化（REQ-P0-03）

## 为什么需要这个模块

底稿的数值字段长期靠 `_frac` / `_pct` 命名后缀约定单位，单位、币种、复权、
报表口径没有任何字段承载。已登记的三条观察全部是量纲错误：

- OBS-600660-01：福耀市值 566294 ↔ 56630（10 倍错位）、海控 2535137 ↔ 253514；
- S9b：股息率三口径（TTM 实施派发 / 年度方案 / 可持续预期）未区分；
- OBS-000895-02：东财 fqt=1 前复权为等差复权，序列比值 ≠ 总回报。

三条都发生在有经验的执行者手里，说明命名约定拦不住——它依赖写入者记得约定。
量纲错误是「精确的错误」的典型形态：公式全对，结论差一个数量级。

## 设计取舍：文件级声明 + 字段级例外

不给每个数值字段都套 `{value, unit, ...}` 对象。理由有三：

1. **底稿会膨胀 5-8 倍**，且 43 份存量底稿全部要重写，迁移本身就是新的错误源；
2. **同一份底稿内 99% 的字段共享同一口径**（同币种、同单位、同准则），
   逐字段重复声明是噪声，真正的信息量在**例外**上；
3. 已发生的三次量纲错误全部是**跨文件/跨口径**错位（市值传参 vs 底稿单位、
   复权渠道差异），不是同一文件内字段间的单位不一致。

所以采用：**文件级 `meta` 块声明默认口径 + `field_overrides` 登记例外**。
例外才是需要被机器盯住的东西。

## 三档强度（与数据分级同构，避免一刀切阻断存量）

- `strict`  ：全部必填字段缺失即 ERROR。新建底稿（含第三批起的回测案例）用。
- `standard`：核心四项（unit/currency/data_vintage/source_ref）ERROR，其余 WARN。
- `legacy`  ：全部 WARN。存量 43 份底稿的过渡档，迁移完成后废弃。

档位由底稿 `meta.schema_version` 决定：缺失 → legacy；`>=2` → strict。
这样迁移可以逐案例推进，不需要一次性改 43 份。

用法：
    from schema_meta import validate_meta, MetaSpec
    errors, warns = validate_meta(data, path="backtest/xxx/data/financials.json")

    python3 scripts/schema_meta.py <financials.json>      # 单文件体检
    python3 scripts/schema_meta.py --scan backtest cases  # 批量扫描迁移进度
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime

SCHEMA_VERSION_CURRENT = 2

# ── 受控词表 ────────────────────────────────────────────────────────
# 单位：写成「乘数」而非自由文本——下游要用它做换算，自由文本无法计算。
UNIT_MULTIPLIER = {
    "元": 1.0, "yuan": 1.0, "USD": 1.0, "dollar": 1.0,
    "千元": 1e3, "thousand": 1e3,
    "万元": 1e4,
    "百万": 1e6, "百万元": 1e6, "million": 1e6, "mn": 1e6,
    "亿元": 1e8, "亿": 1e8,
    "十亿": 1e9, "billion": 1e9, "bn": 1e9,
}
# 股本单位：与金额单位相互独立，是量纲哨兵锚一的分母。存量底稿里
# 「金额百万 + 股本百万股」「金额元 + 股本万股」并存，不声明就无法换算。
SHARES_UNIT_MULTIPLIER = {
    "股": 1.0, "shares": 1.0,
    "千股": 1e3, "thousand_shares": 1e3,
    "万股": 1e4,
    "百万股": 1e6, "million_shares": 1e6,
    "亿股": 1e8,
}
# 每股收入上界（本币/股），供量纲哨兵锚一使用。按币种面值量级分档。
RPS_UPPER_BY_CURRENCY = {
    "CNY": 1e3, "HKD": 1e3, "TWD": 1e4, "SGD": 1e3, "AUD": 1e3, "CAD": 1e3,
    "USD": 1e4, "EUR": 1e4, "GBP": 1e4,
    "JPY": 1e5, "KRW": 1e6,
}
CURRENCIES = {"CNY", "USD", "HKD", "JPY", "EUR", "GBP", "TWD", "KRW", "SGD", "AUD", "CAD"}
BASIS = {"consolidated", "parent", "合并", "母公司"}
STANDARDS = {"CAS", "IFRS", "US-GAAP", "HKFRS", "JGAAP", "K-IFRS"}
PERIOD_TYPES = {"annual", "interim", "quarterly", "TTM", "年报", "半年报", "季报"}
# 复权口径：等比后复权是收益计算的唯一合法口径（OBS-000895-02 / OBS-2015-08-06）
ADJUSTED = {"hfq_ratio", "qfq_ratio", "qfq_arithmetic", "none", "raw"}
ADJUSTED_LEGAL_FOR_RETURN = {"hfq_ratio"}

# ── 非财务证据区块（P1-8 MVP，REQ-P3-05 最小子集）──────────────────
# 护城河判断的一半证据来自非财务数据（市场份额/行业基率/竞争格局），此前
# 完全依赖搜索且不留引用——「这部分完全不可复核」是 fable 1.1 节的自陈，
# 全 scripts/ grep retrieved_at 仅命中注释文案，零结构化承载。
# MVP 设计（三条铁律）：
#   1. 字段最小化：statement（断言）+ url（出处）+ retrieved_at（检索日期）
#      三个必填，fallback_action（降级路径）一个条件必填——多了没人填；
#   2. 结构即证据：断言与出处同格存放，报告引用 [N:] 指针即可定位，
#      不依赖执行者记得在文档里补脚注；
#   3. 降级路径是数据源问题不是文档问题：url 失效后怎么办必须登记
#      （re-verify 重检 / substitute 替代源 / degrade-and-disclose 降级披露），
#      这正是 check_data_sources 四层探测在底稿层的镜像。
# 严重度设计：主底稿（非 is_peer）缺区块 → WARN（MVP 阶段不阻断，逼登记
# 习惯先建立）；登记了但字段不合规 → ERROR（登记了就不许填错——半吊子
# 登记比不登记更有害，它制造「已溯源」的假象，与 [E:] 悬空指针同型）。
NONFIN_EVIDENCE_REQUIRED = ("statement", "url", "retrieved_at")
NONFIN_FALLBACK_ACTIONS = {"re-verify", "substitute", "degrade-and-disclose"}


def _check_url(v):
    if not isinstance(v, str) or not re.match(r"https?://\S+$", v.strip()):
        return f"`url` = {v!r} 须为 http(s):// 开头且非空——非财务证据必须给出可核验出处"
    return None


def _check_retrieved_at(v):
    if not isinstance(v, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(v).strip()):
        return (f"`retrieved_at` = {v!r} 应为 YYYY-MM-DD（检索日期）——"
                "无检索日期的引用无法判断证据时效")
    try:
        datetime.strptime(str(v).strip(), "%Y-%m-%d")
    except ValueError:
        return f"`retrieved_at` = {v!r} 不是合法日期"
    return None


def _check_fallback_action(v):
    if str(v) not in NONFIN_FALLBACK_ACTIONS:
        return (f"`fallback_action` = {v!r} 应为 {sorted(NONFIN_FALLBACK_ACTIONS)} 之一"
                "（url 失效后的处置路径，P1-8 必填）")
    return None


def check_nonfinancial_evidence(data, path=""):
    """非财务证据区块校验（P1-8 MVP，REQ-P3-05 最小子集）。

    区块格式（底稿顶层可选键）：
      "nonfinancial_evidence": [
        {"statement": "中国平安寿险市占率约 14%，行业第二",
         "url": "https://www.iachina.cn/...",
         "retrieved_at": "2026-09-01",
         "fallback_action": "re-verify",
         "confidence": "medium",          # 可选：high/medium/low
         "note": "行业协会年度数据"}, ...]

    规则：
      - 主底稿缺区块 → WARN（MVP 不阻断）；
      - 有区块但条目缺必填（statement/url/retrieved_at）→ ERROR；
      - url 为搜索结果页/门户首页（无定位锚）→ WARN 逼深链；
      - retrieved_at 距今 >400 天 → WARN 证据时效（非财务数据换代快）；
      - fallback_action 缺失 → ERROR（MVP 的第四字段，url 会死链是常态，
        「登记了引用却没有失效预案」正是 [E:] 悬空指针的镜像问题）。
    """
    errors, warns = [], []
    tag = f"[{os.path.basename(path)}] " if path else ""
    ev = data.get("nonfinancial_evidence")
    is_peer = bool(data.get("is_peer"))
    if ev is None:
        if not is_peer:
            warns.append(
                tag + "非财务溯源（P1-8）：缺 `nonfinancial_evidence` 区块——护城河/"
                "竞争格局判断若引用了市场份额/行业基率等非财务数据，须逐条登记 "
                "{statement, url, retrieved_at, fallback_action}（MVP 三+一字段，"
                "见 references/data-sourcing.md 第七节）")
        return errors, warns
    if not isinstance(ev, list):
        errors.append(tag + "非财务溯源：`nonfinancial_evidence` 应为数组（逐条证据）")
        return errors, warns
    today = datetime.now().strftime("%Y-%m-%d")
    vague_url_hints = ("google.com/search", "baidu.com/s?", "bing.com/search",
                       "/search?", "q=")
    for i, e in enumerate(ev, 1):
        if not isinstance(e, dict):
            errors.append(tag + f"非财务溯源：第 {i} 条应为对象")
            continue
        for k in NONFIN_EVIDENCE_REQUIRED:
            if not str(e.get(k) or "").strip():
                errors.append(tag + f"非财务溯源：第 {i} 条缺 `{k}`"
                              + {"statement": "（证据断言本身）",
                                 "url": "（可核验出处——搜索可得不等于可复核）",
                                 "retrieved_at": "（检索日期，判断时效）"}[k])
        if e.get("url") and _check_url(e.get("url")):
            errors.append(tag + f"非财务溯源：第 {i} 条 " + _check_url(e.get("url")))
        if e.get("retrieved_at"):
            ra = _check_retrieved_at(e.get("retrieved_at"))
            if ra:
                errors.append(tag + f"非财务溯源：第 {i} 条 " + ra)
            elif str(e.get("retrieved_at")).strip() < "2015-01-01" or \
                    str(e.get("retrieved_at")).strip() > today:
                errors.append(tag + f"非财务溯源：第 {i} 条 retrieved_at "
                                    f"{e.get('retrieved_at')!r} 超出合理区间"
                                    f"（2015-01-01 ~ {today}）")
            elif (datetime.now() - datetime.strptime(str(e.get("retrieved_at")).strip(),
                                                     "%Y-%m-%d")).days > 400:
                warns.append(tag + f"非财务溯源：第 {i} 条 retrieved_at "
                                   f"{e.get('retrieved_at')} 距今超 400 天——"
                                   "非财务数据换代快，建议复核是否仍成立")
        if not str(e.get("fallback_action") or "").strip():
            errors.append(tag + f"非财务溯源：第 {i} 条缺 `fallback_action`"
                          "（url 失效后的处置：re-verify/substitute/degrade-and-disclose）"
                          "——引用会死链是常态，无预案的引用是 [E:] 悬空指针的镜像")
        elif _check_fallback_action(e.get("fallback_action")):
            errors.append(tag + f"非财务溯源：第 {i} 条 "
                          + _check_fallback_action(e.get("fallback_action")))
        if isinstance(e.get("url"), str) and any(h in e["url"] for h in vague_url_hints):
            warns.append(tag + f"非财务溯源：第 {i} 条 url 为搜索结果页而非内容页——"
                               "请替换为最终出处深链（搜索页会随时间失效且不可核验）")
        cf = e.get("confidence")
        if cf is not None and str(cf) not in ("high", "medium", "low"):
            warns.append(tag + f"非财务溯源：第 {i} 条 confidence={cf!r} "
                               "应为 high/medium/low（可选字段）")
    return errors, warns

# ── 字段规格 ────────────────────────────────────────────────────────
# (字段名, 是否核心, 校验函数, 说明)
class MetaSpec:
    CORE = ("unit", "currency", "data_vintage", "source_ref")
    EXTENDED = ("basis", "standard", "period_type")
    # 可选声明：股本单位与每股字段单位。它们是「普遍例外」而非个案——
    # 福耀「百万元（除每股数据）」、日本底稿「股本千股」、平安「金额元 + 股本万股」。
    # 不声明时按 unit 的默认推断（股本=百万股 / 每股=元），但哨兵锚一会按
    # 声明值换算，声明错误会被锚一直接抓出。
    OPTIONAL = ("shares_unit", "per_share_unit")
    # `adjusted`（复权口径）只对**含价格序列**的底稿有意义——财务报表没有复权
    # 概念，对财务底稿强制它会制造一堆填 "none" 的噪声字段，稀释真正的信号。
    # 触发条件：底稿含 price/quote/ohlc 类区块，或声明 used_for_return_calc。
    PRICE_ONLY = ("adjusted",)
    ALL = CORE + EXTENDED


PRICE_BLOCK_KEYS = ("prices", "price_series", "quotes", "ohlc",
                    "monthly_prices", "price_basis")


def has_price_series(data):
    """底稿是否含价格序列——决定 `adjusted` 是否必填。"""
    if any(k in data for k in PRICE_BLOCK_KEYS):
        return True
    return bool((data.get("meta") or {}).get("used_for_return_calc"))


def _check_unit(v):
    if v in UNIT_MULTIPLIER:
        return None
    return (f"`unit` = {v!r} 不在受控词表——必须可换算为乘数，"
            f"合法值：{sorted(set(UNIT_MULTIPLIER))}")


def _check_currency(v):
    if str(v).upper() in CURRENCIES:
        return None
    return f"`currency` = {v!r} 不是 ISO 4217 三字母码（{sorted(CURRENCIES)}）"


def _check_vintage(v):
    """data_vintage：数据可得日期。REQ-P0-06 时点校验的输入。"""
    if not isinstance(v, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        return f"`data_vintage` = {v!r} 格式应为 YYYY-MM-DD（数据可得日期）"
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        return f"`data_vintage` = {v!r} 不是合法日期"
    return None


_ANCHOR_RE = re.compile(
    r"(p\.?\s*\d+|第\s*\d+\s*页|#\d+|行\s*\d+|\w+\.(pdf|htm|html|xlsx|json|csv)"
    r"|https?://|accession|\d{10}-\d{2}-\d{6}|附注\s*\d+|note\s*\d+|表\s*\d+"
    r"|ltn\d{8,}|\[E:[^\]]+\])", re.I)
_FILING_RE = re.compile(
    r"(年报|年度报告|半年报|中报|季报|业绩公告|招股|10-K|10-Q|20-F|6-K|8-K|S-1"
    r"|annual report|form\s*\d|有価証券報告書|사업보고서)", re.I)
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def has_locator(v):
    """source_ref 是否可定位。

    两类算可定位：① 硬锚点（页码/附注号/文件名/URL/accession/公告编号/[E:] 指针）；
    ② 命名了**具体申报文件**且带年份（如「2023 年报（披露易）」「20-F 2023」）——
    第二个人能据此在 1 分钟内打开同一份文件。
    被拒绝的是「公司年报数据」「公开资料」这类既无年份也无文件的声明。
    """
    if not isinstance(v, str):
        return False
    if _ANCHOR_RE.search(v):
        return True
    return bool(_FILING_RE.search(v) and _YEAR_RE.search(v))


def _check_source_ref(v):
    """source_ref：指向源文件与页码/行号。要求可定位，不接受泛指。

    基础校验（所有档位）：长度与泛指词。
    锚点校验（strict 档，在 validate_meta 中追加）：审查发现「公司年报数据」六个字
    就能过基础校验——这不是溯源，是声明「我看过」。strict 档必须含定位锚。
    """
    if not isinstance(v, str) or len(v.strip()) < 6:
        return f"`source_ref` = {v!r} 过短——须可定位到源文件与页码/行号/表名"
    vague = ("网上", "查询所得", "公开资料", "数据商", "接口")
    if any(x in v for x in vague) and not has_locator(v):
        return (f"`source_ref` = {v!r} 是泛指来源且无定位锚点——"
                "须含页码 / 行号 / 文件名之一")
    return None


def _check_shares_unit(v):
    if str(v) in SHARES_UNIT_MULTIPLIER:
        return None
    return f"`shares_unit` = {v!r} 应为 {sorted(SHARES_UNIT_MULTIPLIER)} 之一"


def _check_per_share_unit(v):
    if v in UNIT_MULTIPLIER:
        return None
    return f"`per_share_unit` = {v!r} 应为金额单位受控词之一（通常为 '元'/'USD'）"


def _check_basis(v):
    if str(v) in BASIS:
        return None
    return f"`basis` = {v!r} 应为 {sorted(BASIS)} 之一（合并/母公司口径）"


def _check_standard(v):
    if str(v).upper().replace("_", "-") in STANDARDS:
        return None
    return f"`standard` = {v!r} 应为 {sorted(STANDARDS)} 之一"


def _check_period_type(v):
    if str(v) in PERIOD_TYPES:
        return None
    return f"`period_type` = {v!r} 应为 {sorted(PERIOD_TYPES)} 之一"


def _check_adjusted(v):
    if str(v) in ADJUSTED:
        return None
    return (f"`adjusted` = {v!r} 应为 {sorted(ADJUSTED)} 之一——"
            "收益计算唯一合法口径为 hfq_ratio（等比后复权，OBS-000895-02）")


CHECKERS = {
    "unit": _check_unit, "currency": _check_currency,
    "data_vintage": _check_vintage, "source_ref": _check_source_ref,
    "basis": _check_basis, "standard": _check_standard,
    "period_type": _check_period_type, "adjusted": _check_adjusted,
    "shares_unit": _check_shares_unit, "per_share_unit": _check_per_share_unit,
}

# 旧头字段 → meta 字段的兼容映射（存量底稿已有这些顶层键）
LEGACY_ALIASES = {
    "unit": ("unit",),
    "currency": ("currency",),
    "standard": ("accounting_standard",),
}


def _strength(data):
    """由 meta.schema_version 决定校验强度。"""
    meta = data.get("meta") or {}
    try:
        ver = int(meta.get("schema_version") or 0)
    except (TypeError, ValueError):
        ver = 0
    if ver >= SCHEMA_VERSION_CURRENT:
        return "strict"
    if ver == 1:
        return "standard"
    return "legacy"


def resolve_meta(data):
    """合并 meta 块与旧顶层头字段，返回生效的元数据字典。

    优先级：meta 块 > 旧顶层头字段。这样迁移期两种写法并存不冲突，
    且迁移只需**新增** meta 块，不必删除旧字段（降低迁移风险）。
    """
    meta = dict(data.get("meta") or {})
    for field, aliases in LEGACY_ALIASES.items():
        if meta.get(field) is None:
            for a in aliases:
                if data.get(a) is not None:
                    meta[field] = data[a]
                    break
    return meta


# ── 双源一致性（三版审查修订 2026-09-11）────────────────────────────
# 迁移策略「只增不改」让顶层 unit/currency/accounting_standard 与 meta 块
# 长期并存。问题在于消费者不同：计算端（compute_metrics 的市值换算、
# validate_data 的口径注册表）读**顶层字段**，校验端（本模块）读 meta。
# 两者分歧时校验照过、计算静默错位——这正是 OBS-600660-01「两份声明
# 各自自洽、合起来才露馅」的新形态。矛盾比缺失更糟：缺失会触发补全
# 流程，矛盾会被两个消费者各取所需。故不受档位调制，无条件 ERROR。
def _norm_standard(v):
    return str(v).strip().upper().replace("_", "-").replace(" ", "-")


def top_meta_conflicts(data, meta):
    """顶层头字段与 meta 块的量纲/口径矛盾清单（空列表=无冲突）。

    同义写法放行：million ↔ 百万（乘数相等）、US GAAP ↔ US-GAAP；
    自由文本无法判定乘数/准则时跳过（不猜）。只拦「能确证的分歧」。
    """
    out = []
    tu, mu = data.get("unit"), meta.get("unit")
    if isinstance(tu, str) and isinstance(mu, str):
        tm, mm = UNIT_MULTIPLIER.get(tu.strip()), UNIT_MULTIPLIER.get(mu.strip())
        if tm is not None and mm is not None and tm != mm:
            out.append(f"顶层 `unit`={tu!r}（×{tm:g}）与 `meta.unit`={mu!r}（×{mm:g}）"
                       f"量纲分歧（{max(tm, mm) / min(tm, mm):g} 倍）——计算端读顶层、"
                       "校验端读 meta，两边各取所需即静默错位")
    tc, mc = data.get("currency"), meta.get("currency")
    if isinstance(tc, str) and isinstance(mc, str) and tc.strip().upper() != mc.strip().upper():
        out.append(f"顶层 `currency`={tc!r} 与 `meta.currency`={mc!r} 分歧——"
                   "快照币种链与计算端各取一侧")
    ts, ms = data.get("accounting_standard"), meta.get("standard")
    if isinstance(ts, str) and isinstance(ms, str):
        nts, nms = _norm_standard(ts), _norm_standard(ms)
        if nts in STANDARDS and nms in STANDARDS and nts != nms:
            out.append(f"顶层 `accounting_standard`={ts!r} 与 `meta.standard`={ms!r} 分歧")
    return out


def unit_multiplier(data):
    """底稿单位的数值乘数；无法判定返回 None（下游据此拒绝换算而非猜测）。"""
    return UNIT_MULTIPLIER.get(resolve_meta(data).get("unit"))


def validate_meta(data, path=""):
    """校验底稿元数据，返回 (errors, warns)。"""
    errors, warns = [], []
    strength = _strength(data)
    meta = resolve_meta(data)
    tag = f"[{os.path.basename(path)}] " if path else ""

    def _emit(msg, is_core):
        if strength == "strict":
            errors.append(tag + msg)
        elif strength == "standard" and is_core:
            errors.append(tag + msg)
        else:
            warns.append(tag + msg + f"（当前强度 {strength}）")

    for field in MetaSpec.ALL + (MetaSpec.PRICE_ONLY if has_price_series(data) else ()):
        is_core = field in MetaSpec.CORE
        v = meta.get(field)
        if v is None:
            _emit(f"schema：缺 `meta.{field}`——"
                  + {"unit": "单位无字段承载，量纲错误无法拦截",
                     "currency": "币种缺失，跨市场对比会静默混算",
                     "data_vintage": "数据可得日期缺失，时点校验（REQ-P0-06）无输入",
                     "source_ref": "无源定位，可溯源只是口号",
                     "basis": "合并/母公司口径缺失",
                     "standard": "会计准则缺失，跨准则可比性无声明",
                     "period_type": "报表期间类型缺失",
                     "adjusted": "复权口径缺失，收益计算可能用错复权方式"}[field],
                  is_core)
            continue
        err = CHECKERS[field](v)
        if err:
            _emit("schema：" + err, is_core)

    # 双源一致性：顶层头字段与 meta 块的矛盾（三版审查修订）。无条件 ERROR——
    # 见 top_meta_conflicts 文档：矛盾比缺失更糟，且不受档位调制。
    for msg in top_meta_conflicts(data, meta):
        errors.append(tag + "schema：" + msg)

    # strict 档追加：source_ref 必须含定位锚点。「公司年报数据」能过基础校验，
    # 但它只声明「我看过」，不能让第二个人在 30 秒内翻到同一个数。
    if strength == "strict" and meta.get("source_ref") and not has_locator(meta["source_ref"]):
        errors.append(tag + f"schema：strict 档 `source_ref` = {meta['source_ref']!r} 无定位锚点"
                            "——须含页码 / 附注号 / 文件名 / URL / accession 之一")

    # 可选字段：给了就必须合法（不给不报）
    for field in MetaSpec.OPTIONAL:
        v = meta.get(field)
        if v is not None:
            err = CHECKERS[field](v)
            if err:
                _emit("schema：" + err, False)

    # 字段级例外：field_overrides 登记与文件默认口径不同的字段
    _override_keys = set(MetaSpec.ALL) | set(MetaSpec.OPTIONAL) | set(MetaSpec.PRICE_ONLY) | {"note"}
    overrides = meta.get("field_overrides") or {}
    if not isinstance(overrides, dict):
        errors.append(tag + "schema：`meta.field_overrides` 应为对象（字段名 → 口径覆盖）")
    else:
        for fname, ov in overrides.items():
            if not isinstance(ov, dict):
                errors.append(tag + f"schema：`field_overrides.{fname}` 应为对象")
                continue
            unknown = sorted(set(ov) - _override_keys)
            if unknown:
                errors.append(tag + f"schema：`field_overrides.{fname}` 含未知键 {unknown}")
            for k, v in ov.items():
                if k in CHECKERS:
                    e = CHECKERS[k](v)
                    if e:
                        errors.append(tag + f"schema：`field_overrides.{fname}` " + e)
            if not ov.get("note"):
                warns.append(tag + f"schema：`field_overrides.{fname}` 缺 `note`——"
                                   "例外口径须写明为什么与文件默认不同")

    # 复权口径与用途的一致性：声明用于收益计算却非等比后复权，直接错误。
    adj = meta.get("adjusted")
    if adj and meta.get("used_for_return_calc") and adj not in ADJUSTED_LEGAL_FOR_RETURN:
        errors.append(tag + f"schema：`adjusted`={adj} 用于收益计算——"
                            "等差前复权的序列比值 ≠ 总回报（OBS-000895-02），"
                            "收益计算唯一合法口径为 hfq_ratio")
    return errors, warns


def _fin_type(data):
    ctype = str(data.get("company_type") or "").strip().lower()
    FIN = {"bank", "银行", "insurance", "保险", "保险集团", "寿险", "财险",
           "broker", "券商", "securities", "金融", "financial"}
    return ctype in FIN


def check_unit_sanity(data, path=""):
    """量纲哨兵的 schema 侧补充：声明单位与数值量级是否自洽。

    OBS-600660-01 的形态是市值传参与底稿单位错位 10 倍。

    **为什么不用「绝对区间」法**：最初实现取「收入换算成元后落在 1e7~1e14」，
    跨 7 个数量级，福耀百万→万元（100 倍错位）换算后仍在区间内，拦不住。
    公司规模本身横跨几个数量级，任何单一绝对区间要么漏放要么误伤。

    **改用底稿内部的独立锚**：`shares_diluted` 与 `revenue` 同在一份底稿里，
    但股本单位与金额单位相互独立，二者相除得到的**每股收入**落在一个远窄于
    绝对区间的范围。锚一先按 `meta.unit` 与 `meta.shares_unit` 换算成
    「每股本币」再判——不换算时 BRK.A 这类高价股会误报，声明单位错则被抓出。
    净利率提供无量纲锚（对单位错位免疫，异常=同一底稿内字段单位不一致）；
    资产周转率覆盖无股本字段的竞对底稿（金融类天然极低，按 company_type 跳过）。

    返回 warns 列表（向后兼容）。strict 档应把这些告警升级为 ERROR——
    用 `unit_sanity_as_errors(data)` 判断；审查发现平安底稿单位声明错却挂着
    strict 标签通过校验，原因正是哨兵只出 WARN 且迁移不跑哨兵。
    """
    warns = []
    tag = f"[{os.path.basename(path)}] " if path else ""
    rows = sorted([r for r in (data.get("annual") or []) if isinstance(r, dict)],
                  key=lambda r: r.get("year", 0))
    if not rows:
        return warns
    last = rows[-1]
    rev, sh = last.get("revenue"), last.get("shares_diluted")
    meta = resolve_meta(data)
    money_mult = UNIT_MULTIPLIER.get(meta.get("unit"))
    # 股本单位：未声明时按「与金额同级」的历史约定推断（百万 ↔ 百万股），
    # 这是存量 40 份 million 底稿的实际口径；声明了就用声明。
    shares_mult = SHARES_UNIT_MULTIPLIER.get(meta.get("shares_unit"))
    if shares_mult is None:
        shares_mult = money_mult
    cur = str(meta.get("currency") or "").upper()
    hi = RPS_UPPER_BY_CURRENCY.get(cur, 1e4)
    waiver = (data.get("meta") or {}).get("unit_sanity_waiver")

    # 锚一/锚二遍历全部年度行（三版审查修订 2026-09-11）：单位声明错位影响
    # **所有行**，只查最新年会漏两件事——① 历史某行数值本身错位（多打一个零）；
    # ② 系统性错位的定性证据：全部行越界 = 声明错（改 meta），个别行越界 =
    # 该行录入错（改数值），处置方式不同。命中按锚聚合为一条，附年份范围。
    bad_rps, bad_margin = [], []
    for r in rows:
        _rev, _sh, _ni = r.get("revenue"), r.get("shares_diluted"), r.get("net_income")
        if (all(isinstance(x, (int, float)) for x in (_rev, _sh)) and _rev > 0 and _sh > 0
                and money_mult and shares_mult and not waiver):
            rps = (_rev * money_mult) / (_sh * shares_mult)
            if not (0.1 <= rps <= hi):
                bad_rps.append((r.get("year"), rps, _rev, _sh))
        if all(isinstance(x, (int, float)) for x in (_rev, _ni)) and _rev > 0:
            margin = _ni / _rev
            if not (-2.0 <= margin <= 1.0):
                bad_margin.append((r.get("year"), margin))
    if bad_rps:
        _ys = [str(y) for y, *_ in bad_rps]
        span = _ys[0] if len(_ys) == 1 else f"{_ys[0]}–{_ys[-1]}（{len(_ys)}/{len(rows)} 年）"
        y_l, rps_l, rev_l, sh_l = bad_rps[-1]
        warns.append(
            f"{tag}量纲哨兵：每股收入越界 {span}，最新越界 {y_l} 年 = "
            f"{rev_l:g}×{money_mult:g} / {sh_l:g}×{shares_mult:g} = {rps_l:.4g}"
            f"（{cur or '?'}/股，界 [0.1, {hi:g}]）——`revenue` 与 `shares_diluted` 的"
            f"单位声明（unit={meta.get('unit')!r}, "
            f"shares_unit={meta.get('shares_unit') or '未声明，按与金额同级推断'}）至少有一个错位"
            f"（全部行越界=声明错位，个别行越界=该行录入错）；"
            f"确为高价股请在 meta.unit_sanity_waiver 写明理由")
    if bad_margin:
        _ys = [str(y) for y, _ in bad_margin]
        span = _ys[0] if len(_ys) == 1 else f"{_ys[0]}–{_ys[-1]}（{len(_ys)}/{len(rows)} 年）"
        y_l, m_l = bad_margin[-1]
        warns.append(
            f"{tag}量纲哨兵：净利率越界 {span}，最新越界 {y_l} 年 = {m_l:.1%}"
            f"（界 [-200%, 100%]）——`net_income` 与 `revenue` 单位不一致"
            f"（个别行越界=该行录入错，全部行越界=同底稿内字段单位不一致）")

    # 锚三：单位声明与自报市值的一致性（若底稿登记了市值）
    mc = data.get("market_cap") or (data.get("meta") or {}).get("market_cap")
    if isinstance(mc, (int, float)) and mc > 0 and isinstance(rev, (int, float)) and rev > 0:
        ps = mc / rev
        if not (0.05 <= ps <= 100):
            warns.append(
                f"{tag}量纲哨兵：市销率 = {mc:g}/{rev:g} = {ps:.3g}，"
                f"超出 [0.05, 100]——`market_cap` 与 `revenue` 可能不同单位")

    # 锚四：资产周转率（收入/总资产）——不依赖股本字段，覆盖锚一失效的底稿
    # （竞对底稿常无 shares_diluted）。实业公司该比值几乎总在 0.01~10；
    # 金融类天然极低，按 company_type 跳过。
    ta = last.get("total_assets")
    if (not _fin_type(data) and isinstance(ta, (int, float)) and ta > 0
            and isinstance(rev, (int, float)) and rev > 0):
        turnover = rev / ta
        if not (0.01 <= turnover <= 10):
            warns.append(
                f"{tag}量纲哨兵：资产周转率 = {rev:g}/{ta:g} = {turnover:.3g}，"
                f"超出 [0.01, 10]——`revenue` 与 `total_assets` 单位可能错位")
    return warns


def unit_sanity_as_errors(data):
    """strict 档下量纲哨兵告警是否应升级为 ERROR。

    只有 strict 才升级：legacy/standard 是过渡档，量纲告警仍是 WARN 让执行者
    看到；但一份挂着 strict 标签的底稿量纲不自洽，比没标签更危险——
    读者会信它。
    """
    return _strength(data) == "strict"


def validate_full(data, path=""):
    """validate_meta + 量纲哨兵 + 非财务溯源（P1-8）的组合入口，按档位决定哨兵告警的严重度。"""
    errors, warns = validate_meta(data, path)
    sanity = check_unit_sanity(data, path)
    if unit_sanity_as_errors(data):
        errors += [w + "（strict 档量纲不自洽升级为错误）" for w in sanity]
    else:
        warns += sanity
    nf_errs, nf_warns = check_nonfinancial_evidence(data, path)
    errors += nf_errs
    warns += nf_warns
    return errors, warns


# ---- 分层验收（REQ-P0-03，用户裁决 2026-09-14：主底稿 strict、竞对显式豁免）----
# 原验收条款「全部迁移并通过」对竞对底稿不可达：现行纪律竞对免双源/原文核对，
# source_ref 的可定位锚无从取得（数据商快照无页码无 URL），强行补 = 编造溯源。
# 但"无声 legacy"更危险——迁移停在半路的文件看起来和豁免文件一样。裁决口径：
#   主底稿必须 strict；竞对底稿要么补齐升 strict、要么带显式豁免块；
#   豁免必须可问责（裁决人 + 日期 + 覆盖字段），禁止静默 legacy。
SCHEMA_WAIVER_REQUIRED_KEYS = ("scope", "reason", "covers", "decided_by", "date")


def layered_acceptance(data):
    """REQ-P0-03 分层验收：返回 (errors, warns)。

    规则（schema_version < 2 即停留 legacy 时判定）：
      - 非竞对底稿（无 is_peer=true）停留 legacy → ERROR（主底稿必须 strict）；
      - 竞对底稿停留 legacy 无 schema_waiver → ERROR（禁止无声 legacy）；
      - schema_waiver 缺必填键（scope/reason/covers/decided_by/date）→ ERROR；
      - 合规豁免 → WARN（要求报告数据附录 validate-summary 段披露）。
    """
    errors, warns = [], []
    meta = data.get("meta") or {}
    if (meta.get("schema_version") or 0) >= 2:
        return errors, warns  # strict：分层规则不适用
    waiver = meta.get("schema_waiver")
    if not data.get("is_peer"):
        errors.append(
            "schema 分层验收（REQ-P0-03）：主底稿停留 legacy（meta.schema_version<2）——"
            "主底稿必须完整迁移升 strict；若确属竞对对照底稿，请显式登记 is_peer=true")
        return errors, warns
    if not isinstance(waiver, dict):
        errors.append(
            "schema 分层验收（REQ-P0-03）：竞对底稿停留 legacy 必须带显式豁免 "
            "meta.schema_waiver={scope,reason,covers,decided_by,date}——禁止无声 legacy"
            "（豁免含义：免原文核对、仅作定性对照、不进估值管线）")
        return errors, warns
    missing = [k for k in SCHEMA_WAIVER_REQUIRED_KEYS if not waiver.get(k)]
    if missing:
        errors.append(f"meta.schema_waiver 缺必填键 {missing}——豁免必须可问责"
                      "（谁裁决、何时、覆盖哪些字段、为什么）")
        return errors, warns
    warns.append("schema 豁免披露（REQ-P0-03）：竞对底稿 " + str(waiver.get("scope"))
                 + "——报告数据附录 validate-summary 段须披露该豁免")
    return errors, warns


def scan(roots):
    """批量扫描迁移进度。"""
    files = []
    for r in roots:
        files += sorted(glob.glob(os.path.join(r, "*", "data", "financials_*.json")))
    if not files:
        print("未找到底稿")
        return 0
    buckets = {"strict": [], "standard": [], "legacy": []}
    bad = []
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            bad.append((f, str(e)))
            continue
        s = _strength(d)
        errs, _ = validate_full(d, f)
        buckets[s].append((f, len(errs)))
    total = sum(len(v) for v in buckets.values())
    print(f"底稿 schema 迁移进度（共 {total} 份）")
    print(f"  strict   （schema_version>=2，全字段强制）：{len(buckets['strict'])}")
    print(f"  standard （schema_version=1，核心四项强制）：{len(buckets['standard'])}")
    print(f"  legacy   （无 meta 块，全部警告）        ：{len(buckets['legacy'])}")
    done = len(buckets["strict"]) + len(buckets["standard"])
    print(f"  迁移率：{done}/{total} = {done / total * 100:.0f}%")
    failing = [(f, n) for b in ("strict", "standard") for f, n in buckets[b] if n]
    if failing:
        print(f"\n已迁移但校验未过（{len(failing)} 份）：")
        for f, n in failing:
            print(f"  {n} 错误  {os.path.relpath(f)}")
    if bad:
        print(f"\n无法解析（{len(bad)} 份）：")
        for f, e in bad:
            print(f"  {os.path.relpath(f)}: {e[:80]}")
    return 1 if (failing or bad) else 0


def main():
    ap = argparse.ArgumentParser(description="底稿元数据 schema 校验（REQ-P0-03）")
    ap.add_argument("input", nargs="?", help="底稿 JSON 路径")
    ap.add_argument("--scan", nargs="+", metavar="DIR",
                    help="批量扫描目录（如 backtest cases），报告迁移进度")
    args = ap.parse_args()

    if args.scan:
        sys.exit(scan(args.scan))
    if not args.input:
        ap.print_help()
        sys.exit(0)

    data = json.load(open(args.input, encoding="utf-8"))
    errors, warns = validate_full(data, args.input)
    print(f"schema 校验（强度 {_strength(data)}）："
          f"{'失败' if errors else '通过'}（错误 {len(errors)} / 警告 {len(warns)}）")
    for e in errors:
        print("  [ERROR]", e)
    for w in warns:
        print("  [WARN] ", w)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
