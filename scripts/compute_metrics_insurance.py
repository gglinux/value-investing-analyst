#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_metrics_insurance.py — 保险专属指标计算（v2.8 新增，metric-playbook 类型七）

适用主体：保险集团 / 寿险 / 财险（company_type=保险/保险集团/insurance）
定位：与 compute_metrics.py（实业）和 compute_metrics_bank.py（银行）平行的第三管道。
拒绝金融服务以外的类型进行计算。

输入保险专属底稿 schema（financials_<ticker>_insurance.json），字段为 metric-playbook 类型七
所定义的命门指标 + 支持图，单位建议人民币百万元：
{
  "company": "中国平安", "ticker": "601318", "currency": "CNY",
  "unit": "million", "company_type": "保险集团",
  "accounting_standard": "CAS", "fiscal_year_end": "12-31",
  "bourse": "SH",                     # SH/SZ/HK/NYSE 等，影响 P/EV 历史区间参考
  "annual": [
    {
      "year": 2025, "publish_date": "2026-03-27",
      "revenue": 1050506,                  # 营业收入（A股年报口径）
      "net_income": 134778,                # 归母净利润
      "operating_profit": 134415,          # 归母营运利润（平安特色，若披露）
      "total_assets": 13898471,            # 总资产
      "total_liabilities": 12482483,       # 总负债
      "total_equity": 1415988,             # 股东权益总计
      "equity_attr_parent": 1000419,       # 归母净资产
      "bvps": 55.25,                       # 每股净资产
      "roe": 0.140,                        # 加权平均 ROE（平安年报，官方口径）
      "roe_operating": 0.127,              # 营运 ROE（若披露）
      "shares_diluted": 18108,             # 总股本（百万股）
      "dividend_per_share": 2.70,          # 每股股息（全年）
      "embedded_value": 1504288,           # 集团内含价值（A级命门，必须引自年报）
      "nbv": 36897,                        # 一年新业务价值（扣持偿后，A级命门）
      "evps": 83.07,                       # 每股内含价值（A级命门）
      "roev": 0.112,                       # 内含价值营运回报率（若披露）
      "investment_return": 0.063,          # 综合投资收益率
      "investment_assumption": 0.040,      # 长期投资回报假设（EV 计算用）
      "discount_rate": 0.085,              # 风险贴现率（EV 计算用）
      "combined_ratio_pnc": 0.968,         # 财险综合成本率
      "solvency_ratio": 1.933,             # 集团综合偿付能力充足率
      "solvency_life": 1.757               # 寿险综合偿付能力充足率（若披露）
    }
  ],
  "crosscheck": [
    { "year": 2024, "source": "FY2024 年报 EV 分析 [E:filings/pingan-2024-annual.pdf]",
      "revenue": 1028925, "net_income": 126607, "embedded_value": 1422602, "nbv": 28534 }
  ],
  "spike_notes": {},
  "market": { "price": 55.82, "shares_outstanding": 18108 }
}

输出 metrics JSON：
- series：每年一行，含 EV/NBV/ROE/ROEV/COR/偿付能力/股息/P/EV 派生列
- chart_series：P/EV、NBV 增速、投资收益率 vs 假设差额、偿付能力走势
- valuation_inputs：P/EV 判定用输入（当前 EV / 每股 EV / P/EV 分位 / 股息率）
- alerts：强约束警报（利差收窄/NBV 连续负增长/偿付能力跌破红线/EV 缩水）
用法：
    python3 compute_metrics_insurance.py <financials_insurance.json> -o metrics.json
