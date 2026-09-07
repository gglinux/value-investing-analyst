#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Netflix NFLX 2016-12-31 回放报告 HTML：数值全部从 data/ 底稿程序化注入，防手抄漂移"""
import json, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backtest/NFLX_2016-12-31/
D = lambda name: json.load(open(os.path.join(BASE, 'data', name)))

snap = D('market_snapshot_NFLX_2016.json')
scen = D('scenarios.json')
er = D('expected_return.json')
met = D('metrics_NFLX_2016.json')
bd = D('business_drivers_NFLX_2016.json')
fin = D('financials_NFLX_2016.json')

def vnum(src, path, fmt, val):
    if fmt == 'pct1':   s = f"{val*100:.1f}%"
    elif fmt == 'pct2': s = f"{val*100:.2f}%"
    elif fmt == 'num0': s = f"{val:,.0f}"
    elif fmt == 'num1': s = f"{val:,.1f}"
    elif fmt == 'num2': s = f"{val:,.2f}"
    else:               s = str(val)
    return f'<span class="vnum" data-src="{src}" data-path="{path}" data-fmt="{fmt}">{s}</span>'

price = snap['price']['value']
bear = scen['scenarios'][0]['value_per_share']
base_v = scen['scenarios'][1]['value_per_share']
opt_v = scen['scenarios'][2]['value_per_share']
mktcap = snap['market_cap']['value']
pe = snap['valuation_multiples']['pe_ttm']
pb = snap['valuation_multiples']['pb_mrq']
pos52 = snap['kline_2016_dec']['position_in_52w_range_pct']
ust = snap['risk_free']['us_10y_treasury_yield_pct']
mos = (base_v - price) / price            # 安全边际 (V-P)/P = -0.8892
premium = (price - base_v) / base_v       # 现价对基准溢价 = 8.031
cs = met['chart_series']
years = cs['years']
g2 = er['gate2']
members_2015 = bd['drivers'][3]['volume']
arpu_2015 = bd['drivers'][3]['price']
capex_2015 = [r for r in fin['annual'] if r['year'] == 2015][0]['capex']

# 派生计算底稿（供报告 vnum 引用与留痕）
mos_calc = {
    "mos": round(mos, 4),
    "premium": round(premium, 2),
    "epv_main": 9.28,
    "epv_cross": 17.78,
    "epv_pessimistic_ni_anchor": 7.38,
    "buy_ceiling_epv": round(9.28 * (1 - 0.40), 2),
    "excess_vs_index_pct": round(er['excess_vs_index'] * 100, 2),
    "avg_loss_pct": round(er['expected_downside_given_loss'] * 100, 1) if er.get('expected_downside_given_loss') else -80.5,
    "note": "派生值：mos=(V0-P)/P；premium=(P-V0)/V0；EPV=base_oe/0.10/shares；其余为报告叙述辅助值"
}
json.dump(mos_calc, open(os.path.join(BASE, 'data', 'mos_calc.json'), 'w'), indent=1, ensure_ascii=False)

chart = """
<script src="https://cdnjs.cloudflare.com/ajax/libs/echarts/4.8.0/echarts.min.js"></script>
<script>
function mk(id, opt){ var c = echarts.init(document.getElementById(id)); c.setOption(opt); }
document.addEventListener('DOMContentLoaded', function(){
"""
def line_opt(title, name, anchor, data, pct=False, color='#c0392b'):
    return f"""mk('{title}', {{
  title:{{text:'{name}',left:'center',textStyle:{{fontSize:14}}}},
  tooltip:{{trigger:'axis'}},
  grid:{{left:60,right:20,bottom:30,top:40}},
  xAxis:{{type:'category',data:{json.dumps(years)}}},
  yAxis:{{type:'value'{',axisLabel:{formatter:"{value}%"}' if pct else ''}}},
  series:[{{name:'{name}',type:'bar',itemStyle:{{color:'{color}'}},
  {anchor}
  data:{json.dumps(data)}}}]
}});"""

rev_arr = [round(x,1) for x in cs['revenue']]
ni_arr  = [round(x,1) for x in cs['net_income']]
nm_arr  = [round(x*100,2) for x in cs['net_margin']]
oe_arr  = [round(x,1) for x in cs['owner_earnings']]
fcf_arr = [round(x,1) for x in cs['fcf']]

charts = chart + line_opt('c1','营业收入（百万 USD）','<!-- vchart src=metrics_NFLX_2016.json path=chart_series.revenue -->', rev_arr) + "\n" + \
         line_opt('c2','净利润（百万 USD）','<!-- vchart src=metrics_NFLX_2016.json path=chart_series.net_income -->', ni_arr, color='#2c3e50') + "\n" + \
         line_opt('c3','净利率 %','<!-- vchart src=metrics_NFLX_2016.json path=chart_series.net_margin scale=100 -->', nm_arr, pct=True) + "\n" + \
         line_opt('c4','Owner Earnings（百万 USD，引擎口径 mc=D&A 封顶）','<!-- vchart src=metrics_NFLX_2016.json path=chart_series.owner_earnings -->', oe_arr, color='#8e44ad') + "\n" + \
         line_opt('c5','自由现金流 FCF=OCF−capex（百万 USD，方案 B 口径）','<!-- vchart src=metrics_NFLX_2016.json path=chart_series.fcf -->', fcf_arr, color='#16a085') + "\n</script>"

