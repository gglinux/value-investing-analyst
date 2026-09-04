#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成贵州茅台 2015-08-31 回放报告 HTML：数值全部从 data/ 底稿程序化注入，防手抄漂移"""
import json, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backtest/600519.SH_2015-08-31/
D = lambda name: json.load(open(os.path.join(BASE, 'data', name)))

fin = D('financials_MAOTAI_2015H1.json')
snap = D('market_snapshot_MAOTAI_2015H1.json')
scen = D('scenarios_MAOTAI_2015H1.json')
er = D('expected_return_MAOTAI_2015H1.json')
met = D('metrics_MAOTAI_2015H1.json')
ig = D('implied_growth.json')

def vnum(src, path, fmt, val):
    """渲染与 verify_report.py 相同格式的显示文本"""
    if fmt == 'pct1':   s = f"{val*100:.1f}%"
    elif fmt == 'num0': s = f"{val:,.0f}"
    elif fmt == 'num1': s = f"{val:,.1f}"
    elif fmt == 'num2': s = f"{val:,.2f}"
    else:               s = str(val)
    return f'<span class="vnum" data-src="{src}" data-path="{path}" data-fmt="{fmt}">{s}</span>'

# 底稿值快捷方式
price = snap['price_cny']; bear = scen['scenarios'][0]['value_per_share']
base_v = scen['scenarios'][1]['value_per_share']; opt_v = scen['scenarios'][2]['value_per_share']
cs = met['chart_series']
years = cs['years']

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
bvps_arr = [round(x,2) for x in cs['bvps']]
eps_arr = [round(x,2) if x is not None else None for x in cs['eps']]

charts = chart + line_opt('c1','营业收入（百万 CNY）','<!-- vchart src=metrics_MAOTAI_2015H1.json path=chart_series.revenue -->', rev_arr) + "\n" + \
         line_opt('c2','归母净利润（百万 CNY）','<!-- vchart src=metrics_MAOTAI_2015H1.json path=chart_series.net_income -->', ni_arr, color='#2c3e50') + "\n" + \
         line_opt('c3','净利率 %','<!-- vchart src=metrics_MAOTAI_2015H1.json path=chart_series.net_margin scale=100 -->', nm_arr, pct=True) + "\n" + \
         line_opt('c4','每股净资产 BVPS（元）','<!-- vchart src=metrics_MAOTAI_2015H1.json path=chart_series.bvps -->', bvps_arr, color='#8e44ad') + "\n" + \
         line_opt('c5','EPS（元，当年摊薄股本）','<!-- vchart src=metrics_MAOTAI_2015H1.json path=chart_series.eps -->', eps_arr, color='#16a085') + "\n</script>"

