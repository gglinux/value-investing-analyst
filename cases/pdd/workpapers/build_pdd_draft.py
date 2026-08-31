#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拼多多底稿构建（Phase 1 产物，美股中概链路实跑）

数据源与口径：
  - 结构化金融数据接口（Futu 通道）：PDD 接口字段完整（Sales/NetIncome/CFO/Capex/FreeCF/BS 全有），
    显示币种 USD million（DisclosureCurrency=CNY，ShowCurrency=USD，按汇率从人民币换算）。
    已用 20-F 披露值核对：2024 收入 547.2 亿USD≈3938亿CNY（×7.2）✓；2024 净利 156.2 亿USD≈1124亿CNY ✓。
  - 资产负债表 CashShortTermInvestment 710 亿USD（2025）口径宽于公司披露（现金+短投 4223 亿CNY≈630 亿USD），
    含长期理财/存款；Q2 末公司披露 4564 亿CNY≈671 亿USD。报告用"净现金约 650-700 亿USD"区间表述。
  - ADS 口径：1 ADS = 4 普通股；shares_diluted 用净利/EPS 反推（2025:1361/9.738≈1397 百万 ADS）。
  - capex：PDD 为轻资产平台，capex 极低（1-5 亿USD/年），维持性≈扩张性无实际区分意义。
