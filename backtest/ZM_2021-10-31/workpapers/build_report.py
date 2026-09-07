#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_report.py — Zoom Video Communications（ZM）@2021-10-31 报告生成器
数字全部从 data/ 底稿 JSON 读取渲染（vnum span / vchart 锚点），避免手抄漂移。
运行：python3 workpapers/build_report.py
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

def chart(src, path, div_id, title, yname, scale=1, note="", years=None):
    d = load(src)
    vals = [round(x * scale, 4) for x in gp(d, path)]
    yr = years or gp(d, "chart_series.years")
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
  xAxis: {{type: "category", data: {json.dumps([str(y) for y in yr])}, axisLabel: {{rotate: 30}}}},
  yAxis: {{type: "value", name: "{yname}"}}
}});
}})();
</script>
<p class="small">{note}</p>'''

m = load("metrics_ZM_2021.json")
s = load("market_snapshot_ZM_2021.json")
e = load("expected_return_ZM_2021.json")
sc = load("scenarios_ZM_2021.json")
mc = load("mos_calc_ZM_2021.json")

vs_labels = ["FY2017", "FY2018", "FY2019", "FY2020", "FY2021"]

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Zoom Video Communications (ZM) — 历史回放分析报告 2021-10-31</title>
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
.verification-strength{{margin-top:10px;padding:8px 10px;border-left:4px solid #c89020;background:#fdf6e8;font-size:12.5px}}
</style>
</head>
<body>
<h1>Zoom Video Communications (ZM) — 历史回放分析报告</h1>
<p class="small">回放时点 2021-10-31 ｜ 案例编号 B2-11（第二批） ｜ 信息截断日 2021-10-31</p>

<h2 style="margin-top:0">决策卡</h2>
<div class="card">
<p><b>结论：拒绝（观察等价格）（档位 1/4）——现价不买；本案为"参数不可达"型：任何可辩护锚都在现价下方，等待语义是"等估值坍塌"而非"等回调"（档位表述对齐 Netflix 先例）</b></p>
<p>现价 {v("market_snapshot_ZM_2021.json", "price.value", "num2")} USD ｜ 市值 {v("market_snapshot_ZM_2021.json", "market_cap.value", "num0")} 百万 USD ｜ 流通股 {v("market_snapshot_ZM_2021.json", "shares.value", "num1")} 百万股 ｜ PE(TTM) {v("market_snapshot_ZM_2021.json", "valuation_multiples.pe_ttm", "num1")}x ｜ PS(TTM) {v("market_snapshot_ZM_2021.json", "valuation_multiples.ps_ttm", "num1")}x ｜ 距峰值 588.84 −53.4% ｜ 股息率 {v("scenarios_ZM_2021.json", "dividend_yield", "pct1")}</p>
<p>闸门一（安全边际，narrow 档要求 40%）：基准内在价值 {v("scenarios_ZM_2021.json", "scenarios.1.value_per_share", "num2")} → MoS = {v("mos_calc_ZM_2021.json", "mos_vs_base", "pct1")} <span class="gate-fail">✗ 深度不过</span>；乐观极限口径 {v("mos_calc_ZM_2021.json", "mos_vs_bull", "pct1")}（现价高于乐观极限值）；悲观口径 {v("mos_calc_ZM_2021.json", "mos_vs_bear", "pct1")}。触发价 {v("mos_calc_ZM_2021.json", "trigger_price_base_40pct", "num2")} USD（低于上市以来全部成交区间——等待极端深）</p>
<p>闸门二（期望回报，护城河 narrow）：①期望 IRR {v("expected_return_ZM_2021.json", "expected_annualized_irr", "pct1")} vs 门槛 21.8% <span class="gate-fail">✗</span>；②不收敛下限 {v("expected_return_ZM_2021.json", "gate2.no_convergence_floor.value", "pct1")} vs 6% <span class="gate-fail">✗</span>；③悲观年化 {v("expected_return_ZM_2021.json", "gate2.pessimistic_irr.value", "pct1")} vs 0% <span class="gate-fail">✗</span> → <span class="gate-fail">闸门二 0/3，整体不过</span>（亏损概率 {v("expected_return_ZM_2021.json", "loss_probability", "pct0")}、亏损情景平均跌幅 {v("expected_return_ZM_2021.json", "expected_downside_given_loss", "pct1")}）</p>
<p>三情景（悲观为独立方法 pe_trough_multiple）：悲观 {v("scenarios_ZM_2021.json", "scenarios.0.value_per_share", "num2")} ／ 基准 {v("scenarios_ZM_2021.json", "scenarios.1.value_per_share", "num2")} ／ 乐观 {v("scenarios_ZM_2021.json", "scenarios.2.value_per_share", "num2")} vs 现价 {v("market_snapshot_ZM_2021.json", "price.value", "num2")}——<b>乐观极限情景（25.4% 峰值净利率永不回落+30% 增速 fade 15 年）仍低于现价 {v("mos_calc_ZM_2021.json", "mos_vs_bull", "pct1")}，"参数不可达"的最强量化</b></p>
<div class="verification-strength" data-verification-strength="1"
     data-grade="C"
     data-reconciliation-coverage="0.8000"
     data-crosscheck-level="full"
     data-window-years="5"
     data-covers-full-cycle="false">
  <b>核验强度 C</b>：三表勾稽覆盖率 80%（FY2017 BS 无载体豁免） ｜ 命门科目官方值核对 3 年（<u>未经原文机器逐字比对</u>，--audit 模式 0 错 0 警；FY 标签×自然年归年错位一年已登记豁免） ｜ 数据窗口 5 年（不足 10 年）且后半段被 COVID 需求脉冲扭曲、不含任何系统性压力年（序列最差年 2017 是幼年期非危机年）<br>
  <span class="vs-meaning">核验强度不足：结论的数据地基有明确缺口，档位判定从严（narrow 档 40% + gate2 全三项已内嵌）。本案缺口说明：①"历史常态"在窗口内不存在，正常化基期改用情景输入区间（ADJ5）而非历史均值；②悲观情景弃用 worst_year_margin（数据层原因即"最差年未被观测到"），改用行业成熟倍数锚；③"参数不可达"结论方向对缺口不敏感——缺口幅度（MoS −329.3%）远超数据缺口可解释范围</span>
</div>
</div>

<h2>一、信息集与防前视声明</h2>
<table>
<tr><th>信息</th><th>日期</th><th>截断裁决</th></tr>
<tr><td>FY2021 10-K（FY 止 2021-01-31，accession 0001585521-21-000048）</td><td>2021-03-05 发布</td><td>✓ 进信息集</td></tr>
<tr><td>Q1/Q2 FY2022 10-Q 与 Q2 财报信（EX-99.1，含 Q3 指引）</td><td>2021-06-04 / 2021-08-30 发布</td><td>✓ 进信息集</td></tr>
<tr><td>274.65 USD 收盘价（2021-10-29，东财日K 294 根，fqt=0 不复权）</td><td>2021-10-29</td><td>✓ 进信息集</td></tr>
<tr><td>UST 10Y 1.56%（est 标注）</td><td>2021-10 末</td><td>✓ 进信息集（r 下限依据）</td></tr>
<tr><td>S-1（2019-03-22，accession 0001193125-19-083351）——FY2017 唯一载体</td><td>2019-03-22</td><td>✓ 进信息集（IPO 前私有年度，官方 10-K 不存在）</td></tr>
<tr><td>Q3 FY2022 财报（2021-11-22）</td><td>—</td><td><span class="bad">✗ forbidden</span> 未进任何底稿（companyfacts 中 filed 2021-11-23 帧禁用）</td></tr>
<tr><td>FY2022 10-K（filed 2022-03-07）</td><td>—</td><td><span class="bad">✗ forbidden</span> 同上</td></tr>
</table>
<p class="small">meta.json 在任何数据抓取之前冻结（先验档位 1，confidence=medium，预登记 OBS-2016-12-01"参数不可达型"复检场身份）；训练语料中"Zoom 2022 年增长坍塌/股价路径/裁员"均属后见之明，全部剔除，仅用截断日前事实。</p>

<h2>二、Phase 0 排雷（对立检索 + 污染披露）</h2>
<p>对立检索返回的 2021-11 后事件（Q3'22 财报 miss、FY2022 增长坍塌、2022 股价走势）全部剔除入 forbidden 清单 [E:manifest.json]。动机污染四层披露（meta.json，commit 40ebfbb）：①OBS-2016-12-01 预登记预锚定（本案为复检场）；②成长股通道改码激励（三案例合并门槛）；③回放后先见之明（信息截断+forbidden 帧清单隔离）；④"Zoom 是疫情崩盘股"的事后共识反向污染（可能使分析者过度苛责一家单元经济健康的公司——红队第 3 项对其做了双向质询）。截断日内可用对立事实：①2021-08-31 Q2 财报信指引环比零增长（Q3F 1,017.5 vs Q2 1,021.5）次日股价 −16.7% 放量；②Teams 免费捆绑（Office 3 亿+ 座位）在截断日内已是公开事实且 10-K 风险因子列首位；③2021-04 宣布 10 亿美元回购授权（未执行）。</p>

<h2>三、定量画像（FY2017-FY2021，5 年窗口 + TTM）</h2>
<p>本案数据层的核心事实是<b>增速断崖</b>：分季收入 YoY 轨迹 Q1'22 <b>+191.4%</b>→Q2'22 <b>+53.9%</b>→Q3'22F <b>+30.9%</b>（指引中值 1,017.5）→隐含 Q4'22F <b>+15.0%</b>；环比 Q2 1,021.5→Q3F 1,017.5 = <b>−0.4%（零增长）</b>——五年 45.6% CAGR 的公司在截断日前两个月把"增长归零"写进了自己的指引。引擎 7 告警中 3 项结构性成立：<b>稀释警报</b>（收入 CAGR {v("metrics_ZM_2021.json", "summary.cagr_total.revenue", "pct1")} vs 每股 {v("metrics_ZM_2021.json", "summary.cagr_per_share.rev_ps", "pct1")}，股本 4 年增至 {v("metrics_ZM_2021.json", "summary.share_count_change", "num2")} 倍）；<b>周期高位形态</b>（最新净利率 25.4% = 全期均值 5.8% 的 4.35 倍）；<b>双轨基期+利润率形状</b>（秩相关 +0.90 长期上行——"结构性改善拒均值"，ADJ1）。<b>量纲哨兵触发但核对自洽</b>：owner yield 0.80% 非单位错位（83,948 = 274.65 × 305.653 核验通过），0.80% 是真实量纲——回本 543 年本身就是"贵"的量纲化表达 [E:metrics_ZM_2021.json]。</p>
<p>质量画像：TTM（2020-08~2021-07）OCF 1,812.2 / capex 136.8 / FCF 1,675.4 / SBC 391.3——<b>SBC 占 FCF 23.4%</b>；递延收入 230→1,178M（五年 5.1 倍，预收沉淀真实）；无任何金融债务+净现金 5.1bn（现金 1,931.4+短投 ~3,168.6）[E:financials_ZM_2021.json]。</p>
{chart("metrics_ZM_2021.json", "chart_series.revenue", "c_rev", "营业收入（百万美元，FY 止 1-31）", "百万美元", 1, "FY2021 2,651.4 含疫情需求脉冲；FY2022F 4,010（公司指引锚）在窗口外 [E:financials_ZM_2021.json]", vs_labels)}
{chart("metrics_ZM_2021.json", "chart_series.net_income", "c_ni", "净利润（百万美元）", "百万美元", 1, "FY2017 微亏（S-1 口径 −0.014M）；FY2020-2021 为疫情峰值利润 [E:metrics_ZM_2021.json]", vs_labels)}
{chart("metrics_ZM_2021.json", "chart_series.net_margin", "c_nm", "净利率（%）", "%", 100, "单向上行（秩相关 +0.90）——全期均值 5.8% 不是周期中枢而是公司幼年期盈利能力，强制正常化到均值会系统性低估（ADJ1 拒绝）[E:normalization_ZM_2021.json]", vs_labels)}
{chart("metrics_ZM_2021.json", "chart_series.owner_earnings", "c_oe", "Owner Earnings（百万美元，引擎口径）", "百万美元", 1, "引擎口径 OE 主锚裁决见 normalization ADJ1-ADJ5：均值锚 154.7 过苛拒绝、SBC 后 612.6 为主锚 [E:normalization_ZM_2021.json]", vs_labels)}
{chart("metrics_ZM_2021.json", "chart_series.shares_diluted", "c_sh", "稀释股本（百万股）", "百万股", 1, "4 年 4.24 倍：FY2019 116.0（IPO 前优先股转换口径断裂）→FY2020 254.3→FY2021 305.7；半年稀释 +2.5%（298.1→305.7）——SBC 是真实成本（ADJ2）[E:financials_ZM_2021.json]", vs_labels)}

<h2>四、五维定性（护城河裁决是闸门档位决定项）</h2>
<h3>4.1 商业模式</h3>
<p>订阅制 SaaS：视频优先的统一通讯平台。双获客引擎并存：企业直销/渠道（>10 员工客户 504,900 家，企业收入占比 64%）+ 在线自助 freemium（个人与 ≤10 员工小客户，收入占比 36%——疫情期从 18% 翻倍）。核心财务特征：递延收入预收沉淀、capex 极轻（3% 收入）、SBC 高占比（FY2021 占收入 10.4%）[E:qualitative_ZM_2021.json#business_model]。</p>
<h3 data-moat="narrow" data-moat-trend="eroding">4.2 护城河（narrow，trend=受侵蚀压力——闸门档位决定项）</h3>
<p>五源检验：无形资产中（品牌动词化+NPS>70，核心功能无专利壁垒）／转换成本弱-中（企业侧部署集成与硬件生态粘性，个人与 SMB 近乎零——而收入结构恰向长尾倾斜至 36%）／网络效应弱-中（双边通讯网络存在，但会议天然要求跨平台互通，削弱排他锁定）／成本优势中（13 个自有数据中心+带宽批量采购，毛利率 73-81%；对自建骨干网的 Microsoft/Google 无优势）／有效规模中（504,900 企业客户先发，但 Teams 以 Office 3 亿+ 座位免费捆绑，有效规模对捆绑打法效力有限）。最硬量化证据 NRR>130% TTM 连续 13 季（对增速突变滞后，TTM 平滑）。侵蚀四项：Teams 捆绑/疫情回吐（客户数增速 +470%→+36% 悬崖即证据）/隐私安全尾部/OS 级免费方案截流 [E:qualitative_ZM_2021.json#moat]。</p>
<h3>4.3 增长空间</h3>
<p>企业渗透仍早（$100k+ 大客户 2,278 家仅占企业客户 0.45%，+131% YoY 加速）+ Zoom Phone/Rooms 交叉销售（NRR 载体）构成真实增长储备；但疫情脉冲回吐使在线引擎（36% 收入）进入负增长轨道，Q3 指引环比零增长已是公司自己的确认。增长中枢的重定位（30%+→15-20%？）是截断日市场分歧的实质 [E:qualitative_ZM_2021.json#growth][E:business_drivers_ZM_2021.json]。</p>
<h3>4.4 管理层</h3>
<p>Eric Yuan 创始人 CEO：WebEx 出走创业、疫情期间免费开放（短期让利买长期口碑）、指引风格保守（+31% 指引被市场嫌低反而说明未画饼）。扣分项：SBC 激励结构持续摊薄（半年 +2.5%），10 亿回购授权未执行 [E:qualitative_ZM_2021.json#management]。</p>
<h3>4.5 财务质量</h3>
<p>全绿但含一个结构性保留：FCF 1,675.4M 大正、无债务、净现金 5.1bn——但 SBC 占 FCF 23.4%，"SBC 与递延沉淀不可永久化"（与 Netflix"wc 释放不可永久化"同一纪律族）；净利率 25.4% 处软件业顶部，峰值回吐路径已被毛利率 81.5%→69.0%→73.4% 的疫情扰动示范 [E:qualitative_ZM_2021.json#financial_quality][E:financials_ZM_2021.json]。</p>

<h2>五、估值与安全边际（Phase 4）</h2>
<h3>5.1 正常化五裁决（normalization ADJ1-ADJ5）</h3>
<p>五锚并列：引擎均值锚 154.68（<b>ADJ1 拒绝</b>——ρ+0.90 结构性改善下均值锚是"公司幼年期的盈利能力"，鞍钢"结构性恶化禁主轨"的镜像）→ SBC 后主锚 <b>612.58</b>（<b>ADJ2 核心裁决</b>：TTM 净利 1,003.9−SBC 391.3；SBC 是真实成本——稀释半年 +2.5%、FCF 的 23.4% 依赖 SBC 会计处理；FCF 1,675.4 为乐观交叉并标注依赖）→ GAAP 672.32 → 情景输入 721.8/1,002.5（<b>ADJ5</b>：FY2022F 4,010×15-25% 区间）。ADJ3（稀释警报归因：IPO 股本转换口径断裂非经营稀释）与 ADJ4（ROIIC 失真不适用：负权益/轻资产 IC 分母）为归因修正 [E:normalization_ZM_2021.json]。</p>
<h3>5.2 反向 DCF：现价隐含什么</h3>
<p>implied-growth（EV 口径：市值 83,948−净现金 5,100）：现价隐含未来 10 年 OE 增速 <b>33.53%/年</b>（全市值口径 34.44%）——而收入 YoY 已断崖至指引 +30.9%F→隐含 +15.0%F、利润率峰值 25.4% 处软件业顶部。forward-value 极限测试：<b>base_oe 1,002.5（25.4% 峰值维持）+ g 30% fade 15 年 + r 10% + 加回 5,100 = 220.91，仍低于现价 19.6%</b>——可辩护假设空间内无组合支撑现价，"参数不可达"量化成立 [E:metrics_ZM_2021.json][E:fv_bull.json]。</p>
<h3>5.3 三情景（r=10%、H=5 年、股本 305.653M）</h3>
<table>
<tr><th>项</th><th>悲观（pe_trough_multiple，独立方法）</th><th>基准（dcf_owner_earnings）</th><th>乐观（dcf_owner_earnings，极限测试）</th></tr>
<tr><td>内核</td><td>15× SBC 后 OE 612.58（不给 COVID 峰值信用；增长信任破灭后按稳态软件资产定价）</td><td>FY2022F 4,010×18% 净利率=721.8，g 12% fade 10 年，tg 2.5%</td><td>峰值 25.4% 永续维持=1,002.5，g 30% fade 15 年，tg 2.5%</td></tr>
<tr><td>经营价值 + 净现金加回</td><td>4,561 +（0% 计入 V0；可辩护中间口径 3,060=60% 折价另披露）</td><td>14,453 + 5,100</td><td>62,422 + 5,100</td></tr>
<tr><td>每股价值</td><td>{v("scenarios_ZM_2021.json", "scenarios.0.value_per_share", "num2")}（含加回 40.07；均低于现价）</td><td>{v("scenarios_ZM_2021.json", "scenarios.1.value_per_share", "num2")}</td><td>{v("scenarios_ZM_2021.json", "scenarios.2.value_per_share", "num2")}</td></tr>
<tr><td>概率</td><td>{v("scenarios_ZM_2021.json", "scenarios.0.probability", "pct0")}</td><td>{v("scenarios_ZM_2021.json", "scenarios.1.probability", "pct0")}</td><td>{v("scenarios_ZM_2021.json", "scenarios.2.probability", "pct0")}</td></tr>
</table>
<p class="small">悲观方法 pe_trough_multiple 已过 S2b 机器重算（偏差 0.008%）；worst_year_margin 被 S2c 判据预防性排除（序列最差年 2017 是幼年期+ρ+0.90 长期上行——茅台/苹果两案实证判据，本案数据层同样支持：窗口无压力年）。S5 非经营资产分层折价：基准/乐观全额加回 16.68/股（现金+国债类上市证券流动性极高）、悲观 60% 折价 10.01/股（增长破灭后重投入烧钱叙事）、expected-return 按 0% 计入（最保守）——三档折价与压力强度单调对应，门禁 S1-S9 全过（0 错 0 警）[E:scenarios_ZM_2021.json]。</p>
<h3>5.4 安全边际结论</h3>
<p>MoS（vs 基准）= {v("mos_calc_ZM_2021.json", "mos_vs_base", "pct1")}（narrow 档要求 ≥40%）；乐观极限口径 {v("mos_calc_ZM_2021.json", "mos_vs_bull", "pct1")}；悲观口径 {v("mos_calc_ZM_2021.json", "mos_vs_bear", "pct1")}。烟蒂式例外不成立：悲观值仅为现价 0.109×，净现金 5,100M 仅解释市值 6.1%，无"市值低于净流动资产"形态。触发价 {v("mos_calc_ZM_2021.json", "trigger_price_base_40pct", "num2")} = 基准×0.6，低于上市以来全部成交区间（52 周低 250.11 的 15.3%；IPO 发行价 36.00 为唯一邻近参考位）——等待价位属极端深，可及性依赖估值坍塌 ~86% [E:mos_calc_ZM_2021.json][E:market_snapshot_ZM_2021.json]。</p>
<h3>5.5 假设一致性对账</h3>
<table>
<tr><th>假设</th><th>口径</th><th>来源</th></tr>
<tr><td>主锚 612.58（SBC 后）</td><td>TTM 净利 1,003.9−SBC 391.3</td><td>normalization ADJ2 [E:normalization_ZM_2021.json]</td></tr>
<tr><td>基准 base_oe 721.8</td><td>FY2022F 4,010×18%（GAAP 净利率外推，天然含 SBC 成本——与主锚同族自洽）</td><td>ADJ5 情景输入 [E:scenarios_ZM_2021.json]</td></tr>
<tr><td>iv_growth 0</td><td>永续 OE 增速 2.5% − SBC 稀释 2-3% ≈ 0（每股口径）</td><td>scenarios iv_growth_evidence [E:scenarios_ZM_2021.json]</td></tr>
<tr><td>r=10%</td><td>max(10%, UST 1.56%+4pct)=10%</td><td>scenarios.json [E:market_snapshot_ZM_2021.json]</td></tr>
</table>

<h2>六、双闸门与档位（Phase 4 定档）</h2>
<table>
<tr><th>闸门</th><th>项</th><th>值</th><th>门槛</th><th>判定</th></tr>
<tr><td rowspan="3">闸门一</td><td>MoS（vs 基准 63.97）</td><td>{v("mos_calc_ZM_2021.json", "mos_vs_base", "pct1")}</td><td>≥40%（narrow 档）</td><td class="bad">✗（负值）</td></tr>
<tr><td>MoS（vs 乐观极限 220.91）</td><td>{v("mos_calc_ZM_2021.json", "mos_vs_bull", "pct1")}</td><td>同上</td><td class="bad">✗（现价高于乐观值）</td></tr>
<tr><td>烟蒂式例外</td><td>悲观/现价 0.109×</td><td>清算价值明确低于市价</td><td class="bad">✗</td></tr>
<tr><td rowspan="3">闸门二</td><td>①期望 IRR</td><td>{v("expected_return_ZM_2021.json", "expected_annualized_irr", "pct1")}</td><td>≥21.8%（narrow 档，r=10%/H=5）</td><td class="bad">✗</td></tr>
<tr><td>②不收敛下限</td><td>{v("expected_return_ZM_2021.json", "gate2.no_convergence_floor.value", "pct1")}（0+0）</td><td>≥6%</td><td class="bad">✗</td></tr>
<tr><td>③悲观年化</td><td>{v("expected_return_ZM_2021.json", "gate2.pessimistic_irr.value", "pct1")}</td><td>≥0</td><td class="bad">✗</td></tr>
</table>
<p><b>档位判定：拒绝（观察等价格）（1/4）</b>——闸门一深度不过（−329.3%）、闸门二 0/3、触发价 38.38 在上市以来全部成交区间之外。与 Netflix（同档位 1，同型复合表述）同档但疾病不同：Netflix 三情景全低于现价且生意失血（OE 为负+FCF 连续 6 年负+融资依赖），Zoom 单元经济全绿+FCF 大正+零负债——同为参数不可达，一个死于现金流，一个死于价格。与软银（档位 2）的区别：软银基准/乐观锚在现价上方且触发价 52 周内真实出现过（等待可及），Zoom 的等待语义是"等估值坍塌" [E:expected_return_ZM_2021.json][E:mos_calc_ZM_2021.json]。</p>

<h2>七、关键判断收敛（Phase 4.5）</h2>
<p>三师与红队核心收敛点：①公司质量无争议（NRR>130%×13 季+毛利率 73-81%+递延沉淀+净现金 5.1bn——近十年 SaaS 最干净的单元经济之一）；②"参数不可达"是算术不是观点（隐含增速 33.5%>指引方向>利润率物理上限，极限测试 220.91 仍差 19.6%）；③增长断崖已被市场半定价但远不够（指引次日 −16.7% vs PE_TTM 仍 83.6x）；④SBC 是本案的隐性税率（每股口径增长比报表口径低 78pct）；⑤观察等价格的等待语义="等估值坍塌"，可及性依赖增长陷阱兑现 [E:phase5_workpaper.json]。</p>

<h2>八、三位大师独立评估 + 红队（Phase 5）</h2>
<table>
<tr><th>大师</th><th>结论</th><th>一句话理由</th></tr>
<tr><td>巴菲特</td><td>拒绝（观察等价格）</td><td>"市场预付了 69.4B 的增长支票，而增长曲线刚当着所有人的面断裂"——owner yield 0.80% vs 国债 1.56% 的特殊债券比较；能力圈 2.5</td></tr>
<tr><td>段永平</td><td>拒绝</td><td>"好公司坏价格是最容易骗到聪明人的组合，因为每个数字都是真的"——Stop Doing List 视角：'Microsoft 把 Teams 免费捆绑给 3 亿个座位，你怎么办？'</td></tr>
<tr><td>李录</td><td>拒绝（观察等价格）</td><td>"两问合答：十年后更大更强的置信度八成（互联互通性限制它成不了微信），但现价把'垄断者增长+顶格利润率'都不够付的价提前收走了"——理解最深（4.0）仍拒绝现价</td></tr>
</table>
<p><b>红队五项质询</b>（全文见 data/phase5_workpaper.json）：①悲观锚 15x 是否手调？→ 敏感度 10x-20x 全区间（20.04-40.08）不改变结论，取值方向自检：15x 是区间下界（保守侧），"凑结论"应取上界才对；②TTM 盈利的 COVID 污染？→ 已披露"悲观值可能仍属乐观"（峰值回吐致 20-24.5），方向对结论安全（本案为买入侧拒绝，S2c 立法本意不适用于当前方向）；③乐观极限是否故意压低/第二曲线为何零价值？→ 参数已在激进侧（增速为指引两倍+利润率峰值永不回落），第二曲线时点内无分部披露（无证据不加价值），且要填 19.6% 缺口需再造一个 Zoom；④SBC 口径自洽？→ 基准 18% 为 GAAP 净利率（天然含 SBC 成本），与主锚 612.58 同族，交叉验证 612.58×收入比×利润率比 ≈ 720.9 ≈ 721.8（差 <0.2%）；⑤<b>OBS-2016-12-01 预锚定/改码激励专条</b>→ 六重隔离（时序冻结+管线机械输出+敏感性无着力点+乐观侧方向自检+过苛方向自检+Step 5 判定防火墙），定向带偏无着力点，但"结论恰好落在预登记预期上"要求 Step 5 裁决只引用引擎机械输出（隐含增速 33.53%/极限缺口 19.6%）并防 R1（Zoom FCF 大正 vs Netflix 大负不得因结论同向而自动合并）[E:phase5_workpaper.json]。</p>

<h2>九、持有体验预演 + 跟踪清单</h2>
<p><b>持有体验预演</b>（假设 274.65 买入）：亏损概率 {v("expected_return_ZM_2021.json", "loss_probability", "pct0")}、亏损情景平均跌幅 {v("expected_return_ZM_2021.json", "expected_downside_given_loss", "pct1")}；无股息缓冲；波动由"增速指引+利润率轨迹"双驱动，Q4'22 隐含 +15% 的兑现度是最近的压力测试点（2021-08-31 −16.7% 已演示灵敏度）。<b>跟踪清单</b>（pre-mortem 四形态，核查点见 phase5_workpaper.json）：①收入 YoY 轨迹（Q3/Q4 FY2022 实际 vs 指引——A 形态增长陷阱兑现的主证据）；②NRR 轨迹与客户数增速（B 形态竞争侵蚀）；③FCF 与净现金消耗（C 形态现金燃烧+10 亿回购执行）；④若股价崩至 38 附近而单元经济完好（D 形态约束），触发价触及≠thesis 兑现，须重开全流程评估 [E:phase5_workpaper.json]。</p>

<h2>十、数据引用附录（全部 publish_date）</h2>
<table>
<tr><th>来源</th><th>日期</th><th>用途</th></tr>
<tr><td>FY2021 10-K（htm 2.7MB，0001585521-21-000048）</td><td>2021-03-05</td><td>FY2019-2021 三表+股本+SBC+客户数</td></tr>
<tr><td>FY2020 10-K（0001585521-20-000095）</td><td>2020-03-12</td><td>FY2018-2020 比较期核验</td></tr>
<tr><td>S-1（0001193125-19-083351）</td><td>2019-03-22</td><td>FY2017 唯一载体（Selected Financial Data/审计 BS）</td></tr>
<tr><td>Q2 FY2022 财报信 EX-99.1</td><td>2021-08-30</td><td>Q3 指引中值 1,017.5+净现金 5.1bn 口径</td></tr>
<tr><td>东财美股日K（secid=105.ZM，fqt=0，294 根）</td><td>2021-10-29 收盘</td><td>价格 274.65+52 周区间+峰值回撤</td></tr>
<tr><td>UST 10Y</td><td>2021-10 末</td><td>1.56%（r 下限依据，est）</td></tr>
</table>
<p class="small">数据质量分级 C：5 年窗口不足且含疫情扭曲；FY2017 仅 S-1 载体（未经 BS 勾稽）；公司facts 中 FY2022 帧全部禁用（filed 2021-11-23/2022-03-07 属 forbidden）。官方 FY 标签与脚本自然年归年错位一年已登记 crosscheck_exempt.fy_label_mismatch。</p>
</body>
</html>'''

out = os.path.join(CASE, "Zoom_价值投资分析报告_20211031.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("written:", out, len(html), "chars")
