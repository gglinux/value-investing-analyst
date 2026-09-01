#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_transcription.py — 抽取产物 → 标准底稿 的"搬运完整性"校验（v2.11 新增）

为什么需要这道独立检查
----------------------
数据链路是三段式：**采集（接口/EDGAR）→ 建稿（人工/半人工整理成标准底稿）→ 计算**。
既有门禁只守住了两端：
  - validate_data.py 守"底稿 ≈ 事实"（勾稽、双源、突变）
  - verify_report.py 守"报告 = 底稿"
**中间那段"抽取产物 → 底稿"的搬运，此前完全没有校验。**

这不是理论风险，是已发生的事故：
  GOOG 案例中 raw_edgar_annual.json **9 个年度都成功抓到了 assets/liabilities/equity**，
  但最终 financials_GOOG.json 里只有 2024 一年有 total_assets/total_liabilities，
  其余 8 年是 null。后果是三表勾稽（最核心的取证检查）在 11 年里只跑了 2 年，
  而入口校验依然打印"通过（0 错误）"——因为缺字段只降级成 WARN。
  TSM 同样是 2/11。数据不是拿不到，是**搬运时丢了且无人知晓**。

这类丢失的性质：不是"数据源没有"，而是"我们有、但没搬进去"。
它比数据缺失更危险——分析师以为自己有 11 年勾稽，实际只有 2 年。

用法
----
    python3 check_transcription.py --raw raw_edgar_annual.json \
        --draft financials_GOOG.json [--unit-scale 1e6] [--tol 0.01]

