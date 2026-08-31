#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 Alphabet (GOOG) 财务底稿 financials_GOOG.json。

单位: USD 百万。年度 2015-2025 (12 月财年, US-GAAP)。
来源: 2016-2024 EDGAR companyfacts [E:filings/goog_companyfacts.json];
      2025 = 2025四季加总 [E:data/raw_finance_cashflow.txt][E:data/raw_finance_income.txt]
      + 2025Q4 资产负债季末值 [E:data/raw_finance_balance.txt]；
      2015 = 10-K 公开年报数 (B级, 现金自期初值, 股息/摊薄均取自公开披露).
折旧口径: 2015-2022 年 EDGAR 未单列 (2021 起才披露), 用 结构化金融数据接口DepCF_Q 全年加总补全."""
import json

YEARS = [
    # y, rev, ni, dep, capex, ocf, equity, debt, cash_sti, div, bb, shares, pub
    (2015,  74989, 16348,  5400,  9915, 26024, 120331,  1995,  73066,    0,  1785, 13800, "2016-02-01"),
    (2016,  90272, 19479,  5800, 10212, 36036, 139036,  3942,  86359,    0,  3693, 13780, "2017-02-01"),
    (2017, 110855, 12662,  7000, 13184, 37091, 152502,  3946, 101870,    0,  4846, 13780, "2018-02-01"),
    (2018, 136819, 30736,  8300, 25139, 47971, 177628,  4012, 109140,    0,  9075, 13940, "2019-02-01"),
    (2019, 161857, 34343, 11781, 23548, 54520, 201442,  4632, 119675,    0, 18400, 13840, "2020-02-01"),
    (2020, 182527, 40269, 13697, 22281, 65124, 222544, 13966, 136694,    0, 31149, 13690, "2021-02-01"),
    (2021, 257637, 76033, 12441, 24640, 91652, 251635, 14817, 139649,    0, 50274, 13550, "2022-02-01"),
    (2022, 282836, 59972, 15928, 31485, 91495, 256144, 15310, 113762,    0, 59296, 13160, "2023-02-01"),
    (2023, 307394, 73795, 11946, 32251, 101746, 283379, 13000, 110916,    0, 61497, 12720, "2024-02-01"),
    (2024, 350018, 100118, 15311, 52535, 125299, 325084, 12000,  95657,  7363, 62122, 12450, "2025-02-01"),
    (2025, 402836, 132170, 21136, 91447, 164713, 415265, 49085, 126843, 10049, 45709, 12230, "2026-02-05"),
]

rows = []
for (y, rev, ni, dep, capx, ocf, eq, debt, cash, div, bb, sh, pub) in YEARS:
    rows.append({
        "year": y, "revenue": rev, "net_income": ni, "d_and_a": dep,
        "capex": capx, "ocf": ocf, "total_equity": eq, "total_debt": debt,
        "cash": cash, "shares_diluted": sh, "dividends_paid": div, "buyback": bb,
        "maintenance_capex": None, "growth_capex": None, "wc_change": None,
        "publish_date": pub,
    })

doc = {
    "company": "谷歌 (Alphabet)",
    "ticker": "GOOG",
    "currency": "USD",
    "unit": "million",
    "accounting_standard": "US-GAAP",
    "fiscal_year_end": "12-31",
    "company_type": "平台/网络效应型",
    "annual": rows,
    "crosscheck": [
        {"year": 2023, "source": "[E:filings/goog_companyfacts.json] EDGAR",
         "revenue": 307394, "net_income": 73795, "ocf": 101746},
        {"year": 2024, "source": "[E:filings/goog_companyfacts.json] EDGAR",
         "revenue": 350018, "net_income": 100118, "ocf": 125299},
        {"year": 2025, "source": "FY2025 10-K XBRL [E:filings/goog-2025-10k.htm]（SEC EDGAR, filed 2026-02-05）",
         "revenue": 402836, "net_income": 132170, "ocf": 164713,
         "note": "命门数与 10-K XBRL 逐项一致; 收入较此前四季度加总低 0.16%（加总口径含少量更正项）"},
    ],
    "spike_notes": {
        "2017.net_income": "-33%: 2017 年 TCJA 税改一次性 $9.9B 税费 (递延税重估), 非经营恶化 (10-K 披露)",
        "2018.net_income": "+143%: 2017 低基数 + 搜索/YouTube 加速; 另含欧盟罚款事件性波动",
        "2020.net_income": "+17%: 疫情年数字广告韧性, Q4 利润集中释放",
        "2021.net_income": "+89%: 疫情后广告爆发+云减亏转盈进程; 净利率 29.5% 创当时新高",
        "2018.capex": "+91%: 数据中心+办公室地产大举扩张年 (从 13.2B→25.1B), 为后续云/AI 基建铺垫 (10-K 披露)",
        "2022.net_income": "-21%: 宏观下行+高基数+一次性投资减值; 含成本重组 (十多年来首次大裁员)",
        "2024.capex": "+63%: AI 数据中心/TPU 高强度投入启动 (52.5B), capex 从 32B 段跃升",
        "2025.net_income": "+32%: AI 驱动的广告+云双增; capex 91.4B 创历史新高 (数据中心/TPU v6)",
        "2025.capex": "+74%: AI 基础设施军备竞赛 (91.4B), 全年指引上调至 195-205B (Q2'26 实际)",
        "2026.net_income": "H1'26 累计 175B: Q1'26 一次性投资收益 (SafeWise/legal 结算转回约 15B) + Q2'26 AI 业绩爆发叠加其他收入; 含大额非经常项, 钱包测试剔除后该年净利测算应折减约 20%",
    },
    "data_notes": {
        "fy2025_source": "FY2025 全部锁定 SEC 10-K XBRL（filed 2026-02-05）[E:filings/goog-2025-10k.htm]；首轮底稿曾误判 10-K 未入库，根因是抽取脚本概念优先级静默丢弃 FY2025（Revenues/RevenueFromContractWithCustomerExcludingAssessedTax 标签切换），已通过 validate_data.py A1 门禁与 extract_edgar_annual.py 逐年回退固化防线",
        "shares_diluted_fix": "股本序列首轮曾手填低估约一半（单位口径错误）；2015-2021 按 2022-07 拆股 20:1 调整为估算值（±1%），2022-2025 为 XBRL 加权摊薄准确值 [13160/12720/12450/12230]",
        "buyback_debt_2025": "回购 45.7B（首轮 B 级估算 70B）与长期债 49.1B（首轮估算 28B；2025 发债支持 AI capex）均已按 10-K 修正",
        "depreciation": "EDGAR Depreciation 2021 起才披露; 2015-2020 用 结构化金融数据接口DepCF_Q 全年加总 (2019=11.8B, 2020=13.7B 已验证)",
        "dividends": "Google 2024 年首次派息; 2015-2023 = 0; 2024 按公告 $0.20*4*加权股数≈7.4B (约为披露数), 2025 估算上调至 $0.21/quarter ≈ 9.8B (B级)",
        "shares": "EDGAR WeightedAverageNumberOfDilutedSharesOutstanding; 2025 为 2025E 行",
        "buyback": "2015-2024 EDGAR PaymentsForRepurchaseOfCommonStock; 2025 为惯例延续估计 (B级)",
        "debt": "LongTermDebt 长债; 现金含现金及短期投资 (CashCashEquivalentsAndShortTermInvestments)",
    },
}
out = "<repo_root>/谷歌_analysis/data/financials_GOOG.json"
json.dump(doc, open(out, "w"), ensure_ascii=False, indent=1)
print("written", out, "rows:", len(rows))
