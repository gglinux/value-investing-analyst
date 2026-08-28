#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_data.py — 数据底稿入口校验器（Phase 1 收尾强制运行，通过后才允许进入 Phase 2）

定位：verify_report.py 保证"报告 = 底稿"，本脚本保证"底稿 ≈ 事实"。
对标准格式财务底稿 JSON 做四类确定性检查：

1. 口径注册表：头字段 company/currency/unit 缺失即报错；
   accounting_standard/fiscal_year_end/fx_basis 缺失给警告（跨市场竞对对比必填）。
2. 三表勾稽：total_assets ≈ total_liabilities + total_equity（容差 1%）；
   gross_profit ≤ revenue；净利润量级 sanity。
3. 单位一致性：百万/亿混淆检测——净利润绝对值 > 收入 1.5 倍、OCF > 收入 2 倍等
   数量级异常即报错/警告。
4. 突变检测：核心科目同比变动超过 ±50% 必须在 spike_notes 中标注原因
   （真实业务变化 or 数据修正），未标注不放行。

以及 P0 双源交叉验证的机器检查：
5. crosscheck 区块：最近 3 个年度的 revenue/net_income/ocf/shares_diluted 必须与
   官方披露原文（年报 PDF/XBRL）核对并登记（含 source 出处），与 annual 行容差 1%。
   缺失 crosscheck 区块 → 校验失败。

输入格式 = compute_metrics.py 的标准底稿 JSON，外加可选/必填扩展字段：
{
  ..., "accounting_standard": "CAS|IFRS|US-GAAP",
  "fiscal_year_end": "12-31",
  "fx_basis": "报告币种未换算 / 按年末汇率折算 等（涉及换算时必填）",
  "annual": [ { ..., "total_assets": 1500.0, "total_liabilities": 700.0,
                "gross_profit": 400.0 }, ... ],
  "spike_notes": { "2020.revenue": "疫情停产，年报 MD&A p.12 确认" },
  "crosscheck": [
    { "year": 2024, "source": "2024年报 PDF p.45（巨潮）",
      "revenue": 1000.0, "net_income": 150.0, "ocf": 180.0, "shares_diluted": 100.0 },
    ...
  ]
}

用法：
    python3 validate_data.py <financials.json> [--skip-crosscheck]
    --skip-crosscheck 仅限竞对公司使用（命门科目核对只强制 A 公司），
    使用后校验结果标注"未做双源核对"，报告脚注必须披露。
