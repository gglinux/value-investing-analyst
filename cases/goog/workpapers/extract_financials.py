#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 SEC EDGAR companyfacts 抽取 Alphabet 十年年度数据。"""
import json, datetime

SRC = "<repo_root>/谷歌_analysis/data/filings/goog_companyfacts.json"
CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    "net_income": ["NetIncomeLoss"],
    "op_income": ["OperatingIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "depreciation": ["Depreciation"],
    "buyback": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividendsAndDividendEquivalentsOnCommonStockAndRestrictedStockUnits",
                   "PaymentsOfDividendsCommonStock", "PaymentsOfDividends", "PaymentsOfDividendsMinorityInterest"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "cash_sti": ["CashCashEquivalentsAndShortTermInvestments"],
    "ltd": ["LongTermDebt"],
    "ltd_current": ["LongTermDebtCurrent"],
    "interest_expense": ["InterestExpenseNonoperating", "InterestExpense"],
}
DURATION = {"revenue", "net_income", "op_income", "ocf", "capex", "depreciation",
            "buyback", "dividends", "shares_diluted", "interest_expense"}


def annual_units(fact, want_duration):
    out = {}
    for unit_rows in fact.get("units", {}).values():
        for r in unit_rows:
            end = r.get("end", "")
            if not end:
                continue
            fy = int(end[:4])   # 关键：fy 标签是申报财年，用 end 日期推导（TSM 案例踩过错位坑）
            if want_duration:
                start = r.get("start", "")
                if not start:
                    continue
                try:
                    if not (300 <= (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days <= 400):
                        continue
                except ValueError:
                    continue
            if r.get("val") is None:
                continue
            prev = out.get(fy)
            if prev is None or r.get("filed", "") > prev[1]:
                out[fy] = (r["val"], r.get("filed", ""))
    return out


def main():
    d = json.load(open(SRC))
    gaap = d["facts"]["us-gaap"]
    table = {}
    for field, names in CONCEPTS.items():
        fact = next((gaap[n] for n in names if n in gaap), None)
        if fact is None:
            print(f"[warn] 缺失: {field}")
            continue
        for y, (v, filed) in annual_units(fact, field in DURATION).items():
            table.setdefault(y, {})[field] = v
            table[y][field + "__filed"] = filed
    out = "<repo_root>/谷歌_analysis/data/raw_edgar_annual.json"
    table = {y: r for y, r in table.items() if r.get("revenue")}
    json.dump({str(k): v for k, v in sorted(table.items())}, open(out, "w"), indent=1, sort_keys=True)
    for y in sorted(table):
        r = table[y]
        g = lambda k: f"{r.get(k)/1e9:.1f}B" if r.get(k) else "-"
        print(y, 'rev=', g('revenue'), 'net=', g('net_income'), 'ocf=', g('ocf'), 'capex=', g('capex'),
              'dep=', g('depreciation'), 'eq=', g('equity'), 'div=', g('dividends'), 'bb=', g('buyback'))


if __name__ == "__main__":
    main()
