# 数据源与采集手册（Phase 1）

SKILL.md 只保留数据分级与降级协议的原则；**源能力矩阵、强制动作、各市场实操与踩坑记录全部集中在本文件**。本文件「接口」除特别说明外均指 `westock-data`（腾讯自选股，推荐默认源）。**每次实跑踩到新坑，固化到这里，不要写回 SKILL.md。**

已实证：美股、A股（含银行管道）、港股、日股。**尚未实证**：保险/券商（无专属管道，须手工建稿）、周期低位真实标的（仅合成数据测过）。逐案踩坑清单见 `backtest/BATCH*_FINDINGS.md`。

## 一、核心原则：不绑定单一源

**唯一契约是 `data/*.json` 底稿**，不是某个接口。计算层（`scripts/` 下脚本）零网络依赖，只消费底稿 JSON；取数在 Agent 侧、计算在脚本侧，中间用底稿解耦。**没有任何单一源能覆盖十年×三市场×十一类商业模式**，流程必须接受"多源拼装 + 双源核对 + A/B/C 分级"。三条硬约束：

1. **换源不改脚本**：新源只要能填出底稿 schema，计算与校验链路完全复用。
2. **缺源不等于不能分析**，但必须明确降级并承担工作量（见各市场节）。
3. **同一公司用不同源，数字可能不同**（港币折算/现金口径宽窄均实证过），`manifest.json` 必须登记实际用源。

## 二、能力矩阵

