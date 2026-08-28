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

## 电话会与管理层一手信息

- 美股：公司 IR 页面 transcript/webcast 文字稿、SEC 8-K 附带 prepared remarks（A 级）、Motley Fool 免费 transcript（B 级）。
- A股/港股：业绩说明会实录（上证e互动/深交所互动易/公司公告）。
- 媒体转述只作兜底（C 级）。言行比对必须基于管理层原话——转述会丢失措辞变化这个最重要的信号。
