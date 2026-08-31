#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建数据底稿（数字全部来自 EDGAR XBRL 提取 + 业绩公告/20-F 核对，见 manifest）。"""
import json, os

DATA = os.path.join(os.path.dirname(__file__), "..", "data")

def row(y, rev, cost, op, ni, ocf, capex, da, ie, tax, div, bb, sbc, ta, tl, eq,
        cash_st, gw, ar, debt, sh, pub=None):
    return {
        "year": y, "publish_date": pub, "revenue": rev, "cost_of_revenue": cost,
        "gross_profit": round(rev - cost, 1) if cost is not None else None,
        "ebit": op, "net_income": ni, "ocf": ocf, "capex": capex, "d_and_a": da,
        "interest_expense": ie, "income_tax": tax, "dividends_paid": div,
        "buyback": bb, "sbc": sbc, "total_assets": ta, "total_liabilities": tl,
        "total_equity": eq, "cash": cash_st, "goodwill": gw,
        "accounts_receivable": ar, "total_debt": debt, "shares_diluted": sh,
        "wc_change": None, "maintenance_capex": None, "growth_capex": None,
    }

# ================= 微博 WB（单位：百万美元，US-GAAP，财年 12-31） =================
# 来源：EDGAR companyfacts CIK0001595761（A级）；归母净利=NetIncomeLoss 标签；
# D&A 2023-2025 用 20-F 现金流量表口径（58.5/58.1/59.1），其余年份 XBRL；
# total_debt = 可转债+无抵押债券+银行贷款（2017/2018 用 LongTermDebt 含可转债口径）；
# cash = 现金及等价物+短期投资。
wb_annual = [
    row(2014, 334.2, 83.6, -22.1, -65.5, -19.4, 14.7, 21.8, None, 1.1, None, None, 13.8, 703.5, 136.1, 567.4, 451.3, 11.7, None, 0.0, 186.9),
    row(2015, 477.9, 142.0, 37.5, 34.2, 182.0, 10.9, 19.5, None, 2.6, None, None, 26.4, 839.2, 211.2, 628.0, 335.8, 11.1, None, 0.0, 217.9),
    row(2016, 655.8, 171.2, 141.0, 105.7, 236.2, 13.3, 13.3, None, 4.3, None, None, 36.9, 1036.9, 279.6, 757.4, 396.0, 10.3, None, 0.0, 222.9),
    row(2017, 1150.1, 231.3, 407.6, 350.4, 539.2, 20.5, 14.7, None, 66.7, None, None, 48.0, 2561.8, 1367.0, 1194.8, 1792.7, 13.4, None, 956.8, 225.4),
    row(2018, 1718.5, 277.6, 609.3, 572.6, 488.0, 28.4, 18.5, 15.4, 96.2, None, None, 41.0, 3274.7, 1526.5, 1748.1, 1825.9, 29.3, None, 945.0, 232.7),
    row(2019, 1766.9, 328.8, 597.6, 492.8, 631.7, 21.7, 22.4, 29.9, 109.6, None, None, 61.3, 4804.2, 2522.4, 2281.8, 2404.2, 29.0, None, 1682.3, 226.4),
    row(2020, 1689.9, 302.2, 506.8, 313.4, 741.6, 34.8, 26.5, 57.4, 61.3, None, None, 67.1, 6335.1, 3448.8, 2828.6, 3496.8, 61.7, 492.0, 2428.5, 227.6),
    row(2021, 2257.1, 403.8, 697.4, 428.3, 814.0, 35.1, 32.8, 71.0, 138.8, None, 188.1, 88.0, 7519.5, 3831.5, 3621.4, 3134.8, 130.4, 723.1, 2434.9, 230.2, "2022-03-10"),
    row(2022, 1836.3, 400.6, 480.5, 85.6, 564.1, 43.1, 33.2, 71.6, 30.3, None, 57.7, 111.7, 7129.5, 3738.9, 3344.7, 3171.2, 120.2, 502.4, 2421.6, 236.4, "2023-04-27"),
    row(2023, 1759.8, 374.3, 472.9, 342.6, 672.8, 36.8, 58.5, 120.1, 145.3, 200.1, None, 101.1, 7280.4, 3762.7, 3448.9, 3225.6, 166.4, 440.8, 1852.9, 240.0, "2024-04-25"),
    row(2024, 1754.7, 369.5, 494.3, 300.8, 639.9, 61.5, 58.1, 105.4, 110.5, 194.4, None, 69.7, 6504.5, 2925.6, 3533.8, 2350.5, 162.2, 339.8, 1860.8, 265.2, "2025-04-15"),
    row(2025, 1757.2, 421.8, 464.8, 449.0, 519.5, 42.4, 59.1, 82.4, 144.5, 195.6, None, 42.1, 7091.2, 3083.6, 3974.7, 2405.0, 169.3, 400.2, 1863.5, 268.6, "2026-04-23"),
]
wb = {
    "company": "微博 Weibo Corporation", "ticker": "WB (NASDAQ) / 9898 (HKEX)",
    "company_type": "互联网平台",
    "currency": "USD", "unit": "million", "accounting_standard": "US-GAAP",
    "fiscal_year_end": "12-31", "fx_basis": "美元报告币种，未换算",
    "tax_rate": 0.25,
    "annual": wb_annual,
    "spike_notes": {
        "2015.net_income": "商业化放量扭亏为盈（-65.5→34.2），广告收入高增长",
        "2015.ocf": "扭亏后经营现金流转正并放量（-19.4→182.0）",
        "2017.revenue": "收入+75%：广告放量与视频化，FY2017 20-F MD&A",
        "2017.ocf": "OCF+128% 随收入利润放量",
        "2017.total_equity": "发行 9 亿美元可转债（2017年11月）+ 当年利润累积",
        "2018.net_income": "净利+63% 随经营杠杆释放",
        "2022.net_income": "归母净利 -80%（428.3→85.6）：疫情广告下滑 + 投资减值/公允价值损失（20-F 非经营项）",
        "2023.net_income": "归母净利 +300%（85.6→342.6）：2022 低基数（减值不再）+ 降本增效",
        "2024.capex": "capex +67%（36.8→61.5）：低基数波动，绝对额仍极小（占收入 3.5%）",
    },
    "crosscheck": [
        {"year": 2023, "source": "FY2025 Form 20-F（2026-04-23 申报，PwC 审计）合并现金流量表/利润表 + FY2023 业绩公告（2024-03-14，ir.weibo.com）：收入 US$1.76B、归母净利 US$342.6M",
         "revenue": 1759.8, "net_income": 342.6, "ocf": 672.8, "shares_diluted": 240.0},
        {"year": 2024, "source": "FY2025 Form 20-F 合并报表 + FY2024 业绩公告（2025-03-13，SEC 6-K ex99.1）：收入 US$1.75B",
         "revenue": 1754.7, "net_income": 300.8, "ocf": 639.9, "shares_diluted": 265.2},
        {"year": 2025, "source": "FY2025 业绩公告（2026-03-18，SEC 6-K ex99.1）：收入 US$1.76B、归母净利 US$449.0M、摊薄EPS US$1.70；FY2025 Form 20-F：OCF US$519.5M",
         "revenue": 1757.2, "net_income": 449.0, "ocf": 519.5, "shares_diluted": 268.6},
    ],
}

