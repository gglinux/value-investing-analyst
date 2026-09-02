#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verification_strength.py — 核验强度标签（Phase 5，报告首屏强制披露）

★ 为什么必须有这个标签 ★

本 skill 有五环校验（官方源 → validate_data → 底稿 → compute_metrics → verify_report），
交付时会打印「校验通过（退出码 0）」。但这句话实际保证的范围远小于读者的理解：

  · verify_report 保证「报告 = 底稿」，不保证底稿为真；
  · validate_data 的三表勾稽**只在科目齐备的年份执行，缺科目的年份是跳过而非通过**
    —— GOOG/TSM 曾以 18% 覆盖率通过校验，等于 11 年里只有 2 年真做了勾稽；
  · crosscheck 双源核对是**分析方自填区块**，source 的官方性靠关键词匹配判定，
    编一个数字配一行「2025年报 p.45」即可过闸；
  · 10 年数据窗口对银行/保险/大宗商品不足一轮完整周期（2016-2026 没有一次
    系统性信用出清），此时「全期均值正常化」没有周期含义。

结论：**绿色勾给出的置信度高于它实际的保证范围**，而这恰恰是最危险的地方。
故把三项核验强度指标提到首屏，与结论同屏呈现，让读者看到「这个结论建立在
多硬的数据上」，而不是把限制埋进附录。

═══ 三项指标 ═══
V1 三表勾稽覆盖率 = 三科目（total_assets/total_liabilities/total_equity）齐备的年份
   ÷ 年度总数。<60% 为 weak（validate_data 的阻断线），<90% 为 partial。
V2 命门科目原文比对等级 = 最近 3 年 crosscheck 是否齐备、source 是否指向官方原文。
   full（3 年齐备且全为官方原文）/ partial（部分或含降级来源）/ none（无 crosscheck）。
   **无论哪一级都要在首屏写明「未经原文机器逐字比对」**——脚本只能核验来源字符串
   形态，无法核验数字本身，这个局限必须让读者知道。
V3 数据窗口 = 年度覆盖年数 + 是否包含上一轮系统性压力年。
   压力年清单见 STRESS_YEARS：窗口内不含任何压力年 → 该标的的「历史最差」
   其实没被观测到，周期正常化与悲观情景的历史锚都不成立。
   金融/大宗商品类（--cycle-sensitive）要求 ≥15 年，其余 ≥10 年。

总评 A/B/C：三项全绿 A；任一 partial 且无 weak/fail → B；任一 weak/fail → C。

用法：
    python3 verification_strength.py --financials data/financials_X.json \
        [--manifest data/manifest.json] [--cycle-sensitive] \
        [-o data/verification_strength.json] [--emit-html]
退出码：0 正常输出（C 级不报错——它是要被披露的事实，不是要被拦住的错误）；
        1 输入缺失；3 脚本自身异常。
