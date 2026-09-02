#!/usr/bin/env python3
"""生意驱动因子校验（Phase 2 出口关卡，v2.13 新增）

## 为什么需要这道闸

Phase 2 名为"定量画像"，但此前**只画了财务的像，没画生意的像**。
实证普查 25 份 metrics 底稿：驱动因子字段命中数为 **0**——
台积电没有晶圆出货量/ASP，泡泡玛特没有门店数/单店收入，微博没有 MAU/ARPU。
metric-playbook 为十类商业模式规定了专属指标，但那些指标
**从未进入任何一份结构化底稿**。

后果不是"少算了几个数"，而是三条链路同时断裂：

1. **视角断裂**：收入是会计结果，不是生意本身。所有者看的是
   "卖了多少个 × 每个赚多少"，收入只是它们的乘积。只看收入增速，
   看不出增长是靠涨价（可持续）还是靠铺货（可能是渠道库存）。
2. **溯源断裂**：这些运营数字照样出现在报告正文里（台积电"晶圆代工 72%"、
   泡泡玛特"门店 +46 家"），但底稿里没有 → 违反"底稿是唯一事实源"，
   且 verify_report 的 vnum 机制管不到它们（实测运营数字 vnum 覆盖率 0%）。
   于是财务数字被三道闸守着，生意数字裸奔。
3. **估值断裂**：估值假设里的"收入增速 X%"无法回答"这个 X 是量增还是价增"，
   而这两者的可持续性、所需资本、竞争暴露完全不同。

本脚本把驱动因子提升为与财务底稿同级的一等公民：落盘 → 校验 → 可被引用。

## 用法

    python3 scripts/check_business_drivers.py data/business_drivers_<公司>.json \\
        [--metrics data/metrics_<公司>.json] [--company-type 品牌消费品]

## 底稿 schema（data/business_drivers_<公司>.json）

    {
      "company": "台积电", "ticker": "TSM",
      "company_type": "制造业",           # 必须与财务底稿一致
      "unit_note": "出货量单位=千片12吋当量；ASP单位=美元/片",
      "drivers": [
        {"year": 2024,
         "volume": 12100,                  # 量（件/片/用户数/门店数…）
         "price": 5800,                    # 价（ASP/ARPU/客单价…）
         "volume_label": "12吋当量晶圆出货(千片)",
         "price_label": "综合ASP(美元/片)",
         "source": "TSMC 2024 20-F p.62",  # 必填，A/B 级
         "source_level": "A"},
        ...
      ],
      "unit_economics": {                  # 单位经济（可选但强烈建议）
        "metric": "单片毛利", "value": 2600, "trend": "上升",
        "note": "先进制程占比提升带动", "source": "..."
      }
    }

量×价 ≈ 收入 是本脚本的核心勾稽（容差可配）——对不上说明口径有问题，
而口径对不上的驱动因子会误导估值假设，比没有更危险。

退出码：0 通过（可含警告）、1 不合格、3 脚本自身异常。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 各商业模式类型的推荐驱动因子（来自 metric-playbook 专属指标）
TYPE_DRIVERS = {
    "品牌消费品": ("销量/吨量", "吨价或单价"),
    "制造业": ("出货量/产量", "单位售价 ASP"),
    "互联网平台": ("MAU/DAU 或订单量", "ARPU 或客单价"),
    "SaaS": ("客户数/席位数", "ARPU 或 ACV"),
    "零售": ("门店数或订单量", "单店收入或客单价"),
    "公用事业": ("发电量/流量/吞吐量", "上网电价/通行费"),
    "资源": ("产量/销量", "产品价格"),
    "医药": ("处方量/销量", "单价（含集采降幅）"),
}
FINANCIAL_TYPES = ("银行", "保险", "券商")


def norm_type(t: str) -> str:
    t = (t or "").strip()
    for k in list(TYPE_DRIVERS) + list(FINANCIAL_TYPES):
        if k in t:
            return k
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("draft", help="business_drivers_<公司>.json")
    ap.add_argument("--metrics", help="metrics_<公司>.json，用于量×价≈收入勾稽")
    ap.add_argument("--company-type", help="覆盖底稿中的 company_type")
    ap.add_argument("--tolerance", type=float, default=0.15,
                    help="量×价 vs 收入 的允许偏差（默认 15%%，口径差异常见）")
    ap.add_argument("--min-years", type=int, default=5,
                    help="驱动因子最少覆盖年数（默认 5）")
    args = ap.parse_args()

    if not os.path.exists(args.draft):
        print(f"[错误] 底稿不存在：{args.draft}")
        return 1
    try:
        d = json.load(open(args.draft, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[异常] 底稿无法解析：{e}")
        return 3

    errors: list[str] = []
    warns: list[str] = []

    ctype = norm_type(args.company_type or d.get("company_type", ""))
    if not ctype:
        errors.append("company_type 缺失——驱动因子的选取依赖商业模式类型"
                      "（见 metric-playbook 十类）")

    if ctype in FINANCIAL_TYPES:
        print(f"[跳过] {ctype} 为金融类，驱动因子走专属管道"
              f"（净息差/NBV 等），本脚本不适用。")
        return 0

    rows = d.get("drivers") or []
    if not rows:
        errors.append("drivers 为空——Phase 2 必须落盘生意驱动因子。"
                      "收入是会计结果，量×价才是生意本身；"
                      f"{ctype} 建议口径：{TYPE_DRIVERS.get(ctype, ('量', '价'))}")

    # 逐年校验
    years = []
    for i, r in enumerate(rows):
        y = r.get("year")
        tag = f"drivers[{i}]" + (f"(year={y})" if y else "")
        if y is None:
            errors.append(f"{tag}: 缺 year")
            continue
        years.append(y)
        has_v = r.get("volume") is not None
        has_p = r.get("price") is not None
        if not has_v and not has_p:
            errors.append(f"{tag}: 量与价全缺，该行无信息量")
        # 量价必须带标签与单位，否则无法判断口径
        if has_v and not r.get("volume_label"):
            warns.append(f"{tag}: volume 缺 volume_label，口径不明将无法复核")
        if has_p and not r.get("price_label"):
            warns.append(f"{tag}: price 缺 price_label，口径不明将无法复核")
        if not r.get("source"):
            errors.append(f"{tag}: 缺 source——驱动因子同样受"
                          f"「底稿是唯一事实源」约束，来路不明即删除")
        lvl = (r.get("source_level") or "").upper()
        if lvl and lvl not in ("A", "B", "C"):
            errors.append(f"{tag}: source_level 非法（应为 A/B/C）：{lvl}")
        if lvl == "C":
            warns.append(f"{tag}: 驱动因子为 C 级（网络兜底），"
                         f"不得作为估值假设的唯一依据")

    if years and len(set(years)) < args.min_years:
        warns.append(f"驱动因子仅覆盖 {len(set(years))} 年"
                     f"（建议 ≥{args.min_years} 年）——"
                     f"少于一个完整周期无法判断量价趋势")
    if len(years) != len(set(years)):
        errors.append("drivers 存在重复年份")

    # ---- 核心勾稽：量 × 价 ≈ 收入 ----
    # 对不上说明口径有问题；口径错的驱动因子比没有更危险，
    # 因为它会以"看起来精确"的方式污染估值假设。
    if args.metrics and os.path.exists(args.metrics):
        try:
            m = json.load(open(args.metrics, encoding="utf-8"))
            rev = {s["year"]: s.get("revenue") for s in m.get("series", [])}
            checked = 0
            for r in rows:
                v, p, y = r.get("volume"), r.get("price"), r.get("year")
                if v is None or p is None or not rev.get(y):
                    continue
                implied = v * p
                actual = rev[y]
                # 量纲自动对齐：驱动因子的单位（万人×元、千片×美元）与财务底稿
                # 的单位（百万）几乎从不一致，硬比必然全线报错。
                # 做法：在 10 的幂次候选中选偏差最小的那个量纲，
                # 再判断剩余偏差是否真的超容差——这样报出来的偏差
                # 才是"口径不一致"，而不是"单位不同"。
                cands = [10.0 ** k for k in range(-6, 7)]
                best = min(cands, key=lambda s: abs(implied / s - actual))
                dev = abs(implied / best - actual) / abs(actual)
                checked += 1
                if dev > args.tolerance:
                    warns.append(
                        f"year={y}: 量×价 推算收入 {implied/best:,.0f} "
                        f"与底稿收入 {actual:,.0f} 偏差 {dev:.0%}"
                        f"（>{args.tolerance:.0%}，已做量纲对齐）——"
                        f"口径可能不一致（是否只覆盖部分分部？"
                        f"量价是否取自不同业务？）")
            if checked == 0 and rows:
                warns.append("无任何年份可做量×价≈收入勾稽"
                             "（量或价缺失）——驱动因子的可信度未经验证")
            else:
                print(f"  量×价≈收入勾稽：已核 {checked} 年")
        except Exception as e:  # noqa: BLE001
            warns.append(f"勾稽跳过（metrics 解析失败：{e}）")
    elif args.metrics:
        warns.append(f"--metrics 指向的文件不存在：{args.metrics}，跳过勾稽")

    # 单位经济：所有者视角的核心（卖一单赚多少）
    ue = d.get("unit_economics")
    if not ue:
        warns.append("缺 unit_economics——"
                     "「卖一单/服务一个用户赚多少钱」是商业模式分析的核心问题"
                     "（moat-framework 第一节第 2 问），建议落盘以便量化引用")
    elif not ue.get("source"):
        warns.append("unit_economics 缺 source")

    # 输出
    print(f"\n{'='*56}")
    print(f"生意驱动因子校验：{d.get('company', '?')} "
          f"({d.get('ticker', '?')}, {ctype})")
    print(f"  覆盖年份：{sorted(set(years)) if years else '无'}")
    for w in warns:
        print(f"  [警告] {w}")
    for e in errors:
        print(f"  [错误] {e}")
    print(f"{'='*56}")
    if errors:
        print(f"结果：不合格（{len(errors)} 错误，{len(warns)} 警告）")
        return 1
    print(f"结果：通过（{len(warns)} 警告）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[异常] 校验器自身错误：{exc}")
        sys.exit(3)
