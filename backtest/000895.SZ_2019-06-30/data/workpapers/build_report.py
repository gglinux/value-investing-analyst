#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_report.py — 双汇发展（000895.SZ）@2019-06-30 报告生成器（B2-12 补录）

本报告为 Step 2 交付物补录：verdict.json（commit be12f9d）与 Step 4（d920528）
均已完成，本脚本只把既有底稿渲染为标准 HTML——不引入任何新判断、不改任何数字。
数字全部从 data/ 底稿 JSON 读取渲染（vnum span / vchart 锚点），避免手抄漂移。
运行：python3 data/workpapers/build_report.py
"""
import json
import os

CASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
        elif fmt == "pct0":
            text = f"{val:.0%}"
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
    vals = [round(x * scale, 4) if x is not None else None for x in gp(d, path)]
    yr = gp(d, "chart_series.years")
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
  xAxis: {{type: "category", data: {json.dumps([str(y) for y in yr])}}},
  yAxis: {{type: "value", name: "{yname}"}}
}});
}})();
</script>
<p class="small">{note}</p>'''


SNAP = "market_snapshot_000895_2019.json"
SCEN = "scenarios_000895_2019.json"
MET = "metrics_000895_2019.json"
ER = "expected_return_000895_2019.json"
MOS = "mos_calc_000895_2019.json"
FIN = "financials_000895_2019.json"
NORM = "normalization_000895_2019.json"
QUAL = "qualitative_000895_2019.json"
DIV = "dividends_000895_2019.json"
BD = "business_drivers_000895.json"
P5 = "phase5_workpaper.json"
TRIG = "trigger_reachability_000895.json"

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>双汇发展 (000895.SZ) — 历史回放分析报告 2019-06-30</title>
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
<h1>双汇发展 (000895.SZ) — 历史回放分析报告</h1>
<p class="small">回放时点 2019-06-30 ｜ 案例编号 B2-12（第二批） ｜ 信息截断日 2019-06-30 ｜ 本报告仅使用 2019-06-30 及之前的公开信息</p>

<h2 style="margin-top:0">决策卡</h2>
<div class="card" data-verdict="观察等价格">
<p><b>结论档位：<b>观察等价格</b>（档位 2/4）——现价不买，但属「回报不足型」而非「价值毁灭型」：市场没疯（悲观有底）、公司不差（不收敛下限过线）、只是价格不够低。等待语义 = 等「成本挤压落地 + 价格把悲观算进」的重叠区。</b></p>
<p>现价 {v(SNAP, "price_unadjusted", "num2")} 元（2019-06-28 收盘，06-30 为周日）｜ 市值 {v(SNAP, "market_cap_yi", "num1")} 亿元 ｜ PE(TTM) {v(SNAP, "valuation_snapshot.pe_ttm", "num1")}x ｜ PB {v(SNAP, "valuation_snapshot.pb", "num2")}x ｜ 股息率（归属口径）{v(SCEN, "dividend_yield", "pct1")}</p>
<p>闸门一（安全边际，narrow 档要求 40%）：基准内在价值 {v(MOS, "iv_base", "num2")} 元 → MoS = {v(MOS, "mos_vs_base", "pct1")} <span class="gate-fail">✗ 不过</span>；乐观口径 {v(MOS, "mos_vs_bull", "pct1")}（乐观值仅高现价 7.0%=定价充分）；悲观口径 {v(MOS, "mos_vs_bear", "pct1")}。触发价 {v(MOS, "trigger_price_base_40pct", "num2")} 元 = 基准×0.6，低于 2015-08 股灾底 15.39 达 20.1%——<span data-trigger-price="12.29" data-trigger-band-pct="-94.8">触发价在 52 周价格带（21.00-30.19）之外，属历史区间之外</span>，「等它」按罕见事件定价 [E:{TRIG}]。</p>
<p>闸门二（期望回报，护城河 narrow）：①期望 IRR {v(ER, "expected_annualized_irr", "pct1")} vs 门槛 {v(ER, "gate2.consistency_expected_irr.hurdle", "pct1")} <span class="gate-fail">✗</span>（自洽性校验，与闸门一同源）；②不收敛下限 {v(ER, "gate2.no_convergence_floor.value", "pct1")}（股息 {v(ER, "gate2.no_convergence_floor.dividend_yield", "pct1")}+内在增速 {v(SCEN, "intrinsic_value_growth", "pct1")}）vs 6% <span class="gate-pass">✓ 脆弱通过</span>（悲观盈利×支付率回落至 2017 年带时仅高门槛 0.2-0.4pct——股息-利润周期同步性）；③悲观年化 {v(ER, "gate2.pessimistic_irr.value", "pct1")} vs 0% <span class="gate-pass">✓</span>（悲观由独立方法 pe_trough 13x 推导）→ <span class="gate-fail">闸门二 1/3，整体不过</span>（亏损概率 {v(ER, "loss_probability", "pct0")}——回报不足型，与 Zoom 本金损失型本质对照）</p>
<p>三情景：悲观 {v(SCEN, "scenarios.0.value_per_share", "num2")} ／ 基准 {v(SCEN, "scenarios.1.value_per_share", "num2")} ／ 乐观 {v(SCEN, "scenarios.2.value_per_share", "num2")} vs 现价 {v(SNAP, "price_unadjusted", "num2")}——现价卡在基准与乐观之间靠上位置：市场已按隐含增速 8.5%（定性中枢 1.7-4 倍）定价。</p>
<div class="verification-strength" data-verification-strength="1"
     data-grade="B-"
     data-reconciliation-coverage="1.0000"
     data-crosscheck-level="full"
     data-window-years="5"
     data-covers-full-cycle="false">
  <b>核验强度 B-</b>：三表勾稽覆盖率 100%（FY2014-2018 五年 TA=TL+TE 全平 ≤0.1 百万）｜ 命门科目 2018 年报三源核对+2019Q1 双源一致（<u>未经原文机器逐字比对</u>；FY2014-2017 为转引+勾稽，登记 partial）｜ 数据窗口 5 年（不足 10 年）且不含上行冲击段（挤压锚仅 2016 单点实证）<br>
  <span class="vs-meaning">降档主因=上行段窗口缺失：2019H2-2020 猪价上行挤压在截断日尚未发生，悲观情景 8.5% 净利率锚只有 2016 年一个实证点——这是本案核验强度不足以支撑 A/B 的结构性原因，档位判定已从严（悲观锚取实证压力年+hybrid 基期折价 9.7%）。</span>
</div>
</div>

<h2>一、信息集与防前视声明</h2>
<table>
<tr><th>信息</th><th>日期</th><th>截断裁决</th></tr>
<tr><td>2018 年报（归母 49.15 亿/营收 487.67 亿，三源核对）</td><td>2019-03-16 披露</td><td>✓ 进信息集</td></tr>
<tr><td>2019 一季报（归母 12.79 亿/EPS 0.3877，净利率 10.70% 近三年 Q1 最高）</td><td>2019-04-30 披露</td><td>✓ 进信息集</td></tr>
<tr><td>农业农村部 5 月能繁母猪 −23.9%（连续 6 月加速去化）/5 月猪价 24.71 元/kg（+26.6%）</td><td>2019-06-12 / 2019-06-24 发布</td><td>✓ 进信息集（成本冲击证据链核心）</td></tr>
<tr><td>2019-06-28 收盘价 24.89 元（不复权主口径；前复权 14.36/后复权 388.26 三口径登记）</td><td>2019-06-28</td><td>✓ 进信息集</td></tr>
<tr><td>2019 半年报（含中报分红方案=特别分红是否常态化的判定材料）</td><td>2019-08-14 披露</td><td><span class="bad">✗ forbidden</span> 未进任何底稿</td></tr>
<tr><td>2019-07 起猪价数据与冻肉抛储政策</td><td>—</td><td><span class="bad">✗ forbidden</span>（防前视关键：挤压兑现路径属答案侧信息）</td></tr>
</table>
<p class="small">meta.json 在任何数据抓取前冻结（先验档位 2，confidence=low）；吸收合并双汇集团重组（402 亿对价、摊薄 &lt;1%）进行中如实登记；万洲国际（00288.HK）为母公司非同体，非 A/H 同体样本。</p>

<h2>二、Phase 0 排雷与污染披露</h2>
<p>排雷结论：<b>财务真实、无造假形态</b>——OCF/归母 1.17x（盈利真现金）；两项形态相似告警走反证登记路径：①<b>稀释警报排除</b>（2015 年 10 转 5 系资本公积转增，share_count_change {v(MET, "summary.share_count_change", "num2")}x 为拆股假象非现金定增——转增假警报登记 OBS-000895-01）；②<b>分红幻觉警示保留</b>（FCF 覆盖 0.80x，2016 年 157% 支付率越界，母公司万洲偿债驱动——不归因造假但要求股息锚强制归属口径）[E:{MET}][E:{DIV}]。污染披露三层（meta.json）：①ANSWERS.md 全文在第一批旧版 PROMPT 时期已进入执行上下文（不可撤销，含本案答案行）；②前批次 OBS 结论预锚定（OBS-600660-03 预登记本案为复检场）；③训练语料后见（2019H2 猪价暴涨/2020 峰值）。缓解：全部估值输入来自截断日前数据源并登记抓取时间；档位由引擎管线机械输出唯一决定（后见方向反证：后见应压档位至 1，实测引擎输出 2）[E:{P5}]。</p>

<h2>三、定量画像（FY2014-2018，5 年窗口 + 2019Q1）</h2>
<p>引擎五警报中两项经裁决排除/降级（转增假警报、分红幻觉归警示），核心事实是<b>净利率的统计中性掩盖位置失真</b>：净利率序列 8.5-10.1% 窄幅（秩相关 ρ+0.20、穿越 3 次——统计层均值化成立），但 2018 年 10.08% 与 2019Q1 10.70% 为冻肉红利+低猪价双红利的失真高位——<b>用净利率水平判断周期位置会失真</b>（本案官方定位句的管线实证）：失真在「位置判定」而非「均值化统计」[E:{NORM}][E:{MET}]。增长画像：收入 CAGR {v(MET, "summary.cagr_total.revenue", "pct1")} 但每股收入 CAGR {v(MET, "summary.cagr_per_share.rev_ps", "pct1")}（转增除权算术压低）；ROIC 五年均值 {v(MET, "summary.roic_avg_5y", "pct1")}（高盈利真现金）[E:{MET}]。资本配置：capex 仅 D&A 的 53%、分红率 5 年 103%——激进分配型（留存≈0，内在价值增速只能靠提价传导）[E:{FIN}][E:{DIV}]。</p>
{chart(MET, "chart_series.revenue", "c_rev", "营业收入（百万元）：总量停滞期", "百万元", 1, "2014-2018 收入 CAGR 仅 1.6%——量的扩张已结束，利润弹性全在价格与成本 [E:" + FIN + "]")}
{chart(MET, "chart_series.net_margin", "c_nm", "净利率（%）：8.5-10.1% 窄幅，统计中性掩盖位置失真", "%", 100, "2016 年 8.50% 为五年最低=本案悲观情景的实证锚（猪价上行年）；2018 年 10.08% 为冻肉红利失真位 [E:" + NORM + "]")}
{chart(MET, "chart_series.gross_margin", "c_roe", "毛利率（%）：成本传导的季度监视器", "%", 100, "ROE 五年均值 28%+（2014 缺前值不绘）——高分红压留存、分母做小，ROE 高但不可外推为内生增长能力 [E:" + MET + "]")}
{chart(MET, "chart_series.owner_earnings", "c_oe", "Owner Earnings（百万元，引擎口径）", "百万元", 1, "基期裁决：引擎推荐当期 5,047.3 → 裁决取 hybrid 4,557.9（折价 9.7%）——统计中性+前瞻猪周期上行两证据链同向下修 [E:" + NORM + "]")}

<h2>四、五维定性</h2>
<h3>4.1 商业模式</h3>
<p>屠宰+肉制品双主业：屠宰走量（2019Q1 屠宰 +20.71% vs 行业 4 月 −13%，集中度提升剪刀差）、肉制品赚钱（吨价对冲成本的历史机制 30 年有效）。传导型公司本质：上游猪价是成本端输入变量，提价时滞 2-3 季度+冻肉库存平滑构成缓冲层——周期位置判定必须用驱动因子而非利润率水平 [E:{QUAL}][E:{BD}][E:{NORM}]。</p>
<h3 data-moat="narrow" data-moat-trend="stable">4.2 护城河（narrow——闸门门槛决定项）</h3>
<p>五源检验：品牌（高温肉制品 30 年心智，2018 年提价不丢量实证）+成本规模（22 基地布局+全球采购通道，2016 年进口价差红利实证）双源成立；但消费者零转换成本、生鲜端无溢价、健康化侵蚀未解除——评级「窄」。周期四态对照收尾：神华（资源型真底部）/海控（无护城河周期顶）/鞍钢（无护城河结构衰退）/双汇=<b>唯一「有护城河但周期位置判定失效」形态</b>——护城河可部分对冲周期，但净利率含成本红利失真 [E:{QUAL}][E:{NORM}][E:{MET}]。</p>
<h3>4.3 增长</h3>
<p>量的逻辑=集中度提升（非洲猪瘟加速中小屠宰退出，双汇 2019Q1 屠宰逆势 +20.71%——回放时点最强可见正向逻辑，乐观情景 g6% 的主支撑）；价的逻辑=肉制品提价传导（历史时滞 2-3 季度）；风险=牧原/温氏自建屠宰产能的纵向整合绕开 [E:{QUAL}][E:{BD}][E:{FIN}]。</p>
<h3>4.4 管理层</h3>
<p>资本配置激进分配（分红率 5 年 103%、2016 年 157% 越界——母公司万洲国际偿债驱动，「上市公司为大股东现金流服务」结构扣分）；治理演变双向：吸收合并双汇集团（四层→三层，治理改善真动作，402 亿对价摊薄 &lt;1%）vs 79 岁创始人接班悬置+激励绑定弱 [E:{QUAL}][E:{DIV}][E:{FIN}]。</p>
<h3>4.5 财务质量</h3>
<p>高盈利真现金（OCF/归母 1.17x、五年三表勾稽全平）；但 FCF 覆盖股东回报仅 0.80x——股息可持续性依赖利润周期稳定，是 gate2②「脆弱通过」的微观机制（悲观盈利×支付率回落带时，下限仅高门槛 0.2-0.4pct）；有息负债 3→23 亿方向自洽（五年现金缺口 49 亿由存量现金+举债覆盖）[E:{FIN}][E:{MET}][E:{DIV}]。</p>

<h2>五、估值与安全边际（Phase 3-4）</h2>
<h3>5.1 正常化核心裁决：基期下修</h3>
<p>引擎推荐当期基期 OE 5,047.3 百万 → <b>裁决取 hybrid 轨 4,557.9 百万（折价 9.7%）</b>：①统计层净利率均值化成立（ρ+0.20）但驱动因子（成本红利/冻肉红利）处失真高位；②前瞻证据链——能繁 −23.9% 连续 6 月加速去化+5 月猪价 +26.6%，成本冲击是「何时」而非「是否」。两证据链同向下修；2018 净利率 10.08% 与 2019Q1 10.70% 为失真位的直接证据 [E:{NORM}][E:{BD}]。反向 DCF 隐含增速 8.51%（EV 口径，hybrid 基期；当期基期口径 5.98%）=「净利率失真致隐含预期低估 2.5pct」的定价维度实证 [E:{SCEN}]。</p>
<h3>5.2 三情景（r=10%、H=5 年）</h3>
<table>
<tr><th>项</th><th>悲观（pe_trough 13x，独立方法）</th><th>基准（dcf_owner_earnings）</th><th>乐观（dcf_owner_earnings）</th></tr>
<tr><td>内核</td><td>挤压盈利 4,142 百万（=TTM 收入 48,733×8.5%，2016 上行年净利率实证锚）×13x；2015-08 股灾底实际 PE 11.9x&lt;13x 证明该档位历史真实</td><td>hybrid 基期 4,557.9，g 3.5% fade 10 年</td><td>当期 OE 5,047.3，g 6% fade 12 年（集中度逻辑）</td></tr>
<tr><td>每股价值</td><td>{v(SCEN, "scenarios.0.value_per_share", "num2")}</td><td>{v(SCEN, "scenarios.1.value_per_share", "num2")}</td><td>{v(SCEN, "scenarios.2.value_per_share", "num2")}</td></tr>
<tr><td>概率</td><td>{v(SCEN, "scenarios.0.probability", "pct0")}</td><td>{v(SCEN, "scenarios.1.probability", "pct0")}</td><td>{v(SCEN, "scenarios.2.probability", "pct0")}</td></tr>
</table>
<p class="small">check_scenarios 门禁一次过（0 错 0 警，悲观/基准离散度 0.80≤0.85）；乐观值 {v(SCEN, "scenarios.2.value_per_share", "num2")} 仅高现价 {v(MOS, "mos_vs_bull", "pct1")}——三口径中唯一正值=「定价充分」的量化（与 Zoom「乐观仍低现价 19.6%=参数不可达」的形态分界）[E:{SCEN}][E:{MOS}]。</p>
<h3>5.3 安全边际与分层触发位</h3>
<p>MoS（vs 基准）= {v(MOS, "mos_vs_base", "pct1")} vs 要求 40%（缺口 18.5pct）；烟蒂式例外不成立（悲观值为现价 0.656×、净现金仅市值 3.3%）。<b>分层触发位设计</b>：满档行动位 {v(MOS, "trigger_price_base_40pct", "num2")}（=基准×0.6，低于 2015-08 股灾底 15.39 达 20.1%，TRIGGER_OUT_OF_HISTORY 机器判定——历史区间之外，「等它」按罕见事件定价）／挤压确认观察位 16.3±（=悲观值带，「价格把悲观算进+利空落地」的重叠区）[E:{MOS}][E:{TRIG}]。</p>

<h2>六、双闸门与档位（Phase 4 定档）</h2>
<table>
<tr><th>闸门</th><th>项</th><th>值</th><th>门槛</th><th>判定</th></tr>
<tr><td>闸门一</td><td>MoS（vs 基准 20.48）</td><td>{v(MOS, "mos_vs_base", "pct1")}</td><td>≥40%（narrow 档）</td><td class="bad">✗</td></tr>
<tr><td rowspan="3">闸门二</td><td>①期望 IRR</td><td>{v(ER, "expected_annualized_irr", "pct1")}（亦跑输指数门槛 {v(ER, "index_hurdle", "pct0")} 达 {v(ER, "excess_vs_index", "pct1")}）</td><td>≥{v(ER, "gate2.consistency_expected_irr.hurdle", "pct1")}</td><td class="bad">✗</td></tr>
<tr><td>②不收敛下限</td><td>{v(ER, "gate2.no_convergence_floor.value", "pct1")}（股息 {v(ER, "gate2.no_convergence_floor.dividend_yield", "pct1")}+内在增速 {v(SCEN, "intrinsic_value_growth", "pct1")}）</td><td>≥6%</td><td class="ok">✓ 脆弱通过</td></tr>
<tr><td>③悲观年化</td><td>{v(ER, "gate2.pessimistic_irr.value", "pct1")}</td><td>≥0</td><td class="ok">✓</td></tr>
</table>
<p><b>档位判定：观察等价格（2/4）</b>——①②③的形态组合（IRR 不足+下限够+悲观为正）是「回报不足型」的标准指纹：亏损概率 {v(ER, "loss_probability", "pct0")}、期望 IRR 含 {v(ER, "dividend_share_of_return", "pct1")} 的股息真实现金回报，但 5.55% 不抵 21.83% 门槛与 9% 指数机会成本。与 Zoom（档位 1：IRR −16.64%/亏损概率 80%/参数不可达）构成「等待 vs 逃生」的档位语义分界 [E:{ER}][E:{MOS}]。<span data-reeval-trigger="分层触发位重评 checklist：挤压确认观察位 16.3±（悲观值带+利空落地）/满档行动位 12.29；触发后载入重评（净利率落点 vs 8.5% 锚、股息可持续性、集中度剪刀差三要素），重评结论与原档位分轨登记"></span></p>

<h2>七、关键判断收敛（Phase 4.5，五关键变量）</h2>
<p>①<b>净利率周期位置</b>（本案核心）：统计均值化成立但位置判定失真——高位来自成本红利而非需求强劲；②<b>猪价传导幅度与时滞</b>：挤压幅度决定净利率落点 8.5% 还是 7.5%（差 12 亿利润/悲观值差 1.9 元）；③<b>股息可持续性</b>：支付率 5 年 103%+FCF 覆盖 0.80x——利润挤压年若支付率仍 &gt;100% 则举债分红形态确认；④<b>集中度剪刀差</b>：乐观引擎，监控牧原/温氏纵向整合；⑤<b>治理结构演变</b>：吸收合并改善 vs 接班真空并存 [E:{P5}][E:{BD}][E:{QUAL}]。</p>

<h2>八、三位大师独立评估 + 红队（Phase 5）</h2>
<table>
<tr><th>大师</th><th>五轴评分（生意/护城河/管理层/安全垫/能力圈）</th><th>结论</th><th>一句话理由</th></tr>
<tr><td>巴菲特</td><td>4.0 / 3.5 / 2.5 / 1.5 / 4.5</td><td>观察等价格</td><td>「好公司+公道价+周期顶部利润的三明治；到 15-16 块是十年最好的消费股机会之一，到 12 块无脑满仓」</td></tr>
<tr><td>段永平</td><td>4.0 / 3.5 / 3.0 / 1.5 / 4.0</td><td>不买，观察</td><td>「最容易骗到聪明人的是好公司+合理价格，因为每个数字都是真的」；不碰「上市公司为大股东现金流服务」的结构除非价格足够便宜——错过不是错误</td></tr>
<tr><td>李录</td><td>4.0 / 3.5 / 3.0 / 1.0 / 3.5</td><td>观察等价格</td><td>「时间在我这边、价格不在这边」——重大机会=优秀生意+悲观定价+时间站你这边，双汇缺悲观定价这一半，而这一半正在路上</td></tr>
</table>
<p><b>红队五项</b>（全文见 data/phase5_workpaper.json）：①pe_trough 13x 三层防御+7.5-9.5% 全带压力测试结论不翻转；②hybrid 改判为引擎自输出，当期基期实测 gate1 −10.2% 仍不翻转；③g8% 强行假设 gate1 仍 −17%；④gate2②「脆弱通过」标注（股息-利润周期同步性=OBS-600660-03 第四维度候选）；⑤后见污染六重隔离专条（方向反证：后见应压低档位，实测引擎输出 2 形态不符）。三师五轴全维度共识「观察等价格」，安全垫轴 1.0-1.5 分=回报不足方向；pre-mortem 四形态（成本挤压高/股息不可持续中/健康化低/集中度证伪中）[E:{P5}]。</p>

<h2>九、持有体验预演 + 跟踪清单</h2>
<p><b>持有体验预演</b>（假设 24.89 买入）：亏损概率 {v(ER, "loss_probability", "pct0")}——最差形态是「钱趴着不动」（价格跟随挤压下落后随分红回填）而非本金毁灭；期望 IRR {v(ER, "expected_annualized_irr", "pct1")} 中 {v(ER, "dividend_share_of_return", "pct1")} 为股息现金落袋。<b>证伪条件</b>（触发即重审）：①单季净利率 &lt;8% → 悲观锚击穿，观察降拒绝；②支付率 &gt;110% 且有息负债 &gt;40 亿 → 举债分红确认，gate2② 转败；③屠宰量增速剪刀差消失 → 乐观引擎失效；④肉制品吨价同比转负 → 对冲机制失效；⑤（反向）净利率维持 10%+ 且猪价回落 → 利润率顶部判定错误，全流程重走。<b>复盘点</b>：2019-08-14 中报分红方案（特别分红常态化判定=OBS-600660-03 判定解除点）/2019-10-29 三季报毛利率/2020-04 年报净利率 vs 8.5% 锚 [E:{P5}][E:{DIV}]。</p>

<h2>十、数据引用附录（全部 publish_date）</h2>
<table>
<tr><th>来源</th><th>日期</th><th>用途</th></tr>
<tr><td>新浪三表 FY2014-2018 + 2019Q1（vDOWN 转引，5 年勾稽全平 ≤0.1 百万）</td><td>2018 年报披露 2019-03-16 / 一季报 2019-04-30</td><td>财务序列主底稿</td></tr>
<tr><td>证券时报 2019-03-15 年报报道 + 巨潮 2018 年度董事会工作报告 PDF</td><td>2019-03</td><td>2018 年报三源核对（营收/利润总额/归母一致）</td></tr>
<tr><td>东财 push2his 日K 三口径（secid=0.000895，fqt=0/1/2）</td><td>2019-06-28 收盘</td><td>价格 24.89/前复权 14.36/后复权 388.26 + 市值 821.3 亿</td></tr>
<tr><td>农业农村部 5 月能繁母猪 −23.9% / 5 月猪价 24.71 元/kg</td><td>2019-06-12 / 2019-06-24</td><td>成本冲击证据链（前瞻维度）</td></tr>
<tr><td>东财 RPT_SHAREBONUS_DET 分红序列 vs CF 表 dividends_paid vs 董事会报告</td><td>至 2019-06</td><td>股息率三口径（归属 1.45 元=5.83% vs 支付 2.00 元=8.03%）</td></tr>
</table>
<p class="small">数据质量：核验强度 B-（勾稽 full/三源核对 full/窗口 5 年不足且缺上行段）。本报告为 Step 2 交付物补录（生成于 verdict 落盘与 Step 4 之后），全部数字渲染自既有底稿、未引入新判断；E 指针经 manifest.json 登记核验。数字校验：见交付时 verify_report.py 输出。免责声明：本报告由分析工具生成，仅供研究参考，不构成任何投资建议。市场有风险，投资需谨慎。</p>
</body>
</html>'''

out = os.path.join(CASE, "双汇发展_价值投资分析报告_20190630.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("written:", out, len(html), "chars")