"""
import argparse
import json
import os
import sys
from datetime import date

# 与 validate_data.py 共用的官方原文来源特征（保持两处一致，改一处须同步）
OFFICIAL_SOURCE_HINTS = ["10-K", "10K", "20-F", "20F", "审计", "年报", "annual report",
                         "Annual Report", "EDGAR", "巨潮", "披露易", "cninfo", "hkexnews",
                         "XBRL", "官网"]
DOWNGRADE_SOURCE_HINTS = ["加总", "接口", "估算", "推算"]
RECON_KEYS = ("total_assets", "total_liabilities", "total_equity")
# 银行/保险走专属管道，底稿 schema 不同（无 total_liabilities，负债由资产−权益倒算；
# 取证重点是贷款质量三件套而非三表勾稽）。用通用科目判会一律得 0% 覆盖率——
# 那是 schema 不匹配造成的假警报，不是真的没做取证。
RECON_KEYS_BANK = ("total_assets", "total_equity", "gross_loans",
                   "npl_balance", "provision_balance")
FINANCIAL_TYPES = {"bank", "银行", "insurance", "保险", "broker", "券商",
                   "securities", "金融", "financial"}
CROSSCHECK_MIN_YEARS = 3
# 系统性压力年：窗口必须至少覆盖一次，否则「历史最差」未被观测到。
# 口径说明：全球性（2008 次贷、2020 疫情）+ 中国特有（2015 股灾去杠杆、
# 2018 去杠杆+贸易战、2022 疫情管控与地产出清）。
STRESS_YEARS = {2008: "全球金融危机", 2015: "A股股灾与去杠杆", 2018: "去杠杆与贸易战",
                2020: "新冠冲击", 2022: "地产出清与疫情管控"}
MIN_YEARS_DEFAULT = 10
MIN_YEARS_CYCLE = 15


def _is_official(source):
    s = source or ""
    # 顺序敏感：官方标识是强证据，命中即认定官方；降级词仅在无官方标识时生效
    if any(h in s for h in OFFICIAL_SOURCE_HINTS):
        return True
    if any(h in s for h in DOWNGRADE_SOURCE_HINTS):
        return False
    return False


def assess(fin, manifest=None, cycle_sensitive=False, today=None):
    annual = fin.get("annual") or []
    years = [r.get("year") for r in annual if r.get("year") is not None]
    n = len(annual)
    out = {"company": fin.get("company"), "ticker": fin.get("ticker"),
           "annual_rows": n, "year_min": min(years) if years else None,
           "year_max": max(years) if years else None}

    # ---- V1 取证覆盖率（按公司类型选科目集）----
    ctype = (fin.get("company_type") or "").strip().lower()
    is_financial = ctype in FINANCIAL_TYPES or (fin.get("company_type") or "") in FINANCIAL_TYPES
    keys = RECON_KEYS_BANK if is_financial else RECON_KEYS
    full_rows = [r for r in annual if all(r.get(k) is not None for k in keys)]
    cov = (len(full_rows) / n) if n else None
    out["reconciliation"] = {
        "schema": "bank/insurance（贷款质量三件套 + 资产/权益）" if is_financial
                  else "general（三表勾稽）",
        "keys_required": list(keys),
        "covered_years": len(full_rows), "total_years": n, "coverage": cov,
        "level": ("weak" if cov is None or cov < 0.60 else
                  "partial" if cov < 0.90 else "full"),
        "note": "取证检查只在科目齐备的年份执行，缺科目的年份是跳过而非通过。"
                "覆盖率必须与「0 错误」一起读（GOOG/TSM 曾以 18% 覆盖率通过校验）。"
                + ("金融类无 total_liabilities（负债=资产−权益倒算），"
                   "取证重点是贷款质量三件套，故用专属科目集判定" if is_financial else ""),
    }
    if manifest and manifest.get("reconciliation_coverage_waiver"):
        out["reconciliation"]["waiver"] = manifest["reconciliation_coverage_waiver"]

    # ---- V2 命门科目原文比对等级 ----
    cc = fin.get("crosscheck") or []
    recent = sorted({int(c["year"]) for c in cc if c.get("year") is not None},
                    reverse=True)[:CROSSCHECK_MIN_YEARS]
    official = [c for c in cc if c.get("year") in recent and _is_official(c.get("source"))]
    off_years = {c["year"] for c in official}
    if len(recent) >= CROSSCHECK_MIN_YEARS and len(off_years) >= CROSSCHECK_MIN_YEARS:
        lvl = "full"
    elif cc:
        lvl = "partial"
    else:
        lvl = "none"
    out["crosscheck"] = {
        "years_registered": recent, "years_official_source": sorted(off_years),
        "level": lvl,
        "machine_verified_against_source_text": False,
        "note": "脚本只能核验 source 字符串是否指向官方原文的**形态**，"
                "无法核验数字本身是否与原文一致——编一个数字配一行「2025年报 p.45」"
                "同样过闸。因此本项无论哪一级，首屏都必须写明「未经原文机器逐字比对」",
    }

    # ---- V3 数据窗口 ----
    span = (max(years) - min(years) + 1) if years else 0
    # 金融类天然是周期敏感（信用周期），自动提高窗口要求，不依赖调用方记得传 flag
    cycle_sensitive = bool(cycle_sensitive or is_financial)
    need = MIN_YEARS_CYCLE if cycle_sensitive else MIN_YEARS_DEFAULT
    covered_stress = {y: name for y, name in STRESS_YEARS.items() if years and min(years) <= y <= max(years)}
    out["data_window"] = {
        "span_years": span, "required_years": need,
        "cycle_sensitive": cycle_sensitive,
        "stress_years_covered": covered_stress,
        "covers_full_cycle": bool(span >= need and covered_stress),
        "level": ("full" if span >= need and covered_stress else
                  "partial" if covered_stress else "weak"),
        "note": "窗口内不含任何系统性压力年时，该标的的「历史最差」并未被观测到——"
                "周期正常化的均值口径与悲观情景的历史锚都不成立，"
                "此时正常化基期与悲观情景必须改用行业基率或同类死亡案例",
    }

    levels = [out["reconciliation"]["level"], out["crosscheck"]["level"],
              out["data_window"]["level"]]
    if "weak" in levels or "none" in levels:
        grade = "C"
    elif "partial" in levels:
        grade = "B"
    else:
        grade = "A"
    out["grade"] = grade
    out["grade_meaning"] = {
        "A": "三项均达标：结论建立在完整勾稽 + 官方原文登记 + 覆盖完整周期的数据上",
        "B": "存在部分核验缺口：结论可用，但相关章节须显式降低置信度",
        "C": "核验强度不足：结论的数据地基有明确缺口，档位判定须从严并在首屏说明",
    }[grade]
    out["generated_at"] = (today or date.today()).isoformat()
    return out


def badge_html(v):
    """首屏徽章 HTML。data-* 属性供 verify_report.py 机器比对，禁止手写数值。"""
    r, c, w = v["reconciliation"], v["crosscheck"], v["data_window"]
    cov = r["coverage"]
    zh = {"full": "达标", "partial": "部分", "weak": "不足", "none": "缺失"}
    return (
        f'<div class="verification-strength" data-verification-strength="1"\n'
        f'     data-grade="{v["grade"]}"\n'
        f'     data-reconciliation-coverage="{cov:.4f}"\n'
        f'     data-crosscheck-level="{c["level"]}"\n'
        f'     data-window-years="{w["span_years"]}"\n'
        f'     data-covers-full-cycle="{str(w["covers_full_cycle"]).lower()}">\n'
        f'  <b>核验强度 {v["grade"]}</b>：'
        f'三表勾稽覆盖率 {cov:.0%}（{zh[r["level"]]}） ｜ '
        f'命门科目原文登记 {zh[c["level"]]}（<u>未经原文机器逐字比对</u>） ｜ '
        f'数据窗口 {w["span_years"]} 年'
        f'{"，含" + "、".join(w["stress_years_covered"].values()) if w["stress_years_covered"] else "，<u>未覆盖任何系统性压力年</u>"}'
        f'（{zh[w["level"]]}）<br>\n'
        f'  <span class="vs-meaning">{v["grade_meaning"]}</span>\n'
        f'</div>'
    )


def main():
    ap = argparse.ArgumentParser(description="核验强度标签（首屏强制披露）")
    ap.add_argument("--financials", required=True, help="data/financials_<公司>.json")
    ap.add_argument("--manifest", help="data/manifest.json（读豁免登记）")
    ap.add_argument("--cycle-sensitive", action="store_true",
                    help="金融/大宗商品/强周期标的：数据窗口要求从 10 年提高到 15 年")
    ap.add_argument("--emit-html", action="store_true", help="打印首屏徽章 HTML")
    ap.add_argument("-o", "--output", help="输出 JSON 路径（verify_report 会读它做门禁比对）")
    args = ap.parse_args()

    if not os.path.exists(args.financials):
        print(f"错误：底稿不存在 {args.financials}")
        sys.exit(1)
    try:
        with open(args.financials, "r", encoding="utf-8") as f:
            fin = json.load(f)
        manifest = None
        if args.manifest and os.path.exists(args.manifest):
            with open(args.manifest, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        v = assess(fin, manifest, args.cycle_sensitive)
    except Exception as e:  # noqa: BLE001
        print(f"[EXCEPTION] {type(e).__name__}: {e}")
        sys.exit(3)

    r, c, w = v["reconciliation"], v["crosscheck"], v["data_window"]
    print(f"核验强度：{v['grade']} —— {v['grade_meaning']}")
    print(f"  V1 三表勾稽覆盖率 {r['coverage']:.0%}"
          f"（{r['covered_years']}/{r['total_years']} 年，{r['level']}）")
    print(f"  V2 命门科目原文登记 {c['level']}"
          f"（官方原文年份 {c['years_official_source']}）"
          f"；未经原文机器逐字比对 = True")
    print(f"  V3 数据窗口 {w['span_years']} 年 / 要求 {w['required_years']} 年"
          f"，覆盖压力年 {list(w['stress_years_covered'].values()) or '无'}"
          f"（{w['level']}）")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {args.output}")
    if args.emit_html:
        print("\n--- 首屏徽章（粘到 report-header 内，verdict-banner 之后）---")
        print(badge_html(v))
    sys.exit(0)


if __name__ == "__main__":
    main()
