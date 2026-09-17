#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edgar_facts.py — EDGAR companyfacts 年度抽取统一内核（P0-3 / AST-005）

## 为什么需要它

extract_edgar_annual（B4 抽取器）与 crosscheck_official.edgar_annual（核对器）
此前各自实现了一套 companyfacts 年度筛选逻辑，五处口径分裂：

| 口径 | extract_edgar_annual | crosscheck_official |
|---|---|---|
| form 过滤 | 无（8-K 申报的事实也混入） | 10-K/20-F 系 |
| 期间长度 | duration 字段 300-400 天 | 仅 <300 剔除，无上限 |
| end 归年 | int(end[:4])（无 1-5 月归前年） | 1-5 月归前一年 |
| 单位过滤 | 无（pure 比率混入） | 排除 pure/usd/shares/eur/shares |
| filed 选择 | 最新 filed 优先 | 首见即取（原始申报值） |

同一份 companyfacts 两个答案：核对器拿首见原始值 vs 抽取器拿最新重述值，
差异是"重述噪声"还是"真实错报"无从分辨；8-K 临时数据与 10-K/A 重述的
口径污染直接进入第四批美股案例（GE/F/NOK）的入仓路径。AST-005 裁定：
同一源两个算法不等于独立双源——必须一个内核。

## 统一后的口径（五件套）

1. form 过滤：10-K / 20-F / 10-K/A / 20-F/A（8-K、10-Q 等非年度申报剔除）；
2. 期间长度：duration 行 300-400 天（季度/半年剔除，超长重述期剔除）；
3. end 归年：财年末在 1-5 月的（NVDA 一月财年末、TSM FPI），归前一年；
4. 单位过滤：pure / usd/shares / eur/shares 等比率单位剔除（shares 单位保留，
   供股本字段；金额字段只认 USD/EUR/CNY 等）；
5. filed 选择：参数化 prefer="latest"（最新重述，默认）| "first"（原始申报），
   可加 cutoff（REQ-P0-06 时点正确性：只取 filed <= cutoff 的行）。

## 与两个调用方的关系

- extract_edgar_annual.annual_table：调 annual_series(..., prefer="latest")，
  保留 field__filed / field__concept 输出与 B4 逐年概念回退；
- crosscheck_official.edgar_annual：调 annual_series(..., prefer="latest")，
  保持 (value, concept) 返回签名——首见即取的分裂口径就此消除。
"""
from __future__ import annotations

import datetime

# 字段 → XBRL 概念候选（逐年独立回退；GOOG 实证：概念标签中途切换，
# "第一个非空概念用到底"会静默丢年）
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
                       "WeightedAverageShares",
                       "WeightedAverageNumberOfDilutedSharesOutstandingIfrs"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "EquityAttributableToOwnersOfParent", "Equity"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents",
             "CashCashEquivalentsAndShortTermInvestments"],
    "long_debt": ["LongTermDebt", "LongtermBorrowings"],
}
# duration 字段（流量）须有 start 且期间 300-400 天；瞬时字段（assets/cash 等）
# 无 start，只按 end 归年
DURATION_FIELDS = {"revenue", "gross_profit", "op_income", "net_income", "eps_diluted",
                   "ocf", "capex", "depreciation", "amortisation", "buyback",
                   "dividends", "interest_expense", "shares_diluted"}
CRITICAL = {"revenue", "net_income"}

ANNUAL_FORMS = ("10-K", "20-F", "10-K/A", "20-F/A")
RATIO_UNITS = ("pure", "usd/shares", "eur/shares")


def end_year(end: str) -> int | None:
    """end 日期 → 归属财年。财年末在 1-5 月的（NVDA 1月、TSM 12月末型 FPI 的
    比较期等）归前一年——按 fy 标签聚合会整体错位（TSM 实证）。"""
    if not end or len(end) < 7:
        return None
    try:
        y = int(end[:4])
        if int(end[5:7]) <= 5:
            y -= 1
        return y
    except ValueError:
        return None


def row_ok(r: dict, want_duration: bool,
           prefer: str = "latest", cutoff: str | None = None) -> tuple | None:
    """单行筛选（五件套之一行级实现）。

    返回 (val, filed) 或 None（行不合规）。
    - form 白名单；val 非空；
    - duration 行：start 在场且 300 <= span <= 400；瞬时行：不得有 start
      （companyfacts 瞬时事实本无 start，防御性剔除带 start 的伪瞬时）；
    - 单位比率剔除在 unit 级做（见 annual_series），此处不管单位；
    - cutoff：filed > cutoff 的行剔除（REQ-P0-06 时点正确性）。
    """
    if r.get("form") not in ANNUAL_FORMS:
        return None
    end, val = r.get("end"), r.get("val")
    if not end or val is None:
        return None
    filed = r.get("filed", "")
    if cutoff and filed and filed > cutoff:
        return None
    start = r.get("start", "")
    if want_duration:
        if not start:
            return None
        try:
            span = (datetime.date.fromisoformat(end)
                    - datetime.date.fromisoformat(start)).days
        except ValueError:
            return None
        if not (300 <= span <= 400):
            return None
    elif start:
        return None
    return (val, filed)


def annual_series(cf: dict, taxonomy: str, field: str,
                  prefer: str = "latest", cutoff: str | None = None,
                  concepts: list | None = None):
    """companyfacts → {year: (val, concept, filed)}（统一内核主入口）。

    - 逐年独立回退：按概念候选顺序逐个尝试，某年命中即停（B4 语义）；
    - prefer="latest"：同年多行（重述/修订）取最新 filed——重述后的值是
      最接近当前认知的官方口径；prefer="first"：取原始申报值（RESTATED
      审计用）；
    - 单位过滤：RATIO_UNITS 剔除；同概念多单位（罕见）取首个合规单位。
    """
    facts = (cf.get("facts") or {}).get(taxonomy) or {}
    names = concepts if concepts is not None else FIELD_CONCEPTS.get(field, [])
    want_duration = field in DURATION_FIELDS
    out: dict[int, tuple] = {}
    for concept in names:
        node = facts.get(concept)
        if not node:
            continue
        for unit_key, rows in (node.get("units") or {}).items():
            if unit_key.lower() in RATIO_UNITS:
                continue
            best: dict[int, tuple] = {}
            for r in rows:
                hit = row_ok(r, want_duration, prefer, cutoff)
                if hit is None:
                    continue
                y = end_year(r.get("end", ""))
                if y is None:
                    continue
                val, filed = hit
                if y not in best:
                    best[y] = (val, filed)
                elif prefer == "latest" and filed > best[y][1]:
                    best[y] = (val, filed)
                elif prefer == "first" and filed < best[y][1]:
                    best[y] = (val, filed)
            for y, (val, filed) in best.items():
                if y not in out:
                    out[y] = (val, concept, filed)
    return out
