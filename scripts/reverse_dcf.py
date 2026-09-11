#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reverse_dcf.py — 反向 DCF 求解器（Phase 4 强制使用）

目的：用当前市值反推市场隐含预期，替代模型手算，保证解法一致、可复现。
四种模式：
  1. implied-growth：给定利润率/折现率等假设，反解现价隐含的收入增速 g
  2. forward-value：给定三情景假设，正向计算每股价值（供三情景 DCF 复用同一引擎）
  3. expected-return：三情景每股价值 + 概率 → 期望年化回报率/亏损概率（Phase 4.5 强制）
  4. growth：成熟期稳态利润 × 到达概率折回（REQ-P1-01 成长股/再投入型通道，
     当期 OE 被增长性资本开支压低时替代 OE 基期；反解"现价隐含到达概率"）

用法：
  python3 reverse_dcf.py implied-growth --market-cap 50000 --base-oe 2000 \
      --discount-rate 0.10 --terminal-growth 0.025 --years 10
  python3 reverse_dcf.py forward-value --base-oe 2000 --growth 0.12 \
      --discount-rate 0.10 --terminal-growth 0.025 --years 10 --shares 1000 \
      [--fade]   # 增速在预测期内线性衰减到永续增速（更保守、更真实）
  python3 reverse_dcf.py expected-return --price 209.75 --hold-years 5 \
      --scenarios "悲观:105:0.3,基准:212:0.5,乐观:397:0.2" --index-hurdle 0.09 \
      [--dividend-yield 0.05]   # 高股息标的必填，与门槛比较用含息 IRR
  python3 reverse_dcf.py growth --market-cap 53128 --current-revenue 8832 \
      --mature-revenue 36000 --mature-oe-margin 0.20 --terminal-multiple 20 \
      --arrival-prob 0.25 --years-to-maturity 10 --shares 436.456 \
      --contribution-margin 0.44 --failure-equity-value 646 \
      --mature-state-basis "渗透率×ARPU×利润率反推 [E:...]" \
      --arrival-prob-basis "基率锚+单元经济证据 [E:...]"   # REQ-P1-01 成长股通道