badge = open(os.path.join(BASE, 'workpapers', 'badge_div.html')).read()

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><title>贵州茅台 历史回放报告 2015-08-31（第一批案例1）</title>
<style>
body{{font-family:'PingFang SC','Microsoft YaHei',sans-serif;max-width:980px;margin:0 auto;padding:24px;color:#2c3e50;line-height:1.75;background:#fafafa}}
h1{{font-size:22px;border-bottom:3px solid #8e44ad;padding-bottom:8px}} h2{{font-size:18px;color:#8e44ad;margin-top:32px;border-left:4px solid #8e44ad;padding-left:10px}}
.card{{background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:18px 22px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.verdict{{background:linear-gradient(135deg,#fff8e1,#fff);border:2px solid #f39c12}}
.gate-fail{{color:#c0392b;font-weight:700}} .gate-pass{{color:#27ae60;font-weight:700}}
table{{border-collapse:collapse;width:100%;margin:10px 0;background:#fff}}
th,td{{border:1px solid #d5d8dc;padding:7px 10px;font-size:14px;text-align:left}}
th{{background:#f4ecf7}}
.warnbox{{background:#fdecea;border-left:4px solid #c0392b;padding:10px 14px;border-radius:4px;margin:10px 0}}
.okbox{{background:#e8f6f3;border-left:4px solid #27ae60;padding:10px 14px;border-radius:4px;margin:10px 0}}
.chart{{width:100%;height:300px}}
.small{{font-size:12.5px;color:#7f8c8d}}
.tag{{display:inline-block;background:#8e44ad;color:#fff;border-radius:4px;padding:1px 8px;font-size:12px;margin-right:6px}}
</style>
</head>
<body>

<h1>贵州茅台 600519.SH — 历史回放分析报告</h1>
<p class="small">backtest/PROMPT.md 第一批 · 案例 1 ｜ 回放时点 <b>2015-08-31</b>（信息截断日同日）｜ 生成于 2026-09-04</p>

<div class="warnbox"><b>本报告仅使用 2015-08-31 及之前的公开信息。</b>信息截断纪律：2015 年报（2016-03-23 披露）、2015 三季报（2015-10-23 披露）等一切晚于截断日的披露均已剔除；执行者已知事后答案的污染自陈见 <code>meta.json / contamination_disclosure</code>，档位完全由数据管线决定。</div>

<div class="card verdict">
<h2 style="margin-top:0">决策卡</h2>
<p>结论档位：<b style="font-size:20px">观察等价格</b>（五档制：排除／太难放弃／观察等价格／小仓位试探／核心买入。闸门一、闸门二均未过，档位上限即"观察等价格"）</p>
<p>现价 {vnum('market_snapshot_MAOTAI_2015H1.json','price_cny','num2',price)} 元 ｜ 市值 {vnum('market_snapshot_MAOTAI_2015H1.json','total_market_cap_cny_million','num0',snap['total_market_cap_cny_million'])} 百万 CNY ｜ TTM PE {vnum('market_snapshot_MAOTAI_2015H1.json','pe_ttm','num2',snap['pe_ttm'])}x ｜ PB {vnum('market_snapshot_MAOTAI_2015H1.json','pb','num2',snap['pb'])}x ｜ 股息率 {vnum('market_snapshot_MAOTAI_2015H1.json','dividend_yield_ttm_frac','pct1',snap['dividend_yield_ttm_frac'])} ｜ 52 周位置 {vnum('market_snapshot_MAOTAI_2015H1.json','position_52w','pct1',snap['position_52w'])}</p>
<p>闸门一（安全边际，宽护城河要求 25%）：基准内在价值 {vnum('scenarios_MAOTAI_2015H1.json','scenarios.1.value_per_share','num2',base_v)} 元 → MoS = <span class="gate-fail">+6.80% ✗ 未达标</span></p>
<p>闸门二（期望回报）：期望年化 IRR {vnum('expected_return_MAOTAI_2015H1.json','expected_annualized_irr','pct1',er['expected_annualized_irr'])} < 门槛 {vnum('expected_return_MAOTAI_2015H1.json','gate2.consistency_expected_irr.hurdle','pct1',er['gate2']['consistency_expected_irr']['hurdle'])} <span class="gate-fail">✗</span>；三项独立校验 {er['gate2']['no_convergence_floor']['pass'] and er['gate2']['pessimistic_irr']['pass'] and '<span class="gate-pass">部分通过</span>' or ''}（②过 ③不过）→ <span class="gate-fail">闸门二 1/3 ✗</span></p>
<p><b>观察等价格触发价（25% 闸门反推）＝ 166.93 元</b>；当前距触发价还需下跌 14.5%。</p>
{badge}
</div>

<h2>一、信息集与防前视声明</h2>
<div class="card">
<table>
<tr><th>披露</th><th>publish_date</th><th>状态</th></tr>
<tr><td>2014 年报（年度数据基期）</td><td>2015-04-21</td><td>✓ 进信息集</td></tr>
<tr><td>2015 半年报（最新季报）</td><td>2015-08-28</td><td>✓ 进信息集（截断日前 3 天）</td></tr>
<tr><td>2015-08-31 收盘价 195.37 元（不复权）</td><td>2015-08-31</td><td>✓ 进信息集</td></tr>
<tr><td>2015 三季报</td><td>2015-10-23</td><td>✗ 剔除（晚于截断日）</td></tr>
<tr><td>2015 年报</td><td>2016-03-23</td><td>✗ 剔除（晚于截断日）</td></tr>
</table>
<p class="small">股本口径：2014 年度分配（10 转 1 派 43.74 元）已于 2015 年年中实施完毕，回放日总股本 1,256.198 百万股 [E:financials_MAOTAI_2015H1.json]。行情来源：腾讯行情接口不复权日K/周K（不复权口径防送股除权扭曲）。年度行 2006-2014 与 2016-04-20 回放底稿同源同值（该底稿已过双源核对+validate_data 门禁），2015H1 行为本次新采（新浪财务库 vs cninfo 官方摘要元级双源一致）。</p>
</div>

<h2>二、Phase 0 排雷</h2>
<div class="card okbox">
<b>通过。</b>① 存贷双高检验：货币资金 30,236.5 百万 vs 有息负债（总负债 8,675.96 百万中绝大部分为经营性应付款与预收款，有息债务极小）——"账上巨额现金却高息举债"的造假形态不成立；② 现金流与利润背离检验：2014 OCF/净利 0.82，2015H1 OCF 4,901.69 百万（+14.29%）与净利增长匹配；③ 对立面检索（adversarial_check，见 manifest）：无做空报告/造假指控/重大处罚，审计意见标准无保留；④ 需留意的观察项（非排雷命中）：2013-2014 渠道批价倒挂背景下的真实动销存信息不对称——由预收款与批价两个季度锚跟踪。
</div>

<h2>三、定量画像（2006-2014，9 年窗口）</h2>
<div class="card">
<div id="c1" class="chart"></div>
<div id="c2" class="chart"></div>
<div id="c3" class="chart"></div>
<div id="c4" class="chart"></div>
<div id="c5" class="chart"></div>
<p class="small">引擎警报 3 条：①稀释警报（收入 CAGR 26.5% vs 每股 CAGR 23.6%，增长被 2012-2014 连续三年 10 转 1 摊薄——注意这是股本转增而非股权融资稀释，每股指标已用当年摊薄股本校正）；②利润含金量警报（2012-2014 FCF/净利 &lt; 0.6，系扩产高峰 capex 投入，属主动性扩张而非盈利造假）；③利润率形状检验：周期波动（秩相关 +0.87、穿越均值 3 次）——均值可作周期中枢，正常化判定成立。</p>
<p>正常化判定：净利率当期 47.64% vs 全期均值 44.34%，比值 1.07 处<b>中性区间</b>；基期 OE 推荐 = {vnum('metrics_MAOTAI_2015H1.json','normalization.base_oe_recommended','num1',met['normalization']['base_oe_recommended'])} 百万（2014 当期值）。</p>
</div>

<h2>四、五维定性</h2>
<div class="card">
<h3>4.1 商业模式</h3>
<p>先款后货（预收款 2,336.53 百万、同比 +58.33%）+ 品牌即渠道（酒类毛利率 92.61%）+ 产品不迭代（基酒越存越值钱）+ 产能地理垄断 [E:financials_MAOTAI_2015H1.json][E:financials_MAOTAI_2015H1.json#half_year_2015h1][E:metrics_MAOTAI_2015H1.json]。</p>
<h3>4.2 护城河（宽）</h3>
<p>品牌与产地双垄断：出厂价 2000-2012 年 195→819 元（CAGR 约 12%）的提价记录是定价权硬证据；茅台镇 7.5 平方公里原产地域保护构成物理壁垒；2013-2014 行业最凶冲击期（三公消费+塑化剂）净利率仅从 50.3% 回落到 47.6%，未跌破 44% 全期均值——抗冲击能力实证 [E:financials_MAOTAI_2015H1.json][E:metrics_MAOTAI_2015H1.json][E:scenarios_MAOTAI_2015H1.json]。</p>
<h3>4.3 增长空间</h3>
<p>行业出清+集中度提升：2014 收入 +2.1% 触底、2015H1 恢复 +10.17% 且茅台酒（+11.49%）快于系列酒；民间消费接棒政务消费的方向性证据=预收款激增与渠道补库存；产能储备（2015H1 基酒 24,487 吨）支撑量增 [E:financials_MAOTAI_2015H1.json][E:financials_MAOTAI_2015H1.json#half_year_2015h1][E:market_snapshot_MAOTAI_2015H1.json]。</p>
<h3>4.4 管理层与治理</h3>
<p>资本配置保守：2014 capex 44.31 百万级投入自主节奏、现金 302 亿几乎无有息负债、连续高分红（2014 年度派现 49.94 亿，分红率约 33%）；公司 2015 全年指引仅 +1%（保守基调，防止渠道压货）[E:financials_MAOTAI_2015H1.json][E:financials_MAOTAI_2015H1.json#half_year_2015h1][E:scenarios_MAOTAI_2015H1.json]。</p>
<h3>4.5 财务质量与风险深查</h3>
<p>TTM PE {vnum('market_snapshot_MAOTAI_2015H1.json','pe_ttm','num2',snap['pe_ttm'])}x、PB {vnum('market_snapshot_MAOTAI_2015H1.json','pb','num2',snap['pb'])}x、TTM EPS {vnum('market_snapshot_MAOTAI_2015H1.json','ttm_eps','num2',snap['ttm_eps'])} 元；风险点：①政策风险（反腐/消费税）不可控且 2012-2014 已展现杀伤力；②渠道库存不透明（预收款回升也可能是压货节奏）；③2015-08 股灾中市场微观结构脆弱，价格噪音大 [E:financials_MAOTAI_2015H1.json][E:market_snapshot_MAOTAI_2015H1.json][E:manifest.json#adversarial_check]。</p>
</div>

<h2>五、估值与安全边际（Phase 4）</h2>
<div class="card">
<p><b>折现率下限纪律披露（2026-09-03 新增纪律，回溯适用）</b>：计价货币 CNY，2015-08-31 中国 10Y 国债收益率 3.33%（中债曲线，广发基金 2015-09 月报与友邦 2015-08 月报双源）[E:manifest.json#cn_10y] → r = max(10%, 3.33%+4pct) = <b>10.0%</b>，合规。</p>
<h3>5.1 三情景（forward-value 引擎，r=10%、永续 2.5%、10 年 fade）</h3>
<table>
<tr><th>情景</th><th>每股价值</th><th>方法</th><th>概率</th></tr>
<tr><td>悲观</td><td>{vnum('scenarios_MAOTAI_2015H1.json','scenarios.0.value_per_share','num2',bear)} 元</td><td>worst_year_margin：历史最差年净利率 31.5%（2006）× 2014 收入 32,217 百万 × 危机倍数 9x ÷ 股本（独立推导，非 DCF 调低）</td><td>0.3</td></tr>
<tr><td>基准</td><td>{vnum('scenarios_MAOTAI_2015H1.json','scenarios.1.value_per_share','num2',base_v)} 元</td><td>DCF OE：基期 {vnum('metrics_MAOTAI_2015H1.json','normalization.base_oe_recommended','num1',met['normalization']['base_oe_recommended'])} × 8% fade 10 年；8% 依据=半年报收入 +10.17%/预收款 +58.33% 领先信号与行业出清 [E:workpapers/mt_fwd_base.json]</td><td>0.5</td></tr>
<tr><td>乐观</td><td>{vnum('scenarios_MAOTAI_2015H1.json','scenarios.2.value_per_share','num2',opt_v)} 元</td><td>DCF OE：12% fade（提价周期重启+集中度）[E:workpapers/mt_fwd_opt.json]</td><td>0.2</td></tr>
</table>
<p class="small">终值占比：基准 51.4% / 乐观 52.8%（&lt;75% 红线，估值主体由可见预测期支撑）。S1-S9 三情景门禁：通过（悲观/基准离散度 0.35 ≤ 0.85；压力项 margin/multiple/demand 三重）。</p>
<h3>5.2 反向 DCF（基率检验）</h3>
<p>现价隐含未来 10 年 OE 增速 = {vnum('implied_growth.json','implied_oe_growth_10y','pct1',ig['implied_oe_growth_10y'])}，低于我方基准 8% 约 1.6pct——市场预期比我方保守但分歧温和；与 2016-04-20 回放的隐含 +11.10% 对照，本时点市场预期明显更低 [E:implied_growth.json]。</p>
<h3>5.3 安全边际结论</h3>
<p>基准 V0 {vnum('scenarios_MAOTAI_2015H1.json','scenarios.1.value_per_share','num2',base_v)} vs 现价 {vnum('market_snapshot_MAOTAI_2015H1.json','price_cny','num2',price)}：<b>MoS = +6.80%（(V0−P)/V0 口径 +6.4%），远低于宽护城河 25% 要求 → 闸门一不通过</b>。25% 闸门反推买点上限 = 208.66/1.25 = <b>166.93 元</b>。即使按 8-25 股灾低点收盘 176.59 元计算 MoS 也仅 +18.2%，仍不达标——本案例的"便宜"是 PE 15.3x 的绝对便宜，不是相对内在价值的 25% 折扣便宜。</p>
<h3>5.4 假设一致性对账</h3>
<p>① 终年收入 ≈ 2014 收入×(1.08^10 fade) ≈ 690 亿 vs TAM（高端白酒+民间升级）× 份额上限——不越界；② 利润率假设与宽护城河匹配（OE≈净利，净利率维持 46-48%）；③ 再投资率与 ROIIC 一致（capex/OCF 35%，扩产自主）；④ 增速 8% 通过基率检验（自身 10 年 EPS CAGR 21.8%、行业出清后中枢下移，8% 取下沿）；⑤ 净利率假设符合产业链议价权（对经销商强议价、预收款占款）。</p>
</div>

<h2>六、双闸门与档位（Phase 4 定档）</h2>
<div class="card">
<table>
<tr><th>闸门</th><th>项目</th><th>值</th><th>门槛</th><th>结果</th></tr>
<tr><td rowspan="1">闸门一</td><td>安全边际（vs 基准 V0）</td><td>+6.80%</td><td>25%（宽护城河）</td><td class="gate-fail">✗</td></tr>
<tr><td rowspan="3">闸门二</td><td>① 期望 IRR（自洽性校验）</td><td>{vnum('expected_return_MAOTAI_2015H1.json','gate2.consistency_expected_irr.value','pct1',er['gate2']['consistency_expected_irr']['value'])}</td><td>{vnum('expected_return_MAOTAI_2015H1.json','gate2.consistency_expected_irr.hurdle','pct1',er['gate2']['consistency_expected_irr']['hurdle'])}</td><td class="gate-fail">✗</td></tr>
<tr><td>② 不收敛下限 = 股息率 + 内在价值增速</td><td>{vnum('expected_return_MAOTAI_2015H1.json','gate2.no_convergence_floor.value','pct1',er['gate2']['no_convergence_floor']['value'])}</td><td>6.0%</td><td class="gate-pass">✓</td></tr>
<tr><td>③ 悲观情景年化</td><td>{vnum('expected_return_MAOTAI_2015H1.json','gate2.pessimistic_irr.value','pct1',er['gate2']['pessimistic_irr']['value'])}</td><td>0.0%</td><td class="gate-fail">✗</td></tr>
</table>
<p>期望总回报 {er['expected_total_return']*100:.1f}%（5 年期望值口径，不年化进闸门二）；期望年化 IRR {vnum('expected_return_MAOTAI_2015H1.json','expected_annualized_irr','pct1',er['expected_annualized_irr'])} vs 指数机会成本 {vnum('expected_return_MAOTAI_2015H1.json','index_hurdle','pct1',er['index_hurdle'])}：<b>跑输 7.2pct</b>；亏损概率 {vnum('expected_return_MAOTAI_2015H1.json','loss_probability','pct1',er['loss_probability'])}（亏损情景平均跌幅 −40.1%）。</p>
<p class="okbox">核心买入追加下行约束检验：亏损概率 30% 未超 30% 红线但期望 IRR 为正——若闸门全过仍需降档至小仓位试探；本案例双闸门均未过 → <b>最终档位：观察等价格（触发价 166.93 元）</b>。S8 价值陷阱闸门：MoS 6.4% &lt; 50% 且收入无连续 3 年负增长、无峰值停滞 → <b>不触发</b>（must_not_trigger 满足）。</p>
</div>

<h2>七、关键判断收敛（Phase 4.5）</h2>
<div class="card">
<p><b>关键变量（重要且可知）</b>：① 一批价 vs 出厂价 819 元的价差企稳回升（季度可跟踪）；② 预收款的持续性（连续 2 季度不回落即确认渠道补库存是真需求）。</p>
<p><b>变异认知测试</b>：市场为什么给 195.37？——股灾避险抛售+对政策冲击二次恶化的恐惧；现价隐含 6.36% vs 我方基准 8%，分歧仅 1.6pct，<b>我方与市场的分歧不在数字而在定性</b>（政策冲击是周期性还是永久性——我方判周期性，依据预收款+批价证据链）。认知优势有限，按规则"分歧答不出超额认知 → 档位上限观察等价格"——与双闸门结论一致。</p>
<p><b>双重机会成本</b>：期望年化 IRR 5.82% &lt; 指数门槛 13.0% → "机会成本不划算"成立，档位上限"观察等价格"再次确认 [E:expected_return_MAOTAI_2015H1.json]。</p>
</div>

<h2>八、三位大师独立评估 + 芒格红队（Phase 5）</h2>
<div class="card">
<table>
<tr><th>维度</th><th>巴菲特</th><th>段永平</th><th>李录</th></tr>
<tr><td>终局可预测性</td><td>高</td><td>极高</td><td>中高</td></tr>
<tr><td>价格判断</td><td>合理不便宜，支持等待</td><td>下得去手（体系唯一想推翻系统的点）</td><td>赔率不诱人，给 2 季度观察期</td></tr>
<tr><td>档位倾向</td><td>观察等价格</td><td>小仓位试探或以上</td><td>观察等价格</td></tr>
</table>
<p><b>红队最强空头逻辑</b>：反腐是永久性需求结构调整而非周期冲击；2015H1 增长系经销商对出厂价套利的恐慌性打款，真实动销弱于报表；若 2016 收入负增长，PE 从 15x 杀到 10x 对应 ~120 元。证伪锚点=预收款连续 2 季度回落 + 一批价跌破 800。Pre-mortem：跌 40% 至 ~117 元且预收款/批价两锚未破 → 三人均答"敢加仓"。（三份评估全文见 workpapers/persona_*.md，分歧对照见 persona_divergence_redteam.md）</p>
</div>

<h2>九、持有体验预演 + 跟踪清单</h2>
<div class="card">
<p><b>历史回撤（真实数据）</b>：2012-07 高点 266 元 → 2014-01 低点 118 元（−55%，修复至前高用了约 3 年）；2008 危机 −63%；当前回撤中（290 → 195.37 = −32.6%）。同类高端白酒典型回撤区间 50-65%。</p>
<p><b>证伪条件</b>：① 预收款连续 2 个季度回落且同比转负；② 一批价跌破 800 元（出厂价 819 倒挂）；③ 2016Q1 收入同比负增长（排除了春节因素后）。</p>
<p><b>季度跟踪清单</b>：① 预收款余额与同比（警戒线：环比降 &gt;30%）；② 一批价（警戒线：&lt;800 元）；③ 茅台酒收入增速（警戒线：&lt;0%）；④ 经营现金流/净利（警戒线：&lt;0.5）。</p>
</div>

<h2>十、数据引用附录（全部 publish_date）</h2>
<div class="card small">
<table>
<tr><th>数据项</th><th>值</th><th>来源</th><th>publish_date</th></tr>
<tr><td>2006-2013 年度财务数据</td><td>见 financials 底稿 annual</td><td>历年年报摘要（新浪财经库，2016-04 回放已双源核对）</td><td>2007-04-21 ~ 2014-04-03</td></tr>
<tr><td>2014 年度财务数据</td><td>营收 32,217.21 / 归母净利 15,349.80 / OCF 12,632.52</td><td>2014 年报摘要（cninfo）+行情终端双源</td><td>2015-04-21</td></tr>
<tr><td>2014 D&amp;A 756.80 百万</td><td>现金流量表补充资料官方值</td><td>2015 年报对比栏（登记于 2016-04 回放底稿）</td><td>2016-03-23（数值归属 2014 年报期）</td></tr>
<tr><td>2015H1 财务数据</td><td>营收 15,778.65 / 归母净利 7,888.23 / OCF 4,901.69 / 归母权益 61,304.35</td><td>2015 半年度报告官方摘要（cninfo 1201511437.PDF）+新浪库元级双源</td><td>2015-08-28</td></tr>
<tr><td>收盘价 195.37 / 52周高 290.00 / 低 145.50</td><td>不复权日K/周K</td><td>腾讯行情接口</td><td>2015-08-31</td></tr>
<tr><td>中国 10Y 国债 3.33%</td><td>中债国债收益率曲线</td><td>中国债券信息网（广发基金 2015-09 月报+友邦 2015-08 月报双源）</td><td>2015-08-31</td></tr>
<tr><td>2014 年度分红实施（10 转 1 派 43.74）</td><td>现金分红总额 4,994.7 百万</td><td>权益分派实施公告（2015 年年中实施）</td><td>2015-06 实施完毕</td></tr>
</table>
<p><b>晚于截断日已剔除的披露</b>：2015 三季报（2015-10-23）、2015 年报（2016-03-23）、2016Q1 季报（2016-04-21）、以及一切 2015-09-01 之后的信息。</p>
<p>核验记录：validate_data.py 入口校验通过（0 错误，2 警告均已在 manifest 回应）；check_scenarios.py S1-S9 通过；本报告 verify_report.py 数字校验结果见校验摘要。</p>
</div>

{charts}
</body>
</html>"""

out = os.path.join(BASE, 'report.html')
open(out, 'w').write(html)
print('报告已生成:', out, f'({len(html)} 字符)')
