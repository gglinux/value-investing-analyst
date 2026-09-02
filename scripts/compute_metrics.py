#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_metrics.py — 价值投资分析统一口径指标计算器（Phase 2 强制使用）

目的：ROIC / ROIIC / Owner Earnings / 每股口径 等关键指标必须由本脚本统一计算，
禁止模型在对话中心算或各处用不同口径，保证跨公司、跨次分析可比、可复现。

用法：
    python3 compute_metrics.py <financials.json> [-o output.json]

输入 JSON 格式（数据底稿标准格式，单位统一为百万，缺失字段填 null）：
{
  "company": "A公司",
  "ticker": "0000.HK",
  "currency": "CNY",
  "unit": "million",
  "tax_rate": 0.25,                  # 可选，缺省 0.25；用于 NOPAT
  "annual": [                        # 按年份升序
    {
      "year": 2015,
      "revenue": 1000.0,             # 营业收入
      "ebit": 200.0,                 # 息税前利润（可选，缺省用 net_income + interest_expense 近似）
      "net_income": 150.0,           # 归母净利润
      "interest_expense": 10.0,      # 利息费用（可选）
      "d_and_a": 50.0,               # 折旧摊销
      "capex": 80.0,                 # 资本开支（正数）
      "maintenance_capex": null,     # 维持性资本开支（管理层披露口径；null 时按 heuristic 估算）
      "growth_capex": null,          # 扩张性资本开支（披露或估算口径；与 capex 同正数约束）
      "wc_change": -5.0,             # 营运资本变动（增加为正）
      "ocf": 180.0,                  # 经营现金流净额
      "total_equity": 800.0,         # 归母净资产
      "total_debt": 300.0,           # 有息负债
      "cash": 200.0,                 # 货币资金+现金等价物
      "goodwill": 50.0,              # 商誉（可选）
      "shares_diluted": 100.0,       # 摊薄股本（百万股）
      "dividends_paid": 40.0,        # 现金分红（可选）
      "buyback": 0.0,                # 回购金额（可选）
      "equity_raised": 0.0           # 增发募资（可选）
    }, ...
  ]
}