| 源 | 形态 | 授权 | 覆盖 | 本 skill 中的定位 |
|---|---|---|---|---|
| **westock-data**（腾讯自选股）| 平级 skill，`node <skill目录>/scripts/index.js <子命令>` | **免费无 key**，Node ≥ 18 + 网络 | A股/港股/美股/日韩股 + ETF/指数/期货/外汇；三表财报、行情、K线、一致预期、研报、公告、股东、分红、龙虎榜、宏观 | **推荐默认**。已实证四管道（NVDA/伊利/泡泡玛特+腾讯/招行）。命中其能力域禁用 web_search 替代 |
| **SEC EDGAR** | 官方 HTTP JSON API | 免费免鉴权（需 User-Agent）| 美股全历史 XBRL 事实、10-K/10-Q/20-F/6-K 原文 | **美股永久兜底仍算 A 级**。`extract_edgar_annual.py` 直连，命门双源核对首选 |
| **巨潮资讯网** | 官方网站 | 免费 | A股年报原文 PDF、处罚记录、问询函 | **A股命门科目双源核对强制走它**（原文优先于任何接口）|
| **港交所披露易** | 官方网站 | 免费 | 港股年报/公告原文、合股供股配售史 | **港股原文核对 + 老千股特征排查** |
| **ifind-finance-data**（同花顺）| 平级 skill（[安装指南](https://mcp.51ifind.com/gwstatic/static/ds_web/ifind-mcp-web/skills/SKILL_INSTALL_GUIDE.md)）| **付费**，key 写 `mcp_config.json` | A股/港股/美股行情财报、行业宏观 | **可选增强**：有 key 时补 A 股 capex/D&A 与银行科目；无 key 不影响主流程 |
| **机构研报** | B 级二手 | — | 历史 capex 序列、行业数据、竞争格局 | 补接口缺口。**只取事实，不取评级与目标价** |
| **web_search / web-fetch** | C 级兜底 | — | 媒体报道、访谈、行业新闻 | 仅作旁证，必须标来源链接。**影响结论的核心判断禁止只靠 C 级** |

## 三、强制动作（先于采集）

1. **Phase 1 开始前跑一次源探测**：`python3 scripts/check_data_sources.py`（P1-7 起为 **AST-020 四层实测**，不再输出恒真 status="always"）。四层：**安装**（PATH/`~/.local/bin`）→ **连通**（真实查询实测）→ **字段**（真实标的财报键位；限频 code=1620053006 与 PascalCase 键名漂移可识别）→ **时点**（disclosure 子命令）。按失败层级给处置（安装引导/网络提示/westock-data 升级——参照系 `.knot/metadata.json` skill_version/官方端点 HTTP 实测）。`--offline` 跳联网层、`--json` 结构化输出。缺推荐源时**不要静默降级**。
2. **`manifest.json` 登记 `data_sources`**：数组，逐项写 `{"source": "westock-data", "version": "1.0.6", "used_for": ["三表","行情","一致预期"], "level": "A"}`。同一公司换源重跑数字对不上时，第一件事查这里。
3. **命门科目双源核对不可用接口自证**：收入/归母净利/经营现金流/总股本最近 3 年必须与官方披露原文核对，跑 `scripts/crosscheck_official.py`。**接口 ≠ 原文，即使接口是 A 级**：

   | 市场 | 官方源形态 | 核对方式 |
   |---|---|---|
   | 美股 | EDGAR companyfacts（XBRL，结构化、免鉴权）| **机器核对**：`--companyfacts <file>` 自动取官方值逐年比对，`--write` 回写 crosscheck。不经人手转录 |
   | A股 | 巨潮年报 PDF（非结构化）| **人工转录 + `--audit` 体检完整性** |
   | 港股 | 披露易年报 PDF（非结构化）| 同上 |

   美股必须机器核对：手抄 `crosscheck` 与手填 `annual` 出自同一次阅读，比的是"抄得一致吗"而非"接口对不对"。EDGAR 有 XBRL 能真正独立取数，不该退回人工。

   **A4 规则**：强制科目的官方值为空＝该科目实际未被交叉核对，**直接报错**而非告警（`shares_diluted` 是每股序列的分母，未核对会线性缩放整个估值与安全边际）。确无法取得时写 `crosscheck_exempt` 显式豁免并进报告披露。

   **多市场股本公司**：`shares_diluted` 一律用总股本，不得用单一市场上市股本（否则每股序列在口径切换年机械跳变）。annual 行须显式登记 `shares_basis`（`total`/`h_a_only`/`a_only`/`basic`），跨年不一致机器拦截（`M_SHARES_BASIS_BREAK`，平安实证：H 股 8,890 → 总股本 18,210，EVPS 148.75 → 78.13 腰斩零拦截）；确属口径切换在 `spike_notes` 登记 `<year>.shares_basis` 并统一重建。年度行可选登记 `sbc`（`sbc/revenue` ≥10% 触发 `M_SBC_DILUTION`，忽略 SBC 稀释每股价值高估 10-20%）与 `convertibles`（占有息负债 ≥50% 触发 `M_CONVERTIBLE_OVERHANG`，潜在转股稀释须入下行情景）。

## 四、行情复权口径纪律（三口径使用矩阵）

| 口径 | 东财参数 | 语义 | **唯一合法用途** | 禁止用途 |
|---|---|---|---|---|
| 等比后复权 | `fqt=2` | 乘法调整，分红再投资，跨期可比 | **收益/总回报计算**（actual_Ny_total_return 一律用它） | — |
| 不复权 | `fqt=0` | 当年真实成交价 | **触及检验、内在价值比较、与当年披露 BPS/股价运算** | 长区间收益计算 |
| 等差前复权 | `fqt=1` | 减法调整（P_raw − 累计分红） | **仅形态展示、除息序列反推** | **严禁**用于收益计算（高估）、触及检验（前复权价随分红整体下移产生虚假触及） |

三条配套：① `answer.json` 的 `price_basis.source` 强制声明口径（含抓取渠道与日期），缺 `price_basis` 但含 `actual_*_total_return` 时 runner 咨询提示；② 后复权跨批次应一致、前复权因子随分红重算不可比；③ A/H 双市场时触及检验用不复权价、收益用后复权价，不混用。

## 五、比率类字段命名纪律（强制，量纲哨兵已固化进 check_scenarios S9）

**所有比率类字段（股息率、分位、占比、增速）落盘时必须带单位后缀：`_frac` = 小数，`_pct` = 百分数。** 裸字段名一律按小数解释，且值不得 > 0.20（归档底稿实证同名字段单位跨案例不统一，逐项举例见 BATCH*_FINDINGS）。

闸门二的**价值不收敛下限 = 股息率 + 内在价值增速**把股息率当加数直接参与判定——读错一个量级闸门二直接自动过闸，故升级为门禁：`check_scenarios.py` S9 要求 `dividend_yield` 必须是小数且 ≤20%；`--snapshot` 时与快照归一化值交叉核对（容差 5%）。

## 六、信息覆盖度强制动作

0. **搬运完整性（最隐蔽的事故）**：抽取产物拿到了、底稿却没搬进去——比"数据源没有"更危险，因为分析师以为自己有（GOOG/TSM 实证：勾稽覆盖率 18% 而入口校验照打"通过"）。纪律：凡有抽取中间产物必须跑 `check_transcription.py` 比对；建稿脚本化优先。`cash` 取 `cash_sti`（现金+短期投资）是估值口径，不要为过校验改狭义。
1. **分部数据只认财报原文**：`data/segments.json` 的分部收入必须从年报/20-F 的**分部报告附注**提取（A 级，登记 filings 文件名+页码）；接口主营构成与新闻转述只作交叉验证。五维定性一半论断压在分部数据上，此处降级等于全楼地基降级。
2. **对立面检索（Phase 0 排雷强制步）**：逐项检索 `公司名 + 做空报告/财务造假/监管处罚/集体诉讼/审计意见`，命中进排雷清单评估；未命中也要在 manifest 登记 `adversarial_check`（含检索日期与结论；validate_data 无痕迹即告警）。
3. **信息时效检查**：距最新财报披露超 100 天时（validate_data 会提示），强制核对最新季报/盈利预告有无未消化的剧变（腾讯 AI capex 教训），结果写入 manifest 的 `latest_quarter_checked`。
4. **来源措辞要精确**：`crosscheck.source` 里官方原文标识（10-K/20-F/年报/EDGAR/巨潮/披露易/XBRL）是强证据；混入"接口/加总/估算"会触发警告。写"20-F p.45 表 3"这类定位，不写"20-F 披露接口值"。
5. **非财务溯源最小子集**（P1-8，MVP）：护城河/竞争格局判断引用的任何非财务数据（市场份额/行业增速/用户数/产能/基率），逐条登记进底稿顶层 `nonfinancial_evidence`，必填三字段：`statement`（论断原文）、`url`（可核验链接，禁搜索结果页）、`retrieved_at`（抓取日，>400 天 WARN）；选填 `fallback_action`（`re-verify`/`substitute`/`degrade-and-disclose`）与 `confidence`。竞对底稿（`is_peer=true`）豁免。机器门禁：缺区块 WARN、缺必填 ERROR（`schema_meta.check_nonfinancial_evidence`）。引用了外部数据就必须留痕——「数字撑结论、结论无出处」是最常见的隐性降级。

## 七、各市场实操与踩坑

### 美股（含中概股）

- 首选 `westock-data`；不可用或字段缺失时，直接用 SEC EDGAR 官方 JSON API（仍算 A 级）：companyfacts 事实端点 `data.sec.gov/api/xbrl/companyfacts/CIK{10位CIK}.json`、CIK 查询 `www.sec.gov/cgi-bin/browse-edgar`（getcompany）。请求带 User-Agent 头，免鉴权。
- 踩坑：companyfacts 早年 capex 标签可能缺失（NVDA 实证），回 10-K 原文补（存档 `data/filings/`）；财年错位（NVDA 1月末、AVGO 10月末）竞对图须脚注；AVGO NCI 致勾稽失败时 `total_equity` 用含 NCI 口径。
- **中概美股（PDD 实证）**：Futu 通道字段完整；五个强制反核：币种双口径（`DisclosureCurrency`/`ShowCurrency`）按汇率与 20-F 反核、现金口径接口宽于披露须统一到披露口径、利息收入按占比决定剔除、爬坡期正常化失真双轨披露、竞对图财年错位脚注。

### A股（已实证：海天链路 + 伊利全流程）

- `westock-data` 三表可直接建底稿：income/balance/cashflow 全拉，报告期取 12-31 年报行，单位元→百万。
- 踩坑：**权益科目用 `TotalShareholderEquity`（含少数股东）而非 `SEWithoutMI`**，回报率与估值只认归母（另存 `roe_parent`/`bvps_parent`）；**TotalAssets 缺失**用 `TotalLiability+TotalShareholderEquity` 推导；**capex 无独立科目**（只有净额 NetInvestCashFlow），从年报原文（巨潮 PDF）或研报序列补、标 B 级，补不到时 `compute_metrics` 用 D&A 兜底进 warnings（须报告确认披露）；缺摊薄股本同样从年报补；字段名与港股不同（归母净利/经营现金流/营业成本均另有键名，营收 `OperatingRevenue` 完整）；`publish_date` 用 `InfoPublDate`。
- **减值年必须标注**：大额一次性减值年份须在 spike_notes 说明"还原后真实增长"，否则次年高增长被突变检测误报、基期被低基数扭曲。成熟公司 spike 约 18 条，IPO 高增长 40+ 条。
- 命门科目双源核对强制走巨潮年报原文；处罚记录与问询函也查巨潮。

### A股银行（招行全链路验证）

- **接口缺银行专属科目**：`westock-data` 三表不提供不良/拨备/NIM/资本充足率。补齐：标普信评"债券通评级报告"（连续 5 年银行指标表，B 级）+ 年报历史披露转引（C 级双源交叉）；或 ifind。
- **招行年报 PDF 是图片版**，pypdf 抽不出文本；改用巨潮"年度报告摘要"获取双源核对数字。
- **银行 ROE/BVPS 必须用官方披露口径**（ROAE，剔除优先股/永续债）：账面口径系统性低估（招行实证 1.7pp）。底稿用 `roe_reported`/`bvps_reported` 字段。
- **经营现金流±50% 突变检测对银行不适用**：银行 OCF 受同业负债/存贷款节奏影响天然剧烈波动，validate_data 已启用银行旁路。
- **拨备率两种口径兼容**：可直接填披露比率（`npl_ratio`/`provision_coverage`）或填余额反算，compute_metrics_bank 两者都认。
- **估值弃用单阶段 Gordon PB-ROE**：隐含 g 逼近折现率时分母趋零。银行统一用两阶段现价回归（分红折现 + 终期 BVPS×终期 PB）。
- **vchart 校验约束**：图表 data 必须与底稿 JSON 一致且为一维数字数组；`scale=0.01` 时图表亿元、底稿百万。

### 港股（泡泡玛特 + 腾讯全链路验证）

- **H 股/红筹币种口径**：报告币种可能与交易币种不同（`fx_basis` 必填）；老千股特征查频繁合股/供股/配售史（披露易 www1.hkexnews.hk 检索公告史）。
- **营收字段可能缺失**：港股接口利润/现金流/资产完整，部分公司营收为空（腾讯实证）——从财报手工补齐并标 A 级。
- **港币口径陷阱**：港股接口科目为港币（期末汇率折算），与披露人民币值差约 10%——「接口值 ÷ 当年末汇率」反推，年报交叉验证误差 <1%。
- **D&A 缺失**：港股接口无折旧摊销独立科目，从业绩公告"重大非现金开支"取三项叠加。
- **IPO 公司历史 spike 密集**：上市前后同比变动普遍超 50%，`spike_notes` 逐条标注。
- **周期高位警报在消费股同样适用**：泡泡玛特实证净利率为全期均值 1.6 倍被引擎判周期高位强制正常化——消费股爆款周期标准形态。
- **"经营+投资"双轮公司**：腾讯实证（组合 8751 亿 CNY）——DCF 只评估经营业务，组合按折价（上市 9 折/非上市 6 折，或更保守 7/4 折）单独加回（`reverse_dcf.py --add-back/--deduct`）。
- **投资减值年扭曲均值**：腾讯 2023 归母 vs Non-IFRS 差 425 亿——spike_notes 须标注，双口径差异披露。
- **AI 资本开支陡增期 FCF 失真**：腾讯 2026Q2 剔除算力预付款前后 FCF 符号反转——报告需双重口径披露，避免"FCF 崩塌"误读。
- **回购是重要估值支撑**：持续大额回购注销（腾讯 2024 千亿级 HKD 实证）在估值中应作为显性因素。

## 八、电话会与管理层一手信息

- 美股：公司 IR 页面 transcript/webcast 文字稿、SEC 8-K 附带 prepared remarks（A 级）、Motley Fool 免费 transcript（B 级）。
- A股/港股：业绩说明会实录（上证e互动/深交所互动易/公司公告）。
- 媒体转述只作兜底（C 级）。言行比对必须基于管理层原话——转述会丢失措辞变化这个最重要的信号。

## 九、源冲突裁决规则（REQ-P0-07）

同一科目在不同源之间不一致时，差异本身是信息（可能是口径差，也可能是公司修改过披露——后者是排雷线索）。

**源优先级**（由高到低；`crosscheck[].source_tier` 可显式写下列 key，缺省时按 source 文本推断）：

1. 监管官方原文（EDGAR XBRL / 巨潮年报 PDF / 披露易年报 / EDINET·決算短信）——`edgar_xbrl`（美股）/ `cninfo_pdf`（A股）/ `hkex_pdf`（港股）/ `edinet_pdf`（日股）
2. 公司官网原文（IR 页年报/公告/数据下载）——`company_ir`
3. A 级接口数据（westock-data / ifind-finance-data）——`westock` / `ifind`
4. B 级二手数据（研报/Wind 截图）——`research_report` / `wind_screenshot`
5. C 级兜底（web_search / 媒体报道）——`web_search` / `media`

**tier 1 的 key 须与辖区一致**——`edgar_xbrl` 仅限美股，港股/A股/日股标它即错（辖区错误会掩盖真实出处）。

**转引否决前置**（REQ-P0-09，veto 先于一切档位扫描）：含「转引/转载/援引/引自/摘自/报道」且无可核验官方指针时直接判 tier 5——「年报」在任何转引描述里都必然出现，不得据此升为官方源。例外（不降级）：①含监管 URL/公告编号/渠道锚；②明说已「弃用/不符/未采纳」；③官方为主源、接口仅作第二独立源。

**推断算法**（`crosscheck_official.source_tier`，P0-1 统一内核，`validate_data`/`verification_strength` 均转发）：① veto 前置；② 强锚词（10-K/EDGAR/XBRL/companyfacts/巨潮/披露易/HKEX/EDINET/交易所/accession 等）命中且无 veto 锁 tier 1；③ 弱官方词仅贡献 tier 1 候选，遇渠道词让位取 **max**——渠道才是真实出处；④ 无命中判 tier 5。体裁词孤证不升 tier 1。`is_official_source` = `source_tier(text) <= 2`。

> 显式 `source_tier` **短路**推断，写错即固化。批量补录须复核辖区；守卫测试须传 `source` 文本走真实推断。显式值比文本推断标得更高时 `--audit` 报错（OBS-META-12：显式值优先的短路必须独立校验显式值本身）。

**EDGAR 抽取统一内核**（P0-3/AST-005）：`edgar_facts.py` 是唯一抽取口径（`extract_edgar_annual.py` 与 `crosscheck_official.edgar_annual` 均转发）——form 白名单（10-K/20-F 及 /A）、期间 300-400 天、按 `end` 归年（1-5 月归前年）、比率单位剔除、`prefer="latest"|"first"` 重述口径 + `--cutoff` 时点截断（REQ-P0-06）、逐年独立概念回退（B4）。**[E:] 指针可达性门禁**（P0-2）见 report-spec.md [E:] 规则④。

**差异阈值与处置**（`crosscheck_official.tol_for()` 单点实现，`validate_data.py` 从它 import，不各自维护）：

| 科目类型 | 阈值 | 处置 |
|---|---|---|
| **命门科目**（revenue / net_income / ocf / shares_diluted，外加 cash / interest_bearing_debt / total_debt） | >1% | **阻断**（`crosscheck_official.py` 退出码 1，`validate_data.py` ERROR）；确认口径差异时在 `crosscheck_exempt` 写五要素豁免并披露 |
| 资产负债表科目（total_assets / total_equity / total_liabilities / goodwill / inventory / receivables 等） | >3% | **告警**并登记差异表进报告附录 |
| 其他科目 | >5% | **登记**进差异表 |

crosscheck 条目中登记的**任何**数值科目都参与分级比对，不只命门四科目；EDGAR 自动与 `--audit` 人工转录走同一裁决函数。

**裁决动作**：
- 发现差异后，以 tier 更高的源为准更新底稿。豁免须结构化五要素：`"crosscheck_exempt": {"<field>": {"adopted_value", "adopted_source", "rejected_value", "rejected_source", "reason"}}`——纯字符串豁免仍被接受（legacy）但每次提示迁移。
- 差异表由 `crosscheck_official.py --write` 落盘到底稿 `crosscheck_conflicts`；报告侧披露走 `data-appendix` 机器标记，清单见 `report-spec.md`。

## 十、新增数据源的接入清单

想接第四个源时按此走，不要直接改脚本：

1. 确认它能填的底稿字段，对照 `compute_metrics.py` 头注释的 schema；
2. 在上方能力矩阵加一行，写清授权模式与覆盖范围；
3. 在 `check_data_sources.py` 的 `SOURCES` 注册探测方式；
4. 跑一个真实公司全流程，把踩坑固化到本文件对应市场节；
5. **不要**为某个源在脚本里加分支——若发现非改不可，说明底稿 schema 抽象漏了，应该改 schema。
