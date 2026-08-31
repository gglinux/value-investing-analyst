#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reverse_dcf.py — 反向 DCF 求解器（Phase 4 强制使用）

目的：用当前市值反推市场隐含预期，替代模型手算，保证解法一致、可复现。
两种模式：
  1. implied-growth：给定利润率/折现率等假设，反解现价隐含的收入增速 g
  2. forward-value：给定三情景假设，正向计算每股价值（供三情景 DCF 复用同一引擎）
  3. expected-return：三情景每股价值 + 概率 → 期望年化回报率/亏损概率（Phase 4.5 强制）

用法：
  python3 reverse_dcf.py implied-growth --market-cap 50000 --base-oe 2000 \
      --discount-rate 0.10 --terminal-growth 0.025 --years 10
  python3 reverse_dcf.py forward-value --base-oe 2000 --growth 0.12 \
      --discount-rate 0.10 --terminal-growth 0.025 --years 10 --shares 1000 \
      [--fade]   # 增速在预测期内线性衰减到永续增速（更保守、更真实）
  python3 reverse_dcf.py expected-return --price 209.75 --hold-years 5 \
      --scenarios "悲观:105:0.3,基准:212:0.5,乐观:397:0.2" --index-hurdle 0.09 \
      [--dividend-yield 0.05]   # 高股息标的必填，与门槛比较用含息 IRR