"""
import argparse
import json
import sys

ALERTS = []


def alert(text):
    ALERTS.append(text)


def ins_compute(data):
    ctype = str(data.get("company_type") or "").lower()
    INS = {"insurance", "保险", "保险集团", "寿险", "财险",
           "insurance_group", "life", "p&c"}
    if ctype not in INS:
        sys.exit(f"compute_metrics_insurance 仅适用于保险股，收到 company_type='{data.get('company_type')}'")

    rows = sorted(data.get("annual", []), key=lambda r: r.get("year") or 0)
    if len(rows) < 5:
        sys.exit(f"保险底稿年度数 {len(rows)} < 5，保险专属指标需 5 年以上序列才能判断 NBV 趋势与利差中枢")

    market = data.get("market") or {}
    px = market.get("price")
    sh = market.get("shares_outstanding") or (rows[-1].get("shares_diluted"))

    series, cs_years = [], []
    cs_ev, cs_nbv, cs_nbv_g, cs_roe, cs_roev = [], [], [], [], []
    cs_inv, cs_assump, cs_iy_spread, cs_dividend, cs_solv = [], [], [], [], []

    for i, r in enumerate(rows):
        y = r["year"]
        cs_years.append(y)

        ev = r.get("embedded_value")
        nbv = r.get("nbv")
        evps = r.get("evps")
        bvps = r.get("bvps")
        roe = r.get("roe")
        roev = r.get("roev")
        inv_ret = r.get("investment_return")
        inv_assump = r.get("investment_assumption")
        cori = r.get("combined_ratio_pnc")
        solv = r.get("solvency_ratio")
        dps = r.get("dividend_per_share")

        # NBV 增速
        prev_nbv = rows[i - 1].get("nbv") if i else None
        nbv_g = (nbv - prev_nbv) / abs(prev_nbv) if (nbv is not None and prev_nbv and prev_nbv != 0) else None
        # 利差 = 综合投资收益率 - 长期投资假设（核心质量指标：正利差产生浮存金收益）
        iy_spread = inv_ret - inv_assump if (inv_ret is not None and inv_assump is not None) else None
        # P/EV
        pev = px / evps if (px and evps) else None

        # 承保盈亏（财险）：COR 差值
        uw_margin = 1 - cori if cori is not None else None

        # 每股股息覆盖：EV 营运利润 / 总股本 / DPS
        div_cover_ev = None
        if r.get("operating_profit") and r.get("shares_diluted") and dps:
            op_ps = r["operating_profit"] / r["shares_diluted"]
            div_cover_ev = op_ps / dps

        # EV 增速与按期初回报 + NBV 的内在分解（若前一年 EV 可得）
        ev_g = None
        if i:
            prev_ev = rows[i - 1].get("embedded_value")
            if prev_ev and ev:
                ev_g = ev / prev_ev - 1

        row_out = {"year": y, "embedded_value": ev, "nbv": nbv, "ev_growth": ev_g,
                   "nbv_growth": nbv_g, "roe": roe, "roev": roev,
                   "investment_return": inv_ret, "investment_assumption": inv_assump,
                   "iy_spread": iy_spread, "uw_margin_pnc": uw_margin,
                   "solvency_ratio": solv, "dividend_per_share": dps,
                   "div_cover_by_op": div_cover_ev, "bvps": bvps, "evps": evps, "pev": pev}
        series.append(row_out)

        for store, val in [(cs_ev, ev), (cs_nbv, nbv), (cs_nbv_g, nbv_g), (cs_roe, roe),
                           (cs_roev, roev), (cs_inv, inv_ret), (cs_assump, inv_assump),
                           (cs_iy_spread, iy_spread), (cs_dividend, dps), (cs_solv, solv)]:
            store.append(val)

    # ── 强警报 ──────────────────────────────────────────────
    # 1. 利差持续收窄：post-2020 平均利差 < 0.5%
    recent_spr = [s["iy_spread"] for s in series[-3:] if s["iy_spread"] is not None]
    if recent_spr and sum(recent_spr) / len(recent_spr) < 0.005:
        alert(f"利差警报：近 3 年平均（综合投资收益率−假设）仅 "
              f"{sum(recent_spr)/len(recent_spr):.2%} ——投资假设可能过于乐观，实际回报难以持续覆盖")

    # 2. NBV 连续负增长
    gns = [s["nbv_growth"] for s in series[-3:] if s["nbv_growth"] is not None]
    if len(gns) >= 2 and all(g < -0.05 for g in gns[-2:]):
        alert("NBV 连续两年负增长——新业务创造能力衰减，P/EV 目标值应下修")

    # 3. 偿付能力红线
    solvs = [s["solvency_ratio"] for s in series if s["solvency_ratio"] is not None]
    if solvs and solvs[-1] < 1.5:
        alert(f"偿付能力警报：最新集团综合偿付能力充足率 "
              f"{solvs[-1]:.0%} < 150%，进入监管关注区")

    # 4. EV 缩水（两年下滑）
    evs = [s["embedded_value"] for s in series if s["embedded_value"] is not None]
    if len(evs) >= 3 and evs[-2] < evs[-3] and evs[-1] < evs[-2]:
        alert("内含价值连续缩水——有效业务价值减值或假设调整损害长期价值")

    # 5. 股息覆盖率警戒：营运利润 / DPS < 1.5（高股息承诺的可持续性）
    latest = series[-1]
    if latest["div_cover_by_op"] and latest["div_cover_by_op"] < 1.5:
        alert(f"股息覆盖警报：营运利润 / 总股本 / DPS 覆盖率 {latest['div_cover_by_op']:.1f} < 1.5——"
              "高分红依赖投资收益与 EV 释放，非纯承保盈利支撑")

    out = {
        "company": data.get("company"),
        "ticker": data.get("ticker"),
        "company_type": data.get("company_type"),
        "series": series,
        "chart_series": {
            "years": cs_years,
            "embedded_value": cs_ev,
            "nbv": cs_nbv,
            "nbv_growth": cs_nbv_g,
            "roe": cs_roe,
            "roev": cs_roev,
            "investment_return": cs_inv,
            "investment_assumption": cs_assump,
            "iy_spread": cs_iy_spread,
            "dividend_per_share": cs_dividend,
            "solvency_ratio": cs_solv,
        },
        "valuation_inputs": {
            "price": px,
            "shares_outstanding": sh,
            "latest_ev": latest["embedded_value"],
            "latest_evps": latest["evps"],
            "latest_nbv": latest["nbv"],
            "latest_roe": latest["roe"],
            "latest_roev": latest["roev"],
            "pev": latest["pev"],
            "iy_spread_latest": latest["iy_spread"],
            "dividend_per_share": latest["dividend_per_share"],
            "dividend_yield_hint": (latest["dividend_per_share"] / px) if px else None,
        },
        "alerts": ALERTS,
        "method_notes": (
            "保险股专属管道（v2.8）：估值主框架用 P/EV 判定法 + EV 增长贴现 + 股息模型三轨；"
            "通用 compute_metrics 的 ROIC/OE/DCF 不适用（利差产生浮存金收益的特殊生意模式）。"
            "P/EV 参考区间需按 bourse 确定：A股 0.5-1.2，港股 0.4-0.9，美股 0.8-1.5。"
            "利差 Alert 线是（investment_return - investment_assumption）近 3 年平均 < 0.5%。"),
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    data = json.load(open(args.input))
    result = ins_compute(data)
    if args.output:
        json.dump(result, open(args.output, "w"), ensure_ascii=False, indent=1)
        print(f"已写入 {args.output}；警报 {len(ALERTS)} 条")
        for a in ALERTS:
            print("  [ALERT]", a)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
