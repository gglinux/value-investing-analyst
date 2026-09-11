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
| `GROWTH_*` | 成长股通道门禁与诊断 | `reverse_dcf.py growth` 自动（UNANCHORED 为硬拒绝、人工登记） |

`P0_*` 合计 6+20+4 = 30 项，与福耀案「0/6+0/20+0/4 = 0/30 误杀」口径一致。

## 断言的「任一满足」语义

`ASSERTIONS` 把一个回放断言映射到**一组**代号，任一命中即算满足。这是刻意的设计：
海控案官方要求断言「周期高位」，但引擎实际输出的是
`NORM_BASE_UNUSABLE`（全期平均亏损 → 均值路径数学失效，拒绝给标签）而非
`NORM_CYCLE_PEAK`。第一批靠人工「两层合读」裁定命中，本表把这个等价关系
显式化，使其可机器判定且可被审查。

## 直引代号（Netflix 案 B2-09 新增）

官方答案有时**直接点名某条告警**而非某个抽象断言——Netflix 案的
`must_not_trigger` 写的就是烧钱告警族三个代号（`M_DIVIDEND_ILLUSION` /
`M_FCF_QUALITY` / `M_OWNER_YIELD_NOT_CASH_BACKED`）。为此断言机制支持
**直引注册代号**：`ALERTS` 表中已有的代号可不经 `ASSERTIONS` 组映射直接用作
断言名，按自映射单元素组处理。写错的代号依然会被门禁逮住（不在两张表的
名字仍判未注册），故不牺牲「写错必须被逮住」的初衷。

## 豁免注册表（EXEMPTIONS）