# ================= 竞对（均 --skip-crosscheck） =================
def simple(company, ticker, currency, annual):
    return {"company": company, "ticker": ticker, "company_type": "互联网平台",
            "currency": currency, "unit": "million", "accounting_standard": "US-GAAP",
            "fiscal_year_end": "12-31", "fx_basis": "美元（20-F 便利折算口径）" if company != "快手" else "人民币，未换算",
            "tax_rate": 0.25, "annual": annual[0], "spike_notes": annual[1]}

bidu = simple("百度 Baidu", "BIDU (NASDAQ)", "USD", ([
    row(2016, 10161.2, 5081.2, 1447.4, 1670.1, 3205.9, 603.4, 497.1, None, 419.6, None, 0.0, 253.5, 26213.1, 12135.2, 13286.8, 13045.7, 2209.7, 591.9, 4731.6, None),
    row(2017, 13034.0, 6619.0, 2410.0, 2810.0, 5054.0, 735.0, 585.0, None, 460.0, None, 265.0, 499.0, 38689.0, 18651.0, 18344.0, 15442.0, 2429.0, 703.0, 5473.0, None),
    row(2018, 14876.0, 7526.0, 2259.0, 3284.0, 5231.0, 1276.0, 543.0, None, 690.0, None, 482.0, 681.0, 43279.0, 17716.0, 25459.0, 20255.0, 2696.0, 875.0, 7215.0, None),
    row(2019, 15429.0, 9028.0, 906.0, -328.0, 4088.0, 923.0, 807.0, None, 279.0, None, 712.0, 808.0, 43280.0, 18458.0, 24663.0, 21025.0, 2621.0, 1065.0, 6221.0, None),
    row(2020, 16410.0, 8454.0, 2198.0, 2916.0, 3709.0, 779.0, 869.0, None, 623.0, None, 2001.0, 1031.0, 50990.0, 21589.0, 28926.0, 24856.0, 3410.0, 1328.0, 7419.0, None),
    row(2021, 19536.0, 10092.0, 1651.0, 1191.0, 3158.0, 1710.0, 896.0, None, 500.0, None, 1190.0, 1107.0, 59636.0, 24492.0, 34022.0, 28261.0, 3547.0, 1566.0, 8414.0, None),
    row(2022, 17931.0, 9269.0, 2307.0, 1092.0, 3794.0, 1201.0, 905.0, None, 374.0, None, 279.0, 984.0, 56686.0, 22208.0, 33261.0, 25227.0, 3259.0, 1701.0, 6785.0, None),
    row(2023, 18958.0, 9159.0, 3078.0, 3036.0, 5157.0, 1576.0, 1000.0, None, 514.0, None, 671.0, 894.0, 57291.0, 20304.0, 35654.0, 27311.0, 3181.0, 1528.0, 5777.0, None),
    row(2024, 18238.0, 9056.0, 2914.0, 3312.0, 2909.0, 1114.0, 909.0, None, 609.0, None, 866.0, 655.0, 58606.0, 19751.0, 37503.0, 17459.0, 3094.0, 1384.0, 4935.0, None),
    row(2025, 18458.0, 10358.0, -833.0, 780.0, -431.0, 1726.0, 1100.0, None, 180.0, None, 792.0, 517.0, 64229.0, 22798.0, 39548.0, 16483.0, 5260.0, 1855.0, 5907.0, None),
], {
    "2019.net_income": "转亏：投资公允价值损失与减值（20-F 非经营项）",
    "2020.net_income": "扭亏：投资损失不再 + 成本收缩",
    "2021.net_income": "净利 -59%：投资公允价值损失（快手等持股）",
    "2021.capex": "capex +120%：AI/云基础设施投入启动",
    "2023.net_income": "净利 +178%：广告复苏 + 2022 低基数",
    "2025.net_income": "净利 -76%、经营利润转负：FY2025 计提大额商誉减值（竞对，未做原文核对）",
    "2025.ocf": "OCF 转负（竞对，疑与 AI 投入营运资本相关，未做原文核对）",
    "2025.capex": "capex +55%：AI 资本开支",
}))

