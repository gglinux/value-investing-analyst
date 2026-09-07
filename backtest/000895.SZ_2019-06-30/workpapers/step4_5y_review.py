#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双汇 000895.SZ Step4 事后 5 年复核（回放 2019-06-28 → 复盘 2024-06-28）
数据：workpapers/mk_adj_201906_202406.txt（东财月K三口径，2026-09-07 同批抓取）

口径说明（本案方法论要点）：
- fqt=0 不复权：与内在价值/触发价比较的唯一合法口径（情景值是每股内在价值，含分红权益）
- fqt=1 东财前复权 = 等差（减法）复权：P_adj = P_raw − 之后累计分红。
  序列比值 ≠ 分红再投资总回报（分红被当作零收益直接减掉），仅适合形态展示。
  本脚本用逐月差值反推除息序列证明其等差性质。
- fqt=2 等比后复权：因子从上市累积，比值 = 精确"除息日再投资"总回报（全收益口径）。
  2019-06-28 值 388.26 与回放时点（2019-06-30 会话）独立抓取值完全一致——无基准漂移。
"""
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
rows = []
with open(os.path.join(BASE, "mk_adj_201906_202406.txt"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d, adj, raw, post = line.split(",")
        rows.append((d, float(adj), float(raw), float(post)))

p0_adj, p0_raw, p0_post = rows[0][1], rows[0][2], rows[0][3]
p1_adj, p1_raw, p1_post = rows[-1][1], rows[-1][2], rows[-1][3]
YEARS = 5.0

# ── 1. 三口径 5 年回报 ──────────────────────────────────────────
price_ret = p1_raw / p0_raw - 1                     # 不复权价格回报
total_ret = p1_post / p0_post - 1                   # 等比后复权 = 总回报（再投资）
adj_ret = p1_adj / p0_adj - 1                       # 等差前复权（有偏口径）
div_factor = (p1_post / p0_post) / (p1_raw / p0_raw)  # 分红再投资乘子

def ann(r):
    return (1 + r) ** (1 / YEARS) - 1

print("=" * 64)
print("双汇 000895.SZ  2019-06-28 → 2024-06-28  五年复核")
print("=" * 64)
print(f"不复权收盘        {p0_raw:.2f} → {p1_raw:.2f}")
print(f"价格回报          {price_ret:+.2%}   年化 {ann(price_ret):+.2%}")
print(f"总回报(后复权)    {total_ret:+.2%}   年化 {ann(total_ret):+.2%}   ← 精确全收益口径")
print(f"分红再投资乘子    {div_factor:.4f}  (累计 {div_factor-1:+.2%}, 年化贡献 {ann(div_factor-1):+.2%})")
print(f"等差前复权口径    {adj_ret:+.2%}   ← 有偏：高估 {(adj_ret-total_ret)*100:.1f}pct（方法论登记）")

# ── 2. 等差前复权逐月差值反推除息序列 ──────────────────────────
print("-" * 64)
print("等差前复权性质证明：P_raw − P_adj 逐月差值（恒定=无除息，跳降=除息月）")
diffs = [(d, round(r - a, 2)) for d, a, r in zip([x[0] for x in rows], [x[1] for x in rows], [x[2] for x in rows])]
prev, exdiv = None, []
for d, df in diffs:
    if prev is not None and df < prev - 0.005:
        exdiv.append((d, round(prev - df, 2)))
    prev = df
print(f"  起点差值(基准日前累计分红): {diffs[0][1]}")
print(f"  终点差值: {diffs[-1][1]}")
print(f"  窗口内除息 {len(exdiv)} 次，合计 {sum(x[1] for x in exdiv):.2f} 元/股：")
for d, amt in exdiv:
    print(f"    {d}  除息 {amt:.2f} 元")

# ── 3. 三情景值/触发价 vs 五年价格轨迹（不复权口径） ────────────
print("-" * 64)
scen = {"悲观值": 16.32, "基准值": 20.48, "乐观值": 26.76, "触发价": 12.29}
raws = [(d, r) for d, _, r, _ in rows]
lo = min(raws, key=lambda x: x[1])
hi = max(raws, key=lambda x: x[1])
print(f"五年不复权最低月收盘 {lo[1]:.2f}（{lo[0]}）/ 最高月收盘 {hi[1]:.2f}（{hi[0]}）")
for name, v in scen.items():
    crossed_lo = [d for d, r in raws if r < v]
    crossed_hi = [d for d, r in raws if r > v]
    if name == "触发价":
        print(f"  {name} {v:>6.2f}: {'触及 @ ' + str(crossed_lo[0]) if crossed_lo else '五年从未触及'}"
              f"（最近距离 {min(abs(r-v) for _, r in raws):.2f} 元， {(min(r for _, r in raws)/v-1)*100:+.1f}%）")
    elif name == "乐观值":
        first = crossed_hi[0] if crossed_hi else None
        print(f"  {name} {v:>6.2f}: {'✗ 被突破 @ ' + str(first) + '（月收盘 ' + str([r for d, r in raws if d == first][0]) + '）' if first else '未被突破'}")
    else:
        print(f"  {name} {v:>6.2f}: {'触及 @ ' + str(crossed_lo[0]) if crossed_lo else '未触及'}"
              f"（最低 {lo[1]:.2f} = 值的 {lo[1]/v:.2f}x，差 {(lo[1]/v-1)*100:+.1f}%）")

# ── 4. 事后对照引擎期望 ────────────────────────────────────────
print("-" * 64)
print(f"回放时点引擎期望 IRR 5.55%  vs  实际年化总回报 {ann(total_ret):+.2%}（偏差 {ann(total_ret)*100-5.55:+.2f}pct）")
print(f"gate2② 不收敛下限 7.83%（股息5.83%+iv_growth2%）  vs  实际 {ann(total_ret):+.2%}")
print(f"悲观 IRR +1.10% < 实际 {ann(total_ret):+.2%} < 期望 IRR 5.55%（落在悲观-期望带内，偏中下）")
print(f"2020-08 峰值：不复权月收盘 {63.50:.2f}（{63.50/p0_raw-1:+.1%}），后复权总回报峰值 {827.46/p0_post-1:+.1%}")
print(f"2021-08 谷点：不复权月收盘 24.10（{24.10/63.50-1:+.1%} 自峰值，{24.10/p0_raw-1:+.1%} 自回放价）")
print("=" * 64)

# 落盘 JSON 供 answer.json 引用
out = {
    "window": ["2019-06-28", "2024-06-28"],
    "price_return": round(price_ret, 4),
    "price_annualized": round(ann(price_ret), 4),
    "total_return_post": round(total_ret, 4),
    "total_return_annualized": round(ann(total_ret), 4),
    "dividend_reinvest_factor": round(div_factor, 4),
    "adj_method_bias": round(adj_ret - total_ret, 4),
    "exdiv_in_window": [{"date": d, "amount": a} for d, a in exdiv],
    "exdiv_total": round(sum(a for _, a in exdiv), 3),
    "low_close": {"date": lo[0], "price": lo[1]},
    "high_close": {"date": hi[0], "price": hi[1]},
}
with open(os.path.join(BASE, "step4_5y_review_result.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("saved → workpapers/step4_5y_review_result.json")
