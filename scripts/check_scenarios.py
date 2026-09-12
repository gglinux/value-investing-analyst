#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_scenarios.py — 三情景底稿门禁（Phase 4 出口关卡，expected-return 之前强制运行）

定位：`validate_data.py` 守"底稿 ≈ 事实"，`verify_report.py` 守"报告 = 底稿"，
本脚本守**"下行情景 ≠ 基准情景的打折"**——此前这段完全无人看守。

★ 为什么必须有这道闸（起因，必读）★

双闸门的期望值部分在数学上是安全边际的单调函数（买在内在价值上 IRR 恒等于折现率），
因此闸门二**唯一真正独立的信息来自下行指标与情景概率**。但归档案例实测显示，
三情景的生成方式是"同一个 DCF 上把增速旋钮拧低几个点"：

  微博 valuation.json：悲观 g=-5%/tg=0% → 15.11，基准 g=0%/tg=1% → 18.07，
  乐观 g=+3%/tg=2% → 20.93。基期 OE、折现率、投资组合折价（6 折）三情景**完全相同**。

后果有三层，每层都致命：
  1. 悲观情景继承了基准的全部假设。基准模型错了，三情景一起朝同一方向错，
     "下行保护"这项独立信息归零。
  2. 离散度被压扁。11 个归档案例的 悲观/基准 中位数 0.79——所谓"最坏情况"平均
     只比基准低两成；腾讯 0.91、拼多多 0.90，这不是情景分析，是误差棒。
  3. 股权价值由非经营资产主导时漏洞最大。微博长期投资 16.63 亿 + 净现金 5.42 亿，
     而全部企业价值仅 11.76 亿——**悲观情景里那笔投资照样按 6 折加回**。
     结果悲观每股 15.11 vs 现价 6.99，机器在说"最坏情况也赚 116%"，
     于是它成了全库唯一的"核心买入"，而三位大师人格全部投了反对票。

因此本脚本把"悲观情景必须由独立方法推导"从文档纪律升级为机器门禁。

═══ 十二项检查 ═══
S1 schema：必填字段齐全、概率和为 1、现价为正、护城河档位合法。
S1b 护城河定量得分（REQ-P1-03，可选）：给了 moat_score 即校验区间 / 词=分带投影 /
   依据挂 [E:]——得分是平滑 MoS 门槛的直接输入，裸分数禁止。
S2 悲观情景方法独立性：`method` 必须属独立方法白名单（不走 DCF 的另一条路），
   禁止 dcf_* 系列。基准/乐观可以用 DCF。
S2b 悲观值算术重算：`method` 只是标签，标签与数字之间此前零算术关联——实测把
   method 写成 liquidation、数字从旧 DCF 随手减 0.2、配一句带 [E:] 的话即可全过。
   故白名单方法必须登记 `method_inputs`（结构化输入），脚本按该方法的公式重算
   每股价值并与 `value_per_share` 比对，容差 2%，对不上即 FAIL。
   悲观值必须是「算出来的」，不是「填出来的」。
S2c 最差年必须是实证压力年：`worst_year_margin` 原实现只重算算术、不问
   worst_margin 从哪来，于是机械取了序列最小值。对利润率长期上行的公司，
   序列最早那年必然最低——但那年低是因为**公司当年还小**，不是因为危机。
   实证：茅台取 2006 年 31.5%（序列首年、幼年期），而真实政策冲击只让净利率
   从 2012 的 50.3% 降到 2014 的 47.6%；苹果取 FY2007 的 14.6%（iPhone 刚发布、
   仍是 Mac+iPod 公司），实际压力年 FY2013 是 21.7%。故须登记 `worst_year` +
   含 [E:] 的 `worst_year_stress_evidence`；worst_margin 须与该年实际净利率相符；
   若 worst_year 是序列最早年且秩相关 >0.5（长期上行）即 FAIL。
   方向不对称正是它长期没被发现的原因：拒绝侧高估悲观值无害（柯达档位不变），
   买入侧则会误杀（茅台悲观年化 -9.73% 直接打死闸门二③）。
S3 压力项充分性：悲观情景 `stressed_assumptions` ≥2 项且不得只有 growth——
   "只调增速"正是本次要消灭的做法。
S4 离散度哨兵：悲观/基准 每股价值比 > 0.85 即报错（下行不是独立估计，只是轻微打折）。
S5 非经营资产分层压力：若各情景登记了 `non_operating_addback_per_share`，
   悲观 ≥ 基准 且该项占基准价值 >10% → 报错（微博漏洞的直接门禁）。
S6 "无真实下行"显式承认：悲观每股价值 > 现价时，等价于"本标的没有下行风险"，
   必须在 `no_real_downside_ack` 写明理由，否则报错。
S7 概率证据指针：任一情景概率偏离 `default_probabilities` 即须挂 [E:] 指针
   （偏离容差 1e-9——纪律是"任何偏离"，不是"偏离超过 10pct"）。
S7b iv-growth 证据指针：`intrinsic_value_growth` 非零即须在 `iv_growth_evidence`
   挂 [E:] 指针。该字段是闸门二"不收敛下限"的加数（下限 = 股息率 + 内在价值增速），
   杀伤力大于情景概率——回溯审计显示保守按 0 填时 11 个案例中 9 个过不了闸门二，
   即这个手填数字直接决定过闸与否。概率偏离都要证据而它裸奔，
   等于前门装指纹锁、后窗户敞开。缺失该字段时告警（下限将只剩股息率）。
S8 价值陷阱闸门：安全边际（vs 基准）> 50% 且（收入或核心驱动因子连续 ≥3 年负增长）
   → 强制降档，且必须给出 `value_trap.catalyst` 与 `value_trap.catalyst_deadline`，
   `verdict_cap` 只能是"小仓位试探"或"排除"。缺任一项报错。
   收入连续下滑年数可由 `--metrics` 自动读取，核心驱动因子年数手工登记。
S9 股息率量纲哨兵：`dividend_yield` >20% 判为百分数误填报错、为负报错；
   提供 `--snapshot` 时与 market_snapshot 交叉核对同源同口径（差异 >5% 报错）。
S9b 股息率口径哨兵（OBS-600660-03 升格，2 案例达门槛）：`dividend_yield` 须携带
   `dividend_yield_basis`（ttm_paid/annual_plan/sustainable_forward 三选一）；
   登记可选对照值 `dividend_yield_alt` 时两口径差 >25% 披露（>50% 强制换口径
   或挂 [E:] 豁免）——特别分红/首次中期分红会把 TTM 口径推高 38-53%（茅台/双汇实证）。
S10 乐观情景增速基率上限（REQ-P1-05，登记才校验）：`industry` 不在行业基率
   表白名单报错；乐观情景 `growth_assumption` 超基率 80 分位且无
   `optimistic_growth_support`（含 [E:]，Phase 4.5 变异认知）支撑 → 拦截；
   有支撑 → 放行但披露（BASERATE_OPTIMISTIC_P80_OVERRIDE）。
S11 尾部风险块结构（REQ-P1-05，块存在才校验）：`tail_risk_derivation` 的
   rationale_ref [E:] / forensic_score / arithmetic_coverage / governance
   词表 / loss_tail ∈[-1,0)——非法结构在底稿关卡即拦，不等引擎崩。

