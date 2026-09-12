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
  5. sotp：持仓型控股 SOTP（REQ-P1-02）——Σ(持仓毛值×变现折价) + 经营业务
     − 母公司净债 = equity NAV，×(1−控股折价)；控股折价挂行业基率带；
     反解"现价隐含控股折价"。软银/伯克希尔/Prosus 类并表错位与重估污染的
     公司（OE 通道结构性失真，OBS-2019-06-01）走本通道

用法：
  python3 reverse_dcf.py implied-growth --market-cap 50000 --base-oe 2000 \
      --discount-rate 0.10 --terminal-growth 0.025 --years 10
  python3 reverse_dcf.py forward-value --base-oe 2000 --growth 0.12 \
      --discount-rate 0.10 --terminal-growth 0.025 --years 10 --shares 1000 \
      [--fade]   # 增速在预测期内线性衰减到永续增速（更保守、更真实）
  python3 reverse_dcf.py expected-return --price 209.75 --hold-years 5 \
      --scenarios "悲观:105:0.3,基准:212:0.5,乐观:397:0.2" --index-hurdle 0.09 \
      [--dividend-yield 0.05]   # 高股息标的必填，与门槛比较用含息 IRR
      # REQ-P1-04：scenarios.json 可选登记 discount_rate_derivation（行业档
      # ×Rf 分层表一致性 + market 校准 floor 门槛）与 probability_derivation
      # （护城河得分+变异认知+红队悲观 → 概率映射 + ±10pp 敏感性表），
      # 两块均须挂 [E:] rationale_ref——让定性分析真正进入数字
  python3 reverse_dcf.py growth --market-cap 53128 --current-revenue 8832 \
      --mature-revenue 36000 --mature-oe-margin 0.20 --terminal-multiple 20 \
      --arrival-prob 0.25 --years-to-maturity 10 --shares 436.456 \
      --contribution-margin 0.44 --failure-equity-value 646 \
      --mature-state-basis "渗透率×ARPU×利润率反推 [E:...]" \
      --arrival-prob-basis "基率锚+单元经济证据 [E:...]"   # REQ-P1-01 成长股通道
  python3 reverse_dcf.py sotp --holdings-file data/sotp_holdings.json \
      --net-debt 6200000 --net-debt-basis parent_standalone \
      --holding-discount 0.40 --holding-profile asian_conglomerate_no_convergence \
      --holding-discount-basis "历史 NAV 折价带 30-50% 中枢略保守 [E:...]" \
      --market-cap 10890000 --shares 2107.667 -o data/sotp_value.json

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


def moat_irr_hurdle(moat, discount_rate, hold_years, score=None):
    """由闸门一的安全边际要求反推期望 IRR 门槛，保证两闸门自洽。

    返回 (mos_requirement, irr_hurdle)；moat='none' 时返回 (None, None)——
    无护城河不给买入结论，闸门讨论无意义。

    REQ-P1-03：提供 score（0~100 定量得分）时门槛取平滑函数值而非阶跃常数；
    仅给评级词时走 legacy 常数（12 案基线字节级兼容）。
    """
    mos = (mos_requirement_from_score(score) if score is not None
           else MOAT_MOS_REQUIREMENT.get(moat))
    if mos is None:
        return None, None
    return mos, (1.0 + discount_rate) * (1.0 / (1.0 - mos)) ** (1.0 / hold_years) - 1.0


# ── REQ-P1-03 护城河评级连续化（2026-09-11）──────────────────────────
#
# 动因（神华 2015 案 diff.md 第 42 行已实证）：评级为离散词且两道闸门门槛阶跃
# 挂词——"窄"要 MoS 40%（实际 37.7%，差 2.3pct 被拦）、闸门二①要 21.83%，
# 若评"宽"则 25%/16.5% 双双放行，一字之差档位跳 2 档；persona_buffett 只能写
# "窄（偏宽）"这类自造中间词，正是阶跃传导逼出来的。边界处的不稳定让回测
# 命中率对评级措辞极度敏感，掩盖真正的框架问题。
#
# 连续化设计（通道建设而非阈值放松——平滑函数在带内处处 ≥ legacy 常数，
# 仅在锚点相等：宽带顶 s=100 → 25%，宽/窄边界 s=65 → 40% = 旧窄锚）：
#   1. 护城河评级新增 0~100 定量得分（moat-framework 第二节半三组件评分：
#      超额回报证据 / 源硬度 / 定标与趋势修正），评级词降级为得分的分带投影
#      ——报告词表 {wide,narrow,none} 兼容不变，S1 校验不动；
#   2. MoS 门槛 = 分段线性平滑函数（在分带边界连续）：
#        s = 35（窄/无边界）→ 50%（可买带内最严）
#        s = 65（宽/窄边界）→ 40%（旧窄锚）
#        s = 100            → 25%（旧宽锚）
#      s < 35 → None（不给买入结论——这是政策边界而非数值边界，由双档披露
#      而非由连续性消除）；
#   3. 得分落在边界带（边界 ±5 分窗口）→ 强制输出双档报告 +
#      MOAT_BOUNDARY_BAND_DUAL，标注「结论对护城河判断敏感」——比强行给
#      一个档位诚实，也让投资者知道该把精力花在哪个判断上；
#   4. 得分与评级词同时给出时强制一致性校验（词必须等于分带投影），
#      禁止两套口径并存；得分必须挂 [E:] 依据（裸分数禁止——它直接决定门槛）。
MOAT_SCORE_WIDE_MIN = 65       # ≥65 → wide
MOAT_SCORE_NARROW_MIN = 35     # ≥35 → narrow；<35 → none
MOAT_SCORE_BOUNDARY_HALFWIDTH = 5.0   # 边界带半宽：边界 ±5 分内强制双档报告


def mos_requirement_from_score(score):
    """平滑 MoS 门槛（REQ-P1-03）：分段线性、分带边界处连续、带内 ≥ legacy。

    s<35 → None（不给买入结论）；35→50%、65→40%、100→25%。
    """
    if score is None:
        return None
    if not (isinstance(score, (int, float)) and 0 <= score <= 100):
        raise ValueError(f"护城河得分须为 0~100 的数值，收到 {score}")
    if score < MOAT_SCORE_NARROW_MIN:
        return None
    if score < MOAT_SCORE_WIDE_MIN:
        return (0.50 - (score - MOAT_SCORE_NARROW_MIN)
                / (MOAT_SCORE_WIDE_MIN - MOAT_SCORE_NARROW_MIN) * 0.10)
    return (0.40 - (score - MOAT_SCORE_WIDE_MIN)
            / (100.0 - MOAT_SCORE_WIDE_MIN) * 0.15)


def moat_word_from_score(score):
    """得分的分带投影：≥65 wide / ≥35 narrow / <35 none（评级词的唯一合法来源）。"""
    if score >= MOAT_SCORE_WIDE_MIN:
        return "wide"
    if score >= MOAT_SCORE_NARROW_MIN:
        return "narrow"
    return "none"


def moat_boundary_band(score):
    """边界带检测：得分落在任一分带边界 ±5 分窗口内 → 双档报告强制输出。

    返回 dict：in_band / edge（分界分）/ edge_name / band（窗口）/ adjacent_words。
    """
    edges = (("narrow/wide", MOAT_SCORE_WIDE_MIN),
             ("none/narrow", MOAT_SCORE_NARROW_MIN))
    for edge_name, e in edges:
        if abs(score - e) <= MOAT_SCORE_BOUNDARY_HALFWIDTH:
            return {
                "in_band": True, "edge": e, "edge_name": edge_name,
                "band": [e - MOAT_SCORE_BOUNDARY_HALFWIDTH,
                         e + MOAT_SCORE_BOUNDARY_HALFWIDTH],
                "adjacent_words": tuple(edge_name.split("/")),
                "halfwidth": MOAT_SCORE_BOUNDARY_HALFWIDTH,
            }
    return {"in_band": False, "edge": None, "edge_name": None,
            "band": None, "adjacent_words": None,
            "halfwidth": MOAT_SCORE_BOUNDARY_HALFWIDTH}


# ── REQ-P1-04 折现率与情景概率的证据传导（2026-09-11）──────────────────
#
# 动因：折现率统一 10%、三情景概率固定（30/50/20），是全流程对 IRR 与期望值
# 敏感度最高、证据最弱的两个参数——Phase 3 护城河、Phase 4.5 变异认知、红队
# 悲观概率对最终数字毫无影响，只影响文字。"定性分析进入数字"要求两者都建立
# 证据传导链。
#
# 设计原则（与 P1-01/02/03 同源）：通道建设而非阈值放松——
#   1. 折现率分层**只向上**：10% 纪律下限不动，行业风险溢价只能把高波动行业
#      的 r 往上抬（周期/金融 +1pct、高波动成长 +2pct、控股复杂治理 +1pct，
#      必需消费/宽基 0），下限由 max(10%, 10Y国债+4pct) 决定；
#   2. 概率映射的锚点 = 规范默认（check_scenarios 的 default_probabilities
#      兜底值 30/50/20）：得分 65 → 恰为默认值，带内悲观权重 ≥ 默认（得分越低
#      越悲观，方向与 MoS 平滑门槛一致）；
#   3. 红队悲观概率是**下界**（max）：红队质询只能让结论更悲观，不能更乐观
#      ——保守不对称；
#   4. 一切 opt-in：两个 derivation 块不出现时引擎走 legacy 路径，
#      12 案基线字节级不变；块出现但字段缺证据 → 硬拒绝。
#
# 行业溢价档位校准参考 Damodaran（NYU Stern）行业股权资本成本数据集的相对
# 排序（科技/互联网 > 金融/材料 > 必需消费/公用事业），取整并偏保守，
# 非逐行业抄录；Rf 锚取案例时点 10Y 国债收益率（[E:] 证据）。
INDUSTRY_RISK_PREMIUM = {
    "stable": 0.00,             # 必需消费/公用事业/成熟医药：低周期敏感性
    "standard": 0.00,           # 宽基默认（一般工业/消费/服务）
    "cyclical": 0.01,           # 周期/资源/航运/化工/地产：盈利波动放大
    "financials": 0.01,         # 银行/保险：杠杆放大资产端错误
    "speculative_growth": 0.02, # 高波动成长/未盈利科技/流媒体
    "holding_complex": 0.01,    # 控股集团/多层治理（SOTP 类）
}
# 不收敛下限门槛的市场校准（P0-04③ 移交项落地）：= 各市场 10Y 国债 + 2~3pct。
# 旧全局常量 6% 隐含 CNY 语境（"长期国债+2~3pct"），跨市场案例（9984.T 的
# floor 5.4% vs 6% CNY 门槛）存在口径噪声。opt-in：仅当 derivation 块声明
# market 且未显式传 --floor-hurdle 时生效；默认（无块）仍 6%，基线不动。
MARKET_FLOOR_HURDLES = {
    "CN": 0.06,   # 10Y 国债 ~3% + 3pct（现行值，不变）
    "HK": 0.05,   # 联系汇率随美债 ~2% + 3pct
    "US": 0.05,   # 10Y UST 1.5~4.5% + 2~3pct 取中枢
    "JP": 0.03,   # JGB ~0% + 3pct
}
# 概率映射锚点（分段线性，作用于悲观/乐观两翼，基准 = 1 − 两翼）：
#   得分 35 → 悲观 35% / 乐观 15%    （窄带下沿，最悲观）
#   得分 65 → 悲观 30% / 乐观 20%    （= 规范默认 30/50/20）
#   得分 100 → 悲观 25% / 乐观 25%   （宽带顶）
# 两翼斜率相反 ⇒ 基准在锚点间恒为 50%。得分 <35 无买入结论，映射无意义。
PROB_ANCHORS = {35: (0.35, 0.15), 65: (0.30, 0.20), 100: (0.25, 0.25)}
VARIANT_PERCEPTION_ADJ = {"weak": 0.05, "neutral": 0.0, "strong": -0.05}
PROB_PESS_CLAMP = (0.20, 0.60)        # 映射后悲观权重硬边界
PROB_DEVIATION_FREE = 0.02            # 采用值偏离映射 ≤2pp 免论证
PROB_DEVIATION_MAX = 0.10             # 偏离 >10pp 即使有论证也硬拒
PROB_SENSITIVITY_SHIFT = 0.10         # 验收要求的 ±10pp 敏感性摆幅

