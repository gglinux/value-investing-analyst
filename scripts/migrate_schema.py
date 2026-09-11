#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_schema.py — 存量底稿 meta 块迁移工具（REQ-P0-03 配套）

## 为什么需要迁移脚本而不是手改

43 份底稿手工加 meta 块有两个问题：一是工作量，二是**手改本身是新的错误源**——
这条需求要解决的就是「靠人记得约定」不可靠，用手工迁移落地它是自相矛盾的。

存量字段的实际状态也说明必须机器归一化。扫描 43 份底稿发现：

- `unit` 有 4 种写法：`million`(40) / `亿元`(1) / `million USD（股本为 million shares）`(1)
  / `百万元（除每股/每股本数据）`(1)——后两种是自由文本夹带说明；
- `accounting_standard` 有 8 种写法，含 `US GAAP`（缺连字符）以及
  `US GAAP（FY2009-FY2014）→ IFRS（FY2015 重列起…）` 这种整段叙述。

这些变体正是强类型化要消灭的东西。脚本把它们归一化到受控词表，
**归一化不了的一律留空并报告**，不猜——猜错单位就是 OBS-600660-01 重演。

## 迁移策略：只增不改，可回滚

- 只**新增** `meta` 块，不删除、不修改任何既有顶层字段（旧字段仍是事实源，
  `schema_meta.resolve_meta` 会做兼容合并）；
- 自由文本里夹带的说明**不丢弃**，移入 `meta.notes` 留痕；
- `data_vintage` 与 `source_ref` 无法从存量字段推断，脚本按可得线索填充：
  vintage 取 crosscheck/publish_date 的最晚日期或案例回放日；source_ref 取
  crosscheck.source。两者都取不到则留空 → 该文件停在 legacy 档，
  由执行者补全后再升档（这是刻意的：编造溯源信息比没有溯源信息更危险）。
- 默认 dry-run，`--write` 才落盘。

用法：
    python3 scripts/migrate_schema.py --scan backtest cases          # 预演
    python3 scripts/migrate_schema.py --scan backtest cases --write  # 落盘
    python3 scripts/migrate_schema.py <file.json> --write
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_meta import (SCHEMA_VERSION_CURRENT, UNIT_MULTIPLIER,  # noqa: E402
                         check_unit_sanity, validate_meta)

# 自由文本 → 受控词表。键按长度降序匹配，避免 "million" 命中 "million USD（…）"
UNIT_NORMALIZE = {
    "million": "百万", "百万元": "百万", "百万": "百万", "mn": "百万",
    "亿元": "亿元", "亿": "亿元",
    "千元": "千元", "thousand": "千元", "万元": "万元",
    "billion": "十亿", "bn": "十亿",
    "元": "元", "yuan": "元",
}
STANDARD_NORMALIZE = {
    "US-GAAP": "US-GAAP", "US GAAP": "US-GAAP", "USGAAP": "US-GAAP",
    "CAS": "CAS", "IFRS": "IFRS", "HKFRS": "HKFRS",
    "Taiwan-IFRS": "IFRS", "JGAAP": "JGAAP", "K-IFRS": "K-IFRS",
}


def _normalize(raw, table):
    """从自由文本中提取受控词，返回 (受控值, 残留说明)。"""
    if not raw:
        return None, None
    s = str(raw).strip()
    for key in sorted(table, key=len, reverse=True):
        if re.search(re.escape(key), s, re.I):
            residue = re.sub(re.escape(key), "", s, count=1, flags=re.I).strip(" （）()，,。;；")
            return table[key], (residue or None)
    return None, s


