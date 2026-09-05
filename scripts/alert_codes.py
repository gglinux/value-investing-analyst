"""告警码注册表 —— 回放断言与引擎告警之间的唯一事实源。

## 为什么需要这个文件

回放协议（`backtest/PROMPT.md` 第九节）承诺「每次改引擎都应能一键重跑全部断言」。
第一批 6 案例的复核发现该承诺无法兑现，根因有三：

1. `compute_metrics.py` 的告警是自由文本（`"稀释警报：收入总量CAGR 26.5%…"`），
   每个案例的字符串都不同，无法机器匹配；
2. `check_scenarios.py` 虽有 S1–S9 前缀，但编号是**拼在中文句子开头**的，
   不是可查询字段；
3. Phase 0 排雷完全没有代码（人工按 `references/forensic-checklist.md` 逐条过），
   引擎里查不到任何痕迹。

于是「改完代码，确认柯达/康美/海控/福耀四个该拒绝的案例没被搞坏」这件事，
只能人工重读 6 份报告 —— 每改一次代码就要重做一遍。

本文件把每条规则固定为一个稳定代号，引擎输出代号 + 中文说明两个字段，
案例落盘一个扁平的 `codes` 列表，断言判定退化为集合运算。

## 分层命名

| 前缀 | 层 | 来源 |
|---|---|---|
| `P0_V*` | Phase 0 一票否决（6 项） | 人工按清单登记，代号取自本表 |
| `P0_R*` | Phase 0 红旗（20 项） | 同上 |
| `P0_C*` | Phase 0 A股防割附加（4 项） | 同上 |
| `M_*` | 定量画像告警 | `compute_metrics.py` 自动 |
| `NORM_*` | 周期正常化与基期纪律 | `compute_metrics.py` 自动 |
| `S*` | 三情景门禁十一项 | `check_scenarios.py` 自动 |
| `GATE*` | 双闸门结果 | `reverse_dcf.py expected-return` 自动 |

`P0_*` 合计 6+20+4 = 30 项，与福耀案「0/6+0/20+0/4 = 0/30 误杀」口径一致。

## 断言的「任一满足」语义

`ASSERTIONS` 把一个回放断言映射到**一组**代号，任一命中即算满足。这是刻意的设计：
海控案官方要求断言「周期高位」，但引擎实际输出的是
`NORM_BASE_UNUSABLE`（全期平均亏损 → 均值路径数学失效，拒绝给标签）而非
`NORM_CYCLE_PEAK`。第一批靠人工「两层合读」裁定命中，本表把这个等价关系
显式化，使其可机器判定且可被审查。
"""

# ═══════════════════════════════════════════════════════════════════
# 一、告警码表：code -> (层, 语义说明)
# ═══════════════════════════════════════════════════════════════════

