#!/usr/bin/env python3
"""source_tier 存量标签回填修正（REQ-P0-09）。

背景：REQ-P0-07 存量补录（2026-09-14）用 crosscheck_official.source_tier 关键词
推断批量生成显式 source_tier。但该函数当时存在乐观偏置缺陷——从 tier1 起「首次命中
即返回」，而「年报/年度报告」在任何转引描述里都必然出现，导致二手源被判为监管官方源。
缺陷经函数批量放大到全库，且显式标签会短路推断，形成永久固化。

本脚本在缺陷修复后重新回填，两类改写：
  A. tier 等级本身错了（如二手源被标 tier1 官方源）→ 按修正后推断改为对应等级的 key；
  B. tier 等级对但官方源 key 与实际市场不符（如港股/A股被标 edgar_xbrl）→ 按 source
     文本判定的市场改 key。

市场归属**从 source 文本判定**，不依赖目录名——cases/ 下的案例目录名无市场后缀
（cmb/popmart/yili），按目录猜会把正确标签改坏（实测教训）。文本无法判定市场时，
保留原有 key 不动（宁可不改，不可改错）。

只改 source_tier / source_tier_note 两个 meta 字段，**不动任何财务数值**。
"""
import json
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crosscheck_official as C  # noqa: E402

NOTE = ("修正：REQ-P0-07 补录时由有缺陷的 source_tier 推断生成（转引描述含「年报」"
        "被误判官方源），REQ-P0-09 修复推断函数后重新回填 2026-09-15")

# source 文本 → 监管辖区。按最具辨识度的凭证特征判，不猜。
MARKET_MARKERS = [
    ("hkex_pdf", ("披露易", "hkexnews", "hkex", "ltn2", "港交所", "業績", "綜合損益")),
    ("cninfo_pdf", ("巨潮", "cninfo", "上交所", "深交所", "上证", "深证", "finalpage",
                    "年度报告摘要", "年报摘要")),
    ("edinet_pdf", ("决算短信", "決算短信", "tanshin", "edinet", "有価証券")),
    ("edgar_xbrl", ("10-k", "10k", "20-f", "20f", "edgar", "xbrl", "companyfacts",
                    "sec.gov", "accession")),
]
TIER_TO_KEY = {2: "company_ir", 3: "westock", 4: "research_report", 5: "media"}
OFFICIAL_KEYS = {"edgar_xbrl", "cninfo_pdf", "hkex_pdf", "edinet_pdf"}


def market_key_from_text(src: str):
    """从 source 文本判监管辖区；判不出返回 None（保留原标签）。"""
    low = src.lower()
    for key, markers in MARKET_MARKERS:
        if any(m in low or m in src for m in markers):
            return key
    return None


def market_key_from_case(case: str):
    """从 backtest 案例目录的交易所后缀判辖区。

    与 market_key_from_text 的区别：后缀是案例的**标识**（600660.SH 就是上交所上市），
    属确证而非猜测。cases/ 下的目录名是公司简称无后缀（cmb/tencent/yili），返回 None
    继续挂账——那里的辖区须查底稿 filings 人工确认。
    """
    up = case.upper()
    if ".HK" in up:
        return "hkex_pdf"
    if ".SH" in up or ".SZ" in up:
        return "cninfo_pdf"
    if ".T_" in up:
        return "edinet_pdf"
    return None


def main() -> int:
    paths = sorted(glob.glob(os.path.join("backtest", "*", "data", "financials_*.json"))
                   + glob.glob(os.path.join("cases", "*", "data", "financials_*.json")))
    changed_files, report, kept = 0, [], []
    for path in paths:
        data = json.load(open(path, encoding="utf-8"))
        cc = data.get("crosscheck")
        if isinstance(cc, list):
            items = [x for x in cc if isinstance(x, dict)]
        elif isinstance(cc, dict):
            items = [v for v in cc.values() if isinstance(v, dict)]
        else:
            continue
        case = path.split(os.sep)[1]
        dirty = False
        for entry in items:
            src = str(entry.get("source") or "")
            have = entry.get("source_tier")
            if not src or not have:
                continue
            tier = C.source_tier(src)
            if tier == 1:
                # 先信文本（最具体，能识别 H 股条目落在 A 股案例里的情况），
                # 再退回案例代码后缀（交易所后缀是案例标识，属确证）。
                want = market_key_from_text(src) or market_key_from_case(case)
                if want is None:
                    # 辖区仍判不出（cases/ 下无后缀目录，描述只写「年报」）：**完全不动**。
                    # 腾讯/平安/招行不是 SEC 报送主体，原标签 edgar_xbrl 确实错，但正确值
                    # 须查底稿 filings 或原始公告后人工确认——脚本不猜辖区（默认美国
                    # 正是本次事故的成因）。挂账由 OBS 承载。
                    kept.append((case, entry.get("year"), have, src[:60]))
                    continue
            else:
                want = TIER_TO_KEY.get(tier, "media")
            if have == want:
                continue
            entry["source_tier"] = want
            entry["source_tier_note"] = NOTE
            report.append((case, entry.get("year"), have, want, tier, src[:70]))
            dirty = True
        if dirty:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            changed_files += 1

    print(f"回填完成：{changed_files} 份底稿 / {len(report)} 条 source_tier 改写\n")
    downgrades = [r for r in report if r[4] >= 4]
    print(f"— 实质降级（官方源 → 二手源）{len(downgrades)} 条：")
    for case, year, have, want, tier, src in downgrades:
        print(f"  ⚠️  {case:<26} {str(year):<6} {have} -> {want} (tier{tier})")
        print(f"      {src}")
    print(f"\n— 辖区 key 纠正（tier 不变，仍为官方源）{len(report) - len(downgrades)} 条：")
    for case, year, have, want, tier, src in report:
        if tier < 4:
            print(f"  {case:<26} {str(year):<6} {have} -> {want}")
    if kept:
        print(f"\n— 辖区判不出、保留原官方源标签 {len(kept)} 条：")
        for case, year, have, src in kept:
            print(f"  {case:<26} {str(year):<6} 保留 {have}｜{src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