═══ 输入格式（data/scenarios.json）═══
{
  "company": "微博", "ticker": "WB", "currency": "USD",
  "price": 6.99,                        # 与 market_snapshot 同源
  "moat": "narrow",                     # wide|narrow|none（决定闸门门槛，见 reverse_dcf）
  "discount_rate": 0.10,
  "hold_years": 5,
  "dividend_yield": 0.087,
  "intrinsic_value_growth": 0.00,       # 基准情景下每股内在价值长期增速（不收敛下限用）
  "iv_growth_evidence": "过去 5 年 OE 复合增速 -3%，保守取 0 [E:financials_WB.json]",
                                        # S7b：intrinsic_value_growth 非零时必填，须含 [E:] 指针
  "default_probabilities": {"悲观": 0.3, "基准": 0.5, "乐观": 0.2},
  "scenarios": [
    {"name": "悲观", "value_per_share": 5.90, "probability": 0.4,
     "method": "liquidation",
     "method_note": "净现金 5.42 亿 + 上市投资 3 折 + 非上市归零 [E:financials_WB.json]",
     "method_inputs": {                 # S2b：脚本按方法公式重算并与 value_per_share 比对（容差 2%）
       "shares_m": 245.7,               # 股本（百万股）
       "components": [                  # 清算科目：金额（百万，负债用负数）× 折价率
         {"item": "净现金", "amount_m": 542, "haircut": 1.0},
         {"item": "上市投资", "amount_m": 780, "haircut": 0.3},
         {"item": "非上市投资", "amount_m": 883, "haircut": 0.0},
         {"item": "主业清算价值", "amount_m": 300, "haircut": 0.5}
       ]
     },
     "stressed_assumptions": ["base_oe", "non_operating_discount", "margin"],
     "non_operating_addback_per_share": 2.10,
     "probability_evidence": "[E:phase3_analysis.md] DAU 连续 6 季下滑"},
    {"name": "基准", "value_per_share": 18.07, "probability": 0.45,
     "method": "dcf_owner_earnings", "non_operating_addback_per_share": 5.60,
     "probability_evidence": "[E:phase3_analysis.md] 广告大盘企稳"},
    {"name": "乐观", "value_per_share": 20.93, "probability": 0.15,
     "method": "dcf_owner_earnings", "non_operating_addback_per_share": 5.60,
     "probability_evidence": "[E:phase3_analysis.md] 视频号分流见顶"}
  ],
  "value_trap": {                        # 仅在 S8 触发时必填
    "catalyst": "…", "catalyst_deadline": "2027-12-31", "verdict_cap": "小仓位试探"
  }
}

用法：
    python3 check_scenarios.py data/scenarios.json [--metrics data/metrics_X.json] \
        [-o data/scenario_audit.json]
退出码：0 通过（可含警告）；1 存在错误，禁止进入 Phase 4.5；3 脚本自身异常。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alert_codes import unknown_codes  # 告警码注册表：唯一事实源

# S 前缀 -> 注册表代号。长前缀必须排在短前缀之前（S2b 先于 S2、S7b 先于 S7），
# derive_codes 按本列表顺序做首次匹配。
_S_PREFIX_TO_CODE = [
    ("S2b", "S2B_BEAR_ARITHMETIC"),
    ("S2c", "S2C_WORST_YEAR_NOT_STRESS"),
    ("S2d", "S2D_TROUGH_PB_BASIS"),
    ("S2e", "S_DISCOUNT_RATE_FLOOR"),
    # S10/S11 必须排在 S1 之前（前缀首次匹配，"S10-IND" 会被 "S1" 吞掉）
    ("S10-IND", "BASERATE_INDUSTRY_UNKNOWN"),
    ("S10-OVR", "BASERATE_OPTIMISTIC_P80_OVERRIDE"),
    ("S10", "BASERATE_OPTIMISTIC_ABOVE_P80"),
    ("S11", "TAIL_DERIVATION_UNANCHORED"),
    ("S7c-DR-TIER", "DR_INDUSTRY_TIER_UNKNOWN"),
    ("S7c-DR-RAT", "DR_DERIVATION_UNANCHORED"),
    ("S7c-DR", "DR_STRATIFIED_RATE_MISMATCH"),
    ("S7c-VAR", "PROB_DERIVATION_INVALID"),
    ("S7c-RANGE", "PROB_DERIVATION_OUT_OF_RANGE"),
    ("S7c-DEV", "PROB_DERIVATION_MISMATCH"),
    ("S7b", "S7B_IV_GROWTH_EVIDENCE"),
    ("S1", "S1_SCHEMA"),
    ("S2", "S2_BEAR_METHOD_INDEPENDENCE"),
    ("S3", "S3_STRESS_SUFFICIENCY"),
    ("S4", "S4_DISPERSION"),
    ("S5", "S5_NON_OPERATING_STRESS"),
    ("S6", "S6_NO_REAL_DOWNSIDE"),
    ("S7", "S7_PROBABILITY_EVIDENCE"),
    ("S8", "S8_VALUE_TRAP"),
    ("S9", "S9_DIVIDEND_YIELD_DIMENSION"),
]

# 独立方法白名单：共同特征是**不经过基准情景那套 DCF**，而是另起一条推导路径。
INDEPENDENT_METHODS = {
    "liquidation": "清算/净资产变现价值（有形净资产、净现金 + 可变现投资并施加更狠折价）",
    "asset_replacement": "重置成本法（重建同等产能/门店/牌照要花多少钱）",
    "pb_trough": "历史 PB 最低分位 × 当期每股净资产",
    "pe_trough_multiple": "危机期估值倍数（历史 PE / EV-EBIT 最低分位）× 当期盈利",
    "worst_year_margin": "历史最差年利润率 × 当期收入，再乘危机期倍数（全程不走 DCF）",
    "peer_death_analogy": "同类死亡案例类比（该商业模式已衰退完的公司，峰值→稳态的实际跌幅与终局倍数）",
    "sotp_asset_floor": "分部资产底价加总（各分部按可变现价值而非盈利能力估）",
}
# 基准/乐观允许的方法（走 DCF 系列没问题，问题只在悲观情景也走它）
DCF_METHODS = {
    "dcf_owner_earnings": "Owner Earnings 三情景 DCF",
    "dcf_fcf": "自由现金流 DCF",
    "ddm": "股息折现",
    "rab": "受监管资产基数",
    "rnpv": "管线风险调整净现值",
    "reverse_dcf": "反向 DCF",
    # REQ-P1-02 持仓型控股通道：reverse_dcf.py sotp 的结构化产出（持仓表+控股
    # 折价+反解隐含折价）。悲观情景的 SOTP 对应形态是 sotp_asset_floor（分部按
    # 可变现价值，在独立方法白名单）——基准/乐观的 "sotp" 与之构成上下行不同源，
    # 软银 2019 案即此形态。
    "sotp": "分部加总（盈利能力口径；REQ-P1-02 起由 reverse_dcf.py sotp 结构化产出）",
    "pb_roe": "PB-ROE 回归",
    # REQ-P1-01 成长股通道：成熟期稳态利润×到达概率折回（reverse_dcf.py growth）。
    # 只允许出现在基准/乐观——悲观情景仍须走独立方法白名单（到达失败分支的
    # 失败残值锚定建议复用悲观情景的独立方法，两处同源）。
    "growth_terminal_backcast": "成长股通道：成熟期稳态 OE×终局倍数折回×到达概率",
}
MOATS = {"wide", "narrow", "none"}
DISPERSION_MAX = 0.85          # S4：悲观/基准 上限
S2B_TOLERANCE = 0.02           # S2b：机器重算 vs 登记值 容差
NON_OP_MATERIAL = 0.10         # S5：非经营资产占基准价值的重大性阈值
VALUE_TRAP_MOS = 0.50          # S8：安全边际触发线
VALUE_TRAP_DECLINE_YEARS = 3   # S8：连续负增长年数
TRAP_VERDICT_CAPS = {"小仓位试探", "排除"}
E_PTR = re.compile(r"\[E:[^\]]+\]")


# ═══ S2b 悲观值算术重算 ═══
# 为什么：`method` 是自由字符串，`value_per_share` 是手填数字，两者零算术关联。
# 归档实测的绕过路径：标签写 liquidation、数字从旧 DCF 随手减 0.2、配一句带 [E:]
# 的话 → 九项检查全过。S2 查的是"你声称用了什么方法"，不是"数字是否真由该方法算出"。
# 修法：白名单方法各定义结构化输入 schema，脚本按公式重算、与登记值比对（容差 2%）。
# 这是把「底稿 ≈ 事实」的纪律从数据层延伸到推导层：悲观值必须是算出来的。

def _rc_components(mi):
    """清算/重置/分部底价共用：Σ(科目金额 × 折价率) / 股本。负债登负数、折价 1.0。"""
    comps = mi.get("components")
    shares = mi.get("shares_m")
    if not comps or not shares:
        return None, "缺 `components`（科目金额×折价率清单）或 `shares_m`（百万股）"
    total = 0.0
    for c in comps:
        if c.get("amount_m") is None or c.get("haircut") is None:
            return None, f"科目 {c.get('item', '?')} 缺 `amount_m` 或 `haircut`"
        h = float(c["haircut"])
        if not (0.0 <= h <= 1.0):
            return None, f"科目 {c.get('item', '?')} 折价率 {h} 越界（应在 0~1，负债请把金额登负数）"
        total += float(c["amount_m"]) * h
    return total / float(shares), None


def _rc_pb_trough(mi):
    """历史 PB 最低分位 × 当期每股净资产。"""
    pb, bvps = mi.get("trough_pb"), mi.get("bvps")
    if pb is None or bvps is None:
        return None, "缺 `trough_pb`（历史 PB 低分位）或 `bvps`（当期每股净资产）"
    return float(pb) * float(bvps), None