bili = simple("哔哩哔哩 Bilibili", "BILI (NASDAQ)", "USD", ([
    row(2018, 600.5, 476.1, -106.0, -82.2, 107.2, 42.7, 14.5, None, 3.8, None, None, 26.4, 1525.7, 479.8, 1045.9, 646.0, 136.9, 47.2, 0.0, 233.0),
    row(2019, 973.6, 802.6, -214.8, -187.2, 27.9, 42.5, 27.5, 6.7, 5.2, None, None, 24.8, 2228.8, 1131.9, 1096.9, 941.7, 145.4, 107.0, 490.5, 323.2),
    row(2020, 1838.9, 1403.6, -481.4, -468.0, 115.4, 92.3, 50.0, 16.6, 8.2, None, None, 59.1, 3657.6, 2464.9, 1192.7, 1248.6, 198.6, 161.5, 1278.3, None),
    row(2021, 3041.7, 2407.3, -1008.9, -1068.4, -415.4, 151.5, 84.5, 24.4, 15.0, None, None, 156.9, 8168.3, 4760.6, 3407.7, 3543.9, 366.9, 216.9, 2790.7, None),
    row(2022, 3175.1, 2617.0, -1211.8, -1088.5, -567.1, 110.3, 109.5, 36.4, 15.1, None, 50.4, 150.9, 6064.9, 3855.3, 2209.5, 2145.2, 395.1, 192.6, 1258.9, None),
    row(2023, 3173.0, 2406.5, -713.3, -677.7, 37.6, 25.6, 102.4, 23.2, 11.1, None, None, 159.5, 4670.4, 2641.6, 2028.8, 1386.6, 383.8, 221.7, 0.1, None),
    row(2024, 3675.9, 2473.9, -184.1, -186.8, 824.0, 63.8, 75.9, 12.2, -5.0, None, 16.1, 152.9, 4479.7, 2547.4, 1932.2, 1775.0, 373.3, 168.1, 447.2, None),
    row(2025, 4339.7, 2750.4, 160.8, 170.3, 1022.0, 73.3, 68.8, 21.5, 2.5, None, 117.6, 167.4, 5886.9, 3663.5, 2223.4, 2664.1, 403.0, 181.4, 682.9, None),
], {
    "2019.revenue": "收入 +62%：游戏+直播+广告放量（竞对，未做原文核对）",
    "2020.revenue": "收入 +89%：破圈增长期",
    "2021.revenue": "收入 +65%：破圈延续；同年港股二次上市",
    "2021.net_income": "亏损扩大 -128%：破圈期内容/营销重投入",
    "2021.total_equity": "权益 +186%：港股二次上市募资+可转债",
    "2020.capex": "capex +117%：服务器/内容投入",
    "2023.ocf": "OCF 转正：降本增效拐点",
    "2024.ocf": "OCF 大幅转正（824.0）：广告与游戏放量",
    "2024.net_income": "亏损收窄 +72%：减亏路径兑现",
    "2025.net_income": "首次年度盈利（170.3）：扭亏拐点",
}))

