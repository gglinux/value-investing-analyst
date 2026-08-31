#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""泡泡玛特底稿构建（Phase 1 产物，一次性）

数据源与口径（港股首次实证要点）：
  - 结构化金融数据接口港股接口：字段为港币口径（按期末汇率换算），revenue 字段为空。
    汇率反推体系：各年 接口港币值/该年末 CNY→HKD 汇率（2018:1.1166, 2019:1.1166, 2020:1.1846,
    2021:1.2136, 2022:1.1189, 2023:1.1050, 2024:1.0816, 2025:1.1068），
    已用存货/总资产/归母权益与披露值交叉验证，误差 <1%（A 级年报为人民币列报）。
  - A 级原文：2025 年度业绩公告（披露易 2026-03-25），revenue 371.20 亿/归母 127.76 亿/
    经营现金流 108.65 亿/毛利率 72.1% 双源核对一致；2024 年数据公告内并列披露。
  - 收入/归母 2018-2023 取自历年年报披露值（B 级转引，与接口净利率口径反推一致）。
  - 经营现金流 2018-2023 由接口 CFO/汇率反推；2018-2022 未经年报原文逐项核对（招股书年代久远），
    已在 crosscheck 注明。
  - fx_basis：财报 CNY，行情 HKD；估值输出人民币每股价值，按 0.92 换算港币对比现价。
