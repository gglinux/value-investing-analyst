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

UNIT_TO_MILLION = {"million": 1.0, "yi": 100.0, "亿": 100.0,
                   "yuan": 0.000001, "元": 0.000001}

# 旧命名 -> 规范字段 的已知映射。每个 tuple = (规范字段, 旧字段名, 隐含单位说明)
_PRICE_LEGACY = ["price", "price_cny", "price_usd", "price_hkd", "price_a"]
_SHARES_LEGACY = ["shares_outstanding", "total_shares_million"]  # 均为百万股
_MCAP_NUMERIC = ["market_cap", "total_market_cap_cny_million"]
_MCAP_YI = ["total_market_cap_usd_yi", "total_market_cap_cny_yi"]  # 字段名明示单位为亿


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
            _warn(warnings, "SNAPSHOT_LEGACY_SCHEMA",
                  f"股本使用旧命名 `{k}`；规范为 shares.value + shares.unit")
            return float(d[k])
    return None


def read_market_cap_million(d, warnings):
    """返回百万为单位的市值，或 None。"""
    m = d.get("market_cap")
    if isinstance(m, dict):
        if isinstance(m.get("value"), (int, float)):
            unit = m.get("unit")
            f = UNIT_TO_MILLION.get(unit)
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


def main():
    ap = argparse.ArgumentParser(description="行情快照契约校验（量纲三角 + 币种链 + schema）")
    ap.add_argument("snapshot", help="market_snapshot.json 路径")
    ap.add_argument("--report-currency", help="报表币种（缺省读快照内 currency_report）")
    ap.add_argument("-o", "--output", help="结果 JSON 输出路径")
    args = ap.parse_args()

    errors, warnings = check(args.snapshot, args.report_currency)
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