momo = simple("挚文集团 Hello Group", "MOMO (NASDAQ)", "USD", ([
    row(2016, 553.1, 241.5, 144.5, 145.2, 218.3, 7.0, 8.4, None, 5.1, None, None, 31.7, 769.7, 135.7, 634.0, 257.6, None, 36.1, 0.0, 407.0),
    row(2017, 1318.3, 649.3, 360.9, 318.0, 427.6, 32.3, 11.7, None, 66.0, None, None, 49.7, 1302.0, 264.2, 1037.8, 687.4, 3.4, 39.6, 0.0, 415.3),
    row(2018, 1950.2, 1044.7, 475.1, 405.6, 484.0, 35.3, 21.6, 8.2, 101.8, None, None, 84.5, 2758.4, 1155.2, 1603.2, 359.0, 626.4, 104.7, 0.0, 433.1),
    row(2019, 2444.1, 1219.8, 510.6, 425.3, 782.7, 26.8, 28.5, 11.3, 127.0, None, None, 202.3, 3229.6, 1259.0, 1970.6, 375.3, 626.4, 38.1, 0.0, 451.2),
    row(2020, 2302.6, 1222.5, 388.0, 321.9, 472.2, 19.0, 32.0, 12.1, 115.8, None, 50.6, 104.0, 3558.7, 1285.1, 2273.6, 515.5, 626.6, 30.8, 0.0, 452.1),
    row(2021, 2287.2, 1315.5, -375.0, -459.1, 244.7, 15.0, 24.4, 11.6, 129.1, None, 135.4, 74.7, 2842.0, 1180.9, 1661.1, 874.1, 0.0, 32.2, 0.0, 404.7),
    row(2022, 1841.9, 1076.0, 236.0, 214.6, 177.9, 11.7, 15.5, 12.1, 81.5, None, 56.9, 58.2, 2295.1, 710.3, 1584.8, 771.1, None, 27.4, 273.0, 423.8),
    row(2023, 1690.5, 989.5, 324.7, 274.9, 320.7, 81.2, 10.5, 8.8, 88.7, None, 29.9, 37.6, 2285.7, 597.4, 1688.3, 791.6, None, 28.4, 273.0, 401.8),
    row(2024, 1447.1, 883.3, 210.0, 142.4, 224.7, 39.1, 7.2, 17.5, 115.8, None, 164.0, 26.4, 2518.5, 952.2, 1566.3, 564.8, 18.7, 26.3, 0.0, 373.6),
    row(2025, 1482.5, 921.9, 193.7, 115.3, 169.2, 70.4, 6.2, 10.4, 120.5, None, 107.2, 23.3, 1970.4, 385.3, 1585.1, 778.6, 85.3, 35.2, 0.4, 338.6),
], {
    "2017.revenue": "收入 +138%：直播业务爆发（竞对，未做原文核对）",
    "2017.net_income": "净利 +119% 随直播放量",
    "2018.total_equity": "权益 +54%：发行可转债（2018）+ 利润累积",
    "2019.ocf": "OCF +62% 随利润放量",
    "2021.net_income": "转亏 -243%：探探商誉及无形资产大额减值（约 6.9 亿美元）",
    "2022.net_income": "扭亏：减值不再 + 降本",
    "2023.capex": "capex +594%（11.7→81.2）：低基数，购置资产",
}))

