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

用法：
    python3 extract_edgar_annual.py --companyfacts <companyfacts.json> \
        --taxonomy <us-gaap|ifrs-full> [--year-from 2014] [--year-to 2026] \
        [--out <annual.json>]
输出：{year: {field: val, field__filed: filed, field__concept: 所用概念名}}
退出码：0 成功；1 关键字段（revenue/net_income）存在缺年。
"""
import argparse
import datetime
import json
import sys

FIELD_CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "Revenue", "RevenueFromContractsWithCustomers"],
    "gross_profit": ["GrossProfit"],
    "op_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "net_income": ["NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted", "DilutedEarningsLossPerShare"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "depreciation": ["Depreciation", "DepreciationDepletionAndAmortization"],
    "amortisation": ["AmortisationExpense"],
    "buyback": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividends", "PaymentsOfOrdinaryDividends",
                  "DividendsPaidClassifiedAsFinancingActivities"],
    "interest_expense": ["InterestExpenseNonoperating", "InterestExpense"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding",
                       "WeightedAverageShares"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "EquityAttributableToOwnersOfParent", "Equity"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents",
             "CashCashEquivalentsAndShortTermInvestments"],
    "long_debt": ["LongTermDebt", "LongtermBorrowings"],
}
DURATION_FIELDS = {"revenue", "gross_profit", "op_income", "net_income", "eps_diluted",
                   "ocf", "capex", "depreciation", "amortisation", "buyback",
                   "dividends", "interest_expense", "shares_diluted"}
CRITICAL = {"revenue", "net_income"}


def rows_for_year(fact, year, want_duration):
    """从概念的全部单位行中筛出指定日历年（end 推导）的最佳行（最新 filed）。"""
    best = None
    for unit_rows in fact.get("units", {}).values():
        for r in unit_rows:
            end = r.get("end", "")
            if not end or int(end[:4]) != year:
                continue
            if want_duration:
                start = r.get("start", "")
                if not start:
                    continue
                try:
                    span = (datetime.date.fromisoformat(end)
                            - datetime.date.fromisoformat(start)).days
                except ValueError:
                    continue
                if not (300 <= span <= 400):
                    continue
            if r.get("val") is None:
                continue
            if best is None or r.get("filed", "") > best[1]:
                best = (r["val"], r.get("filed", ""))
    return best


def pick_year(facts, names, year, want_duration):
    """B4 核心：逐年独立回退——该年按概念优先级逐个尝试，直到命中。"""
    for n in names:
        fact = facts.get(n)
        if not fact:
            continue
        hit = rows_for_year(fact, year, want_duration)
        if hit:
            return hit[0], hit[1], n
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--companyfacts", required=True)
    ap.add_argument("--taxonomy", required=True, choices=["us-gaap", "ifrs-full"])
    ap.add_argument("--year-from", type=int, default=2014)
    ap.add_argument("--year-to", type=int, default=datetime.date.today().year)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    facts = json.load(open(args.companyfacts))["facts"][args.taxonomy]
    years = list(range(args.year_from, args.year_to + 1))
    table = {y: {} for y in years}
    used = {}

    # 逐年 × 逐字段独立回退抽取
    for y in years:
        for field, names in FIELD_CONCEPTS.items():
            val, filed, concept = pick_year(facts, names, y, field in DURATION_FIELDS)
            if val is not None:
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
