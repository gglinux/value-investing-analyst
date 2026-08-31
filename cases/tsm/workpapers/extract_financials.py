#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 SEC EDGAR companyfacts(ifrs-full) 抽取 TSM 年度财务数据（参照 cases/nvidia 同款脚本惯例）。

输出: 台积电_analysis/data/raw_edgar_annual.json  (字段: year -> 各指标, 单位 TWD 原生值)
口径: 年度时长 300-400 天; 同一概念多时点取最新 filed; 资产负债为时点值(end)。
"""
import json
import sys

SRC = "<repo_root>/台积电_analysis/data/filings/tsm_companyfacts.json"
OUT = "<repo_root>/台积电_analysis/data/raw_edgar_annual.json"

CONCEPTS = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers"],
    "gross_profit": ["GrossProfit"],
    "cost_of_sales": ["CostOfSales"],
    "net_income": ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
    "op_profit": ["ProfitLossFromOperatingActivities"],
    "eps_basic": ["BasicEarningsLossPerShare"],
    "eps_diluted": ["DilutedEarningsLossPerShare"],
    "ocf": ["CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "amortisation": ["AmortisationExpense"],
    "dividends_paid": ["DividendsPaidClassifiedAsFinancingActivities"],
    "dividends_ps": ["DividendsPaidOrdinarySharesPerShare"],
    "weighted_shares": ["WeightedAverageShares", "AdjustedWeightedAverageShares"],
    "assets": ["Assets"],
    "current_assets": ["CurrentAssets"],
    "liabilities": ["Liabilities"],
    "current_liabilities": ["CurrentLiabilities"],
    "equity": ["EquityAttributableToOwnersOfParent", "Equity"],
    "cash": ["CashAndCashEquivalents"],
    "short_debt": ["ShorttermBorrowings"],
    "cur_lt_debt": ["CurrentPortionOfLongtermBorrowings"],
    "long_debt": ["LongtermBorrowings"],
    "inventory": ["Inventories"],
    "receivables": ["CurrentTradeReceivables"],
    "ppe": ["PropertyPlantAndEquipment"],
}
DURATION = {"revenue", "gross_profit", "cost_of_sales", "net_income", "op_profit",
            "eps_basic", "eps_diluted", "ocf", "capex", "amortisation",
            "dividends_paid", "dividends_ps", "weighted_shares"}


def annual_units(fact, want_duration):
    """返回 {year: (val, filed, start, end)} — duration 概念筛 300-400 天时长; 时点概念直取。"""
    out = {}
    for unit_rows in fact.get("units", {}).values():
        for r in unit_rows:
            end = r.get("end", "")
            if not end:
                continue
            # companyfacts 的 fy 是「申报财年」而非「期间所属年」——FPI 20-F 中的
            # 比较期数据会共用申报 fy，直接用 fy 会把数值整体错位一年（TSM 已验证）。
            fy = int(end[:4])
            if want_duration:
                start = r.get("start", "")
                if not start:
                    continue
                import datetime
                try:
                    d0 = datetime.date.fromisoformat(start)
                    d1 = datetime.date.fromisoformat(end)
                except ValueError:
                    continue
                if not (300 <= (d1 - d0).days <= 400):
                    continue
            if r.get("val") is None:
                continue
            prev = out.get(fy)
            if prev is None or r.get("filed", "") > prev[1]:
                out[fy] = (r["val"], r.get("filed", ""), r.get("start"), end)
    return out


def main():
    d = json.load(open(SRC))
    facts = d["facts"]["ifrs-full"]
    years = list(range(2014, 2027))
    table = {y: {} for y in years}
    for field, names in CONCEPTS.items():
        fact = None
        for n in names:
            if n in facts:
                fact = facts[n]
                break
        if fact is None:
            print(f"[warn] 概念缺失: {field} ({names})")
            continue
        vals = annual_units(fact, field in DURATION)
        for y, (v, filed, start, end) in vals.items():
            if y in table:
                table[y][field] = v
                table[y][field + "__filed"] = filed
    table = {y: r for y, r in table.items() if r.get("revenue") or r.get("assets")}
    json.dump({str(k): v for k, v in sorted(table.items())}, open(OUT, "w"),
              ensure_ascii=False, indent=1, sort_keys=True)
    print(f"years: {sorted(table.keys())} -> {OUT}")


if __name__ == "__main__":
    main()
