# 数据源与采集手册（Phase 1）

SKILL.md 只保留数据分级与降级协议的原则；**源能力矩阵、强制动作、各市场实操与踩坑记录全部集中在本文件**。本文件提到的「接口」除特别说明外均指 `westock-data`（腾讯自选股，推荐默认源）。**每次实跑踩到新坑，固化到这里，不要写回 SKILL.md。**

已实证覆盖：美股（NVDA/AMD/INTC/AVGO + 中概 PDD）、A股（海天采集链路 + 伊利全流程 + 招行银行管道）、港股（泡泡玛特 + 腾讯全流程）。尚未实证：保险/券商（无专属管道，须手工建稿）、周期低位真实标的（引擎逻辑有合成数据测试，未遇到真实底部周期股）。

## 一、核心原则：不绑定单一源

**唯一契约是 `data/*.json` 底稿**，不是某个接口。计算层（`scripts/` 下脚本）零网络依赖，只消费底稿 JSON；取数在 Agent 侧、计算在脚本侧，中间用底稿解耦。**没有任何单一源能覆盖十年×三市场×十一类商业模式**，流程必须接受"多源拼装 + 双源核对 + A/B/C 分级"。三条硬约束：

1. **换源不改脚本**：新源只要能填出底稿 schema，计算与校验链路完全复用。
2. **缺源不等于不能分析**，但必须明确降级并承担工作量（见各市场节）。
3. **同一公司用不同源，数字可能不同**（港币折算差约 10%、现金口径宽窄差均实证过），所以 `manifest.json` 必须登记本次实际用了哪些源。

## 二、能力矩阵

