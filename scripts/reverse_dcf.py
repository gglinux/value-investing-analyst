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


def dcf_value(base_oe, growth, discount, terminal_g, years, fade=False, split=False,
              _solver_mode=False, terminal_g_cap=0.05, min_spread=0.02):
    """两阶段 DCF：预测期 + Gordon 永续。fade=True 时增速线性衰减至 terminal_g。

    split=True 时返回 (总值, 预测期PV, 终值PV, 诊断dict)——用于终值占比诊断：
    终值占比越高，估值越依赖"第 N+1 年以后"这个看不见的假设，安全边际的
    有效分辨率越低。占比 >75% 时，"安全边际 25%" 这个数字本身就是虚假精确。

    ★ P0 静默错误护栏（v2.11）★
    以下四类情形此前均"算得出数、不报错、无痕迹"，属最危险的一类缺陷——
    输出一个看起来很精确的错数。现全部改为显式拒绝或结构化告警：

    1. 永续增速上限（terminal_g_cap，默认 5%）：永续增速高于长期名义 GDP
       等于假设公司永远快于整体经济增长，数学上不可持续。本纪律文档早已写明
       （"永续增长率 ≤ 长期名义 GDP"），但此前只在文档里、代码完全不校验。
    2. 折现率与永续增速的安全间距（min_spread，默认 2pct）：Gordon 分母为
       (r-g)，间距趋零时价值爆炸 —— r=10%/g=9.99% 得出 6915× OE 的荒谬估值
       且不报错。只挡 g>=r 远远不够，多打一个 9 就能污染整份报告。
    3. fade 静默失效与语义反转：years<=1 时线性插值无法定义，此前被
       `years > 1` 条件静默跳过（传了 fade 却按恒定增速算）；growth <
       terminal_g 时线性插值让增速逐年"爬升"，fade 从保守化变成乐观化。
    4. 负基期拒绝：亏损公司（base_oe <= 0）套 DCF 静默产出负内在价值，而负
       价值在 DCF 语境下无意义。方法树要求这类公司走"反向 DCF + 单位经济
       外推"，故直接拒绝、迫使换方法而非给一个错数。

    _solver_mode: 仅供 solve_implied_growth 内部使用。二分法需在 -50%~+60%
      区间自由试探 growth，其中包含 growth < terminal_g 的区段，那属数值搜索
      过程而非用户假设，故该模式下豁免 fade 语义检查；其余护栏仍然生效，
      因为它们校验的是调用者传入的固定假设。
    """
    if base_oe is None or base_oe <= 0:
        raise SystemExit(
            f"错误：基期 Owner Earnings 必须为正，收到 {base_oe}。"
            "亏损公司套 DCF 会产出无意义的负内在价值 —— 按估值方法树，"
            "『高增长未盈利』类应走『反向 DCF 为主 + 单位经济外推』："
            "先用单位经济学论证盈利路径，再对成熟期利润做 DCF。")
    if terminal_g > terminal_g_cap:
        raise SystemExit(
            f"错误：永续增速 {terminal_g:.2%} 超过上限 {terminal_g_cap:.2%}。"
            "永续增速高于长期名义 GDP，等于假设公司永远快于整体经济增长，"
            "数学上不可持续（终值会吞掉全部估值）。若确有理由"
            "（如更高的长期通胀假设），显式传 terminal_g_cap 并在报告中论证。")
    if discount <= terminal_g:
        raise SystemExit("错误：折现率必须大于永续增长率")
    if discount - terminal_g < min_spread:
        raise SystemExit(
            f"错误：折现率 {discount:.2%} 与永续增速 {terminal_g:.2%} 间距仅 "
            f"{(discount - terminal_g):.2%}，小于安全间距 {min_spread:.2%}。"
            "Gordon 永续公式分母为 (r-g)，间距趋零时价值爆炸："
            "r=10%/g=9.99% 会得出 6915× OE 的荒谬估值。"
            "这是『精确的错误』最典型的温床 —— 请调整假设，或显式传 min_spread。")
    if fade and years <= 1:
        raise SystemExit(
            f"错误：--fade 需要 years >= 2 才能定义线性衰减路径，当前 years={years}。"
            "此前该情形被静默忽略（传了 fade 却按恒定增速计算），属静默失效。"
            "请去掉 --fade，或增加预测期年数。")
    if fade and not _solver_mode and growth < terminal_g:
        raise SystemExit(
            f"错误：--fade 模式下期初增速 {growth:.2%} 低于永续增速 {terminal_g:.2%}，"
            "线性插值会让增速逐年『爬升』到永续 —— fade 本意是保守化（衰减），"
            "此处语义反转为乐观化。衰退型公司请改用恒定增速（去掉 --fade），"
            "或把永续增速下调到期初增速之下。")

    pv = 0.0
    oe = base_oe
    for t in range(1, years + 1):
        g_t = growth + (terminal_g - growth) * (t - 1) / (years - 1) if (fade and years > 1) else growth
        oe = oe * (1 + g_t)
        pv += oe / ((1 + discount) ** t)
    terminal = oe * (1 + terminal_g) / (discount - terminal_g)
    term_pv = terminal / ((1 + discount) ** years)
    total = pv + term_pv
    if split:
        # 诊断随返回值给出，使 verify_report 等下游可机器校验
        # "终值占比 >=75% 却以安全边际单独支撑买入" 这类违规。
        # 此前占比判定只在 CLI print，程序化调用完全拿不到。
        ratio = term_pv / total if total else 0.0
        if ratio >= 0.75:
            level = "critical"
            action = ("估值主体来自预测期之后的永续假设，本质是信仰不是估值；"
                      "必须改用倍数法/资产法交叉验证，"
                      "且禁止以『安全边际达标』单独支撑买入结论")
        elif ratio >= 0.60:
            level = "warning"
            action = "显著依赖永续假设：建议加 fade 重跑，并在报告披露该占比"
        else:
            level = "ok"
            action = "估值主体由可见的预测期现金流支撑"
        diag = {
            "terminal_value_ratio": ratio,
            "forecast_pv": pv,
            "terminal_pv": term_pv,
            "level": level,
            "action_required": action,
            "blocks_margin_of_safety_only_buy": ratio >= 0.75,
            "terminal_growth": terminal_g,
            "discount_rate": discount,
            "spread": discount - terminal_g,
        }
        return total, pv, term_pv, diag
    return total


