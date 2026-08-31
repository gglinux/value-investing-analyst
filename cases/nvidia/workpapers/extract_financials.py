#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 SEC EDGAR companyfacts JSON 抽取年度财务数据 → 标准底稿 JSON（单位：百万美元）
规则：
- 数值型科目取同一 end 日期中 filed 最新的（含重述）；摊薄股本取最早 filed（原始披露），
  再按拆股表调整到最新股本口径。
- year 映射：财年截止月 >=6 → end.year；否则 end.year-1（NVDA 1月财年 → 归入覆盖的主要日历年）。
"""
import json, datetime

BASE = "<repo_root>/英伟达_analysis/data"

CAND = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "gross_profit": ["GrossProfit"],
    "ebit": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "d_and_a": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
                 "DepreciationAndAmortization"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "buyback": ["PaymentsForRepurchaseOfCommonStock"],
    "equity_raised": ["ProceedsFromIssuanceOfCommonStock"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax_exp": ["IncomeTaxExpenseBenefit"],
}
CAND_INST = {
    "total_equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash_only": ["CashAndCashEquivalentsAtCarryingValue",
                   "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "sti": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent",
             "TradingSecurities", "AvailableForSaleSecuritiesDebtSecurities"],
    "ltd": ["LongTermDebtAndCapitalLeaseObligations", "LongTermDebtNoncurrent", "LongTermDebt"],
    "ltd_cur": ["LongTermDebtCurrent"],
    "goodwill": ["Goodwill"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
}
SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding"]

SPLITS = {  # (生效日, 因子)：财年 end < 生效日 → 乘以因子
    "NVDA": [("2021-07-20", 4.0), ("2024-06-10", 10.0)],
    "AMD": [], "INTC": [], "AVGO": [("2024-07-15", 10.0)],
}

def load(t):
    with open(f"{BASE}/filings/{t.lower()}_companyfacts.json") as f:
        return json.load(f)["facts"].get("us-gaap", {})

def annual_entries(fact):
    out = {}
    for u, arr in fact.get("units", {}).items():
        if u not in ("USD", "shares"):
            continue
        for e in arr:
            if e.get("form") != "10-K":
                continue
            if "start" in e:  # duration：330~380 天
                try:
                    d = (datetime.date.fromisoformat(e["end"]) - datetime.date.fromisoformat(e["start"])).days
                except Exception:
                    continue
                if not (330 <= d <= 380):
                    continue
            out.setdefault(e["end"], []).append(e)
    return out

def pick(gaap, cands, end, latest=True):
    for c in cands:
        if c not in gaap:
            continue
        es = annual_entries(gaap[c]).get(end)
        if es:
            es = sorted(es, key=lambda x: x.get("filed", ""))
            return es[-1]["val"] if latest else es[0]["val"]
    return None

def year_of(end):
    d = datetime.date.fromisoformat(end)
    return d.year if d.month >= 6 else d.year - 1

def extract(ticker, min_year=2015):
    gaap = load(ticker)
    # 以 revenue 的年度 end 日期为主轴
    ends = set()
    for c in CAND["revenue"]:
        if c in gaap:
            ends |= set(annual_entries(gaap[c]).keys())
    rows, taxes = {}, []
    for end in sorted(ends):
        y = year_of(end)
        if y < min_year or (y in rows):
            continue
        r = {"year": y, "fy_end": end}
        for k, cands in CAND.items():
            v = pick(gaap, cands, end)
            r[k] = round(v / 1e6, 1) if v is not None else None
        for k, cands in CAND_INST.items():
            v = pick(gaap, cands, end)
            r[k] = round(v / 1e6, 1) if v is not None else None
        sh = pick(gaap, SHARES, end, latest=False)
        if sh is not None:
            f = 1.0
            for eff, factor in SPLITS[ticker]:
                if end < eff:
                    f *= factor
            sh = sh * f / 1e6
        r["shares_diluted"] = round(sh, 1) if sh else None
        if r["d_and_a"] is None:
            dep = pick(gaap, ["Depreciation"], end)
            amo = pick(gaap, ["AmortizationOfIntangibleAssets"], end)
            if dep is not None:
                r["d_and_a"] = round((dep + (amo or 0)) / 1e6, 1)
        if r.get("pretax") and r.get("tax_exp") is not None and r["pretax"] > 0:
            taxes.append(r["tax_exp"] / r["pretax"])
        r["cash"] = round((r.pop("cash_only") or 0) + (r.pop("sti") or 0), 1)
        r["total_debt"] = round((r.pop("ltd") or 0) + (r.pop("ltd_cur") or 0), 1)
        r["interest_expense"] = None
        r["wc_change"] = None
        r["maintenance_capex"] = None
        r.pop("pretax"), r.pop("tax_exp")
        rows[y] = r
    tax = round(min(max(sum(taxes[-3:]) / len(taxes[-3:]), 0.05), 0.30), 3) if taxes else 0.15
    return {"company": ticker, "ticker": ticker, "currency": "USD", "unit": "million",
            "accounting_standard": "US-GAAP", "fiscal_year_end": "varies", "tax_rate": tax,
            "annual": [rows[y] for y in sorted(rows)]}

for t in ["NVDA", "AMD", "INTC", "AVGO"]:
    d = extract(t)
    with open(f"{BASE}/financials_{t}.json", "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    print(f"\n== {t} tax={d['tax_rate']} ==")
    for r in d["annual"]:
        print(r["year"], r["fy_end"], "rev", r["revenue"], "ni", r["net_income"], "ebit", r["ebit"],
              "ocf", r["ocf"], "capex", r["capex"], "eq", r["total_equity"], "sh", r["shares_diluted"],
              "gw", r["goodwill"], "debt", r["total_debt"], "cash", r["cash"])