ALERTS = {
    # ---- Phase 0 一票否决（触发任一 → 直接排除）----
    "P0_V1_AUDIT_OPINION": ("phase0_veto", "近 3 年审计非标意见（保留/无法表示/否定）"),
    "P0_V2_FRAUD_HISTORY": ("phase0_veto", "公司或实控人有财务造假处罚前科"),
    "P0_V3_PLEDGE_HIGH": ("phase0_veto", "实控人股权质押比例 > 70%"),
    "P0_V4_DEPOSIT_LOAN_DOUBLE_HIGH": ("phase0_veto", "存贷双高（货币资金与有息负债均 > 总资产 25%）且无合理解释"),
    "P0_V4A_INTEREST_INVERSION": ("phase0_veto", "利率倒挂：利息收入/平均货币资金 < 1.2% 且远低于融资成本——存贷双高的加强验证器，假现金拿不出真利息"),
    "P0_V5_AUDITOR_CFO_CHURN": ("phase0_veto", "3 年内更换审计师理由含糊，或 CFO 两年内离职 ≥2 人"),
    "P0_V6_CONTROLLER_TUNNELING": ("phase0_veto", "大股东掏空迹象：关联方资金占用/违规担保/不公允关联交易"),

    # ---- Phase 0 红旗：利润质量 ----
    "P0_R1_OCF_PROFIT_DIVERGENCE": ("phase0_redflag", "净利润连续 3 年 > 经营现金流净额的 1.5 倍"),
    "P0_R2_RECEIVABLES_SURGE": ("phase0_redflag", "应收账款增速 > 收入增速 1.5 倍且连续 2 年"),
    "P0_R3_GROSS_MARGIN_OUTLIER": ("phase0_redflag", "毛利率显著高于同行且无可信解释"),
    "P0_R4_NONRECURRING_PROP": ("phase0_redflag", "非经常性损益撑利润（扣非占比 < 60%）"),
    "P0_R5_INVENTORY_SURGE": ("phase0_redflag", "存货增速远超收入增速且无扩产逻辑"),
    # ---- Phase 0 红旗：资产负债表 ----
    "P0_R6_GOODWILL_HEAVY": ("phase0_redflag", "商誉占净资产 > 30%"),
    "P0_R7_OTHER_RECEIVABLES": ("phase0_redflag", "其他应收款异常大额（资金体外循环通道）"),
    "P0_R8_CIP_STAGNANT": ("phase0_redflag", "在建工程长期挂账不转固"),
    "P0_R9_SHORT_DEBT_LONG_ASSET": ("phase0_redflag", "短债长投，流动性错配"),
    # ---- Phase 0 红旗：行为信号 ----
    "P0_R10_INSIDER_SELLING": ("phase0_redflag", "大股东/高管持续大额减持"),
    "P0_R11_FINANCING_VS_RETURN": ("phase0_redflag", "累计融资额 > 累计分红+回购的 3 倍"),
    "P0_R12_RENAME_HYPE": ("phase0_redflag", "频繁改名、蹭热点变更主业"),
    "P0_R13_AGGRESSIVE_INCENTIVE": ("phase0_redflag", "激进股权激励行权条件（只考核收入不考核回报）"),
    "P0_R14_ACCOUNTING_POLICY_CHANGE": ("phase0_redflag", "会计政策变更恰好美化当期利润"),
    # ---- Phase 0 红旗：披露质量 ----
    "P0_R15_VAGUE_DISCLOSURE": ("phase0_redflag", "年报关键信息含糊（分部/客户集中度不披露）"),
    "P0_R16_MDA_TEMPLATE": ("phase0_redflag", "MD&A 连年模板化复制"),
    "P0_R17_INQUIRY_EVASIVE": ("phase0_redflag", "对交易所问询函回复避重就轻"),
    # ---- Phase 0 红旗：流动性与可交易性 ----
    "P0_R18_LOW_LIQUIDITY": ("phase0_redflag", "日均成交额 < 拟投入金额 20 倍"),
    "P0_R19_CONCENTRATED_FLOAT": ("phase0_redflag", "流通盘极小或筹码高度集中（前十大+实控人 > 85%）"),
    "P0_R20_HALT_HISTORY": ("phase0_redflag", "长期无成交/频繁停牌史"),
    # ---- Phase 0 A股防割附加 ----
    "P0_C1_LOCKUP_RELEASE": ("phase0_cn", "解禁量 > 流通盘 20% 且解禁方浮盈巨大"),
    "P0_C2_INSIDER_REDUCTION_PLAN": ("phase0_cn", "有效期内的大股东/高管减持计划"),
    "P0_C3_HYPE_SECTOR_HIGH": ("phase0_cn", "热点板块高位（涨幅显著超基本面改善）"),
    "P0_C4_HOLDER_COUNT_SURGE": ("phase0_cn", "股东户数短期暴增（筹码散化）"),

    # ---- 定量画像（compute_metrics.py 自动）----
    "M_DILUTION": ("metrics", "稀释：收入总量 CAGR 显著高于每股 CAGR"),
    "M_SHARE_INFLATION": ("metrics", "股本膨胀：期间股本增至 > 1.3 倍"),
    "M_DIVIDEND_ILLUSION": ("metrics", "分红幻觉：股东回报未被累计自由现金流覆盖（<1.0x）"),
    "M_SHAREHOLDER_RETURN_THIN_COVER": ("metrics", "股东回报覆盖偏薄（FCF 覆盖 <1.5x）"),
    "M_FCF_QUALITY": ("metrics", "利润含金量：近 5 年中 ≥3 年 FCF/净利 < 0.6"),
    "M_ROIIC_LOW": ("metrics", "增长质量：最新滚动 3 年 ROIIC < 8%"),
    "M_OWNER_YIELD_NOT_CASH_BACKED": ("metrics", "所有者收益率不可落袋：OE 未转化为可分配现金"),
    "M_UNIT_SUSPECT": ("metrics", "量纲哨兵：市值单位疑似错位（OE 收益率 <1% 或回本 >50 年 或 PB 越界）"),
    "M_PEAK_DRAWDOWN_STAGNANT": ("metrics", "峰值回撤停滞：自峰值回撤 ≥10% 且已 ≥3 年未收复（人工登记）"),
    # 观察级：形态上会影响跨年可比性，但**不是造假形态**，故刻意不纳入
    # ASSERTIONS 的 ANY_FRAUD_ALERT 组——海控 IFRS16 切换若被误计为造假类告警，
    # 会让「负样本零误杀」这类断言产生假阳性。
    "M_ACCOUNTING_STANDARD_SWITCH": ("observation", "会计准则强制切换致跨年不可比（如 IFRS16），非主动政策变更、非造假形态"),

    # ---- 周期正常化与基期纪律（compute_metrics.py 自动）----
    "NORM_CYCLE_PEAK": ("normalization", "周期高位：当期净利率显著高于全期均值，禁止当期 OE 作 DCF 基期"),
    "NORM_CYCLE_TROUGH": ("normalization", "周期低位：当期利润低估长期盈利能力"),
    "NORM_BASE_UNUSABLE": ("normalization", "基期不可用：全期平均亏损，均值路径数学失效，禁止当期数据作基期"),
    "NORM_DUAL_TRACK": ("normalization", "双轨基期：'周期高位'可能是结构性变化的误报，须并列两轨"),
    "NORM_STRUCTURAL_DECLINE": ("normalization", "结构性衰退：利润率单向下行未回归均值，'周期低位'可能是衰退"),
    "NORM_MARGIN_SHAPE": ("normalization", "利润率形状检验结果（改善/恶化/波动）"),

    # ---- 三情景门禁（check_scenarios.py 自动）----
    "S1_SCHEMA": ("scenarios", "schema：必填字段/概率和/现价/护城河档位"),
    "S2_BEAR_METHOD_INDEPENDENCE": ("scenarios", "悲观情景方法必须属独立方法白名单，禁 dcf_*"),
    "S2B_BEAR_ARITHMETIC": ("scenarios", "悲观值算术重算与登记值偏差 > 2%"),
    "S3_STRESS_SUFFICIENCY": ("scenarios", "压力项 ≥2 且不得只压增速"),
    "S4_DISPERSION": ("scenarios", "离散度哨兵：悲观/基准 > 0.85"),
    "S5_NON_OPERATING_STRESS": ("scenarios", "非经营资产悲观折价未比基准更狠"),
    "S6_NO_REAL_DOWNSIDE": ("scenarios", "悲观值 > 现价，须显式承认未构造出真实下行"),
    "S7_PROBABILITY_EVIDENCE": ("scenarios", "概率偏离默认值未挂 [E:] 证据指针"),
    "S7B_IV_GROWTH_EVIDENCE": ("scenarios", "内在价值增速非零未挂 [E:] 证据指针"),
    "S8_VALUE_TRAP": ("scenarios", "价值陷阱闸门：安全边际 >50% 且收入/驱动因子连续 ≥3 年负增长"),
    "S9_DIVIDEND_YIELD_DIMENSION": ("scenarios", "股息率量纲哨兵：>20% 判百分数误填，或与快照不一致"),
    "S_MOAT_RATING_INVALID": ("scenarios", "护城河评级非标准词（只许 wide/narrow/none）"),
    "S_DISCOUNT_RATE_FLOOR": ("scenarios", "折现率低于下限 max(10%, 10Y+4pct)"),

    # ---- 双闸门（reverse_dcf.py expected-return 自动）----
    "GATE1_PASS": ("gate", "闸门一通过：安全边际达护城河档位要求"),
    "GATE1_FAIL": ("gate", "闸门一不过：安全边际未达要求"),
    "GATE2_PASS": ("gate", "闸门二三项全过"),
    "GATE2_FAIL": ("gate", "闸门二未全过"),
    "GATE2_1_IRR_FAIL": ("gate", "闸门二①：期望 IRR 低于护城河反推门槛"),
    "GATE2_2_FLOOR_FAIL": ("gate", "闸门二②：价值不收敛下限（股息率+内在价值增速）< 6%"),
    "GATE2_3_BEAR_FAIL": ("gate", "闸门二③：悲观情景年化 < 0"),
    "GATE2_UNRATED": ("gate", "闸门二不可评（缺 --iv-growth），绝不可当作通过"),
    "GATE_EFFECTIVE_HURDLE_GAP": ("gate", "有效门槛显著高于名义门槛（悲观情景离散度推高实际折价要求）"),
}