def solve_implied_growth(market_cap, base_oe, discount, terminal_g, years, fade=False,
                         terminal_g_cap=0.05, min_spread=0.02):
    """二分法反解隐含增速 g ∈ (-50%, +60%)

    返回 (g, status)：status 为 'ok' | 'negative_operating_value' | 'out_of_range'
    —— 三者性质完全不同，不可混为一句"无解"（v2.10 修正）：
      negative_operating_value：剔除净现金/投资组合后市值为负，即市场给经营业务
        负估值。这是价值投资里最强的信号之一（净现金 > 市值），必须显著提示并
        以非零退出码中断，绝不能降级成"超出区间"后 exit 0 静默通过。
      out_of_range：现价确实无法用 -50%~+60% 增速解释，属假设区间问题。

    求解过程会试探 growth < terminal_g 的区段（属数值搜索而非用户假设），
    故内部调用带 _solver_mode=True 豁免 fade 语义检查（v2.11）。
    """
    if market_cap <= 0:
        return None, "negative_operating_value"

    def _f(g):
        return dcf_value(base_oe, g, discount, terminal_g, years, fade,
                         _solver_mode=True, terminal_g_cap=terminal_g_cap,
                         min_spread=min_spread) - market_cap

    lo, hi = -0.5, 0.6
    f_lo = _f(lo)
    f_hi = _f(hi)
    if f_lo * f_hi > 0:
        return None, "out_of_range"  # 现价超出该假设区间能解释的范围
    for _ in range(100):
        mid = (lo + hi) / 2
        f_mid = _f(mid)
        if abs(f_mid) < 1e-6 * market_cap:
            return mid, "ok"
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2, "ok"


# ---- 闸门二的护城河档位映射（v2.15）----
# 门槛不再手写常数，而是**从闸门一的安全边际要求反推**，保证两道闸门口径自洽：
#   期望 IRR 门槛 = (1+r) × (1/(1−安全边际要求))^(1/H) − 1
# r=10%、H=5 时：宽护城河 25% → 16.5%；窄护城河 40% → 21.8%。
# 这个门槛的作用是**自洽性校验**而不是独立证据 —— 见 gate2 的 basis 字段说明。
MOAT_MOS_REQUIREMENT = {"wide": 0.25, "narrow": 0.40, "none": None}
# 不收敛下限的默认门槛：长期国债 + 2~3pct。含义是"即使折价永不收敛，
# 也要跑赢低风险替代"。取 6% 是 A 股/美股十年国债（约 2~4.5%）加溢价后的中枢，
# 可按市场用 --floor-hurdle 显式调整并在报告论证。
DEFAULT_FLOOR_HURDLE = 0.06


def moat_irr_hurdle(moat, discount_rate, hold_years):
    """由闸门一的安全边际要求反推期望 IRR 门槛，保证两闸门自洽。

    返回 (mos_requirement, irr_hurdle)；moat='none' 时返回 (None, None)——
    无护城河不给买入结论，闸门讨论无意义。
    """
    mos = MOAT_MOS_REQUIREMENT.get(moat)
    if mos is None:
        return None, None
    return mos, (1.0 + discount_rate) * (1.0 / (1.0 - mos)) ** (1.0 / hold_years) - 1.0


