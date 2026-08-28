#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_metrics_bank.py — 银行股专属定量管道（Phase 2，替代通用 compute_metrics）

背景：通用管道（compute_metrics.py）对金融类公司会拒绝执行——利息收支、拨备、
浮存金口径与实业完全不同，Owner Earnings/FCF 对银行没有意义。被门控踢出后不能
裸奔，本脚本给银行一条完整的定量出路：专属底稿 schema + 专属指标 + 专属勾稽。

估值路径（valuation-guide 方法树）：银行用 PB-ROE 回归 + 股息贴现，
禁用 DCF/FCF/Owner Earnings。本脚本输出 PB-ROE 所需的全部输入。

保险/券商暂不支持（口径又不同），仍需手工建稿并在报告声明。

标准底稿 JSON（单位：百万，与通用底稿头字段一致）：
{
  "company": "招商银行", "ticker": "600036.SH", "currency": "CNY",
  "unit": "million", "company_type": "银行",
  "accounting_standard": "CAS", "fiscal_year_end": "12-31",
  "annual": [
    {
      "year": 2024,
      "net_interest_income": 210000.0,     # 利息净收入
      "non_interest_income": 120000.0,     # 非息收入（手续费+其他）
      "operating_income": 330000.0,        # 营业收入
      "operating_expense": 110000.0,       # 业务及管理费（算成本收入比）
      "provision_charge": 60000.0,         # 信用减值损失（当期计提）
      "net_income": 140000.0,              # 归母净利润
      "total_assets": 11000000.0,
      "total_equity": 1000000.0,           # 含少数股东权益
      "gross_loans": 6500000.0,            # 贷款总额
      "npl_balance": 61000.0,              # 不良贷款余额
      "provision_balance": 275000.0,       # 拨备余额（贷款减值准备）
      "special_mention_ratio": 0.011,      # 关注类占比（可选）
      "core_tier1_ratio": 0.135,           # 核心一级资本充足率
      "nim": 0.024,                        # 净息差（年报披露值）
      "deposits": 8000000.0,               # 存款总额（可选，算存贷比）
      "shares_diluted": 25220.0,
      "dividend_per_share": 1.97,          # 每股分红（元）
      "book_value_per_share": 36.71        # 每股净资产（披露值，用于交叉核对）
    }, ...
  ],
  "crosscheck": [  # 与年报原文核对（同 validate_data 语义，必做最近 3 年）
    { "year": 2024, "source": "2024年报 p.XX（巨潮）",
      "operating_income": 330000.0, "net_income": 140000.0,
      "npl_balance": 61000.0, "core_tier1_ratio": 0.135 }, ...
  ]
}

输出 metrics_<公司>_bank.json：
  - series: 逐年 NPL率/拨备覆盖率/拨贷比/成本收入比/ROA/ROE/权益乘数/非息占比/
            每股净资产/每股分红
  - summary: 10 年 CAGR（净利润/每股净资产）、ROE 均值与趋势、分红率均值
  - pb_roe_inputs: PB-ROE 回归所需输入（当期 ROE、可持续 ROE、每股净资产）
  - alerts: 资产质量与资本纪律警报（勾稽失败会直接报错拒绝输出）

用法：
    python3 compute_metrics_bank.py data/financials_<银行>.json -o data/metrics_<银行>_bank.json