| 源 | 形态 | 授权 | 覆盖 | 本 skill 中的定位 |
|---|---|---|---|---|
| **westock-data**（腾讯自选股）| 平级 skill，`node <skill目录>/scripts/index.js <子命令>` | **免费、无需 key**，需 Node ≥ 18 + 网络 | A股/港股/美股/日韩股 + ETF/指数/板块/期货/外汇/可转债；三表财报、行情、K线、一致预期、研报、公告、股东、分红、事件、龙虎榜、产业链图谱、宏观 | **推荐默认**。已实证四条管道（美股 NVDA 系 / A股伊利 / 港股泡泡玛特+腾讯 / A股银行招行）。命中其能力域时禁止用 web_search 替代 |
| **SEC EDGAR** | 官方 HTTP JSON API | 免费、免鉴权（需 User-Agent 头）| 美股全历史 XBRL 财务事实、10-K/10-Q/20-F/6-K 原文 | **美股永久兜底且仍算 A 级**。`scripts/extract_edgar_annual.py` 直连。命门科目双源核对首选 |
| **巨潮资讯网** | 官方网站 | 免费 | A股年报原文 PDF、处罚记录、问询函 | **A股命门科目双源核对强制走它**（原文优先于任何接口）|
| **港交所披露易** | 官方网站 | 免费 | 港股年报/公告原文、合股供股配售史 | **港股原文核对 + 老千股特征排查** |
| **ifind-finance-data**（同花顺）| 平级 skill，安装见[官方指南](https://mcp.51ifind.com/gwstatic/static/ds_web/ifind-mcp-web/skills/SKILL_INSTALL_GUIDE.md) | **付费**，需自备 key 写入 `mcp_config.json`（[密钥管理](https://mcp.51ifind.com)）| A股/港股/美股行情财报、行业与宏观 | **可选增强**，不作默认。有 key 时优先用于补 A 股 capex/D&A 与银行专属科目；无 key 完全不影响主流程 |
| **机构研报** | B 级二手 | — | 历史 capex 序列、行业数据、竞争格局 | 补接口缺口。**只取事实，不取评级与目标价** |
| **web_search / web-fetch** | C 级兜底 | — | 媒体报道、访谈、行业新闻 | 仅作旁证，必须标来源链接。**影响结论的核心判断禁止只靠 C 级** |

## 三、强制动作（先于采集）

1. **Phase 1 开始前跑一次源探测**：`python3 scripts/check_data_sources.py`。输出本机可用源、覆盖能力与缺口应对提示。缺推荐源时脚本会给出安装引导——**不要静默降级**。
2. **`manifest.json` 登记 `data_sources`**：数组，逐项写 `{"source": "westock-data", "version": "1.0.6", "used_for": ["三表","行情","一致预期"], "level": "A"}`。同一公司换源重跑数字对不上时，第一件事查这里。
3. **命门科目双源核对不可用接口自证**：收入/归母净利/经营现金流/总股本最近 3 年必须与官方披露原文核对，跑 `scripts/crosscheck_official.py`。**接口 ≠ 原文，即使接口是 A 级**：

   | 市场 | 官方源形态 | 核对方式 |
   |---|---|---|
   | 美股 | EDGAR companyfacts（XBRL，结构化、免鉴权）| **机器核对**：`--companyfacts <file>` 自动取官方值逐年比对，`--write` 回写 crosscheck。不经人手转录 |
   | A股 | 巨潮年报 PDF（非结构化）| **人工转录 + `--audit` 体检完整性** |
   | 港股 | 披露易年报 PDF（非结构化）| 同上 |

   美股必须机器核对的理由：手抄进 `crosscheck` 与手填进 `annual` 的值来自同一次阅读，比对的是"我抄得一致吗"而不是"接口对不对"——同人同眼，看错年份不会被发现。EDGAR 有结构化 XBRL，能真正独立取数，就不该退回人工。

   **A4 规则**：强制科目的官方值为空＝该科目实际未被交叉核对，**直接报错**而非告警（`shares_diluted` 是每股序列的分母，未核对会线性缩放整个估值与安全边际）。确无法取得时写 `crosscheck_exempt` 显式豁免并进报告披露。

   **多市场股本公司**：`shares_diluted` 一律用总股本，不得用单一市场上市股本（否则每股序列在口径切换年机械跳变）。

## 四、行情复权口径纪律（三口径使用矩阵）

| 口径 | 东财参数 | 语义 | **唯一合法用途** | 禁止用途 |
|---|---|---|---|---|
| 等比后复权 | `fqt=2` | 乘法调整，分红再投资，跨期可比 | **收益/总回报计算**（actual_Ny_total_return 一律用它） | — |
| 不复权 | `fqt=0` | 当年真实成交价 | **触及检验、内在价值比较、与当年披露 BPS/股价运算** | 长区间收益计算 |
| 等差前复权 | `fqt=1` | 减法调整（P_raw − 累计分红） | **仅形态展示、除息序列反推** | **严禁**用于收益计算（高估）、触及检验（前复权价随分红整体下移产生虚假触及） |

三条配套：① `answer.json` 的 `price_basis.source` 强制声明口径（含抓取渠道与日期），runner 对含 `actual_*_total_return` 但缺 `price_basis` 的 answer 出咨询提示；② 后复权跨批次抓取应完全一致（自检方法：无基准漂移），前复权因子随分红重算、跨批次不可比；③ A/H 多市场价时，触及检验用对应市场不复权价、收益用对应市场后复权价，不混用。

## 五、比率类字段命名纪律（强制，量纲哨兵已固化进 check_scenarios S9）

**所有比率类字段（股息率、分位、占比、增速）落盘时必须带单位后缀：`_frac` = 小数，`_pct` = 百分数。** 裸字段名一律按小数解释，且值不得 > 0.20。归档底稿实证（同一字段名 `dividend_yield_ttm`，单位不统一）：

| 案例 | 字段 | 值 | 实际含义 |
|---|---|---|---|
| 招行 | `dividend_yield_ttm` | 0.0511 | 5.11%（小数） |
| 腾讯 | `dividend_yield_ttm` | 1.17 | 1.17%（百分数，另给了 `_frac` 消歧） |
| 伊利 | `dividend_yield_ttm` | 5.14 | 5.14%（百分数，另给了 `_frac` 消歧） |
| 英伟达 | `dividend_yield_ttm_pct` | 0.13 | **0.13%**，不是 13% |

闸门二的**价值不收敛下限 = 股息率 + 内在价值增速**把股息率当加数直接参与判定——英伟达的 0.13% 若被读成 13%，闸门二直接自动过闸。这是典型的「算得出数、不报错、无痕迹」静默错误，因此升级为门禁：`check_scenarios.py` S9 要求 `dividend_yield` 必须是小数且 ≤20%；传 `--snapshot` 时与快照归一化值交叉核对（容差 5%）。归一化规则（`normalize_yield`）：后缀是强证据，裸字段值 > 0.20 判为百分数误填并标记**歧义**——脚本会归一但同时告警，要求分析师用后缀显式消歧。

## 六、信息覆盖度强制动作

0. **搬运完整性（最隐蔽的事故）**：抽取产物拿到了、底稿却没搬进去——比"数据源没有"更危险，因为分析师以为自己有。事故原型：GOOG/TSM 的 raw 产物 9 个年度全抓到，底稿却只有 1 年有值，勾稽覆盖率 18% 而入口校验照打"通过"（从 raw 回填后 91%，数据本身是好的、只是从未被验证）。纪律：凡有抽取中间产物必须跑 `scripts/check_transcription.py` 比对；建稿脚本化优先于手工转录。附带：`cash` 取"现金+短期投资"（`cash_sti`）是估值正确口径，不要为"过校验"改狭义。
1. **分部数据只认财报原文**：`data/segments.json` 的分部收入必须从年报/20-F 的**分部报告附注**提取（A 级，登记 filings 文件名+页码）；接口主营构成与新闻转述只作交叉验证。五维定性一半论断压在分部数据上，这里降级等于全楼地基降级。
2. **对立面检索（Phase 0 排雷强制步）**：逐项检索 `公司名 + 做空报告/财务造假/监管处罚/集体诉讼/审计意见`，命中进排雷清单评估；未命中也要在 manifest 登记"对立面检索已做、无发现"（`adversarial_check`，含检索日期与结论；validate_data 无痕迹即告警——纯文档纪律没有执行力，10 个归档案例只有 1 个留了痕）。
3. **信息时效检查**：分析日距最新财报披露日超过 100 天时（validate_data 会提示），强制核对最新季报/盈利预告是否有未消化的剧变（腾讯 AI capex +176% 是季中爆出的教训），核对结果写入 manifest 的 `latest_quarter_checked`。
4. **来源措辞要精确**：`crosscheck.source` 里官方原文标识（10-K/20-F/年报/EDGAR/巨潮/披露易/XBRL）是强证据；混入"接口/加总/估算"会触发警告。写"20-F p.45 表 3"这类定位，不写"20-F 披露接口值"。

## 七、各市场实操与踩坑

### 美股（已实证：NVDA/AMD/INTC/AVGO + 中概 PDD）

- 首选 `westock-data`；不可用或字段缺失时，直接用 SEC EDGAR 官方 JSON API（仍算 A 级）：
  - 全历史 XBRL 财务事实：`https://data.sec.gov/api/xbrl/companyfacts/CIK{10位CIK}.json`
  - CIK 查询：`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=<名称>&output=atom`
  - 请求需带 User-Agent 头（任意联系方式格式），无需鉴权。
- 踩坑：companyfacts 早年 capex 标签可能缺失（NVDA FY2016-2021），回 10-K 原文补（存档 `data/filings/`）；财年错位（NVDA 1月末、AVGO 10月末）竞对图须脚注；AVGO NCI 致勾稽失败时 `total_equity` 用含 NCI 口径；`publish_date` 用 filing date。
- **中概美股（PDD 实证）**：Futu 通道字段完整（Sales/NetIncome/CFO/Capex/FreeCF 可用）；**币种双口径**（`DisclosureCurrency=CNY`/`ShowCurrency=USD`）须按汇率与 20-F 人民币值反核；**现金口径**接口宽于披露（PDD 710 亿 vs 630 亿），统一到披露口径；**利息收入剔除**（PDD 占税前 19%，从经营 OE 剔除）；**爬坡期正常化失真**（早年亏损拉低全期均值，双轨披露）。

### A股（已实证：海天链路 + 伊利全流程）

- `westock-data` 三表可直接建底稿：income/balance/cashflow 全拉，报告期取 12-31 年报行，单位元→百万。
- 踩坑：**权益科目用 `TotalShareholderEquity`（含少数股东）而非 `SEWithoutMI`**（否则勾稽过不了），回报率与估值只认归母（另存 `roe_parent`/`bvps_parent`）；**TotalAssets 缺失**用 `TotalLiability+TotalShareholderEquity` 推导（误差<0.1%）；**capex 无独立科目**（只有净额 NetInvestCashFlow），从年报原文（巨潮 PDF）或研报序列补、标 B 级，补不到时 `compute_metrics` 用 D&A 兜底进 warnings（须报告确认披露）；缺摊薄股本同样从年报补；字段名与港股不同（归母净利 `NPParentCompanyOwners`/经营现金流 `NetOperateCashFlow`/营业成本 `OperatingCost`），营收完整（`OperatingRevenue`）；`publish_date` 用 `InfoPublDate`。
- **减值年必须标注**：大额一次性减值年份（伊利 2024 年 52.3 亿）须在 spike_notes 说明"还原后真实增长"，否则次年高增长被突变检测误报、基期被低基数扭曲。成熟公司 spike 约 18 条，IPO 高增长 40+ 条。
- 命门科目双源核对强制走巨潮年报原文；处罚记录与问询函也查巨潮。

### A股银行（招行全链路验证）

- **接口缺银行专属科目**：`westock-data` 三表不提供不良/拨备/NIM/资本充足率。补齐路径：标普信评"债券通评级报告"（附录有连续 5 年完整银行指标表，B 级，中文免费取）+ 早年用年报历史披露的公开转引（C 级，需双源交叉）；或用 ifind 补。
- **招行年报 PDF 是图片版**，pypdf 抽不出文本；改用巨潮"年度报告摘要"（文字版 PDF）获取双源核对数字。
- **银行 ROE/BVPS 必须用官方披露口径**（ROAE，剔除优先股/永续债）：账面口径会系统性低估 1.7 个百分点（招行 2025：披露 13.44% vs 账面 11.7%）。底稿用 `roe_reported`/`bvps_reported` 字段。
- **经营现金流±50% 突变检测对银行不适用**：银行 OCF 受同业负债/存贷款节奏影响天然剧烈波动，validate_data 已启用银行旁路。
- **拨备率两种口径兼容**：可直接填披露比率（`npl_ratio`/`provision_coverage`）或填余额反算，compute_metrics_bank 两者都认。
- **估值弃用单阶段 Gordon PB-ROE**：可持续 ROE×(1−分红率) 的隐含 g 逼近折现率时分母趋零（招行算出过 +141% 公允价）。银行统一用两阶段现价回归（分红折现 + 终期 BVPS×终期 PB）。
- **vchart 校验约束**：ECharts 图表 data 必须与底稿 JSON 数组完全一致且为一维数字数组；带 `scale=0.01` 时图表显示亿元、底稿存百万。

### 港股（泡泡玛特 + 腾讯全链路验证）

- **H 股/红筹币种口径**：报告币种可能与交易币种不同（`fx_basis` 必填）；老千股特征查频繁合股/供股/配售史（披露易 www1.hkexnews.hk 检索公告史）。
- **营收字段可能缺失**：港股接口利润/现金流/资产字段完整，但部分公司营收字段为空（腾讯 OperatingRevenue/Sales 均无；泡泡玛特正常）——从财报手工补齐并标 A 级。
- **港币口径陷阱**：港股接口利润表/资产负债表科目为港币（期末汇率从人民币折算），与披露人民币值差约 10%——「接口值 ÷ 当年末汇率」反推，年报公告交叉验证误差 <1%。
- **D&A 缺失**：港股接口无折旧摊销独立科目，从业绩公告"重大非现金开支"部分取三项叠加（物业厂房设备折旧+使用权资产折旧+无形资产摊销）。
- **IPO 公司历史 spike 密集**：泡泡玛特 2018-2020 同比变动普遍超 50%，`spike_notes` 逐条标注，工作量是成熟公司 3 倍。
- **周期高位警报在消费股同样适用**：泡泡玛特 2025 净利率 34.4% 是全期均值 21.5% 的 1.60 倍，引擎自动判周期高位并强制正常化（92 亿而非峰值 128 亿）——消费股爆款周期的标准形态。
- **"经营+投资"双轮公司**：腾讯投资组合 8751 亿 CNY——DCF 只评估经营业务，组合按折价（上市 9 折/非上市 6 折，或更保守 7/4 折）单独加回（`reverse_dcf.py --add-back/--deduct`）。
- **投资减值年扭曲均值**：腾讯 2023 归母 1152 亿（公允下修+联营减值）vs Non-IFRS 1577 亿——spike_notes 须标注，双口径差异披露。
- **AI 资本开支陡增期 FCF 失真**：2026Q2 FCF 转负 -138 亿（capex +176%），剔除算力预付款后 +376 亿——报告需双重口径披露，避免"FCF 崩塌"误读。
- **回购是重要估值支撑**：腾讯 2024 年回购 1120 亿 HKD、2025 年约 1300 亿——持续大额回购注销在估值中应作为显性因素。

## 八、电话会与管理层一手信息

- 美股：公司 IR 页面 transcript/webcast 文字稿、SEC 8-K 附带 prepared remarks（A 级）、Motley Fool 免费 transcript（B 级）。
- A股/港股：业绩说明会实录（上证e互动/深交所互动易/公司公告）。
- 媒体转述只作兜底（C 级）。言行比对必须基于管理层原话——转述会丢失措辞变化这个最重要的信号。

## 九、源冲突裁决规则（REQ-P0-07）

同一科目在不同源之间不一致时，差异本身是信息（可能是口径差，也可能是公司修改过披露——后者是排雷线索）。规则：

**源优先级**（由高到低；`crosscheck[].source_tier` 可显式写下列 key，缺省时脚本按 source 文本关键词推断）：

1. 监管官方原文（EDGAR XBRL / 巨潮年报 PDF / 披露易年报）——`edgar_xbrl` / `cninfo_pdf` / `hkex_pdf`
2. 公司官网原文（IR 页年报/公告/数据下载）——`company_ir`
3. A 级接口数据（westock-data / ifind-finance-data）——`westock` / `ifind`
4. B 级二手数据（研报/Wind 截图）——`research_report` / `wind_screenshot`
5. C 级兜底（web_search / 媒体报道）——`web_search` / `media`

**差异阈值与处置**（`crosscheck_official.tol_for()` 单点实现，`validate_data.py` 从它 import，不各自维护）：

| 科目类型 | 阈值 | 处置 |
|---|---|---|
| **命门科目**（revenue / net_income / ocf / shares_diluted，外加 cash / interest_bearing_debt / total_debt） | >1% | **阻断**（`crosscheck_official.py` 退出码 1，`validate_data.py` ERROR）。确认为口径差异时在 `crosscheck_exempt` 写五要素结构化豁免并在报告披露 |
| 资产负债表科目（total_assets / total_equity / total_liabilities / goodwill / inventory / receivables 等） | >3% | **告警**并登记差异表进报告附录 |
| 其他科目 | >5% | **登记**进差异表 |

crosscheck 条目中登记的**任何**数值科目都参与分级比对，不只命门四科目；两种模式（EDGAR 自动 / `--audit` 人工转录）走同一裁决函数。

**裁决动作**：
- 发现差异后，以 tier 更高的源为准更新底稿。豁免必须是结构化五要素：`"crosscheck_exempt": {"<field>": {"adopted_value", "adopted_source", "rejected_value", "rejected_source", "reason"}}`——纯字符串豁免仍被接受（legacy）但每次运行都会提示迁移。
- 差异表（含已裁决与未裁决）由 `crosscheck_official.py --write` 落盘到底稿 `crosscheck_conflicts`（字段：year / field / annual_value / official_value / annual_tier / official_tier / diff_pct / severity / adopted_side / resolved / resolution）。
- 报告「数据附录」节须列出差异表并带机器标记 `data-appendix="source-conflicts"`；底稿含非空 `crosscheck_conflicts`（或结构化豁免中有 `rejected_value`）而报告缺该标记时，`verify_report.py` 直接 FAIL。同理，含 `restated_from` 的底稿须带 `data-appendix="restatements"`，含 `meta.point_in_time_waiver` 的须带 `data-appendix="point-in-time-waiver"`（REQ-P0-06）。

## 十、新增数据源的接入清单

想接第四个源时按此走，不要直接改脚本：

1. 确认它能填的底稿字段，对照 `compute_metrics.py` 头注释的 schema；
2. 在上方能力矩阵加一行，写清授权模式与覆盖范围；
3. 在 `check_data_sources.py` 的 `SOURCES` 注册探测方式；
4. 跑一个真实公司全流程，把踩坑固化到本文件对应市场节；
5. **不要**为某个源在脚本里加分支——若发现非改不可，说明底稿 schema 抽象漏了，应该改 schema。
