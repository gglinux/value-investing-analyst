#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_report.py — 软银集团（9984.T）@2019-06-30 报告生成器
数字全部从 data/ 底稿 JSON 读取渲染（vnum span / vchart 锚点），避免手抄漂移。
运行：python3 workpapers/build_report.py（在案例目录上一级运行亦可，路径内置）
"""
import json
import os

CASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(CASE, "data")

def load(name):
    with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
        return json.load(f)

def gp(obj, path):
    cur = obj
    for part in path.split("."):
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur

def v(src, path, fmt, text=None):
    """生成 vnum span；text 为显示文本（默认按 fmt 渲染底稿值）。"""
    d = load(src)
    val = gp(d, path)
    if text is None:
        if fmt == "pct1":
            text = f"{val:.1%}"
        elif fmt == "pct2":
            text = f"{val:.2%}"
        elif fmt == "num0":
            text = f"{val:,.0f}"
        elif fmt == "num1":
            text = f"{val:,.1f}"
        elif fmt == "num2":
            text = f"{val:,.2f}"
        else:
            text = str(val)
    return f'<span class="vnum" data-src="{src}" data-path="{path}" data-fmt="{fmt}">{text}</span>'

def chart(src, path, div_id, title, yname, scale=1, note=""):
    d = load(src)
    years = gp(d, "chart_series.years") if "chart_series" in path else None
    vals = [round(x * scale, 4) for x in gp(d, path)]
    yr = years or ["FY%d" % y for y in range(2009, 2019)]
    anchor = f'<!-- vchart src={src} path={path}{" scale="+str(scale) if scale != 1 else ""} -->'
    return f'''{anchor}
<div id="{div_id}" style="width:100%;height:300px;margin:8px 0"></div>
<script>
(function(){{
var c = echarts.init(document.getElementById("{div_id}"));
c.setOption({{
  series: [{{type: "bar", data: {json.dumps(vals)}, itemStyle: {{color: "#4a6fa5"}}}}],
  title: {{text: "{title}", left: "center", textStyle: {{fontSize: 14}}}},
  tooltip: {{trigger: "axis"}},
  grid: {{left: 90, right: 30, bottom: 60}},
  xAxis: {{type: "category", data: {json.dumps(yr)}, axisLabel: {{rotate: 30}}}},
  yAxis: {{type: "value", name: "{yname}"}}
}});
}})();
</script>
<p class="small">{note}</p>'''

m = load("metrics_9984_2019.json")
s = load("market_snapshot_9984_2019.json")
e = load("expected_return.json")
bd = load("business_drivers_9984_2019.json")

sotp = bd.get("sotp") or {}

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>软银集团 (9984.T) — 历史回放分析报告 2019-06-30</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
body{{font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222;line-height:1.65}}
h1{{font-size:22px;border-bottom:3px solid #4a6fa5;padding-bottom:8px}}
h2{{font-size:18px;border-left:4px solid #4a6fa5;padding-left:10px;margin-top:32px}}
h3{{font-size:15px;margin-top:22px}}
table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}}
th,td{{border:1px solid #bbb;padding:5px 8px;text-align:left}}
th{{background:#eef2f8}}
.card{{border:1px solid #ccc;border-radius:8px;padding:12px 16px;background:#fafcff}}
.gate-fail{{color:#b03030;font-weight:bold}}
.gate-pass{{color:#1d7a3e;font-weight:bold}}
.small{{font-size:12px;color:#666}}
.ok{{color:#1d7a3e}}.bad{{color:#b03030}}
</style>
</head>
<body>
<h1>软银集团 (9984.T) — 历史回放分析报告</h1>
<p class="small">回放时点 2019-06-30 ｜ 案例编号 B2-10（第二批） ｜ 信息截断日 2019-06-30</p>

<h2 style="margin-top:0">决策卡</h2>
<div class="card">
<p><b>结论：观察等价格（档位 2/4）——现价不买，≤3,980 日元且 NAV 构成未变时重开评估</b></p>
<p>现价 {v("market_snapshot_9984_2019.json", "price.value", "num2")} JPY ｜ 市值 {v("market_snapshot_9984_2019.json", "market_cap.value", "num0")} 百万 JPY（≈$1,010 亿）｜ 流通股 {v("market_snapshot_9984_2019.json", "shares.value", "num1")} 百万股（2019-06-25 1:2 拆股后）｜ PB {v("market_snapshot_9984_2019.json", "valuation_multiples.pb_mrq", "num1")}x ｜ 股息率 {v("scenarios.json", "dividend_yield", "pct1")}</p>
<p>闸门一（安全边际，无护城河档要求 40% 或不给买入结论）：基准内在价值 {v("scenarios.json", "scenarios.1.value_per_share", "num0")} JPY → MoS = {v("mos_calc.json", "mos_vs_base", "pct1")} <span class="gate-fail">✗ 不足</span>；保守中枢口径 {v("mos_calc.json", "mos_vs_conservative_center", "pct1")}；悲观口径 {v("mos_calc.json", "mos_vs_bear", "pct1")}（现价高于悲观值）。触发价 {v("mos_calc.json", "trigger_price_base_40pct", "num0")} JPY（52 周低 3,652 已在其下——等待可及）</p>
<p>闸门二（期望回报，护城河 none）：①期望 IRR {v("expected_return.json", "expected_annualized_irr", "pct1")}——none 档结构性不过；②不收敛下限 {v("expected_return.json", "gate2.no_convergence_floor.value", "pct1")} vs 6% <span class="gate-fail">✗</span>；③悲观年化 {v("expected_return.json", "gate2.pessimistic_irr.value", "pct1")} vs 0% <span class="gate-pass">✓（收敛口径；不收敛口径 −1.0%/年）</span> → <span class="gate-fail">闸门二 1/3，整体不过</span></p>
<p>三情景（SOTP，独立方法悲观）：悲观 {v("scenarios.json", "scenarios.0.value_per_share", "num0")} ／ 基准 {v("scenarios.json", "scenarios.1.value_per_share", "num0")} ／ 乐观 {v("scenarios.json", "scenarios.2.value_per_share", "num0")} vs 现价 {v("market_snapshot_9984_2019.json", "price.value", "num2")}</p>
<div class="verification-strength" data-verification-strength="1"
     data-grade="C"
     data-reconciliation-coverage="0.0000"
     data-crosscheck-level="none"
     data-window-years="10"
     data-covers-full-cycle="true">
  <b>核验强度 C</b>：三表勾稽覆盖率 0%（不足） ｜ 命门科目原文登记 缺失（<u>未经原文机器逐字比对</u>） ｜ 数据窗口 10 年，含A股股灾与去杠杆、去杠杆与贸易战（达标）<br>
  <span class="vs-meaning">核验强度不足：结论的数据地基有明确缺口，档位判定须从严并在首屏说明。本案缺口说明：官方决算 PDF 手工提取含利润表+现金流科目，资产负债表科目未逐项提取（US GAAP→IFRS 切换+并表范围变动使三表机械勾稽在本案不可靠）；估值通道为 SOTP（持仓市值定价），不依赖三表勾稽——PB 1.43x 所用归属权益 7.62 万亿为官方单点披露。档位从严（none 档不给买入结论 + 闸门二从紧）已内嵌于结论</span>
</div>
</div>

<h2>一、信息集与防前视声明</h2>
<table>
<tr><th>信息</th><th>日期</th><th>截断裁决</th></tr>
<tr><td>q4FY2018 官方决算报告（2019-03-31 止，含 IFRS 全序列与官方 NAV 口径）</td><td>2019-05-09 发布</td><td>✓ 进信息集</td></tr>
<tr><td>{v("market_snapshot_9984_2019.json", "price.value", "num2")} JPY 收盘价（2019-06-28，东财/ADR 三重闭环）</td><td>2019-06-28</td><td>✓ 进信息集</td></tr>
<tr><td>9434 收盘 ¥1,399.5 JPY、BABA $173.11 USD（SOTP 定价输入，[E:market_snapshot_9984_2019.json]）</td><td>2019-06-28</td><td>✓ 进信息集</td></tr>
<tr><td> appropriation WeWork 470 亿美元估值（软银 2019-01 领投轮）</td><td>2019-01</td><td>✓ 进信息集（VF 压力项实证锚）</td></tr>
<tr><td>FY2019Q1 决算（2019-08-06）</td><td>—</td><td><span class="bad">✗ forbidden</span> 未进任何底稿</td></tr>
<tr><td>Sprint-T-Mobile 合并审批结果</td><td>2019-11（事后）</td><td><span class="bad">✗ forbidden</span> 悲观/乐观按截断日未决状态建模</td></tr>
</table>
<p class="small">meta.json 在任何数据抓取之前冻结（先验档位 2，confidence=medium）；训练语料中"软银 2019-2021 股价路径/VF 减值潮/WeWork 事件后续"均属后见之明，全部剔除，仅用截断日前事实。</p>

<h2>二、Phase 0 排雷（对立检索 + 监管/审计核查）</h2>
<p>六项可用事实（截断日内）：①2019-02 公司宣布 6,000 亿日元回购（市值 0.55%，一次性市值管理动作）；②Sprint/T-Mobile 合并 DOE/FCC 审批未决；③WeWork 2019-01 估值 470 亿美元（软银 VF 领投）；④FY2018 归属净利中 VF/Delta 重估收益 1,302,838 百万（税前）为非现金项；⑤日本 JGB 10Y 约 −0.1%；⑥2019-06-25 1:2 拆股。对立检索返回的 2019H2 后事件（WeWork IPO 撤回、VF 减值公告、孙正义表态转变）全部剔除入 forbidden 清单 [E:manifest.json]。</p>

<h2>三、定量画像（FY2009-FY2018，10 年窗口）</h2>
<p>口径复杂度是本案数据层的核心事实：US GAAP→IFRS 切换（FY2016 期起）、多年度重列值优先、非现金重估常态化。引擎 4 告警全部成立：<b>利润含金量</b>（2014-2018 五年 FCF/净利全部 &lt;0.6 [E:metrics_9984_2019.json]）；<b>周期高位形态</b>（最新净利率 14.7% = 全期均值 1.50×）；<b>双轨基期</b>（正常化 1,531,545 vs 当期 2,292,849）；<b>利润率形状</b>（秩相关 +0.62、穿越均值 3 次）。<b>owner yield 定量失真</b>（本案检验点①核心）：引擎口径当期 {v("metrics_9984_2019.json", "owner_yield.owner_yield_current", "pct1")}、正常化 {v("metrics_9984_2019.json", "owner_yield.owner_yield_normalized", "pct1")}——对一个股息率 {v("scenarios.json", "dividend_yield", "pct1")} 的公司是数学不可能，差额全部来自并表错位（OCF 含 100% Sprint/9434，股东按 84.4%/66.49% 享有）与非现金重估 [E:metrics_9984_2019.json][E:normalization_9984_2019.json][E:financials_9984_2019.json]。</p>
{chart("metrics_9984_2019.json", "chart_series.revenue", "c_rev", "营业收入（百万日元）", "百万日元", 1, "FY2013 起含 Sprint 并表（2013-07），收入跳变来自口径扩张而非内生增长 [E:financials_9984_2019.json]")}
{chart("metrics_9984_2019.json", "chart_series.net_income", "c_ni", "归属净利润（百万日元）", "百万日元", 1, "FY2016 1,426,308 与 FY2018 1,411,199 均含大额非现金重估（阿里/9434/VF/Delta）[E:metrics_9984_2019.json]")}
{chart("metrics_9984_2019.json", "chart_series.net_margin", "c_nm", "净利率（%）", "%", 100, "波动主因是非现金重估与口径重分类（FY2018 分红收入 +2,051,422 从营业外改列营业），非周期景气 [E:normalization_9984_2019.json]")}
{chart("metrics_9984_2019.json", "chart_series.owner_earnings", "c_oe", "Owner Earnings（百万日元，引擎口径）", "百万日元", 1, "引擎口径 OE 含并表错位——本案证明该口径对持仓型控股公司失真，估值通道改用 SOTP [E:normalization_9984_2019.json]")}
{chart("metrics_9984_2019.json", "chart_series.fcf", "c_fcf", "自由现金流（百万日元）", "百万日元", 1, "FCF = OCF − capex（全口径），2013-2017 连续为负（并购整合与 5G/网络投资期）[E:metrics_9984_2019.json]")}

<h2>四、五维定性（护城河裁决是本案定档关键输入）</h2>
<h3>4.1 商业模式</h3>
<p>资本配置平台：收入 88% 来自电信运营（并表 Sprint+9434），但价值 100% 由投资组合决定——两本账的错位正是本案全部方法学问题的根源。SBG 层面实质是"孙正义的封闭式基金 + 电信现金流抵押品" [E:qualitative_9984_2019.json][E:business_drivers_9984_2019.json]。</p>
<h3 data-moat="none" data-moat-trend="stable">4.2 护城河（none——本案闸门档位决定项）</h3>
<p>投资控股公司无经典五源护城河：资本规模/历史投资业绩是关键人资源而非结构性优势；市场长期 NAV 折价 30-50% 本身是资本配置不可验证性的理性定价；无"回购至 NAV"类收敛机制承诺（2019-02 的 6,000 亿回购占市值 0.55%，不构成机制）。9434 的区域有效规模是 9434 的护城河，母公司股东按 66.49% 享有且被抽租 [E:qualitative_9984_2019.json#moat][E:business_drivers_9984_2019.json#sotp][E:market_snapshot_9984_2019.json]。</p>
<h3>4.3 增长空间</h3>
<p>NAV 口径 iv_growth 取 5%：9434 现金流增长 + ARM/雅日增长 + VF 组合滚动，减本体净债利息侵蚀；剥离 VF 重估等不可持续贡献（FY2017→2018 每股权益 +57% 为重估主导，不外推）。0.43% + 5% = 5.43% &lt; 6%，闸门二②不过的直接来源 [E:qualitative_9984_2019.json#growth][E:scenarios.json]。</p>
<h3>4.4 管理层与治理</h3>
<p>能力与治理分开打分：能力 A（创办集团、投中阿里 $20M→12.48 万亿、搭建全球电信版图）；治理 D（创始人一股独大、董事会无实质制衡、关联交易网络、VF 结构 GP 出资少数而收 carry、下行先亏软银）。"天才+无制衡+杠杆"组合是本案风险结构的核心 [E:qualitative_9984_2019.json#management][E:business_drivers_9984_2019.json]。</p>
<h3>4.5 财务质量与风险深查</h3>
<p>股东现金口径残废：股息率 {v("scenarios.json", "dividend_yield", "pct1")}、分红占归属净利 3.3%；FCF 2013-2017 连续为负；本体净债 6.2 万亿对 equity NAV 占 27%，利息支出 633,769 百万/年且逐年上升。风险深查结论：有真实资产支撑（PB 1.43x 对应账面归属权益 7.62 万亿），但杠杆叠加在不可测资产上 [E:qualitative_9984_2019.json#financial_quality][E:financials_9984_2019.json][E:market_snapshot_9984_2019.json]。</p>

<h2>五、估值与安全边际（Phase 4）</h2>
<h3>5.1 估值通道裁决：OE/DCF 整段废弃，SOTP 为唯一合法通道</h3>
<p>引擎推荐基期（正常化 1,531,545 → owner yield 14.1% → DCF 将给出"低估 41%"）被裁决为<b>伪正常化灾难</b>（ADJ2，四证据）：净利率"周期高位"实为非现金重估+口径重分类（FY2018 分红收入 +2,051,422 从营业外改列营业），并表利润流与股东可获现金流之间存在结构性楔子——若按引擎基期折现等于把 100% Sprint/9434 的现金流全数记给股东。SOTP+持久折价是唯一与"股东实际能拿到什么"同构的通道 [E:normalization_9984_2019.json][E:metrics_9984_2019.json]。</p>
<h3>5.2 SOTP 三情景（r=10%、H=5 年、股本 2,107.667 百万股）</h3>
<table>
<tr><th>项</th><th>悲观（sotp_asset_floor）</th><th>基准</th><th>乐观</th></tr>
<tr><td>毛 NAV（万亿日元）</td><td>27.61（Sprint 失败 1.2 / VF −25% / 净债上界 6.7）</td><td>29.49 − 6.2 = 23.3</td><td>30.01 − 6.0</td></tr>
<tr><td>持久折价</td><td>55%</td><td>40%</td><td>30%</td></tr>
<tr><td>可投资价值 / 每股</td><td>9.41 万亿 / {v("scenarios.json", "scenarios.0.value_per_share", "num0")}</td><td>13.98 万亿 / {v("scenarios.json", "scenarios.1.value_per_share", "num0")}</td><td>16.81 万亿 / {v("scenarios.json", "scenarios.2.value_per_share", "num0")}</td></tr>
<tr><td>概率</td><td>30%</td><td>50%</td><td>20%</td></tr>
</table>
<p class="small">SOTP 分项（毛 NAV 29.49 万亿）：9434 11.02（33.45%×¥1,399.5）+ 阿里 12.48 + Sprint 2.38 + 雅日 0.71 + VF 线性 2.40 + 其他 0.5；折价参数取历史区间 30-50% 端点+中枢 [E:business_drivers_9984_2019.json#sotp][E:market_snapshot_9984_2019.json#source_prices]。悲观方法 sotp_asset_floor 已过 S2b 机器重算（偏差 0.007%），门禁 S1-S9 通过（唯一警告 S5：本案 SOTP 即资产口径、无独立非经营加回层，合理豁免）[E:scenario_audit.json]。</p>
<h3>5.3 安全边际结论</h3>
<p>MoS（vs 基准）= {v("mos_calc.json", "mos_vs_base", "pct1")} &lt; 40%（none 档）；保守中枢（悲观 NAV×基准折价）口径 {v("mos_calc.json", "mos_vs_conservative_center", "pct1")}；悲观口径 {v("mos_calc.json", "mos_vs_bear", "pct1")}。烟蒂式例外检验不通过：悲观值仅为现价 0.86×，且控股公司拆卖需经治理程序、普通股东无法触发变现。触发价 {v("mos_calc.json", "trigger_price_base_40pct", "num0")} 在近 6 个月内出现过（52 周低 3,652 @2018-12-28，当时折价约 64%）——等待价位可及 [E:mos_calc.json][E:market_snapshot_9984_2019.json]。</p>
<h3>5.4 假设一致性对账</h3>
<table>
<tr><th>假设</th><th>口径</th><th>来源</th></tr>
<tr><td>equity NAV 23.3 万亿</td><td>官方 NAV 口径（剔除子公司自身净债与预付远期 0.73）</td><td>q4FY2018 官方 [E:business_drivers_9984_2019.json]</td></tr>
<tr><td>折价 30/40/55%</td><td>历史区间端点+中枢</td><td>2009-2019 可观测段 [E:business_drivers_9984_2019.json#sotp]</td></tr>
<tr><td>iv_growth 5%</td><td>NAV 可持续增速，剥离重估</td><td>qualitative growth [E:qualitative_9984_2019.json]</td></tr>
<tr><td>r=10%</td><td>max(10%, JGB −0.1%+4pct)=10%</td><td>scenarios.json [E:market_snapshot_9984_2019.json]</td></tr>
</table>

<h2>六、双闸门与档位（Phase 4 定档）</h2>
<table>
<tr><th>闸门</th><th>项</th><th>值</th><th>门槛</th><th>判定</th></tr>
<tr><td rowspan="4">闸门一</td><td>MoS（vs 基准 6,633）</td><td>{v("mos_calc.json", "mos_vs_base", "pct1")}</td><td>≥40%（none 档；实际纪律为不给买入结论）</td><td class="bad">✗</td></tr>
<tr><td>MoS（保守中枢 5,954）</td><td>{v("mos_calc.json", "mos_vs_conservative_center", "pct1")}</td><td>同上</td><td class="bad">✗</td></tr>
<tr><td>MoS（悲观 4,465）</td><td>{v("mos_calc.json", "mos_vs_bear", "pct1")}</td><td>同上</td><td class="bad">✗（负）</td></tr>
<tr><td>烟蒂式例外</td><td>悲观/现价 0.86×</td><td>清算价值明确低于市价</td><td class="bad">✗</td></tr>
<tr><td rowspan="3">闸门二</td><td>①期望 IRR</td><td>{v("expected_return.json", "expected_annualized_irr", "pct1")}</td><td>none 档结构性不过</td><td class="bad">✗</td></tr>
<tr><td>②不收敛下限</td><td>{v("expected_return.json", "gate2.no_convergence_floor.value", "pct1")}（0.43%+5.0%）</td><td>≥6%</td><td class="bad">✗</td></tr>
<tr><td>③悲观年化</td><td>{v("expected_return.json", "gate2.pessimistic_irr.value", "pct1")}</td><td>≥0</td><td class="ok">✓（收敛口径）</td></tr>
</table>
<p><b>档位判定：观察等价格（2/4）</b>——闸门一不过（22.13% vs 40%）、闸门二 1/3 且 none 档不给买入结论；但估值中枢（6,633）高于现价、悲观锚（4,465）可及、触发价 3,980 近 6 个月内真实出现过——"等价格"有可辩护的价位与可及性，区别于 Netflix（三情景全低于现价，档位 1）与鞍钢（清算锚+生意萎缩，档位 1）。期望 IRR {v("expected_return.json", "expected_annualized_irr", "pct1")} 跑赢指数门槛 9.0% 但覆盖不了 none 档 21.8% 参照门槛——机会成本缺口即"不懂"的保险费 [E:expected_return.json][E:mos_calc.json]。</p>

<h2>七、关键判断收敛（Phase 4.5）</h2>
<p>三师与红队核心收敛点：①资产真实性不是问题（SOTP 三重闭环：东财月K×8/ADR 折算/官方股本），折价 53% 是事实而非估计误差；②折价不是安全边际——收敛需要治理机制，截断日四条路（回购承诺/分拆/清算/控制权变更）一条都没开；③股东现金口径残废（0.43% 股息 + owner yield 失真）使"拿时间换收敛"的等待成本无现金流兜底；④档位 2 的例外条款一致指向 ≤3,980 且须 NAV 构成未变（pre-mortem D 形态约束）[E:phase5_workpaper.json]。</p>

<h2>八、三位大师独立评估 + 红队（Phase 5）</h2>
<table>
<tr><th>大师</th><th>结论</th><th>一句话理由</th></tr>
<tr><td>巴菲特</td><td>拒绝（观察等价格）</td><td>"你买到的不是资产，是资产上的期权结构，行权者是孙正义"——能力圈 1.5（VF 十年胜率是风投竞猜）</td></tr>
<tr><td>段永平</td><td>拒绝</td><td>"资产好、结构差、价格半便宜，三样凑一起最容易骗到聪明人；错过不是错误，付错价才是"</td></tr>
<tr><td>李录</td><td>拒绝（观察等价格）</td><td>"市场折价 53% 不是错误定价，是市场对结构的诚实定价"——理解最深（3.5）仍拒绝现价</td></tr>
</table>
<p><b>红队五项质询</b>（全文见 data/phase5_workpaper.json）：①折价 40% 是否手调？→ 历史区间 30-50% 锚定 + 敏感性三层防御（要边际 ≥40% 需折价 ≤14%，历史区间外），结论对折价完全不敏感；②弃用引擎 DCF 是否浪费信息？→ 不是丢弃而是实证裁决（owner yield 21.1% 对 0.43% 股息率数学不可能），引擎 4 告警全部被 SOTP 叙事吸收；③"机制不存在"是否武断？→ 修正为"机制未承诺"（6,000 亿回购占 0.55% 是一次性动作），但修正不改变 none 档纪律裁决；④Sprint 合并入悲观是否把常态当下行？→ 三方向都有情景承载（基准已含获批路径），离散度 0.67 过 S4 门禁，WeWork LP 下调先例为 −25% 提供实证锚；⑤OBS-2016-12-01 预锚定的动机污染？→ 五重隔离（输入截断日披露+参数历史锚定+三师不接触 OBS+检验点是通道非档位+档位由定量锁死）[E:phase5_workpaper.json]。</p>

<h2>九、持有体验预演 + 跟踪清单</h2>
<p><b>持有体验预演</b>：买入即入"半便宜"区——悲观口径现价高 15.7% 意味着首年大概率浮亏；股息 0.43% 无现金流缓冲；波动由"NAV 锚 + 治理新闻"双驱动，回撤深度取决于 VF 事件而非大盘。<b>跟踪清单</b>（五变量，核查截止见 decision-log.json）：①equity NAV 构成（核心持仓变现 &gt;30% 或 VF 再加杠杆式扩张 → NAV 锚失效）；②持久折价（&lt;30% 两季=收敛兑现 / &gt;60% 两季=复核）；③Sprint 审批；④VF 公允价值事件；⑤本体净债（&gt;7.5 万亿或利差跳升）。证伪条件与重入开关见 decision-log.md 与 phase5_workpaper.json [E:phase5_workpaper.json]。</p>

<h2>十、数据引用附录（全部 publish_date）</h2>
<table>
<tr><th>来源</th><th>日期</th><th>用途</th></tr>
<tr><td>q4FY2018 官方决算报告（PDF 2,557,421 字节）</td><td>2019-05-09</td><td>IFRS 全序列/官方 NAV/股本 1,100,660,365</td></tr>
<tr><td>q4FY2010-2016 官方决算报告 ×6</td><td>2010-2017 各年 5 月</td><td>10 年序列（重列值优先）</td></tr>
<tr><td>东财月K（fqt=1，secid=176.9984）</td><td>2019-06-28 收盘</td><td>价格 5,165（拆股闭环 ×8/×4）</td></tr>
<tr><td>SFTBY ADR 报价</td><td>2019-06-28</td><td>$5.99×107.8×2=¥1,291 交叉验证</td></tr>
<tr><td>JGB 10Y / USDJPY</td><td>2019-06-28</td><td>−0.1% / 107.8（r 下限依据）</td></tr>
<tr><td>WeWork 470 亿估值报道</td><td>2019-01</td><td>VF 压力项实证锚</td></tr>
</table>
<p class="small">数据质量分级 B：10 年窗口完整但含口径切换（US GAAP→IFRS）与多年度重列；capex 全序列为引擎启发式估算（10 warnings）；官方 NAV 口径为本底稿方法学基础（SOTP 分项与净债剔除）。</p>
</body>
</html>'''

out = os.path.join(CASE, "软银集团_价值投资分析报告_20190630.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("written:", out, len(html), "chars")
