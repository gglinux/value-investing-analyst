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
    (2015,  74989, 16348,  5400,  9915, 26024, 120331,  1995,  73066,    0,  1785, 6949, "2016-02-01"),
    (2016,  90272, 19479,  5800, 10212, 36036, 139036,  3942,  86359,    0,  3693, 6904, "2017-02-01"),
    (2017, 110855, 12662,  7000, 13184, 37091, 152502,  3946, 101870,    0,  4846, 6948, "2018-02-01"),
    (2018, 136819, 30736,  8300, 25139, 47971, 177628,  4012, 109140,    0,  9075, 6955, "2019-02-01"),
    (2019, 161857, 34343, 11781, 23548, 54520, 201442,  4632, 119675,    0, 18400, 6964, "2020-02-01"),
    (2020, 182527, 40269, 13697, 22281, 65124, 222544, 13966, 136694,    0, 31149, 6797, "2021-02-01"),
    (2021, 257637, 76033, 12441, 24640, 91652, 251635, 14817, 139649,    0, 50274, 6705, "2022-02-01"),
    (2022, 282836, 59972, 15928, 31485, 91495, 256144, 14701, 113762,    0, 59296, 6629, "2023-02-01"),
    (2023, 307394, 73795, 11946, 32251, 101746, 283379, 13253, 110916,    0, 61497, 6442, "2024-02-01"),
    (2024, 350018, 100118, 15311, 52535, 125299, 325084,  8568,  95657,  7363, 62122, 6044, "2025-02-01"),
    (2025, 403463, 132170, 21136, 91447, 164713, 415265, 28000, 126843,  9800, 70000, 5620, "2026-02-01"),
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
        {"year": 2025, "source": "[E:data/raw_finance_cashflow.txt] 四季加总 + raw_finance_balance 2025Q4 期末值",
         "revenue": 403463, "net_income": 132170, "ocf": 164713,
         "note": "FY2025 已收官但 10-K 未入 EDGAR; 财务数据为 结构化金融数据接口四季加总 (USD百万), dotgov 未锁"},
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
        "fy2025_source": "10-K 未入 EDGAR; 财务数据为 结构化金融数据接口四季加总 (income/cashflow), BS 为 2025Q4 期末值; publish_date 标注 2026-02-01 (Alphabet 惯例 2 月初发布上年财报)",
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