"""
import json, os

OUT = "<repo_root>/泡泡玛特_analysis/data"

# 单位：百万，人民币
ANN = {
 # year: [revenue, gross_profit, net_income(归母), ocf, capex, assets, equity(归母), shares(百万), dps(HKD), publish_date]
 2018: [  514.5,   298.0,   102,   179,  36.3,  411.7,  223.6, 1320, None, "2020-12-01"],
 2019: [ 1688.4,  1093.5,   451,   503, 105.0, 1066.3,  592.6, 1320, None, "2020-12-01"],
 2020: [ 2513.5,  1594.2,   523,   706, 176.5, 6992.4, 6148.0, 1387, 0.149, "2021-03-26"],
 2021: [ 4490.7,  2758.6,   854,   785, 290.0, 8389.0, 6872.0, 1402, 0.18, "2022-03-28"],
 2022: [ 4617.4,  2654.3,   476,   892, 266.3, 8583.9, 6967.0, 1360, 0.087, "2023-03-29"],
 2023: [ 6301.0,  3864.1,  1082,  1988, 323.7, 9955.2, 7759.3, 1345, 0.28, "2024-03-20"],
 2024: [13037.7,  8707.8,  3125,  4954, 372.0, 14870.7, 10683.5, 1334, 0.8796, "2025-03-26"],
 2025: [37120.1, 26764.9, 12776, 10865, 985.5, 32101.4, 22277.7, 1329.6, None, "2026-03-25"],
}
rows = []
for y, v in sorted(ANN.items()):
    rev, gp, ni, ocf, cap, ta, eq, sh, dps, pub = v
    rows.append({
        "year": y, "publish_date": pub,
        "revenue": rev, "gross_profit": gp, "net_income": ni,
        "ocf": ocf, "capex": cap,
        "total_assets": ta, "total_equity": eq,
        "total_liabilities": round(ta - eq, 1),
        "shares_diluted": sh,
        "dividend_per_share_hkd": dps,  # 港币每股（含中期+末期），2025 年度末期息待公告
    })

doc = {
 "company": "泡泡玛特",
 "ticker": "hk09992",
 "currency": "CNY",
 "unit": "million",
 "company_type": "品牌消费",
 "accounting_standard": "IFRS",
 "fiscal_year_end": "12-31",
 "fx_basis": "财报CNY；行情HKD；估值输出CNY每股价值，按0.92(HKD/CNY)折算对比现价158.4HKD≈145.7CNY",
 "annual": rows,
 "crosscheck": [
  {"year": 2023, "source": "2023年报（披露易，历史披露值）", "revenue": 6301.0, "net_income": 1082, "ocf": 1988},
  {"year": 2024, "source": "2025年度业绩公告（披露易2026-03-25，并列披露2024）", "revenue": 13037.7, "net_income": 3125.5, "ocf": 4954.0},
  {"year": 2025, "source": "2025年度业绩公告（披露易2026-03-25）", "revenue": 37120.1, "net_income": 12775.7, "ocf": 10865.2},
 ],
 "spike_notes": {
  "2019.revenue": "上市后渠道扩张+MOLLY成熟期放量（+227%）",
  "2020.ocf": "上市募资到账与门店扩张（港股经营现金流波动）",
  "2021.revenue": "疫情期逆势扩张，线上抽盒机放量（+79%）",
  "2022.net_income": "疫情封控+存货减值，利润腰斩（-44%）",
  "2023.revenue": "复苏+DIMOO/SP放量（+36%）",
  "2024.revenue": "LABUBU爆发元年，收入+106.8%",
  "2024.net_income": "LABUBU爆款+海外铺开，归母+188.8%",
  "2025.revenue": "LABUBU全球现象级爆发，收入+184.7%",
  "2025.net_income": "归母+308.8%，净利率35.1%历史峰值（周期高位信号）",
  "2025.ocf": "净利暴增但存货+259%吞噬营运资本，现金转换比降至0.85",
  "2025.capex": "海外产能/乐园/物流中心投入，capex 9.86亿（+165%）",
 },
}
json.dump(doc, open(os.path.join(OUT, "financials_POPMART.json"), "w"), ensure_ascii=False, indent=1)

# 分部底稿：IP 与地区（人民币，亿元）；2025 由接口港币分部/1.1068 反推+公告核对，2026H1 取中报
seg = {
 "currency": "CNY_yi",
 "fy2025_ips": [
  {"name": "THE MONSTERS (LABUBU家族)", "revenue": 141.45, "share": 0.381, "yoy": 3.86},
  {"name": "SKULLPANDA", "revenue": 35.39, "share": 0.095, "yoy": 1.42},
  {"name": "DIMOO", "revenue": 27.78, "share": 0.075, "yoy": 0.61},
  {"name": "CRYBABY", "revenue": 24.14, "share": 0.065, "yoy": 2.16},
  {"name": "MOLLY", "revenue": 33.30, "share": 0.090, "yoy": 0.40},
  {"name": "HIRONO", "revenue": 20.55, "share": 0.055, "yoy": 0.83},
  {"name": "其他17+个IP", "revenue": 88.6, "share": 0.239, "yoy": None},
 ],
 "fy2025_regions": [
  {"name": "中国内地", "revenue": 208.5, "share": 0.562, "yoy": 1.41},
  {"name": "港澳台及海外", "revenue": 162.7, "share": 0.438, "yoy": 2.92},
 ],
 "fy2025_categories": [
  {"name": "毛绒", "revenue": 187.1, "share": 0.504, "yoy": 5.61},
  {"name": "手办", "revenue": 120.2, "share": 0.324, "yoy": 0.73},
  {"name": "MEGA/衍生品等", "revenue": 63.9, "share": 0.172, "yoy": None},
 ],
 "h1_2026_ips": [
  {"name": "THE MONSTERS", "revenue": 44.54, "yoy": -0.075},
  {"name": "星星人", "revenue": 26.50, "yoy": 5.806},
  {"name": "CRYBABY", "revenue": 16.33, "yoy": 0.34},
  {"name": "DIMOO", "revenue": 16.19, "yoy": 0.465},
  {"name": "SKULLPANDA", "revenue": 15.51, "yoy": 0.271},
  {"name": "HIRONO", "revenue": 10.09, "yoy": 0.385},
  {"name": "MOLLY", "revenue": 9.01, "yoy": -0.336},
 ],
 "h1_2026_regions": [
  {"name": "中国内地", "revenue": 122.01, "yoy": 0.473},
  {"name": "亚太", "revenue": 25.8, "yoy": -0.097},
  {"name": "美洲", "revenue": 18.9, "yoy": 0.0},
  {"name": "欧洲及其他", "revenue": 5.1, "yoy": -0.165},
 ],
 "note": "1H26 LABUBU家族首次负增长、星星人+580.6%接棒；汇兑损失7.2亿拖累净利。2026年回购20-50亿HKD计划进行中",
}
json.dump(seg, open(os.path.join(OUT, "segments.json"), "w"), ensure_ascii=False, indent=1)

snap = {
 "fetched_at": "2026-08-28 收盘", "source": "行情接口（A级）",
 "price_hkd": 158.4, "price_cny_est": round(158.4*0.92, 1),
 "fx_hkd_cny": 0.92,
 "total_market_cap_yi_hkd": 2109.5, "total_market_cap_yi_cny": round(2109.5*0.92, 1),
 "pe_ttm": 13.84, "pb": 7.95, "dividend_yield_ttm": 0.0173,
 "high_52w_hkd": 328.875, "low_52w_hkd": 137.375,
 "position_52w": round((158.4-137.375)/(328.875-137.375), 3),
 "amount_20d_avg_note": "日均成交约10亿港元级别，流动性充裕",
 "total_shares": 1331779203,
 "drawdown_note": "2025-08-29历史高点337.075，最大回撤-90.0%（上市以来两次）；距今-51.7%",
}
json.dump(snap, open(os.path.join(OUT, "market_snapshot.json"), "w"), ensure_ascii=False, indent=1)

consensus = {
 "updated": "2026-08-28", "source_note": "太平洋证券(08-26)/国盛证券(08-25)/东方财富(08-25)，B级",
 "net_profit_yoy": {"2026E": [-0.13, -0.15, -0.17], "2027E": [0.16, 0.13, 0.21], "2028E": [0.17, 0.12, 0.15]},
 "net_profit_yi": {"2026E": [110, 105.96, 108], "2027E": [127.8, 127.99, 122], "2028E": [149.6, 147.42, 137]},
 "narrative": "卖方一致预期2026年归母净利负增长（-13%~-17%）：LABUBU高基数+海外调整；2027-2028恢复中低双位数增长；评级全员买入/增持，隐含逻辑是'IP矩阵平滑单IP周期'",
 "confidence": "低-中：潮玩需求预测误差天然大，卖方预测分歧区间窄反而可疑",
}
json.dump(consensus, open(os.path.join(OUT, "consensus.json"), "w"), ensure_ascii=False, indent=1)
print("底稿构建完成")