kuaishou = {
    "company": "快手 Kuaishou", "ticker": "1024 (HKEX)", "company_type": "互联网平台",
    "currency": "CNY", "unit": "million", "accounting_standard": "IFRS",
    "fiscal_year_end": "12-31", "fx_basis": "人民币，未换算", "tax_rate": 0.15,
    "annual": [
        row(2021, 81082, None, None, -78074, -6751, None, None, None, None, None, None, None, 92515, 47419, 45096, None, None, None, None, None),
        row(2022, 94183, None, None, -13690, 2461, None, None, None, None, None, None, None, 89307, 49469, 39838, None, None, None, None, None),
        row(2023, 113470, None, None, 6396, 22932, None, None, None, None, None, None, None, 106296, 57222, 49074, None, None, None, None, None),
        row(2024, 126898, None, None, 15335, 32166, None, None, None, None, None, None, None, 139873, 77849, 62024, None, None, None, None, None),
        row(2025, 142776, None, None, 18617, 29579, None, None, None, None, None, None, None, 164504, 84920, 79584, None, None, None, None, None),
    ],
    "spike_notes": {
        "2022.net_income": "亏损收窄：降本增效（2021 含上市前可转债公允价值等大额非现金损失）",
        "2023.net_income": "首次全年盈利（经调整口径更早转正）",
        "2023.ocf": "OCF 大幅转正随盈利拐点",
    },
}

# ================= 市场快照 / 分部 / 平台指标 / 一致预期 =================
market_snapshot = {
    "as_of": "2026-08-28 美股收盘",
    "price_usd": 6.99, "market_cap_usd_m": 1717.0, "pe_ttm": 5.78,
    "pb": 0.51, "dividend_per_ads_forward_usd": 0.61, "dividend_yield_forward": 0.087,
    "w52_high": 12.35, "w52_low": 6.89, "price_position_52w": 0.018,
    "avg_daily_volume_shares_m": 1.19, "avg_daily_turnover_usd_m": 8.3,
    "shares_outstanding_m": 245.5, "cash_st_usd_m": 2405.0,
    "total_debt_usd_m": 1863.5, "net_cash_usd_m": 541.5, "ev_usd_m": 1175.5,
    "long_term_investments_usd_m": 1663.3,
    "source": "腾讯行情 qt.gtimg.cn（usWB，2026-08-28 收盘，A级行情）; PB/均量 Yahoo Finance 2026-08（B级）; 股本/现金/债务 FY2025 20-F（A级）",
}

segments = {
    "unit": "USD million", "source": "各年 FY 业绩公告（SEC 6-K ex99.1，A级）；2024 广告收入为总收入减 VAS 的残差",
    "years": [
        {"year": 2021, "ads": 1981.0, "vas": 276.3, "ads_ex_alibaba": 1840.0},
        {"year": 2022, "ads": 1597.0, "vas": 239.7, "ads_ex_alibaba": 1490.0},
        {"year": 2023, "ads": 1534.0, "vas": 225.8, "ads_ex_alibaba": None},
        {"year": 2024, "ads": 1498.7, "vas": 256.0, "ads_ex_alibaba": None},
        {"year": 2025, "ads": 1501.6, "vas": 255.6, "ads_ex_alibaba": 1330.0},
        {"year": 2026, "note": "H1: Q1 收入 421.3(+6%)/广告 369.8(+9%); Q2 收入 453.8(+2%)/广告 381.0(-1%)；恒定汇率口径 Q2 广告 -6%"},
    ],
}

platform_metrics = {
    "source": "各年 FY 业绩公告与 20-F（A级）；2021 年数据经人民日报英文版 FY2021 报道与港股招股书转引双源确认",
    "mau_dec_m": {"2020": 521, "2021": 573, "2022": 586, "2023": 598, "2024": 590, "2025": 567, "2026-06": 561},
    "dau_dec_m": {"2021": 249, "2022": 252, "2023": 257, "2024": 260, "2025": 252, "2026-06": 254},
    "note": "MAU 2023 年见顶 5.98 亿后连续下滑；DAU/MAU 约 43-45%",
}

consensus = {
    "level": "C",
    "source": "Yahoo Finance 分析师汇总（2026-08 中旬抓取）+ Citigroup 评级报道（2025-08-15）",
    "fy2026": {"revenue_usd_m_approx": 1780, "eps_gaap_approx": 1.45,
               "q1_2026_eps_est_vs_actual": "est 0.36 / actual 0.34（小幅 miss）"},
    "price_targets": {"avg": 9.04, "low": 6.60, "high": 11.10, "citi_2025_08": {"rating": "Buy", "pt": 14}},
    "note": "C 级来源（媒体/聚合平台转引），按 skill 规则变异认知测试置信度降一档",
}

