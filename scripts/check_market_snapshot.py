#!/usr/bin/env python3
"""行情快照契约校验 —— market_snapshot.json 的统一 schema 与量纲三角校验。

## 为什么存在

第一批复核（SKILL_IMPROVEMENT_REPORT.md E1、REPORT_FACTCHECK_AND_ACTIONS.md）发现
估值输入层没有 schema：六个案例六套命名，且**单位塞在字段名里**——
`total_market_cap_cny_million`（百万）与 `total_market_cap_usd_yi`（亿）并存，
福耀案正是因此把"亿"当"百万"填出 10 倍错位（566294 应为 56630），
海控案同构错位（2535137 应为 253514）当时甚至未被发现。

`compute_metrics.py` 的 M_UNIT_SUSPECT 哨兵只能从**荒谬派生值**倒推单位错位
（OE 收益率 <1% / 回本 >50 年 / PB 越界），但如果错位倍数温和（如 2 倍），
派生值不会荒谬，哨兵抓不到。本脚本直接在**输入层**做三角校验：

    market_cap（换算到百万） ≈ price × shares（百万股），偏差 > 3% 即 FAIL

市值、股价、股本三个输入，任何一个量纲错都会打破这个等式。

## 规范 schema（v2.16 起，新案例必须遵守）

```json
{
  "price":      {"value": 22.78, "currency": "CNY"},
  "shares":     {"value": 2508.62, "unit": "million_shares"},
  "market_cap": {"value": 56630, "unit": "million", "currency": "CNY"},
  "fx":         {"quote_to_report": 0.8762}
}
```

单位必须是**独立字段**，禁止塞字段名。旧命名（price_cny / price_a /
total_shares_million / market_cap 为 dict 等）按已知映射兼容读取并记
`SNAPSHOT_LEGACY_SCHEMA` 告警——能读不等于合规，新案例禁止再产生旧命名。

## 用法

    python3 scripts/check_market_snapshot.py data/market_snapshot.json \
        [--report-currency CNY] [--fin data/financials_*.json]

退出码：0 通过；1 有错误。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

UNIT_TO_MILLION = {"million": 1.0, "百万": 1.0, "yi": 100.0, "亿": 100.0,
                   "亿元": 100.0, "yuan": 0.000001, "元": 0.000001}


def unit_to_million(unit):
    """宽松单位归一：精确匹配优先，前缀匹配兜底。

    底稿 unit 字段存在自由文本（如 "million USD（股本为 million shares）"、
    "百万元（除每股/每股本数据）"），精确匹配失败时按 million/亿 前缀识别；
    仍无法识别返回 None（调用方负责拒绝执行而非静默按百万算）。
    """
    if not isinstance(unit, str):
        return None
    u = unit.strip().lower()
    if u in UNIT_TO_MILLION:
        return UNIT_TO_MILLION[u]
    if u.startswith("million") or u.startswith("百万"):
        return 1.0
    if u.startswith("亿") or u.startswith("yi"):
        return 100.0
    return None

# 旧命名 -> 规范字段 的已知映射。每个 tuple = (规范字段, 旧字段名, 隐含单位说明)
# price_unadjusted 放最后：双汇式三价并列快照（unadjusted/forward/backward）
# 只有未复权价与快照日市值自洽（复权价是收益计算口径，不是市值口径）。
_PRICE_LEGACY = ["price", "price_cny", "price_usd", "price_hkd", "price_a",
                 "price_unadjusted"]
_SHARES_LEGACY = ["shares_outstanding", "total_shares_million"]  # 均为百万股
_MCAP_NUMERIC = ["market_cap", "total_market_cap_cny_million"]
_MCAP_YI = ["total_market_cap_usd_yi", "total_market_cap_cny_yi", "market_cap_yi"]  # 字段名明示单位为亿


def _err(errors, code, msg):
    errors.append((code, msg))


def _warn(warnings, code, msg):
    warnings.append((code, msg))


def read_price(d, warnings):
    for k in _PRICE_LEGACY:
        if isinstance(d.get(k), (int, float)):
            _warn(warnings, "SNAPSHOT_LEGACY_SCHEMA",
                  f"股价使用旧命名 `{k}`；规范为 price.value + price.currency")
            cur = (d.get("currency_quote") or d.get(f"{k}_currency")
                   or d.get("price_a_currency") or d.get("currency"))
            return float(d[k]), cur
    p = d.get("price")
    if isinstance(p, dict) and isinstance(p.get("value"), (int, float)):
        return float(p["value"]), p.get("currency")
    return None, None


def read_shares_million(d, warnings):
    """返回百万股数。"""
    s = d.get("shares")
    if isinstance(s, dict):
        if isinstance(s.get("value"), (int, float)):
            if s.get("unit") == "million_shares":
                return float(s["value"])
            _warn(warnings, "SNAPSHOT_SCHEMA",
                  "shares 为 dict 但缺 unit='million_shares'，无法确证单位")
        # 历史 dict 形态：{"total_million": ...} 或 {"total": ...}
        for kk in ("total_million", "total", "total_shares_million"):
            if isinstance(s.get(kk), (int, float)):
                _warn(warnings, "SNAPSHOT_LEGACY_SCHEMA",
                      f"shares 使用旧 dict 形态（{kk}）；规范为 shares.value + shares.unit")
                return float(s[kk])
        return None
    for k in _SHARES_LEGACY:
        if isinstance(d.get(k), (int, float)):
            v = float(d[k])
            # 量级自检：旧命名裸数字的股数单位无字段佐证（双汇案实证——
            # shares_outstanding=3,301,693,093 是原始股数，非百万股）。
            # 上市公司总股本（原始计数）必然 ≥10^6；而以百万计的股本
            # >10^6 意味着 10^12 股（无真实案例）。>10^6 按原始股数归一。
            if v > 1e6:
                _warn(warnings, "SNAPSHOT_LEGACY_SCHEMA",
                      f"股本旧命名 `{k}`={v:,.0f} 量级为原始股数（非百万股），"
                      f"已自动 ÷10^6 归一；规范为 shares.value + shares.unit")
                v = v / 1e6
            else:
                _warn(warnings, "SNAPSHOT_LEGACY_SCHEMA",
                      f"股本使用旧命名 `{k}`（百万股）；规范为 shares.value + shares.unit")
            return v
    return None


def read_market_cap_million(d, warnings):
    """返回百万为单位的市值，或 None。"""
    m = d.get("market_cap")
    if isinstance(m, dict):
        if isinstance(m.get("value"), (int, float)):
            unit = m.get("unit")
            f = unit_to_million(unit)
            if f:
                return float(m["value"]) * f, m.get("currency")
            _warn(warnings, "SNAPSHOT_SCHEMA",
                  f"market_cap.unit={unit!r} 未知，三角校验按百万解读")
            return float(m["value"]), m.get("currency")
        if isinstance(m.get("value_cny_million"), (int, float)):
            _warn(warnings, "SNAPSHOT_LEGACY_SCHEMA",
                  "market_cap 为 dict 且用 value_cny_million；规范为 market_cap.value + unit")
            return float(m["value_cny_million"]), "CNY"
        return None, None
    if isinstance(m, (int, float)):
        _warn(warnings, "SNAPSHOT_LEGACY_SCHEMA",
              "market_cap 为裸数字且无单位字段；规范为 market_cap.value + unit + currency")
        return float(m), d.get("currency")
    for k in _MCAP_NUMERIC[1:]:
        if isinstance(d.get(k), (int, float)):
            _warn(warnings, "SNAPSHOT_LEGACY_SCHEMA",
                  f"市值单位塞在字段名 `{k}` 里（百万）；单位必须是独立字段")
            return float(d[k]), "CNY"
    for k in _MCAP_YI:
        if isinstance(d.get(k), (int, float)):
            _warn(warnings, "SNAPSHOT_LEGACY_SCHEMA",
                  f"市值单位塞在字段名 `{k}` 里（亿）；单位必须是独立字段")
            return float(d[k]) * 100.0, "USD" if "usd" in k else "CNY"
    return None, None


def _dual_listed(d):
    """识别 A/H 双重计价快照并计算分计价市值（百万）。

    返回 (implied_million, formula_text)；不构成该形态返回 None。
    单以 A 价 × 总股本算这类公司会把 H 股也按 A 价计——系统性高估（海控案差 11.5%）。
    """
    pa, ph = d.get("price_a"), d.get("price_h")
    sh = d.get("shares")
    fx = d.get("fx") or {}
    if not (isinstance(pa, (int, float)) and isinstance(ph, (int, float))
            and isinstance(sh, dict)):
        return None
    a_m, h_m = sh.get("a_million"), sh.get("h_million")
    fxr = fx.get("cny_per_hkd") or fx.get("quote_to_report")
    if not (isinstance(a_m, (int, float)) and isinstance(h_m, (int, float))
            and isinstance(fxr, (int, float))):
        return None
    implied = pa * a_m + ph * h_m * fxr
    formula = f"{pa}×{a_m:,.0f} + {ph}×{h_m:,.0f}×{fxr}"
    return implied, formula


def check(fp, report_currency=None):
    with open(fp, "r", encoding="utf-8") as f:
        d = json.load(f)
    errors, warnings = [], []

    price, price_cur = read_price(d, warnings)
    shares_m = read_shares_million(d, warnings)
    mcap_m, mcap_cur = read_market_cap_million(d, warnings)

    if price is None:
        _err(errors, "SNAPSHOT_SCHEMA", "缺 price（value+currency）或可兼容的旧命名字段")
    if shares_m is None:
        _err(errors, "SNAPSHOT_SCHEMA", "缺 shares（value+unit='million_shares'）")
    if mcap_m is None:
        _err(errors, "SNAPSHOT_SCHEMA", "缺 market_cap（value+unit+currency）")

    # 三角校验：三者都在才比。A/H 双重上市用分计价口径
    # （price_a×A股 + price_h×H股×汇率），单一上市用 price×总股本。
    dual = _dual_listed(d)
    if dual:
        implied, formula = dual
        _warn(warnings, "SNAPSHOT_LEGACY_SCHEMA",
              "A/H 分计价快照使用旧命名；规范 schema 同样要求独立单位字段")
        if mcap_m:
            dev = implied / mcap_m - 1
            if abs(dev) > 0.03:
                _err(errors, "SNAPSHOT_TRIANGLE",
                     f"A/H 分计价三角不自洽：{formula} = {implied:,.0f} 百万，"
                     f"market_cap = {mcap_m:,.0f} 百万，偏差 {dev:+.1%} > 3%")
            else:
                print(f"  三角校验通过（A/H 分计价）：{formula} = {implied:,.0f} ≈ market_cap={mcap_m:,.0f} 百万（偏差 {dev:+.2%}）")
    elif price is not None and shares_m and mcap_m:
        implied = price * shares_m  # 价格×百万股 = 百万为单位的市值
        dev = implied / mcap_m - 1
        if abs(dev) > 0.03:
            _err(errors, "SNAPSHOT_TRIANGLE",
                 f"市值三角不自洽：price×shares = {implied:,.0f} 百万，"
                 f"market_cap = {mcap_m:,.0f} 百万，偏差 {dev:+.1%} > 3%。"
                 f"市值/股价/股本三者必有一个量纲错——"
                 f"最常见的是市值按亿填写但标注百万（福耀/海控两案实证）")
        else:
            print(f"  三角校验通过：price×shares={implied:,.0f} ≈ market_cap={mcap_m:,.0f} 百万（偏差 {dev:+.2%}）")

    # 币种链：报价币种 vs 报表币种
    report_cur = report_currency
    if report_cur is None:
        report_cur = d.get("currency_report")
    if price_cur and report_cur and price_cur != report_cur:
        fx = d.get("fx")
        fx_val = fx.get("quote_to_report") if isinstance(fx, dict) else None
        if fx_val is None:
            _err(errors, "SNAPSHOT_FX_MISSING",
                 f"报价币种 {price_cur} ≠ 报表币种 {report_cur}，但缺 "
                 f"fx.quote_to_report——跨币种换算无据，估值会被汇率静默扭曲")
        elif not (0.001 <= fx_val <= 1000):
            _err(errors, "SNAPSHOT_FX_SUSPECT",
                 f"fx.quote_to_report={fx_val} 越出 [0.001, 1000] 合理带，疑似汇率方向填反")
        else:
            print(f"  币种链：{price_cur} → {report_cur}，fx={fx_val}（合理带内）")

    return errors, warnings


def check_against_financials(snapshot_path, fin_path, errors, warnings):
    """跨文件量纲比对（OBS-600660-01 原型）。

    福耀/海控两案的错位都发生在**市值传参与底稿单位之间**，不在单文件内部。
    快照三角校验只能保证快照自洽，底稿 schema 只能保证底稿自洽——
    两份文件各自正确却互相错位，此前没有任何机器检查。这里做三件事：

    1. 币种链：快照 market_cap.currency 必须等于底稿 meta.currency，或快照带 fx；
    2. 股本交叉：快照 shares（百万股）vs 底稿最新年 shares_diluted（按 meta.shares_unit
       换算到百万股），偏差 > 5% 即 WARN（回购/增发可解释小差，10 倍差不可解释）；
    3. 市销率交叉：快照市值（换算到底稿 unit）/ 底稿最新年营收，越出 [0.05, 100] 即 FAIL。
       它不依赖股本字段，是竞对底稿也能做的兜底锚。
    """
    try:
        from schema_meta import (SHARES_UNIT_MULTIPLIER, UNIT_MULTIPLIER,
                                 resolve_meta)
    except ImportError:
        _warn(warnings, "SNAPSHOT_FIN_SKIP", "schema_meta 不可用，跳过跨文件比对")
        return
    with open(snapshot_path, "r", encoding="utf-8") as f:
        snap = json.load(f)
    with open(fin_path, "r", encoding="utf-8") as f:
        fin = json.load(f)
    meta = resolve_meta(fin)
    fin_cur = str(meta.get("currency") or "").upper()
    fin_mult = UNIT_MULTIPLIER.get(meta.get("unit"))
    if fin_mult is None:
        _warn(warnings, "SNAPSHOT_FIN_SKIP",
              f"底稿 meta.unit={meta.get('unit')!r} 不可换算，跳过跨文件比对——先修底稿 schema")
        return

    _w = []
    mcap_m, mcap_cur = read_market_cap_million(snap, _w)
    shares_m = read_shares_million(snap, _w)
    rows = sorted([r for r in (fin.get("annual") or []) if isinstance(r, dict)],
                  key=lambda r: r.get("year", 0))
    last = rows[-1] if rows else {}

    # 1. 币种链
    mcap_cur = str(mcap_cur or "").upper()
    if mcap_cur and fin_cur and mcap_cur != fin_cur:
        fx = snap.get("fx")
        if not (isinstance(fx, dict) and fx.get("quote_to_report")):
            _err(errors, "SNAPSHOT_FIN_CURRENCY",
                 f"快照市值币种 {mcap_cur} ≠ 底稿币种 {fin_cur} 且无 fx.quote_to_report——"
                 f"跨币种混算无据")
            return
        mcap_m = mcap_m * float(fx["quote_to_report"]) if mcap_m else mcap_m

    # 2. 股本交叉
    sh = last.get("shares_diluted")
    sh_mult = SHARES_UNIT_MULTIPLIER.get(meta.get("shares_unit")) or fin_mult
    if isinstance(sh, (int, float)) and sh > 0 and shares_m:
        fin_sh_m = sh * sh_mult / 1e6
        dev = shares_m / fin_sh_m - 1
        if abs(dev) > 0.05:
            level = _err if abs(dev) > 0.5 else _warn
            level(errors if level is _err else warnings, "SNAPSHOT_FIN_SHARES",
                  f"快照股本 {shares_m:,.0f} 百万股 vs 底稿 {last.get('year')} 年 "
                  f"shares_diluted={sh:g}（shares_unit={meta.get('shares_unit') or '按 unit 同级推断'}）"
                  f"= {fin_sh_m:,.0f} 百万股，偏差 {dev:+.0%}"
                  + ("——超 50%，量纲错位而非回购/增发" if abs(dev) > 0.5 else "——请核对回购/增发"))
        else:
            print(f"  跨文件股本一致：快照 {shares_m:,.0f} ≈ 底稿 {fin_sh_m:,.0f} 百万股（{dev:+.1%}）")

    # 3. 市销率交叉（兜底锚，不依赖股本）
    rev = last.get("revenue")
    if isinstance(rev, (int, float)) and rev > 0 and mcap_m:
        rev_m = rev * fin_mult / 1e6
        ps = mcap_m / rev_m
        if not (0.05 <= ps <= 100):
            _err(errors, "SNAPSHOT_FIN_PS",
                 f"快照市值 {mcap_m:,.0f} 百万 / 底稿 {last.get('year')} 年营收 {rev_m:,.0f} 百万 "
                 f"= 市销率 {ps:.3g}，越出 [0.05, 100]——市值与底稿至少一方量纲错位"
                 f"（福耀/海控原型：市值按亿填却标百万）")
        else:
            print(f"  跨文件市销率 {ps:.2f}（合理带内）")


def main():
    ap = argparse.ArgumentParser(description="行情快照契约校验（量纲三角 + 币种链 + schema）")
    ap.add_argument("snapshot", help="market_snapshot.json 路径")
    ap.add_argument("--report-currency", help="报表币种（缺省读快照内 currency_report）")
    ap.add_argument("--fin", help="财务底稿路径：启用快照 ↔ 底稿跨文件量纲比对（OBS-600660-01 原型）")
    ap.add_argument("-o", "--output", help="结果 JSON 输出路径")
    args = ap.parse_args()

    errors, warnings = check(args.snapshot, args.report_currency)
    if args.fin:
        check_against_financials(args.snapshot, args.fin, errors, warnings)
    print(f"快照契约校验：{os.path.basename(args.snapshot)}")
    for code, msg in warnings:
        print(f"  [WARN] [{code}] {msg}")
    for code, msg in errors:
        print(f"  [FAIL] [{code}] {msg}")

    codes = sorted({c for c, _ in errors} | {c for c, _ in warnings})
    result = {"file": args.snapshot,
              "errors": [m for _, m in errors], "warnings": [m for _, m in warnings],
              "codes": codes, "passed": not errors}
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    if errors:
        print(f"结论：{len(errors)} 项错误，估值输入不可信——先修快照再跑管线。")
        sys.exit(1)
    print(f"结论：通过（{len(warnings)} 条旧命名告警——可运行但应迁移到规范 schema）。")
    sys.exit(0)


if __name__ == "__main__":
    main()