退出码：0 通过（可含警告）；1 存在错误，禁止进入 Phase 2。
"""
import argparse
import json
import sys

SPIKE_KEYS = ["revenue", "net_income", "ocf", "capex", "total_equity", "shares_diluted"]
SPIKE_THRESHOLD = 0.5
CROSSCHECK_KEYS = ["revenue", "net_income", "ocf", "shares_diluted"]
CROSSCHECK_MIN_YEARS = 3
TOL = 0.01  # 1% 相对容差


def rel_diff(a, b):
    if a is None or b is None:
        return None
    base = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / base


def main():
    ap = argparse.ArgumentParser(description="数据底稿入口校验")
    ap.add_argument("input", help="标准格式财务底稿 JSON")
    ap.add_argument("--skip-crosscheck", action="store_true",
                    help="跳过双源核对检查（仅限竞对公司，报告须披露）")
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    errors, warns = [], []

    # 0. 行业类型门控（金融股禁走通用管道）
    FINANCIAL_TYPES = {"bank", "银行", "insurance", "保险", "broker", "券商",
                       "securities", "金融", "financial"}
    ctype = str(data.get("company_type", "")).strip().lower()
    if ctype in FINANCIAL_TYPES:
        errors.append(f"行业门控：company_type={data.get('company_type')} 为金融类，"
                      "通用底稿/compute_metrics 管道不适用（利息收支/浮存金/准备金口径不同），"
                      "请按 metric-playbook 银行/保险专属指标集单独建稿")
    elif not ctype:
        warns.append("行业门控：company_type 缺失——Phase 0 必须判定商业模式类型（metric-playbook 七类）"
                     "并写入底稿头字段，金融类严禁走通用管道")

    # 1. 口径注册表
    for k in ("company", "currency", "unit"):
        if not data.get(k):
            errors.append(f"口径注册表：头字段 `{k}` 缺失")
    for k in ("accounting_standard", "fiscal_year_end"):
        if not data.get(k):
            warns.append(f"口径注册表：`{k}` 缺失——跨市场竞对对比时必填，图表脚注需注明")

    rows = sorted(data.get("annual", []), key=lambda r: r.get("year", 0))
    if not rows:
        errors.append("annual 为空")
        report(errors, warns, args)
        return

    def g(row, key):
        v = row.get(key)
        return float(v) if v is not None else None

    # 2 & 3. 勾稽与单位 sanity（逐年）
    for r in rows:
        y = r.get("year")
        rev, ni = g(r, "revenue"), g(r, "net_income")
        gp, ocf = g(r, "gross_profit"), g(r, "ocf")
        ta, tl, eq = g(r, "total_assets"), g(r, "total_liabilities"), g(r, "total_equity")

        if ta is not None and tl is not None and eq is not None:
            d = rel_diff(ta, tl + eq)
            if d is not None and d > TOL:
                errors.append(f"{y}: 勾稽失败 资产({ta}) ≠ 负债({tl})+权益({eq})，偏差 {d:.1%}"
                              "（注意：total_equity 若为归母口径需并入少数股东权益后再核）")
        elif ta is None or tl is None:
            warns.append(f"{y}: 缺 total_assets/total_liabilities，无法做三表勾稽")

        if rev is not None and gp is not None and gp > rev * (1 + TOL):
            errors.append(f"{y}: 毛利({gp}) > 收入({rev})，疑似科目或单位错误")
        if rev is not None and ni is not None and abs(ni) > abs(rev) * 1.5:
            errors.append(f"{y}: |净利润|({ni}) > 收入×1.5({rev})，疑似百万/亿单位混淆")
        if rev is not None and ocf is not None and abs(ocf) > abs(rev) * 2:
            warns.append(f"{y}: |经营现金流|({ocf}) > 收入×2，量级异常，请复核")

    # 4. 突变检测
    notes = data.get("spike_notes", {}) or {}
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        y = cur.get("year")
        for k in SPIKE_KEYS:
            a, b = g(prev, k), g(cur, k)
            if a is None or b is None or abs(a) < 1e-9:
                continue
            chg = (b - a) / abs(a)
            if abs(chg) > SPIKE_THRESHOLD:
                key = f"{y}.{k}"
                if key not in notes:
                    errors.append(f"{y}: `{k}` 同比变动 {chg:+.0%} 超过 ±50%，"
                                  f"spike_notes 缺少 `{key}` 的原因标注（业务变化或数据修正）")

    # 5. 双源交叉验证
    if args.skip_crosscheck:
        warns.append("已跳过双源核对（--skip-crosscheck）：仅限竞对公司；报告脚注必须披露该公司未做原文核对")
    else:
        cc = data.get("crosscheck") or []
        by_year = {r.get("year"): r for r in rows}
        if len(cc) < CROSSCHECK_MIN_YEARS:
            errors.append(f"双源核对：crosscheck 区块不足 {CROSSCHECK_MIN_YEARS} 个年度"
                          "（命门科目须与年报原文核对：收入/归母净利润/经营现金流/股本）")
        for entry in cc:
            y = entry.get("year")
            if not entry.get("source"):
                errors.append(f"双源核对 {y}: 缺 source 出处（年报页码/XBRL 标签）")
            row = by_year.get(y)
            if row is None:
                errors.append(f"双源核对 {y}: annual 中无该年度数据")
                continue
            for k in CROSSCHECK_KEYS:
                ov = entry.get(k)
                dv = row.get(k)
                if ov is None:
                    warns.append(f"双源核对 {y}: 官方值缺 `{k}`")
                    continue
                d = rel_diff(float(ov), float(dv) if dv is not None else None)
                if d is None:
                    errors.append(f"双源核对 {y}: 底稿缺 `{k}`，无法比对")
                elif d > TOL:
                    errors.append(f"双源核对 {y}: `{k}` 底稿({dv}) vs 官方({ov}) 偏差 {d:.1%}，"
                                  "以官方披露为准修正底稿并在 spike_notes 记录差异原因")

    report(errors, warns, args)


def report(errors, warns, args):
    print(f"入口校验：{'失败' if errors else '通过'}（错误 {len(errors)} / 警告 {len(warns)}）")
    for e in errors:
        print("  [ERROR]", e)
    for w in warns:
        print("  [WARN] ", w)
    if errors:
        print("→ 修正底稿后重跑本脚本；错误未清零禁止进入 Phase 2。")
        sys.exit(1)
    print("→ 校验通过。结果摘要（含警告）写入 manifest 与报告附录。")
    sys.exit(0)


if __name__ == "__main__":
    main()