单位：market-cap / base-oe 用同一货币单位（建议百万）；shares 百万股。
base-oe = 基期 Owner Earnings（来自 compute_metrics.py 输出，保持口径一致）。
**周期高位公司必须用 compute_metrics 输出的 normalization.base_oe_recommended 作基期**，
禁止直接用当期 Owner Earnings（周期顶部利润外推是价值投资最经典的翻车方式）。
"""
import argparse
import json
import sys


def dcf_value(base_oe, growth, discount, terminal_g, years, fade=False):
    """两阶段 DCF：预测期 + Gordon 永续。fade=True 时增速线性衰减至 terminal_g。"""
    if discount <= terminal_g:
        raise SystemExit("错误：折现率必须大于永续增长率")
    pv = 0.0
    oe = base_oe
    for t in range(1, years + 1):
        g_t = growth + (terminal_g - growth) * (t - 1) / (years - 1) if (fade and years > 1) else growth
        oe = oe * (1 + g_t)
        pv += oe / ((1 + discount) ** t)
    terminal = oe * (1 + terminal_g) / (discount - terminal_g)
    pv += terminal / ((1 + discount) ** years)
    return pv


def solve_implied_growth(market_cap, base_oe, discount, terminal_g, years, fade=False):
    """二分法反解隐含增速 g ∈ (-50%, +60%)"""
    lo, hi = -0.5, 0.6
    f_lo = dcf_value(base_oe, lo, discount, terminal_g, years, fade) - market_cap
    f_hi = dcf_value(base_oe, hi, discount, terminal_g, years, fade) - market_cap
    if f_lo * f_hi > 0:
        return None  # 无解：现价超出该假设区间能解释的范围
    for _ in range(100):
        mid = (lo + hi) / 2
        f_mid = dcf_value(base_oe, mid, discount, terminal_g, years, fade) - market_cap
        if abs(f_mid) < 1e-6 * market_cap:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def expected_return(price, scenarios, hold_years, index_hurdle=0.09,
                    dividend_yield=0.0):
    """期望回报率引擎：把三情景估值转成"这笔钱年化几个点"。

    价值投资的决策变量不是"公司好不好"，而是"相对机会成本，这笔钱划不划算"。
    单点内在价值只能回答贵不贵，无法回答期望回报与亏损概率。

    scenarios: [{"name","value_per_share","probability"}, ...]，概率之和须为 1。
    假设持有期末股价回归各情景内在价值。
    dividend_yield: 预期持有期平均股息率（对成本价口径，如 0.05 = 5%）。
      股价回归假设不含股息，对长江基建/神华这类股息率 5~7% 的标的，五年漏掉
      25%+ 回报会导致双闸门系统性误杀——所以含息 IRR 才是与机会成本门槛比较的口径。
      近似算法：年化 IRR + 股息率（线性叠加，未复投，仍偏保守）。
    """
    total_p = sum(s["probability"] for s in scenarios)
    if abs(total_p - 1.0) > 1e-6:
        raise SystemExit(f"错误：三情景概率之和为 {total_p:.4f}，必须等于 1")
    if price <= 0:
        raise SystemExit("错误：现价必须为正")
    if dividend_yield < 0 or dividend_yield > 0.20:
        raise SystemExit("错误：股息率应在 0~20% 之间（按小数传入，如 0.05）")

    rows = []
    exp_irr = 0.0
    exp_terminal = 0.0
    loss_prob = 0.0
    downside = 0.0
    for s in scenarios:
        v, p = s["value_per_share"], s["probability"]
        total_ret = v / price - 1.0
        irr = (v / price) ** (1.0 / hold_years) - 1.0 if v > 0 else -1.0
        rows.append({
            "name": s["name"], "probability": p, "value_per_share": v,
            "total_return": total_ret, "annualized_irr": irr,
            "annualized_irr_incl_div": irr + dividend_yield,
        })
        exp_irr += p * irr
        exp_terminal += p * v
        # 亏损判定含股息缓冲：期末价值 + 累计股息 仍低于成本才算亏
        total_ret_incl_div = total_ret + dividend_yield * hold_years
        if total_ret_incl_div < 0:
            loss_prob += p
            downside += p * total_ret_incl_div

    exp_total = exp_terminal / price - 1.0
    exp_irr_incl_div = exp_irr + dividend_yield
    return {
        "price": price, "hold_years": hold_years,
        "dividend_yield": dividend_yield,
        "scenarios": rows,
        "expected_value_per_share": exp_terminal,
        "expected_total_return": exp_total,
        "expected_annualized_irr": exp_irr,
        "expected_annualized_irr_incl_div": exp_irr_incl_div,
        "loss_probability": loss_prob,
        "expected_downside_given_loss": downside / loss_prob if loss_prob > 0 else None,
        "probability_weighted_downside": downside,
        "index_hurdle": index_hurdle,
        "beats_index": exp_irr_incl_div > index_hurdle,
        "excess_vs_index": exp_irr_incl_div - index_hurdle,
        "note": "期末股价回归内在价值假设；含息 IRR = 不含息 IRR + 股息率（线性近似、未复投）；"
                "亏损概率按含股息口径判定；与机会成本门槛比较用含息 IRR",
    }


def main():
    ap = argparse.ArgumentParser(description="反向 DCF 求解器")
    sub = ap.add_subparsers(dest="mode", required=True)

    p1 = sub.add_parser("implied-growth", help="反解现价隐含增速")
    p1.add_argument("--market-cap", type=float, required=True, help="当前市值（剔除净现金后更严谨：用 EV 减净债）")
    p1.add_argument("--base-oe", type=float, required=True, help="基期 Owner Earnings")
    p1.add_argument("--discount-rate", type=float, default=0.10)
    p1.add_argument("--terminal-growth", type=float, default=0.025)
    p1.add_argument("--years", type=int, default=10)
    p1.add_argument("--fade", action="store_true", help="增速线性衰减到永续增速")
    p1.add_argument("--deduct", type=float, default=0.0,
                    help="从市值中剔除的非经营资产（净现金/投资组合折价可回收值，"
                         "与 market-cap 同币种同单位）——反解的是经营业务隐含增速")

    p2 = sub.add_parser("forward-value", help="给定假设正向估值")
    p2.add_argument("--base-oe", type=float, required=True)
    p2.add_argument("--growth", type=float, required=True, help="预测期增速（fade 模式下为期初增速）")
    p2.add_argument("--discount-rate", type=float, default=0.10)
    p2.add_argument("--terminal-growth", type=float, default=0.025)
    p2.add_argument("--years", type=int, default=10)
    p2.add_argument("--shares", type=float, help="摊薄股本（百万股），提供则输出每股价值")
    p2.add_argument("--fade", action="store_true")
    p2.add_argument("--add-back", type=float, default=0.0,
                    help="非经营资产加回总额（与 base-oe 同币种同单位）：净现金、"
                         "投资组合折价后可回收价值等。DCF 只估经营业务，"
                         "'经营+投资'双轮公司（如腾讯）与高净现金公司（如 PDD）必填，"
                         "否则系统性低估。折价论证写在 valuation.json（如上市9折/非上市6折）")
    p2.add_argument("--fx", type=float, default=1.0,
                    help="每股价值的币种换算系数（报告币→行情币），如 CNY→HKD 用 1.087。"
                         "默认 1.0 不换算")

    p3 = sub.add_parser("expected-return",
                        help="三情景转期望年化回报率（Phase 4.5 机会成本对照强制使用）")
    p3.add_argument("--price", type=float, required=True, help="当前股价（market_snapshot 底稿）")
    p3.add_argument("--hold-years", type=int, default=5, help="持有期，默认 5 年")
    p3.add_argument("--scenarios", required=True,
                    help='三情景每股价值与概率，格式："悲观:105.12:0.3,基准:211.65:0.5,乐观:396.52:0.2"')
    p3.add_argument("--index-hurdle", type=float, default=0.09,
                    help="机会成本门槛（指数长期年化），默认 9%%")
    p3.add_argument("--dividend-yield", type=float, default=0.0,
                    help="预期持有期平均股息率（小数，如 0.05）。高股息标的必填，"
                         "否则期望 IRR 系统性低估、双闸门误杀")
    p3.add_argument("-o", "--output", help="输出 JSON 路径")

    args = ap.parse_args()

    if args.mode == "expected-return":
        scen = []
        for part in args.scenarios.split(","):
            bits = part.split(":")
            if len(bits) != 3:
                raise SystemExit(f"情景格式错误：{part}，应为 名称:每股价值:概率")
            scen.append({"name": bits[0], "value_per_share": float(bits[1]),
                         "probability": float(bits[2])})
        res = expected_return(args.price, scen, args.hold_years, args.index_hurdle,
                              args.dividend_yield)
        print(f"现价 {res['price']:,.2f}，持有期 {res['hold_years']} 年，"
              f"股息率假设 {res['dividend_yield']:.1%}\n")
        print(f"{'情景':<8}{'概率':>8}{'每股价值':>12}{'总回报':>10}{'年化':>10}")
        for r in res["scenarios"]:
            print(f"{r['name']:<8}{r['probability']:>8.0%}{r['value_per_share']:>12,.2f}"
                  f"{r['total_return']:>10.1%}{r['annualized_irr']:>10.1%}")
        print(f"\n期望每股价值        : {res['expected_value_per_share']:,.2f}")
        print(f"期望总回报          : {res['expected_total_return']:.1%}")
        print(f"期望年化（不含息）  : {res['expected_annualized_irr']:.2%}")
        print(f"期望年化（含股息）  : {res['expected_annualized_irr_incl_div']:.2%}"
              f"{'  ← 与机会成本门槛比较用这个' if res['dividend_yield'] > 0 else ''}")
        print(f"亏损概率            : {res['loss_probability']:.0%}（含股息缓冲口径）")
        if res["expected_downside_given_loss"] is not None:
            print(f"亏损情景平均跌幅    : {res['expected_downside_given_loss']:.1%}")
        print(f"机会成本门槛（指数）: {res['index_hurdle']:.1%}")
        verdict = "跑赢" if res["beats_index"] else "跑输"
        print(f"结论：期望年化（含息）{verdict}指数 {abs(res['excess_vs_index']):.2%}"
              f"{'' if res['beats_index'] else '，机会成本不划算 → 结论最高只能给「观察等价格」'}")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            print(f"\n已写入 {args.output}")
        return

    if args.mode == "implied-growth":
        mc = args.market_cap - args.deduct
        if args.deduct:
            print(f"市值 {args.market_cap:,.0f} 剔除非经营资产 {args.deduct:,.0f}"
                  f" → 经营业务隐含市值 {mc:,.0f}")
        g = solve_implied_growth(mc, args.base_oe, args.discount_rate,
                                 args.terminal_growth, args.years, args.fade)
        if g is None:
            print("无解：在给定折现率/永续增速下，现价无法用 -50%~+60% 的增速解释。"
                  "说明市场定价隐含了其他假设（利润率跃迁、并购、或情绪定价），需在报告中明确讨论。")
            sys.exit(0)
        mode_note = "（增速线性衰减至永续）" if args.fade else "（增速恒定）"
        print(f"现价隐含的未来 {args.years} 年 Owner Earnings 年增速{mode_note}: {g:.2%}")
        print(f"假设：折现率 {args.discount_rate:.1%}，永续增速 {args.terminal_growth:.1%}")
        print("下一步：将该隐含增速与 Phase 3 基准情景增速对照，回答『市场预期苛刻还是宽松』。")
    else:
        v = dcf_value(args.base_oe, args.growth, args.discount_rate,
                      args.terminal_growth, args.years, args.fade)
        print(f"经营业务价值（Owner Earnings 口径 DCF）: {v:,.0f}")
        total = v + args.add_back
        if args.add_back:
            print(f"非经营资产加回: {args.add_back:,.0f} → 股权价值合计: {total:,.0f}")
        if args.shares:
            ps = total / args.shares
            if args.fx != 1.0:
                print(f"每股价值: {ps:,.2f}（报告币） = {ps * args.fx:,.2f}（行情币，fx={args.fx}）")
            else:
                print(f"每股价值: {ps:,.2f}")


if __name__ == "__main__":
    main()
