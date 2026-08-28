# 数据采集市场手册（Phase 1 数据源细节）

SKILL.md 只保留数据分级与降级协议的原则，各市场的实操路径与踩坑记录集中在本文件。**每次实跑踩到新坑，固化到这里，不要写回 SKILL.md。**

## 美股（已实证：NVDA/AMD/INTC/AVGO，2026-08）

- 首选结构化金融数据接口；不可用或字段缺失时，直接用 SEC EDGAR 官方 JSON API（仍算 A 级）：
  - 全历史 XBRL 财务事实：`https://data.sec.gov/api/xbrl/companyfacts/CIK{10位CIK}.json`
  - CIK 查询：`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=<名称>&output=atom`
  - 请求需带 User-Agent 头（任意联系方式格式），无需鉴权。
- 踩坑记录：
  - companyfacts 早年 capex 标签可能缺失（NVDA FY2016-2021），需回 10-K 原文合并现金流量表补，原文存档 `data/filings/`。
  - 财年错位公司（NVDA 1月末财年、AVGO 10月末）在竞对对比图必须脚注注明。
  - 少数股东权益（AVGO NCI）会导致三表勾稽失败，`total_equity` 用含 NCI 口径。
  - `publish_date` 用 filing date。

## A股（已实证：海天味业，2026-08，采集链路）

- 结构化接口的三表数据可直接建底稿：income/balance/cashflow 全拉，报告期取 12-31 年报行，单位元→百万。
- 踩坑记录：
  - **权益科目必须用 `TotalShareholderEquity`（含少数股东），不能用 `SEWithoutMI`（归母口径）**，否则三表勾稽过不了。
  - 接口通常缺 D&A/capex/摊薄股本明细，须从年报现金流量表补充（巨潮 PDF）；补不到时 D&A 可由脚本兜底，但须在 warnings 中确认。
  - `publish_date` 用接口的 `InfoPublDate` 字段，现成可用。
- 命门科目双源核对强制走巨潮年报原文；处罚记录与问询函也查巨潮。

## 港股（未实证——诚实声明）

- 美股与 A 股链路均实跑验证过，港股的"结构化接口 + 披露易（www1.hkexnews.hk）原文核对"整链**尚未走通过一次**。
- 首次分析港股标的时按实证模式操作：每一步与预期不符即记录（披露易 PDF 检索方式、财报科目命名、币种/股本口径坑点），完成后把要点固化到本节，并在该次报告附录声明"港股链路首次实证"。
- 已知注意点（未验证）：H 股/红筹的报告币种可能与交易币种不同（fx_basis 必填）；老千股特征查频繁合股/供股/配售史。

## A股银行实证要点（招商银行全链路验证过，2026-08-28）

- **接口缺银行专属科目**：westock finance 三表只有营收/归母净利/总权益/贷款/存款/经营现金流，**不提供不良余额/拨备/NIM/资本充足率/减值计提**。补齐路径：标普信评"债券通评级报告"（附录有连续 5 年完整银行指标表，B 级，中文免费取）+ 早年用年报历史披露的公开转引（C 级，需双源交叉）。
- **招行年报 PDF 是图片版**，pypdf 抽不出文本；改用巨潮"年度报告摘要"（文字版 PDF）获取双源核对数字，正文关键审计事项在港交所披露的审计报告中可读。
- **银行 ROE/BVPS 必须用官方披露口径**（ROAE，剔除优先股/永续债）：含少数股东权益+其他权益工具的账面口径会系统性低估 1.7 个百分点（招行 2025：披露 13.44% vs 账面 11.7%）。底稿用 `roe_reported` / `bvps_reported` 字段，compute_metrics_bank 优先采用。
- **经营现金流±50% 突变检测对银行不适用**：银行 OCF 受同业负债/存贷款节奏影响天然剧烈波动（招行间 -357 亿到 +5701 亿），validate_data 已启用银行旁路跳过该检测（v2.3 实证新增）。
- **拨备率两种口径兼容**：底稿可直接填披露比率（`npl_ratio`/`provision_coverage`）或填余额（`npl_balance`/`provision_balance` 反算），compute_metrics_bank 两者都认。
- **估值弃用单阶段 Gordon PB-ROE**：可持续 ROE×(1−分红率) 的隐含 g 逼近折现率时分母趋零（招行算出过 +141% 公允价）。银行统一用两阶段现价回归（分红折现 + 终期 BVPS×终期 PB）。
- **vchart 校验约束**：ECharts 图表 data 必须与底稿 JSON 数组完全一致且为一维数字数组；二维坐标（[[x,y],...]）和嵌套数组无法被校验器解析。

## 电话会与管理层一手信息

- 美股：公司 IR 页面 transcript/webcast 文字稿、SEC 8-K 附带 prepared remarks（A 级）、Motley Fool 免费 transcript（B 级）。
- A股/港股：业绩说明会实录（上证e互动/深交所互动易/公司公告）。
- 媒体转述只作兜底（C 级）。言行比对必须基于管理层原话——转述会丢失措辞变化这个最重要的信号。
