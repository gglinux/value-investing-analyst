#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_edgar_annual.py — SEC EDGAR companyfacts 年度序列抽取器（B4 逐年概念回退版）

起源（必须读的两个实证教训）：
1. TSM 案例（FPI）：companyfacts 的 fy 标签是"申报财年"而非"期间所属年"，比较期数据
   共用申报 fy——按 fy 聚合会把数值整体错位一年。本器一律按 end 日期推导年度。
2. GOOG 案例（本国申报人）：某公司今年改用另一个 XBRL 概念标签（Revenues →
   RevenueFromContractWithCustomerExcludingAssessedTax 或反向），按概念优先级"第一个
   非空概念用到底"会静默丢掉该年。本器逐年独立回退尝试全部候选概念，缺年即报错。

P0-3（2026-09-17）：年度筛选五件套（form 白名单/期间 300-400/end 归年/单位剔除/
filed 口径）统一收编至 edgar_facts.py 单一内核，本文件只做 CLI 编排与覆盖报告；
与 crosscheck_official.edgar_annual 共用同一实现——同一源两个算法不等于独立
双源，口径分裂会让重述噪声与真实错报无从分辨（AST-005）。

用法：
    python3 extract_edgar_annual.py --companyfacts <companyfacts.json> \
        --taxonomy <us-gaap|ifrs-full> [--year-from 2014] [--year-to 2026] \
        [--cutoff 2016-04-30] [--out <annual.json>]
输出：{year: {field: val, field__filed: filed, field__concept: 所用概念名}}
退出码：0 成功；1 关键字段（revenue/net_income）存在缺年。
"""
import argparse
import datetime
import json
import sys

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from edgar_facts import (ANNUAL_FORMS, CRITICAL, DURATION_FIELDS, FIELD_CONCEPTS,  # noqa: E402,F401
                         annual_series)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--companyfacts", required=True)
    ap.add_argument("--taxonomy", required=True, choices=["us-gaap", "ifrs-full"])
    ap.add_argument("--year-from", type=int, default=2014)
    ap.add_argument("--year-to", type=int, default=datetime.date.today().year)
    ap.add_argument("--cutoff", default=None,
                    help="时点截断（REQ-P0-06，YYYY-MM-DD）：只取 filed <= cutoff 的申报行，"
                         "剔除截断日后的重述/晚申报（回放正确性）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cf = json.load(open(args.companyfacts))
    years = list(range(args.year_from, args.year_to + 1))
    table = {y: {} for y in years}
    used = {}

    # 逐年 × 逐字段独立回退抽取（统一内核 edgar_facts.annual_series，
    # prefer="latest"：同年重述取最新 filed）
    for field in FIELD_CONCEPTS:
        series = annual_series(cf, args.taxonomy, field,
                               prefer="latest", cutoff=args.cutoff)
        for y, (val, concept, filed) in series.items():
            if y not in table:
                continue
            table[y][field] = val
            table[y][field + "__filed"] = filed
            table[y][field + "__concept"] = concept
            used.setdefault(field, set()).add(concept)

    table = {y: r for y, r in table.items() if r.get("revenue") or r.get("assets")}

    # 覆盖报告
    print("== 逐年覆盖报告 ==")
    fields = list(FIELD_CONCEPTS)
    hdr = "field".ljust(20) + "".join(str(y)[2:] for y in years if y in table)
    print(hdr)
    for f in fields:
        line = f.ljust(20)
        for y in years:
            if y not in table:
                continue
            line += "█" if table[y].get(f) is not None else "·"
        concepts = used.get(f)
        line += "  [" + ",".join(sorted(concepts)) + "]" if concepts else ""
        print(line)

    missing_critical = [(y, f) for y in table for f in CRITICAL if table[y].get(f) is None]
    if args.out:
        json.dump({str(k): v for k, v in sorted(table.items())},
                  open(args.out, "w"), ensure_ascii=False, indent=1, sort_keys=True)
        print("->", args.out)
    if missing_critical:
        print("!! 关键字段缺年:", missing_critical)
        sys.exit(1)


if __name__ == "__main__":
    main()