告警因公司语境不触发时，豁免必须走注册表声明（covers/criteria/removal
三要素齐全），禁止在引擎里散落硬编码 if。与存在性前置的分工：告警预设
对象不存在（如零分红公司之于分红幻觉）属前提缺陷，前置直接写进告警判定
逻辑，不算豁免。
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
    "P0_V4A_INTEREST_INVERSION": ("phase0_veto", "利率倒挂（存贷双高验证器）：双高形态下利息收益率 < 同期存款基准（币种×年份查表，--deposit-rate 可覆盖）且 < 融资成本一半——假现金拿不出真利息"),
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
    # 持续经营存疑（2026-09-11，REQ-P0-02 落码时补注册）：净资产为负 或
    # 经营现金流连续为负且现金覆盖不足。刻意归为 redflag 而非 veto——
    # 它不是造假形态，而是「这家公司可能撑不到你的持有期结束」的算术事实；
    # 柯达 2011 原型（2009 起权益转负至 -1077，OCF 连续两年为负），
    # 该形态在 Phase 0 层此前完全无覆盖，只能等到估值层的不收敛下限才拦下。
    "P0_R21_GOING_CONCERN": ("phase0_redflag",
                             "持续经营存疑：净资产为负，或经营现金流连续 ≥2 年为负且现金不足覆盖"),
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

    # ---- 触发器可达性（trigger_reachability.py 自动；阶段三）----
    # 实证（OBS-2015-08-01 / OBS-600660-04）：茅台触发价 166.93 元在 2014-11 之后
    # 至 2020 年从未被触及——「观察等价格」退化为永不触发的观察；福耀触发价
    # 2020-03-23 真实触发且触发后 +297%，但触发后无承接流程。两个问题同一根源：
    # 触发器没有被当作一等公民——既不校验它给的价格在现实价格分布中的位置，
    # 也不定义触发后做什么。
    "TRIGGER_OUT_OF_HISTORY": ("trigger", "触发价低于 52 周最低价（历史区间之外）——观察等价格实质是永不触发的观察"),
    "TRIGGER_LOW_REACHABILITY": ("trigger", "触发价在 52 周价格带底部 10% 分位以内——可达性低，须显式披露"),
    "TRIGGER_REACHABLE": ("trigger", "触发价位于 52 周价格带内且可达性正常"),
    "TRIGGER_REEVAL_MISSING": ("trigger", "观察等价格档位缺重评触发器（触发后载入哪个 checklist 未定义）"),
    "SNAPSHOT_LEGACY_SCHEMA": ("snapshot", "行情快照使用旧命名（单位塞在字段名里），应迁移到规范 schema"),
    "SNAPSHOT_SCHEMA": ("snapshot", "行情快照缺规范字段"),
    "SNAPSHOT_TRIANGLE": ("snapshot", "市值三角不自洽：market_cap ≠ price × shares（偏差 >3%）"),
    "SNAPSHOT_FX_MISSING": ("snapshot", "报价币种与报表币种不一致但缺 fx 换算依据"),
    "SNAPSHOT_FX_SUSPECT": ("snapshot", "fx 汇率值越出合理带，疑似方向填反"),

    # ---- 周期正常化与基期纪律（compute_metrics.py 自动）----
    "NORM_CYCLE_PEAK": ("normalization", "周期高位：当期净利率显著高于全期均值，禁止当期 OE 作 DCF 基期"),
    "NORM_CYCLE_TROUGH": ("normalization", "周期低位：当期利润低估长期盈利能力"),
    "NORM_BASE_UNUSABLE": ("normalization", "基期不可用：全期平均亏损，均值路径数学失效，禁止当期数据作基期"),
    "NORM_DUAL_TRACK": ("normalization", "双轨基期：'周期高位'可能是结构性变化的误报，须并列两轨"),
    "NORM_STRUCTURAL_DECLINE": ("normalization", "结构性衰退：利润率单向下行未回归均值，'周期低位'可能是衰退"),
    "NORM_MARGIN_SHAPE": ("normalization", "利润率形状检验结果（改善/恶化/波动）"),
    # Netflix 案（B2-09）新设：engine 的 hybrid 向上正常化推荐（NI×conv）被人工裁决拒绝，
    # 属「引擎对高成长公司适配问题」的可判定实证。conv 由营运资本释放驱动（预收沉淀是
    # 会员增速的函数而非利润率函数），永久化即高估——同型证据与茅台 OBS-STAGE4-03
    # 合计 2 例，达改码门槛候选。manually_recorded，须在 normalization 底稿留痕。
    "NORM_ADJ_HYBRID_REJECTED": ("normalization", "正常化裁决人工覆盖：hybrid 向上正常化推荐被裁决拒绝（转换系数不可持续），基期锚改用引擎区间下界或更低"),

    # ---- 三情景门禁（check_scenarios.py 自动）----
    "S1_SCHEMA": ("scenarios", "schema：必填字段/概率和/现价/护城河档位"),
    "S2_BEAR_METHOD_INDEPENDENCE": ("scenarios", "悲观情景方法必须属独立方法白名单，禁 dcf_*"),
    "S2B_BEAR_ARITHMETIC": ("scenarios", "悲观值算术重算与登记值偏差 > 2%"),
    # 阶段四：worst_year_margin 的「最差年」必须是实证压力年，不是序列最小值。
    # 两案例硬证据：茅台悲观取 2006 年 31.5%（序列最早年、公司幼年期，而真实
    # 政策冲击期 2013-14 净利率仅从 50.3% 降到 47.6%）；苹果悲观取 FY2007 的
    # 14.6%（iPhone 刚发布、仍是 Mac+iPod 公司，而实际压力年 FY2013 是 21.7%）。
    # 该方法机械取序列最小值，对利润率长期上行的公司会把「幼年期」当「危机」。
    "S2C_WORST_YEAR_NOT_STRESS": ("scenarios", "worst_year_margin 的最差年选取不合规：是序列最早年且利润率长期上行（该年低利润率是规模/阶段效应而非危机），或缺 worst_year / 压力事件证据 / 与序列实际利润率不符"),
    # 与 S2c 同类的「方法只重算算术、不问输入口径」缺陷（OBS-600660-02）：
    # pb_trough 的 trough_pb 若用前复权价 ÷ 当年账面 BPS，前复权价已扣除后续
    # 分红除权影响、与当年 BPS 不可比，系统性低估谷底 PB 约 15-20%（福耀案实证：
    # 初版取 2.0-2.2，按不复权价重建后真实区间为 1.66-2.46）。
    "S2D_TROUGH_PB_BASIS": ("scenarios", "pb_trough 的谷底 PB 缺口径声明（不复权/前复权、BPS 时点）或未标注为期间最低点"),
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
    "GATE2_PASS": ("gate", "闸门二通过：参与判定的四项全过（期望 IRR≥r / 不收敛下限 / 悲观 IRR / 亏损概率）"),
    "GATE2_FAIL": ("gate", "闸门二未全过"),
    "GATE2_1_IRR_FAIL": ("gate", "闸门二①（诊断项，不参与判定）：期望 IRR 低于护城河反推门槛"),
    "GATE2_1B_IRR_BELOW_R": ("gate", "闸门二①'：期望 IRR 低于折现率 r（概率加权后不如买在公允价值）"),
    "GATE2_2_FLOOR_FAIL": ("gate", "闸门二②：价值不收敛下限（股息率+内在价值增速）< 6%"),
    "GATE2_3_BEAR_FAIL": ("gate", "闸门二③：悲观情景年化 < 0"),
    "GATE2_4_LOSS_PROB_FAIL": ("gate", "闸门二④：亏损概率 > 30%（valuation-guide 核心买入下行约束）"),
    "GATE2_UNRATED": ("gate", "闸门二不可评（缺 --iv-growth），绝不可当作通过"),
    "GATE_EFFECTIVE_HURDLE_GAP": ("gate", "【诊断】旧三项全过口径下有效门槛显著高于名义门槛（解释旧口径假阴性，不构成当前门槛）"),

    # ---- 成长股通道（reverse_dcf.py growth 自动，REQ-P1-01）----
    # 动因：B2-09 Netflix 案（全回测最深假阴性）——纯 OE 框架对「当期 OE 极小但
    # 单元经济已证」的公司无语言可说。通道以成熟期稳态利润×到达概率折回替代当期
    # OE 基期；以下代号为通道的门禁与诊断输出。
    "GROWTH_UNIT_ECONOMICS_UNPROVEN": (
        "growth", "单元经济未证：规模化边际贡献率 ≤0 或 LTV/CAC <1——增长在单位层面"
        "毁灭价值，是烧钱不是再投入。成长通道拒绝服务（exit 2），改走标准管道；"
        "这是区分 Netflix 型再投入与乐视型成长陷阱的第一道门"),
    "GROWTH_ARRIVAL_PROB_UNANCHORED": (
        "growth", "到达概率未挂证据：--arrival-prob-basis 缺失或无 [E:] 指针——裸概率禁止"
        "（与 S7 概率纪律同源；该参数直接决定通道价值）"),
    "GROWTH_ARRIVAL_PROB_ABOVE_BASERATE": (
        "growth", "到达概率高于收入基率锚：到达=增长兑现+利润率扩张+竞争存活的联合概率，"
        "不应超过同等规模公司达成所需 CAGR 的历史比例——超出须在 basis 中论证例外"),
    "GROWTH_PRICE_IMPLIES_CERTAIN_ARRIVAL": (
        "growth", "现价隐含到达概率 ≥100%：连『必然到达』都解释不了现价——透支信号，"
        "价格已定价通道外叙事"),
    "GROWTH_IMPLIED_VS_BASERATE_GAP": (
        "growth", "现价隐含到达概率显著高于基率锚（≥2 倍或绝对差 ≥25pct）：市场对到达的"
        "定价远超历史达成比例——分歧显式化，是观察/拒绝档位带的分界输入"),
    "GROWTH_TERMINAL_DOMINATED": (
        "growth", "成长通道估值 100% 来自成熟期终值折回（结构性终值主导，按构造恒触发）："
        "估值主体是尚未发生的成熟态，禁止以安全边际单独支撑核心买入，"
        "通道档位上限恒为小仓位试探"),
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
    # 杠杆与资金链压力（观察级，非造假形态）：短债长投期限错配 + 融资依赖
    # （累计融资 > 3x 累计分红+回购）。第三批恒大案官方断言「杠杆与资金链风险」
    # 的注册表等价物——立案前软银案「官方 must_trigger 无等价物」缺口的补全。
    # P0 码由 Phase 0 按 checklist 赋码（manually_recorded 先例）。
    "DEBT_LIQUIDITY_STRESS": {
        "P0_R9_SHORT_DEBT_LONG_ASSET", "P0_R11_FINANCING_VS_RETURN"},
    # 商誉/并购驱动增长（观察级）：商誉占净资产 > 30%。第四批 Valeant 案官方
    # 断言「商誉/并购驱动增长、add-back 不可持续」的等价物。刻意不入
    # ANY_FRAUD_ALERT 复用（该组语义是造假误杀防护，商誉重不等于造假）。
    "GOODWILL_ACQUISITION_DRIVEN": {"P0_R6_GOODWILL_HEAVY"},
    # 持续经营存疑（REQ-P0-02）：净资产为负 / 经营性失血。刻意**不并入**
    # ANY_FRAUD_ALERT——柯达不是造假，是烧光了；把两者混为一谈会让福耀那类
    # 「负样本零误杀」断言失去意义。
    "GOING_CONCERN_DOUBT": {"P0_R21_GOING_CONCERN"},

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

    # ---- 触发器（阶段三）----
    "TRIGGER_UNREACHABLE": {"TRIGGER_OUT_OF_HISTORY", "TRIGGER_LOW_REACHABILITY"},
}


