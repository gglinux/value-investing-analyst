#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 EDGAR companyfacts JSON 提取年度序列（流量取全年，存量取年末）。"""
import json, os, sys
UNIT = sys.argv[2] if len(sys.argv) > 2 else "USD"
from datetime import date

RAW = os.path.join(os.path.dirname(__file__), "..", "data", "edgar_raw")

# 候选标签（按优先级取第一个有数据的）
FLOW_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfServicesRevenue"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["ProfitLoss", "NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "d_and_a": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestIncomeExpenseNet"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock", "PaymentsOfDividendsAndDividendEquivalentsOnCommonStockAndRestrictedStockUnits", "Dividends"],
    "buyback": ["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"],
    "equity_raised": ["ProceedsFromIssuanceOfCommonStock", "ProceedsFromInitialPublicOffering"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
}
INSTANT_TAGS = {
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "total_equity": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "short_term_investments": ["ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "long_term_investments": ["LongTermInvestments", "EquityMethodInvestments"],
    "goodwill": ["Goodwill"],
    "accounts_receivable": ["AccountsReceivableNetCurrent", "AccountsReceivableNet"],
    "long_term_debt": ["LongTermDebt", "LongTermDebtNoncurrent", "DebtNoncurrent"],
    "convertible_debt": ["ConvertibleDebtNoncurrent", "ConvertibleDebtCurrent", "ConvertibleNotesPayable"],
    "unsecured_debt": ["UnsecuredLongTermDebt", "UnsecuredDebtCurrent"],
    "loans_payable": ["LongTermLoansPayable", "ShortTermBorrowings", "BankBorrowingsCurrent"],
    "operating_lease_liab": ["OperatingLeaseLiability"],
}
SHARE_TAGS = {
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "shares_basic": ["WeightedAverageNumberOfSharesOutstandingBasic"],
}

def _merge(out, y, val, filed, tag):
    cur = out.get(y)
    if cur is None or filed > cur[1]:
        out[y] = (val, filed, tag)

def annual_flow(facts, tags):
    """返回 {year: (val, filed, tag)}，取全年时长（300~400天），跨候选标签合并，最新 filed 优先。"""
    out = {}
    for tag in tags:
        node = facts.get(tag)
        if not node:
            continue
        for unit, items in node.get("units", {}).items():
            if unit != UNIT:
                continue
            for it in items:
                s, e = it.get("start"), it.get("end")
                if not s or not e:
                    continue
                try:
                    d0 = date.fromisoformat(s); d1 = date.fromisoformat(e)
                except ValueError:
                    continue
                days = (d1 - d0).days
                if not (300 <= days <= 400):
                    continue
                y = d1.year
                if d1.month not in (11, 12, 1):
                    continue
                _merge(out, y, it["val"], it.get("filed", ""), tag)
    return out

def annual_instant(facts, tags):
    out = {}
    for tag in tags:
        node = facts.get(tag)
        if not node:
            continue
        for unit, items in node.get("units", {}).items():
            if unit != UNIT:
                continue
            for it in items:
                e = it.get("end")
                if not e:
                    continue
                try:
                    d1 = date.fromisoformat(e)
                except ValueError:
                    continue
                if d1.month != 12:
                    continue
                y = d1.year
                _merge(out, y, it["val"], it.get("filed", ""), tag)
    return out

def annual_shares(facts, tags):
    out = {}
    for tag in tags:
        node = facts.get(tag)
        if not node:
            continue
        for unit, items in node.get("units", {}).items():
            if unit != "shares":
                continue
            for it in items:
                s, e = it.get("start"), it.get("end")
                if not s or not e:
                    continue
                try:
                    d0 = date.fromisoformat(s); d1 = date.fromisoformat(e)
                except ValueError:
                    continue
                days = (d1 - d0).days
                if not (300 <= days <= 400) or d1.month not in (11, 12, 1):
                    continue
                val = it["val"]
                if val < 1e6:  # 千股单位 → 股
                    val *= 1000
                _merge(out, d1.year, val, it.get("filed", ""), tag)
    return out

def main(path):
    data = json.load(open(path))
    name = data["entityName"]
    facts = data.get("facts", {}).get("us-gaap", {})
    print(f"===== {name} =====")
    years = set()
    series = {}
    for k, tags in FLOW_TAGS.items():
        series[k] = annual_flow(facts, tags)
        years |= set(series[k])
    for k, tags in INSTANT_TAGS.items():
        series[k] = annual_instant(facts, tags)
        years |= set(series[k])
    for k, tags in SHARE_TAGS.items():
        series[k] = annual_shares(facts, tags)
        years |= set(series[k])
    years = sorted(y for y in years if y >= 2014)
    hdr = ["year"] + list(series.keys())
    print(",".join(hdr))
    for y in years:
        row = [str(y)]
        for k in series:
            v = series[k].get(y)
            row.append(f"{v[0]/1e6:.1f}" if v else "")
        print(",".join(row))
    # 打印缺失诊断
    for k in series:
        if not series[k]:
            print(f"  [MISSING-ALL] {k}: {FLOW_TAGS.get(k) or INSTANT_TAGS.get(k) or SHARE_TAGS.get(k)}")
    # 同时输出 filed 日期（最近3年 publish_date 用）
    for k in ("revenue", "net_income", "ocf"):
        for y in sorted(series[k])[-3:]:
            print(f"  filed {k} {y}: {series[k][y][1]} tag={series[k][y][2]}")

if __name__ == "__main__":
    main(sys.argv[1])
