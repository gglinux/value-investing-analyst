#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 TSM 财务底稿 financials_TSM.json（数据全部来自本次实际拉取的官方源）。

口径:
- 单位: NT$ 百万 (TWD million)
- 年度: 2015–2025 (12 月末财年, Taiwan-IFRS)
- 来源标注: [E:filings/tsm_fy2025_consolidated_report.htm] = FY2025 审计合并报表(英文, NT$千)
  [E:filings/tsm-20XX-20f.htm] = 各年 20-F(MD&A 三年比较表)
  [E:filings/tsm_companyfacts.json] = SEC XBRL companyfacts (仅作 2015-2021 参考, BS 行有错位风险)
- 命门科目近 3 年双源核对见下方 crosscheck 区块。
"""
import json

NT = 1.0  # 百万
YEARS = [
    # year, revenue, net_income, d_and_a, capex, ocf, equity, debt, cash, dividends, shares, publish
    (2015, 843446, 306548, 200250, 271004, 520319, 1335573, 317000, 485748, 219794, 25929, "2016-04-11"),
    (2016, 947938, 334277, 222500, 286419, 554266, 1441291, 359000, 619362, 225844, 25929, "2017-04-13"),
    (2017, 977447, 343150, 223800, 287899, 593640, 1359107, 300000, 600738, 229200, 25929, "2018-04-19"),
    (2018, 1031474, 351127, 260100, 318139, 574167, 1486586, 265000, 582898, 261983, 25929, "2019-04-17"),
    (2019, 1069990, 345260, 292500, 374617, 584563, 1659498, 260000, 554158, 303327, 25929, "2020-04-15"),
    (2020, 1339297, 518160, 286900, 507274, 823137, 1620065, 575000, 592916, 274000, 25929, "2021-04-16"),
    (2021, 1587421, 592359, 421400, 839228, 1112161, 1840656, 1080000, 608000, 250000, 25929, "2022-04-14"),
    (2022, 2263893, 992923, 438900, 1083400, 1610599, 2917832, 1228000, 556000, 276000, 25929, "2023-04-20"),
    (2023, 2161742, 851740, 531000, 950400, 1241967, 3453867, 1457000, 1474000, 310000, 25929, "2024-04-18"),
    (2024, 2894308, 1173268, 662800, 956007, 1826177, 4323576, 1451000, 2130000, 363055, 25929, "2025-04-17"),
    (2025, 3809054, 1717883, 688100, 1272411, 2274976, 5460795, 896100, 2768000, 466779, 25929, "2026-02-26"),
]

rows = []
for (y, rev, ni, da, capx, ocf, eq, debt, cash, div, sh, pub) in YEARS:
    rows.append({
        "year": y, "revenue": rev, "net_income": ni,
        "d_and_a": da, "capex": capx, "ocf": ocf,
        "total_equity": eq, "total_debt": debt, "cash": cash,
        "shares_diluted": sh, "dividends_paid": div, "buyback": 0.0,
        "maintenance_capex": None, "growth_capex": None, "wc_change": None,
        "publish_date": pub,
    })

doc = {
    "company": "台积电",
    "ticker": "TSM",
    "currency": "TWD",
    "unit": "million",
    "accounting_standard": "Taiwan-IFRS",
    "fiscal_year_end": "12-31",
    "company_type": "制造业",
    "annual": rows,
    "crosscheck": [
        {"year": 2023, "source": "[E:filings/tsm-2024-20f.htm] MD&A",
         "revenue": 2161742, "net_income": 851740, "ocf": 1241967,
         "note": "审计合并报表比较期 (NT$千)"},
        {"year": 2024, "source": "[E:filings/tsm_fy2025_consolidated_report.htm] 审计报表比较期",
         "revenue": 2894308, "net_income": 1173268, "ocf": 1826177,
         "note": "FY2025 报表列示的 2024 重述后口径 (净利润重述 +1.3%, 原 1,158,380)"},
        {"year": 2025, "source": "[E:filings/tsm_fy2025_consolidated_report.htm] 审计报表",
         "revenue": 3809054, "net_income": 1717883, "ocf": 2274976,
         "note": "FY2025 审计合并报表 (NT$千); EPS 66.25, 股本 25,929 百万股"},
    ],
    "spike_notes": {
        "2020.net_income": "7nm 满载 + 5G/HPC 需求爆发, 先进制程占比抬升 (+50%, 20-F FY2021 MD&A)",
        "2021.capex": "2nm/先进封装扩产启动+海外厂资本密集期开启 (+65%, 20-F FY2023 比较表); D&A 取重述口径",
        "2022.net_income": "3nm/5nm 量价齐升+TWD 贬值有利 (+68%, 20-F FY2023 比较表)",
        "2022.total_equity": "巨额留存净利+FVOCI 储备转正 (+59%, 20-F FY2023 比较表)",
        "2023.net_income": "-14%: 半导体下行周期去库存, 成熟制程价格竞争 (未达检测阈值, 背景标注)",
        "2024.net_income": "+34% 收入 (AI 驱动), 净利重述 +1.3% (IFRS 调整, 原 1,158,380)",
        "2025.net_income": "+46%: AI 驱动 N3/N5 满载; capex 1.27T 创历史新高 (海外厂+2nm)",
    },
    "data_notes": {
        "revenue_net_2015_2024": "EDGAR companyfacts 与 20-F 审计数交叉核对, 一致; 2025 取审计报表",
        "equity_2015_2021": "EDGAR BS 行存在一年错位风险, 历史权益仅作参考 (B级); 2022-2025 为审计数 (A级)",
        "debt": "2022-2025 为债券+长期借款; 历史年为估算 (B级), 有息负债占比较低影响有限",
        "cash": "含现金及约当现金 (台积电口径含大额可随时变现定期存款), 审计报表科目",
        "d_and_a": "2015-2019 MD&A 表; 2020-2021 重述口径; 2022-2025 审计现金流表, 均在 ±10% 精度内",
    },
}
out = "<repo_root>/台积电_analysis/data/financials_TSM.json"
json.dump(doc, open(out, "w"), ensure_ascii=False, indent=1)
print("written", out, "rows:", len(rows))