def expected_return(price, scenarios, hold_years, index_hurdle=0.09,
                    dividend_yield=0.0, discount_rate=0.10,
                    moat=None, iv_growth=None,
                    floor_hurdle=DEFAULT_FLOOR_HURDLE, pessimistic_hurdle=0.0):
    """期望回报率引擎：把三情景估值转成"这笔钱年化几个点"。

    价值投资的决策变量不是"公司好不好"，而是"相对机会成本，这笔钱划不划算"。
    单点内在价值只能回答贵不贵，无法回答期望回报与亏损概率。

    scenarios: [{"name","value_per_share","probability"}, ...]，概率之和须为 1。

    ★ 终值时点纪律（v2.9 修正，此前为结构性错误）★
    内在价值 V0 是**当期现值**。持有 H 年后，若基本面如期兑现，价值不会原地不动：
        V_H = V0×(1+r)^H − Σ[已派现金流 × 复利]
    因此「期末价值 + 期间现金流（再投资）」= V0×(1+r)^H，年化回报：
        IRR = (1+r)×(V0/P)^(1/H) − 1
    旧版用 IRR=(V0/P)^(1/H)−1，隐含假设「内在价值 H 年原地不动、期间现金流凭空消失」，
    对留存再投资的复利机器系统性低估约 (1+r)^H（5 年 10% ≈ 61% 的价值增长被丢弃）。
    实证：10 个归档案例中 4 个（招行/拼多多/腾讯/平安）闸门二结论因此被误判为不通过。

    ★ 股息不再叠加 ★
    修正式已隐含全部股东回报（分红 + 留存增值合计 = r）。再加 dividend_yield 属重复计算。
    dividend_yield 保留仅用于：① 披露分红占总回报的比例（现金落袋 vs 账面增值的质量差异）；
    ② 亏损判定的现金缓冲。不再进入 IRR 计算。

    ★ 非正内在价值与下行语义（v2.10）★
    悲观情景每股价值 ≤0 时，统一按「股权归零」口径：irr 与 total_return 均为 -100%
    （有限责任下股东亏损上限即本金）。同时输出 pessimistic_equity_wiped_out /
    has_non_positive_scenario 标志，使「股权正好归零」与「资产负债表已穿透」可区分
    —— 二者风险性质不同，是下行约束最该分辨的场景。

    ★ 两个回报字段口径不可混用（v2.10）★
    expected_annualized_irr（Σpᵢ·IRRᵢ，先年化再加权）是闸门二唯一判定口径；
    expected_total_return 是按期望价值算的总回报，直接年化会因 Jensen 凹性偏高
    0.5~1pct。JSON 已加 *_basis 标记与 jensen_gap_vs_annualized_total 供下游自检。

    discount_rate: 三情景估值所用折现率 r，必须与 forward-value 的 --discount-rate 一致，
      否则期望回报与内在价值不同源。r 同时是机会成本的下限——买在内在价值上（P=V0）
      时 IRR 恒等于 r，故 index_hurdle 设在 r 之下时闸门二形同虚设（见 --index-hurdle 校验）。

    ★ 闸门二换维度（v2.15，重要）★
    旧闸门二 = 期望年化 IRR vs 指数门槛。问题在于**期望 IRR 与安全边际同源**：
    两者共享同一个 V0、同一套假设、同一份模型误差，V0 高估 30% 两个闸门同时被污染。
    实测更糟：r=10%/H=5 时 13% 门槛等价于安全边际 12.6%，而闸门一要求 25%/40%
    （等价 IRR 16.5%/21.8%）—— 门槛被校准在闸门一之下，闸门二在 10 个归档案例里
    从未成为约束条件。

    改为三项，各自门槛独立设定，全过才算闸门二通过：
      ① consistency_expected_irr（自洽性校验，**不是独立证据**）
         期望 IRR vs 由护城河档位从闸门一反推的门槛（moat_irr_hurdle）。
         数学上它与闸门一在期望值口径下等价，故其唯一作用是暴露口径不自洽
         （例如安全边际达标而期望 IRR 不达标 ⇒ 情景离散度或概率有问题）。
      ② no_convergence_floor（**独立**）= 股息率 + 内在价值增速。
         回答"如果折价永不收敛，我实际赚什么"。它完全不含 V0/P 比值——
         不管你把内在价值算成多少，这个数只由分红和生意本身的增长决定。
         价值陷阱的杀伤力正在此：安全边际 60% 而不收敛下限只有 1%，
         意味着这笔投资的回报全部押在"市场哪天承认我对"。
      ③ pessimistic_irr（**独立**）来自独立方法推导的悲观情景（见 check_scenarios.py）。
         回答"错了会怎样"。只有当悲观值由清算/PB底/历史最差年等独立路径给出时
         这一项才真正独立——若悲观情景只是基准调低增速，它退化成 ① 的换算。
    """
    total_p = sum(s["probability"] for s in scenarios)
    if abs(total_p - 1.0) > 1e-6:
        raise SystemExit(f"错误：三情景概率之和为 {total_p:.4f}，必须等于 1")
    if price <= 0:
        raise SystemExit("错误：现价必须为正")
    if not isinstance(hold_years, int) or hold_years < 1:
        raise SystemExit(
            f"错误：持有期必须为 ≥1 的整数年，收到 {hold_years}。"
            "（hold_years=0 会导致年化开方除零；负值会静默产出无意义的 IRR）")
    if dividend_yield < 0 or dividend_yield > 0.20:
        raise SystemExit("错误：股息率应在 0~20% 之间（按小数传入，如 0.05）")
    if discount_rate <= 0 or discount_rate > 0.30:
        raise SystemExit("错误：折现率应在 0~30% 之间（按小数传入，如 0.10）")
    if discount_rate < 0.10:
        print(f"⚠ 警告：折现率 {discount_rate:.2%} 低于下限纪律 max(10%, 10Y国债+4pct)。"
              "低利率环境下限仍为 10%，请上调后重跑。", file=sys.stderr)

    growth_factor = (1.0 + discount_rate) ** hold_years
    rows = []
    exp_irr = 0.0
    exp_terminal = 0.0
    loss_prob = 0.0
    downside = 0.0
    has_negative_value = False
    for s in scenarios:
        v, p = s["value_per_share"], s["probability"]
        v_h = v * growth_factor          # 期末价值（含期间现金流再投资）
        total_ret = v_h / price - 1.0
        # ★ 非正内在价值的口径统一（v2.10 修正）★
        # 旧版：irr 走 v>0 分支返回 -1.0（本金全损），而 total_return 仍按
        # v_h/price-1 算出 <-100% 的数 —— 同一情景两个字段互相矛盾。
        # 现统一为「股权归零」口径：亏损上限就是本金 100%，二者一致。
        # 股权价值为负在有限责任下不等于股东要再掏钱，故 total_return 也封顶 -100%。
        if v <= 0:
            has_negative_value = True
            irr = -1.0
            total_ret = -1.0
            v_h = 0.0
        else:
            irr = (v_h / price) ** (1.0 / hold_years) - 1.0
        rows.append({
            "name": s["name"], "probability": p,
            "value_per_share": v,               # V0，当期内在价值
            "value_per_share_terminal": v_h,    # V_H，期末价值（含再投资）
            "value_is_non_positive": v <= 0,
            "total_return": total_ret, "annualized_irr": irr,
        })
        exp_irr += p * irr
        exp_terminal += p * v
        if total_ret < 0:
            loss_prob += p
            downside += p * total_ret

    exp_total = exp_terminal * growth_factor / price - 1.0
    # 下行指标：不可由安全边际单调推出，是闸门二真正独立的信息
    pess = min(scenarios, key=lambda s: s["value_per_share"])
    pess_v = pess["value_per_share"]
    # ★ 负价值 vs 归零必须可区分（v2.10）★
    # 旧版两种情形都吐 -1.0，而这恰是下行约束最该分辨的场景：
    # 「股权正好归零」与「资产负债表已穿透、需要注资/重整」风险性质不同。
    pess_irr = ((pess_v * growth_factor) / price) ** (1.0 / hold_years) - 1.0 \
        if pess_v > 0 else -1.0
    div_share = (dividend_yield / discount_rate) if discount_rate > 0 else None

    # ---- 闸门二三项（v2.15）----
    mos_req, irr_hurdle = moat_irr_hurdle(moat, discount_rate, hold_years) if moat else (None, None)
    floor = (dividend_yield + iv_growth) if iv_growth is not None else None
    gate2 = {
        "moat": moat,
        "consistency_expected_irr": {
            "value": exp_irr,
            "hurdle": irr_hurdle,
            "hurdle_derivation": (
                f"由闸门一安全边际要求 {mos_req:.0%} 反推：(1+r)×(1/(1−MoS))^(1/H)−1"
                if mos_req is not None else None),
            "pass": (exp_irr >= irr_hurdle) if irr_hurdle is not None else None,
            "basis": "自洽性校验，非独立证据——期望值口径下与闸门一互为单调函数，"
                     "共享同一个 V0 与同一份模型误差。不达标而安全边际达标 ⇒ "
                     "情景离散度或概率赋值有问题",
        },
        "no_convergence_floor": {
            "value": floor,
            "dividend_yield": dividend_yield,
            "intrinsic_value_growth": iv_growth,
            "hurdle": floor_hurdle,
            "pass": (floor >= floor_hurdle) if floor is not None else None,
            "basis": "独立信息——完全不含 V0/P 比值。回答『折价永不收敛时我实际赚什么』："
                     "= 股息率 + 内在价值增速。安全边际很大而此值很低 = 回报全押在"
                     "『市场哪天承认我对』，这是价值陷阱的定量特征",
        },
        "pessimistic_irr": {
            "value": pess_irr,
            "hurdle": pessimistic_hurdle,
            "pass": pess_irr >= pessimistic_hurdle,
            "basis": "独立信息——回答『错了会怎样』。前提是悲观情景由独立方法推导"
                     "（清算/PB底/历史最差年，见 check_scenarios.py S2）；"
                     "若悲观值只是基准调低增速，本项退化为自洽性校验的换算",
        },
    }
    _checks = [gate2[k]["pass"] for k in
               ("consistency_expected_irr", "no_convergence_floor", "pessimistic_irr")]
    gate2["independent_checks"] = ["no_convergence_floor", "pessimistic_irr"]
    gate2["evaluable"] = all(c is not None for c in _checks)
    gate2["pass"] = all(c is True for c in _checks) if gate2["evaluable"] else None
    gate2["missing_inputs"] = [k for k in
                               ("consistency_expected_irr", "no_convergence_floor")
                               if gate2[k]["pass"] is None]
    if moat == "none":
        gate2["pass"] = False
        gate2["note"] = "无护城河不给买入结论（valuation-guide 第四步），闸门二直接不过"

    return {
        "price": price, "hold_years": hold_years,
        "discount_rate": discount_rate,
        "dividend_yield": dividend_yield,
        "dividend_share_of_return": div_share,
        "scenarios": rows,
        "expected_value_per_share": exp_terminal,
        "expected_value_per_share_terminal": exp_terminal * growth_factor,
        "expected_total_return": exp_total,
        "expected_annualized_irr": exp_irr,
        # ★ 口径标记（v2.10）★ 两个回报字段口径不同，差值源于 Jensen 凹性：
        # expected_annualized_irr = Σpᵢ·IRRᵢ（先年化再加权）——闸门二唯一判定口径
        # expected_total_return   = 按期望价值算的总回报（先加权再年化会偏高）
        # 实测可差 0.5~1pct，在「差一点」的判定里足以翻转结论，故显式标注。
        "expected_annualized_irr_basis": "probability_weighted_of_per_scenario_irr",
        "expected_total_return_basis": "on_expected_value_do_not_annualize_for_gate2",
        "gate2_decision_field": "expected_annualized_irr",
        "jensen_gap_vs_annualized_total": (
            ((1.0 + exp_total) ** (1.0 / hold_years) - 1.0) - exp_irr
            if exp_total > -1.0 else None),
        # 兼容旧字段名：修正后含息与不含息同为一个数（股息已隐含）
        "expected_annualized_irr_incl_div": exp_irr,
        "pessimistic_irr": pess_irr,
        "pessimistic_value_per_share": pess_v,
        "pessimistic_equity_wiped_out": pess_v <= 0,
        "has_non_positive_scenario": has_negative_value,
        "loss_probability": loss_prob,
        "expected_downside_given_loss": downside / loss_prob if loss_prob > 0 else None,
        "probability_weighted_downside": downside,
        "index_hurdle": index_hurdle,
        "beats_index": exp_irr > index_hurdle,
        "excess_vs_index": exp_irr - index_hurdle,
        "hurdle_above_discount_rate": index_hurdle > discount_rate,
        "gate2": gate2,
        "gate2_decision_note": "闸门二判定看 gate2.pass（三项全过）。beats_index 保留仅作"
                               "机会成本对照展示，不再单独决定档位——它与安全边际同源",
        "note": "IRR=(1+r)×(V0/P)^(1/H)−1：内在价值随时间以折现率增值，"
                "已隐含分红+留存增值全部股东回报，股息不再叠加（叠加即重复计算）；"
                f"折现率 r={discount_rate:.1%} 是 IRR 的下限（P=V0 时 IRR=r），"
                "门槛须设在 r 之上才构成有效约束；下行指标（悲观 IRR/亏损概率/亏损跌幅）"
                "不可由安全边际单调推出，是闸门二独立信息来源",
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
    p1.add_argument("--terminal-growth-cap", type=float, default=0.05,
                    help="永续增速上限（默认 5%%，约当长期名义 GDP）。"
                         "超限即拒绝——永续快于经济增长在数学上不可持续")
    p1.add_argument("--min-spread", type=float, default=0.02,
                    help="折现率与永续增速的最小安全间距（默认 2pct）。"
                         "Gordon 分母 (r-g) 趋零时价值爆炸，须挡在源头")

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
    p2.add_argument("--terminal-growth-cap", type=float, default=0.05,
                    help="永续增速上限（默认 5%%，约当长期名义 GDP）。"
                         "超限即拒绝——永续快于经济增长在数学上不可持续。"
                         "如确有理由须显式放宽并在报告论证")
    p2.add_argument("--min-spread", type=float, default=0.02,
                    help="折现率与永续增速的最小安全间距（默认 2pct）。"
                         "r=10%%/g=9.99%% 会得出 6915× OE 的荒谬估值，故挡在源头")
    p2.add_argument("-o", "--output",
                    help="输出 JSON 路径（含估值结果与终值占比结构化诊断，"
                         "供报告校验器机器读取）")

    p3 = sub.add_parser("expected-return",
                        help="三情景转期望年化回报率（Phase 4.5 机会成本对照强制使用）")
    p3.add_argument("--price", type=float,
                    help="当前股价（market_snapshot 底稿）。用 --scenarios-file 时可省略")
    p3.add_argument("--hold-years", type=int, default=5, help="持有期，默认 5 年")
    p3.add_argument("--scenarios",
                    help='三情景每股价值与概率，格式："悲观:105.12:0.3,基准:211.65:0.5,乐观:396.52:0.2"。'
                         '与 --scenarios-file 二选一，优先用后者（单一事实源）')
    p3.add_argument("--index-hurdle", type=float, default=0.09,
                    help="机会成本门槛（指数长期年化），默认 9%%。"
                         "必须 > --discount-rate，否则闸门二形同虚设（买在内在价值上"
                         "IRR 恒等于折现率，门槛低于折现率则任何不溢价的标的自动过闸）")
    p3.add_argument("--discount-rate", type=float, default=0.10,
                    help="三情景估值所用折现率 r（须与 forward-value 的 --discount-rate 一致）。"
                         "内在价值按 (1+r)^H 增值，这是期望 IRR 的理论下限")
    p3.add_argument("--moat", choices=["wide", "narrow", "none"],
                    help="护城河档位。闸门二的期望 IRR 门槛由此从闸门一的安全边际要求"
                         "（宽 25% / 窄 40%）反推，保证两闸门自洽（r=10%/H=5 时为 "
                         "16.5% / 21.8%）。该项只是自洽性校验，独立信息在下面两项")
    p3.add_argument("--iv-growth", type=float,
                    help="基准情景下每股内在价值的长期增速（小数）。与股息率相加得"
                         "『价值不收敛下限』——折价永不收敛时的实际年化回报。"
                         "这是闸门二真正独立于 V0 的一项，强烈建议必填")
    p3.add_argument("--floor-hurdle", type=float, default=DEFAULT_FLOOR_HURDLE,
                    help=f"不收敛下限的门槛（默认 {DEFAULT_FLOOR_HURDLE:.0%}，"
                         f"约当长期国债 + 2~3pct）。含义：即使市场永不重估，"
                         f"也要跑赢低风险替代")
    p3.add_argument("--pessimistic-hurdle", type=float, default=0.0,
                    help="悲观情景年化门槛（默认 0%，即最坏情况不亏本金）。"
                         "前提是悲观值来自独立方法，见 check_scenarios.py")
    p3.add_argument("--scenarios-file",
                    help="已通过 check_scenarios.py 门禁的 data/scenarios.json。"
                         "提供时自动读取 price/情景/概率/折现率/护城河/股息率/"
                         "内在价值增速，避免命令行手抄导致口径漂移")
    p3.add_argument("--dividend-yield", type=float, default=0.0,
                    help="预期持有期平均股息率（小数，如 0.05）。**不再进入 IRR 计算**"
                         "（修正式已隐含分红+留存的全部股东回报，叠加即重复计算）；"
                         "仅用于披露分红占总回报比例——现金落袋 vs 账面增值的回报质量差异")
    p3.add_argument("-o", "--output", help="输出 JSON 路径")

    args = ap.parse_args()

    if args.mode == "expected-return":
        price, hold_years = args.price, args.hold_years
        dividend_yield, discount_rate = args.dividend_yield, args.discount_rate
        moat, iv_growth = args.moat, args.iv_growth
        if args.scenarios_file:
            # 单一事实源：口径全部取自已过门禁的 scenarios.json，禁止命令行手抄
            with open(args.scenarios_file, "r", encoding="utf-8") as f:
                sd = json.load(f)
            scen = [{"name": s["name"], "value_per_share": float(s["value_per_share"]),
                     "probability": float(s["probability"])} for s in sd["scenarios"]]
            price = float(sd.get("price", price))
            hold_years = int(sd.get("hold_years", hold_years))
            discount_rate = float(sd.get("discount_rate", discount_rate))
            dividend_yield = float(sd.get("dividend_yield", dividend_yield))
            moat = sd.get("moat", moat)
            if sd.get("intrinsic_value_growth") is not None:
                iv_growth = float(sd["intrinsic_value_growth"])
            print(f"口径取自 {args.scenarios_file}（已过 check_scenarios 门禁）\n")
        else:
            if args.price is None:
                raise SystemExit("错误：须提供 --price，或改用 --scenarios-file")
            if not args.scenarios:
                raise SystemExit("错误：须提供 --scenarios 或 --scenarios-file")
            scen = []
            for part in args.scenarios.split(","):
                bits = part.split(":")
                if len(bits) != 3:
                    raise SystemExit(f"情景格式错误：{part}，应为 名称:每股价值:概率")
                scen.append({"name": bits[0], "value_per_share": float(bits[1]),
                             "probability": float(bits[2])})
        res = expected_return(price, scen, hold_years, args.index_hurdle,
                              dividend_yield, discount_rate,
                              moat=moat, iv_growth=iv_growth,
                              floor_hurdle=args.floor_hurdle,
                              pessimistic_hurdle=args.pessimistic_hurdle)
        print(f"现价 {res['price']:,.2f}，持有期 {res['hold_years']} 年，"
              f"折现率 {res['discount_rate']:.1%}"
              f"（内在价值按此速率增值）\n")
        print(f"{'情景':<8}{'概率':>8}{'V0现值':>11}{'V_H期末':>11}{'总回报':>10}{'年化':>9}")
        for r_ in res["scenarios"]:
            print(f"{r_['name']:<8}{r_['probability']:>8.0%}{r_['value_per_share']:>11,.2f}"
                  f"{r_['value_per_share_terminal']:>11,.2f}"
                  f"{r_['total_return']:>10.1%}{r_['annualized_irr']:>9.1%}")
        print(f"\n期望每股价值(V0)    : {res['expected_value_per_share']:,.2f}")
        print(f"期望每股价值(V_H)   : {res['expected_value_per_share_terminal']:,.2f}")
        print(f"期望总回报          : {res['expected_total_return']:.1%}")
        print(f"期望年化 IRR        : {res['expected_annualized_irr']:.2%}"
              f"  ← 已含分红+留存增值，与门槛比较用这个")
        if res["dividend_yield"] > 0 and res["dividend_share_of_return"] is not None:
            print(f"  其中分红贡献占比  : {res['dividend_share_of_return']:.0%}"
                  f"（股息率 {res['dividend_yield']:.1%} ÷ 折现率 "
                  f"{res['discount_rate']:.1%}）现金落袋部分")
        print(f"\n--- 下行保护（闸门二独立信息，不可由安全边际推出）---")
        print(f"悲观情景年化 IRR    : {res['pessimistic_irr']:.2%}")
        if res.get("pessimistic_equity_wiped_out"):
            print(f"  🔴 悲观情景每股内在价值为 {res['pessimistic_value_per_share']:,.2f}（≤0）"
                  f"——股权归零口径，已按亏损 100% 计入。")
            print(f"     负值意味着资产负债表被穿透（可能需注资/重整），"
                  f"风险性质重于'正好归零'，禁止仅以安全边际支撑买入。")
        print(f"亏损概率            : {res['loss_probability']:.0%}")
        if res["expected_downside_given_loss"] is not None:
            print(f"亏损情景平均跌幅    : {res['expected_downside_given_loss']:.1%}")
        print(f"\n机会成本门槛（指数）: {res['index_hurdle']:.1%}")
        if not res["hurdle_above_discount_rate"] and not res["gate2"]["moat"]:
            print(f"⚠️  警告：门槛 {res['index_hurdle']:.1%} ≤ 折现率 "
                  f"{res['discount_rate']:.1%}，闸门二失效——买在内在价值上 IRR 即等于"
                  f"折现率，任何不溢价的标的都会自动过闸。请将门槛设在折现率之上"
                  f"（如 {res['discount_rate'] + 0.03:.0%}）或提高安全边际要求。")
        verdict = "跑赢" if res["beats_index"] else "跑输"
        print(f"（机会成本对照，仅展示）期望年化 {verdict}指数门槛 "
              f"{abs(res['excess_vs_index']):.2%}")

        # ---- 闸门二三项判定（v2.15：换维度，不再由 beats_index 定档）----
        g = res["gate2"]
        print(f"\n=== 闸门二（护城河档位：{g['moat'] or '未指定'}）===")
        rows = [
            ("① 期望 IRR（自洽性校验，非独立证据）", g["consistency_expected_irr"]),
            ("② 不收敛下限 = 股息率 + 内在价值增速（独立）", g["no_convergence_floor"]),
            ("③ 悲观情景年化（独立）", g["pessimistic_irr"]),
        ]
        for label, item in rows:
            v, h, ok = item["value"], item["hurdle"], item["pass"]
            v_txt = f"{v:.2%}" if v is not None else "未提供"
            h_txt = f"{h:.2%}" if h is not None else "未设定"
            mark = "✓" if ok is True else ("✗" if ok is False else "—")
            print(f"  {mark} {label}: {v_txt}  门槛 {h_txt}")
        if g["consistency_expected_irr"]["hurdle_derivation"]:
            print(f"     ①门槛来源：{g['consistency_expected_irr']['hurdle_derivation']}")
        if g["missing_inputs"]:
            print(f"  ⚠️  缺输入无法判定：{g['missing_inputs']}"
                  f"（--moat / --iv-growth 未提供时闸门二不可评）")
        if g.get("note"):
            print(f"  {g['note']}")
        if g["pass"] is True:
            print("  闸门二：通过（三项全过）")
        elif g["pass"] is False:
            print("  闸门二：不通过 → 档位最高「观察等价格」")
            fl = g["no_convergence_floor"]
            if fl["pass"] is False and fl["value"] is not None:
                print(f"     其中不收敛下限仅 {fl['value']:.2%}：安全边际再大，"
                      f"回报也全押在『市场哪天承认我对』——这是价值陷阱的定量特征")
        else:
            print("  闸门二：不可评（缺必要输入，不得当作通过）")
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
        g, status = solve_implied_growth(mc, args.base_oe, args.discount_rate,
                                        args.terminal_growth, args.years, args.fade,
                                        terminal_g_cap=args.terminal_growth_cap,
                                        min_spread=args.min_spread)
        if status == "negative_operating_value":
            print(f"\n🔴 重大信号：剔除非经营资产后，经营业务隐含市值为 {mc:,.0f}（≤0）。")
            print("   市场给经营业务的定价为负 —— 即净现金/投资组合价值已超过总市值。")
            print("   这不是'无解'，而是价值投资中最强的信号之一，必须在报告中单列讨论：")
            print("     ① 核实非经营资产可回收性（折价率、变现路径、少数股东权益、税负）；")
            print("     ② 核实经营业务是否在持续烧钱（负 Owner Earnings 会正当化负估值）；")
            print("     ③ 若资产为真且主业不烧钱，属深度低估，须交叉验证后重点跟踪。")
            print("   ⚠️  隐含增速在此情形下无定义，禁止填入报告的'市场隐含预期'栏位。")
            sys.exit(2)
        if status == "out_of_range":
            print("无解：在给定折现率/永续增速下，现价无法用 -50%~+60% 的增速解释。"
                  "说明市场定价隐含了其他假设（利润率跃迁、并购、或情绪定价），需在报告中明确讨论。")
            sys.exit(0)
        mode_note = "（增速线性衰减至永续）" if args.fade else "（增速恒定）"
        print(f"现价隐含的未来 {args.years} 年 Owner Earnings 年增速{mode_note}: {g:.2%}")
        print(f"假设：折现率 {args.discount_rate:.1%}，永续增速 {args.terminal_growth:.1%}")
        print("下一步：将该隐含增速与 Phase 3 基准情景增速对照，回答『市场预期苛刻还是宽松』。")
    else:
        v, fcst_pv, term_pv, diag = dcf_value(
            args.base_oe, args.growth, args.discount_rate,
            args.terminal_growth, args.years, args.fade, split=True,
            terminal_g_cap=args.terminal_growth_cap, min_spread=args.min_spread)
        print(f"经营业务价值（Owner Earnings 口径 DCF）: {v:,.0f}")
        # 终值占比诊断：估值可靠性的第一指标。
        # 判定口径统一取自引擎返回的 diag，CLI 不再自行重算阈值
        # （避免"引擎与展示层两套阈值"日后漂移）。
        ratio = diag["terminal_value_ratio"]
        print(f"\n--- 估值可靠性诊断（终值占比）---")
        print(f"预测期 {args.years} 年现值: {fcst_pv:,.0f}（{1 - ratio:.0%}）")
        print(f"永续终值现值      : {term_pv:,.0f}（{ratio:.0%}）")
        if diag["level"] == "critical":
            print(f"⚠️  终值占比 {ratio:.0%} ≥ 75%：估值主要来自第 {args.years + 1} 年以后的"
                  f"永续假设，本质是信仰不是估值。安全边际的有效分辨率低于假设误差——"
                  f"{diag['action_required']}。")
        elif diag["level"] == "warning":
            print(f"⚠️  终值占比 {ratio:.0%} ≥ 60%：{diag['action_required']}。")
        else:
            print(f"✓ 终值占比 {ratio:.0%} < 60%，{diag['action_required']}。")
        print(f"假设间距：折现率 {args.discount_rate:.2%} − 永续增速 "
              f"{args.terminal_growth:.2%} = {diag['spread']:.2%}")
        print(f"提示：永续增速 ±1pct 通常引起价值 ±15~20% 波动，"
              f"远超安全边际的分辨率——报告须给出永续增速敏感性。\n")
        total = v + args.add_back
        if args.add_back:
            print(f"非经营资产加回: {args.add_back:,.0f} → 股权价值合计: {total:,.0f}")
        ps = None
        if args.shares:
            ps = total / args.shares
            if args.fx != 1.0:
                print(f"每股价值: {ps:,.2f}（报告币） = {ps * args.fx:,.2f}（行情币，fx={args.fx}）")
            else:
                print(f"每股价值: {ps:,.2f}")
        if args.output:
            out = {
                "operating_value": v,
                "add_back": args.add_back,
                "equity_value": total,
                "shares": args.shares,
                "value_per_share": ps,
                "fx": args.fx,
                "value_per_share_quote_ccy": (ps * args.fx) if ps is not None else None,
                "assumptions": {
                    "base_oe": args.base_oe, "growth": args.growth,
                    "discount_rate": args.discount_rate,
                    "terminal_growth": args.terminal_growth,
                    "years": args.years, "fade": args.fade,
                },
                "terminal_diagnostics": diag,
            }
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            print(f"已写入 {args.output}")


if __name__ == "__main__":
    main()