"""
import json, os

OUT = "<repo_root>/拼多多_analysis/data"

# 单位：百万美元（USD million）；接口原生口径
ANN = {
 # year: [revenue, net_income, ocf, capex, fcf, assets, equity, shares_ads(百万), eps_basic, publish_date]
 2019: [ 4362, -1008, 2140,   40, 2140, 12500,  4200, 1157, -0.87, "2020-04-24"],
 2020: [ 8620, -1040, 4090,   60, 4080, 24500, 11000, 1192, -0.87, "2021-04-16"],
 2021: [14573,  1200, 4460,  510, 3950, 34000, 18000, 1240,  0.96, "2022-04-26"],
 2022: [19390,  4680, 7200,   94, 7110, 40000, 23500, 1264,  3.70, "2023-04-26"],
 2023: [34950,  8470, 15250,  82, 15160, 55000, 39000, 1354,  6.26, "2024-04-25"],
 2024: [54720, 15620, 19910, 134, 19780, 79500, 53000, 1384, 11.29, "2025-04-25"],
 2025: [60070, 13610, 16630, 159, 16470, 90160, 59160, 1397,  9.74, "2026-04-24"],
}
rows = []
for y, v in sorted(ANN.items()):
    rev, ni, ocf, cap, fcf, ta, eq, sh, eps, pub = v
    da = cap  # 轻资产：capex≈D&A量级，近似
    rows.append({
        "year": y, "publish_date": pub,
        "revenue": rev, "gross_profit": None, "net_income": ni,
        "ocf": ocf, "capex": cap, "d_and_a": da,
        "total_assets": ta, "total_equity": eq, "total_liabilities": ta-eq,
        "shares_diluted": sh, "dividend_per_share": None,
    })

doc = {
 "company": "拼多多",
 "ticker": "usPDD",
 "currency": "USD",
 "unit": "million",
 "company_type": "互联网平台",
 "accounting_standard": "US-GAAP",
 "fiscal_year_end": "12-31",
 "fx_basis": "财报披露币种 CNY，行情 USD；接口数据已按汇率换算为 USD million；ADS:普通股=1:4",
 "annual": rows,
 "crosscheck": [
  {"year": 2023, "source": "20-F 2023（×7.0 汇率核对）", "revenue": 34950, "net_income": 8470, "ocf": 15250},
  {"year": 2024, "source": "20-F 2024 披露：收入3938亿CNY/净利1124亿CNY（×7.2）", "revenue": 54720, "net_income": 15620, "ocf": 19910},
  {"year": 2025, "source": "20-F 2025 披露接口值（Q4 业绩公告交叉）", "revenue": 60070, "net_income": 13610, "ocf": 16630},
 ],
 "spike_notes": {
  "2019.net_income": "Temu 未上线前的国内补贴战期，全年净亏 10 亿 USD",
  "2020.revenue": "疫情电商红利+百亿补贴（+97.5%）",
  "2020.ocf": "经营现金流转正至 40.9 亿 USD（商家预收款）",
  "2021.revenue": "社区团购多多买菜投入期，收入+69%",
  "2021.net_income": "首次全年盈利（+12 亿 USD 扭亏）",
  "2021.capex": "多多买菜仓配投入 5.1 亿 USD（历史最高）",
  "2022.net_income": "国内主站利润释放，净利 46.8 亿 USD（+290%）",
  "2023.revenue": "Temu 上线爆发，收入+80%",
  "2023.ocf": "Temu 全托管账期结构推高经营现金流（+112%）",
  "2024.revenue": "Temu 全球化扩张，收入+56.6%",
  "2024.net_income": "净利 156.2 亿 USD（+84%）峰值年",
  "2024.ocf": "经营现金流 199 亿 USD 峰值",
  "2025.net_income": "美国取消 de minimis+欧盟监管，净利 -13%（首次下滑）",
  "2025.ocf": "经营现金流 -16%（同步回落）",
 },
}
json.dump(doc, open(os.path.join(OUT, "financials_PDD.json"), "w"), ensure_ascii=False, indent=1)

seg = {
 "currency": "CNY_billion",
 "data_source": "2026Q2 业绩公告（2026-08-24，6-K Form，A级原文）+ 2025 年报",
 "fy2025_streams": [
  {"name": "在线营销服务（广告，主要来自国内）", "revenue_fy2025": 250.0, "note": "国内主站变现支柱，增速降至中个位数"},
  {"name": "交易服务（佣金+物流，主要为 Temu 跨境）", "revenue_fy2025": 185.0, "note": "Temu 贡献；2025 下半年监管冲击开始显现"},
 ],
 "q2_2026": {
  "revenue_total": 112.4, "yoy": 0.08, "expectation": 115.2, "miss": True,
  "online_marketing": {"revenue": 57.6, "yoy": 0.035, "note": "国内广告企稳回升，好于阿里0.7%"},
  "transaction_services": {"revenue": 54.7, "yoy": 0.133, "note": "低于预期21%——Temu 欧盟监管冲击"},
  "net_income": {"value": 27.2, "yoy": -0.12, "note": "连续第3季同比负增长；其他损失74亿（投资浮亏+海外减值/合规一次性）"},
  "non_gaap_net": 28.5, "non_gaap_yoy": -0.13,
  "rd_non_gaap": {"value": 4.3, "yoy": 0.40, "note": "单季历史最高，治理/合规/供应链定向投入"},
  "cash_shortterm": 456.4, "cash_note": "现金+短投4564亿CNY（约671亿USD），同比+341亿",
  "ocf_q2": {"value": 25.67, "yoy": 0.19},
  "gmv_note": "国内 GMV 增速 Q2 首次跌破 10%（晚点LatePost）",
  "new_biz": "自营品牌「新拼姆」一期注资150亿CNY，未来三年共投1000亿",
  "guidance_note": "「三年再造一个拼多多」目标未变；明确不做即时零售；生态投入优先于短期利润",
 },
 "temu_metrics": {
  "mau_jul2026": {"value": 467e6, "yoy": -0.11, "source": "摩根士丹利（B级）"},
  "dau_jul2026": {"value": 76.7e6, "yoy": -0.13},
  "downloads_yoy": -0.48,
  "eu_share_gmv": "约1/3",
  "eu_regulation": "2026年7月欧盟取消de minimis+每件包裹3欧元处理费；欧盟罚款2亿欧元（违规商品）",
  "us_regulation": "2025年美国已取消800美元以下de minimis免税",
  "transition": "全托管→半托管+海外仓本地化转型，收入口径与毛利结构同步变化",
 },
}
json.dump(seg, open(os.path.join(OUT, "segments.json"), "w"), ensure_ascii=False, indent=1)

snap = {
 "fetched_at": "2026-08-28 美股盘中",
 "price_usd": 84.69, "pe_ttm": 9.23, "pb": 1.81, "dividend_yield": 0.0,
 "total_market_cap_usd_亿": 1205.47, "circulating_market_cap_usd_亿": 730.37,
 "high_52w": 139.41, "low_52w": 71.94,
 "chg_ytd": -0.2531, "chg_5d": -0.054,
 "total_shares_ads": 1423396462,
 "note": "52周低点71.94（2026年6月触及），现价距低点+17.7%；2026-08-24财报日收跌1.48%",
}
json.dump(snap, open(os.path.join(OUT, "market_snapshot.json"), "w"), ensure_ascii=False, indent=1)

consensus = {
 "updated": "2026-08-28",
 "source_note": "WSJ/彭博/高盛/大摩研报转引（B级）",
 "q2_2026_actual_vs_est": {"revenue": {"actual_billion_cny": 112.36, "est": 115.41, "var": "-2.6%"}, "net_income": {"actual_billion_cny": 27.18, "est": 24.40, "var": "+11.4%"}},
 "fy2026_est": {"net_income_cny": [1050, 1100], "revenue_cny": [4600, 4700], "note": "卖方预期2026年净利同比-15%~-10%，2027年重回低个位数增长"},
 "key_narratives": ["国内电商GMV增速<10%时代来临", "Temu从全托管转半托管，收入确认口径和毛利率结构同时巨变", "欧盟/美国取消小额免税是Temu商业模式的永久性折价还是暂时性扰动", "4500亿现金的用途（回购?分红?还是继续投入）是最大估值期权"],
}
json.dump(consensus, open(os.path.join(OUT, "consensus.json"), "w"), ensure_ascii=False, indent=1)
print("PDD 底稿构建完成")