退出码：0 通过（可含警告）；1 发现搬运丢失/不一致；3 脚本内部异常。
"""
import argparse
import json
import sys

# 抽取字段名 → 标准底稿字段名（两侧命名不一致本身就是丢失的温床）
# 值为 list 时表示"底稿该字段可能对应多个抽取口径"，任一匹配即算搬运正确。
# cash 是典型：底稿的 `cash` 在估值语境下通常取"现金+短期投资"（cash_sti），
# 而非狭义的库存现金（cash）——GOOG 底稿正是如此（2024: 95657 = cash_sti）。
# 若死板对应狭义 cash，会把"口径选得对"误报成"搬运错了"，制造 9 条假错误。
FIELD_MAP = {
    "revenue": "revenue",
    "net_income": "net_income",
    "ocf": "ocf",
    "capex": "capex",
    "gross_profit": "gross_profit",
    "op_income": "op_income",
    "depreciation": "da",
    "shares_diluted": "shares_diluted",
    "assets": "total_assets",
    "liabilities": "total_liabilities",
    "equity": "total_equity",
    "buyback": "buyback",
    "dividends": "dividends",
}

# 底稿字段 → 可接受的多个抽取口径（按优先级）。用于口径合法差异的豁免。
MULTI_SOURCE = {
    "cash": ["cash_sti", "cash"],
}

# 这些字段一旦在 raw 里存在却没搬进底稿，直接判 ERROR：
# 它们各自支撑一项核心检查，缺失会让检查静默跳过而非报错。
CRITICAL_FIELDS = {"assets", "liabilities", "equity", "revenue", "net_income", "ocf"}


def rel_diff(a, b):
    if a is None or b is None:
        return None
    base = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / base


def load_raw(path):
    """兼容两种抽取产物结构：{year: {...}} 或 {"annual":[{year,...}]}"""
    d = json.load(open(path, "r", encoding="utf-8"))
    if isinstance(d, dict) and "annual" in d and isinstance(d["annual"], list):
        return {str(r.get("year")): r for r in d["annual"] if r.get("year")}
    out = {}
    for k, v in d.items():
        if str(k).isdigit() and isinstance(v, dict):
            out[str(k)] = v
    return out


def main():
    ap = argparse.ArgumentParser(description="抽取产物→底稿 搬运完整性校验")
    ap.add_argument("--raw", required=True, help="抽取产物 JSON（如 raw_edgar_annual.json）")
    ap.add_argument("--draft", required=True, help="标准底稿 JSON（financials_*.json）")
    ap.add_argument("--unit-scale", type=float, default=None,
                    help="底稿单位 / 抽取单位。抽取常为元，底稿常为百万 → 传 1e6。"
                         "不传则自动按收入量级推断")
    ap.add_argument("--tol", type=float, default=0.01, help="相对容差，默认 1%%")
    args = ap.parse_args()

    raw = load_raw(args.raw)
    draft = json.load(open(args.draft, "r", encoding="utf-8"))
    drows = {str(r.get("year")): r for r in draft.get("annual", [])}

    errors, warns, infos = [], [], []
    if not raw:
        errors.append(f"抽取产物 {args.raw} 未解析出任何年度数据")
        return finish(errors, warns, infos)

    # --- 单位比例推断：搬运丢失最常伴随单位错位，先把口径对齐 ---
    scale = args.unit_scale
    if scale is None:
        cands = []
        for y, r in raw.items():
            rv, dv = r.get("revenue"), (drows.get(y) or {}).get("revenue")
            if rv and dv:
                try:
                    cands.append(float(rv) / float(dv))
                except (TypeError, ZeroDivisionError):
                    pass
        if cands:
            cands.sort()
            scale = cands[len(cands) // 2]
            infos.append(f"自动推断单位比例 scale={scale:,.0f}（抽取单位/底稿单位，取中位数）")
        else:
            scale = 1.0
            warns.append("无法推断单位比例（收入两侧无可比年份），按 1.0 处理")

    # --- 逐年逐字段核对：raw 有值 → 底稿必须有值且数值一致 ---
    common = sorted(set(raw) & set(drows))
    only_raw = sorted(set(raw) - set(drows))
    if only_raw:
        warns.append(f"抽取产物含底稿没有的年度 {only_raw}——"
                     "确认是有意裁剪（如只取近10年）还是建稿时漏年")
    if not common:
        errors.append("抽取产物与底稿没有任何共同年度，无法核对搬运")
        return finish(errors, warns, infos)

    lost = {}   # field -> [years]
    mismatch = []
    for y in common:
        r, d = raw[y], drows[y]
        for rk, dk in FIELD_MAP.items():
            rv = r.get(rk)
            if rv is None:
                continue
            dv = d.get(dk)
            if dv is None:
                lost.setdefault(rk, []).append(y)
                continue
            try:
                diff = rel_diff(float(rv) / scale, float(dv))
            except (TypeError, ValueError):
                continue
            if diff is not None and diff > args.tol:
                mismatch.append(f"{y}.{dk}: 底稿({dv}) vs 抽取({float(rv)/scale:,.2f}) 偏差 {diff:.1%}")

        # 多口径字段：底稿值只要匹配任一可接受口径即算搬运正确
        for dk, cands in MULTI_SOURCE.items():
            dv = d.get(dk)
            if dv is None:
                continue
            avail = [(c, r.get(c)) for c in cands if r.get(c) is not None]
            if not avail:
                continue
            best = None
            for cname, cval in avail:
                try:
                    diff = rel_diff(float(cval) / scale, float(dv))
                except (TypeError, ValueError):
                    continue
                if diff is not None and (best is None or diff < best[1]):
                    best = (cname, diff)
            if best and best[1] > args.tol:
                names = "/".join(c for c, _ in avail)
                mismatch.append(
                    f"{y}.{dk}: 底稿({dv}) 与任一抽取口径({names})均不匹配，"
                    f"最接近的 {best[0]} 偏差 {best[1]:.1%}")

    for f, years in sorted(lost.items()):
        dk = FIELD_MAP[f]
        msg = (f"搬运丢失：抽取产物中 `{f}` 有 {len(years)} 个年度有值，"
               f"底稿 `{dk}` 为空 —— 年份 {years}")
        if f in CRITICAL_FIELDS:
            errors.append(msg + "。该字段支撑核心检查（三表勾稽/命门科目），"
                                "缺失会让检查静默跳过：请从抽取产物补齐后重跑入口校验")
        else:
            warns.append(msg)

    for m in mismatch[:15]:
        errors.append("搬运不一致：" + m)
    if len(mismatch) > 15:
        errors.append(f"搬运不一致：另有 {len(mismatch) - 15} 处未列出")

    if not lost and not mismatch:
        infos.append(f"搬运完整性 OK：{len(common)} 个年度、{len(FIELD_MAP)} 个字段全部一致")
    return finish(errors, warns, infos)


def finish(errors, warns, infos):
    print(f"搬运校验：{'失败' if errors else '通过'}"
          f"（错误 {len(errors)} / 警告 {len(warns)}）")
    for i in infos:
        print("  [INFO] ", i)
    for e in errors:
        print("  [ERROR]", e)
    for w in warns:
        print("  [WARN] ", w)
    if errors:
        print("→ 抽取到的数据没能进底稿，或进错了。补齐后重跑 validate_data.py。")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        print("搬运校验：脚本内部异常（非数据问题）——退出码 3")
        print(f"  [FATAL] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        sys.exit(3)