def _infer_vintage(data, path):
    """数据可得日期：取 crosscheck / publish_date 中最晚者；再退回案例目录日期。"""
    dates = []
    for r in data.get("crosscheck") or []:
        if not isinstance(r, dict):
            continue  # 部分存量底稿的 crosscheck 是字符串列表
        for k in ("retrieved_at", "date", "as_of"):
            if isinstance(r.get(k), str) and re.match(r"^\d{4}-\d{2}-\d{2}$", r[k]):
                dates.append(r[k])
    for r in data.get("annual") or []:
        if not isinstance(r, dict):
            continue
        p = r.get("publish_date")
        if isinstance(p, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", p):
            dates.append(p)
    if dates:
        return max(dates), "由 crosscheck/publish_date 最晚日期推断"
    # 案例目录名形如 <ticker>_<YYYY-MM-DD>：回放日即数据截断日
    m = re.search(r"_(\d{4}-\d{2}-\d{2})", os.path.basename(os.path.dirname(os.path.dirname(path))))
    if m:
        return m.group(1), "由案例回放日推断（目录名）"
    return None, None


def _infer_source_ref(data):
    for r in data.get("crosscheck") or []:
        if isinstance(r, dict) and r.get("source"):
            return str(r["source"]), "取自 crosscheck[0].source"
        if isinstance(r, str) and len(r.strip()) >= 6:
            return r.strip(), "取自 crosscheck 字符串条目"
    return None, None


def build_meta(data, path):
    """从存量字段推断 meta 块，返回 (meta, 推断说明列表, 未能填充的字段)。"""
    notes, why, missing = [], [], []

    unit, u_res = _normalize(data.get("unit"), UNIT_NORMALIZE)
    if unit is None:
        missing.append("unit")
    if u_res:
        notes.append(f"原 unit 字段附注：{u_res}")

    cur = str(data.get("currency") or "").strip().upper() or None
    if cur is None:
        missing.append("currency")

    std, s_res = _normalize(data.get("accounting_standard"), STANDARD_NORMALIZE)
    if std is None:
        missing.append("standard")
    if s_res:
        notes.append(f"原 accounting_standard 字段附注：{s_res}")

    vintage, v_why = _infer_vintage(data, path)
    if vintage is None:
        missing.append("data_vintage")
    elif v_why:
        why.append(f"data_vintage：{v_why}")

    sref, s_why = _infer_source_ref(data)
    if sref is None:
        missing.append("source_ref")
    elif s_why:
        why.append(f"source_ref：{s_why}")

    meta = {
        # 全部字段齐备才升到 strict；缺任何一项停在 legacy，
        # 由执行者补全后手工改 schema_version——刻意不自动升档。
        "schema_version": SCHEMA_VERSION_CURRENT if not missing else 0,
        "unit": unit,
        "currency": cur,
        "basis": "consolidated",   # 存量底稿全部为合并报表（annual 区块口径）
        "standard": std,
        "period_type": "annual",
        "data_vintage": vintage,
        "source_ref": sref,
        "field_overrides": {},
    }
    # 从残留文本解析股本/每股单位——审查发现「百万元（除每股数据）」「股本为
    # million shares」被丢进 notes，机器仍不知道例外。能解析的落进标准键。
    residue_text = " ".join(x for x in (u_res, s_res) if x)
    su, psu = _parse_share_units(residue_text)
    if su:
        meta["shares_unit"] = su
        why.append(f"shares_unit={su}：由原 unit 附注解析")
    if psu:
        meta["per_share_unit"] = psu
        why.append(f"per_share_unit={psu}：由原 unit 附注解析")
    why.append("basis=consolidated / period_type=annual 为存量底稿统一口径（annual 区块）")
    if notes:
        meta["notes"] = notes
    meta["migrated_by"] = "scripts/migrate_schema.py"
    meta = {k: v for k, v in meta.items() if v is not None}

    # 升档前跑量纲哨兵：声明单位与数值量级不自洽的底稿**不得**贴 strict 标签。
    # 平安底稿（unit=million 而 revenue=105050600）此前被升到 strict 且校验通过，
    # 一份已知量纲错误的底稿挂着 strict 比没有标签更危险——读者会信它。
    if meta.get("schema_version") == SCHEMA_VERSION_CURRENT:
        probe = dict(data)
        probe["meta"] = meta
        sanity = check_unit_sanity(probe, path)
        if sanity:
            meta["schema_version"] = 0
            meta["unit_sanity_blocked"] = [s.split("量纲哨兵：", 1)[-1][:160] for s in sanity]
            missing.append("unit_sanity")
            why.append("量纲哨兵告警 → 拒绝升档，停在 legacy 待人工核实单位")
    return meta, why, missing


_SHARES_UNIT_PATTERNS = [
    (r"million\s*shares|百万股", "百万股"),
    (r"thousand\s*shares|千股", "千股"),
    (r"万股", "万股"),
    (r"亿股", "亿股"),
]


def _parse_share_units(text):
    """从自由文本附注解析 (shares_unit, per_share_unit)。解析不到返回 None。"""
    if not text:
        return None, None
    su = None
    for pat, val in _SHARES_UNIT_PATTERNS:
        if re.search(pat, text, re.I):
            su = val
            break
    psu = None
    # 「除每股数据」「每股为元」类说明 ⇒ 每股字段单位为本币元
    if re.search(r"除每股|每股.*(元|dollar|USD)|per[- ]share.*(yuan|dollar|USD)", text, re.I):
        psu = "元"
    return su, psu


def migrate_file(path, write=False, recheck=False):
    data = json.load(open(path, encoding="utf-8"))
    existing = data.get("meta") or {}
    if existing.get("schema_version"):
        if not recheck:
            return ("skip", path, [], [])
        # 已迁移底稿复检：strict 标签下量纲不自洽 → 降回 legacy
        sanity = check_unit_sanity(data, path)
        if existing.get("schema_version") >= SCHEMA_VERSION_CURRENT and sanity:
            existing["schema_version"] = 0
            existing["unit_sanity_blocked"] = [s.split("量纲哨兵：", 1)[-1][:160] for s in sanity]
            existing["demoted_at"] = "recheck: strict 档量纲不自洽"
            if write:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.write("\n")
            return ("demoted", path, ["量纲哨兵告警 → 降回 legacy"], ["unit_sanity"])
        return ("skip", path, [], [])
    meta, why, missing = build_meta(data, path)
    if write:
        # meta 置于顶部便于阅读：重建有序 dict
        out = {"meta": meta}
        out.update({k: v for k, v in data.items() if k != "meta"})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return ("done" if not missing else "partial", path, why, missing)


def main():
    ap = argparse.ArgumentParser(description="存量底稿 meta 块迁移（REQ-P0-03）")
    ap.add_argument("input", nargs="?", help="单个底稿 JSON")
    ap.add_argument("--scan", nargs="+", metavar="DIR", help="批量目录")
    ap.add_argument("--write", action="store_true", help="落盘（默认 dry-run）")
    ap.add_argument("--recheck", action="store_true",
                    help="对已迁移 strict 底稿重跑量纲哨兵，不自洽者降回 legacy")
    args = ap.parse_args()

    files = []
    if args.scan:
        for r in args.scan:
            files += sorted(glob.glob(os.path.join(r, "*", "data", "financials_*.json")))
    if args.input:
        files.append(args.input)
    if not files:
        ap.print_help()
        sys.exit(0)

    stats = {"done": 0, "partial": 0, "skip": 0, "demoted": 0}
    partials, demoted = [], []
    for f in files:
        st, p, why, missing = migrate_file(f, args.write, args.recheck)
        stats[st] += 1
        if st == "partial":
            partials.append((p, missing))
        elif st == "demoted":
            demoted.append(p)

    mode = "已落盘" if args.write else "预演（加 --write 落盘）"
    print(f"迁移{mode}：共 {len(files)} 份")
    print(f"  完整迁移（升 strict）：{stats['done']}")
    print(f"  部分迁移（留 legacy，待补字段）：{stats['partial']}")
    print(f"  跳过（已有 meta）：{stats['skip']}")
    if args.recheck:
        print(f"  复检降档（strict → legacy，量纲不自洽）：{stats['demoted']}")
        for p in demoted:
            print(f"    ↓ {os.path.relpath(p)}")
    if partials:
        print(f"\n待人工补全的底稿（{len(partials)} 份）——"
              f"缺 data_vintage/source_ref 不自动编造，补齐后手工把 "
              f"schema_version 改为 {SCHEMA_VERSION_CURRENT}：")
        for p, m in partials[:40]:
            print(f"  缺 {m}  {os.path.relpath(p)}")
    # 退出码语义（三版审查修订）：存在未完成迁移或降档 → 1，全量完成 → 0。
    # 此前无条件返回 0，作为收官门禁（CI/批次检查）调用时 21 份遗留也假通过。
    sys.exit(1 if (stats["partial"] or stats["demoted"]) else 0)


if __name__ == "__main__":
    main()