def _rc_pe_trough(mi):
    """危机期倍数 × 当期每股盈利（eps 或 earnings_m/shares_m 二选一）。"""
    mult = mi.get("trough_multiple")
    if mult is None:
        return None, "缺 `trough_multiple`（历史 PE / EV-EBIT 最低分位倍数）"
    eps = mi.get("eps")
    if eps is None and mi.get("earnings_m") is not None and mi.get("shares_m"):
        eps = float(mi["earnings_m"]) / float(mi["shares_m"])
    if eps is None:
        return None, "缺 `eps`（或 `earnings_m` + `shares_m`）"
    return float(mult) * float(eps), None


def _rc_worst_year_margin(mi):
    """历史最差年利润率 × 当期收入 × 危机期倍数，除以股本。全程不走 DCF。"""
    need = [k for k in ("worst_margin", "revenue_m", "crisis_multiple", "shares_m")
            if mi.get(k) is None]
    if need:
        return None, f"缺 {'、'.join('`%s`' % k for k in need)}"
    return (float(mi["worst_margin"]) * float(mi["revenue_m"])
            * float(mi["crisis_multiple"]) / float(mi["shares_m"])), None


def _rc_peer_death(mi):
    """同类死亡案例类比：锚定每股价值 × 终局比例（峰值→稳态实际跌幅推出的存活比例）。"""
    a, r = mi.get("anchor_value_per_share"), mi.get("terminal_ratio")
    if a is None or r is None:
        return None, "缺 `anchor_value_per_share`（锚定每股价值）或 `terminal_ratio`（终局存活比例 0~1）"
    if not (0.0 <= float(r) <= 1.0):
        return None, f"`terminal_ratio` {r} 越界（应在 0~1）"
    return float(a) * float(r), None


S2B_RECOMPUTE = {
    "liquidation": _rc_components,
    "asset_replacement": _rc_components,
    "sotp_asset_floor": _rc_components,
    "pb_trough": _rc_pb_trough,
    "pe_trough_multiple": _rc_pe_trough,
    "worst_year_margin": _rc_worst_year_margin,
    "peer_death_analogy": _rc_peer_death,
}


def normalize_yield(key, value):
    """比率类字段归一化为小数口径，返回 (小数值, 判定依据, 是否歧义)。

    ★ 为什么需要这个函数（归档实测，v2.15 新增闸门引入的新风险面）★
    market_snapshot 里**同一个字段名 `dividend_yield_ttm` 单位并不统一**：
      招行 0.0511（小数）  腾讯 1.17（百分数）  伊利 5.14（百分数）
      英伟达 `dividend_yield_ttm_pct` = 0.13（即 0.13%，不是 13%）
    腾讯与伊利额外给了 `_frac` 字段消歧，另外 8 个案例只有一个裸字段。

    这在旧流程里只影响展示，但「价值不收敛下限 = 股息率 + 内在价值增速」
    把股息率当成**加数**直接参与闸门判定——单位错 100× 会让英伟达的 0.13%
    被读成 13%，闸门二直接自动过闸。这是典型的「算得出数、不报错」的静默错误。

    判定顺序（后缀是强证据，量级只作兜底）：
      1. 键名以 `_pct` 结尾 → 值为百分数，除以 100；
      2. 键名以 `_frac` 结尾 → 值已是小数；
      3. 裸键名且值 > 0.20 → 判为百分数误填（20% 以上股息率极罕见），除以 100
         并标记歧义 —— 这种情况必须由分析师显式消歧，不能由脚本猜。
      4. 其余按小数。
    """
    if value is None:
        return None, "缺失", False
    k = (key or "").lower()
    if k.endswith("_pct"):
        return value / 100.0, "键名后缀 _pct", False
    if k.endswith("_frac"):
        return value, "键名后缀 _frac", False
    if value > 0.20:
        return value / 100.0, "裸键名且值 >0.20，判为百分数误填", True
    return value, "裸键名，按小数", False


def snapshot_dividend_yield(snapshot):
    """从 market_snapshot 取股息率并归一化。优先带后缀的字段（无歧义）。"""
    if not snapshot:
        return None, None, "无快照", False
    order = ["dividend_yield_ttm_frac", "dividend_yield_frac",
             "dividend_yield_ttm_pct", "dividend_yield_pct",
             "dividend_yield_forward", "dividend_yield", "dividend_yield_ttm"]
    for k in order:
        if isinstance(snapshot.get(k), (int, float)):
            v, basis, ambiguous = normalize_yield(k, snapshot[k])
            return v, k, basis, ambiguous
    return None, None, "快照无股息率字段", False


def _revenue_series(metrics):
    cs = (metrics or {}).get("chart_series") or {}
    revs = None
    if isinstance(cs.get("revenue"), list):
        revs = cs["revenue"]
    elif isinstance((metrics or {}).get("series"), list):
        revs = [s.get("revenue") for s in metrics["series"]]
    if not revs:
        return None, None
    years = cs.get("years") if isinstance(cs.get("years"), list) else None
    pairs = [(years[i] if years and i < len(years) else i, v)
             for i, v in enumerate(revs) if v is not None]
    if len(pairs) < 2:
        return None, None
    return [p[0] for p in pairs], [p[1] for p in pairs]


def revenue_decline_streak(metrics):
    """末端连续收入负增长年数（严格口径）。返回 0 表示最新一年未下滑。"""
    _, clean = _revenue_series(metrics)
    if not clean:
        return None
    streak = 0
    for i in range(len(clean) - 1, 0, -1):
        if clean[i] < clean[i - 1]:
            streak += 1
        else:
            break
    return streak


def revenue_peak_stagnation(metrics, min_drawdown=0.10, min_years_since_peak=3):
    """峰值回撤停滞检测：高位回落后长期回不去，也是「生意在萎缩」。

    ★ 为什么严格「连续负增长」不够（微博实测）★
    微博收入 2021 见顶 2257.1，随后 1836.3 → 1759.8 → 1754.7 → 1757.2。
    最后一年微涨 0.14%，严格连续口径立刻归零，价值陷阱闸门完全不触发 ——
    而这条曲线的真相是「峰值回撤 22%、四年没回去」，正是融化冰块的标准形态。
    一个 0.14% 的翘尾就能关掉闸门，那闸门等于不存在。

    故补第二个触发源：最新收入低于历史峰值 min_drawdown 以上，且峰值距今
    ≥ min_years_since_peak 年（即已连续多年未收复峰值）。
    """
    years, clean = _revenue_series(metrics)
    if not clean:
        return None
    peak_i = max(range(len(clean)), key=lambda i: clean[i])
    peak, latest = clean[peak_i], clean[-1]
    if peak <= 0:
        return None
    drawdown = 1.0 - latest / peak
    years_since = (len(clean) - 1) - peak_i
    return {
        "peak_year": years[peak_i] if years else peak_i,
        "peak_revenue": peak,
        "latest_revenue": latest,
        "drawdown_from_peak": drawdown,
        "years_since_peak": years_since,
        "stagnating": bool(drawdown >= min_drawdown and years_since >= min_years_since_peak),
        "thresholds": {"min_drawdown": min_drawdown,
                       "min_years_since_peak": min_years_since_peak},
    }