# ── REQ-P1-05 尾部风险单列与基率锚定（2026-09-12）──────────────────
# p_tail 锚点（排雷得分 → 尾部概率，分段线性）：
#   得分 0  → 1%   （通过排雷的干净公司：欺诈/监管突变/黑天鹅的年化基率地板
#                   ——好治理也降不到 0，未知未知必须计价）
#   得分 5  → 10%  （红旗带：≥3 红旗本应排除，这里是"边缘通过"世界观）
#   得分 20 → 30%  （双 veto 级别 = Phase 0 排除世界：康美 2017 形态
#                   ——存贷双高 + 利率倒挂双 veto，事前看归零概率三成）
# 锚点是校准值而非统计断言：定位"把 -5% 增速折扣换成独立归零项"的量级，
# 逼尾部风险进数字而不是挤进悲观情景的增速里（REQ-P1-05 需求原文）。
TAIL_P_ANCHORS = {0: 0.01, 5: 0.10, 20: 0.30}
GOVERNANCE_TAIL_ADJ = {"good": -0.005, "normal": 0.0, "poor": 0.03}
TAIL_P_FLOOR = 0.01                   # 黑天鹅基率地板：好治理不打穿
TAIL_P_CAP = 0.50                     # 映射域上界（双 veto + 恶治理也到不了）
TAIL_COVERAGE_MIN = 0.50              # forensic_screen 约定：算术覆盖率低于此，
TAIL_COVERAGE_FLOOR = 0.05            # 低分不构成安全证据 → p_tail 至少 5%
TAIL_IRR_DRAG_DISCLOSE = 0.02         # p_tail 拉低期望 IRR ≥2pct 触发披露码
# 行业收入增速基率（长期 10 年窗口 CAGR 横截面分位；乐观情景增速 > p80 须
# Phase 4.5 变异认知 [E:] 支撑，由 check_scenarios.py S10 拦截）。校准锚：
# p50 ≈ 行业长期名义收入中枢（名义 GDP ~4~5% ± 行业趋势），p80 ≈ 周期上行/
# 高增长带门限，相对排序按 Damodaran 行业数据集校准（用途是拦"20 年 20%"
# 级别的想象力，不追求分位精确定位）。白名单纪律：新行业先登记再使用。
INDUSTRY_GROWTH_BASE_RATES = {
    "food_processing":      {"p50": 0.03,  "p80": 0.07},   # 双汇：必选消费≈名义GDP
    "steel":                {"p50": 0.02,  "p80": 0.08},   # 鞍钢：量平、价周期波动
    "pharma":               {"p50": 0.05,  "p80": 0.12},   # 康美：医药（含中药）
    "liquor_premium":       {"p50": 0.08,  "p80": 0.15},   # 茅台：高端白酒量价
    "auto_parts":           {"p50": 0.04,  "p80": 0.10},   # 福耀：≈全球汽车产量+1
    "coal_energy":          {"p50": 0.02,  "p80": 0.08},   # 神华：煤炭+电力
    "shipping":             {"p50": 0.03,  "p80": 0.12},   # 中远海控：运价振幅
    "holding_investment":   {"p50": 0.05,  "p80": 0.12},   # 软银：控股投资净值
    "consumer_electronics": {"p50": 0.06,  "p80": 0.15},   # 苹果：硬件+平台
    "imaging_legacy":       {"p50": -0.05, "p80": 0.03},   # 柯达：结构性衰退
    "streaming_media":      {"p50": 0.15,  "p80": 0.30},   # Netflix：内容订阅
    "saas_communications":  {"p50": 0.15,  "p80": 0.30},   # Zoom：视频通信
}



def stratified_discount_rate(industry_tier, rf_10y=None):
    """折现率分层（REQ-P1-04）：r = max(10%, Rf+4pct) + 行业溢价。

    industry_tier 须在 INDUSTRY_RISK_PREMIUM 白名单内（否则 SystemExit，
    注册码 DR_INDUSTRY_TIER_UNKNOWN）。rf_10y 为案例时点 10Y 国债收益率
    （小数）；缺省时下限取 10% 纪律值。返回 (rate, composition_dict)。
    """
    premium = INDUSTRY_RISK_PREMIUM.get(industry_tier)
    if premium is None:
        raise SystemExit(
            f"错误：industry_tier `{industry_tier}` 未注册，应为 "
            f"{sorted(INDUSTRY_RISK_PREMIUM)}（注册码 DR_INDUSTRY_TIER_UNKNOWN）"
            "——行业档白名单防自造档位，与护城河词表同源纪律")
    floor = max(DEFAULT_DISCOUNT_RATE,
                (rf_10y + 0.04) if rf_10y is not None else 0.0)
    rate = floor + premium
    return rate, {
        "floor": floor, "rf_10y": rf_10y, "industry_tier": industry_tier,
        "industry_premium": premium, "rate": rate,
        "formula": "max(10% 纪律下限, 10Y国债+4pct) + 行业溢价",
        "source": "行业档校准参考 Damodaran(NYU Stern) 行业股权资本成本相对"
                  "排序，取整偏保守；分层只向上、10% 下限不动",
    }


def _prob_lerp(score):
    """概率映射的分段线性两翼插值：返回 (悲观, 乐观)。"""
    if score < MOAT_SCORE_NARROW_MIN:
        return None
    if score < MOAT_SCORE_WIDE_MIN:
        lo, hi = PROB_ANCHORS[35], PROB_ANCHORS[65]
        t = (score - 35) / (MOAT_SCORE_WIDE_MIN - 35)
    else:
        lo, hi = PROB_ANCHORS[65], PROB_ANCHORS[100]
        t = (score - MOAT_SCORE_WIDE_MIN) / (100.0 - MOAT_SCORE_WIDE_MIN)
    pess = lo[0] + (hi[0] - lo[0]) * t
    opt = lo[1] + (hi[1] - lo[1]) * t
    return pess, opt


def map_scenario_probabilities(moat_score, variant_perception="neutral",
                               red_team_pessimistic=None):
    """情景概率映射（REQ-P1-04）：护城河得分 + 变异认知 + 红队 → 三情景概率。

    传导链（每步可审计，让定性分析真正进入数字）：
      ① 基线 = 得分的分段线性映射（锚 65 → 30/50/20 规范默认）；
      ② 变异认知调整（Phase 4.5 三问结论）：weak 三问答不出 → 悲观 +5pp；
        strong 分歧明确且量化 → 悲观 −5pp；可调范围 ±5pp；
      ③ 红队悲观概率 = max() 下界：红队最强空头逻辑的成立概率只允许把悲观
        权重往上推（保守不对称），不允许往下拉；
      ④ clamp 到 PROB_PESS_CLAMP；乐观 = 映射值不动，基准 = 1 − 两翼。
    得分 <35 返回 None（无买入结论，概率传导无意义）。
    返回 dict：probabilities / chain（逐步前后值）/ anchors / ranges。
    """
    if moat_score is None:
        raise SystemExit("错误：概率映射需要护城河得分（probability_derivation"
                         " 缺 moat_score 且顶层无 moat_score——注册码 "
                         "PROB_DERIVATION_INVALID）")
    wings = _prob_lerp(float(moat_score))
    if wings is None:
        return None
    if variant_perception not in VARIANT_PERCEPTION_ADJ:
        raise SystemExit(
            f"错误：variant_perception `{variant_perception}` 非法，应为 "
            f"{sorted(VARIANT_PERCEPTION_ADJ)}（Phase 4.5 三问结论：weak="
            "三问答不出 / neutral / strong=分歧明确且量化）——注册码 "
            "PROB_DERIVATION_INVALID")
    p_pess, p_opt = wings
    chain = [("基线（得分映射）", {"悲观": round(p_pess, 6), "乐观": round(p_opt, 6),
                               "基准": round(1 - p_pess - p_opt, 6)})]
    adj = VARIANT_PERCEPTION_ADJ[variant_perception]
    if adj:
        p_pess += adj
        chain.append((f"变异认知 {variant_perception}（{'+' if adj > 0 else ''}{adj:.0%}）",
                      {"悲观": round(p_pess, 6)}))
    if red_team_pessimistic is not None:
        if not (0.0 < red_team_pessimistic <= 1.0):
            raise SystemExit(
                f"错误：red_team_pessimistic {red_team_pessimistic} 须在 (0,1]，"
                "注册码 PROB_DERIVATION_INVALID（红队最强空头逻辑的成立概率）")
        before = p_pess
        p_pess = max(p_pess, red_team_pessimistic)
        if p_pess != before:
            chain.append((f"红队悲观概率下界 max(→{red_team_pessimistic:.0%})",
                          {"悲观": round(p_pess, 6)}))
        else:
            chain.append((f"红队悲观概率 {red_team_pessimistic:.0%}（低于映射，不约束）",
                          {"悲观": round(p_pess, 6)}))
    lo, hi = PROB_PESS_CLAMP
    clamped = min(max(p_pess, lo), hi)
    if clamped != p_pess:
        chain.append((f"clamp [{lo:.0%},{hi:.0%}]", {"悲观": clamped}))
        p_pess = clamped
    p_base = 1.0 - p_pess - p_opt
    return {
        "probabilities": {"悲观": p_pess, "基准": p_base, "乐观": p_opt},
        "chain": chain,
        "moat_score": float(moat_score),
        "variant_perception": variant_perception,
        "red_team_pessimistic": red_team_pessimistic,
        "anchors": {"35": "35/50/15", "65": "30/50/20（规范默认）",
                    "100": "25/50/25"},
        "ranges": {
            "variant_perception": "±5pp（作用于悲观权重）",
            "red_team": "max() 下界（只允许更悲观）",
            "pessimistic_clamp": f"[{lo:.0%},{hi:.0%}]",
            "adoption_deviation": (
                f"采用值偏离映射 ≤{PROB_DEVIATION_FREE:.0%} 免论证；"
                f"≤{PROB_DEVIATION_MAX:.0%} 须 [E:] 论证；超限硬拒"),
        },
    }