manifest = {"files": [
    {"file": "edgar_raw/facts_0001595761.json", "source": "SEC EDGAR companyfacts API", "level": "A", "fetched": "2026-08-29", "period": "FY2012-FY2025", "validated": True},
    {"file": "edgar_raw/facts_0001329099.json", "source": "SEC EDGAR companyfacts API", "level": "A", "fetched": "2026-08-29", "period": "FY2014-FY2025", "validated": True},
    {"file": "edgar_raw/facts_0001723690.json", "source": "SEC EDGAR companyfacts API", "level": "A", "fetched": "2026-08-29", "period": "FY2018-FY2025", "validated": True},
    {"file": "edgar_raw/facts_0001610601.json", "source": "SEC EDGAR companyfacts API", "level": "A", "fetched": "2026-08-29", "period": "FY2014-FY2025", "validated": True},
    {"file": "filings/wb_20f_fy2025.htm", "source": "SEC EDGAR Form 20-F FY2025（2026-04-23 申报，PwC 无保留意见）", "level": "A", "fetched": "2026-08-29", "period": "FY2023-FY2025", "validated": True},
    {"file": "filings/wb_fy2025_results.htm", "source": "SEC 6-K ex99.1 FY2025 业绩公告（2026-03-18）", "level": "A", "fetched": "2026-08-29", "period": "Q4/FY2025", "validated": True},
    {"file": "filings/wb_fy2024_results.htm", "source": "SEC 6-K ex99.1 FY2024 业绩公告（2025-03-13）", "level": "A", "fetched": "2026-08-29", "period": "Q4/FY2024", "validated": True},
    {"file": "filings/wb_q1_2026_results.htm", "source": "SEC 6-K ex99.1 Q1 2026 业绩公告（2026-05-28）", "level": "A", "fetched": "2026-08-29", "period": "Q1 2026", "validated": True},
    {"file": "filings/wb_q2_2026_results.htm", "source": "SEC 6-K ex99.1 Q2 2026 业绩公告（2026-08-19）", "level": "A", "fetched": "2026-08-29", "period": "Q2 2026", "validated": True},
    {"file": "financials_WB.json", "source": "由 edgar_raw + filings 构建，validate_data.py 入口校验", "level": "A", "fetched": "2026-08-29", "period": "FY2014-FY2025", "validated": True},
    {"file": "financials_BIDU.json", "source": "EDGAR 构建（竞对，--skip-crosscheck，未做原文核对）", "level": "A-", "fetched": "2026-08-29", "period": "FY2016-FY2025", "validated": True},
    {"file": "financials_BILI.json", "source": "EDGAR 构建（竞对，--skip-crosscheck）", "level": "A-", "fetched": "2026-08-29", "period": "FY2018-FY2025", "validated": True},
    {"file": "financials_MOMO.json", "source": "EDGAR 构建（竞对，--skip-crosscheck）", "level": "A-", "fetched": "2026-08-29", "period": "FY2016-FY2025", "validated": True},
    {"file": "financials_Kuaishou.json", "source": "港交所 FY2025 业绩公告五年财务概要（static.cninfo.com.cn 镜像，竞对，--skip-crosscheck）", "level": "B", "fetched": "2026-08-29", "period": "FY2021-FY2025", "validated": True},
    {"file": "market_snapshot.json", "source": "腾讯行情 + Yahoo Finance + 20-F", "level": "A/B", "fetched": "2026-08-28/29", "period": "2026-08-28 收盘", "validated": True},
    {"file": "segments.json", "source": "各年 FY 业绩公告", "level": "A", "fetched": "2026-08-29", "period": "FY2021-2026H1", "validated": True},
    {"file": "platform_metrics.json", "source": "业绩公告/20-F/招股书转引", "level": "A/B", "fetched": "2026-08-29", "period": "2020-2026H1", "validated": True},
    {"file": "consensus.json", "source": "Yahoo Finance / 媒体转引", "level": "C", "fetched": "2026-08-29", "period": "FY2026 预期", "validated": False},
]}

for name, obj in [("financials_WB", wb), ("financials_BIDU", bidu), ("financials_BILI", bili),
                  ("financials_MOMO", momo), ("financials_Kuaishou", kuaishou),
                  ("market_snapshot", market_snapshot), ("segments", segments),
                  ("platform_metrics", platform_metrics), ("consensus", consensus),
                  ("manifest", manifest)]:
    with open(os.path.join(DATA, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print("written", name)