输出：逐年指标序列 + 长期汇总（CAGR 总量 vs 每股、ROIC 均值带、ROIIC、
资本配置流向、稀释追踪、警报清单）。所有口径定义随结果一并输出，供报告附录引用。
"""
import argparse
import json
import math
import sys

DEFAULT_TAX = 0.25

DEFINITIONS = {
    "NOPAT": "EBIT × (1 − 税率)；EBIT 缺失时用 归母净利润 + 利息费用×(1−税率) 近似，并在 warnings 中标注",
    "InvestedCapital": "归母净资产 + 有息负债 − 货币资金（剔除超额现金的简化口径）",
    "ROIC": "NOPAT ÷ 期初期末平均 InvestedCapital",
    "ROIIC": "滚动3年：ΔNOPAT ÷ Δ平均InvestedCapital（增量资本回报率，衡量增长质量）",
    "OwnerEarnings": "净利润 + 折旧摊销 − 维持性capex − 营运资本增加；维持性capex 缺失时兜底用近5年 D&A 均值近似（warnings 标注）",
    "FCF": "经营现金流净额 − 资本开支",
    "ROE": "归母净利润 ÷ 期初期末平均归母净资产",
    "每股口径": "全部使用当年摊薄股本；CAGR 用期初/期末值几何年化",
    "NormalizedEarnings": "正常化盈利 = 全期平均利润率 × 最新一期收入。用于消除周期位置对基期的扭曲；"
                          "另给中位数口径（mid-cycle）作交叉验证。周期性判定见 normalization.cyclicality",
    "周期位置": "最新一期利润率 ÷ 全期平均利润率。>1.25 判为周期高位（当期利润不可直接外推），"
                "<0.75 判为周期低位（当期利润低估长期能力）",
    "Capex拆分": "维持性 vs 扩张性资本开支。底稿可给 maintenance_capex（披露口径）或 growth_capex（扩张性）；"
                 "两者都给则 capex_split 输出确定性拆分。缺省时按收入增速启发式估算并标注 capex_split_basis",
    "真实FCF区间": "fcf_true_range = [ocf−全部capex（悲观，视同全是保命钱）, ocf−维持性capex（乐观，扩张性是可裁量再投资）]。"
                    "真实盈利含金量夹在此区间；扩张性占比高且 ROIIC 高 → 偏乐观端，维持性占比高 → 偏悲观端",
}


def safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def cagr(begin, end, years):
    if begin is None or end is None or years <= 0:
        return None
    if begin <= 0 or end <= 0:
        return None  # 负基数不做几何年化，报告中改用文字描述
    return (end / begin) ** (1.0 / years) - 1.0


def get(row, key):
    v = row.get(key)
    return float(v) if v is not None else None


def median(vals):
    s = sorted(v for v in vals if v is not None)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def compute_normalization(series):
    """正常化盈利：消除周期位置对估值基期的扭曲。

    价值投资铁律——周期高位的当期利润不可直接作为 DCF 基期外推。
    输出三个口径的基期供 Phase 4 对照：
      current      当期实际（周期高位时会系统性高估内在价值）
      normalized   全期平均利润率 × 最新收入（主口径）
      mid_cycle    全期中位数利润率 × 最新收入（抗极端值交叉验证）
    """
    latest = series[-1]
    rev_latest = latest.get("revenue")
    if rev_latest is None or rev_latest <= 0:
        return None

    net_margins = [s["net_margin"] for s in series if s.get("net_margin") is not None]
    oe_margins = [safe_div(s.get("owner_earnings"), s.get("revenue")) for s in series]
    oe_margins = [m for m in oe_margins if m is not None]
    if len(net_margins) < 5:
        return {"status": "数据不足（利润率样本 < 5 年），不做正常化，估值须在报告中说明该限制"}

    avg_nm = sum(net_margins) / len(net_margins)
    med_nm = median(net_margins)
    cur_nm = latest.get("net_margin")

    # 均值扭曲年检测（实证教训：PDD 早年亏损、腾讯/伊利减值年）——
    # 序列中存在亏损年或净利 |同比|>50% 的异常年时，全期平均利润率被结构性拉偏，
    # normalized 口径不再是"周期中枢"而是"被历史事故污染的均值"。
    # 此时必须双轨输出：主轨正常化 + 交叉轨当期，由分析师在报告中并列论证。
    distortion_years = []
    ni_seq = [(s["year"], s.get("net_income")) for s in series]
    for i, (y, ni) in enumerate(ni_seq):
        if ni is not None and ni < 0:
            distortion_years.append({"year": y, "reason": "亏损年"})
        elif i > 0 and ni is not None and ni_seq[i - 1][1] not in (None, 0):
            chg = ni / ni_seq[i - 1][1] - 1.0
            if abs(chg) > 0.5:
                distortion_years.append(
                    {"year": y, "reason": f"净利同比 {chg:+.0%}（疑似减值/一次性损益/爆发年）"})
    mean_distorted = bool(distortion_years)

    avg_oem = sum(oe_margins) / len(oe_margins) if oe_margins else None
    med_oem = median(oe_margins) if oe_margins else None

    ratio = safe_div(cur_nm, avg_nm)
    # 亏损与拐点期必须单独处理：净利率为负时比值失去周期含义
    # （负÷正会得到负数，若按数值大小判定会被误判为"周期低位"）；
    # 全期均值为负说明长期不赚钱，正常化本身无意义。
    loss_case = None
    if cur_nm is not None and cur_nm < 0:
        loss_case = "当期亏损"
    elif avg_nm <= 0:
        loss_case = "全期平均亏损"

    if loss_case:
        cyc = f"{loss_case}（不适用周期正常化）"
    elif ratio is None:
        cyc = "无法判定"
    elif ratio > 1.25:
        cyc = "周期高位"
    elif ratio < 0.75:
        cyc = "周期低位"
    else:
        cyc = "中性区间"

    shares = latest.get("shares_diluted")
    out = {
        "status": "ok",
        "basis_period": f"{series[0]['year']}–{latest['year']}（{len(net_margins)} 年利润率样本）",
        "latest_revenue": rev_latest,
        "net_margin_latest": cur_nm,
        "net_margin_avg": avg_nm,
        "net_margin_median": med_nm,
        "margin_ratio_latest_vs_avg": ratio,
        "cyclicality": cyc,
        "net_income_current": latest.get("net_income"),
        "net_income_normalized": avg_nm * rev_latest,
        "net_income_mid_cycle": med_nm * rev_latest if med_nm is not None else None,
        "oe_margin_avg": avg_oem,
        "oe_current": latest.get("owner_earnings"),
        "oe_normalized": avg_oem * rev_latest if avg_oem is not None else None,
        "oe_mid_cycle": med_oem * rev_latest if med_oem is not None else None,
        "shares_diluted": shares,
        "mean_distortion": {
            "distorted": mean_distorted,
            "years": distortion_years,
            "note": ("序列含亏损年/剧烈波动年，全期平均利润率被结构性拉偏——"
                     "normalized 口径偏离周期中枢，启用双轨基期" if mean_distorted else
                     "序列平稳，均值可作周期中枢"),
        },
    }
    cur_oe, norm_oe = out["oe_current"], out["oe_normalized"]
    out["oe_normalized_vs_current"] = safe_div(norm_oe, cur_oe)

    # 混合口径：正常化净利润 × 当期 OE/净利润转化率。
    # 用途——把"利润率的周期性"与"现金转化率的结构性改善"分开：
    # oe_normalized 用历史 OE 利润率均值，会把早年低转化率一并平均进来（偏保守）；
    # 混合口径只正常化利润率、保留当期资本结构与 capex 强度（更贴近现状）。
    conv = safe_div(cur_oe, out["net_income_current"])
    out["oe_to_ni_conversion_latest"] = conv
    out["oe_normalized_hybrid"] = out["net_income_normalized"] * conv if conv is not None else None
    out["oe_normalized_hybrid_vs_current"] = safe_div(out["oe_normalized_hybrid"], cur_oe)

    if cyc == "周期高位":
        # 高位时取正常化区间上界（混合口径）为推荐基期，避免双重保守；
        # 但报告必须同时展示 oe_normalized 下界，形成基期区间。
        # 非正值不能作 DCF 基期，需剔除（如早年长期亏损把 OE 利润率均值拉成负数）。
        cands = sorted(v for v in (out["oe_normalized_hybrid"], norm_oe)
                       if v is not None and v > 0)
        if cands:
            out["base_oe_recommended"] = cands[-1]
            out["base_oe_range"] = [cands[0], cands[-1]] if len(cands) == 2 else None
            out["base_oe_recommended_basis"] = (
                "正常化混合口径（当期处周期高位，禁用当期基期；区间下界为历史OE利润率均值口径）"
                if len(cands) == 2 else
                "正常化（当期处周期高位；另一口径为非正值已剔除，说明历史盈利能力薄弱，须在报告说明）"
            )
            if mean_distorted:
                # 爬坡期公司防误判（实证教训：PDD 早年亏损把均值拉到 10.4%，
                # "周期高位"判定实为"商业模式换挡"，强制正常化会过度保守）。
                # 双轨输出：主轨仍为正常化（纪律不放松），交叉轨当期基期，
                # 由分析师在报告中论证当期利润率是"周期顶"还是"新常态"。
                out["base_oe_dual_track"] = {
                    "main": {"basis": "正常化（纪律主轨）", "value": out["base_oe_recommended"]},
                    "cross": {"basis": "当期（爬坡期交叉轨）", "value": cur_oe},
                    "adjudication": (
                        "均值扭曲年存在，'周期高位'判定可能是'商业模式换挡'的误报。"
                        "报告必须并列两轨估值并论证：当期利润率是周期顶（用主轨）"
                        "还是结构性新常态（可向交叉轨靠拢）；证据看利润率来源"
                        "（提价/份额/结构 vs 景气/一次性）"),
                }
        else:
            out["base_oe_recommended"] = None
            out["base_oe_range"] = None
            out["base_oe_recommended_basis"] = (
                "正常化基期为非正值——历史长期不赚钱，当期高盈利缺乏历史支撑。"
                "禁止用当期基期做 DCF，应改用反向 DCF + 单位经济外推，并在报告显著标注该限制"
            )
    elif loss_case:
        out["base_oe_recommended"] = None
        out["base_oe_range"] = None
        out["base_oe_recommended_basis"] = (
            f"{loss_case}，周期正常化不适用。基期须由分析师按正常化路径单独论证"
            "（如产能利用率恢复后的中周期利润率），并在报告写明推导过程"
        )
    elif cyc == "周期低位":
        # 低位向上正常化：当期利润低估了长期能力，会用当期基期系统性低估内在价值、
        # 漏掉"底部便宜的周期股"。同样用正常化混合口径（全期均值利润率 × 当期转化率）。
        cands = sorted(v for v in (out["oe_normalized_hybrid"], norm_oe)
                       if v is not None and v > 0)
        if cands:
            out["base_oe_recommended"] = cands[-1]
            out["base_oe_range"] = [cands[0], cands[-1]] if len(cands) == 2 else None
            out["base_oe_recommended_basis"] = (
                "正常化混合口径（当期处周期低位，向上正常化；区间下界为历史OE利润率均值口径）"
            )
        else:
            out["base_oe_recommended"] = cur_oe
            out["base_oe_range"] = None
            out["base_oe_recommended_basis"] = f"当期（周期低位但正常化基期不可用，用当期+报告说明）"
    else:
        out["base_oe_recommended"] = cur_oe
        out["base_oe_range"] = None
        out["base_oe_recommended_basis"] = f"当期（周期位置：{cyc}）"
    return out


# 行业类型门控：本脚本的口径（revenue/capex/OE/ROIC）只适用于一般工商企业。
# 银行/保险/券商的报表结构完全不同（利息收支、浮存金、准备金），喂进来会
# 一本正经地算出错误的 ROIC 和 Owner Earnings——所以对金融类硬拒绝。
FINANCIAL_TYPES = {"bank", "银行", "insurance", "保险", "broker", "券商",
                   "securities", "金融", "financial"}

def compute(data):
    ctype = str(data.get("company_type", "")).strip().lower()
    if ctype in FINANCIAL_TYPES:
        raise SystemExit(
            f"错误：company_type={data.get('company_type')} 属于金融类。"
            "本脚本的通用口径（OE/ROIC/capex拆分）对金融股会产出错误结果，已拒绝执行。"
            "金融股请按 references/metric-playbook.md 的银行/保险专属指标集手工建底稿"
            "（净息差/不良率/拨备覆盖率 或 EV/NBV/综合成本率），估值按 valuation-guide 金融股方法树。"
        )
    tax = data.get("tax_rate") or DEFAULT_TAX
    rows = sorted(data.get("annual", []), key=lambda r: r["year"])
    if len(rows) < 3:
        raise SystemExit("错误：年度数据不足 3 年，无法计算长期指标。请先补齐数据底稿。")

    warnings = []
    if not ctype:
        warnings.append("company_type 未填写——Phase 0 应判定七类型之一（见 metric-playbook），"
                        "金融类严禁走本管道；非金融也需按类型选关键指标集")
    # 维持性 capex 兜底：近5年 D&A 均值
    da_list = [get(r, "d_and_a") for r in rows if get(r, "d_and_a") is not None]
    da_avg5 = sum(da_list[-5:]) / len(da_list[-5:]) if da_list else None

    series = []
    prev = None
    for r in rows:
        year = r["year"]
        rev, ni = get(r, "revenue"), get(r, "net_income")
        ebit, intexp = get(r, "ebit"), get(r, "interest_expense")
        da, capex = get(r, "d_and_a"), get(r, "capex")
        mcapex = get(r, "maintenance_capex")
        wc, ocf = get(r, "wc_change"), get(r, "ocf")
        eq, debt, cash = get(r, "total_equity"), get(r, "total_debt"), get(r, "cash")
        shares = get(r, "shares_diluted")

        if ebit is not None:
            nopat = ebit * (1 - tax)
        elif ni is not None:
            nopat = ni + (intexp or 0.0) * (1 - tax)
            warnings.append(f"{year}: EBIT 缺失，NOPAT 用净利润+税后利息近似")
        else:
            nopat = None

        ic = None
        if eq is not None and debt is not None:
            ic = eq + debt - (cash or 0.0)

        avg_ic = None
        if ic is not None and prev and prev.get("invested_capital") is not None:
            avg_ic = (ic + prev["invested_capital"]) / 2.0
        roic = safe_div(nopat, avg_ic)

        avg_eq = None
        if eq is not None and prev and prev.get("equity") is not None:
            avg_eq = (eq + prev["equity"]) / 2.0
        roe = safe_div(ni, avg_eq)

        growth_capex = get(r, "growth_capex")

        # ---- capex 真拆分 ----
        # 优先底稿明示：maintenance_capex（披露口径）或 growth_capex（扩张性）。
        # 缺省时按收入增速启发式估算——capex/收入比显著高于全期均值的部分视为扩张性投入，
        # 维持性 = 全期 capex/收入比 × 当年收入 与 D&A 的较低者（避免衰退年收入萎缩时维持性虚增）。
        capex_split_basis = None
        if mcapex is not None:
            mc = mcapex; capex_split_basis = "披露口径"
        elif growth_capex is not None and capex is not None:
            mc = capex - growth_capex; capex_split_basis = "披露口径（由growth_capex反推）"
        elif capex is not None:
            # 启发式：维持性 ≈ 全期 capex/收入比 中位数 × 当年收入，封顶 D&A（无 D&A 则D&A均值）
            hist = [safe_div(get(x, "capex"), get(x, "revenue")) for x in rows]
            hist = [h for h in hist if h is not None]
            med_ratio = median(hist) if hist else None
            est = (med_ratio * rev) if (med_ratio is not None and rev is not None) else da_avg5
            cap = da if da is not None else da_avg5
            mc = min(est, cap) if (est is not None and cap is not None) else (est if est is not None else cap)
            capex_split_basis = "启发式估算（capex/收入比中位数，封顶D&A）"
            if mc is not None:
                warnings.append(f"{year}: capex未披露拆分，按{capex_split_basis}估维持性 ≈{mc:.1f}")
        else:
            mc = None
        growth_part = (capex - mc) if (capex is not None and mc is not None) else None

        oe = None
        if ni is not None and da is not None and mc is not None:
            oe = ni + da - mc - (wc or 0.0)

        fcf = ocf - capex if (ocf is not None and capex is not None) else None
        # 真实 FCF 区间：悲观=全部 capex 视同维持（fcf），乐观=扩张性 capex 视为可裁量再投资（ocf−mc）
        fcf_optimistic = (ocf - mc) if (ocf is not None and mc is not None) else None
        fcf_true_range = None
        if fcf is not None and fcf_optimistic is not None and abs(fcf_optimistic - fcf) > 1e-9:
            fcf_true_range = [min(fcf, fcf_optimistic), max(fcf, fcf_optimistic)]

        item = {
            "year": year,
            "revenue": rev, "net_income": ni, "nopat": nopat,
            "invested_capital": ic, "equity": eq,
            "roic": roic, "roe": roe,
            "gross_margin": safe_div(get(r, "gross_profit"), rev),
            "net_margin": safe_div(ni, rev),
            "owner_earnings": oe,
            "capex_total": capex,
            "maintenance_capex_used": mc,
            "growth_capex_used": growth_part,
            "capex_split_basis": capex_split_basis,
            "fcf": fcf,
            "fcf_true_range": fcf_true_range,
            "fcf_to_ni": safe_div(fcf, ni),
            "ocf_to_ni": safe_div(ocf, ni),
            "shares_diluted": shares,
            "rev_ps": safe_div(rev, shares),
            "eps": safe_div(ni, shares),
            "oe_ps": safe_div(oe, shares),
            "bvps": safe_div(eq, shares),
        }
        series.append(item)
        prev = item

    # ROIIC 滚动3年
    for i, item in enumerate(series):
        if i >= 3:
            d_nopat = None
            d_ic = None
            if item["nopat"] is not None and series[i - 3]["nopat"] is not None:
                d_nopat = item["nopat"] - series[i - 3]["nopat"]
            if item["invested_capital"] is not None and series[i - 3]["invested_capital"] is not None:
                d_ic = item["invested_capital"] - series[i - 3]["invested_capital"]
            item["roiic_3y"] = safe_div(d_nopat, d_ic) if (d_ic and d_ic > 0) else None
        else:
            item["roiic_3y"] = None

    first, last = series[0], series[-1]
    n_years = last["year"] - first["year"]

    def pair_cagr(key):
        return cagr(first.get(key), last.get(key), n_years)

    summary = {
        "period": f"{first['year']}–{last['year']}（{n_years} 年）",
        "cagr_total": {
            "revenue": pair_cagr("revenue"),
            "net_income": pair_cagr("net_income"),
            "owner_earnings": pair_cagr("owner_earnings"),
        },
        "cagr_per_share": {
            "rev_ps": pair_cagr("rev_ps"),
            "eps": pair_cagr("eps"),
            "oe_ps": pair_cagr("oe_ps"),
            "bvps": pair_cagr("bvps"),
        },
        "share_count_change": safe_div(last["shares_diluted"], first["shares_diluted"]),
        "roic_avg_5y": None,
        "roic_min_max_5y": None,
    }
    roics = [s["roic"] for s in series[-5:] if s["roic"] is not None]
    if roics:
        summary["roic_avg_5y"] = sum(roics) / len(roics)
        summary["roic_min_max_5y"] = [min(roics), max(roics)]

    # 资本配置流向（全期累计）
    def total(key):
        vals = [get(r, key) for r in rows if get(r, key) is not None]
        return sum(vals) if vals else None

    def total_alias(*keys):
        """按别名依次尝试。

        ⚠️ 字段名不匹配是最隐蔽的一类静默失效（v2.12 实证）：
        中国建筑底稿写的是 `dividends`，引擎只读 `dividends_paid`，
        于是 cum_dividends 静默为 null —— 而这家公司的投资论点恰恰
        压在"6%+ 股息率能不能持续"上。同一行的 `buyback` 读到了，
        所以输出看起来正常，没有任何报错。
        """
        for k in keys:
            v = total(k)
            if v is not None:
                return v
        return None

    cum_div = total_alias("dividends_paid", "dividends", "dividend_paid")
    # 兜底：底稿只登记了每股分红时，用 每股分红 × 当年股本 反算总额。
    # 港股/A股底稿常只有 dividend_per_share（伊利/腾讯/泡泡玛特均如此），
    # 若不兜底，这些真金白银分红的公司会因"缺字段"而算不出覆盖倍数，
    # 所有者最关心的问题反而在最典型的分红股上失效。
    div_basis = "dividends_paid" if cum_div is not None else None
    if cum_div is None:
        est = []
        for r in rows:
            dps = get(r, "dividend_per_share")
            sh = get(r, "shares_diluted")
            if dps is not None and sh:
                est.append(dps * sh)
        if est:
            cum_div = sum(est)
            div_basis = "estimated_from_dividend_per_share×shares"
    cum_bb = total_alias("buyback", "repurchase", "share_repurchase")
    cum_ocf_v = total("ocf")
    cum_capex_v = total("capex")
    alloc = {
        "cum_ocf": cum_ocf_v, "cum_capex": cum_capex_v,
        "cum_dividends": cum_div, "cum_buyback": cum_bb,
        "cum_equity_raised": total_alias("equity_raised", "equity_issued"),
        "dividends_basis": div_basis,
    }

    # ---- 所有者视角：分红到底是"挣出来的"还是"借出来的"（v2.12 新增）----
    # 买股票就是买公司：所有者最终关心的不是"公司赚了多少"，而是
    # "属于我的那部分现金，有多少真正到手、这份到手能不能持续"。
    # 判据是自由现金流对股东回报的覆盖倍数——分红率（占净利润）会骗人，
    # 因为净利润可以是应计的；而分红必须用真金白银付。
    # 实证原型（中国建筑）：连续 10 年分红、股息率 6%+，看起来是"A股难得的红利股"，
    # 但十年 FCF 累计为负，分红实际靠融资维持——这是"分红幻觉"，
    # 对所有者的意义与茅台的分红完全不同，必须在指标层就分开。
    cum_fcf_v = None
    if cum_ocf_v is not None and cum_capex_v is not None:
        cum_fcf_v = cum_ocf_v - cum_capex_v
    cum_ret = (cum_div or 0.0) + (cum_bb or 0.0)
    alloc["cum_shareholder_return"] = cum_ret if (cum_div is not None or cum_bb) else None
    alloc["cum_fcf"] = cum_fcf_v
    alloc["fcf_cover_shareholder_return"] = (
        cum_fcf_v / cum_ret if (cum_fcf_v is not None and cum_ret > 0) else None)
    alloc["shareholder_return_funded_by_fcf"] = (
        None if alloc["fcf_cover_shareholder_return"] is None
        else alloc["fcf_cover_shareholder_return"] >= 1.0)

    # 自动警报
    alerts = []
    rt, rp = summary["cagr_total"]["revenue"], summary["cagr_per_share"]["rev_ps"]
    if rt is not None and rp is not None and (rt - rp) > 0.02:
        alerts.append(f"稀释警报：收入总量CAGR {rt:.1%} 显著高于每股CAGR {rp:.1%}，增长被增发摊薄")
    scc = summary["share_count_change"]
    if scc is not None and scc > 1.3:
        alerts.append(f"股本膨胀警报：期间股本增至 {scc:.2f} 倍")

    # 分红幻觉警报（v2.12，所有者视角核心）：股东回报未被自由现金流覆盖
    cov = alloc.get("fcf_cover_shareholder_return")
    if cov is not None and cov < 1.0:
        _fcf = alloc.get("cum_fcf")
        _ret = alloc.get("cum_shareholder_return")
        alerts.append(
            f"分红幻觉警报：全期股东回报（分红+回购）{_ret:,.0f} 超过累计自由现金流 "
            f"{_fcf:,.0f}（覆盖 {cov:.2f}x < 1.0）——这份回报不是经营挣出来的，"
            "而是靠融资/举债/消耗存量现金维持。高股息率在此情形下是幻觉，"
            "对所有者的意义与自由现金流充沛公司的同等股息率完全不同，"
            "报告必须明确区分并质询可持续性")
    elif cov is not None and cov < 1.5:
        alerts.append(
            f"股东回报覆盖偏薄：累计自由现金流仅覆盖股东回报 {cov:.2f}x（<1.5x），"
            "分红/回购的安全垫较薄，经营现金流波动即可能迫使削减")
    if alloc.get("cum_dividends") is None and alloc.get("cum_buyback") in (None, 0.0):
        warnings.append(
            "所有者口径缺口：底稿缺分红与回购数据（字段 `dividends_paid`/`dividends`、"
            "`buyback`），无法计算股东回报率与自由现金流覆盖倍数。"
            "买股票就是买公司，'属于我的现金有多少真到手'是所有者的核心问题，"
            "请从现金流量表『分配股利、利润或偿付利息支付的现金』与"
            "『购买子公司少数股权/回购股份』补齐")
    bad_fcf_years = [s["year"] for s in series[-5:] if s["fcf_to_ni"] is not None and s["fcf_to_ni"] < 0.6]
    if len(bad_fcf_years) >= 3:
        alerts.append(f"利润含金量警报：近5年中 {bad_fcf_years} 年 FCF/净利润 < 0.6")
    low_roiic = [s["year"] for s in series if s["roiic_3y"] is not None and s["roiic_3y"] < 0.08]
    if low_roiic and low_roiic[-1] == last["year"]:
        alerts.append(f"增长质量警报：最新滚动3年 ROIIC < 8%，增量资本回报低下")

    normalization = compute_normalization(series)
    if normalization and normalization.get("status") == "ok":
        cyc = normalization["cyclicality"]
        if cyc == "周期高位":
            rng = normalization.get("base_oe_range")
            rec = normalization.get("base_oe_recommended")
            if rng:
                rng_txt = f"{rng[0]:,.0f}~{rng[1]:,.0f}"
            elif rec is not None:
                rng_txt = f"{rec:,.0f}"
            else:
                rng_txt = "不可用（正常化基期为非正值）"
            pct = safe_div(rec, normalization["oe_current"])
            pct_txt = f"（推荐值为当期的 {pct:.0%}）" if pct is not None else ""
            alerts.append(
                f"周期高位警报：最新净利率 {normalization['net_margin_latest']:.1%} 是全期均值 "
                f"{normalization['net_margin_avg']:.1%} 的 {normalization['margin_ratio_latest_vs_avg']:.2f} 倍，"
                f"当期 Owner Earnings 禁止直接作为 DCF 基期；正常化基期区间 {rng_txt}{pct_txt}"
            )
            if normalization.get("base_oe_dual_track"):
                dt = normalization["base_oe_dual_track"]
                alerts.append(
                    f"双轨基期提示：序列含均值扭曲年"
                    f"（{'；'.join(y['reason'] + str(y['year']) for y in normalization['mean_distortion']['years'][:3])}"
                    f"{'…' if len(normalization['mean_distortion']['years']) > 3 else ''}），"
                    f"'周期高位'可能是商业模式换挡的误报——主轨正常化 {dt['main']['value']:,.0f}"
                    f" / 交叉轨当期 {dt['cross']['value']:,.0f}，报告须并列两轨估值并论证利润率来源"
                )
        elif cyc == "周期低位":
            rec = normalization.get("base_oe_recommended")
            rec_txt = f"；向上正常化基期 {rec:,.0f}" if rec is not None else ""
            alerts.append(
                f"周期低位提示：最新净利率仅为全期均值的 {normalization['margin_ratio_latest_vs_avg']:.2f} 倍，"
                f"当期利润低估长期盈利能力，用当期基期会低估内在价值{rec_txt}"
            )
        elif "亏损" in cyc:
            alerts.append(
                f"基期不可用警报：{cyc}（当期净利率 "
                f"{normalization['net_margin_latest']:.1%}，全期均值 {normalization['net_margin_avg']:.1%}），"
                f"周期正常化不适用，禁止直接用当期数据做 DCF 基期，须单独论证正常化盈利路径"
            )

    # chart_series：报告 ECharts 图直接从本区块引用数据（并由 verify_report.py 的
    # vchart 锚点校验），禁止模型手抄数组进 HTML——图表数据唯一来源是这里。
    def col(key):
        return [s.get(key) for s in series]

    chart_series = {
        "years": [s["year"] for s in series],
        "revenue": col("revenue"),
        "net_income": col("net_income"),
        "net_margin": col("net_margin"),
        "gross_margin": col("gross_margin"),
        "roe": col("roe"),
        "roic": col("roic"),
        "roiic_3y": col("roiic_3y"),
        "owner_earnings": col("owner_earnings"),
        "fcf": col("fcf"),
        "fcf_low": [s["fcf_true_range"][0] if s.get("fcf_true_range") else s.get("fcf") for s in series],
        "fcf_high": [s["fcf_true_range"][1] if s.get("fcf_true_range") else s.get("fcf") for s in series],
        "eps": col("eps"),
        "oe_ps": col("oe_ps"),
        "bvps": col("bvps"),
        "shares_diluted": col("shares_diluted"),
    }

    return {
        "company": data.get("company"), "ticker": data.get("ticker"),
        "currency": data.get("currency"), "unit": data.get("unit"),
        "definitions": DEFINITIONS,
        "series": series, "summary": summary,
        "normalization": normalization,
        "capital_allocation": alloc,
        "chart_series": chart_series,
        "alerts": alerts,
        "warnings": sorted(set(warnings)),
    }


def main():
    ap = argparse.ArgumentParser(description="统一口径指标计算器")
    ap.add_argument("input", help="标准格式财务数据 JSON（数据底稿）")
    ap.add_argument("-o", "--output", help="输出 JSON 路径；缺省打印到 stdout")
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = compute(data)
    out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"已写入 {args.output}；警报 {len(result['alerts'])} 条，warnings {len(result['warnings'])} 条")
        for a in result["alerts"]:
            print("  [ALERT]", a)
    else:
        print(out)


if __name__ == "__main__":
    main()