单位：market-cap / base-oe / mature-oe 用同一货币单位（建议百万）；shares 百万股。
base-oe = 基期 Owner Earnings（来自 compute_metrics.py 输出，保持口径一致）。
**周期高位公司必须用 compute_metrics 输出的 normalization.base_oe_recommended 作基期**，
禁止直接用当期 Owner Earnings（周期顶部利润外推是价值投资最经典的翻车方式）。
"""
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alert_codes import unknown_codes  # 告警码注册表：唯一事实源


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
# 亏损概率门槛：valuation-guide 第四步半「核心买入档追加下行约束：亏损概率 ≤ 30%」。
# 此前只在文档、未落码（REQ-P0-04 审查发现）。
DEFAULT_LOSS_PROB_HURDLE = 0.30
# 以下四项此前只藏在 argparse default 里，prepare_case.snapshot_rules 用 getattr
# 取不到、快照里记成 null（REQ-P0-08 审查发现）。提为模块常量、argparse 引用之，
# 保证「快照记录的」与「引擎实际用的」是同一个数。
DEFAULT_DISCOUNT_RATE = 0.10        # 折现率 r（forward-value / expected-return 共用）
DEFAULT_PESSIMISTIC_HURDLE = 0.0    # 悲观情景年化门槛：最坏情况不亏本金
DEFAULT_INDEX_HURDLE = 0.09         # 机会成本门槛（指数长期年化）
DEFAULT_HOLD_YEARS = 5              # 持有期
DEFAULT_TERMINAL_GROWTH = 0.025     # 永续增速
DEFAULT_TERMINAL_GROWTH_CAP = 0.05  # 永续增速上限
DEFAULT_MIN_SPREAD = 0.02           # r 与 g 最小间距


def moat_irr_hurdle(moat, discount_rate, hold_years):
    """由闸门一的安全边际要求反推期望 IRR 门槛，保证两闸门自洽。

    返回 (mos_requirement, irr_hurdle)；moat='none' 时返回 (None, None)——
    无护城河不给买入结论，闸门讨论无意义。
    """
    mos = MOAT_MOS_REQUIREMENT.get(moat)
    if mos is None:
        return None, None
    return mos, (1.0 + discount_rate) * (1.0 / (1.0 - mos)) ** (1.0 / hold_years) - 1.0


# ── REQ-P1-01 成长股 / 再投入型估值通道（2026-09-11）────────────────────
#
# 动因（B2-09 Netflix 案，全回测最深假阴性 2 档）：估值以当期 Owner Earnings 为
# 基期做反向 DCF，对高再投入公司（Netflix 2016、亚马逊 2010 类）当期 OE 被
# 增长性资本开支压低甚至为负——131x OE 的静态倍数下任何买入档位在数学上不可达，
# 框架对这类公司"无语言可说"。用 OE 框架估成长股不是保守，是用错了尺子：它把
# "为未来投入"和"赚不到钱"混为一谈。
#
# 通道数学：以**成熟期稳态利润 × 到达概率折回**替代当期 OE 作估值基期——
#   V = p_arrival × TV(成熟期 OE) / (1+r)^N + (1−p_arrival) × 失败残值
#   其中 TV = 成熟期 OE × 终局倍数（市场口径）或 × (1+g)/(r−g)（Gordon 保守口径）。
#
# 与旧通道的正交性（这是"通道建设而非阈值放松"的关键）：
#   1. 单元经济门（硬拒绝）：规模化边际贡献率 ≤0 或 LTV/CAC <1 的公司，增长在
#      单位层面毁灭价值——那不是再投入而是烧钱，本通道直接拒绝为其服务
#      （exit 2 + GROWTH_UNIT_ECONOMICS_UNPROVEN）。这是区分 Netflix 与乐视型
#      成长陷阱的第一道门：两类公司在旧框架里得到同样的"观察/拒绝"，在新通道
#      里得到相反结论。
#   2. 到达概率必须挂基率锚：p_arrival 是"增长兑现 + 利润率扩张 + 竞争存活"的
#      联合概率，不应超过同等规模公司达成所需收入 CAGR 的历史比例（基率表）。
#      现值锚来自 valuation-guide 基率检验表（Mauboussin）；REQ-P1-05 的
#      references/base-rates.md 建成后由 --base-rates-file 接管（挂钩接口）。
#   3. 终值结构性主导（本通道价值 100% 来自成熟期终值折回）：按既有终值纪律，
#      禁止以"安全边际达标"单独支撑核心买入——通道档位上限锁死"小仓位试探"。
#   4. 反向求解：implied-growth 反解的是"现价隐含增速"，本通道反解的是
#      "现价隐含到达概率"——implied p ≥100% 意味着连必然到达都解释不了现价。

# 收入基率锚（事实源：valuation-guide.md 第二步基率检验表，Mauboussin《The Base
# Rate Book》美股 1950-2015 全样本；量级适用于各市场）。
# 结构：(收入规模下限[十亿美元], {所需 10 年 CAGR 阈值: 历史达成比例})
# <100 亿美元档的 ≥10% 行原表未列，为插值估计（interpolated=True 标注）。
# REQ-P1-05 建成 references/base-rates.md 后，本表由 --base-rates-file 覆盖，
# 避免两处事实源漂移（当前以引擎表为临时事实源，文档表与之一致）。
REVENUE_CAGR_BASE_RATES = [
    (50, {0.20: 0.01, 0.10: 0.10}),
    (10, {0.20: 0.03, 0.10: 0.15}),
    (0, {0.20: 0.10, 0.10: 0.25}),   # ≥10% 一行为插值估计
]
GROWTH_VERDICT_CAP = "小仓位试探"   # 终值结构性主导 ⇒ 通道档位上限


def revenue_growth_base_rate(current_revenue, required_cagr, table=None):
    """收入基率锚：历史上同等规模公司 10 年 CAGR 达到 required 的比例**上界**。

    返回 (anchor, meta)：anchor=None 表示基率表对该增速无上界约束
    （所需增速低于表内最低档 10%，或成熟态不高于当期规模）。
    锚是上界的理由：P(CAGR ≥ required) ≤ P(CAGR ≥ t) 对一切 t ≤ required 成立，
    故取表内 ≤ required 的最大档位；required ≥ 20% 时退用 ≥20% 档（同为上界）。
    注意规模分档按表格标定币种（美元）——current_revenue 与成熟态同币种时，
    所需 CAGR 比值是币种无关的，但规模分档跨币种时须先换算（见 --base-rate-revenue-usd）。
    """
    table = table or REVENUE_CAGR_BASE_RATES
    meta = {"required_cagr": required_cagr, "interpolated": False}
    if required_cagr is None or required_cagr <= 0:
        meta["note"] = "成熟态不高于当期规模，无增长基率约束"
        return None, meta
    b = current_revenue / 1000.0  # 百万 → 十亿（表格口径）
    for floor, rows in table:
        if b >= floor:
            meta["scale_band"] = ("≥%d0亿美元" % floor) if floor else "<100亿美元"
            break
    thresholds = sorted(rows.keys(), reverse=True)  # [0.20, 0.10]
    below = [t for t in thresholds if t <= required_cagr]
    if not below:
        # required < 最低档（10%）：P(≥required) ≥ P(≥10%)，表内任何行都不构成上界
        meta["note"] = "所需增速 %.1f%% 低于表内最低档 10%%，基率表无上界约束" % (required_cagr * 100)
        return None, meta
    t = max(below)
    anchor = rows[t]
    meta["table_threshold"] = t
    if floor == 0 and t == 0.10:
        meta["interpolated"] = True
        meta["note"] = "<100亿美元档的 ≥10%% 行为插值估计（原表未列）"
    return anchor, meta


def growth_channel_value(mature_oe, arrival_prob, years_to_maturity, discount_rate,
                         terminal_growth=DEFAULT_TERMINAL_GROWTH,
                         terminal_multiple=None,
                         failure_equity_value=0.0,
                         terminal_g_cap=DEFAULT_TERMINAL_GROWTH_CAP,
                         min_spread=DEFAULT_MIN_SPREAD):
    """成熟期稳态利润 × 到达概率折回（REQ-P1-01）。

    返回 dict（含成功分支终值、失败分支、概率加权价值与终值占比披露）。
    护栏与 forward-value 同源：永续上限 / r-g 间距 / 正基期，一处不放。
    """
    if mature_oe is None or mature_oe <= 0:
        raise SystemExit(
            f"错误：成熟期稳态 Owner Earnings 必须为正，收到 {mature_oe}。"
            "成熟态本身不盈利说明『到达后也不值钱』——先回 growth-framework.md "
            "第三段（成熟期利润率反推）重做单位经济外推，再进本通道。")
    if not (0.0 < arrival_prob <= 1.0):
        raise SystemExit(
            f"错误：到达概率应在 (0, 1]（小数），收到 {arrival_prob}。"
            "若填的是 40 这类百分数，请改写 0.40。p=1 意为『必然到达』，"
            "须有极强证据并接受红队质询。")
    if not isinstance(years_to_maturity, int) or years_to_maturity < 1:
        raise SystemExit(f"错误：到达年数须为 ≥1 的整数，收到 {years_to_maturity}。")
    if failure_equity_value < 0:
        raise SystemExit(f"错误：失败残值不得为负（有限责任下股权残值下限为 0），"
                         f"收到 {failure_equity_value}。")
    if terminal_growth > terminal_g_cap:
        raise SystemExit(
            f"错误：永续增速 {terminal_growth:.2%} 超过上限 {terminal_g_cap:.2%}。"
            "成熟期之后仍快于整体经济增长在数学上不可持续（与 forward-value 同一护栏）。")
    if discount_rate <= terminal_growth:
        raise SystemExit("错误：折现率必须大于永续增长率")
    if discount_rate - terminal_growth < min_spread:
        raise SystemExit(
            f"错误：折现率 {discount_rate:.2%} 与永续增速 {terminal_growth:.2%} 间距不足"
            f" {min_spread:.2%}——Gordon 分母趋零时终值爆炸，与 forward-value 同一护栏。")
    gordon_tv = mature_oe * (1.0 + terminal_growth) / (discount_rate - terminal_growth)
    if terminal_multiple is not None:
        if terminal_multiple <= 0:
            raise SystemExit(f"错误：终局倍数必须为正，收到 {terminal_multiple}。")
        tv, tv_method = mature_oe * terminal_multiple, "multiple"
    else:
        tv, tv_method = gordon_tv, "gordon"
    pv = tv / ((1.0 + discount_rate) ** years_to_maturity)
    v = arrival_prob * pv + (1.0 - arrival_prob) * failure_equity_value
    return {
        "mature_oe": mature_oe,
        "terminal_value_mature": tv,
        "terminal_value_method": tv_method,
        "terminal_multiple": terminal_multiple,
        "gordon_cross_check_tv": gordon_tv,
        "gordon_implied_multiple": gordon_tv / mature_oe,
        "pv_today": pv,
        "failure_equity_value": failure_equity_value,
        "arrival_prob": arrival_prob,
        "years_to_maturity": years_to_maturity,
        "probability_weighted_value": v,
        # 终值占比披露：本通道价值按构造 100% 来自成熟期终值折回，
        # 这是档位上限锁死"小仓位试探"的原因（valuation-guide 终值纪律）。
        "terminal_value_ratio": 1.0,
        "terminal_value_ratio_note": "结构性终值主导（按构造为 1.0）：估值主体是"
                                     "『到达后的成熟态』这个尚未发生的状态，"
                                     "禁止以安全边际单独支撑核心买入",
    }


def expected_return(price, scenarios, hold_years, index_hurdle=0.09,
                    dividend_yield=0.0, discount_rate=0.10,
                    moat=None, iv_growth=None,
                    floor_hurdle=DEFAULT_FLOOR_HURDLE, pessimistic_hurdle=0.0,
                    loss_prob_hurdle=DEFAULT_LOSS_PROB_HURDLE):
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

    # ── REQ-P0-04 双闸门第二维度换源（2026-09-11，审查后二版）──────────
    # 旧逻辑：三项全过才过。问题在于①与闸门一共用 V0（代码注释已承认），
    # 两个同源闸门把名义门槛 25% 的有效门槛抬到 ~40%。这不是"系统偏保守"，
    # 是公式结构缺陷——额外 15pct 安全边际不能更好拦截坏公司（假阳性公司同样
    # 通过或不通过两个同源闸门），只让系统在该出手时不出手。
    #
    # 新逻辑：闸门二 = 以下检验全过：
    #   ①' expected_irr_floor    期望 IRR ≥ 折现率 r（**最低机会成本**，不是护城河反推门槛）。
    #       审查发现一版把①整体降级后，闸门二对期望值不设任何下限——期望 IRR 低于 r
    #       的标的只要股息+增速 ≥6% 且悲观 IRR ≥0 就能过，而"期望值不得低于机会成本"
    #       只剩 Phase 5 散文。r 是 IRR 的数学下限（P=V0 时 IRR=r），要求 ≥r 等价于
    #       "概率加权后至少不比买在公允价值差"，不再与闸门一的 25%/40% 重复计价。
    #   ②  no_convergence_floor   股息率 + 内在价值增速 ≥ 6%（独立于 V0）
    #   ③  pessimistic_irr        悲观情景年化 ≥ 0（独立，前提悲观值独立推导）
    #   ④  loss_probability       亏损概率 ≤ 30%（valuation-guide 第四步半已声明为
    #       核心买入下行约束，此前只在文档、未落码；它与③同属"错了会怎样"，
    #       但③看最坏情景的深度，④看亏损情景的概率质量，二者不可互推）
    #   ①  consistency_expected_irr 护城河反推门槛（16.5%/21.8%）**降为诊断披露**：
    #       仍计算、仍输出 GATE2_1_IRR_FAIL 供人工审查，不参与 pass 判定。
    #
    # 回退条件：REQ-P0-01 第四批假阳性基线若出现新增 FP，本改动回滚。
    # 检验方法：重跑 12 案 --rerun --baseline；正向错过数应下降，假阳性不新增。
    gate2["expected_irr_floor"] = {
        "value": exp_irr,
        "hurdle": discount_rate,
        "pass": exp_irr >= discount_rate,
        "basis": "最低机会成本——期望 IRR 不得低于折现率 r（P=V0 时 IRR=r）。"
                 "与闸门一不重复计价：闸门一要求折价 25%/40%，本项只要求"
                 "概率加权后不比买在公允价值差",
    }
    gate2["loss_probability"] = {
        "value": loss_prob,
        "hurdle": loss_prob_hurdle,
        "pass": loss_prob <= loss_prob_hurdle,
        "basis": "独立信息——亏损情景的概率质量。③看最坏情景多深，本项看"
                 "多大概率落入亏损，二者不可互推（valuation-guide 第四步半核心买入下行约束）",
    }
    gate2["independent_checks"] = ["no_convergence_floor", "pessimistic_irr", "loss_probability"]
    gate2["participating_checks"] = ["expected_irr_floor"] + gate2["independent_checks"]
    gate2["diagnostic_only"] = ["consistency_expected_irr"]
    _participating = [gate2[k]["pass"] for k in gate2["participating_checks"]]
    # evaluable 按参与判定的检验算——一版仍按三项旧口径算，①为 None 时会
    # 同时输出 GATE2_PASS 与 GATE2_UNRATED，自相矛盾。
    gate2["evaluable"] = all(c is not None for c in _participating)
    gate2["pass"] = all(c is True for c in _participating) if gate2["evaluable"] else None
    gate2["missing_inputs"] = [k for k in gate2["participating_checks"]
                               if gate2[k]["pass"] is None]
    gate2["consistency_check_diagnostic"] = (
        "①护城河反推门槛未达（诊断项，不阻塞闸门二，须在报告中显著披露——"
        "期望 IRR 低于 16.5%/21.8% 通常意味着情景离散度大或概率赋值偏悲观，值得人工复核）"
        if gate2["consistency_expected_irr"]["pass"] is False else
        "①护城河反推门槛达标（与参与判定的检验一致）"
        if gate2["consistency_expected_irr"]["pass"] is True else
        "①护城河反推门槛不可评（缺 --moat）")
    if moat == "none":
        gate2["pass"] = False
        gate2["note"] = "无护城河不给买入结论（valuation-guide 第四步），闸门二直接不过"

    # 闸门结果的稳定告警码（供回放断言机器判定，见 scripts/alert_codes.py）。
    # 注意 evaluable=False 时输出 GATE2_UNRATED 而非 GATE2_FAIL——「不可评」
    # 与「不过」在纪律上是两件事，前者禁止被当作通过，也不等于已判不过。
    codes = []
    if gate2["pass"] is True:
        codes.append("GATE2_PASS")
    elif gate2["pass"] is False:
        codes.append("GATE2_FAIL")
    if not gate2["evaluable"]:
        codes.append("GATE2_UNRATED")
    if gate2["consistency_expected_irr"]["pass"] is False:
        codes.append("GATE2_1_IRR_FAIL")
    if gate2["expected_irr_floor"]["pass"] is False:
        codes.append("GATE2_1B_IRR_BELOW_R")
    if gate2["no_convergence_floor"]["pass"] is False:
        codes.append("GATE2_2_FLOOR_FAIL")
    if gate2["pessimistic_irr"]["pass"] is False:
        codes.append("GATE2_3_BEAR_FAIL")
    if gate2["loss_probability"]["pass"] is False:
        codes.append("GATE2_4_LOSS_PROB_FAIL")
    # ---- 名义门槛 vs 有效门槛（纯诊断披露，不改任何 pass/fail 判定）----
    # 一版文案称「使①刚好通过所需的折价率即有效门槛」。REQ-P0-04 后①不参与判定，
    # 这个数不再约束任何东西——保留是因为它量化了**情景离散度的代价**：
    # 茅台基准 IRR 11.46% → 加权 5.82%，悲观拖累 5.64pct。读者需要知道
    # 「若沿用旧三项全过口径，实际要求折价 40.8% 而非名义 25%」这一历史事实，
    # 以理解为什么旧口径下神华/茅台被错过。它是回测解释工具，不是当前门槛。
    base_irr = next((s["annualized_irr"] for s in rows
                     if s["name"] in ("基准", "base")), None)
    if base_irr is not None and irr_hurdle is not None and mos_req is not None:
        drag = base_irr - exp_irr
        need_base = irr_hurdle + drag
        vp = ((1 + need_base) / (1 + discount_rate)) ** hold_years
        eff_mos = 1 - 1 / vp if vp > 0 else None
        gate2["effective_hurdle"] = {
            "nominal_margin_of_safety_required": mos_req,
            "base_scenario_irr": base_irr,
            "expected_irr_weighted": exp_irr,
            "pessimistic_drag_pct": drag,
            "base_irr_needed_to_clear": need_base,
            "effective_margin_of_safety_required": eff_mos,
            "gap_vs_nominal_pct": (eff_mos - mos_req) if eff_mos is not None else None,
            "status": "diagnostic_only",
            "basis": "【诊断口径，不构成当前门槛】若沿用旧「三项全过」口径，使①护城河反推门槛"
                     "刚好通过所需的折价率。REQ-P0-04 后①不参与判定，本数只用于量化"
                     "情景离散度的代价并解释旧口径下的假阴性（茅台/神华）。"
                     "当前闸门二对期望值的唯一要求是 ≥ 折现率 r（expected_irr_floor）",
        }
        if eff_mos is not None and eff_mos - mos_req > 0.05:
            codes.append("GATE_EFFECTIVE_HURDLE_GAP")

    unknown = unknown_codes(codes)
    if unknown:
        raise KeyError(f"未注册的告警码 {unknown}，请先在 scripts/alert_codes.py 登记")
    gate2["codes"] = codes

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
        "gate2_decision_note": "闸门二判定看 gate2.pass（participating_checks 全过：期望 IRR ≥ r、"
                               "不收敛下限 ≥ 6%、悲观 IRR ≥ 0、亏损概率 ≤ 30%；护城河反推门槛为诊断项）。"
                               "beats_index 保留仅作机会成本对照展示，不再单独决定档位——它与安全边际同源",
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
    p1.add_argument("--discount-rate", type=float, default=DEFAULT_DISCOUNT_RATE)
    p1.add_argument("--terminal-growth", type=float, default=DEFAULT_TERMINAL_GROWTH)
    p1.add_argument("--years", type=int, default=10)
    p1.add_argument("--fade", action="store_true", help="增速线性衰减到永续增速")
    p1.add_argument("--deduct", type=float, default=0.0,
                    help="从市值中剔除的非经营资产（净现金/投资组合折价可回收值，"
                         "与 market-cap 同币种同单位）——反解的是经营业务隐含增速")
    p1.add_argument("--terminal-growth-cap", type=float, default=DEFAULT_TERMINAL_GROWTH_CAP,
                    help="永续增速上限（默认 5%%，约当长期名义 GDP）。"
                         "超限即拒绝——永续快于经济增长在数学上不可持续")
    p1.add_argument("--min-spread", type=float, default=DEFAULT_MIN_SPREAD,
                    help="折现率与永续增速的最小安全间距（默认 2pct）。"
                         "Gordon 分母 (r-g) 趋零时价值爆炸，须挡在源头")

    p2 = sub.add_parser("forward-value", help="给定假设正向估值")
    p2.add_argument("--base-oe", type=float, required=True)
    p2.add_argument("--growth", type=float, required=True, help="预测期增速（fade 模式下为期初增速）")
    p2.add_argument("--discount-rate", type=float, default=DEFAULT_DISCOUNT_RATE)
    p2.add_argument("--terminal-growth", type=float, default=DEFAULT_TERMINAL_GROWTH)
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
    p2.add_argument("--terminal-growth-cap", type=float, default=DEFAULT_TERMINAL_GROWTH_CAP,
                    help="永续增速上限（默认 5%%，约当长期名义 GDP）。"
                         "超限即拒绝——永续快于经济增长在数学上不可持续。"
                         "如确有理由须显式放宽并在报告论证")
    p2.add_argument("--min-spread", type=float, default=DEFAULT_MIN_SPREAD,
                    help="折现率与永续增速的最小安全间距（默认 2pct）。"
                         "r=10%%/g=9.99%% 会得出 6915× OE 的荒谬估值，故挡在源头")
    p2.add_argument("-o", "--output",
                    help="输出 JSON 路径（含估值结果与终值占比结构化诊断，"
                         "供报告校验器机器读取）")

    p3 = sub.add_parser("expected-return",
                        help="三情景转期望年化回报率（Phase 4.5 机会成本对照强制使用）")
    p3.add_argument("--price", type=float,
                    help="当前股价（market_snapshot 底稿）。用 --scenarios-file 时可省略")
    p3.add_argument("--hold-years", type=int, default=DEFAULT_HOLD_YEARS, help=f"持有期，默认 {DEFAULT_HOLD_YEARS} 年")
    p3.add_argument("--scenarios",
                    help='三情景每股价值与概率，格式："悲观:105.12:0.3,基准:211.65:0.5,乐观:396.52:0.2"。'
                         '与 --scenarios-file 二选一，优先用后者（单一事实源）')
    p3.add_argument("--index-hurdle", type=float, default=DEFAULT_INDEX_HURDLE,
                    help="机会成本门槛（指数长期年化），默认 9%%。"
                         "必须 > --discount-rate，否则闸门二形同虚设（买在内在价值上"
                         "IRR 恒等于折现率，门槛低于折现率则任何不溢价的标的自动过闸）")
    p3.add_argument("--discount-rate", type=float, default=DEFAULT_DISCOUNT_RATE,
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
    p3.add_argument("--pessimistic-hurdle", type=float, default=DEFAULT_PESSIMISTIC_HURDLE,
                    help="悲观情景年化门槛（默认 0%，即最坏情况不亏本金）。"
                         "前提是悲观值来自独立方法，见 check_scenarios.py")
    p3.add_argument("--loss-prob-hurdle", type=float, default=DEFAULT_LOSS_PROB_HURDLE,
                    help=f"亏损概率上限（默认 {DEFAULT_LOSS_PROB_HURDLE:.0%}，"
                         f"valuation-guide 核心买入下行约束）。闸门二第④项")
    p3.add_argument("--scenarios-file",
                    help="已通过 check_scenarios.py 门禁的 data/scenarios.json。"
                         "提供时自动读取 price/情景/概率/折现率/护城河/股息率/"
                         "内在价值增速，避免命令行手抄导致口径漂移")
    p3.add_argument("--dividend-yield", type=float, default=0.0,
                    help="预期持有期平均股息率（小数，如 0.05）。**不再进入 IRR 计算**"
                         "（修正式已隐含分红+留存的全部股东回报，叠加即重复计算）；"
                         "仅用于披露分红占总回报比例——现金落袋 vs 账面增值的回报质量差异")
    p3.add_argument("-o", "--output", help="输出 JSON 路径")

    # ── REQ-P1-01：成长股 / 再投入型估值通道 ──
    p4 = sub.add_parser(
        "growth", help="成长股通道：成熟期稳态利润 × 到达概率折回，替代当期 OE 基期")
    p4.add_argument("--mature-oe", type=float,
                    help="成熟期稳态 Owner Earnings（与 market-cap 同币种同单位，建议百万）。"
                         "与 --mature-revenue + --mature-oe-margin 二选一")
    p4.add_argument("--mature-revenue", type=float,
                    help="成熟期稳态收入（三段式第一二段：渗透率天花板 × 份额 × ARPU 的产出）")
    p4.add_argument("--mature-oe-margin", type=float,
                    help="成熟期稳态 OE 利润率（小数；三段式第三段由单位经济反推，"
                         "非当期利润率外推）")
    p4.add_argument("--mature-state-basis", required=True,
                    help="成熟态推导依据（必填，须含 [E:] 指针）：渗透率/会员数/ARPU/利润率"
                         "各自的证据来源。成熟态是本通道最大的假设，必须可审计")
    p4.add_argument("--arrival-prob", type=float, required=True,
                    help="到达概率 p（(0,1] 小数）：增长兑现+利润率扩张+竞争存活的联合概率。"
                         "有基率锚时不应超过锚（见 --current-revenue）；无锚须论证")
    p4.add_argument("--arrival-prob-basis", required=True,
                    help="到达概率推导依据（必填，须含 [E:] 指针）——裸概率禁止，"
                         "它与情景概率同为最易被叙事污染的参数")
    p4.add_argument("--current-revenue", type=float, required=True,
                    help="当期收入（与成熟态同币种；用于反推所需 CAGR 并查基率锚——"
                         "到达概率与基率表挂钩的机器强制项）")
    p4.add_argument("--base-rate-revenue-usd", type=float,
                    help="规模分档用的美元口径收入（百万）。基率表按美元标定，"
                         "报告币种非美元时须换算后传入，否则分档可能错带")
    p4.add_argument("--base-rates-file",
                    help="基率表文件（REQ-P1-05 的 references/base-rates.md 建成后接入，"
                         "JSON 数组格式同 REVENUE_CAGR_BASE_RATES）。缺省用引擎内置表")
    p4.add_argument("--contribution-margin", type=float, required=True,
                    help="规模化边际贡献率（小数，可负）：收入 − 直接变动成本（内容/获客/"
                         "履约）后再除以收入。≤0 即单元经济未证，本通道拒绝服务（exit 2）")
    p4.add_argument("--ltv-cac", type=float,
                    help="LTV/CAC（可选但强烈建议）：客户终身价值 / 获客成本。<1 即获客在"
                         "毁灭价值，同样触发单元经济未证拒绝")
    p4.add_argument("--years-to-maturity", type=int, default=10,
                    help="到达年数 N（默认 10）：成熟态兑现所需年数，即折回期")
    p4.add_argument("--discount-rate", type=float, default=DEFAULT_DISCOUNT_RATE)
    p4.add_argument("--terminal-growth", type=float, default=DEFAULT_TERMINAL_GROWTH,
                    help="成熟期之后的永续增速（Gordon 口径与保守交叉核对共用）")
    p4.add_argument("--terminal-multiple", type=float,
                    help="终局倍数（成熟期 OE × 倍数作终值，市场口径）。缺省用 Gordon"
                         "（保守口径）；两口径都会输出并披露分歧——分歧 >30% 时"
                         "报告必须双口径并列（与基率检验同属『假设要过外部视角』）")
    p4.add_argument("--failure-equity-value", type=float, default=0.0,
                    help="到达失败时的股权残值（与 mature-oe 同单位；默认 0=归零口径）。"
                         "建议用独立方法锚（清算/历史最差年×危机倍数），与悲观情景同源")
    p4.add_argument("--market-cap", type=float, required=True,
                    help="当前市值（股权口径，与 mature-oe 同币种同单位）——用于反解"
                         "『现价隐含到达概率』，即本通道的反向 DCF")
    p4.add_argument("--shares", type=float,
                    help="摊薄股本（百万股），提供则输出每股口径")
    p4.add_argument("--terminal-growth-cap", type=float, default=DEFAULT_TERMINAL_GROWTH_CAP)
    p4.add_argument("--min-spread", type=float, default=DEFAULT_MIN_SPREAD)
    p4.add_argument("-o", "--output", help="输出 JSON 路径")

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
                              pessimistic_hurdle=args.pessimistic_hurdle,
                              loss_prob_hurdle=args.loss_prob_hurdle)
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

        # ---- 闸门二判定（REQ-P0-04：四项参与 + 护城河反推门槛为诊断）----
        g = res["gate2"]
        print(f"\n=== 闸门二（护城河档位：{g['moat'] or '未指定'}）===")
        rows = [
            ("①' 期望 IRR ≥ 折现率 r（最低机会成本）", g["expected_irr_floor"]),
            ("② 不收敛下限 = 股息率 + 内在价值增速（独立）", g["no_convergence_floor"]),
            ("③ 悲观情景年化（独立）", g["pessimistic_irr"]),
            ("④ 亏损概率 ≤ 上限（独立）", g["loss_probability"]),
        ]
        for label, item in rows:
            v, h, ok = item["value"], item["hurdle"], item["pass"]
            v_txt = f"{v:.2%}" if v is not None else "未提供"
            h_txt = f"{h:.2%}" if h is not None else "未设定"
            mark = "✓" if ok is True else ("✗" if ok is False else "—")
            print(f"  {mark} {label}: {v_txt}  门槛 {h_txt}")
        ci = g["consistency_expected_irr"]
        ci_mark = "✓" if ci["pass"] is True else ("✗" if ci["pass"] is False else "—")
        ci_h = f"{ci['hurdle']:.2%}" if ci["hurdle"] is not None else "未设定"
        print(f"  {ci_mark} [诊断] ① 期望 IRR vs 护城河反推门槛 {ci_h}（不参与判定）")
        if ci["hurdle_derivation"]:
            print(f"     ①门槛来源：{ci['hurdle_derivation']}")
        if ci["pass"] is False:
            print(f"     {g['consistency_check_diagnostic']}")
        if g["missing_inputs"]:
            print(f"  ⚠️  缺输入无法判定：{g['missing_inputs']}"
                  f"（--iv-growth 未提供时闸门二不可评）")
        if g.get("note"):
            print(f"  {g['note']}")
        if g["pass"] is True:
            print("  闸门二：通过（参与判定的四项全过）")
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

    if args.mode == "growth":
        # ---- 单元经济门（第一道门，先于一切估值）----
        # 区分「再投入」与「烧钱」的唯一可量化标准（REQ-P1-01 价值主张）：
        # 边际贡献率 ≤0 = 每多做一个单位生意就多亏一份钱，增长本身在毁灭价值。
        # 这类公司在旧框架与 Netflix 得到同样的"观察/拒绝"，在本通道被显式拒绝
        # ——Netflix（US 边际贡献 44%）与乐视型（内容成本无边界、贡献率为负）
        # 由此分道。拒绝服务而非给一个错数（与负基期拒绝同一纪律）。
        ue_proven = args.contribution_margin > 0 and (args.ltv_cac is None or args.ltv_cac >= 1.0)
        ue_detail = []
        if args.contribution_margin <= 0:
            ue_detail.append(f"规模化边际贡献率 {args.contribution_margin:.1%} ≤ 0")
        if args.ltv_cac is not None and args.ltv_cac < 1.0:
            ue_detail.append(f"LTV/CAC {args.ltv_cac:.2f} < 1（获客本身在毁灭价值）")
        if not ue_proven:
            print("🔴 单元经济未证：" + "；".join(ue_detail) + "。")
            print("   增长在单位层面毁灭价值——这不是『为未来投入』而是『烧钱』，")
            print("   成长通道对此类公司不适用（GROWTH_UNIT_ECONOMICS_UNPROVEN）。")
            print("   按 valuation-guide 方法树改走标准 OE 管道，结论通常是拒绝/排除；")
            print("   禁止通过调高到达概率或终局倍数让烧钱公司在成长通道里『看起来值钱』。")
            sys.exit(2)
        for _label, _val in (("--mature-state-basis", args.mature_state_basis),
                             ("--arrival-prob-basis", args.arrival_prob_basis)):
            if "[E:" not in (_val or ""):
                _code = ("GROWTH_ARRIVAL_PROB_UNANCHORED"
                         if _label == "--arrival-prob-basis" else None)
                raise SystemExit(
                    f"错误：{_label} 必须含 [E:] 证据指针——成熟态与到达概率是本通道"
                    f"最大的两个假设，裸假设禁止（与 S7 概率纪律同源）。"
                    + (f"违反项告警码：{_code}（人工登记进 verdict.codes）。"
                       if _code else ""))
        if args.mature_oe is None:
            if args.mature_revenue is None or args.mature_oe_margin is None:
                raise SystemExit("错误：须提供 --mature-oe，或 --mature-revenue + "
                                 "--mature-oe-margin（三段式产出）")
            mature_oe = args.mature_revenue * args.mature_oe_margin
        else:
            mature_oe = args.mature_oe
            if args.mature_revenue is not None and args.mature_oe_margin is not None:
                print("⚠ 同时提供了 --mature-oe 与 revenue×margin，以 --mature-oe 为准",
                      file=sys.stderr)

        # ---- 基率锚（到达概率与基率表挂钩，REQ-P1-05 接口）----
        table = None
        if args.base_rates_file:
            with open(args.base_rates_file, "r", encoding="utf-8") as _f:
                table = json.load(_f)
        # 所需 CAGR 优先用收入比值（与基率表口径一致）；直接给 --mature-oe 时
        # 以 OE 增长作增长要求的代理并在输出标注（OE 含利润率扩张，会高估增速要求，
        # 使锚偏紧——方向保守，可接受但须披露）。
        if args.current_revenue > 0 and args.mature_revenue:
            required_cagr = (args.mature_revenue / args.current_revenue) ** (
                1.0 / args.years_to_maturity) - 1.0
            cagr_basis = "revenue"
        elif args.current_revenue > 0:
            required_cagr = (mature_oe / args.current_revenue) ** (
                1.0 / args.years_to_maturity) - 1.0
            cagr_basis = "oe_proxy"
        else:
            required_cagr, cagr_basis = None, "unavailable"
        band_rev = args.base_rate_revenue_usd or args.current_revenue
        anchor, anchor_meta = revenue_growth_base_rate(band_rev, required_cagr, table)
        anchor_meta["cagr_basis"] = cagr_basis

        res = growth_channel_value(
            mature_oe, args.arrival_prob, args.years_to_maturity, args.discount_rate,
            terminal_growth=args.terminal_growth, terminal_multiple=args.terminal_multiple,
            failure_equity_value=args.failure_equity_value,
            terminal_g_cap=args.terminal_growth_cap, min_spread=args.min_spread)

        # ---- 反向求解：现价隐含到达概率 ----
        pv, fail_v = res["pv_today"], res["failure_equity_value"]
        implied_p = None
        if pv > fail_v:
            implied_p = (args.market_cap - fail_v) / (pv - fail_v)
        gordon_pv = res["gordon_cross_check_tv"] / ((1.0 + args.discount_rate)
                                                    ** args.years_to_maturity)
        gordon_implied_p = ((args.market_cap - fail_v) / (gordon_pv - fail_v)
                            if gordon_pv > fail_v else None)

        codes = ["GROWTH_TERMINAL_DOMINATED"]  # 结构性披露：永远随通道输出
        if implied_p is not None and implied_p >= 1.0:
            codes.append("GROWTH_PRICE_IMPLIES_CERTAIN_ARRIVAL")
        if anchor is not None and implied_p is not None and (
                (anchor > 0 and implied_p >= 2.0 * anchor) or implied_p - anchor >= 0.25):
            codes.append("GROWTH_IMPLIED_VS_BASERATE_GAP")
        if anchor is not None and args.arrival_prob > anchor:
            codes.append("GROWTH_ARRIVAL_PROB_ABOVE_BASERATE")
        unknown = unknown_codes(codes)
        if unknown:
            raise KeyError(f"未注册的告警码 {unknown}，请先在 scripts/alert_codes.py 登记")

        # ---- 档位带（引擎建议、裁决层定档——与双闸门哲学一致）----
        vps = res["probability_weighted_value"] / args.shares if args.shares else None
        if implied_p is not None and implied_p >= 1.0:
            band = "拒绝（透支）"
            band_reason = (f"现价隐含到达概率 {implied_p:.0%} ≥ 100%：连『必然到达』都"
                           "解释不了现价——价格已透支通道内全部假设，或定价了通道外"
                           "的叙事（叙事溢价）。除非论证更高成熟态/更短到达期且过基率检验")
        elif vps is not None and args.market_cap and args.shares and \
                res["probability_weighted_value"] >= args.market_cap:
            band = "小仓位试探候选"
            band_reason = ("价格 ≤ 概率加权成长价值：到达赔率站在买方——但仍受通道档位"
                           "上限约束（终值结构性主导），且必须过 expected-return 闸门二"
                           "（期望 IRR ≥ r / 悲观 IRR / 亏损概率）")
        else:
            band = "观察等价格"
            band_reason = ("单元经济已证、公司真实，与市场的分歧在到达赔率而非生意真假"
                           "——触发参考 = 概率加权成长价值（须披露可达性）；"
                           "单元经济证据深化或价格回落均可重估")
        out = {
            "mode": "growth",
            "mature_state": {
                "mature_oe": mature_oe,
                "mature_revenue": args.mature_revenue,
                "mature_oe_margin": args.mature_oe_margin,
                "basis": args.mature_state_basis,
            },
            "unit_economics": {
                "contribution_margin": args.contribution_margin,
                "ltv_cac": args.ltv_cac,
                "proven": True,
                "gate_note": "GROWTH_UNIT_ECONOMICS_UNPROVEN 未触发（规模化边际贡献率>0"
                             + (" 且 LTV/CAC≥1" if args.ltv_cac is not None else "") + "）",
            },
            "arrival": {
                "probability": args.arrival_prob,
                "basis": args.arrival_prob_basis,
                "years_to_maturity": args.years_to_maturity,
                "required_cagr": required_cagr,
                "required_cagr_basis": cagr_basis,
                "base_rate_anchor": anchor,
                "base_rate_meta": anchor_meta,
                "base_rate_hook": "REQ-P1-05：references/base-rates.md 建成后由 "
                                  "--base-rates-file 接管（当前为引擎内置表，"
                                  "与 valuation-guide 基率检验表同源）",
            },
            **res,
            "implied": {
                "market_cap": args.market_cap,
                "implied_arrival_prob": implied_p,
                "gordon_cross_check_implied_prob": gordon_implied_p,
                "note": "反解 MC = p×PV(终值) + (1−p)×失败残值；≥100% 即透支信号",
            },
            "value_per_share": vps,
            "sensitivity": {
                "arrival_prob_pm0.10": [
                    (min(1.0, args.arrival_prob + 0.10) * pv
                     + (1 - min(1.0, args.arrival_prob + 0.10)) * fail_v) / args.shares
                    if args.shares else None,
                    (max(0.0, args.arrival_prob - 0.10) * pv
                     + (1 - max(0.0, args.arrival_prob - 0.10)) * fail_v) / args.shares
                    if args.shares else None],
                "years_to_maturity_pm2": [
                    (args.arrival_prob * res["terminal_value_mature"]
                     / ((1 + args.discount_rate) ** (args.years_to_maturity + 2))
                     + (1 - args.arrival_prob) * fail_v) / args.shares
                    if args.shares else None,
                    (args.arrival_prob * res["terminal_value_mature"]
                     / ((1 + args.discount_rate) ** max(1, args.years_to_maturity - 2))
                     + (1 - args.arrival_prob) * fail_v) / args.shares
                    if args.shares else None],
                "terminal_multiple_pm20pct": ([
                    (args.arrival_prob * (mature_oe * args.terminal_multiple * 1.2)
                     / ((1 + args.discount_rate) ** args.years_to_maturity)
                     + (1 - args.arrival_prob) * fail_v) / args.shares if args.shares else None,
                    (args.arrival_prob * (mature_oe * args.terminal_multiple * 0.8)
                     / ((1 + args.discount_rate) ** args.years_to_maturity)
                     + (1 - args.arrival_prob) * fail_v) / args.shares if args.shares else None]
                    if args.terminal_multiple else None),
            },
            "verdict_band": {
                "cap": GROWTH_VERDICT_CAP,
                "suggestion": band,
                "reasons": band_reason,
                "codes": codes,
                "note": "引擎建议档位带，最终档位由双闸门与裁决层确定；"
                        "通道档位上限恒为小仓位试探（终值结构性主导纪律）",
            },
            "codes": codes,
        }
        # ---- 打印 ----
        tv_m = res["terminal_value_mature"]
        print(f"成熟期稳态 OE : {mature_oe:,.0f}（{args.mature_state_basis[:60]}…）")
        print(f"终值（{res['terminal_value_method']}口径）: {tv_m:,.0f}"
              f"（Gordon 交叉核对 {res['gordon_cross_check_tv']:,.0f}，"
              f"隐含倍数 {res['gordon_implied_multiple']:.1f}x）")
        if res["terminal_value_method"] == "multiple":
            div = abs(tv_m - res["gordon_cross_check_tv"]) / res["gordon_cross_check_tv"]
            if div > 0.30:
                print(f"⚠ 终局倍数与 Gordon 口径分歧 {div:.0%} > 30%：报告必须双口径并列，"
                      f"并论证所选口径的依据（市场倍数含质量溢价，Gordon 是纪律下限）")
        print(f"折回 {args.years_to_maturity} 年（r={args.discount_rate:.1%}）: "
              f"成功分支现值 {pv:,.0f}")
        print(f"到达概率 p={args.arrival_prob:.0%} × 成功 + {1 - args.arrival_prob:.0%} × "
              f"失败残值 {fail_v:,.0f}")
        print(f"概率加权价值 : {res['probability_weighted_value']:,.0f}"
              + (f"（每股 {vps:,.2f}）" if vps else ""))
        if anchor is not None:
            _cagr_label = {"revenue": "收入 CAGR", "oe_proxy": "OE CAGR（代理）"}.get(
                cagr_basis, "CAGR")
            print(f"\n基率锚       : 所需{_cagr_label} {required_cagr:.1%}（规模分档 "
                  f"{anchor_meta.get('scale_band', '?')}）→ 历史达成比例上界 {anchor:.0%}")
            if args.arrival_prob > anchor:
                print(f"⚠ 到达概率 {args.arrival_prob:.0%} 高于基率锚 {anchor:.0%}"
                      f"（GROWTH_ARRIVAL_PROB_ABOVE_BASERATE）：到达=增长+利润率扩张+"
                      f"竞争存活的联合概率，超过『仅增长兑现』的历史比例须在 basis 中论证")
        else:
            print(f"\n基率锚       : 无（{anchor_meta.get('note', '未提供')}）")
        if implied_p is not None:
            print(f"现价隐含到达概率 : {implied_p:.0%}"
                  + (f"（Gordon 口径 {gordon_implied_p:.0%}）" if gordon_implied_p else ""))
            if implied_p >= 1.0:
                print("🔴 隐含概率 ≥100%：连必然到达都解释不了现价——透支信号")
            elif anchor is not None and implied_p >= 2.0 * anchor:
                print(f"⚠ 市场要求的到达概率是基率锚的 {implied_p / anchor:.1f} 倍——"
                      f"市场比框架乐观，分歧显式化（这是观察/拒绝的分界输入）")
        print(f"\n档位带（上限 {GROWTH_VERDICT_CAP}）: {band}")
        print(f"  {band_reason}")
        print(f"告警码: {' '.join(codes)}")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
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