def map_tail_probability(forensic_score, arithmetic_coverage=None,
                         governance="normal"):
    """尾部概率映射（REQ-P1-05）：排雷得分 × 治理 → p_tail。

    输入来自 Phase 0 排雷脚本（forensic_screen.py）的输出：
      forensic_score      veto×10 + redflag×1 加权命中数
      arithmetic_coverage 算术条款覆盖率（下游须知：低覆盖率下的低分
                          不构成安全证据——forensic_screen.downstream_note
                          的机器兑现：覆盖率 <50% 时 p_tail 至少 5%）
      governance          治理评分词表 good/normal/poor（须挂 [E:] 依据）

    与 map_scenario_probabilities 的关键差异：p_tail **纯映射、不可采用偏离**。
    三情景概率是"分析判断"（允许带论证偏离映射），尾部概率是"防自欺底线"
    ——允许分析师把 p_tail 调低，等于允许把"公司可能归零"论证没（与红队
    下界保守不对称同理）。治理修正只许变差变差的方向有限：good 也打不穿
    黑天鹅基率地板 1%。

    返回 dict：p_tail / chain（每步说明）/ anchors / floors_applied。
    """
    if not isinstance(forensic_score, (int, float)) or forensic_score < 0:
        raise SystemExit(
            f"错误：tail_risk_derivation.forensic_score `{forensic_score}` "
            "须为 ≥0 的数值（forensic_screen.py 输出，注册码 "
            "TAIL_DERIVATION_UNANCHORED）")
    if governance not in GOVERNANCE_TAIL_ADJ:
        raise SystemExit(
            f"错误：governance `{governance}` 非法，应为 "
            f"{sorted(GOVERNANCE_TAIL_ADJ)}（治理评分词表，注册码 "
            "TAIL_DERIVATION_UNANCHORED）——与护城河/行业档白名单同源纪律")
    cov = 1.0 if arithmetic_coverage is None else float(arithmetic_coverage)
    if not (0.0 <= cov <= 1.0):
        raise SystemExit(
            f"错误：arithmetic_coverage `{arithmetic_coverage}` 须在 [0,1]"
            "（forensic_screen.py 输出，注册码 TAIL_DERIVATION_UNANCHORED）")

    score = float(forensic_score)
    chain = []
    # 分段线性（锚 0→1%、5→10%、20→30%；>20 段外取顶锚）
    if score >= 20.0:
        p = TAIL_P_ANCHORS[20]
    elif score >= 5.0:
        p = (TAIL_P_ANCHORS[5]
             + (score - 5.0) / 15.0 * (TAIL_P_ANCHORS[20] - TAIL_P_ANCHORS[5]))
    else:
        p = (TAIL_P_ANCHORS[0]
             + score / 5.0 * (TAIL_P_ANCHORS[5] - TAIL_P_ANCHORS[0]))
    chain.append((f"排雷得分 {score:g} 分段线性（锚 0→1%/5→10%/20→30%）",
                  {"p_tail": p}))
    # 治理修正（poor +3pp / good −0.5pp；地板见下）
    adj = GOVERNANCE_TAIL_ADJ[governance]
    if adj:
        p += adj
        chain.append((f"治理评分 {governance}（{'+' if adj > 0 else ''}{adj:.1%}）",
                      {"p_tail": p}))
    floors = []
    # 地板 1%：黑天鹅基率，好治理不打穿
    if p < TAIL_P_FLOOR:
        p = TAIL_P_FLOOR
        floors.append("black_swan_floor")
        chain.append((f"黑天鹅基率地板 {TAIL_P_FLOOR:.0%}（好治理不构成免疫）",
                      {"p_tail": p}))
    # 覆盖率地板：低覆盖率下的低分不是安全证据
    if cov < TAIL_COVERAGE_MIN and p < TAIL_COVERAGE_FLOOR:
        p = TAIL_COVERAGE_FLOOR
        floors.append("coverage_floor")
        chain.append((f"算术覆盖率 {cov:.0%} < {TAIL_COVERAGE_MIN:.0%}——"
                      f"低覆盖率低分≠安全，p_tail 抬至 {TAIL_COVERAGE_FLOOR:.0%}",
                      {"p_tail": p}))
    # 上界（防御性：映射域内不可达）
    if p > TAIL_P_CAP:
        p = TAIL_P_CAP
        floors.append("cap")
    return {
        "p_tail": p,
        "chain": chain,
        "forensic_score": score,
        "arithmetic_coverage": cov,
        "governance": governance,
        "floors_applied": floors,
        "anchors": {"0": "1%（干净通过：欺诈/黑天鹅年化基率）",
                    "5": "10%（红旗带）", "20": "30%（双 veto＝Phase 0 排除世界）"},
    }


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


# ── REQ-P1-02 持仓型控股 SOTP 通道（2026-09-11）────────────────────────
#
# 动因（OBS-2019-06-01 软银案，方向与 Netflix 相反的框架失效）：持仓型控股
# 公司（软银/伯克希尔/Prosus/复星类）的 OE 被三类口径污染——①并表错位
# （OCF 含 100% Sprint/9434，股东按 84.4%/66.49% 享有）；②非现金重估
# （IFRS 9 FVTPL 持仓公允价值变动直接入损益）；③口径重分类（分红收入在
# 营业外/营业之间改列）。owner yield 21.1% 对股息率 0.43% 的公司是数学
# 不可能——差额全部是污染。旧工具 --add-back/--deduct 是补丁：它依赖执行者
# 知道该剔什么、剔多少，且不留结构化记录。
#
# 通道数学（三段式，替代单一 OE 框架）：
#   可投资价值 = [ Σ(持仓归属毛值 × 变现折价) + 经营业务价值 − 母公司净债 ]
#                × (1 − 控股折价)
#   即 equity NAV × (1−holding_discount)。两层折价分工：
#     · 变现折价（持仓级 liquidity_haircut）：这笔资产今天卖能拿回几成——
#       大宗冲击/私募估值水分/税务，逐项登记；
#     · 控股折价（整体 holding_discount）：市场对"钱在别人手里"的定价——
#       治理摩擦/资本配置不可验证/无收敛机制，一个参数、须挂基率带。
#
# 与旧通道的正交性：
#   1. 结构化持仓表（硬拒绝）：标的/持股比例/估值方法/流动性/变现折价率/
#      [E:] 证据六要素不全即通道拒绝服务（exit 2）——"不留结构化记录"是
#      本需求要消灭的补丁形态，通道入口即拦。
#   2. 净债口径门（并表错位防线）：持仓按持股比例计价而净债用合并口径，
#      会双重计入少数股东应担债务（软银案 alternative_treatment 教训：
#      合并净债 11.83 万亿全额扣减属错误做法，已弃用）——净债口径显式
#      声明，合并口径触发告警。
#   3. 控股折价挂行业基率带：折价率是 SOTP 结论的最大摆动因子（valuation-guide
#      多元集团纪律），低于基率带下界 = 比历史实证更乐观，须论证收敛机制。
#   4. 反向求解：implied-growth 反解隐含增速、growth 反解隐含到达概率，
#      本通道反解"现价隐含控股折价" implied = 1 − 市值/equity NAV——
#      市场按多大折价交易，是观察/拒绝档位带的分界输入。
#   5. 持仓主导 ⇒ 档位上限"小仓位试探"（与成长通道终值纪律同源）：
#      价值主体是资产变现而非经营复利，折价收敛不可控——无收敛机制的
#      折价不是便宜（价值陷阱闸门 S8 的 SOTP 形态）。
#
# 基率带事实源：软银档为仓库内实证（9984.T 底稿 sotp_holdings.nav_discount，
# 市场长期按 equity NAV 折价 30-50% 交易）；其余分档为带注估计，REQ-P1-05
# 基率表建成统一校准后由 --discount-bands-file 接管（与 growth 的
# --base-rates-file 同一模式）。
HOLDING_DISCOUNT_BANDS = {
    "asian_conglomerate_no_convergence": {
        "label": "亚洲多元化控股：关键人集权/关联交易/无回购至NAV承诺",
        "range": [0.30, 0.50],
        "evidence": "软银集团 2016-2019 长期 NAV 折价 30-50% "
                    "[E:backtest/9984.T_2019-06-30 data/business_drivers sotp_holdings.nav_discount]",
    },
    "listed_stake_no_convergence": {
        "label": "上市持仓主导控股：持仓流动性好但无收敛机制（无回购/分拆承诺）",
        "range": [0.20, 0.40],
        "evidence": "带内估计——上市持仓折价浅于私募主导，但无收敛机制则折价常驻；"
                    "标定待 REQ-P1-05 基率表",
    },
    "convergence_mechanism": {
        "label": "有收敛机制：持续回购注销/分拆兑现中（NAV 折价有压缩路径）",
        "range": [0.05, 0.20],
        "evidence": "带内估计——收敛机制存在但兑现有不确定性；标定待 REQ-P1-05 基率表",
    },
    "operating_with_portfolio": {
        "label": "经营主导+投资副轮：投资组合占市值 <25%，估值主体是经营业务",
        "range": [0.00, 0.15],
        "evidence": "经营主导型通常仅对组合施加变现折价（腾讯案口径：上市9折/"
                    "非上市6折），控股折价小带",
    },
}
# 持仓估值方法白名单（方法树纪律：估值方法必须显式登记且可审计）
SOTP_VALUATION_METHODS = {
    "market_price": "上市市价 × 持股比例（回放日收盘）",
    "fair_value_disclosed": "官方披露公允价值（私募持仓按财报 FV）",
    "private_estimate": "自估/第三方估值（无市价无披露 FV）",
    "book_value": "账面价值（保守兜底）",
    "dcf_segment": "分部 DCF（经营性子公司按盈利能力估）",
}
# 流动性分级（变现折价的语义锚：haircut 数值须与流动性分级一致，如
# listed_major 配 0.90-1.0 而 private_co 配 0.3-0.6——不一致时人工复核）
SOTP_LIQUIDITY_CLASSES = {
    "listed_major": "大市值上市持仓（大宗可吸收）",
    "listed_stake": "上市但大额减持有冲击（折价前的持股比例已高）",
    "private_fund": "基金份额/私募组合（估值依赖第三方）",
    "private_co": "非上市股权",
    "illiquid": "其他低流动资产",
}
SOTP_VERDICT_CAP = "小仓位试探"     # 持仓主导形态 ⇒ 通道档位上限（结构纪律）


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
    if required_cagr is None:
        meta["note"] = "缺成熟期收入口径（--mature-revenue），无法反推所需收入 CAGR"
        return None, meta
    if required_cagr <= 0:
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