# ═══════════════════════════════════════════════════════════════════
# 二、档位序数（回放档位轨判定用）
# ═══════════════════════════════════════════════════════════════════

VERDICT_ORDINAL = {
    "排除": 0,
    "拒绝": 1,
    "观察等价格": 2,
    "小仓位试探": 3,
    "核心买入": 4,
}
ORDINAL_TO_VERDICT = {v: k for k, v in VERDICT_ORDINAL.items()}


# ═══════════════════════════════════════════════════════════════════
# 三、断言组：assertion_id -> 满足它的 code 集合（任一命中即满足）
# ═══════════════════════════════════════════════════════════════════

_FRAUD_VETO = {
    "P0_V1_AUDIT_OPINION", "P0_V2_FRAUD_HISTORY", "P0_V3_PLEDGE_HIGH",
    "P0_V4_DEPOSIT_LOAN_DOUBLE_HIGH", "P0_V4A_INTEREST_INVERSION",
    "P0_V5_AUDITOR_CFO_CHURN", "P0_V6_CONTROLLER_TUNNELING",
}
_FRAUD_REDFLAG = {
    "P0_R1_OCF_PROFIT_DIVERGENCE", "P0_R2_RECEIVABLES_SURGE",
    "P0_R3_GROSS_MARGIN_OUTLIER", "P0_R4_NONRECURRING_PROP",
    "P0_R5_INVENTORY_SURGE", "P0_R6_GOODWILL_HEAVY",
    "P0_R7_OTHER_RECEIVABLES", "P0_R8_CIP_STAGNANT",
    "P0_R14_ACCOUNTING_POLICY_CHANGE",
}

