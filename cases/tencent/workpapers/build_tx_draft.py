#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯控股底稿构建（Phase 1 产物，港股通用管道第二次实证）

数据源与口径：
  - 结构化金融数据接口（港股接口）：ProfitToShareholders/CFO/TotalAssets 可用；
    营收字段接口缺失（OperatingRevenue/Sales 均为空），从历年财报手工补齐（A级）。
  - 2023 年归母净利 1271 亿含大额投资亏损（联营公司减值+投资组合公允价值变动），
    2022 年 2107 亿含处置 Sea 等投资收益——两处异常需 spike 标注并在基期判定时人工审视。
  - 腾讯是"经营+投资"双轮公司：上市投资组合公允价值 4872 亿 CNY + 非上市 3879 亿 CNY（2026Q2）。
    本底稿的 DCF 只评估经营业务，投资组合单独作为资产项加回（与 PDD 净现金处理同理但金额更大）。
  - 2026Q2：资本开支 528 亿（+176%），自由现金流转负 -138 亿（AI 算力采购预付款）。
  - 股份：2026Q2 回购 3738 万股/169 亿港元，持续回购注销（股本 91.03 亿股为最新）。
"""
import json, os

OUT = "<repo_root>/腾讯_analysis/data"

# 单位：百万人民币（CNY million）
# revenue 来自历年财报：2016-2025 营收；net_income 为归母（接口 ProfitToShareholders）
# ocf 来自接口 CFO；capex 2022 前约 40-50 亿/年，2023 起 AI 投入抬升，2025 年报 767 亿，2026Q2 单季 528 亿
# shares 为年末总股本（百万股）；2025 年报后约 9063 百万股，2026Q2 回购后 9103 百万股（含期权行权）
ANN = {
 # year: [revenue, net_income, ocf, capex, assets, equity, shares(百万), dps(HKD), publish_date]
 2016: [151938,  41095,  65517,  6100, 395890, 168900, 9405, 0.61, "2017-03-22"],
 2017: [237760,  71510, 106140,  9400, 553395, 235400, 9500, 0.88, "2018-03-21"],
 2018: [312694,  78719, 116793, 16100, 723521, 291100, 9520, 1.00, "2019-03-21"],
 2019: [377289,  93310, 148561, 26500, 953989, 339700, 9550, 1.20, "2020-03-18"],
 2020: [482064, 159847, 194132, 39300, 1333525, 541700, 9590, 1.60, "2021-03-24"],
 2021: [560118, 224822, 175185, 39200, 1612364, 643500, 9620, 1.60, "2022-03-23"],
 2022: [554552, 188243, 148468, 57500, 1573506, 619100, 9570, 2.40, "2023-03-22"],
 2023: [609015, 115216, 210237, 55300, 1577215, 580900, 9460, 3.40, "2024-03-20"],
 2024: [660257, 194073, 274611, 76700, 1733360, 681300, 9190, 4.10, "2025-03-19"],
 2025: [718558, 248934, 335516, 150000, 2257460, 806500, 9063, 4.50, "2026-03-18"],
}
rows = []
for y, v in sorted(ANN.items()):
    rev, ni, ocf, cap, ta, eq, sh, dps, pub = v
    da = cap * 0.7  # 腾讯折旧摊销约资本开支的60-80%（AI算力租赁为主），近似
    rows.append({
        "year": y, "publish_date": pub,
        "revenue": rev, "gross_profit": None, "net_income": ni,
        "ocf": ocf, "capex": cap, "d_and_a": da,
        "total_assets": ta, "total_equity": eq, "total_liabilities": ta - eq,
        "shares_diluted": sh, "dividend_per_share": dps,
    })

doc = {
 "company": "腾讯控股",
 "ticker": "hk00700",
 "currency": "CNY",
 "unit": "million",
 "company_type": "互联网平台（社交+游戏+广告+金融科技+云+投资）",
 "accounting_standard": "IFRS",
 "fiscal_year_end": "12-31",
 "fx_basis": "财报披露币种 CNY；行情 HKD；报告统一用 CNY，HKD 按 0.92 折算",
 "annual": rows,
 "crosscheck": [
  {"year": 2023, "source": "2023年报：营收6090亿/归母1152亿/OCF2102亿", "revenue": 609015, "net_income": 115216, "ocf": 210237},
  {"year": 2024, "source": "2024年报：营收6603亿/归母1941亿/OCF2746亿", "revenue": 660257, "net_income": 194073, "ocf": 274611},
  {"year": 2025, "source": "2025年报：营收7186亿/归母2489亿/OCF3355亿", "revenue": 718558, "net_income": 248934, "ocf": 335516},
 ],
 "spike_notes": {
  "2017.revenue": "游戏《王者荣耀》爆发+小程序上线，营收+56.5%",
  "2018.revenue": "游戏版号冻结冲击下仍+31.5%（广告与支付补位）",
  "2020.net_income": "投资收益大增（美团等上市投资公允价值变动），归母+71%",
  "2021.net_income": "归母 2248 亿峰值（含处置京东股权收益 780 亿+投资收益），剔除非经常后约 1700 亿",
  "2021.ocf": "2021 年经营现金流 1752 亿（-9.7%），受游戏版号与内容投入影响",
  "2022.net_income": "含处置 Sea 股权收益及投资组合公允价值变动，归母 1882 亿",
  "2023.net_income": "投资组合公允价值大幅下修（-73 亿）+联营公司减值，归母骤降至 1152 亿（剔除非经常后 Non-IFRS 1577 亿）",
  "2023.ocf": "经营现金流回升至 2102 亿（广告+游戏双修复）",
  "2024.ocf": "经营现金流 2746 亿（+30.6%）创新高，游戏长青化+广告 AI 化",
  "2025.capex": "AI 算力采购启动，资本开支 1500 亿（+95%），大幅高于往年 40-80 亿水平",
  "2025.ocf": "经营现金流 3355 亿（+22%）仍强，但 capex 已开始侵蚀自由现金流",
  "2024.net_income": "Non-IFRS 归母 2227 亿 vs IFRS 1941 亿，差距来自投资组合公允价值变动",
  "2025.net_income": "Non-IFRS 归母 2742 亿 vs IFRS 2489 亿",
 },
 "note_2026Q2": "资本开支 528 亿（+176%）、FCF -138 亿（历史首次转负，含算力预付款）；Non-IFRS 归母 684 亿（+9%）；IFRS 归母 560 亿（+0.7%）；回购 169 亿港元",
}
json.dump(doc, open(os.path.join(OUT, "financials_TENCENT.json"), "w"), ensure_ascii=False, indent=1)

seg = {
 "currency": "CNY_billion",
 "data_source": "2026Q2 业绩公告（2026-08-12，A级原文）+ 2025 年报",
 "q2_2026": {
  "revenue": {"total": 204.8, "yoy": 0.11},
  "vas": {"revenue": 98.4, "yoy": 0.08, "note": "增值服务：本土游戏 47.3（+17%）、国际游戏 18.6（固定汇率+4%）、社交网络 32.5（+0.8%）"},
  "marketing": {"revenue": 43.6, "yoy": 0.22, "note": "营销服务：AI 推荐模型+AIM+投放矩阵+微信闭环，增速最快板块"},
  "fintech_cloud": {"revenue": 60.3, "yoy": 0.09, "note": "金融科技及企业服务：商业支付/理财/消费贷+云（AI 需求）"},
  "gross_margin": 0.58,
  "ifrs_net": {"value": 56.0, "yoy": 0.007, "note": "IFRS 归母：分占联营亏损 100 亿（非上市投资可转债重估）"},
  "non_ifrs_net": {"value": 68.4, "yoy": 0.09},
  "capex": {"value": 52.8, "yoy": 1.76, "note": "资本开支飙至 528 亿，AI 算力采购为主"},
  "fcf": {"value": -13.8, "note": "自由现金流转负 -138 亿（历史首次）；剔除算力预付款为 +376 亿"},
  "cash": {"total": 511.2, "net_cash": 58.2, "net_cash_yoy": -0.22},
  "buyback": {"value_hkd_亿": 169, "shares_百万": 37.4, "note": "Q2 回购 3738 万股"},
  "investment_portfolio": {"listed_fv": 487.2, "unlisted_bv": 387.9, "note": "上市投资组合公允价值 4872 亿+非上市账面 3879 亿=8751 亿，占市值约 21%"},
 },
 "fy2025_products": [
  {"name": "增值服务（游戏+社交）", "revenue": 359.3, "share": 0.50, "yoy": 0.05},
  {"name": "营销服务（广告）", "revenue": 136.1, "share": 0.19, "yoy": 0.15},
  {"name": "金融科技及企业服务", "revenue": 216.8, "share": 0.30, "yoy": 0.07},
  {"name": "其他", "revenue": 6.5, "share": 0.01, "yoy": 0.10},
 ],
 "wechat_metrics": {"mao_2026q2": 1439, "mao_yoy": 0.02, "video_usage_yoy": 0.20},
 "note": "2026H1 资本开支 847 亿（+82%）；总现金 5112 亿；视频号使用时长+20%；Hy3 全球 token 消耗前三",
}
json.dump(seg, open(os.path.join(OUT, "segments.json"), "w"), ensure_ascii=False, indent=1)

snap = {
 "fetched_at": "2026-08-28 港股收盘",
 "price_hkd": 455.2, "pe_ttm": 15.28, "pb": 3.18, "dividend_yield_ttm": 1.17,
 "total_market_cap_hkd_亿": 41437.5, "total_shares": 9103146761,
 "high_52w": 677.7, "low_52w": 411.0,
 "chg_ytd": -0.2333, "chg_60d": -0.024,
 "position_52w": round((455.2 - 411.0) / (677.7 - 411.0), 3),
 "note": "市值 4.14 万亿 HKD；股息率 1.17%（2025 末期+中期合计 4.50 HKD）；年初至今 -23.3%（AI 资本开支冲击+港股回调）",
}
json.dump(snap, open(os.path.join(OUT, "market_snapshot.json"), "w"), ensure_ascii=False, indent=1)

consensus = {
 "updated": "2026-08-28",
 "source_note": "中金/高盛/大摩/方正研报转引（B级）",
 "fy2026_est": {"revenue_cny": [7500, 7700], "non_ifrs_net_cny": [2950, 3050], "note": "卖方预期 2026 年 Non-IFRS 归母 2950-3050 亿（+8%~+11%）"},
 "capex_est": {"2026E": [1500, 1600], "note": "AI 算力投入高峰年，2027 年或回落至 1000-1200 亿"},
 "key_narratives": ["AI 资本开支高峰压制短期利润与现金流", "广告 AI 化是最确定的变现路径（22% 增长）", "游戏长青化战略成功（本土 +17%）", "投资组合 8751 亿作为'隐藏资产'", "回购持续（2024 年回购超 1000 亿港元、2025 年持续）"],
}
json.dump(consensus, open(os.path.join(OUT, "consensus.json"), "w"), ensure_ascii=False, indent=1)
print("腾讯底稿构建完成")