def sotp_channel_value(items, net_debt, holding_discount, operating_value=0.0):
    """持仓型控股 SOTP（REQ-P1-02）。

    items: [{"name","gross_value","liquidity_haircut","stake_pct",
             "valuation_method","liquidity","evidence","note"}]
      gross_value 语义：**按持股比例折算后的归属毛值**（上市持仓=市价×持股；
      非上市=披露 FV 或账面）。stake_pct 仅作披露、不参与计算——防双重折算。

    三段式：Σ(毛值×变现折价) + 经营业务价值 − 母公司净债 = equity NAV，
    再 × (1−控股折价) = 可投资价值。两层折价分工见模块注释。
    返回 dict（含逐项明细、equity NAV、可投资价值与持仓占比披露）。
    护栏与 growth 通道同源：结构化字段不全即 SystemExit（通道拒绝服务）。
    """
    if not items:
        raise SystemExit(
            "错误：持仓表为空——持仓型控股的估值主体就是持仓组合，"
            "空表意味着『我买的是什么』无答案。先按 references/company-types.md "
            "卡四建结构化持仓表（标的/持股比例/估值方法/折价率/流动性/证据），"
            "再进本通道（SOTP_HOLDINGS_TABLE_INVALID）。")
    gross_total, net_total, detail = 0.0, 0.0, []
    for it in items:
        name = it.get("name", "?")
        missing = [k for k in ("name", "gross_value", "valuation_method",
                               "liquidity", "liquidity_haircut", "evidence")
                   if it.get(k) in (None, "")]
        if missing:
            raise SystemExit(
                f"错误：持仓条目『{name}』缺结构化字段：{'、'.join(missing)}。"
                "持仓表六要素（标的/归属毛值/估值方法/流动性/变现折价率/[E:] 证据）"
                "不全即通道拒绝服务——这是把『--add-back 一个总数』的补丁形态"
                "挡在入口（SOTP_HOLDINGS_TABLE_INVALID，exit 2）。")
        gv, hc = float(it["gross_value"]), float(it["liquidity_haircut"])
        vm, lq = it["valuation_method"], it["liquidity"]
        if gv < 0:
            raise SystemExit(f"错误：持仓『{name}』归属毛值 {gv} 为负——负债请登到净债，"
                             "持仓表只放资产。")
        if not (0.0 < hc <= 1.0):
            raise SystemExit(
                f"错误：持仓『{name}』变现折价率 {hc} 越界（应在 (0,1]，小数）。"
                "0 意味着资产一文不值（那就别列）；>1 是溢价，SOTP 保守口径不接受。")
        if vm not in SOTP_VALUATION_METHODS:
            raise SystemExit(
                f"错误：持仓『{name}』估值方法 {vm!r} 不在白名单 "
                f"{sorted(SOTP_VALUATION_METHODS)}——估值方法必须显式登记且可审计"
                "（方法树纪律），自由文本会让『这个数怎么来的』无法复核。")
        if lq not in SOTP_LIQUIDITY_CLASSES:
            raise SystemExit(
                f"错误：持仓『{name}』流动性分级 {lq!r} 不在白名单 "
                f"{sorted(SOTP_LIQUIDITY_CLASSES)}——变现折价率的语义锚，"
                "listed_major 配 0.5 这类不一致会在分级缺失时无法复核。")
        if "[E:" not in (it.get("evidence") or ""):
            raise SystemExit(
                f"错误：持仓『{name}』缺 [E:] 证据指针——持仓毛值是 SOTP 的"
                f"第一输入，裸数字禁止（与 S7 概率纪律同源）。"
                "违反项告警码：SOTP_HOLDINGS_TABLE_INVALID。")
        adj = gv * hc
        gross_total += gv
        net_total += adj
        detail.append({
            "name": name, "stake_pct": it.get("stake_pct"),
            "valuation_method": vm, "liquidity": lq,
            "gross_value": gv, "liquidity_haircut": hc,
            "adjusted_value": adj,
            "evidence": it["evidence"],
            "note": it.get("note"),
        })
    if net_debt is None:
        raise SystemExit("错误：--net-debt 必填——持仓型公司的净值口径里净债是"
                         "显式第三段，缺它等于『只数资产不数债』。净现金传负数。")
    if not (0.0 <= holding_discount < 1.0):
        raise SystemExit(
            f"错误：控股折价 {holding_discount} 应在 [0,1)（小数）。"
            "≥1 是『NAV 全部归零』无意义；负数是控股溢价——溢价形态的市场定价"
            "（如伯克希尔）不走本通道，走标准 OE 管道并在报告披露。")
    equity_nav = net_total + operating_value - net_debt
    if equity_nav <= 0:
        raise SystemExit(
            f"错误：equity NAV = {equity_nav:,.0f} ≤ 0（持仓折后 {net_total:,.0f} + "
            f"经营 {operating_value:,.0f} − 净债 {net_debt:,.0f}）。"
            "资不抵债口径下『控股折价』失去定义——请核查净债是否误用合并口径"
            "（SOTP_NET_DEBT_CONSOLIDATION_BASIS），或持仓毛值/折价率是否量纲错位。")
    investable = equity_nav * (1.0 - holding_discount)
    holdings_share = (net_total / equity_nav) if equity_nav > 0 else None
    return {
        "holdings_detail": detail,
        "portfolio_gross_value": gross_total,
        "portfolio_net_value": net_total,
        "operating_value": operating_value,
        "net_debt": net_debt,
        "equity_nav": equity_nav,
        "holding_discount": holding_discount,
        "investable_value": investable,
        "holdings_share_of_equity_nav": holdings_share,
        "holdings_share_note": (
            "持仓净价值占 equity NAV 比重：≥50% 即『非经营资产主导』形态"
            "（OBS-2019-06-01 候选判据的通道内实现）——OE 通道结论不进档位裁决，"
            "档位上限锁小仓位试探"),
    }