# ═══════════════════════════════════════════════════════════════════
# 四、告警豁免注册表：exemption_id -> 声明（covers + 判据语义 + 移除条件）
# ═══════════════════════════════════════════════════════════════════
#
# 豁免 = 同一算术事实因公司语境应作不同处理（告警 → 留痕）。
# 与告警码同表纪律：covers 里的代号必须已注册（validate_exemptions 自检），
# 写错当场被测试逮住。判据本体是算术的，实现在引擎侧
# （compute_metrics.py 的 evaluate_exemptions）；本表是**治理声明**——
# 每条豁免必须写明 covers / context / criteria / removal 四要素。
#
# 合法性五条（新增豁免必须全部满足，不满足的属于叙事豁免、禁止入表）：
#   ① 算术可判：同底稿两人跑出同一豁免判定；
#   ② 证据独立：判据字段与被豁免告警的触发字段不同源（否则是循环重述）；
#   ③ 判据收窄：只写语境的必要条件，不写类型叙事（"成长股所以豁免"禁止）；
#   ④ 抑制留痕：降级 warnings + exemptions 块登记被抑制代号，禁止静默吞掉；
#   ⑤ 附移除条件：判据何时收窄/泛化须挂案例证据，防止豁免面只扩不缩。
#
# 与存在性前置的分工：告警**预设对象不存在**（如零分红公司之于分红幻觉）
# 属前提缺陷，不是豁免——前置直接写进告警判定逻辑，不进本表。