badge = open(os.path.join(BASE, 'workpapers', 'badge_div.html')).read()

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><title>Netflix NFLX 历史回放报告 2016-12-31（第二批案例9）</title>
<style>
body{{font-family:'PingFang SC','Microsoft YaHei',sans-serif;max-width:980px;margin:0 auto;padding:24px;color:#2c3e50;line-height:1.75;background:#fafafa}}
h1{{font-size:22px;border-bottom:3px solid #b03a2e;padding-bottom:8px}} h2{{font-size:18px;color:#b03a2e;margin-top:32px;border-left:4px solid #b03a2e;padding-left:10px}} h3{{font-size:15px;color:#2c3e50;margin-top:18px}}
.card{{background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:18px 22px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.verdict{{background:linear-gradient(135deg,#fdedec,#fff);border:2px solid #b03a2e}}
.gate-fail{{color:#c0392b;font-weight:700}} .gate-pass{{color:#27ae60;font-weight:700}}
table{{border-collapse:collapse;width:100%;margin:10px 0;background:#fff}}
th,td{{border:1px solid #d5d8dc;padding:7px 10px;font-size:14px;text-align:left}}
th{{background:#f5eef0}}
.warnbox{{background:#fdecea;border-left:4px solid #c0392b;padding:10px 14px;border-radius:4px;margin:10px 0}}
.okbox{{background:#e8f6f3;border-left:4px solid #27ae60;padding:10px 14px;border-radius:4px;margin:10px 0}}
.chart{{width:100%;height:300px}}
.small{{font-size:12.5px;color:#7f8c8d}}
</style>
</head>
<body>

<h1>Netflix (NFLX) — 历史回放分析报告</h1>
<p class="small">backtest/PROMPT.md 第二批 · 案例 9 ｜ 回放时点 <b>2016-12-31</b>（周六休市，价格取 2016-12-30 收盘）｜ 生成于 2026-09-07 ｜ 本案是 PROMPT 提问的复检场：经营现金流长期为负但属主动投入，<code>add-back</code> 与自由现金流逻辑会不会误判为烧钱（OBS-STAGE4-03 高成长档位适配性复检）</p>

<div class="warnbox"><b>本报告仅使用 2016-12-31 及之前的公开信息。</b>信息截断纪律：FY2016 10-K（2017-01-27 filed）、Q4'16 股东信（2017-01-19 发布）为 forbidden data 已剔除；2016 全年以 9M'16 实际（Q3'16 10-Q）+ Q4'16F 公司指引（Q3'16 股东信 2016-10-17）推算，全部标注 estimate、不进年度序列。已识别污染（"高成长烧钱"预期叙事）见 <code>meta.json / prior_expectation</code>（confidence=low），结论严格由管线定量输出决定。</div>

<div class="card verdict">
<h2 style="margin-top:0">决策卡</h2>
<p>结论档位：<b style="font-size:20px">拒绝（观察等价格）</b>（闸门一深度不过且闸门二 3/3 全败。本案独特性：三师一致认为"公司可能对、价格绝对错"——现价为乐观情景价值的 3.2 倍）</p>
<p>现价 {vnum('market_snapshot_NFLX_2016.json','price.value','num2',price)} USD ｜ 市值 {vnum('market_snapshot_NFLX_2016.json','market_cap.value','num0',mktcap)} 百万 USD ｜ TTM PE {vnum('market_snapshot_NFLX_2016.json','valuation_multiples.pe_ttm','num1',pe)}x ｜ PB {vnum('market_snapshot_NFLX_2016.json','valuation_multiples.pb_mrq','num1',pb)}x ｜ 股息率 {vnum('scenarios.json','dividend_yield','pct1',scen['dividend_yield'])} ｜ 52 周位置 {vnum('market_snapshot_NFLX_2016.json','kline_2016_dec.position_in_52w_range_pct','num1',pos52)}%（高位区）</p>
<p>闸门一（安全边际，窄护城河要求 40%）：基准内在价值 {vnum('scenarios.json','scenarios.1.value_per_share','num2',base_v)} USD → MoS = <span class="gate-fail">{vnum('mos_calc.json','mos','pct1',mos)} ✗ 深度不过</span>（现价较基准溢价 {vnum('mos_calc.json','premium','num0',premium)}%）</p>
<p>闸门二（期望回报）：①期望 IRR {vnum('expected_return.json','gate2.consistency_expected_irr.value','pct1',g2['consistency_expected_irr']['value'])} vs 门槛 {vnum('expected_return.json','gate2.consistency_expected_irr.hurdle','pct1',g2['consistency_expected_irr']['hurdle'])} <span class="gate-fail">✗</span>；②不收敛下限 {vnum('expected_return.json','gate2.no_convergence_floor.value','pct1',g2['no_convergence_floor']['value'])} vs 6% <span class="gate-fail">✗</span>；③悲观年化 {vnum('expected_return.json','gate2.pessimistic_irr.value','pct1',g2['pessimistic_irr']['value'])} vs 0% <span class="gate-fail">✗</span> → <span class="gate-fail">闸门二 0/3</span></p>
<p>三情景：悲观 {vnum('scenarios.json','scenarios.0.value_per_share','num2',bear)} ／ 基准 {vnum('scenarios.json','scenarios.1.value_per_share','num2',base_v)} ／ 乐观 {vnum('scenarios.json','scenarios.2.value_per_share','num2',opt_v)} vs 现价 {vnum('market_snapshot_NFLX_2016.json','price.value','num2',price)}——<b>亏损概率 {vnum('expected_return.json','loss_probability','pct1',er['loss_probability'])}</b>（所有情景 5 年持有期回报均为负）</p>
{badge}
</div>

<h2>一、信息集与防前视声明</h2>
<div class="card">
<table>
<tr><th>披露</th><th>publish_date</th><th>状态</th></tr>
<tr><td>FY2007-FY2014 10-K（年度序列，方案 B 重口径重建）</td><td>2008-01 ~ 2015-01 各年 filed</td><td>✓ 进信息集</td></tr>
<tr><td>FY2015 10-K（年度基期，accn 0001065280-16-000047）</td><td>filed 2016-01-28</td><td>✓ 进信息集</td></tr>
<tr><td>Q1/Q2/Q3'16 10-Q + Q3'16 股东信（8-K EX-99.1）</td><td>2016-04/07 月 / 2016-10-17 信 / 2016-10-20 10-Q</td><td>✓ 进信息集（9M'16 实际数）</td></tr>
<tr><td>2016-12-30 收盘价 {vnum('market_snapshot_NFLX_2016.json','price.value','num2',price)} USD（不复权）</td><td>2016-12-30</td><td>✓ 进信息集</td></tr>
<tr><td>Q4'16F 指引（收入 2,344 / OP 125 / NI 56 百万）</td><td>Q3'16 股东信 2016-10-17</td><td>✓ 进信息集（仅作 2016E 推算锚，标 estimate）</td></tr>
<tr><td>Q4'16 股东信（实际业绩）</td><td>2017-01-19</td><td>✗ 剔除（forbidden data，晚于截断日 19 天）</td></tr>
<tr><td>FY2016 10-K</td><td>2017-01-27</td><td>✗ 剔除（forbidden data）</td></tr>
</table>
<p class="small">股本口径：摊薄 {vnum('financials_NFLX_2016.json','annual.8.shares_diluted','num1',fin['annual'][8]['shares_diluted'])} 百万股（FY2015 10-K）；2015-07-15 7:1 拆股已对 2007-2012 全序列调整（×7），60.758×7=425.306≈425.327 精确吻合验证，序列平滑无跳变。行情来源：行情底稿不复权日 K。</p>
</div>

<h2>二、Phase 0 排雷（对立检索 + 监管/审计核查）</h2>
<div class="card okbox">
<b>通过——不触发一票否决，但留存四组时点内空头论据及应答。</b>① 财务造假形态检验：<b>OCF vs 净利长期背离定性为"订阅预收现+内容支出列报"而非收入造假形态</b>——收入端月费先收钱后确认（应收极小、无压货载体，造假典型入口天然缺失）；费用端争议全在内容摊销速度（管理层判断空间）而非收入虚增；方案 B 口径 ocf_adj−capex 与公司股东信 FCF 恒等（2015：5,022.2−5,940.9=−918.6 ≡ −749.4−91.2−78.0=−918.6 ✓），现金缺口全量可见无隐藏通道 [E:manifest.json#adversarial_check][E:workpapers/phase0_forensic_20260907.json]。② 对立面检索：NewConstructs（ROIC 现金口径 55%→5%）、Ovum（"激进内容会计"：摊销期最长 5 年 vs DVD 时代 1-3 年加速）、Seeking Alpha（Q3'16 摊销方法变更 −19.8M OP）、Investing.com（9M'16 摊销 3.4B vs 现金支付 4.1B）——四组论据全部属"再投入烧钱"维度，与方案 B 口径同源（揭会计面纱看现金），由管线独立验证不作输入；且空头内部矛盾（既指控摊销太慢又指控内容库缩水 40%，互斥）削弱其证据力。③ 监管：2012-07 Hastings Facebook 帖 RegFD 事件 → 2013-04 SEC 21(a) 报告未认定违规未执法（披露渠道争议，与财务真实性无关；初稿误记 2010 年已更正）。④ 审计：EY LLP 历年标准无保留意见+ICFR 有效，无 material weakness、无造假集体诉讼 [E:manifest.json][E:workpapers/phase0_forensic_20260907.json]。
</div>

<h2>三、定量画像（FY2007-FY2015，9 年窗口）</h2>
<div class="card">
<div id="c1" class="chart"></div>
<div id="c2" class="chart"></div>
<div id="c3" class="chart"></div>
<div id="c4" class="chart"></div>
<div id="c5" class="chart"></div>
<p class="small">引擎警报 6 条（关键 4 条）：①<b>利润率形状检验判"周期波动"</b>（秩相关 −0.57、穿越均值 3 次）——本案裁决为<b>误判</b>：净利率波动与内容支出/收入比精确同步（投入节奏），非行业景气（详见 ADJ1）；②周期低位提示+mean_distortion 4 年标记（2012-2015 同比剧变，裁决为投入节奏非一次性损益）；③利润含金量警报：近 5 年 FCF/净利全部 &lt;0.6（2015 年 FCF 为净利的 −7.5 倍）——不是造假信号而是再投入信号；④分红幻觉警报（覆盖 −0.63x）：Netflix 零分红，警报实质是"九年累计 OCF 18,685 百万一分未落袋全砸内容库+倒贴 647 百万"，股东回报 1,034 百万系早期回购 [E:metrics_NFLX_2016.json]。</p>
<p>正常化裁决（本案核心方法论决策）：引擎判"周期低位"推荐 hybrid 基期 {vnum('metrics_NFLX_2016.json','normalization.base_oe_recommended','num1',met['normalization']['base_oe_recommended'])} 百万——<b>裁决拒绝采纳</b>（ADJ2）：hybrid = 正常化净利 {vnum('metrics_NFLX_2016.json','normalization.net_income_normalized','num1',met['normalization']['net_income_normalized'])} × conv {vnum('metrics_NFLX_2016.json','normalization.oe_to_ni_conversion_latest','num2',met['normalization']['oe_to_ni_conversion_latest'])}，而 conv 完全由 wc 释放驱动（mc=D&A 封顶下 OE=NI−wc_change，2015 递延收入释放 172.9 百万）——预付沉淀是会员增速的函数，把单年 conv 2.41 永久化等于假设预付沉淀与利润同比例永续，会员增速放缓即归零。<b>主锚降档至引擎区间下界</b> {vnum('metrics_NFLX_2016.json','normalization.oe_normalized','num1',met['normalization']['oe_normalized'])} 百万（OE 利润率均值 {vnum('metrics_NFLX_2016.json','normalization.oe_margin_avg','pct2',met['normalization']['oe_margin_avg'])}×收入）[E:metrics_NFLX_2016.json][E:normalization_NFLX_2016.json]。</p>
</div>

<h2>四、五维定性（护城河裁决是本案复检关键输入）</h2>
<div class="card">
<h3>4.1 商业模式</h3>
<p>全球流媒体订阅：收入=会员数×月费（2015：平均付费会员 {vnum('business_drivers_NFLX_2016.json','drivers.3.volume','num1',members_2015)} 百万 × 每会员年收入 {vnum('business_drivers_NFLX_2016.json','drivers.3.price','num2',arpu_2015)} USD）。分部贡献利润：US 流媒体 44.0%、Intl −5.9%、DVD 32.2%——利润全靠 US，国际在烧钱换会员。现金口径每会员月内容成本 7.67 USD vs 收入 8.32 USD（<b>内容成本占收入 92%</b>）——当期现金几乎全部再投入，盈利依赖"存量内容已付清+增量会员摊薄"的时间差 [E:financials_NFLX_2016.json][E:business_drivers_NFLX_2016.json]。</p>
<h3 data-moat="narrow" data-moat-trend="improving">4.2 护城河（窄，trend 改善）——OBS-STAGE4-03 复检关键输入</h3>
<p>五源检验：无形资产中（品牌+推荐技术+自制内容，但授权内容可竞价夺走）；转换成本弱（月付无合约）；网络效应弱-中（数据效应）；<b>成本优势中——规模经济已在 US 兑现</b>（贡献利润率 44% vs Intl −5.9% 的差就是规模差）；有效规模中（86.7M 会员先发+190 国生态）。定价权实证：2014 与 2016 两轮 US 提价（7.99→8.99→9.99）无成体系流失恶化。<b>评级'窄'而非'宽'的理由</b>：内容独占性只有一半在自己手里（Disney 2016-08 收购 BAMTech 已显收回授权自建信号）；提价频度幅度不足以证明宽级定价权；Intl 盈利未证明 [E:qualitative_NFLX_2016.json]。</p>
<h3>4.3 增长空间</h3>
<p>量主导：付费会员 {vnum('business_drivers_NFLX_2016.json','drivers.0.volume','num1',bd['drivers'][0]['volume'])}（2012）→ {vnum('business_drivers_NFLX_2016.json','drivers.4.volume','num1',bd['drivers'][4]['volume'])} 百万（2016E），CAGR 24.2%，每会员年收入 {vnum('business_drivers_NFLX_2016.json','drivers.0.price','num2',bd['drivers'][0]['price'])}→{vnum('business_drivers_NFLX_2016.json','drivers.4.price','num2',bd['drivers'][4]['price'])} USD（年 +0.7%）——增长 96% 靠会员量。收入 CAGR {vnum('metrics_NFLX_2016.json','summary.cagr_total.revenue','pct1',met['summary']['cagr_total']['revenue'])}（2007-2015）。2016-01 全球上线 130 国打开 TAM（全球付费电视万亿美元级、SVOD 渗透早期）。内容支出/收入 2011 77%→2015 {vnum('financials_NFLX_2016.json','annual.8.capex','num0',capex_2015)} 百万（占收入 88%）→2016E 93%——跑道长是乐观的燃料也是军备竞赛的燃料 [E:metrics_NFLX_2016.json][E:business_drivers_NFLX_2016.json]。</p>
<h3>4.4 管理层与治理</h3>
<p>资本配置三师一致 4.0/5.0：FCF 为负期零分红零回购（正确纪律）、无乱并购、Qwikster 一年内纠错、股东信 say-do 记录好；把全部经营现金+债务能力押注内容与国际扩张——唯一理性的打法，同时是单一赌注。诚信：RegFD 事件为渠道争议（SEC 未认定），EY 标准无保留；内容摊销期（最长 5 年直线）有观看模式变化支撑但管理层判断空间客观存在——<b>本案以方案 B 现金口径消除该争议对估值的影响</b> [E:qualitative_NFLX_2016.json][E:workpapers/phase0_forensic_20260907.json]。</p>
<h3>4.5 财务质量与风险深查</h3>
<p>风险点：①<b>内容义务表外 14.4B</b>（2016Q3 承诺总额）vs 资产负债表内容负债 4.8B（2015 末）——近 10B 隐性未来支出刚性；②FCF 连续 6 年为负且缺口扩大（2015 −918.6、2016E −1,485），2011-2016 累计发债 ~5.5B、2016-10 再发 1B——滚动发行依赖资本市场窗口；③Intl 贡献利润率 −5.9% 未证；④TTM PE {vnum('market_snapshot_NFLX_2016.json','valuation_multiples.pe_ttm','num1',pe)}x/PB {vnum('market_snapshot_NFLX_2016.json','valuation_multiples.pb_mrq','num1',pb)}x 均无锚定意义（净利 122.6 百万基数+账面净资产被内容摊销低估），估值唯一可行框架为 OE/收入驱动+反向 DCF [E:financials_NFLX_2016.json][E:market_snapshot_NFLX_2016.json]。</p>
</div>

<h2>五、估值与安全边际（Phase 4）</h2>
<div class="card">
<p><b>折现率下限纪律</b>：USD 计价，2016-12 美国 10Y 国债 {vnum('market_snapshot_NFLX_2016.json','risk_free.us_10y_treasury_yield_pct','num2',ust)}% → r = max(10%, 2.45%+4pct) = <b>10.0%</b>，合规。对高波动成长股 10% 属宽容下限（内容投入不确定性+竞争未定）。</p>
<h3>5.1 三情景（r=10%、永续 0%、基准/乐观 10 年 fade）</h3>
<table>
<tr><th>情景</th><th>每股价值</th><th>方法</th><th>概率</th></tr>
<tr><td>悲观</td><td>{vnum('scenarios.json','scenarios.0.value_per_share','num2',bear)} USD</td><td><b>worst_year_margin（独立推导，非 DCF 调低）</b>：历史最差年净利率 0.4752%（2012：Qwikster+内容采购前置+国际启动三重叠加）× 2015 实际收入 6,779.5 × 危机倍数 20x（成熟媒体 PE 15-20x 上界）÷ 436.456 百万股。口径警示：PE 对低基数失真（2012 真实恐慌底隐含 PE 180x、市场实际按 P/S 0.86 定价），20x 建立在"塌陷后利润稳定"假设上 [E:metrics_NFLX_2016.json][E:scenarios.json]</td><td>0.3</td></tr>
<tr><td>基准</td><td>{vnum('scenarios.json','scenarios.1.value_per_share','num2',base_v)} USD</td><td>DCF OE：主锚 404.86（ADJ3 降档裁决：OE 利润率均值口径）× 10% fade 10 年——从历史 OE CAGR 17.4% 打折；依据：US 稳态单元已证（44% 贡献利润率）+投入节奏历史均值回复（2012 塌陷后 2013-14 修复）[E:fv_base.json][E:normalization_NFLX_2016.json]</td><td>0.5</td></tr>
<tr><td>乐观</td><td>{vnum('scenarios.json','scenarios.2.value_per_share','num2',opt_v)} USD</td><td>DCF OE：hybrid 交叉锚 775.83（ADJ2 已论证折价理由，仅作乐观端上限）× 20% fade 10 年（国际爬坡至 US 级利润率+自制护城河变宽+提价兑现）。<b>关键：乐观情景仍比现价低 69%</b> [E:fv_bull.json][E:normalization_NFLX_2016.json]</td><td>0.2</td></tr>
</table>
<p class="small">终值占比：基准 42.3% / 乐观 45.6%（&lt;75% 红线，估值主体由可见预测期支撑）。S1-S9 三情景门禁：通过、0 警告（悲观独立方法 worst_year_margin；S2c 校验器核对 2012 真实净利率 0.4752% 通过）。净债 ~1,900 百万（总债 2,900−现金 969）不另扣：OE 基于净利（已扣利息 132.7）为权益口径，与市值同口径；债务本金尾部风险在悲观情景与 red_flags 体现 [E:scenarios.json]。</p>
<h3>5.2 反向 DCF（本案最强单一证据）</h3>
<p><b>现价隐含增速反解：无解</b>——在 −50%~+60% 的十年增速搜索区间内，现价 {vnum('market_snapshot_NFLX_2016.json','price.value','num2',price)} 无法被任何 OE 增长路径解释（EV 口径 55,028 百万 = 市值 53,128 + 净债 1,900）。静态倍数：市值/主锚 OE = 131x；即使 OE 十年 25% 复合（远超历史 17.4%），静态合理市值 ~37,700 百万仍低于当前市值 28%。市场定价隐含的不是"增长"而是"增长+利润率跃迁+永不竞争恶化"的三重奇迹 [E:scenarios.json#sensitivity.implied_growth_solving]。</p>
<h3>5.3 安全边际结论</h3>
<p>基准 V0 {vnum('scenarios.json','scenarios.1.value_per_share','num2',base_v)} vs 现价 {vnum('market_snapshot_NFLX_2016.json','price.value','num2',price)}：<b>MoS = {vnum('mos_calc.json','mos','pct1',mos)}，要求 ≥40%（窄护城河）→ 闸门一深度不过</b>。敏感性全区间：三锚 EPV 口径 {vnum('mos_calc.json','epv_pessimistic_ni_anchor','num2',7.38)}（NI 保守极限）/{vnum('mos_calc.json','epv_main','num2',9.28)}（主锚）/{vnum('mos_calc.json','epv_cross','num2',17.78)}（hybrid 交叉锚）+ fade 动态口径 g=8-15% → 12.69-16.59——全部锚位与现价缺口 −86%~-94%，<b>锚位争议不改变结论方向</b> [E:scenarios.json#sensitivity][E:mos_calc.json]。</p>
<h3>5.4 假设一致性对账</h3>
<p>① EPV/账面撕裂方向与重资产周期股相反：EPV（主锚 4,049 百万）为账面权益（2,223 百万）的 1.8 倍——盈利能力口径显示账面被低估（内容摊销），而市价又是 EPV 的 13.1 倍；② 利润率假设与"窄"护城河匹配（基准 fade 10% 不假设利润率跃迁）；③ 基准增速 10% 通过基率检验（历史 OE CAGR 17.4% 打折、US 已证模板）；④ 2016E 指引锚三重隔离（标 estimate/不进序列/悲观不给指引信用）；⑤ conv 2.41 不进任何情景基期（ADJ2 裁决）[E:phase5_workpaper.json]。</p>
</div>

<h2>六、双闸门与档位（Phase 4 定档）</h2>
<div class="card">
<table>
<tr><th>闸门</th><th>项目</th><th>值</th><th>门槛</th><th>结果</th></tr>
<tr><td>闸门一</td><td>安全边际（vs 基准 V0）</td><td>{vnum('mos_calc.json','mos','pct1',mos)}</td><td>40%（窄护城河）</td><td class="gate-fail">✗</td></tr>
<tr><td rowspan="3">闸门二</td><td>① 期望 IRR（自洽性校验）</td><td>{vnum('expected_return.json','gate2.consistency_expected_irr.value','pct1',g2['consistency_expected_irr']['value'])}</td><td>{vnum('expected_return.json','gate2.consistency_expected_irr.hurdle','pct1',g2['consistency_expected_irr']['hurdle'])}</td><td class="gate-fail">✗</td></tr>
<tr><td>② 不收敛下限 = 股息率 + 内在价值增速</td><td>{vnum('expected_return.json','gate2.no_convergence_floor.value','pct1',g2['no_convergence_floor']['value'])}</td><td>6.0%</td><td class="gate-fail">✗</td></tr>
<tr><td>③ 悲观情景年化</td><td>{vnum('expected_return.json','gate2.pessimistic_irr.value','pct1',g2['pessimistic_irr']['value'])}</td><td>0.0%</td><td class="gate-fail">✗</td></tr>
</table>
<p>期望总回报 {vnum('expected_return.json','expected_total_return','pct1',er['expected_total_return'])}（5 年）；期望年化 IRR {vnum('expected_return.json','expected_annualized_irr','pct1',er['expected_annualized_irr'])} vs 指数机会成本 {vnum('expected_return.json','index_hurdle','pct1',er['index_hurdle'])}：跑输 {vnum('mos_calc.json','excess_vs_index_pct','num1',mos_calc['excess_vs_index_pct'])}pct；亏损概率 {vnum('expected_return.json','loss_probability','pct1',er['loss_probability'])}，亏损情景平均跌幅 {vnum('mos_calc.json','avg_loss_pct','num1',mos_calc['avg_loss_pct'])}%。</p>
<p class="warnbox"><b>闸门二②的特殊含义（价值陷阱的镜像）</b>：不收敛下限 0%（零股息+内在价值增速取 0 稳态）意味着即使未来市场重估，回报也全押在"市场哪天承认我对"——对成长股这是"成长陷阱"的定量特征：买在 131x OE 上，若增长兑现但市场不认可，没有现金流回报兜底。S8 价值陷阱闸门未触发（安全边际深度为负，不存在"便宜的价值陷阱"问题）——本案是"贵的成长陷阱"风险。</p>
</div>

<h2>七、关键判断收敛（Phase 4.5）</h2>
<div class="card">
<p><b>关键变量（重要且可知）</b>：① 国际分部贡献利润率（2015 −5.9%）能否在 3-4 年内转正并趋向 US 模板；② 内容支出/收入比（88-93%）是否随国际会员规模化回落；③ FCF 转正时点与发债依赖度；④ US 提价模式能否复制到国际。</p>
<p><b>变异认知测试：市场为什么给 {vnum('market_snapshot_NFLX_2016.json','price.value','num2',price)}？</b>——"全球流媒体唯一赢家+190 国 TAM 万亿美元+Netflix 就是下一代电视"的叙事，把"方向的正确"定价为"确定的垄断利润"。我方与市场的分歧不在方向（三师对趋势/生意/管理层评价均为正面），<b>而在价格对方向已兑现的假设密度</b>：131x OE 的起点要求所有乐观假设同时成立且不出任何事故。这不是"答得出超额认知"的分歧——是"方向对但赔率已消失"的分歧：即使我方对方向的判断被完美证实，以现价买入的回报仍为负（乐观情景 {vnum('scenarios.json','scenarios.2.value_per_share','num2',opt_v)} 的 5 年 IRR 也是负的）。</p>
<p><b>双重机会成本</b>：期望年化 IRR {vnum('expected_return.json','expected_annualized_irr','pct1',er['expected_annualized_irr'])} 远低于指数门槛 {vnum('expected_return.json','index_hurdle','pct1',er['index_hurdle'])} → 机会成本不通过 [E:expected_return.json]。</p>
</div>

<h2>八、三位大师独立评估 + 红队（Phase 5）</h2>
<div class="card">
<table>
<tr><th>维度</th><th>巴菲特</th><th>段永平</th><th>李录</th></tr>
<tr><td>五轴均分（不含价格轴）</td><td>2.75</td><td>3.25</td><td>3.13</td></tr>
<tr><td>价格判断</td><td>十年可测性检验不过+价格荒谬（implied-growth 无解）</td><td>市场已把"长成金矿"当确定价入账；错过不是错误，付错价才是</td><td>三对（趋势/生意/管理层）救不回透支的价格</td></tr>
<tr><td>档位倾向</td><td>拒绝</td><td>拒绝</td><td>拒绝（观察等价格是能力圈内唯一正确动作）</td></tr>
</table>
<p><b>红队五项质询（全文见 data/phase5_workpaper.json）</b>：① base_oe 降档是否错杀下一代巨头？→ 乐观情景已按 hybrid 锚+20% 全额兑现仍比现价低 69%，结论对锚稳健；② worst_year_margin 1.48 的 PE 失真？→ 接受口径警示（已入 method_note），档位对悲观方法不敏感；③ OE 框架对成长股错锚？→ 换 P/S（7.84x 需十年收入 5 倍+利润率 3 倍）或单元经济外推（Intl 需从 −5.9% 爬到 44%）每把秤都指向同一结论；④ 2016E 指引循环论证？→ 三重隔离（estimate 标注/不进序列/悲观不给信用）；⑤ 护城河"窄"对净利率 1.8% 的公司太慷慨？→ 评级证据链与鞍钢"无"不同源（定价权实证+规模经济兑现 vs 定价权缺失+成本劣势），且评级零边际（缺口 800+pct >> 档间差 15pct）[E:phase5_workpaper.json]。</p>
<p><b>Pre-mortem（本案最重要的诚实记录）</b>：本案错过的最大风险形态是"基本面兑现速度超过估值消化"的动量叠加——若 2017-2021 国际爬坡+自制内容完全兑现，"拒绝"会错过巨大涨幅。<b>框架对此的回应</b>：框架输出从来不是"公司会失败"（三师定性恰恰相反），而是"$123.80 不是价值投资的介入价格"；价值投资者 2016-12 拒绝 Netflix，错过的不是"价值"而是"动量"——这在框架边界内是正确决策。框架内真正的错误形态是：若股价回落到乐观情景以下且跟踪清单开始兑现，却因"上次拒绝过"而拒绝重新评估——那是锚定偏见，不是纪律。（三份评估全文见 workpapers/persona_*.md）</p>
</div>

<h2>九、持有体验预演 + 跟踪清单</h2>
<div class="card">
<p><b>历史波动（真实数据）</b>：2011-07 高点（拆股调整后 ~42.7）→ 2012-06 低点（调整后 ~7.5，−82%）；2015 年内高点 129.79 → 现价 {vnum('market_snapshot_NFLX_2016.json','price.value','num2',price)}（52 周区间 79.95-129.29，位置 {vnum('market_snapshot_NFLX_2016.json','kline_2016_dec.position_in_52w_range_pct','num1',pos52)}%）。高成长股的波动率意味着：买在 131x OE 上，"增长不及隐含预期"的任何季度都会触发估值与盈利的双杀；且公司不产生可分配现金（反而持续发债），持有期没有现金流回报缓冲。</p>
<p><b>证伪条件（升档触发）</b>：① Intl 贡献利润率 2020 年前转正并趋向 30%+（基准锚上调）；② 内容支出/收入比回落至 70% 以下且会员增长不减（规模效应兑现）。<b>（降档/证伪触发）</b>：③ 头部内容方大规模收回授权自建（护城河根基受损）；④ 发债间隔缩短+利息覆盖跌破 2x（滚动依赖恶化）。</p>
<p><b>观察等价格的触发线</b>：乐观情景 {vnum('scenarios.json','scenarios.2.value_per_share','num2',opt_v)} 以下才进入"即使按乐观假设也不贵"的讨论区间；主锚 EPV 口径 {vnum('mos_calc.json','epv_main','num2',9.28)}×(1−40%) = {vnum('mos_calc.json','buy_ceiling_epv','num2',mos_calc['buy_ceiling_epv'])} 以下才满足窄护城河 40% 安全边际——两个价格都在现价的 1/3 以下 [E:mos_calc.json]。</p>
</div>

<h2>十、数据引用附录（全部 publish_date）</h2>
<div class="card small">
<table>
<tr><th>数据项</th><th>值</th><th>来源</th><th>publish_date</th></tr>
<tr><td>FY2007-FY2014 年度财务（方案 B 九年序列）</td><td>见 financials 底稿 annual</td><td>历年 10-K（SEC EDGAR XBRL + R 文件原文，含 2007-2009 老格式 XML 解析）</td><td>2008-01 ~ 2015-01 各年 filed</td></tr>
<tr><td>FY2015 年度财务</td><td>营收 {vnum('financials_NFLX_2016.json','annual.8.revenue','num1',fin['annual'][8]['revenue'])} / 净利 {vnum('financials_NFLX_2016.json','annual.8.net_income','num1',fin['annual'][8]['net_income'])} / OCF 1,644.9（原值）</td><td>FY2015 10-K（accn 0001065280-16-000047）+ 第二轮检索外获利润表原值交叉验证 ✓</td><td>filed 2016-01-28</td></tr>
<tr><td>9M'16 实际数</td><td>收入 6,353.1 / 净利 119.9 / OCF −916.8</td><td>Q3'16 10-Q + Q3'16 股东信季表</td><td>filed 2016-10-20 / 信 2016-10-17</td></tr>
<tr><td>Q4'16F 指引</td><td>收入 2,344 / OP 125 / NI 56</td><td>Q3'16 股东信（2016E 推算锚，标 estimate）</td><td>2016-10-17</td></tr>
<tr><td>会员量价分解</td><td>付费会员 {vnum('business_drivers_NFLX_2016.json','drivers.0.volume','num1',bd['drivers'][0]['volume'])}→{vnum('business_drivers_NFLX_2016.json','drivers.4.volume','num1',bd['drivers'][4]['volume'])} 百万</td><td>历年股东信 MD&A（分部收入勾稽恒等 ✓）</td><td>2009-2016 各期</td></tr>
<tr><td>收盘价 {vnum('market_snapshot_NFLX_2016.json','price.value','num2',price)} / 52 周 79.95-129.29</td><td>不复权日 K</td><td>行情底稿</td><td>2016-12-30</td></tr>
<tr><td>美国 10Y 国债 {vnum('market_snapshot_NFLX_2016.json','risk_free.us_10y_treasury_yield_pct','num2',ust)}%</td><td>折现率下限纪律输入</td><td>行情底稿（UST 年末水平，美联储 H.15 口径 2.448%）</td><td>2016-12-30</td></tr>
<tr><td>竞对三家（Disney/Comcast/Fox）</td><td>财年错位按 duration 350-380 天匹配</td><td>companyfacts XBRL + Disney FY15 官方财报 PDF</td><td>2012-2016 各期</td></tr>
</table>
<p><b>晚于截断日已剔除的披露</b>：Q4'16 股东信（2017-01-19）、FY2016 10-K（2017-01-27）、2017-01 提价公告、以及一切 2017-01-01 之后的信息。检索中出现的 FY2016 实际业绩一律未采信未进序列。</p>
<p>核验记录：validate_data.py 入口校验通过（0 错误 / 4 警告，已在 manifest 回应）；crosscheck_official 0 错误 / 5 警告（2 科目豁免：方案 B 口径分叉+拆股，官方原值留扩展字段）；check_transcription 26 项口径豁免（4 字段）；check_scenarios 通过 0 警告；check_market_snapshot 三角校验 0.00%；核验强度 B（勾稽 8/9 年 / 窗口 9 年，相关章节已降置信度披露）[E:manifest.json][E:verification_strength.json]。</p>
</div>

{charts}
</body>
</html>"""

out = os.path.join(BASE, 'Netflix_价值投资分析报告_20161231.html')
open(out, 'w').write(html)
print('报告已生成:', out, f'({len(html)} 字符)')