def expected_return(price, scenarios, hold_years, index_hurdle=0.09,
                    dividend_yield=0.0, discount_rate=0.10,
                    moat=None, iv_growth=None,
                    moat_score=None, moat_score_basis=None, moat_sources=None,
                    prob_derivation=None, dr_derivation=None,
                    tail_derivation=None,
                    floor_hurdle=None, pessimistic_hurdle=0.0,
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

    # ── REQ-P1-04 证据传导块解析（两块均 opt-in，不出现走 legacy 路径）──
    # ① discount_rate_derivation：分层表一致性 + rationale_ref 证据 +
    #    floor 门槛市场校准（P0-04③ 移交项）；
    # ② probability_derivation：得分解析提前到 P1-03 平滑门槛通道之前
    #    （块内得分可兼任得分通道输入，rationale_ref 兼任得分依据）。
    dr_derivation_block = None
    if dr_derivation is not None:
        if not isinstance(dr_derivation, dict):
            raise SystemExit("错误：discount_rate_derivation 须为对象（注册码 "
                             "DR_DERIVATION_UNANCHORED）")
        _tier = dr_derivation.get("industry_tier")
        if _tier is None:
            raise SystemExit("错误：discount_rate_derivation 缺 industry_tier"
                             "（行业档白名单见 INDUSTRY_RISK_PREMIUM，注册码 "
                             "DR_DERIVATION_UNANCHORED）")
        _rate, _comp = stratified_discount_rate(_tier, dr_derivation.get("rf_10y"))
        if abs(_rate - discount_rate) > 1e-9:
            raise SystemExit(
                f"错误：分层折现率 {_rate:.2%}（{_tier} 档）≠ scenarios.json 声明的"
                f" discount_rate {discount_rate:.2%}（注册码 DR_STRATIFIED_RATE_MISMATCH）"
                "——分层表是事实源：premium 档须按分层值重算三情景估值并同步"
                " discount_rate，或修正 industry_tier/rf_10y。禁止声明分层却"
                "沿用旧折现率（V0 与 r 不同源）")
        _dr_rat = dr_derivation.get("rationale_ref") or ""
        if "[E:" not in _dr_rat:
            raise SystemExit("错误：discount_rate_derivation.rationale_ref 必填且"
                             "须含 [E:] 证据指针（注册码 DR_DERIVATION_UNANCHORED）"
                             "——折现率直接决定 IRR 下限与终值，裸参数与裸概率同罪")
        _mkt = dr_derivation.get("market")
        if _mkt is not None and _mkt not in MARKET_FLOOR_HURDLES:
            raise SystemExit(f"错误：market `{_mkt}` 未注册，应为 "
                             f"{sorted(MARKET_FLOOR_HURDLES)}（注册码 "
                             "DR_DERIVATION_UNANCHORED）")
        dr_derivation_block = dict(_comp)
        dr_derivation_block.update({
            "rationale_ref": _dr_rat, "market": _mkt,
            "floor_hurdle_market": (MARKET_FLOOR_HURDLES.get(_mkt)
                                    if _mkt else None),
        })
    # floor 门槛解析顺序：显式传参 > 市场校准（DR 块声明 market 时）> 全局默认。
    # 默认仍是 6%——存量 12 案与 gate2_ab 库内调用（不传 DR 块）完全不受影响。
    if floor_hurdle is None:
        floor_hurdle = ((dr_derivation_block or {}).get("floor_hurdle_market")
                        or DEFAULT_FLOOR_HURDLE)
    if prob_derivation is not None:
        if not isinstance(prob_derivation, dict):
            raise SystemExit("错误：probability_derivation 须为对象（注册码 "
                             "PROB_DERIVATION_INVALID）")
        _pd_rat = prob_derivation.get("rationale_ref") or ""
        if "[E:" not in _pd_rat:
            raise SystemExit("错误：probability_derivation.rationale_ref 必填且"
                             "须含 [E:] 证据指针（注册码 PROB_DERIVATION_INVALID）"
                             "——概率是闸门二唯一不受闸门一污染的输入，传导链"
                             "必须可审计")
        _s_block = prob_derivation.get("moat_score")
        if _s_block is not None:
            if moat_score is not None and abs(float(_s_block) - float(moat_score)) > 1e-9:
                raise SystemExit(
                    f"错误：probability_derivation.moat_score {_s_block} 与顶层/"
                    f"CLI moat_score {moat_score} 不一致（注册码 "
                    "PROB_DERIVATION_INVALID）——禁止两套得分并存")
            if moat_score is None:
                moat_score = float(_s_block)
                if not moat_score_basis:
                    moat_score_basis = _pd_rat   # 块级 rationale 兼任得分依据
        if moat_score is None:
            raise SystemExit("错误：概率映射需要护城河得分——probability_derivation"
                             " 缺 moat_score 且顶层/CLI 均未提供（注册码 "
                             "PROB_DERIVATION_INVALID）")

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

    # ── REQ-P1-05 尾部风险单列（opt-in；无块走 legacy 路径零新增键）──
    # 期望 IRR 的正确公式（需求原文）：
    #   E[IRR] = (1 − p_tail) × Σ pᵢ·IRRᵢ + p_tail × loss_tail
    # 旧口径把"公司归零"挤进悲观情景的增速折扣——既把悲观情景压得不合理地
    # 低，又远远低估真正的归零风险。p_tail 纯映射不可采用偏离（防自欺底线），
    # 闸门二 ①' 与 ④ 相应用尾部口径重判。
    tail_block = None
    if tail_derivation is not None:
        if not isinstance(tail_derivation, dict):
            raise SystemExit("错误：tail_risk_derivation 须为对象（注册码 "
                             "TAIL_DERIVATION_UNANCHORED）")
        _tr_rat = tail_derivation.get("rationale_ref") or ""
        if "[E:" not in _tr_rat:
            raise SystemExit(
                "错误：tail_risk_derivation.rationale_ref 必填且须含 [E:] 证据"
                "指针（注册码 TAIL_DERIVATION_UNANCHORED）——尾部概率直接决定"
                "期望 IRR 的第四项，裸参数与裸概率同罪")
        _loss_tail = tail_derivation.get("loss_tail", -1.0)
        if not (isinstance(_loss_tail, (int, float))
                and -1.0 <= _loss_tail < 0.0):
            raise SystemExit(
                f"错误：loss_tail `{_loss_tail}` 须为 [-1, 0) 的年化 IRR"
                "（-1=股权归零；注册码 TAIL_DERIVATION_UNANCHORED）")
        mapped_tail = map_tail_probability(
            tail_derivation.get("forensic_score"),
            tail_derivation.get("arithmetic_coverage"),
            tail_derivation.get("governance", "normal"))
        p_tail = mapped_tail["p_tail"]
        exp_irr_ex_tail = exp_irr
        loss_prob_ex_tail = loss_prob
        # 尾部态按定义是亏损态（loss_tail < 0），亏损概率同口径并入
        exp_irr = (1.0 - p_tail) * exp_irr + p_tail * _loss_tail
        loss_prob = (1.0 - p_tail) * loss_prob + p_tail
        tail_block = {
            "p_tail": p_tail,
            "loss_tail": _loss_tail,
            "forensic_score": mapped_tail["forensic_score"],
            "arithmetic_coverage": mapped_tail["arithmetic_coverage"],
            "governance": mapped_tail["governance"],
            "rationale_ref": _tr_rat,
            "transmission_chain": mapped_tail["chain"],
            "floors_applied": mapped_tail["floors_applied"],
            "expected_annualized_irr_ex_tail": exp_irr_ex_tail,
            "irr_drag_from_tail": exp_irr_ex_tail - exp_irr,
            "loss_probability_ex_tail": loss_prob_ex_tail,
            "formula": "(1−p_tail)×Σpᵢ·IRRᵢ + p_tail×loss_tail",
            "no_adoption_note": "p_tail 纯映射、不可采用偏离——允许把『公司可能"
                                "归零』论证没等于允许自欺（与红队下界保守不对称"
                                "同理）；治理 good 也打不穿黑天鹅基率地板 1%",
            "mandated_by": "REQ-P1-05（尾部风险单列）",
        }
        tail_drag_disclose = (
            tail_block["irr_drag_from_tail"] >= TAIL_IRR_DRAG_DISCLOSE)
    else:
        tail_drag_disclose = False

    # ---- 闸门二三项（v2.15）----
    # ── REQ-P1-03 护城河得分通道（连续化，2026-09-11）──
    # 得分是门槛的直接输入：先校验（区间 / [E:] 依据 / 词一致性），再把闸门一
    # 反推口径切到平滑函数。仅给词（无得分）时走 legacy 阶跃常数——12 案
    # 基线字节级不变，得分是 opt-in 的新通道。
    if moat_score is not None:
        if not (isinstance(moat_score, (int, float)) and 0 <= moat_score <= 100):
            raise SystemExit(f"错误：--moat-score 须为 0~100 的数值，收到 {moat_score}")
        if not moat_score_basis or "[E:" not in moat_score_basis:
            raise SystemExit(
                "错误：--moat-score-basis 必填且须含 [E:] 证据指针——得分直接决定"
                " MoS 门槛与闸门二①诊断门槛，裸分数与裸概率同罪，必须可审计"
                "（注册码 MOAT_SCORE_BASIS_MISSING）")
        _word_from_score = moat_word_from_score(moat_score)
        if moat is not None and moat != _word_from_score:
            raise SystemExit(
                f"错误：--moat {moat} 与 --moat-score {moat_score} 的分带投影 "
                f"'{_word_from_score}' 不一致（注册码 MOAT_SCORE_WORD_MISMATCH）——"
                "评级词必须等于得分的分带投影（≥65 wide / ≥35 narrow / <35 none），"
                "禁止两套口径并存")
        moat = _word_from_score
    mos_req, irr_hurdle = (moat_irr_hurdle(moat, discount_rate, hold_years,
                                           score=moat_score)
                           if moat else (None, None))
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
    # REQ-P1-05：p_tail 拉低期望 IRR ≥2pct——尾部已实质改变结论，必须披露
    if tail_drag_disclose:
        codes.append("TAIL_DRAG_MATERIALIZES")
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

    # ── REQ-P1-03 护城河得分块 + 边界带双档报告 ──────────────────────
    # 得分路径专属输出：平滑门槛、闸门一结论（MoS 按基准情景对现价）、
    # 边界带检测；落在边界带（边界 ±5 分）时强制输出 ±5 分两侧的双档结论。
    moat_score_block = None
    if moat_score is not None:
        band = moat_boundary_band(moat_score)
        base_v = next((s["value_per_share"] for s in scenarios
                       if s["name"] in ("基准", "base")), None)
        mos_actual = (1.0 - price / base_v) if base_v else None
        gate1_pass = ((mos_actual >= mos_req)
                      if (mos_req is not None and mos_actual is not None) else None)
        if moat == "none":
            gate1_pass = False   # 不给买入结论覆盖闸门一
        dual_rows, dual_report = [], None
        if band["in_band"]:
            for s_side in (moat_score - MOAT_SCORE_BOUNDARY_HALFWIDTH,
                           moat_score + MOAT_SCORE_BOUNDARY_HALFWIDTH):
                s_c = min(100.0, max(0.0, float(s_side)))
                w_s = moat_word_from_score(s_c)
                req_s = mos_requirement_from_score(s_c)
                g1_s = ((mos_actual >= req_s)
                        if (req_s is not None and mos_actual is not None) else None)
                if w_s == "none":
                    g1_s = False
                # 闸门二参与判定四项不随得分变（①护城河反推门槛为诊断项），
                # 唯一例外是 none 档的"不给买入结论"覆盖。
                g2_s = False if w_s == "none" else gate2["pass"]
                trig = (base_v * (1.0 - req_s)
                        if (base_v and req_s is not None) else None)
                if w_s == "none":
                    tier_s = "不给买入结论（烟蒂式清算例外须独立论证）"
                elif g1_s is not True:
                    tier_s = (f"观察等价格（触发价 {trig:,.2f}）" if trig
                              else "观察等价格")
                elif g2_s is not True:
                    tier_s = "观察等价格（闸门二不过，档位上限）"
                else:
                    tier_s = ("买入候选（小仓位试探起；核心买入须裁决层按"
                              "核验强度/股东回报另行加码）")
                dual_rows.append({
                    "score_side": s_c, "word": w_s, "mos_requirement": req_s,
                    "gate1_pass": g1_s, "gate2_pass": g2_s,
                    "trigger_price": trig, "tier_suggestion": tier_s,
                })
            _reqs = [r["mos_requirement"] for r in dual_rows
                     if r["mos_requirement"] is not None]
            dual_report = {
                "trigger": (f"得分 {moat_score} 落在 {band['edge_name']} 边界带 "
                            f"{band['band']}（边界 {band['edge']} ±"
                            f"{MOAT_SCORE_BOUNDARY_HALFWIDTH:.0f} 分）"),
                "rows": dual_rows,
                "sensitivity_note": (
                    "结论对护城河判断敏感：±5 分的评分分歧即可把闸门一门槛在 "
                    f"{min(_reqs):.1%}~{max(_reqs):.1%} 之间移动——两个同样认真的"
                    "分析师可能给出不同档位。双档并列披露优于强行定档；本案"
                    "最该花研究精力的判断就是护城河得分"),
                "mandated_by": "REQ-P1-03（moat-framework 第二节半边界带纪律）",
            }
            codes.append("MOAT_BOUNDARY_BAND_DUAL")
        moat_score_block = {
            "score": moat_score,
            "word": moat,
            "sources": moat_sources,
            "sources_count": (len(moat_sources) if moat_sources else None),
            "score_basis": moat_score_basis,
            "mos_requirement": mos_req,
            "mos_requirement_function": (
                "平滑分段线性（35→50%，65→40%，100→25%），带内 ≥ legacy 常数，"
                "分带边界连续（REQ-P1-03）"),
            "legacy_requirement": MOAT_MOS_REQUIREMENT.get(moat),
            "gate2_diagnostic_hurdle": irr_hurdle,
            "gate1_margin_of_safety": mos_actual,
            "gate1_pass": gate1_pass,
            "gate1_trigger_price": (base_v * (1.0 - mos_req)
                                    if (base_v and mos_req is not None) else None),
            "boundary_band": band,
            "dual_report": dual_report,
        }

    # ── REQ-P1-04 概率传导校验 + ±10pp 敏感性表 ──────────────────────
    # 映射输出是"证据起点的建议值"，不是铁律：分析师可在可调范围内偏离
    # （这正是需求原文"映射公式与可调范围"的语义），但偏离须逐项论证——
    # ≤2pp 免论、2~10pp 须 deviation_rationale 挂 [E:]、>10pp 硬拒。
    prob_derivation_block = None
    if prob_derivation is not None:
        mapped = map_scenario_probabilities(
            moat_score,
            prob_derivation.get("variant_perception", "neutral"),
            prob_derivation.get("red_team_pessimistic"))
        if mapped is None:
            raise SystemExit(
                f"错误：护城河得分 {moat_score} < {MOAT_SCORE_NARROW_MIN}——无买入"
                "结论，概率传导无意义（注册码 PROB_DERIVATION_INVALID）")
        adopted = {s["name"]: s["probability"] for s in scenarios}
        if set(adopted) != {"悲观", "基准", "乐观"}:
            raise SystemExit(
                f"错误：概率映射定义于标准三情景名（悲观/基准/乐观），收到 "
                f"{sorted(adopted)}（注册码 PROB_DERIVATION_INVALID）")
        deviations = {k: round(adopted[k] - mapped["probabilities"][k], 6)
                      for k in ("悲观", "基准", "乐观")}
        # 红队下界直接约束采用值：红队悲观概率是"最强空头世界观成立的概率"，
        # 用分析师的乐观去论证它更低等于架空红队（保守不对称）——映射值之下的
        # 偏离可论证，红队下界之下的偏离不可。
        _rt_p = prob_derivation.get("red_team_pessimistic")
        if _rt_p is not None and adopted["悲观"] < float(_rt_p) - 1e-9:
            raise SystemExit(
                f"错误：采用悲观概率 {adopted['悲观']:.2%} 低于红队悲观概率下界 "
                f"{float(_rt_p):.2%}（注册码 PROB_DERIVATION_MISMATCH）——红队"
                "下界不可被论证突破：把空头世界观成立的概率论证得更低，用的正是"
                "红队要质询的那份乐观。应上调悲观权重或下调红队概率估计并挂 [E:]")
        for _k, _dev in deviations.items():
            if abs(_dev) > PROB_DEVIATION_MAX + 1e-9:
                raise SystemExit(
                    f"错误：情景 `{_k}` 采用概率 {adopted[_k]:.2%} 偏离映射值 "
                    f"{mapped['probabilities'][_k]:.2%} 达 {_dev:+.1%}，超出可调"
                    f"范围 ±{PROB_DEVIATION_MAX:.0%}（注册码 PROB_DERIVATION_OUT_"
                    "OF_RANGE）——如此大的偏离意味着映射输入（得分/变异认知/红队）"
                    "与最终概率已不是一个世界观，应修输入而不是绕映射")
            if abs(_dev) > PROB_DEVIATION_FREE + 1e-9:
                _devr = prob_derivation.get("deviation_rationale") or ""
                if "[E:" not in _devr:
                    raise SystemExit(
                        f"错误：情景 `{_k}` 采用概率偏离映射 {_dev:+.1%}"
                        f"（>{PROB_DEVIATION_FREE:.0%}），但 deviation_rationale "
                        "未挂 [E:] 证据指针（注册码 PROB_DERIVATION_MISMATCH）——"
                        "偏离映射须逐项论证，与 S7 偏离默认须证据同构")

        # ±10pp 敏感性（验收条款）：悲观权重 ±10pp 对期望 IRR / 闸门二 / 档位
        # 的影响。IRR_i 不随概率变（只变权重），故直接对 rows 重加权。
        _by_name = {s["name"]: s for s in rows}
        base_v_p = next((s["value_per_share"] for s in scenarios
                         if s["name"] in ("基准", "base")), None)
        mos_actual_p = (1.0 - price / base_v_p) if base_v_p else None
        gate1_pass_p = ((mos_actual_p >= mos_req)
                        if (mos_req is not None and mos_actual_p is not None)
                        else None)
        if moat == "none":
            gate1_pass_p = False
        sens_rows, tiers = [], []
        for _shift in (-PROB_SENSITIVITY_SHIFT, 0.0, PROB_SENSITIVITY_SHIFT):
            _pp = adopted["悲观"] + _shift
            _po = adopted["乐观"]
            _pb = 1.0 - _pp - _po
            if _pb < 0.0:
                sens_rows.append({"shift": _shift, "valid": False,
                                  "note": "悲观+10pp 后基准概率为负，不可行"})
                tiers.append(None)
                continue
            _irr_s = (_pp * _by_name["悲观"]["annualized_irr"]
                      + _pb * _by_name["基准"]["annualized_irr"]
                      + _po * _by_name["乐观"]["annualized_irr"])
            _loss_s = sum(p for nm, p in (("悲观", _pp), ("基准", _pb),
                                          ("乐观", _po))
                          if _by_name[nm]["total_return"] < 0)
            # REQ-P1-05：尾部块在场时敏感性同口径并入（隔离概率通道、
            # 尾部恒定——摆动的是悲观权重，不是尾部世界观）
            if tail_block is not None:
                _irr_s = (1.0 - p_tail) * _irr_s + p_tail * _loss_tail
                _loss_s = (1.0 - p_tail) * _loss_s + p_tail
            _g2_checks = {
                "expected_irr_floor": _irr_s >= discount_rate,
                "no_convergence_floor": gate2["no_convergence_floor"]["pass"],
                "pessimistic_irr": gate2["pessimistic_irr"]["pass"],
                "loss_probability": _loss_s <= loss_prob_hurdle,
            }
            _g2_pass = all(v is True for v in _g2_checks.values())
            if moat == "none":
                _tier = "不给买入结论"
            elif gate1_pass_p is not True:
                _tier = "观察等价格"
            elif _g2_pass is not True:
                _tier = "观察等价格"
            else:
                _tier = "买入候选（小仓位试探起）"
            tiers.append(_tier)
            sens_rows.append({
                "shift": _shift,
                "valid": True,
                "probabilities": {"悲观": _pp, "基准": _pb, "乐观": _po},
                "expected_annualized_irr": _irr_s,
                "loss_probability": _loss_s,
                "gate2_pass": _g2_pass,
                "gate2_checks": _g2_checks,
                "tier_suggestion": _tier,
            })
        _tiers_valid = [t for t in tiers if t]
        tier_flip = (len(set(_tiers_valid)) > 1)
        prob_derivation_block = {
            "moat_score": float(moat_score),
            "variant_perception": prob_derivation.get(
                "variant_perception", "neutral"),
            "red_team_pessimistic": prob_derivation.get(
                "red_team_pessimistic"),
            "rationale_ref": prob_derivation.get("rationale_ref"),
            "deviation_rationale": prob_derivation.get("deviation_rationale"),
            "mapped_probabilities": mapped["probabilities"],
            "adopted_probabilities": adopted,
            "deviations": deviations,
            "transmission_chain": mapped["chain"],
            "anchors": mapped["anchors"],
            "adjustable_ranges": mapped["ranges"],
        }
        if tier_flip:
            codes.append("PROB_SENSITIVITY_TIER_FLIP")

    unknown = unknown_codes(codes)
    if unknown:
        raise KeyError(f"未注册的告警码 {unknown}，请先在 scripts/alert_codes.py 登记")
    gate2["codes"] = codes

    result = {
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
    # REQ-P1-03：得分路径才挂 moat_score 块——legacy 词路径输出字节级不变
    if moat_score_block is not None:
        result["moat_score"] = moat_score_block
    # REQ-P1-04：证据传导块 opt-in（rationale_ref 验收条款的机器载体）+
    # ±10pp 概率敏感性表（验收条款）——legacy 路径不出现这些键
    if dr_derivation_block is not None:
        result["discount_rate_derivation"] = dr_derivation_block
    if prob_derivation_block is not None:
        prob_derivation_block["sensitivity_pm10pp"] = {
            "rows": sens_rows,
            "tier_flip": tier_flip,
            "tier_flip_code": "PROB_SENSITIVITY_TIER_FLIP" if tier_flip else None,
            "note": ("悲观权重 ±10pp 即可移动档位——结论对概率假设敏感，"
                     "本案最该花研究精力的判断就是三情景概率"
                     if tier_flip else
                     "悲观权重 ±10pp 档位不动——结论对概率摆动稳健"),
            "mandated_by": "REQ-P1-04 验收条款（概率 ±10pp 对档位的影响）",
        }
        result["probability_derivation"] = prob_derivation_block
    # REQ-P1-05：尾部风险块 opt-in——legacy 路径不出现这些键
    if tail_block is not None:
        result["tail_risk"] = tail_block
        result["expected_annualized_irr_ex_tail"] = \
            tail_block["expected_annualized_irr_ex_tail"]
        result["loss_probability_ex_tail"] = \
            tail_block["loss_probability_ex_tail"]
    return result


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
    p3.add_argument("--moat-score", type=float,
                    help="护城河定量得分 0~100（REQ-P1-03，moat-framework 第二节半"
                         "三组件评分）。提供时 MoS 门槛走平滑函数（35→50%/65→40%/"
                         "100→25%）而非阶跃常数，评级词必须等于得分的分带投影"
                         "（≥65 wide / ≥35 narrow / <35 none）；落在边界带（边界 ±5 分）"
                         "自动输出双档报告并标注敏感性")
    p3.add_argument("--moat-score-basis",
                    help="得分推导依据（给了 --moat-score 即必填，须含 [E:] 指针）——"
                         "三组件（超额回报证据/源硬度/定标与趋势修正）各自的证据来源，"
                         "裸分数禁止（得分直接决定门槛）")
    p3.add_argument("--moat-sources",
                    help="有硬证据的护城河源列表（逗号分隔，如 '成本优势,有效规模'）——"
                         "五源支持数，信息项随得分一并落盘")
    p3.add_argument("--iv-growth", type=float,
                    help="基准情景下每股内在价值的长期增速（小数）。与股息率相加得"
                         "『价值不收敛下限』——折价永不收敛时的实际年化回报。"
                         "这是闸门二真正独立于 V0 的一项，强烈建议必填")
    p3.add_argument("--floor-hurdle", type=float, default=None,
                    help=f"不收敛下限的门槛（默认 {DEFAULT_FLOOR_HURDLE:.0%}，"
                         f"约当长期国债 + 2~3pct；discount_rate_derivation 声明"
                         f" market 时自动切市场校准值 {MARKET_FLOOR_HURDLES}，"
                         f"显式传参优先）。含义：即使市场永不重估，也要跑赢"
                         f"低风险替代")
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
                         "与 --mature-revenue + --mature-oe-margin 二选一。注意：走此直传路径"
                         "而缺 --mature-revenue 时基率锚不可用（GROWTH_ANCHOR_UNAVAILABLE），"
                         "到达概率须脱离锚独立论证；建议尽量给三段式收入口径以启用锚")
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
                    help="当期收入（与成熟态同币种；与 --mature-revenue 联合反推所需 CAGR "
                         "并查基率锚——到达概率与基率表挂钩的机器强制项。注意锚的完整"
                         "前提是同时提供 --mature-revenue，否则触发 "
                         "GROWTH_ANCHOR_UNAVAILABLE 而非静默无锚）")
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

    # ── REQ-P1-02：持仓型控股 SOTP 通道 ──
    p5 = sub.add_parser(
        "sotp", help="持仓型控股 SOTP：持仓组合 + 经营业务 − 母公司净债，控股折价显式参数")
    p5.add_argument("--holdings-file", required=True,
                    help="结构化持仓表 JSON（交付物 schema）：{items:[{name, stake_pct, "
                         "valuation_method, gross_value, liquidity, liquidity_haircut, "
                         "evidence}]}. gross_value=按持股比例折算后的归属毛值；"
                         "六要素不全即通道拒绝服务（exit 2）")
    p5.add_argument("--net-debt", type=float, required=True,
                    help="母公司净债（与持仓毛值同币种同单位；净现金传负数）。"
                         "必须是本体口径——见 --net-debt-basis")
    p5.add_argument("--net-debt-basis", required=True,
                    choices=["parent_standalone", "consolidated"],
                    help="净债口径声明。持仓按持股比例计价而净债用合并口径会双重"
                         "计入少数股东应担债务（软银 2019 案 alternative_treatment "
                         "教训）；consolidated 触发 SOTP_NET_DEBT_CONSOLIDATION_BASIS 告警")
    p5.add_argument("--holding-discount", type=float, required=True,
                    help="控股折价（[0,1) 小数）：市场对『钱在别人手里』的定价——"
                         "治理摩擦/资本配置不可验证/无收敛机制。与持仓级变现折价"
                         "分工：变现折价管『这笔资产卖得回几成』，控股折价管"
                         "『整体该再打几折』")
    p5.add_argument("--holding-discount-basis", required=True,
                    help="控股折价依据（必填，须含 [E:] 指针并引用基率带）：折价率是"
                         "SOTP 结论的最大摆动因子，裸折价禁止（valuation-guide "
                         "多元集团纪律）。低于基率带下界 = 比历史实证更乐观")
    p5.add_argument("--holding-profile", required=True,
                    choices=sorted(HOLDING_DISCOUNT_BANDS),
                    help="控股形态分档（基率带索引）：折价与带对照，低于下界触发"
                         "SOTP_DISCOUNT_BELOW_BASE_RATE")
    p5.add_argument("--discount-bands-file",
                    help="基率带文件（REQ-P1-05 的 references/base-rates.md 建成后接管，"
                         "JSON dict 格式同 HOLDING_DISCOUNT_BANDS）。缺省用引擎内置带")
    p5.add_argument("--operating-value", type=float, default=0.0,
                    help="经营业务价值（经营主导+投资副轮形态如腾讯：forward-value 的"
                         "经营 DCF 总额；纯控股省略=0——价值主体就是持仓）")
    p5.add_argument("--operating-value-basis",
                    help="经营业务价值依据（--operating-value >0 时必填且须含 [E:]）——"
                         "该值的推导须可审计（正常化基期/增速/终值口径）")
    p5.add_argument("--market-cap", type=float, required=True,
                    help="当前市值（股权口径，与持仓毛值同币种同单位）——用于反解"
                         "『现价隐含控股折价』，即本通道的反向 DCF")
    p5.add_argument("--shares", type=float,
                    help="摊薄股本（百万股），提供则输出每股口径")
    p5.add_argument("--fx", type=float, default=1.0,
                    help="每股价值的币种换算系数（报告币→行情币），如 CNY→HKD 用 1.087")
    p5.add_argument("-o", "--output", help="输出 JSON 路径")

    args = ap.parse_args()

    if args.mode == "expected-return":
        price, hold_years = args.price, args.hold_years
        dividend_yield, discount_rate = args.dividend_yield, args.discount_rate
        moat, iv_growth = args.moat, args.iv_growth
        moat_score = args.moat_score
        moat_score_basis, moat_sources = args.moat_score_basis, None
        prob_derivation, dr_derivation = None, None   # REQ-P1-04 证据传导块
        tail_derivation = None                        # REQ-P1-05 尾部风险块
        if args.moat_sources:
            moat_sources = [s.strip() for s in args.moat_sources.split(",")
                            if s.strip()]
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
            # REQ-P1-03：得分三字段同样以 scenarios.json 为单一事实源
            if sd.get("moat_score") is not None:
                moat_score = float(sd["moat_score"])
            moat_score_basis = sd.get("moat_score_basis", moat_score_basis)
            if sd.get("moat_sources") is not None:
                moat_sources = list(sd["moat_sources"])
            # REQ-P1-04：证据传导两块同样以 scenarios.json 为单一事实源
            prob_derivation = sd.get("probability_derivation")
            dr_derivation = sd.get("discount_rate_derivation")
            # REQ-P1-05：尾部风险块同源（forensic_score/arithmetic_coverage
            # 来自 forensic_screen.py 输出；p_tail 纯映射，无 CLI 手抄通道）
            tail_derivation = sd.get("tail_risk_derivation")
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
                              moat_score=moat_score,
                              moat_score_basis=moat_score_basis,
                              moat_sources=moat_sources,
                              prob_derivation=prob_derivation,
                              dr_derivation=dr_derivation,
                              tail_derivation=tail_derivation,
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
        # ---- REQ-P1-05 尾部风险单列 ----
        tr = res.get("tail_risk")
        if tr:
            print(f"\n--- 尾部风险单列（REQ-P1-05）---")
            print(f"p_tail              : {tr['p_tail']:.1%}"
                  f"（排雷得分 {tr['forensic_score']:g}、算术覆盖率 "
                  f"{tr['arithmetic_coverage']:.0%}、治理 {tr['governance']}）")
            print(f"尾部态年化 IRR      : {tr['loss_tail']:.1%}"
                  f"（公式 (1−p_tail)×Σpᵢ·IRRᵢ + p_tail×loss_tail）")
            print(f"期望 IRR（含尾部）  : {res['expected_annualized_irr']:.2%}"
                  f"  ← 闸门二判定口径")
            print(f"期望 IRR（剔尾部）  : "
                  f"{tr['expected_annualized_irr_ex_tail']:.2%}"
                  f"（尾部拖累 {tr['irr_drag_from_tail']:.2%}）")
            print(f"亏损概率（剔尾部）  : {tr['loss_probability_ex_tail']:.0%}"
                  f" → 含尾部 {res['loss_probability']:.0%}")
            for step, vals in tr["transmission_chain"]:
                _pv = vals.get("p_tail")
                print(f"  · {step} → p_tail {_pv:.1%}" if _pv is not None
                      else f"  · {step}")
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

        # ---- REQ-P1-03 护城河得分与边界带双档报告 ----
        ms = res.get("moat_score")
        if ms:
            print(f"\n=== 护城河定量得分（REQ-P1-03）===")
            src_txt = (f"，五源支持 {ms['sources_count']} 源（{'+'.join(ms['sources'])}）"
                       if ms.get("sources") else "")
            print(f"  得分 {ms['score']:.0f}/100 → 评级 {ms['word']}{src_txt}")
            req_txt = (f"{ms['mos_requirement']:.1%}" if ms["mos_requirement"] is not None
                       else "不给买入结论（<35 分）")
            leg_txt = (f"（legacy 阶跃值 {ms['legacy_requirement']:.0%}）"
                       if ms["legacy_requirement"] is not None else "")
            print(f"  平滑 MoS 门槛: {req_txt}{leg_txt}")
            if ms["gate1_margin_of_safety"] is not None:
                g1 = ms["gate1_pass"]
                g1_mark = "✓" if g1 else "✗"
                print(f"  闸门一: {g1_mark} MoS {ms['gate1_margin_of_safety']:.1%} vs 门槛 "
                      f"{req_txt}"
                      + (f"，触发价 {ms['gate1_trigger_price']:,.2f}"
                         if ms.get("gate1_trigger_price") else ""))
            dr = ms.get("dual_report")
            if dr:
                print(f"  ⚠ 边界带: {dr['trigger']}")
                for r_ in dr["rows"]:
                    w_cn = {"wide": "宽", "narrow": "窄", "none": "无"}.get(r_["word"], r_["word"])
                    req_r = (f"{r_['mos_requirement']:.1%}" if r_["mos_requirement"] is not None
                             else "不给买入结论")
                    g1r = "✓" if r_["gate1_pass"] else "✗"
                    g2r = "✓" if r_["gate2_pass"] else "✗"
                print(f"    - 得分 {r_['score_side']:.0f}（{w_cn}）: 闸门一 {g1r}"
                      f"（门槛 {req_r}） 闸门二 {g2r} → {r_['tier_suggestion']}")
                print(f"  敏感性: {dr['sensitivity_note']}")

        # ---- REQ-P1-04 折现率分层 + 概率传导 + ±10pp 敏感性 ----
        drd = res.get("discount_rate_derivation")
        if drd:
            print(f"\n=== 折现率分层（REQ-P1-04）===")
            print(f"  r = {drd['rate']:.2%} = {drd['formula']}"
                  f"（{drd['industry_tier']} 档 +{drd['industry_premium']:.0%}，"
                  f"下限 {drd['floor']:.1%}）")
            if drd.get("floor_hurdle_market") is not None:
                print(f"  不收敛下限门槛按市场校准: {drd['floor_hurdle_market']:.0%}"
                      f"（market={drd['market']}，P0-04③ 口径噪声修复）")
            print(f"  证据: {drd['rationale_ref'][:80]}")
        pdv = res.get("probability_derivation")
        if pdv:
            print(f"\n=== 概率证据传导（REQ-P1-04）===")
            for step in pdv["transmission_chain"]:
                print(f"  {'→ '.join(f'{k} {v:.1%}' if isinstance(v, float) else f'{k} {v}' for k, v in step[1].items())}"
                      f"    [{step[0]}]")
            mp, ap = pdv["mapped_probabilities"], pdv["adopted_probabilities"]
            print(f"  映射值: 悲观 {mp['悲观']:.1%} / 基准 {mp['基准']:.1%} /"
                  f" 乐观 {mp['乐观']:.1%}")
            print(f"  采用值: 悲观 {ap['悲观']:.1%} / 基准 {ap['基准']:.1%} /"
                  f" 乐观 {ap['乐观']:.1%}（偏离 "
                  + " ".join(f"{k}{v:+.1%}" for k, v in pdv["deviations"].items())
                  + "）")
            sens = pdv["sensitivity_pm10pp"]
            print(f"  ±10pp 敏感性（悲观权重摆动）:")
            for r_ in sens["rows"]:
                if not r_.get("valid"):
                    print(f"    {r_['shift']:+.0%}: {r_['note']}")
                    continue
                g2m = "✓" if r_["gate2_pass"] else "✗"
                print(f"    悲观 {r_['probabilities']['悲观']:.0%}: 期望 IRR "
                      f"{r_['expected_annualized_irr']:.2%}，亏损概率 "
                      f"{r_['loss_probability']:.0%}，闸门二 {g2m}"
                      f" → {r_['tier_suggestion']}")
            flip = "⚠ 档位翻转——结论对概率假设敏感" if sens["tier_flip"] \
                else "档位稳定（对概率摆动稳健）"
            print(f"  {flip}")
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
        # 所需 CAGR 与基率表同口径（收入比值）。直接给 --mature-oe 而无
        # --mature-revenue 时收入口径缺失、锚不可用——显式告警（GROWTH_ANCHOR_
        # UNAVAILABLE）而非静默放行。旧实现用 mature_oe/current_revenue 当代理
        # 是量纲错误：利润÷收入不是增长倍数，典型输入下 ≤0 → 锚静默关闭，
        # 且其注释声称"偏紧——方向保守"与实际效果（偏松/关闭）恰好相反。
        if args.mature_revenue and args.current_revenue > 0:
            required_cagr = (args.mature_revenue / args.current_revenue) ** (
                1.0 / args.years_to_maturity) - 1.0
            cagr_basis = "revenue"
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
        if cagr_basis == "unavailable":
            codes.append("GROWTH_ANCHOR_UNAVAILABLE")
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
            _cagr_label = {"revenue": "收入 CAGR"}.get(cagr_basis, "CAGR")
            print(f"\n基率锚       : 所需{_cagr_label} {required_cagr:.1%}（规模分档 "
                  f"{anchor_meta.get('scale_band', '?')}）→ 历史达成比例上界 {anchor:.0%}")
            if args.arrival_prob > anchor:
                print(f"⚠ 到达概率 {args.arrival_prob:.0%} 高于基率锚 {anchor:.0%}"
                      f"（GROWTH_ARRIVAL_PROB_ABOVE_BASERATE）：到达=增长+利润率扩张+"
                      f"竞争存活的联合概率，超过『仅增长兑现』的历史比例须在 basis 中论证")
        else:
            print(f"\n基率锚       : 无（{anchor_meta.get('note', '未提供')}）")
            if cagr_basis == "unavailable":
                print(f"⚠ 基率锚不可用（GROWTH_ANCHOR_UNAVAILABLE）：缺 --mature-revenue，"
                      f"无法反推与基率表同口径的所需收入 CAGR。"
                      f"GROWTH_ARRIVAL_PROB_ABOVE_BASERATE / GROWTH_IMPLIED_VS_BASERATE_GAP"
                      f" 在此路径下不会触发——到达概率 {args.arrival_prob:.0%} 须在 basis 中"
                      f"脱离基率锚独立论证（无锚须论证）")
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

    if args.mode == "sotp":
        # ---- 持仓表读取与结构化校验（sotp_channel_value 内做六要素硬拒绝）----
        with open(args.holdings_file, "r", encoding="utf-8") as _f:
            htab = json.load(_f)
        items = htab.get("items")
        if items is None:
            # 兼容软银案 business_drivers.sotp_holdings 的旧半结构化形态
            # （holdings 数组无 liquidity/valuation_method 字段）——不猜测、
            # 显式要求转成结构化表（交付物 schema），通道不做静默降级。
            print("错误：持仓表缺 items 数组（结构化持仓表 schema）。旧半结构化持仓数据"
                  "（如 business_drivers.sotp_holdings）须先转成六要素表"
                  "（标的/持股比例/估值方法/流动性/变现折价率/[E:] 证据）"
                  "——转换本身就是 REQ-P1-02 交付物的一部分，通道不代填。")
            print("通道拒绝服务（SOTP_HOLDINGS_TABLE_INVALID，exit 2）。")
            sys.exit(2)
        if args.operating_value > 0 and "[E:" not in (args.operating_value_basis or ""):
            raise SystemExit(
                "错误：--operating-value > 0 时 --operating-value-basis 必填且须含 [E:] "
                "——经营业务价值是三段式的第二大输入，其推导（正常化基期/增速/终值）"
                "必须可审计，与持仓毛值同纪律。")
        if "[E:" not in (args.holding_discount_basis or ""):
            raise SystemExit(
                "错误：--holding-discount-basis 必须含 [E:] 证据指针——控股折价是 SOTP "
                "结论的最大摆动因子（valuation-guide 多元集团纪律：折价率必须给依据并做"
                "±10pct 敏感性），裸折价禁止。违反项告警码："
                "SOTP_HOLDING_DISCOUNT_UNANCHORED（人工登记进 verdict.codes）。")

        # ---- 基率带（控股折价与行业基率挂钩的机器强制项）----
        bands = HOLDING_DISCOUNT_BANDS
        if args.discount_bands_file:
            with open(args.discount_bands_file, "r", encoding="utf-8") as _f:
                bands = json.load(_f)
        if args.holding_profile not in bands:
            raise SystemExit(
                f"错误：--holding-profile {args.holding_profile!r} 不在基率带表内"
                f"（可用：{sorted(bands)}）。REQ-P1-05 基率表接管后由 "
                "--discount-bands-file 提供，避免两处事实源漂移。")
        band = bands[args.holding_profile]
        band_lo, band_hi = band["range"]
        band_check = {
            "profile": args.holding_profile,
            "label": band["label"],
            "range": band["range"],
            "evidence": band["evidence"],
            "discount_in_band": band_lo <= args.holding_discount <= band_hi,
            "hook": "REQ-P1-05：references/base-rates.md 建成后由 "
                    "--discount-bands-file 接管（当前为引擎内置带）",
        }

        # ---- 持仓表校验（六要素/白名单/[E:]，失败=通道拒绝服务 exit 2）----
        try:
            res = sotp_channel_value(items, args.net_debt, args.holding_discount,
                                     operating_value=args.operating_value)
        except SystemExit as _e:
            # 拒绝服务统一 exit 2（与 growth 通道单元经济门同一语义：
            # 通道不为此类输入服务，而非算出一个错数）
            print(str(_e))
            print("通道拒绝服务（SOTP_HOLDINGS_TABLE_INVALID，exit 2）"
                  "——按 valuation-guide 方法树：分部数据不足以支撑分部估值时"
                  "按 Phase 0 规则终止，不降级估算。")
            sys.exit(2)

        # ---- 反向求解：现价隐含控股折价（本通道的反向 DCF）----
        implied_d = 1.0 - args.market_cap / res["equity_nav"] \
            if res["equity_nav"] > 0 else None

        # ---- 告警码组装（全部须先在 alert_codes.py 注册）----
        codes = []
        if args.net_debt_basis == "consolidated":
            codes.append("SOTP_NET_DEBT_CONSOLIDATION_BASIS")
        if not band_check["discount_in_band"] and args.holding_discount < band_lo:
            codes.append("SOTP_DISCOUNT_BELOW_BASE_RATE")
        holdings_dominant = (res["holdings_share_of_equity_nav"] or 0) >= 0.5
        if holdings_dominant:
            codes.append("SOTP_HOLDINGS_DOMINATED")
        if implied_d is not None and abs(implied_d - args.holding_discount) >= 0.10:
            codes.append("SOTP_IMPLIED_DISCOUNT_GAP")
        # 极性与 growth 通道相反：价格越高隐含折价越低。市场零折价/溢价形态
        # （implied_d <= 0，伯克希尔式）是"通道不适用"的信号，非透支也非便宜
        if implied_d is not None and implied_d <= 0:
            codes.append("SOTP_PRICE_IMPLIES_NO_DISCOUNT")
        unknown = unknown_codes(codes)
        if unknown:
            raise KeyError(f"未注册的告警码 {unknown}，请先在 scripts/alert_codes.py 登记")

        # ---- 档位带（引擎建议、裁决层定档——与双闸门哲学一致）----
        vps = res["investable_value"] / args.shares if args.shares else None
        if not holdings_dominant:
            band_suggestion = "标准双闸门裁定"
            band_reason = ("经营业务占 equity NAV 主导：本通道只做『经营性价值与投资"
                           "组合分列』（防 add-back 补丁化），档位由标准双闸门与"
                           "裁决层确定，通道不加额外上限")
            cap = None
        elif res["investable_value"] < args.market_cap:
            band_suggestion = "拒绝（透支）"
            band_reason = (f"可投资价值 {res['investable_value']:,.0f} < 市值 "
                           f"{args.market_cap:,.0f}：现价高于折后 NAV——价格已计满"
                           "持仓价值且未计控股折价")
            cap = SOTP_VERDICT_CAP
        elif args.holding_profile == "convergence_mechanism":
            band_suggestion = "小仓位试探候选"
            band_reason = ("价格 ≤ 折后 NAV 且存在收敛机制（回购注销/分拆兑现中）——"
                           "折价有压缩路径，但仍受通道档位上限约束，且必须过 "
                           "expected-return 闸门二（不收敛下限是这类公司的核心闸）")
            cap = SOTP_VERDICT_CAP
        else:
            band_suggestion = "观察等价格"
            band_reason = ("价格 ≤ 折后 NAV，但无收敛机制的折价不是便宜——折价可以"
                           "永不收敛（价值陷阱的 SOTP 形态），『便宜』的兑现押在"
                           "治理行动上而普通股东无法触发。等价格=等更深的折价或"
                           "收敛催化剂出现（回购至NAV/分拆/清算），须披露触发价与"
                           "不收敛下限（股息率+NAV增速）")
            cap = SOTP_VERDICT_CAP

        out = {
            "mode": "sotp",
            "holdings_table": {
                "source": args.holdings_file,
                "as_of": htab.get("as_of"),
                "currency": htab.get("currency"),
                "unit": htab.get("unit"),
                "items_count": len(items),
            },
            "net_debt": {
                "amount": args.net_debt,
                "basis": args.net_debt_basis,
                "note": "母公司本体口径（净现金为负）。合并口径会与持仓按持股计价"
                        "错配——少数股东应担债务被双重计入" if args.net_debt_basis
                        == "consolidated" else None,
            },
            **res,
            "operating_value_basis": args.operating_value_basis,
            "holding_discount_basis": args.holding_discount_basis,
            "base_rate_band": band_check,
            "implied": {
                "market_cap": args.market_cap,
                "implied_holding_discount": implied_d,
                "note": "反解 implied = 1 − 市值/equity NAV：市场按多大控股折价"
                        "交易。与采用折价分歧 ≥10pct 即 SOTP_IMPLIED_DISCOUNT_GAP"
                        "——若市场折价持续，可投资价值≈现价（无安全边际）；"
                        "≤0 即市值不低于 NAV（市场零折价/溢价，伯克希尔式形态）"
                        "→ SOTP_PRICE_IMPLIES_NO_DISCOUNT（通道不适用信号）",
            },
            "value_per_share": vps,
            "value_per_share_quote_ccy": (vps * args.fx) if vps is not None else None,
            "fx": args.fx,
            "sensitivity": {
                "holding_discount_pm10pct": [
                    (res["equity_nav"] * (1.0 - min(0.99, args.holding_discount + 0.10))
                     / args.shares if args.shares else None),
                    (res["equity_nav"] * (1.0 - max(0.0, args.holding_discount - 0.10))
                     / args.shares if args.shares else None),
                ],
                "note": "折价率是 SOTP 结论的最大摆动因子——±10pct 敏感性是"
                        "valuation-guide 多元集团纪律的强制项",
            },
            "verdict_band": {
                "cap": cap,
                "suggestion": band_suggestion,
                "reasons": band_reason,
                "codes": codes,
                "note": "引擎建议档位带，最终档位由双闸门与裁决层确定；持仓主导"
                        "（≥50% equity NAV）形态通道档位上限恒为小仓位试探——"
                        "价值主体是资产变现而非经营复利，与终值纪律同源",
            },
            "codes": codes,
        }
        # ---- 打印 ----
        print(f"持仓表 {args.holdings_file}（{len(items)} 项，as_of "
              f"{htab.get('as_of', '?')}）")
        print(f"\n{'持仓':<26}{'毛值':>14}{'变现折价':>9}{'折后':>14}")
        for d_ in res["holdings_detail"]:
            print(f"{d_['name'][:24]:<26}{d_['gross_value']:>14,.0f}"
                  f"{d_['liquidity_haircut']:>9.2f}{d_['adjusted_value']:>14,.0f}")
        print(f"{'持仓合计':<26}{res['portfolio_gross_value']:>14,.0f}"
              f"{'':>9}{res['portfolio_net_value']:>14,.0f}")
        if args.operating_value:
            print(f"{'经营业务价值':<26}{'':>14}{'':>9}"
                  f"{args.operating_value:>14,.0f}")
        print(f"{'母公司净债':<26}{'':>14}{'':>9}{-args.net_debt:>14,.0f}")
        print(f"{'equity NAV':<26}{'':>14}{'':>9}{res['equity_nav']:>14,.0f}")
        print(f"\n控股折价 {args.holding_discount:.0%} → 可投资价值 "
              f"{res['investable_value']:,.0f}", end="")
        if vps is not None:
            txt = f"（每股 {vps:,.2f}"
            if args.fx != 1.0:
                txt += f" = {vps * args.fx:,.2f} 行情币"
            txt += "）"
            print(txt, end="")
        print()
        print(f"\n基率带 [{band['label']}]: {band_lo:.0%}~{band_hi:.0%}，"
              f"采用 {args.holding_discount:.0%} "
              + ("✓ 带内" if band_check["discount_in_band"] else
                 "⚠ 低于下界——比历史实证更乐观（SOTP_DISCOUNT_BELOW_BASE_RATE）"
                 if args.holding_discount < band_lo else
                 "（高于上界：保守方向，允许但须披露）"))
        hs = res["holdings_share_of_equity_nav"]
        if hs is not None:
            print(f"持仓占 equity NAV : {hs:.0%}"
                  + ("——非经营资产主导（SOTP_HOLDINGS_DOMINATED），"
                     "OE 通道结论不进档位裁决" if holdings_dominant else ""))
        if implied_d is not None:
            print(f"现价隐含控股折价 : {implied_d:.0%}"
                  + (f"（vs 采用 {args.holding_discount:.0%}，分歧 "
                     f"{abs(implied_d - args.holding_discount):.0%}pct ≥10pct——"
                     "SOTP_IMPLIED_DISCOUNT_GAP）"
                     if abs(implied_d - args.holding_discount) >= 0.10 else ""))
            if implied_d <= 0:
                print("🔴 隐含折价 ≤0：市值不低于 equity NAV——市场未计任何"
                      "控股折价甚至给溢价（伯克希尔式形态）。SOTP 口径下无安全"
                      "边际，『等折价收敛』语义对该形态失效——通道不适用信号，"
                      "非便宜（SOTP_PRICE_IMPLIES_NO_DISCOUNT）")
        print(f"\n档位带（上限 {cap or '标准双闸门'}）: {band_suggestion}")
        print(f"  {band_reason}")
        print(f"告警码: {' '.join(codes) if codes else '（无）'}")
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