EXEMPTIONS = {
    "EX_REINVEST_GROWTH": {
        "covers": ("M_DIVIDEND_ILLUSION", "M_FCF_QUALITY",
                   "M_OWNER_YIELD_NOT_CASH_BACKED"),
        "context": "再投入型增长：订阅预收现 + 主动再投入，OCF/FCF 偏低是"
                   "商业模式特征而非恶化信号",
        "criteria": "三条件同时满足，缺一不豁免：① OCF 序列无任何一年 ≤0"
                    "（序列全空不豁免）；② 累计 capex ≥ 80% 累计 OCF；"
                    "③ rows ≥4 年、最新年收入同比为正且 3 年收入 CAGR ≥10%"
                    "（增长中的再投入=主动扩张；收缩期再投入不豁免）",
        "removal": "第二例非 Netflix 类案例验证判据后收窄或泛化；周期股扩张期"
                   "误豁免的已知代理误差一并复核",
    },
}


def exemption_covers(code):
    """返回 covers 含该代号的豁免 id 列表（判定某告警是否处于豁免语境）。"""
    return [ex_id for ex_id, decl in EXEMPTIONS.items() if code in decl["covers"]]


def validate_exemptions():
    """注册表自检：covers 里的代号必须都已注册（与 ALERTS 同一纪律）。"""
    bad = {ex_id: [c for c in decl["covers"] if c not in ALERTS]
           for ex_id, decl in EXEMPTIONS.items()}
    return {k: v for k, v in bad.items() if v}


# ═══════════════════════════════════════════════════════════════════
# 五、工具函数
# ═══════════════════════════════════════════════════════════════════

def _assertion_codes(assertion_id):
    """断言对应的代号集合：注册断言组取组内代号；直引注册代号按自映射单元素组处理。

    返回 None 表示该名字既非注册断言也非注册代号（写错必须被逮住）。
    """
    wanted = ASSERTIONS.get(assertion_id)
    if wanted is None and assertion_id in ALERTS:
        wanted = {assertion_id}
    return wanted


def is_known_code(code):
    """代号是否已注册。"""
    return code in ALERTS


def unknown_codes(codes):
    """返回未注册的代号列表（用于门禁：写错代号必须被逮住）。"""
    return [c for c in codes if c not in ALERTS]


def unknown_assertions(names):
    """返回未注册的断言名列表（直引注册代号视为已注册）。"""
    return [n for n in names if _assertion_codes(n) is None]


def assertion_satisfied(assertion_id, fired_codes):
    """断言是否被满足：其代号集合与已触发代号有交集即满足。"""
    wanted = _assertion_codes(assertion_id)
    if wanted is None:
        raise KeyError(f"未注册的断言 `{assertion_id}`，请先在 alert_codes.ASSERTIONS 登记"
                       f"（或直接引用 ALERTS 已注册代号）")
    return bool(wanted & set(fired_codes))


def matched_codes(assertion_id, fired_codes):
    """返回使断言成立的具体代号（便于报告里写清「靠哪一条满足的」）。"""
    wanted = _assertion_codes(assertion_id) or set()
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