ASSERTIONS = {
    # ---- 排雷类 ----
    "PHASE0_VETO_FIRED": _FRAUD_VETO,
    "CASH_INTEREST_CONTRADICTION": {
        "P0_V4_DEPOSIT_LOAN_DOUBLE_HIGH", "P0_V4A_INTEREST_INVERSION"},
    "OCF_PROFIT_DIVERGENCE": {"P0_R1_OCF_PROFIT_DIVERGENCE", "M_FCF_QUALITY"},
    # 福耀负样本用：任一造假类告警触发即视为误杀
    "ANY_FRAUD_ALERT": _FRAUD_VETO | _FRAUD_REDFLAG,

    # ---- 周期与基期纪律 ----
    # 海控案的等价关系在此显式化：引擎输出 NORM_BASE_UNUSABLE（均值路径失效、
    # 拒绝给标签）时，官方断言「周期高位」同样视为满足——「正确失效」不弱于「硬判」。
    "CYCLE_PEAK": {"NORM_CYCLE_PEAK", "NORM_BASE_UNUSABLE"},
    "CURRENT_BASE_FORBIDDEN": {"NORM_CYCLE_PEAK", "NORM_BASE_UNUSABLE"},
    "STRUCTURAL_DECLINE": {"NORM_STRUCTURAL_DECLINE"},

    # ---- 三情景门禁 ----
    "VALUE_TRAP": {"S8_VALUE_TRAP"},
    "DISPERSION_TOO_TIGHT": {"S4_DISPERSION"},
    "MOAT_RATING_INVALID": {"S_MOAT_RATING_INVALID"},

    # ---- 闸门 ----
    "GATE1_PASSED": {"GATE1_PASS"},
    "GATE1_FAILED": {"GATE1_FAIL"},
    "GATE2_ALL_PASSED": {"GATE2_PASS"},
    "GATE2_FAILED": {"GATE2_FAIL"},
    "NOT_CONVERGING_FLOOR_LOW": {"GATE2_2_FLOOR_FAIL"},
    "PEAK_DRAWDOWN_STAGNANT": {"M_PEAK_DRAWDOWN_STAGNANT"},

    # ---- 输入校验 ----
    "UNIT_SUSPECT": {"M_UNIT_SUSPECT"},
}


# ═══════════════════════════════════════════════════════════════════
# 四、工具函数
# ═══════════════════════════════════════════════════════════════════

def is_known_code(code):
    """代号是否已注册。"""
    return code in ALERTS


def unknown_codes(codes):
    """返回未注册的代号列表（用于门禁：写错代号必须被逮住）。"""
    return [c for c in codes if c not in ALERTS]


def unknown_assertions(names):
    """返回未注册的断言名列表。"""
    return [n for n in names if n not in ASSERTIONS]


def assertion_satisfied(assertion_id, fired_codes):
    """断言是否被满足：其代号集合与已触发代号有交集即满足。"""
    wanted = ASSERTIONS.get(assertion_id)
    if wanted is None:
        raise KeyError(f"未注册的断言 `{assertion_id}`，请先在 alert_codes.ASSERTIONS 登记")
    return bool(wanted & set(fired_codes))


def matched_codes(assertion_id, fired_codes):
    """返回使断言成立的具体代号（便于报告里写清「靠哪一条满足的」）。"""
    wanted = ASSERTIONS.get(assertion_id, set())
    return sorted(wanted & set(fired_codes))


class AlertBag:
    """告警收集器：同时维护人类可读文本与稳定机器码。

    引擎脚本用它替代裸 list.append，从而在不破坏既有文本输出的前提下
    同步产出可机器判定的代号。
    """

    def __init__(self):
        self.items = []

    def add(self, code, msg, level="ALERT"):
        if code not in ALERTS:
            raise KeyError(
                f"未注册的告警码 `{code}`——新增告警必须先在 "
                f"scripts/alert_codes.py 的 ALERTS 表登记，"
                f"否则回放断言无法判定该规则")
        self.items.append({"code": code, "level": level, "msg": msg})
        return self

    # 兼容既有代码：允许像 list 一样被 len()/迭代
    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.messages)

    @property
    def messages(self):
        return [i["msg"] for i in self.items]

    @property
    def codes(self):
        return [i["code"] for i in self.items]

    @property
    def structured(self):
        return list(self.items)