def check(path, metrics_path=None, snapshot_path=None):
    errors, warnings, info = [], [], {}
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)

    # metrics 提前加载：S2c（最差年校验）与 S8（收入萎缩判定）都要用，
    # 原先只在 S8 处就地读取，S2c 位置更前故上提为共享变量。
    metrics = None
    if metrics_path and os.path.exists(metrics_path):
        with open(metrics_path, "r", encoding="utf-8") as f:
            metrics = json.load(f)

    # ---- S1 schema ----
    for key in ("price", "scenarios", "moat", "discount_rate"):
        if d.get(key) is None:
            errors.append(f"S1 缺必填字段 `{key}`")
    scen = d.get("scenarios") or []
    if len(scen) < 3:
        errors.append(f"S1 情景数 {len(scen)} < 3（悲观/基准/乐观三情景为强制口径）")
    if errors:
        return d, errors, warnings, info
    price = float(d["price"])
    if price <= 0:
        errors.append("S1 现价必须为正")
    if d["moat"] not in MOATS:
        errors.append(f"S1 护城河档位 `{d['moat']}` 非法，应为 {sorted(MOATS)}")
    # ---- S1b 护城河定量得分（REQ-P1-03，可选字段；给了即校验）----
    # 得分是 MoS 门槛的直接输入（平滑函数），纪律与裸概率同罪：
    # 区间合法 / 词=分带投影 / 依据必须挂 [E:]。不给不报错（legacy 词路径合法）。
    if d.get("moat_score") is not None:
        try:
            _ms = float(d["moat_score"])
        except (TypeError, ValueError):
            errors.append(f"S1b 护城河得分 `{d.get('moat_score')}` 非数值")
            _ms = None
        if _ms is not None:
            if not (0 <= _ms <= 100):
                errors.append(f"S1b 护城河得分 {_ms} 超出 0~100 区间")
            else:
                try:
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    from reverse_dcf import moat_word_from_score
                    _proj = moat_word_from_score(_ms)
                    if d["moat"] != _proj:
                        errors.append(
                            f"S1b 护城河评级 `{d['moat']}` 与得分 {_ms} 的分带投影 "
                            f"`{_proj}` 不一致（MOAT_SCORE_WORD_MISMATCH）——评级词"
                            "必须等于得分投影（≥65 wide / ≥35 narrow / <35 none），"
                            "禁止两套口径并存")
                except ImportError:
                    warnings.append("S1b 无法导入 reverse_dcf 校验得分投影（分带校验跳过）")
                _basis = d.get("moat_score_basis")
                if not _basis or "[E:" not in str(_basis):
                    errors.append(
                        "S1b 护城河得分缺 `moat_score_basis` 或其不含 [E:] 证据指针"
                        "（MOAT_SCORE_BASIS_MISSING）——得分直接决定 MoS 门槛，"
                        "裸分数禁止，须登记三组件（超额回报证据/源硬度/定标与趋势）"
                        "各自的证据来源")
        if d.get("moat_sources") is not None and not isinstance(d.get("moat_sources"), list):
            errors.append("S1b `moat_sources` 须为字符串数组（有硬证据的护城河源列表）")
    psum = sum(float(s.get("probability", 0)) for s in scen)
    if abs(psum - 1.0) > 1e-6:
        errors.append(f"S1 概率之和为 {psum:.4f}，必须等于 1")
    for s in scen:
        if s.get("value_per_share") is None or s.get("name") is None:
            errors.append(f"S1 情景 {s.get('name', '?')} 缺 name/value_per_share")
        if not s.get("method"):
            errors.append(f"S1 情景 {s.get('name', '?')} 缺 `method`（方法必须显式登记，"
                          f"否则无法判断下行是否独立）")
    if errors:
        return d, errors, warnings, info

    vals = {s["name"]: float(s["value_per_share"]) for s in scen}
    by_val = sorted(scen, key=lambda s: float(s["value_per_share"]))
    bear, base_s, bull = by_val[0], by_val[len(by_val) // 2], by_val[-1]
    bear_v, base_v = float(bear["value_per_share"]), float(base_s["value_per_share"])
    info["scenario_values"] = vals
    info["bear_name"], info["base_name"] = bear["name"], base_s["name"]
    info["margin_of_safety_vs_base"] = 1.0 - price / base_v if base_v else None

    # ---- S2 悲观情景方法独立性 ----
    m = bear["method"]
    info["bear_method"] = m
    if m in DCF_METHODS:
        errors.append(
            f"S2 悲观情景方法 `{m}` 属 DCF 系列，与基准同源 —— 那不是独立的下行估计，"
            f"只是把基准的增速旋钮拧低。必须改用独立方法之一："
            f"{'、'.join(sorted(INDEPENDENT_METHODS))}")
    elif m not in INDEPENDENT_METHODS:
        errors.append(f"S2 悲观情景方法 `{m}` 不在白名单内。独立方法："
                      f"{'、'.join(sorted(INDEPENDENT_METHODS))}；"
                      f"如确需新增方法，先在 valuation-guide.md 论证并同步白名单")
    # R4-c：字段类型防御。实测 method_note 写成 list 会让 re 匹配抛 TypeError
    # 并以退出码 3 崩掉——报错信息对执行者毫无指引（第一批福耀案 scenarios 底稿
    # 三次被门禁拒绝，其中一次就是这个）。改为结构化提示。
    _mn = bear.get("method_note", "")
    if not isinstance(_mn, (str, type(None))):
        errors.append(
            f"S2 悲观情景 `method_note` 期望字符串，实为 {type(_mn).__name__}"
            f"——若需分点说明请合并为单个字符串（用「；」分隔），"
            f"schema 不接受 list/dict")
        _mn = ""
    if not E_PTR.search(_mn or ""):
        errors.append("S2 悲观情景 `method_note` 必须含 [E:] 证据指针 —— "
                      "独立方法的输入（清算科目/历史最差年/危机期倍数）必须可溯源")

    # ---- S2b 悲观值算术重算（标签必须兑现为算术）----
    if m in S2B_RECOMPUTE:
        mi = bear.get("method_inputs")
        if not mi:
            errors.append(
                f"S2b 悲观情景方法 `{m}` 缺 `method_inputs`：方法标签与数字之间必须有"
                f"算术关联，脚本要按该方法的公式重算并与 value_per_share 比对。"
                f"没有结构化输入，「liquidation」只是一个可以随便写的词"
                f"（实测：标签写 liquidation、数字从旧 DCF 随手减 0.2 即可绕过 S2）")
        else:
            recomputed, err = S2B_RECOMPUTE[m](mi)
            if err:
                errors.append(f"S2b 悲观情景 `method_inputs` 不完整，无法重算：{err}")
            else:
                info["bear_recomputed_value"] = round(recomputed, 4)
                dev = abs(recomputed - bear_v) / bear_v if bear_v else None
                info["bear_recompute_deviation"] = round(dev, 6) if dev is not None else None
                if dev is None or dev > S2B_TOLERANCE:
                    errors.append(
                        f"S2b 机器按 `{m}` 重算悲观每股价值 = {recomputed:.2f}，登记值 "
                        f"{bear_v:.2f}，偏差 {dev:.1%} > {S2B_TOLERANCE:.0%} 容差。"
                        f"两者必须一致：要么 method_inputs 登记的科目/倍数与实际推导不符，"
                        f"要么 value_per_share 不是由该方法算出——悲观值必须是算出来的，"
                        f"不是填出来的")

    # ---- S2e 折现率下限纪律（R4 遗漏项补齐）----
    # SKILL.md 早有该纪律（r >= max(10%, 计价货币 10Y 国债 + 4pct)，单向：只在
    # 高利率环境抬高门槛、不会放松 10%），但第一批复核发现**两个脚本都没实现**
    # ——它一直只是文档纪律。折现率直接决定内在价值，无门禁等于可随手调松。
    # 硬下限 10% 无条件校验；快照提供无风险利率时按 max(10%, rf+4pct) 校验。
    r_ = d.get('discount_rate')
    if r_ is not None:
        floor_r, floor_src = 0.10, '硬下限 10%'
        rf = None
        if snapshot_path and os.path.exists(snapshot_path):
            try:
                with open(snapshot_path, 'r', encoding='utf-8') as _f:
                    _sd = json.load(_f)
                rf = _sd.get('risk_free') or _sd.get('yield_10y') or _sd.get('rf_10y')
                if isinstance(rf, dict):
                    rf = rf.get('value') or rf.get('yield')
            except (OSError, ValueError):
                rf = None
        if isinstance(rf, (int, float)):
            rf = rf / 100.0 if rf > 1 else rf
            if rf + 0.04 > floor_r:
                floor_r, floor_src = rf + 0.04, f'10Y {rf:.2%} + 4pct'
        info['discount_rate_floor'] = round(floor_r, 6)
        info['discount_rate_floor_basis'] = floor_src
        if float(r_) < floor_r - 1e-9:
            errors.append(
                f'S2e 折现率 {float(r_):.2%} 低于下限 {floor_r:.2%}（{floor_src}）'
                f'——该纪律单向生效：只在高利率环境抬高门槛、不会放松 10%。'
                f'折现率直接决定内在价值，调松 1pct 可抬高估值 15-20%')

    # ---- S2d pb_trough 的谷底 PB 口径纪律（OBS-600660-02）----
    # 与 S2c 同类缺陷：_rc_pb_trough 只重算 trough_pb x bvps，不问 trough_pb 从哪来。
    # 福耀案实证：初版用**前复权价 ÷ 当年账面 BPS** 得 2.0-2.2 倍——前复权价已扣除
    # 后续分红除权影响，与当年 BPS 不可比，系统性低估谷底 PB 约 15-20%；按不复权价
    # 重建后真实区间为 1.66-2.46。谷底 PB 直接决定悲观值，口径错等于下行保护虚高。
    # 门禁只要求「说清口径」，不替执行者判断数值对错（避免过拟合个案）。
    if m == 'pb_trough' and bear.get('method_inputs'):
        _mi = bear['method_inputs']
        _ev = str(_mi.get('trough_pb_evidence') or '')
        if not E_PTR.search(_ev):
            errors.append(
                'S2d `pb_trough` 缺 `method_inputs.trough_pb_evidence`（含 [E:] 指针）：'
                '须逐条列出谷底 PB 的样本（日期 + 价格 + 当时 BPS）')
        _low = _ev.replace(' ', '')
        if not any(k in _low for k in ('不复权', '未复权', '后复权')):
            errors.append(
                'S2d `trough_pb_evidence` 未声明行情口径：必须写明「不复权」'
                '（前复权价已扣除后续分红除权影响，与当年账面 BPS 不可比，'
                '会系统性低估谷底 PB——福耀案实证低估约 15-20%）')
        if not any(k in _low for k in ('最低', '谷底', '低点')):
            errors.append(
                'S2d `trough_pb_evidence` 未标明价格样本为期间最低点：'
                '福耀案初版曾误引当年上涨段价格当作谷底')

    # ---- S2c 最差年必须是实证压力年（阶段四，茅台+苹果两案例硬证据）----    # `worst_year_margin` 原实现只重算算术、不问 worst_margin 从哪来，于是它
    # 机械地取了序列最小值。对利润率长期上行的公司，序列最早那年必然最低——
    # 但那年低是因为**公司当年还小**，不是因为危机。
    #   茅台：取 2006 年 31.5%（序列首年、公司幼年期）。而真实政策冲击
    #         （塑化剂+三公消费）只让净利率从 2012 的 50.3% 降到 2014 的 47.6%。
    #         悲观情景假设降 19pct，是实证压力的 7 倍 → 悲观年化 -9.73%，
    #         直接打死闸门二③。
    #   苹果：取 FY2007 的 14.6%（iPhone 刚发布、仍是 Mac+iPod 公司）。
    #         实际压力年 FY2013 是 21.7%。
    # 判据（客观、不拍阈值）：worst_year 是序列最早年 **且** 利润率秩相关 > 0.5
    # （长期上行）⇒ 该年低利润率是规模/阶段效应，不是危机形态。
    # 方向不对称正是它长期没被发现的原因：拒绝侧高估悲观值无害（柯达档位不变），
    # 买入侧则会误杀（茅台）。
    if m == "worst_year_margin" and bear.get("method_inputs"):
        mi = bear["method_inputs"]
        wy = mi.get("worst_year")
        if wy is None:
            errors.append(
                "S2c `worst_year_margin` 缺 `method_inputs.worst_year`：必须登记"
                "最差利润率取自哪一年，否则无法判断那年是「危机」还是「公司当年还小」")
        if not E_PTR.search(str(mi.get("worst_year_stress_evidence") or "")):
            errors.append(
                "S2c `worst_year_margin` 缺 `method_inputs.worst_year_stress_evidence`"
                "（含 [E:] 指针）：必须说明该年份发生了什么可指认的外部压力事件。"
                "「序列里最低的一年」不等于「危机年」——利润率长期上行的公司，"
                "最早那年必然最低，但那是规模/阶段效应")
        if wy is not None and metrics:
            _ser = metrics.get("series") or []
            _yrs = [s.get("year") for s in _ser if s.get("net_margin") is not None]
            _nm = {s.get("year"): s.get("net_margin") for s in _ser
                   if s.get("net_margin") is not None}
            if _yrs and wy not in _nm:
                errors.append(
                    f"S2c `worst_year` = {wy} 不在 metrics 净利率序列年份 "
                    f"{_yrs[0]}~{_yrs[-1]} 内，无法核验")
            elif _yrs:
                _actual = _nm[wy]
                _claim = float(mi.get("worst_margin") or 0)
                info["worst_year"] = wy
                info["worst_year_actual_margin"] = round(_actual, 6)
                if _actual and abs(_claim - _actual) / abs(_actual) > 0.05:
                    errors.append(
                        f"S2c `worst_margin` = {_claim:.1%} 与 {wy} 年实际净利率 "
                        f"{_actual:.1%} 不符（偏差 >5%）——数字必须取自该年真实报表")
                _rho = ((metrics.get("normalization") or {}).get("margin_trend")
                        or {}).get("spearman_rho")
                info["margin_spearman_rho"] = _rho
                if wy == _yrs[0] and _rho is not None and _rho > 0.5:
                    _worst_late = min(
                        ((y, v) for y, v in _nm.items() if y != _yrs[0]),
                        key=lambda kv: kv[1], default=None)
                    _hint = ""
                    if _worst_late:
                        _hint = (f"；剔除首年后的实际最差年为 {_worst_late[0]} 年 "
                                 f"{_worst_late[1]:.1%}，若该年确有压力事件应改用它")
                    errors.append(
                        f"S2c `worst_year` = {wy} 是净利率序列最早年，且序列长期上行"
                        f"（秩相关 {_rho:+.2f} > 0.5）——该年低利润率是规模/阶段效应，"
                        f"不是危机形态。公司不会退回十年前的商业形态，用它做悲观情景"
                        f"会系统性低估悲观值并误杀买入档位（茅台 2006 / 苹果 FY2007 "
                        f"两案实证）{_hint}")

    # ---- S3 压力项充分性 ----
    stressed = bear.get("stressed_assumptions") or []
    info["bear_stressed"] = stressed
    if len(stressed) < 2:
        errors.append(f"S3 悲观情景 `stressed_assumptions` 仅 {len(stressed)} 项（要求 ≥2）："
                      f"下行情景至少要同时压两类假设，只压一类等于没做压力测试")
    elif set(stressed) <= {"growth", "terminal_growth"}:
        errors.append("S3 悲观情景只压了增速类假设 —— 这正是本门禁要消灭的做法。"
                      "必须同时压基期盈利能力/利润率/非经营资产折价/股本摊薄之一")

    # ---- S4 离散度哨兵 ----
    ratio = bear_v / base_v if base_v else None
    info["bear_base_ratio"] = ratio
    if ratio is not None and ratio > DISPERSION_MAX:
        errors.append(
            f"S4 悲观/基准 = {ratio:.2f} > {DISPERSION_MAX}：悲观情景仅比基准低 "
            f"{(1 - ratio) * 100:.0f}%，这不是独立的下行估计而是基准的轻微打折。"
            f"（归档实测中位数 0.79，腾讯 0.91、拼多多 0.90 均属此类）")

    # ---- S5 非经营资产分层压力折价 ----
    addbacks = {s["name"]: s.get("non_operating_addback_per_share") for s in scen}
    info["non_operating_addback_per_share"] = addbacks
    ab_bear, ab_base = addbacks.get(bear["name"]), addbacks.get(base_s["name"])
    if ab_bear is not None and ab_base is not None:
        if ab_base and abs(ab_base) / base_v > NON_OP_MATERIAL and ab_bear >= ab_base:
            errors.append(
                f"S5 非经营资产加回占基准价值 {abs(ab_base) / base_v:.0%}（>{NON_OP_MATERIAL:.0%} "
                f"即重大），但悲观情景加回 {ab_bear} ≥ 基准 {ab_base} —— 三情景共用一套折价率。"
                f"股权价值由非经营资产主导时，悲观情景必须施加更狠折价"
                f"（上市 9 折→7 折、非上市 6 折→3 折或归零，净现金须问是否会被烧掉/困住）")
    elif ab_base is None:
        warnings.append("S5 未登记 `non_operating_addback_per_share`：若估值用了 --add-back，"
                        "必须逐情景登记，否则无法门禁分层折价")

    # ---- S6 "无真实下行"显式承认 ----
    info["bear_vs_price"] = bear_v / price if price else None
    if bear_v > price:
        ack = d.get("no_real_downside_ack")
        if not ack:
            errors.append(
                f"S6 悲观情景每股价值 {bear_v:.2f} > 现价 {price:.2f}（{bear_v / price:.2f}×）："
                f"这在数学上等价于断言「本标的没有下行风险」，属极强主张，"
                f"必须在 `no_real_downside_ack` 写明理由并接受红队质询，不得静默通过。"
                f"（微博案例悲观值达现价 2.16×，正是唯一「核心买入」的成因）")
        else:
            warnings.append(f"S6 悲观值高于现价（{bear_v / price:.2f}×），已显式承认：{ack[:80]}")

    # ---- S7 概率证据指针 ----
    defaults = d.get("default_probabilities") or {"悲观": 0.3, "基准": 0.5, "乐观": 0.2}
    # REQ-P1-04：probability_derivation 块（存在且合法）本身就是概率来源的
    # 结构化证据——得分/变异认知/红队 → 映射 → 采用值的传导链 + rationale_ref
    # 已覆盖"偏离默认须挂证据"的语义，逐情景 probability_evidence 不再重复
    # 要求（推导链即证据）；块不出现时维持 legacy 逐情景要求。
    pd_block = d.get("probability_derivation")
    pd_valid = False
    if pd_block is not None:
        _pd_rat = pd_block.get("rationale_ref") or ""
        pd_valid = ("[E:" in _pd_rat)
    info["probability_deviations"] = {}
    for s in scen:
        p = float(s["probability"])
        dp = defaults.get(s["name"])
        if dp is None:
            warnings.append(f"S7 情景 `{s['name']}` 未在 default_probabilities 登记默认值，"
                            f"无法判断是否偏离——按纪律须挂证据指针")
            dp = None
        if dp is not None and abs(p - float(dp)) > 1e-9:
            info["probability_deviations"][s["name"]] = round(p - float(dp), 6)
            if not pd_valid and not E_PTR.search(s.get("probability_evidence", "") or ""):
                errors.append(
                    f"S7 情景 `{s['name']}` 概率 {p:.0%} 偏离默认 {float(dp):.0%}，"
                    f"但 `probability_evidence` 未挂 [E:] 指针。概率是闸门二唯一不受"
                    f"闸门一污染的输入，也是最容易被叙事污染的参数——任何偏离都必须"
                    f"挂 Phase 3 证据，不是「偏离超过 10pct 才写理由」"
                    + ("；或改用 probability_derivation 块（REQ-P1-04，须挂 [E:] "
                       "rationale_ref）" if pd_block is not None else ""))

    # ---- S7b iv-growth 证据指针（与 S7 同等强制）----
    # `intrinsic_value_growth` 是闸门二"不收敛下限"的加数（下限 = 股息率 + 内在价值增速），
    # 回溯审计显示保守按 0 填时 11 个案例中 9 个过不了闸门二——这个手填数字直接决定过闸与否，
    # 杀伤力大于情景概率。概率偏离 1e-9 都要证据，此处不能裸奔。
    ivg = d.get("intrinsic_value_growth")
    ivg_ev = d.get("iv_growth_evidence") or ""
    info["intrinsic_value_growth"] = ivg
    if ivg is None:
        warnings.append("S7b 未登记 `intrinsic_value_growth`：闸门二的不收敛下限将不可评"
                        "（reverse_dcf 会置 gate2.pass = None）。若判断内在价值不增长，"
                        "请显式填 0 而不是留空")
    else:
        ivg = float(ivg)
        if ivg > 0.20:
            errors.append(f"S7b `intrinsic_value_growth` = {ivg}，超过 20%：几乎必然是"
                          f"百分数误填成小数（如 5 应写 0.05）。本字段是闸门二不收敛下限的"
                          f"加数，错 100× 会让闸门直接自动过闸")
        elif abs(ivg) > 1e-12 and not E_PTR.search(ivg_ev):
            errors.append(
                f"S7b `intrinsic_value_growth` = {ivg:.2%} 非零，但 `iv_growth_evidence` "
                f"未挂 [E:] 证据指针。该字段直接加进闸门二的不收敛下限，是决定过闸与否的"
                f"手填参数——必须写明推导依据（如「过去 5 年 Owner Earnings 复合增速 8%，"
                f"保守取 5% [E:financials_XX.json]」），与 S7 概率纪律同等强制")
        elif abs(ivg) > 1e-12:
            info["iv_growth_evidence"] = ivg_ev[:120]

    # ---- S7c 证据传导块（REQ-P1-04；块存在才校验，legacy 文件零新增消息）----
    # 交付物原文：「check_scenarios.py S7 校验概率来源字段非空」。两个块都是
    # opt-in：出现即须结构合法 + 证据挂 [E:] + 与声明值一致——让 Phase 3 护城河
    # 得分、Phase 4.5 变异认知、红队悲观概率对最终数字的传导可审计。
    if pd_block is not None:
        import reverse_dcf as _rd
        _pd_rat = pd_block.get("rationale_ref") or ""
        if "[E:" not in _pd_rat:
            errors.append("S7c-VAR probability_derivation.rationale_ref 为空或未挂 "
                          "[E:]——概率是闸门二唯一不受闸门一污染的输入，传导链"
                          "必须可审计（REQ-P1-04 交付物：概率来源字段非空）")
        else:
            _score = pd_block.get("moat_score", d.get("moat_score"))
            if _score is None:
                errors.append("S7c-VAR probability_derivation 缺 moat_score（块内与"
                              "顶层均未提供）——映射公式以护城河得分为锚")
            else:
                _vp = pd_block.get("variant_perception", "neutral")
                _rt = pd_block.get("red_team_pessimistic")
                _mapped = _rd.map_scenario_probabilities(
                    float(_score), _vp, _rt)
                if _mapped is None:
                    errors.append(
                        f"S7c-VAR 护城河得分 {_score} < 35：无买入结论，概率传导"
                        "无意义")
                else:
                    _names = {s["name"] for s in scen}
                    if _names != {"悲观", "基准", "乐观"}:
                        errors.append("S7c-VAR 概率映射定义于标准三情景名"
                                      "（悲观/基准/乐观）")
                    else:
                        _adopted = {s["name"]: float(s["probability"]) for s in scen}
                        for _k, _v in _adopted.items():
                            _dev = _v - _mapped["probabilities"][_k]
                            if abs(_dev) > _rd.PROB_DEVIATION_MAX + 1e-9:
                                errors.append(
                                    f"S7c-RANGE 情景 `{_k}` 采用概率 {_v:.2%} 偏离"
                                    f"映射值 {_mapped['probabilities'][_k]:.2%} 达 "
                                    f"{_dev:+.1%}，超出可调范围 ±10%——应修映射输入"
                                    "（得分/变异认知/红队）而不是绕映射")
                            elif abs(_dev) > _rd.PROB_DEVIATION_FREE + 1e-9 and \
                                    "[E:" not in (pd_block.get("deviation_rationale")
                                                  or ""):
                                errors.append(
                                    f"S7c-DEV 情景 `{_k}` 采用概率偏离映射 "
                                    f"{_dev:+.1%}（>2%），但 deviation_rationale "
                                    "未挂 [E:]——偏离映射须逐项论证，与 S7 偏离"
                                    "默认须证据同构")
                        info["prob_derivation_mapped"] = {
                            k: round(v, 4)
                            for k, v in _mapped["probabilities"].items()}
    dr_block = d.get("discount_rate_derivation")
    if dr_block is not None:
        import reverse_dcf as _rd
        _tier = dr_block.get("industry_tier")
        if _tier is None:
            errors.append("S7c-DR-RAT discount_rate_derivation 缺 industry_tier"
                          "（行业档白名单 stable/standard/cyclical/financials/"
                          "speculative_growth/holding_complex）")
        elif _tier not in _rd.INDUSTRY_RISK_PREMIUM:
            errors.append(f"S7c-DR-TIER industry_tier `{_tier}` 不在白名单——"
                          "行业档白名单防自造档位，与护城河词表同源纪律")
        else:
            _rate, _ = _rd.stratified_discount_rate(
                _tier, dr_block.get("rf_10y"))
            _stated = d.get("discount_rate")
            if _stated is None or abs(_rate - float(_stated)) > 1e-9:
                errors.append(
                    f"S7c-DR 分层折现率 {_rate:.2%}（{_tier} 档）≠ 声明的 "
                    f"discount_rate {_stated}——声明分层却沿用旧折现率，V0 与 r "
                    "不同源；premium 档须按分层值重算三情景估值并同步")
        if "[E:" not in (dr_block.get("rationale_ref") or ""):
            errors.append("S7c-DR-RAT discount_rate_derivation.rationale_ref 为空或"
                          "未挂 [E:]——折现率直接决定 IRR 下限与终值，裸参数与"
                          "裸概率同罪")
        _mkt = dr_block.get("market")
        if _mkt is not None and _mkt not in _rd.MARKET_FLOOR_HURDLES:
            errors.append(f"S7c-DR-RAT market `{_mkt}` 未注册（应为 "
                          f"{sorted(_rd.MARKET_FLOOR_HURDLES)}）——floor 门槛市场"
                          "校准仅支持已注册市场")

    # ---- S8 价值陷阱闸门 ----
    mos = info["margin_of_safety_vs_base"]
    streak = d.get("core_driver_decline_years")
    rev_streak, stag = None, None
    if metrics_path:
        if not os.path.exists(metrics_path):
            warnings.append(f"S8 metrics 文件不存在：{metrics_path}，收入萎缩判定无法自动核验")
        elif metrics is not None:
            rev_streak = revenue_decline_streak(metrics)
            stag = revenue_peak_stagnation(metrics)
    decline = max([x for x in (streak, rev_streak) if x is not None], default=None)
    info["revenue_decline_streak"] = rev_streak
    info["core_driver_decline_years"] = streak
    info["peak_stagnation"] = stag
    info["decline_streak_used"] = decline
    shrinking_reasons = []
    if decline is not None and decline >= VALUE_TRAP_DECLINE_YEARS:
        shrinking_reasons.append(f"收入/核心驱动因子连续 {decline} 年负增长")
    if stag and stag["stagnating"]:
        shrinking_reasons.append(
            f"收入自 {stag['peak_year']} 年峰值回撤 {stag['drawdown_from_peak']:.0%}"
            f"、已 {stag['years_since_peak']} 年未收复（峰值回撤停滞口径）")
    info["shrinking_reasons"] = shrinking_reasons
    trap = bool(mos is not None and mos > VALUE_TRAP_MOS and shrinking_reasons)
    info["value_trap_triggered"] = trap
    if trap:
        vt = d.get("value_trap") or {}
        head = (f"S8 价值陷阱闸门触发（安全边际 {mos:.0%} > {VALUE_TRAP_MOS:.0%} 且"
                f"{'；'.join(shrinking_reasons)}）：")
        if not vt.get("catalyst"):
            errors.append(head + "缺 `value_trap.catalyst` —— 便宜本身不是催化剂，"
                                 "必须写明「靠什么让折价收敛」")
        if not vt.get("catalyst_deadline"):
            errors.append(head + "缺 `value_trap.catalyst_deadline` —— 必须给时间上限，"
                                 "否则实际回报≈股息率+内在价值增速（远低于双闸门算出的 IRR）")
        cap = vt.get("verdict_cap")
        if cap not in TRAP_VERDICT_CAPS:
            errors.append(head + f"`value_trap.verdict_cap` 必须为 {sorted(TRAP_VERDICT_CAPS)} 之一"
                                 f"（当前 {cap!r}）：统计意义上的便宜 + 生意在萎缩 = 强制降档，"
                                 f"不得进核心买入")
        if vt.get("catalyst") and not E_PTR.search(vt.get("catalyst", "")):
            warnings.append(head + "催化剂建议挂 [E:] 指针以便红队核验")

    # ---- S9 股息率量纲哨兵（闸门二"不收敛下限"的输入防护）----
    # 「不收敛下限 = 股息率 + 内在价值增速」把股息率当加数直接参与闸门判定，
    # 单位错 100× 会让闸门二自动过闸（英伟达 0.13% 被读成 13%）。
    dy = d.get("dividend_yield")
    info["dividend_yield"] = dy
    if dy is None:
        warnings.append("S9 未登记 `dividend_yield`：闸门二的不收敛下限将只算内在价值增速，"
                        "对高股息标的会系统性低估保底回报")
    elif dy > 0.20:
        errors.append(f"S9 `dividend_yield` = {dy}，超过 20%：几乎必然是百分数误填成小数"
                      f"（如 5.14 应写 0.0514）。本字段是闸门二不收敛下限的加数，"
                      f"错 100× 会让闸门直接自动过闸")
    elif dy < 0:
        errors.append(f"S9 `dividend_yield` = {dy} 为负，非法")
    # ---- S9b 股息率口径哨兵（OBS-600660-03 升格实施：2 案例达门槛）----
    # 量纲对≠口径对：TTM 实施派发口径会被特别分红/首次中期分红推高，
    # 直接作闸门二"不收敛下限"的加数会把「单次成分」当「可持续回报」。
    # 两例标定：茅台 2018-10 首次中期分红推高 TTM 口径 53%（5.05% vs 可持续 3.3%）；
    # 双汇 2019-06 特别分红推高支付口径 38%（8.03% vs 归属年度口径 5.83%）。
    # 25% 线的独立语义：单次成分 ≥1/4 时「TTM≈可持续」假设已实质失效。
    basis = d.get("dividend_yield_basis")
    info["dividend_yield_basis"] = basis
    if dy is not None and basis not in ("ttm_paid", "annual_plan", "sustainable_forward"):
        warnings.append(
            "S9 `dividend_yield` 未登记机器可读口径 `dividend_yield_basis`"
            "（ttm_paid/annual_plan/sustainable_forward 三选一）："
            "两案实证（茅台 2018-10/双汇 2019-06）未登记口径时无法判定该值"
            "是否含不可外推的单次分红成分，闸门二②的保底回报语义存疑")
    alt = d.get("dividend_yield_alt")
    if dy is not None and isinstance(alt, (int, float)) and min(dy, alt) > 0:
        gap = abs(dy - alt) / min(dy, alt)
        info["dividend_yield_basis_gap"] = round(gap, 4)
        if gap > 0.50:
            warnings.append(
                f"S9 两口径股息率分裂 {gap:.0%}（>50%）：主口径 {dy:.2%} vs 对照口径 {alt:.2%}"
                f"——差异过半来自单次分红成分，主口径不得作为闸门二不收敛下限的加数，"
                f"须改用 sustainable_forward 口径重登，或登记 [E:] 证据证明单次成分不可外推")
        elif gap > 0.25:
            warnings.append(
                f"S9 两口径股息率分裂 {gap:.0%}（25%-50% 区间）：主口径 {dy:.2%} vs 对照口径 {alt:.2%}"
                f"——须在报告决策卡披露两口径对照与特别分红/单次分红成分；"
                f"gate2② 按主口径通过时标注『脆弱通过』并挂接股息可持续性证伪条件")
    if snapshot_path:
        if not os.path.exists(snapshot_path):
            warnings.append(f"S9 快照文件不存在：{snapshot_path}，股息率无法交叉核对")
        else:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                snap = json.load(f)
            sv, skey, sbasis, ambiguous = snapshot_dividend_yield(snap)
            info["snapshot_dividend_yield"] = {"value": sv, "field": skey, "basis": sbasis,
                                               "unit_ambiguous": ambiguous}
            if ambiguous:
                warnings.append(
                    f"S9 快照字段 `{skey}` 单位有歧义（{sbasis}）：归档案例中同名字段"
                    f"既有小数（0.0511）也有百分数（1.17 / 5.14）。请在快照中改用"
                    f"`_frac`（小数）或 `_pct`（百分数）后缀显式消歧")
            if sv is not None and dy is not None:
                if abs(sv - dy) > max(abs(sv) * 0.05, 1e-4):
                    errors.append(
                        f"S9 股息率与快照不一致：scenarios.json = {dy:.4%}，"
                        f"market_snapshot `{skey}` 归一化后 = {sv:.4%}（{sbasis}）。"
                        f"两者必须同源同口径，否则闸门二的保底回报是假的")
    # ---- S10 乐观情景增速基率上限（REQ-P1-05；登记才校验，legacy 零新增）----
    # 乐观情景增速的天花板是行业基率不是想象力：『20 年 20% 增速』的公司
    # 历史上不到 1%。industry + growth_assumption 都是 opt-in 字段——
    # 存量 12 案不登记即不触发（基线不动）；登记了 industry 而 optimistic
    # 情景声明 growth_assumption 超过基率 80 分位时，须挂 Phase 4.5 显式
    # 变异认知支撑（optimistic_growth_support 含 [E:]）才放行（披露码）。
    industry = d.get("industry")
    _opt_scn = next((s for s in scen if s.get("name") == "乐观"), None)
    _opt_g = (_opt_scn or {}).get("growth_assumption")
    info["industry"] = industry
    if industry is not None or _opt_g is not None:
        _brt = None
        try:
            import reverse_dcf as _rd10
            _brt = _rd10.INDUSTRY_GROWTH_BASE_RATES
        except Exception:  # noqa: BLE001 — 引擎不可导入时降级为跳过并大声说
            warnings.append("S10 无法导入 reverse_dcf 取基率表，S10 校验跳过")
        if _brt is not None:
            if industry is None:
                errors.append(
                    "S10-IND 乐观情景登记了 `growth_assumption` 但 scenarios.json"
                    " 缺 `industry`——基率锚无从对照（行业基率表白名单见"
                    " references/base-rates.md）")
            elif industry not in _brt:
                errors.append(
                    f"S10-IND industry `{industry}` 不在行业增速基率表白名单——"
                    f"应为 {sorted(_brt)}。白名单防自造行业档，新行业先在"
                    " references/base-rates.md 登记基率（p50/p80）再使用")
            if industry in _brt and _opt_g is not None:
                if not isinstance(_opt_g, (int, float)) or not (-1.0 < _opt_g <= 2.0):
                    errors.append(
                        f"S10 乐观情景 growth_assumption `{_opt_g}` 非法"
                        "（须为 (-1, 2] 的年收入增速小数）")
                else:
                    _p80 = _brt[industry]["p80"]
                    info["optimistic_growth_vs_p80"] = {
                        "growth": _opt_g, "p80": _p80,
                        "exceeds": _opt_g > _p80}
                    if _opt_g > _p80:
                        _sup = (_opt_scn or {}).get("optimistic_growth_support") or ""
                        if "[E:" in _sup:
                            warnings.append(
                                f"S10-OVR 乐观情景增速 {_opt_g:.1%} 超行业基率"
                                f" 80 分位 {_p80:.1%}（{industry}），已挂 Phase 4.5"
                                " 变异认知 [E:] 支撑——放行但强制披露：本案结论"
                                "依赖『本行业历史分布不适用』的判断，红队质询的"
                                "第一靶点就是它")
                        else:
                            errors.append(
                                f"S10 乐观情景增速 {_opt_g:.1%} 超过行业基率"
                                f" 80 分位 {_p80:.1%}（{industry}）且无"
                                " optimistic_growth_support 支撑——『20 年 20%"
                                " 增速』的公司历史上不到 1%，天花板是基率不是"
                                "想象力。要么下调增速至基率内，要么给出 Phase"
                                " 4.5 显式变异认知支撑（挂 [E:]）")
    # ---- S11 尾部风险块结构校验（REQ-P1-05；块存在才校验）----
    # 引擎在 expected-return 侧硬拒非法块；此处提前在底稿关卡拦住，避免
    # "底稿过了门禁、引擎才崩"的两段式失败。p_tail 本身纯映射无采用值，
    # 故无需对照采用概率（与 S7c 概率块的结构不同源）。
    _trd = d.get("tail_risk_derivation")
    if _trd is not None:
        if not isinstance(_trd, dict):
            errors.append("S11 tail_risk_derivation 须为对象")
        else:
            if "[E:" not in (_trd.get("rationale_ref") or ""):
                errors.append(
                    "S11 tail_risk_derivation.rationale_ref 为空或未挂 [E:] 证据"
                    "指针——尾部概率直接决定期望 IRR 的第四项，裸参数禁止")
            _fs = _trd.get("forensic_score")
            if not isinstance(_fs, (int, float)) or _fs < 0:
                errors.append(
                    f"S11 tail_risk_derivation.forensic_score `{_fs}` 须为 ≥0 的"
                    "数值（forensic_screen.py 输出：veto×10 + redflag×1）")
            _cov = _trd.get("arithmetic_coverage")
            if _cov is not None and (not isinstance(_cov, (int, float))
                                     or not 0.0 <= _cov <= 1.0):
                errors.append(
                    f"S11 tail_risk_derivation.arithmetic_coverage `{_cov}`"
                    "须在 [0,1]（forensic_screen.py 输出）")
            _gov = _trd.get("governance", "normal")
            if _gov not in ("good", "normal", "poor"):
                errors.append(
                    f"S11 tail_risk_derivation.governance `{_gov}` 非法，应为"
                    " good/normal/poor（治理评分词表）")
            _lt = _trd.get("loss_tail", -1.0)
            if not isinstance(_lt, (int, float)) or not -1.0 <= _lt < 0.0:
                errors.append(
                    f"S11 tail_risk_derivation.loss_tail `{_lt}` 须为 [-1, 0) 的"
                    "年化 IRR（-1=股权归零；缺省 -1.0）")
    return d, errors, warnings, info


def derive_codes(errors, warnings, info):
    """把 S1–S9 消息前缀提取为稳定告警码，供回放断言机器判定。

    十一项检查的编号本来就是每条消息的第一个 token，此处只做提取而非新增
    事实源——保持「编号定义在检查逻辑处」这一唯一性。任何前缀无法映射的消息
    都会被收入 unmapped 并大声报出：漏映射意味着回放断言会漏判该规则，
    必须当成 bug 修，而不是静默放过。

    注意 `value_trap_triggered` 是独立布尔字段而非错误消息——S8 的 errors
    说的是「缺催化剂字段」，闸门真正触发看的是这个字段，两者不可混淆。
    """
    codes, unmapped = [], []
    for msg in list(errors) + list(warnings):
        for prefix, code in _S_PREFIX_TO_CODE:  # 长前缀在前：S2b 必须先于 S2
            if msg.startswith(prefix):
                codes.append(code)
                break
        else:
            unmapped.append(msg[:80])
    if info.get("value_trap_triggered"):
        codes.append("S8_VALUE_TRAP")
    unknown = unknown_codes(codes)
    if unknown:
        raise KeyError(f"未注册的告警码 {unknown}，请先在 scripts/alert_codes.py 登记")
    return sorted(set(codes)), unmapped


def main():
    ap = argparse.ArgumentParser(description="三情景底稿门禁（Phase 4 出口关卡）")
    ap.add_argument("scenarios", help="data/scenarios.json 路径")
    ap.add_argument("--metrics", help="data/metrics_<公司>.json，用于自动核验收入连续下滑年数")
    ap.add_argument("--snapshot", help="data/market_snapshot.json，用于股息率量纲与同源交叉核对（S9）")
    ap.add_argument("-o", "--output", help="审计结果 JSON 输出路径")
    args = ap.parse_args()

    try:
        d, errors, warnings, info = check(args.scenarios, args.metrics, args.snapshot)
    except Exception as e:  # noqa: BLE001 — 脚本自身异常必须与"数据不合格"区分
        print(f"[EXCEPTION] 校验器自身异常：{type(e).__name__}: {e}")
        print("退出码 3 绝不可当作「已校验」或「数据不合格」 —— 那是脚本 bug，修脚本后重跑。")
        sys.exit(3)

    print(f"三情景门禁：{d.get('company', '?')}（{d.get('ticker', '?')}）"
          f" 现价 {d.get('price')} 护城河 {d.get('moat')}")
    if info.get("scenario_values"):
        print(f"  情景值：{info['scenario_values']}")
        print(f"  悲观/基准 {info.get('bear_base_ratio'):.2f}"
              f"   悲观/现价 {info.get('bear_vs_price'):.2f}"
              f"   安全边际(vs基准) {info.get('margin_of_safety_vs_base'):.1%}")
        print(f"  悲观方法 {info.get('bear_method')} / 压力项 {info.get('bear_stressed')}")
    if info.get("value_trap_triggered"):
        print("  🔴 价值陷阱闸门已触发：" + "；".join(info.get("shrinking_reasons", [])))
    for w in warnings:
        print(f"  [WARN] {w}")
    for e in errors:
        print(f"  [FAIL] {e}")

    codes, unmapped = derive_codes(errors, warnings, info)
    if unmapped:
        print("  [BUG] 以下消息无法映射到告警码（回放断言将漏判该规则，必须修）：")
        for m in unmapped:
            print(f"        {m}")
    if codes:
        print(f"  告警码：{' '.join(codes)}")

    result = {"file": args.scenarios, "errors": errors, "warnings": warnings,
              "codes": codes, "unmapped_messages": unmapped,
              "diagnostics": info, "passed": not errors}
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {args.output}")
    if errors:
        print(f"结论：{len(errors)} 项错误，禁止进入 Phase 4.5（期望回报与机会成本对照）。")
        sys.exit(1)
    print(f"结论：通过（{len(warnings)} 条警告）。下行情景已确认为独立推导。")
    sys.exit(0)


if __name__ == "__main__":
    main()