"""
import argparse
import json
import sys

BANK_TYPES = {"bank", "银行"}


def _f(row, key):
    v = row.get(key)
    return float(v) if v is not None else None


def compute(data):
    ctype = str(data.get("company_type", "")).strip().lower()
    if ctype not in BANK_TYPES:
        raise SystemExit(f"本脚本仅适用于银行（company_type={data.get('company_type')}）。"
                         "保险/券商口径不同，暂需手工建稿；实业公司走 compute_metrics.py。")

    rows = sorted(data.get("annual", []), key=lambda r: r.get("year", 0))
    if len(rows) < 5:
        raise SystemExit("银行底稿至少需要 5 个年度（建议 10 年，覆盖一轮信用周期）")

    errors, alerts, series = [], [], []
    for r in rows:
        y = r.get("year")
        loans, npl, prov = _f(r, "gross_loans"), _f(r, "npl_balance"), _f(r, "provision_balance")
        ni, ta, eq = _f(r, "net_income"), _f(r, "total_assets"), _f(r, "total_equity")
        opin, opex = _f(r, "operating_income"), _f(r, "operating_expense")
        nii, nonii = _f(r, "net_interest_income"), _f(r, "non_interest_income")
        shares = _f(r, "shares_diluted")

        # --- 勾稽（银行版硬检查）---
        # 不良率/拨备覆盖：优先从余额反算；底稿直接给披露比率时直接采用（两种口径兼容）
        npl_ratio = npl / loans if (npl is not None and loans) else _f(r, "npl_ratio")
        if npl_ratio is not None and not (0.0 <= npl_ratio <= 0.15):
            errors.append(f"{y}: 不良率 {npl_ratio:.2%} 超出 0~15% 合理带，疑似单位/科目错误")
        coverage = prov / npl if (prov is not None and npl) else _f(r, "provision_coverage")
        if coverage is not None and coverage < 1.0:
            alerts.append(f"{y}: 拨备覆盖率 {coverage:.0%} < 100%——低于监管红线（120~150%），"
                          "资产质量或利润真实性重大警报")
        if opin is not None and nii is not None and nonii is not None:
            if abs(opin - (nii + nonii)) / max(opin, 1e-9) > 0.05:
                errors.append(f"{y}: 营业收入({opin}) ≠ 利息净收入+非息收入({nii}+{nonii})，"
                              "偏差>5%，检查科目口径（其他收益是否漏记）")
        if ni is not None and opin is not None and ni > opin:
            errors.append(f"{y}: 净利润({ni}) > 营业收入({opin})，疑似单位混淆")

        roa = ni / ta if (ni is not None and ta) else None
        # 银行股 ROE 口径：优先用官方披露 ROAE（归属普通股股东，剔除优先股/永续债），
        # 避免其他权益工具导致含NCI口径系统性低估（招行 2025 官方 13.44% vs 含NCI 11.7%）
        roe = _f(r, "roe_reported")
        if roe is None:
            roe = ni / eq if (ni is not None and eq) else None
        leverage = ta / eq if (ta and eq) else None
        if leverage is not None and leverage > 20:
            alerts.append(f"{y}: 权益乘数 {leverage:.1f}x > 20x，杠杆超出稳健银行常态（10~16x）")

        series.append({
            "year": y,
            "npl_ratio": npl_ratio,
            "provision_coverage": coverage,
            "provision_to_loans": prov / loans if (prov is not None and loans) else None,
            "cost_income_ratio": opex / opin if (opex is not None and opin) else None,
            "roa": roa, "roe": roe, "equity_multiplier": leverage,
            "non_interest_share": nonii / opin if (nonii is not None and opin) else None,
            "nim": _f(r, "nim"),
            "core_tier1_ratio": _f(r, "core_tier1_ratio"),
            "special_mention_ratio": _f(r, "special_mention_ratio"),
            "credit_cost": (_f(r, "provision_charge") / loans) if (_f(r, "provision_charge") is not None and loans) else None,
            "bvps": _f(r, "bvps_reported") if _f(r, "bvps_reported") is not None
                    else (eq / shares if (eq and shares) else None),
            "eps": ni / shares if (ni is not None and shares) else None,
            "dps": _f(r, "dividend_per_share"),
            "loan_to_deposit": (loans / _f(r, "deposits")) if (loans and _f(r, "deposits")) else None,
        })

    if errors:
        raise SystemExit("银行底稿勾稽失败，修正后重跑：\n  " + "\n  ".join(errors))

    # --- summary 与 PB-ROE 输入 ---
    first, last = series[0], series[-1]
    n = last["year"] - first["year"]

    def cagr(a, b):
        if a and b and a > 0 and b > 0 and n > 0:
            return (b / a) ** (1.0 / n) - 1.0
        return None

    roes = [s["roe"] for s in series if s["roe"] is not None]
    payout = []
    for s in series:
        if s["dps"] and s["eps"]:
            payout.append(s["dps"] / s["eps"])
    roe_avg = sum(roes) / len(roes) if roes else None
    roe_recent3 = [s["roe"] for s in series[-3:] if s["roe"] is not None]
    roe_recent = sum(roe_recent3) / len(roe_recent3) if roe_recent3 else None
    payout_avg = sum(payout) / len(payout) if payout else None

    # 可持续 ROE：近 3 年均值与全期均值取低者（保守），信用成本上行期再降
    sustainable_roe = min(x for x in [roe_avg, roe_recent] if x is not None) if (roe_avg or roe_recent) else None
    cc_series = [s["credit_cost"] for s in series if s["credit_cost"] is not None]
    if len(cc_series) >= 3 and cc_series[-1] > (sum(cc_series) / len(cc_series)) * 1.3:
        alerts.append("信用成本处于上行期（最新计提 > 均值 1.3 倍），可持续 ROE 应再往下修，"
                      "不良暴露通常滞后于信贷扩张 2~3 年")

    npl_trend = [s["npl_ratio"] for s in series[-3:] if s["npl_ratio"] is not None]
    sm = last.get("special_mention_ratio")
    if len(npl_trend) == 3 and npl_trend[2] > npl_trend[0] and sm and sm > 0.02:
        alerts.append("不良率连升且关注类 >2%：迁徙压力在积累，拨备覆盖率的下降空间有限")

    result = {
        "company": data.get("company"), "ticker": data.get("ticker"),
        "pipeline": "bank",
        "series": series,
        "summary": {
            "years": n + 1,
            "net_income_cagr": cagr(_f(rows[0], "net_income"), _f(rows[-1], "net_income")),
            "bvps_cagr": cagr(first["bvps"], last["bvps"]),
            "roe_avg": roe_avg, "roe_recent3": roe_recent,
            "payout_avg": payout_avg,
            "npl_ratio_latest": last["npl_ratio"],
            "provision_coverage_latest": last["provision_coverage"],
            "core_tier1_latest": last["core_tier1_ratio"],
        },
        "pb_roe_inputs": {
            "bvps_latest": last["bvps"],
            "roe_sustainable": sustainable_roe,
            "payout_assumed": payout_avg,
            "note": "PB 合理中枢 ≈ (可持续ROE − g) / (r − g)，g = 可持续ROE×(1−分红率)，"
                    "r 用股权成本（建议 10~11%）。股息贴现交叉验证：DPS×(1+g)/(r−g)。"
                    "估值判定基准用可持续 ROE，不用当期 ROE——信用周期顶部的高 ROE 等价于"
                    "实业的周期高位利润，同样禁作基期。",
        },
        "chart_series": {
            "years": [s["year"] for s in series],
            "npl_ratio": [s["npl_ratio"] for s in series],
            "provision_coverage": [s["provision_coverage"] for s in series],
            "roe": [s["roe"] for s in series],
            "nim": [s["nim"] for s in series],
            "cost_income_ratio": [s["cost_income_ratio"] for s in series],
            "bvps": [s["bvps"] for s in series],
        },
        "alerts": alerts,
    }
    return result


def main():
    ap = argparse.ArgumentParser(description="银行股专属定量管道")
    ap.add_argument("input", help="银行标准底稿 JSON")
    ap.add_argument("-o", "--output", help="输出路径（建议 data/metrics_<银行>_bank.json）")
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    res = compute(data)

    print(f"银行管道计算完成：{res['company']}（{res['summary']['years']} 年）")
    s = res["summary"]
    if s["roe_avg"] is not None:
        print(f"  ROE 全期均值 {s['roe_avg']:.1%} / 近3年 {s['roe_recent3']:.1%}")
    if s["npl_ratio_latest"] is not None:
        print(f"  最新不良率 {s['npl_ratio_latest']:.2%}，拨备覆盖率 "
              f"{s['provision_coverage_latest']:.0%}")
    for a in res["alerts"]:
        print(f"  [ALERT] {a}")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"已写入 {args.output}")


if __name__ == "__main__":
    main()
