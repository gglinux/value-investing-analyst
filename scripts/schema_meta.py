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
CURRENCIES = {"CNY", "USD", "HKD", "JPY", "EUR", "GBP", "TWD", "KRW", "SGD", "AUD", "CAD"}
BASIS = {"consolidated", "parent", "合并", "母公司"}
STANDARDS = {"CAS", "IFRS", "US-GAAP", "HKFRS", "JGAAP", "K-IFRS"}
PERIOD_TYPES = {"annual", "interim", "quarterly", "TTM", "年报", "半年报", "季报"}
# 复权口径：等比后复权是收益计算的唯一合法口径（OBS-000895-02 / OBS-2015-08-06）
ADJUSTED = {"hfq_ratio", "qfq_ratio", "qfq_arithmetic", "none", "raw"}
ADJUSTED_LEGAL_FOR_RETURN = {"hfq_ratio"}

# ── 字段规格 ────────────────────────────────────────────────────────
# (字段名, 是否核心, 校验函数, 说明)
class MetaSpec:
    CORE = ("unit", "currency", "data_vintage", "source_ref")
    EXTENDED = ("basis", "standard", "period_type")
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


def _check_source_ref(v):
    """source_ref：指向源文件与页码/行号。要求可定位，不接受泛指。"""
    if not isinstance(v, str) or len(v.strip()) < 6:
        return f"`source_ref` = {v!r} 过短——须可定位到源文件与页码/行号/表名"
    vague = ("网上", "查询所得", "公开资料", "数据商", "接口")
    if any(x in v for x in vague) and not re.search(r"(p\.?\s*\d+|第\s*\d+\s*页|#\d+|行\s*\d+|\w+\.(pdf|htm|html|xlsx|json))", v, re.I):
        return (f"`source_ref` = {v!r} 是泛指来源且无定位锚点——"
                "须含页码 / 行号 / 文件名之一")
    return None


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

    # 字段级例外：field_overrides 登记与文件默认口径不同的字段
    overrides = meta.get("field_overrides") or {}
    if not isinstance(overrides, dict):
        errors.append(tag + "schema：`meta.field_overrides` 应为对象（字段名 → 口径覆盖）")
    else:
        for fname, ov in overrides.items():
            if not isinstance(ov, dict):
                errors.append(tag + f"schema：`field_overrides.{fname}` 应为对象")
                continue
            unknown = sorted(set(ov) - set(MetaSpec.ALL) - {"note"})
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


def check_unit_sanity(data, path=""):
    """量纲哨兵的 schema 侧补充：声明单位与数值量级是否自洽。

    OBS-600660-01 的形态是市值传参与底稿单位错位 10 倍。

    **为什么不用「绝对区间」法**：最初实现取「收入换算成元后落在 1e7~1e14」，
    跨 7 个数量级，福耀百万→万元（100 倍错位）换算后仍在区间内，拦不住。
    公司规模本身横跨几个数量级，任何单一绝对区间要么漏放要么误伤。

    **改用底稿内部的独立锚**：`shares_diluted` 与 `revenue` 同在一份底稿里，
    但股本单位（股/万股/百万股）与金额单位相互独立，二者相除得到的**每股收入**
    落在一个远窄于绝对区间的范围。真实每股收入（本币）几乎总在 0.1~1000 之间——
    低于 0.1 或高于 1000 说明两个字段的单位声明至少有一个错了。
    净利率则提供第二个无量纲锚：它对单位错位天然免疫，一旦异常说明是
    **同一份底稿内**不同字段单位不一致（比命名约定能发现的错误更深一层）。
    """
    warns = []
    tag = f"[{os.path.basename(path)}] " if path else ""
    rows = sorted([r for r in (data.get("annual") or []) if isinstance(r, dict)],
                  key=lambda r: r.get("year", 0))
    if not rows:
        return warns
    last = rows[-1]
    rev, sh = last.get("revenue"), last.get("shares_diluted")
    ni = last.get("net_income")

    # 锚一：每股收入（金额单位 vs 股本单位的交叉校验）
    if all(isinstance(x, (int, float)) for x in (rev, sh)) and rev > 0 and sh > 0:
        rps = rev / sh
        if not (0.1 <= rps <= 1000):
            warns.append(
                f"{tag}量纲哨兵：最新年每股收入 = {rev:g}/{sh:g} = {rps:.4g}"
                f"（{data.get('currency', '?')}），落在合理区间 [0.1, 1000] 之外——"
                f"`revenue` 与 `shares_diluted` 的单位声明至少有一个错位")

    # 锚二：净利率（无量纲，对单位错位免疫；异常=同一底稿内字段单位不一致）
    if all(isinstance(x, (int, float)) for x in (rev, ni)) and rev > 0:
        margin = ni / rev
        if not (-2.0 <= margin <= 1.0):
            warns.append(
                f"{tag}量纲哨兵：最新年净利率 = {ni:g}/{rev:g} = {margin:.1%}，"
                f"超出 [-200%, 100%]——`net_income` 与 `revenue` 单位不一致")

    # 锚三：单位声明与自报市值的一致性（若底稿登记了市值）
    mc = data.get("market_cap") or (data.get("meta") or {}).get("market_cap")
    if isinstance(mc, (int, float)) and mc > 0 and isinstance(rev, (int, float)) and rev > 0:
        ps = mc / rev
        if not (0.05 <= ps <= 100):
            warns.append(
                f"{tag}量纲哨兵：市销率 = {mc:g}/{rev:g} = {ps:.3g}，"
                f"超出 [0.05, 100]——`market_cap` 与 `revenue` 可能不同单位")

    # 锚四：资产周转率（收入/总资产）——不依赖股本字段，覆盖锚一失效的底稿
    # （竞对底稿常无 shares_diluted）。实业公司该比值几乎总在 0.01~10：
    # 低于 0.01 = 收入单位偏小或资产偏大，高于 10 = 反之。金融类除外
    # （银行资产周转率天然极低），故按 company_type 跳过。
    ta = last.get("total_assets")
    ctype = str(data.get("company_type") or "").strip().lower()
    FIN = {"bank", "银行", "insurance", "保险", "保险集团", "寿险", "财险",
           "broker", "券商", "securities", "金融", "financial"}
    if (ctype not in FIN and isinstance(ta, (int, float)) and ta > 0
            and isinstance(rev, (int, float)) and rev > 0):
        turnover = rev / ta
        if not (0.01 <= turnover <= 10):
            warns.append(
                f"{tag}量纲哨兵：资产周转率 = {rev:g}/{ta:g} = {turnover:.3g}，"
                f"超出 [0.01, 10]——`revenue` 与 `total_assets` 单位可能错位")
    return warns


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
        errs, _ = validate_meta(d, f)
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
    errors, warns = validate_meta(data, args.input)
    warns += check_unit_sanity(data, args.input)
    print(f"schema 校验（强度 {_strength(data)}）："
          f"{'失败' if errors else '通过'}（错误 {len(errors)} / 警告 {len(warns)}）")
    for e in errors:
        print("  [ERROR]", e)
    for w in warns:
        print("  [WARN] ", w)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
