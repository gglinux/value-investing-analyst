#!/usr/bin/env python3
"""触发价可达性检验 —— 「我给的这个价格现实吗」的机器回答。

## 为什么存在

第一批回放的两个实证：

- **茅台 2015-08**（OBS-2015-08-01）：触发价 166.93 元（25% 闸门反推），在
  2014-11-07（52 周低 145.5）之后至 2020 年**从未被触及**——「观察等价格」
  退化为永不触发的观察。系统给出档位时没有任何机制告诉用户"这个价格你可能
  永远等不到"。
- **福耀 2018-12**（OBS-600660-04）：触发价 16-17 元在 2020-03-23（16.36）
  **真实触发**，触发后 10 个月 +297%——但触发后没有承接流程，窗口白白流走。

两个案例同一根源：触发器没有被当作一等公民。本脚本回答第一个问题
（价格现实吗）；`verify_report.py` 的 TRIGGER_REEVAL_MISSING 门禁回答第二个
（触发后做什么）。

## 判定规则

以 52 周价格区间为参照带：

    band_position = (trigger − low_52w) / (high_52w − low_52w)

- trigger < low_52w → `TRIGGER_OUT_OF_HISTORY`：触发价在已观察到的历史区间
  之外。不是不可能，但"等它"的期望值要按"罕见事件"定价，报告必须显式承认。
- 0 ≤ band_position < 10% → `TRIGGER_LOW_REACHABILITY`：在带内但贴近下沿，
  须披露可达性。
- 否则 → `TRIGGER_REACHABLE`。

52 周区间只是**最低限度的参照**——更长窗口（3 年/5 年分布分位）更好，
快照里有就一并算（low_3y/high_3y 等可选字段）。

## 用法

    python3 scripts/trigger_reachability.py --trigger 166.93 \
        --snapshot data/market_snapshot.json [-o out.json]
    python3 scripts/trigger_reachability.py --trigger 166.93 --price 195.37 \
        --low52w 145.5 --high52w 290.0
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alert_codes import unknown_codes


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


def read_band(snapshot_path):
    """从快照读 52 周区间，兼容三套历史命名。返回 (low, high, source_note)。"""
    with open(snapshot_path, "r", encoding="utf-8") as f:
        d = json.load(f)
    low = d.get("low_52w")
    high = d.get("high_52w")
    if _num(low) is not None and _num(high) is not None:
        return _num(low), _num(high), "low_52w/high_52w"
    rng = d.get("range_52w")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        return _num(rng[0]), _num(rng[1]), "range_52w"
    if isinstance(rng, dict):
        return _num(rng.get("low")), _num(rng.get("high")), "range_52w"
    return None, None, None


def read_price(snapshot_path):
    with open(snapshot_path, "r", encoding="utf-8") as f:
        d = json.load(f)
    for k in ("price", "price_cny", "price_usd", "price_hkd", "price_a"):
        if _num(d.get(k)) is not None:
            return _num(d[k])
    p = d.get("price")
    if isinstance(p, dict):
        return _num(p.get("value"))
    return None


def assess(trigger, low52w, high52w, price=None):
    """核心判定。返回 (verdict, band_position_pct, codes, note)。"""
    codes = []
    if trigger < low52w:
        codes.append("TRIGGER_OUT_OF_HISTORY")
        gap = (low52w - trigger) / low52w
        return ("out_of_history", None, codes,
                f"触发价 {trigger} 低于 52 周最低 {low52w}（还差 {gap:.1%} 才到历史下限）"
                f"——历史上没人用这个价格买到过，「等它」按罕见事件定价")
    span = high52w - low52w
    if span <= 0:
        return ("unknown", None, [], "52 周区间退化（low ≥ high），无法判定")
    band = (trigger - low52w) / span
    if band < 0.10:
        codes.append("TRIGGER_LOW_REACHABILITY")
        note = (f"触发价在 52 周价格带底部 {band:.1%} 分位——可达性低，"
                f"报告须显式披露「该触发器实际可达性低」")
    else:
        codes.append("TRIGGER_REACHABLE")
        note = f"触发价在 52 周价格带 {band:.1%} 分位，可达性正常"
        if band < 0.20:
            # 边界声明（不改阈值）：52 周窗口只能抓「触发价在历史区间外」的
            # 极端退化；茅台案 166.93 元在带内 14.8% 分位、本脚本判可达，
            # 但事后 5 年未被触及——带内贴近下沿时，须自查更长窗口
            # （3-5 年价格分布）中该价位的触及历史，52 周说了不算。
            note += ("。但注意：带内位置已贴近下沿，52 周窗口无法识别"
                     "「多年未触及」的退化形态——须自查更长窗口（3-5 年）"
                     "该价位的触及历史再下可达性结论")
    if price is not None:
        drop = (price - trigger) / price
        note += f"；需自现价 {price} 下跌 {drop:.1%}"
    return ("low_reachability" if band < 0.10 else "reachable",
            band, codes, note)


def main():
    ap = argparse.ArgumentParser(description="触发价可达性检验")
    ap.add_argument("--trigger", type=float, required=True, help="触发价")
    ap.add_argument("--snapshot", help="market_snapshot.json（自动读 52 周区间与现价）")
    ap.add_argument("--price", type=float, help="现价（--snapshot 存在时可省略）")
    ap.add_argument("--low52w", type=float, help="52 周最低价")
    ap.add_argument("--high52w", type=float, help="52 周最高价")
    ap.add_argument("-o", "--output", help="结果 JSON 输出路径")
    args = ap.parse_args()

    low, high, price = args.low52w, args.high52w, args.price
    band_src = "CLI 参数"
    if args.snapshot:
        if not os.path.exists(args.snapshot):
            cands = sorted(glob.glob(args.snapshot))
            if cands:
                args.snapshot = cands[0]
        low, high, src = read_band(args.snapshot)
        band_src = src or band_src
        if price is None:
            price = read_price(args.snapshot)

    if low is None or high is None:
        print("无法判定：缺 52 周价格区间（快照无 low_52w/high_52w/range_52w，"
              "且未用 --low52w/--high52w 传入）")
        sys.exit(2)

    verdict, band, codes, note = assess(args.trigger, low, high, price)
    unknown = unknown_codes(codes)
    if unknown:
        raise KeyError(f"未注册的告警码 {unknown}")

    print(f"触发价可达性：trigger={args.trigger}  52周区间=[{low}, {high}]"
          + (f"  现价={price}" if price else ""))
    print(f"  判定：{verdict}（{band_src}）")
    if band is not None:
        print(f"  带内位置：{band:.1%}")
    print(f"  {note}")
    print(f"  告警码：{' '.join(codes)}")

    result = {"trigger": args.trigger, "price": price,
              "low_52w": low, "high_52w": high, "band_source": band_src,
              "verdict": verdict, "band_position": band, "codes": codes,
              "note": note,
              "reachable": verdict in ("reachable",)}
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    sys.exit(0 if verdict in ("reachable",) else 1)


if __name__ == "__main__":
    main()
