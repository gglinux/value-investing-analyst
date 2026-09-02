#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_tests.py — 脚本引擎回归测试（任何人改 scripts/ 前后必须各跑一次）

覆盖场景：
  1. 正常公司：指标齐全，基期=当期
  2. 周期高位：强制正常化基期 + alert
  3. 周期低位：向上正常化基期 + alert
  3.5 利润率形状检验：同水平比值、不同曲线形状必须分流（结构性改善/恶化 vs 周期波动），
      含负向用例（真周期低谷、平稳序列不得被误报为结构性趋势）
  4. 当期亏损：正常化不适用，基期为 None
  5. 金融股门控：compute_metrics 必须拒绝执行
  6. capex 拆分：披露口径优先于启发式
  7. reverse_dcf：forward-value 与 expected-return 输出合理性
  7.5 期望回报口径：终值时点铁律（P=V0→IRR=r）、股息不叠加、下行指标输出
  7.55 终值占比诊断：split 拆分、增速越高占比越高、fade 降低占比
  7.6 银行管道：compute_metrics_bank 指标正确、非银行拒绝、低拨备 alert
  8. verify_report 负向：篡改的 vnum/vchart 与幽灵 [E:] 指针必须被逮住

用法：python3 tests/run_tests.py   （在 skill 根目录运行）
退出码：0 全过；1 有失败。
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import compute_metrics as cm  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILED.append(name)


def mk_rows(margins, rev0=1000.0, growth=1.05, capex_ratio=0.08, mcapex=None):
    rows = []
    for i, m in enumerate(margins):
        rev = rev0 * (growth ** i)
        row = {
            "year": 2015 + i, "revenue": rev, "net_income": m * rev,
            "gross_profit": 0.5 * rev, "d_and_a": 0.05 * rev,
            "capex": capex_ratio * rev, "wc_change": 0.005 * rev,
            "ocf": m * rev + 0.05 * rev, "total_equity": 0.5 * rev,
            "total_debt": 0.1 * rev, "cash": 0.05 * rev,
            "shares_diluted": 100.0,
        }
        if mcapex is not None:
            row["maintenance_capex"] = mcapex * rev
        rows.append(row)
    return rows


def base(rows, **kw):
    d = {"company": "T", "ticker": "T", "currency": "USD", "unit": "million",
         "annual": rows}
    d.update(kw)
    return d


print("== 1. 正常公司 ==")
r = cm.compute(base(mk_rows([0.15, 0.16, 0.15, 0.14, 0.16, 0.15, 0.16, 0.15, 0.16, 0.15, 0.16])))
n = r["normalization"]
check("周期位置=中性", "中性" in n["cyclicality"] or "周期" not in n["cyclicality"], n["cyclicality"])
check("基期=当期", n["base_oe_recommended"] == n["oe_current"])
check("chart_series 存在且等长", len(r["chart_series"]["net_margin"]) == 11)
check("series 有 fcf_true_range 或 fcf", r["series"][-1].get("fcf") is not None)

print("== 2. 周期高位 ==")
r = cm.compute(base(mk_rows([0.10, 0.11, 0.10, 0.12, 0.11, 0.10, 0.11, 0.12, 0.20, 0.28, 0.30])))
n = r["normalization"]
check("判定高位", n["cyclicality"] == "周期高位", n["cyclicality"])
check("基期≠当期（禁峰值）", n["base_oe_recommended"] is not None
      and abs(n["base_oe_recommended"] - n["oe_current"]) > 1e-6)
check("基期<当期（向下修正）", n["base_oe_recommended"] < n["oe_current"])
check("发出高位 alert", any("周期高位" in a for a in r["alerts"]))

print("== 3. 周期低位 ==")
r = cm.compute(base(mk_rows([0.20, 0.22, 0.20, 0.18, 0.20, 0.22, 0.18, 0.16, 0.12, 0.10, 0.08])))
n = r["normalization"]
check("判定低位", n["cyclicality"] == "周期低位", n["cyclicality"])
check("向上正常化基期>当期", n["base_oe_recommended"] is not None
      and n["base_oe_recommended"] > n["oe_current"])
check("发出低位 alert", any("周期低位" in a for a in r["alerts"]))

print("== 3.5 利润率形状检验（v2.14 补形状盲区）==")
# 反例核心：A/B 两条序列的最新值、全期均值、比值完全相同（都是 16% / 11% / 1.45），
# 旧引擎只看水平比较，对两者给出完全相同的判定与基期。而 A 该正常化、B 不该。
ZIG = [0.08, 0.14, 0.06, 0.16, 0.07, 0.15, 0.05, 0.13, 0.09, 0.12, 0.16]   # 真周期
MONO = [0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.14, 0.15, 0.16]  # 结构性改善
rz = cm.compute(base(mk_rows(ZIG)))
rm = cm.compute(base(mk_rows(MONO)))
nz, nmn = rz["normalization"], rm["normalization"]
check("A/B 水平判定确实相同（旧引擎的盲区前提）",
      nz["cyclicality"] == nmn["cyclicality"] == "周期高位"
      and abs(nz["margin_ratio_latest_vs_avg"] - nmn["margin_ratio_latest_vs_avg"]) < 0.02,
      f"{nz['margin_ratio_latest_vs_avg']:.3f} vs {nmn['margin_ratio_latest_vs_avg']:.3f}")
check("锯齿序列判为周期波动", nz["margin_trend"]["pattern"] == "周期波动",
      nz["margin_trend"]["pattern"])
check("单调上行判为结构性改善", nmn["margin_trend"]["pattern"] == "结构性改善",
      nmn["margin_trend"]["pattern"])
check("单调上行 rho ≈ +1", nmn["margin_trend"]["spearman_rho"] > 0.99)
check("单调上行穿越均值 ≤1 次", nmn["margin_trend"]["mean_crossings"] <= 1)
check("锯齿穿越均值 ≥3 次", nz["margin_trend"]["mean_crossings"] >= 3)
check("结构性改善置信度=高（末端连续同侧≥3年）",
      nmn["margin_trend"]["confidence"] == "高")
# 关键回归点：旧版双轨触发条件是"亏损年/±50%突变"，对单调改善型公司完全不触发
# （安全网装反）。修补后必须由趋势检验独立触发。
check("单调上行触发双轨基期（旧版漏网）",
      nmn.get("base_oe_dual_track") is not None
      and nmn["base_oe_dual_track"]["trigger"] == "structural_improvement",
      str(nmn.get("base_oe_dual_track") and nmn["base_oe_dual_track"]["trigger"]))
check("双轨主轨仍为正常化（纪律不放松，不是直接放行）",
      nmn["base_oe_dual_track"]["main"]["value"] == nmn["base_oe_recommended"]
      and nmn["base_oe_recommended"] < nmn["oe_current"])
check("双轨交叉轨为当期",
      abs(nmn["base_oe_dual_track"]["cross"]["value"] - nmn["oe_current"]) < 1e-6)
check("发出形状检验 alert", any("形状检验" in a for a in rm["alerts"]))
check("双轨 alert 说明触发原因", any("结构性改善" in a and "双轨" in a for a in rm["alerts"]))

# 对称的另一半：结构性衰退被当成周期低谷 → 向上正常化会系统性高估（价值陷阱入口）
rd = cm.compute(base(mk_rows([0.20, 0.22, 0.20, 0.18, 0.20, 0.22, 0.18, 0.16, 0.12, 0.10, 0.08])))
nd = rd["normalization"]
check("单调下行判为结构性恶化", nd["margin_trend"]["pattern"] == "结构性恶化",
      nd["margin_trend"]["pattern"])
check("结构性恶化触发双轨（防价值陷阱）",
      nd.get("base_oe_dual_track") is not None
      and nd["base_oe_dual_track"]["trigger"] == "structural_deterioration")
check("发出结构性衰退警报", any("结构性衰退警报" in a for a in rd["alerts"]))
# 负向用例：真正的周期低谷（锯齿收尾在低位）不得被误报为结构性衰退
rt = cm.compute(base(mk_rows([0.20, 0.08, 0.22, 0.10, 0.19, 0.09, 0.21, 0.11, 0.20, 0.10, 0.09])))
nt = rt["normalization"]
check("真周期低谷仍判周期低位", nt["cyclicality"] == "周期低位", nt["cyclicality"])
check("真周期低谷不误报结构性恶化", nt["margin_trend"]["pattern"] == "周期波动",
      nt["margin_trend"]["pattern"])
check("真周期低谷不触发衰退双轨", nt.get("base_oe_dual_track") is None)
check("真周期低谷仍向上正常化", nt["base_oe_recommended"] > nt["oe_current"])
# 负向用例：平稳序列不得被判出任何趋势
rf = cm.compute(base(mk_rows([0.15, 0.16, 0.15, 0.14, 0.16, 0.15, 0.16, 0.15, 0.16, 0.15, 0.16])))
check("平稳序列不判结构性趋势",
      rf["normalization"]["margin_trend"]["pattern"] in ("周期波动", "趋势不明确"),
      rf["normalization"]["margin_trend"]["pattern"])
check("秩相关在样本<5时返回 None", cm.spearman_rho([0.1, 0.2, 0.3]) is None)

print("== 4. 当期亏损 ==")
r = cm.compute(base(mk_rows([0.10, 0.12, 0.10, 0.11, 0.10, 0.12, 0.10, 0.11, 0.10, 0.05, -0.08])))
n = r["normalization"]
check("亏损标注", "亏损" in n["cyclicality"], n["cyclicality"])
check("基期为 None（须人工论证）", n["base_oe_recommended"] is None)

print("== 5. 金融股门控 ==")
try:
    cm.compute(base(mk_rows([0.15] * 11), company_type="银行"))
    check("金融股被拒绝", False, "未抛出 SystemExit")
except SystemExit as e:
    check("金融股被拒绝", "金融" in str(e))

print("== 6. capex 拆分 ==")
r = cm.compute(base(mk_rows([0.15] * 11, capex_ratio=0.08, mcapex=0.03)))
s = r["series"][-1]
check("披露口径优先", s["capex_split_basis"] == "披露口径", s.get("capex_split_basis"))
check("维持+扩张=总capex", abs((s["maintenance_capex_used"] + s["growth_capex_used"])
                               - s["capex_total"]) < 1e-6)
r2 = cm.compute(base(mk_rows([0.15] * 11, capex_ratio=0.08)))
s2 = r2["series"][-1]
check("缺省走启发式", "启发式" in (s2.get("capex_split_basis") or ""), s2.get("capex_split_basis"))

print("== 7. reverse_dcf ==")
env = dict(os.environ)


def run(args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "reverse_dcf.py")] + args,
                          capture_output=True, text=True, env=env)

p = run(["forward-value", "--base-oe", "1000", "--growth", "0.10", "--years", "10",
         "--terminal-growth", "0.03", "--discount", "0.10", "--shares", "100"])
check("forward-value 运行成功", p.returncode == 0, p.stderr[:120])
ok_num = False
if p.returncode == 0:
    import re
    m = re.search(r"每股价值[:：]\s*([\d,\.]+)", p.stdout)
    if m:
        vps = float(m.group(1).replace(",", ""))
        ok_num = 100 < vps < 500  # 粗合理带：g10%/r10% 的 OE 倍数约 20~30x
check("forward-value 数值在合理带", ok_num, p.stdout[:200])

with tempfile.TemporaryDirectory() as td:
    fp = os.path.join(td, "er.json")
    p = run(["expected-return", "--price", "100", "--hold-years", "5",
             "--scenarios", "悲观:60:0.3,基准:110:0.5,乐观:150:0.2", "-o", fp])
    check("expected-return 运行成功", p.returncode == 0, (p.stderr or p.stdout)[:120])
    if p.returncode == 0:
        er = json.load(open(fp))
        def dig(obj, key):
            if isinstance(obj, dict):
                if key in obj:
                    return obj[key]
                for v in obj.values():
                    r = dig(v, key)
                    if r is not None:
                        return r
            return None
        irr = dig(er, "expected_annualized_irr") or dig(er, "expected_irr")
        lp = dig(er, "loss_probability") or dig(er, "loss_prob")
        check("期望IRR为数值", isinstance(irr, (int, float)), str(irr))
        check("亏损概率=悲观情景概率", isinstance(lp, (int, float)) and abs(lp - 0.3) < 1e-6,
              str(lp))

print("== 7.5 期望回报口径（v2.9 终值时点修正 + 股息不叠加） ==")
with tempfile.TemporaryDirectory() as td:
    fp0 = os.path.join(td, "er0.json")
    fp5 = os.path.join(td, "er5.json")
    fpx = os.path.join(td, "erx.json")
    scen = "悲观:80:0.3,基准:105:0.5,乐观:130:0.2"
    p0 = run(["expected-return", "--price", "100", "--hold-years", "5",
              "--scenarios", scen, "-o", fp0])
    p5 = run(["expected-return", "--price", "100", "--hold-years", "5",
              "--scenarios", scen, "--dividend-yield", "0.06", "-o", fp5])
    check("expected-return 运行成功", p0.returncode == 0 and p5.returncode == 0)
    if p0.returncode == 0 and p5.returncode == 0:
        e0, e5 = json.load(open(fp0)), json.load(open(fp5))
        # 核心：股息不再改变 IRR（修正式已隐含分红+留存的全部股东回报）
        check("股息不叠加进 IRR（叠加即重复计算）",
              abs(e5["expected_annualized_irr"]
                  - e0["expected_annualized_irr"]) < 1e-12,
              f"{e0['expected_annualized_irr']} vs {e5['expected_annualized_irr']}")
        check("兼容字段 incl_div 与主字段同值",
              abs(e5["expected_annualized_irr_incl_div"]
                  - e5["expected_annualized_irr"]) < 1e-12)
        check("分红贡献占比 = 股息率/折现率",
              abs(e5["dividend_share_of_return"] - 0.06 / 0.10) < 1e-9,
              str(e5.get("dividend_share_of_return")))
        check("股息不改变亏损概率（口径统一）",
              abs(e5["loss_probability"] - e0["loss_probability"]) < 1e-12)
        # 终值时点铁律：买在内在价值上，IRR 恒等于折现率
        px = run(["expected-return", "--price", "100", "--hold-years", "5",
                  "--scenarios", "悲观:100:0.3,基准:100:0.5,乐观:100:0.2",
                  "--discount-rate", "0.10", "-o", fpx])
        if px.returncode == 0:
            ex = json.load(open(fpx))
            check("P=V0 时 IRR 恒等于折现率（终值时点铁律）",
                  abs(ex["expected_annualized_irr"] - 0.10) < 1e-9,
                  str(ex["expected_annualized_irr"]))
            check("期末价值 V_H = V0×(1+r)^H",
                  abs(ex["scenarios"][0]["value_per_share_terminal"]
                      - 100 * 1.10 ** 5) < 1e-6)
            check("门槛≤折现率时标记闸门失效",
                  ex["hurdle_above_discount_rate"] is False)
        check("下行指标已输出（闸门二独立信息）",
              "pessimistic_irr" in e0 and isinstance(e0["pessimistic_irr"], float))
        check("门槛比较用主 IRR 字段", e5["beats_index"] ==
              (e5["expected_annualized_irr"] > e5["index_hurdle"]))

print("== 7.55 终值占比诊断（估值可靠性关卡） ==")
with tempfile.TemporaryDirectory() as td:
    import reverse_dcf as rd  # noqa: E402
    tot, fpv, tpv, diag = rd.dcf_value(100, 0.0, 0.10, 0.025, 10, split=True)
    check("split 拆分求和等于总值", abs(tot - (fpv + tpv)) < 1e-9)
    check("零增长终值占比约 46%", 0.44 < tpv / tot < 0.48, f"{tpv/tot:.3f}")
    check("split 返回结构化诊断（供下游机器校验）",
          isinstance(diag, dict) and abs(diag["terminal_value_ratio"] - tpv / tot) < 1e-12
          and diag["level"] == "ok"
          and diag["blocks_margin_of_safety_only_buy"] is False)
    t2, f2, p2, d2 = rd.dcf_value(100, 0.20, 0.10, 0.025, 10, split=True)
    check("高增长终值占比更高", p2 / t2 > tpv / tot, f"{p2/t2:.3f} vs {tpv/tot:.3f}")
    t3, f3, p3_, d3 = rd.dcf_value(100, 0.20, 0.10, 0.025, 10, fade=True, split=True)
    check("fade 降低终值占比", p3_ / t3 < p2 / t2, f"{p3_/t3:.3f} vs {p2/t2:.3f}")
    out = run(["forward-value", "--base-oe", "100", "--growth", "0.20",
               "--discount-rate", "0.10", "--years", "10"])
    check("forward-value 打印终值占比诊断",
          "终值占比" in out.stdout, out.stdout[-200:])

print("== 7.6 银行专属管道 ==")
import compute_metrics_bank as cmb  # noqa: E402


def mk_bank_rows(n=10, npl_ratio=0.01, coverage=4.0):
    rows = []
    for i in range(n):
        loans = 5000000.0 * (1.08 ** i)
        rows.append({
            "year": 2015 + i,
            "net_interest_income": 200000.0 * (1.06 ** i),
            "non_interest_income": 100000.0 * (1.06 ** i),
            "operating_income": 300000.0 * (1.06 ** i),
            "operating_expense": 100000.0 * (1.06 ** i),
            "provision_charge": 50000.0,
            "net_income": 120000.0 * (1.07 ** i),
            "total_assets": 10000000.0 * (1.08 ** i),
            "total_equity": 800000.0 * (1.09 ** i),
            "gross_loans": loans,
            "npl_balance": loans * npl_ratio,
            "provision_balance": loans * npl_ratio * coverage,
            "core_tier1_ratio": 0.13, "nim": 0.024,
            "shares_diluted": 25000.0, "dividend_per_share": 1.5,
        })
    return rows


bank_data = {"company": "测试银行", "ticker": "TB", "currency": "CNY",
             "unit": "million", "company_type": "银行", "annual": mk_bank_rows()}
rb = cmb.compute(bank_data)
check("银行管道产出 pb_roe_inputs", rb["pb_roe_inputs"]["roe_sustainable"] is not None)
check("不良率计算正确", abs(rb["series"][-1]["npl_ratio"] - 0.01) < 1e-9)
check("拨备覆盖率计算正确", abs(rb["series"][-1]["provision_coverage"] - 4.0) < 1e-9)
check("chart_series 齐全", len(rb["chart_series"]["roe"]) == 10)
try:
    cmb.compute({**bank_data, "company_type": "制造业"})
    check("非银行被拒绝", False, "未抛出 SystemExit")
except SystemExit as e:
    check("非银行被拒绝", "仅适用于银行" in str(e))
low_cov = {"company": "测试银行", "ticker": "TB", "currency": "CNY",
           "unit": "million", "company_type": "银行",
           "annual": mk_bank_rows(coverage=0.8)}
rb2 = cmb.compute(low_cov)
check("低拨备覆盖发 alert", any("拨备覆盖率" in a for a in rb2["alerts"]))

print("== 8. verify_report 负向 ==")
with tempfile.TemporaryDirectory() as td:
    ddir = os.path.join(td, "data"); os.makedirs(ddir)
    json.dump({"kpi": 0.123, "arr": [1.0, 2.0, 3.0]},
              open(os.path.join(ddir, "m.json"), "w"))
    json.dump({"files": [{"file": "m.json"}, {"file": "filings/t-*.htm"}]},
              open(os.path.join(ddir, "manifest.json"), "w"))
    good = ('<span class="vnum" data-src="m.json" data-path="kpi" data-fmt="pct1">12.3%</span>'
            '<!-- vchart src=m.json path=arr -->{data:[1,2,3]}'
            '[E:m.json][E:filings/t-2024.htm]')
    bad = good.replace("12.3%", "21.3%").replace("data:[1,2,3]", "data:[1,2,9]")
    bad_ep = good + "[E:ghost.pdf]"
    gp, bp, ep = (os.path.join(td, x) for x in ("g.html", "b.html", "e.html"))
    open(gp, "w").write(good); open(bp, "w").write(bad); open(ep, "w").write(bad_ep)

    def vr(path):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "verify_report.py"),
                               path, "--data-dir", ddir], capture_output=True, text=True)
    check("正确报告通过", vr(gp).returncode == 0)
    r = vr(bp)
    check("篡改报告被拒", r.returncode == 1)
    check("vnum 篡改被逮住", "kpi" in r.stdout)
    check("vchart 篡改被逮住", "arr" in r.stdout)
    r2 = vr(ep)
    check("幽灵证据指针被逮住", r2.returncode == 1 and "ghost.pdf" in r2.stdout,
          r2.stdout[-200:])
    check("通配登记的指针可通过", "t-2024.htm" not in r2.stdout)

print("== 8.5 图表完整性与乱码防护（verify_report 新增） ==")
with tempfile.TemporaryDirectory() as td:
    ddir = os.path.join(td, "data"); os.makedirs(ddir)
    json.dump({"kpi": 0.123}, open(os.path.join(ddir, "m.json"), "w"))
    vn = '<span class="vnum" data-src="m.json" data-path="kpi" data-fmt="pct1">12.3%</span>'
    ok_html = vn + '<div id="chart-a"></div><script>initChart(\'chart-a\',{});</script>'
    orphan_div = vn + '<div id="chart-a"></div><div id="chart-b"></div><script>initChart(\'chart-a\',{});</script>'
    orphan_init = vn + '<div id="chart-a"></div><script>initChart(\'chart-a\',{});initChart(\'chart-x\',{});</script>'
    garbled = vn + '<p>正常段落 但这里有乱码 приветика 混入正文</p>'
    paths = {}
    for name, content in [("ok", ok_html), ("od", orphan_div),
                          ("oi", orphan_init), ("gb", garbled)]:
        paths[name] = os.path.join(td, name + ".html")
        open(paths[name], "w").write(content)

    def vr8(path):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "verify_report.py"),
                               path, "--data-dir", ddir], capture_output=True, text=True)
    check("图表配对完整的报告通过", vr8(paths["ok"]).returncode == 0)
    r = vr8(paths["od"])
    check("空白图表（div无init）被逮住", r.returncode == 1 and "chart-b" in r.stdout,
          r.stdout[-200:])
    r = vr8(paths["oi"])
    check("悬空init（init无div）被逮住", r.returncode == 1 and "chart-x" in r.stdout,
          r.stdout[-200:])
    r = vr8(paths["gb"])
    check("乱码序列被逮住", r.returncode == 1 and "text-integrity" in r.stdout,
          r.stdout[-200:])

    # fmt 捕获回归（实证 bug：data-path 后贪婪 [^>]* 吞噬 data-fmt，
    # 导致显示精度容差永不生效，pct1 合法舍入被误报）
    json.dump({"kpi": 0.0077}, open(os.path.join(ddir, "m2.json"), "w"))
    edge = os.path.join(td, "edge.html")
    open(edge, "w").write('<span class="vnum" data-src="m2.json" data-path="kpi" '
                          'data-fmt="pct1">0.8%</span>')
    r = vr8(edge)
    check("pct1 合法舍入容差生效（fmt 必须被捕获）", r.returncode == 0, r.stdout[-200:])

print("== 8.6 双轨基期（均值扭曲年防误杀） ==")
# 爬坡期公司：前3年亏损 + 后期利润率稳定爬升 → 判定"周期高位"但应输出双轨
ramp = mk_rows([-0.05, -0.02, 0.02, 0.06, 0.10, 0.14, 0.17, 0.19, 0.21, 0.22, 0.23],
               growth=1.30)
r = cm.compute(base(ramp))
n = r["normalization"]
check("扭曲年被检出", n["mean_distortion"]["distorted"],
      str(n["mean_distortion"]))
if n["cyclicality"] == "周期高位":
    check("高位+扭曲输出双轨", n.get("base_oe_dual_track") is not None)
    if n.get("base_oe_dual_track"):
        dt = n["base_oe_dual_track"]
        check("双轨含主轨与交叉轨", dt["main"]["value"] is not None
              and dt["cross"]["value"] == n["oe_current"])
        check("双轨发 alert", any("双轨基期" in a for a in r["alerts"]))
# 平稳公司不应误报扭曲
r_smooth = cm.compute(base(mk_rows([0.15, 0.16, 0.15, 0.14, 0.16, 0.15, 0.16,
                                    0.15, 0.16, 0.15, 0.16])))
check("平稳序列不报扭曲", not r_smooth["normalization"]["mean_distortion"]["distorted"],
      str(r_smooth["normalization"]["mean_distortion"]["years"]))

print("== 8.7 reverse_dcf 非经营资产加回 ==")
p_no = run(["forward-value", "--base-oe", "1000", "--growth", "0.05", "--years", "10",
            "--shares", "100", "--fade"])
p_ab = run(["forward-value", "--base-oe", "1000", "--growth", "0.05", "--years", "10",
            "--shares", "100", "--fade", "--add-back", "5000", "--fx", "1.087"])
check("add-back 运行成功", p_ab.returncode == 0, p_ab.stderr[:120])
if p_no.returncode == 0 and p_ab.returncode == 0:
    import re as _re
    v0 = float(_re.search(r"每股价值[:：]\s*([\d,\.]+)", p_no.stdout).group(1).replace(",", ""))
    m_ab = _re.search(r"每股价值[:：]\s*([\d,\.]+)（报告币） = ([\d,\.]+)（行情币", p_ab.stdout)
    check("加回后每股 = 原每股 + 加回/股本",
          m_ab and abs(float(m_ab.group(1).replace(",", "")) - (v0 + 50.0)) < 0.02,
          p_ab.stdout[-150:])
    check("fx 换算正确",
          m_ab and abs(float(m_ab.group(2).replace(",", ""))
                       - float(m_ab.group(1).replace(",", "")) * 1.087) < 0.02)
p_dd = run(["implied-growth", "--market-cap", "30000", "--base-oe", "1000",
            "--deduct", "5000", "--fade"])
check("implied-growth deduct 运行成功", p_dd.returncode == 0
      and "剔除非经营资产" in p_dd.stdout, (p_dd.stderr or p_dd.stdout)[:120])

print("== 9. 银行校验（validate_data 银行旁路） ==")
with tempfile.TemporaryDirectory() as td:
    good = {
        "company": "测试银行", "ticker": "TB", "currency": "CNY", "unit": "million",
        "company_type": "银行", "accounting_standard": "CAS", "fiscal_year_end": "12-31",
        "annual": [
            {"year": 2022, "publish_date": "2023-03-25", "operating_income": 280000.0,
             "net_interest_income": 190000.0, "non_interest_income": 90000.0,
             "net_income": 110000.0, "gross_loans": 4600000.0,
             "npl_balance": 46000.0, "provision_balance": 190000.0},
            {"year": 2023, "publish_date": "2024-03-25", "operating_income": 290000.0,
             "net_interest_income": 195000.0, "non_interest_income": 95000.0,
             "net_income": 115000.0, "gross_loans": 4800000.0,
             "npl_balance": 48000.0, "provision_balance": 195000.0},
            {"year": 2024, "publish_date": "2025-03-25", "operating_income": 300000.0,
             "net_interest_income": 200000.0, "non_interest_income": 100000.0,
             "net_income": 120000.0, "gross_loans": 5000000.0,
             "npl_balance": 50000.0, "provision_balance": 200000.0},
        ],
        "crosscheck": [
            {"year": 2022, "source": "2022年报摘要", "operating_income": 280000.0, "net_income": 110000.0},
            {"year": 2023, "source": "2023年报摘要", "operating_income": 290000.0, "net_income": 115000.0},
            {"year": 2024, "source": "2024年报摘要", "operating_income": 300000.0, "net_income": 120000.0},
        ],
    }
    import copy
    bad = copy.deepcopy(good)
    bad["annual"][2]["npl_balance"] = 800000.0
    bad["annual"][2]["provision_balance"] = 30000.0
    gp = os.path.join(td, "g.json"); bp = os.path.join(td, "b.json")
    json.dump(good, open(gp, "w")); json.dump(bad, open(bp, "w"))

    # A1 豁免：测试底稿止于 2024，而 2025 年报死线已过——登记延迟申报豁免
    #（GOOG 实证后 A1 成为硬门，不登记的旧底稿一律拦截）
    from datetime import date as _date
    _exp_years = [y for y in range(2025, _date.today().year + 2)]
    json.dump({"official_filing_missing": {"years": _exp_years,
               "reason": "测试用例：示意底稿非真实公司"}},
              open(os.path.join(td, "manifest.json"), "w"))

    def vr(path):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_data.py"), path],
                              capture_output=True, text=True)
    r1 = vr(gp)
    check("好银行底稿通过", r1.returncode == 0, r1.stdout[-200:])
    r2 = vr(bp)
    check("坏银行底稿被拒", r2.returncode == 1, r2.stdout[-200:])
    check("不良率超界被逮住", "不良率" in r2.stdout, r2.stdout[-200:])

print("== 9.5 validate_data 门禁（A1/A2/A3/C5，GOOG 实证后新增） ==")
with tempfile.TemporaryDirectory() as td:
    import copy as _copy
    from datetime import date as _date2
    _cur = _date2.today().year      # 当前年；expected = _cur-1（上个自然年年报死线已过）
    _mini = {
        "company": "门禁测试", "ticker": "GT", "currency": "USD", "unit": "million",
        "company_type": "平台/网络效应型", "accounting_standard": "US-GAAP",
        "fiscal_year_end": "12-31",
        "annual": [
            {"year": _cur - 4, "publish_date": f"{_cur-3}-02-01", "revenue": 820.0,
             "net_income": 164.0, "ocf": 200.0, "capex": 45.0, "d_and_a": 36.0,
             "total_assets": 1430.0, "total_liabilities": 700.0,
             "total_equity": 730.0, "total_debt": 110.0, "cash": 140.0, "shares_diluted": 101.0},
            {"year": _cur - 3, "publish_date": f"{_cur-2}-02-01", "revenue": 900.0,
             "net_income": 180.0, "ocf": 220.0, "capex": 50.0, "d_and_a": 40.0,
             "total_assets": 1480.0, "total_liabilities": 680.0,
             "total_equity": 800.0, "total_debt": 100.0, "cash": 150.0, "shares_diluted": 100.0},
            {"year": _cur - 2, "publish_date": f"{_cur-1}-02-01", "revenue": 990.0,
             "net_income": 198.0, "ocf": 240.0, "capex": 60.0, "d_and_a": 44.0,
             "total_assets": 1540.0, "total_liabilities": 660.0,
             "total_equity": 880.0, "total_debt": 90.0, "cash": 160.0, "shares_diluted": 99.0},
        ],
        "crosscheck": [
            {"year": _cur - 4, "source": f"{_cur-4} 年报 10-K", "revenue": 820.0,
             "net_income": 164.0, "ocf": 200.0, "shares_diluted": 101.0},
            {"year": _cur - 3, "source": f"{_cur-3} 年报 10-K", "revenue": 900.0,
             "net_income": 180.0, "ocf": 220.0, "shares_diluted": 100.0},
            {"year": _cur - 2, "source": f"{_cur-2} 年报 10-K", "revenue": 990.0,
             "net_income": 198.0, "ocf": 240.0, "shares_diluted": 99.0},
        ],
    }

    def vd(path, extra=None):
        cmd = [sys.executable, os.path.join(SCRIPTS, "validate_data.py"), path] + (extra or [])
        return subprocess.run(cmd, capture_output=True, text=True)

    # --- A1：无豁免 manifest → 必须拦截 ---
    p1 = os.path.join(td, "a1.json")
    json.dump(_mini, open(p1, "w"))
    r = vd(p1)
    check("A1 缺最新年报被拦截", r.returncode == 1 and "年度覆盖哨兵" in r.stdout,
          r.stdout[-200:])

    # --- A1 豁免：manifest 登记 official_filing_missing → 放行 ---
    json.dump({"official_filing_missing": {"years": [_cur - 1], "reason": "公司已公告延迟申报"}},
              open(os.path.join(td, "manifest.json"), "w"))
    r = vd(p1)
    check("A1 登记豁免后放行", r.returncode == 0, r.stdout[-200:])

    # --- A2：最新年 crosscheck source 为降级来源（窗口期外）→ 拦截 ---
    bad_a2 = _copy.deepcopy(_mini)
    bad_a2["crosscheck"][-1]["source"] = "结构化接口四季加总"
    p2 = os.path.join(td, "a2.json")
    json.dump(bad_a2, open(p2, "w"))
    r = vd(p2)
    check("A2 降级来源充数被拦截", r.returncode == 1 and "命门原文级" in r.stdout,
          r.stdout[-200:])
    bad_a2["crosscheck"][-1]["source"] = f"{_cur-2} 年报 10-K"
    json.dump(bad_a2, open(p2, "w"))
    r = vd(p2)
    check("A2 官方来源放行", r.returncode == 0, r.stdout[-200:])

    # --- A3：interim 净利超上年全年 85% 且无 spike 剖析 → 拦截 ---
    bad_a3 = _copy.deepcopy(_mini)
    bad_a3["interim"] = {"period": f"{_cur-1}H1", "net_income": 210.0}
    p3 = os.path.join(td, "a3.json")
    json.dump(bad_a3, open(p3, "w"))
    r = vd(p3)
    check("A3 interim 利润异常被拦截", r.returncode == 1 and "一次性损益哨兵" in r.stdout,
          r.stdout[-200:])
    bad_a3["spike_notes"] = {f"{_cur-1}.net_income": "含一次性投资收益约 30"}
    json.dump(bad_a3, open(p3, "w"))
    r = vd(p3)
    check("A3 剖析后放行", r.returncode == 0, r.stdout[-200:])

    # --- C5：consensus 回落信号 → 警告但放行 ---
    cons = os.path.join(td, "consensus.json")
    json.dump({"eps_consensus_usd": {str(_cur - 2): {"avg": 20.0},
               str(_cur - 1): {"avg": 15.0}}}, open(cons, "w"))
    r = vd(p1, ["--consensus", cons])
    check("C5 回落信号告警", r.returncode == 0 and "回落信号" in r.stdout,
          r.stdout[-200:])

print("== 9.6 extract_edgar_annual 逐年概念回退（B4，GOOG 实证） ==")
with tempfile.TemporaryDirectory() as td:
    cf = {"$schema": "x", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            {"fy": 2024, "start": "2024-01-01", "end": "2024-12-31",
             "val": 100, "form": "10-K", "filed": "2025-02-01"}]}},
        "Revenues": {"units": {"USD": [
            {"fy": 2024, "start": "2024-01-01", "end": "2024-12-31",
             "val": 100, "form": "10-K", "filed": "2025-02-01"},
            {"fy": 2025, "start": "2025-01-01", "end": "2025-12-31",
             "val": 120, "form": "10-K", "filed": "2026-02-01"}]}},
        "NetIncomeLoss": {"units": {"USD": [
            {"fy": 2024, "start": "2024-01-01", "end": "2024-12-31",
             "val": 30, "form": "10-K", "filed": "2025-02-01"},
            {"fy": 2025, "start": "2025-01-01", "end": "2025-12-31",
             "val": 35, "form": "10-K", "filed": "2026-02-01"}]}},
    }}}
    cfp = os.path.join(td, "cf.json")
    json.dump(cf, open(cfp, "w"))
    outp = os.path.join(td, "out.json")
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "extract_edgar_annual.py"),
                        "--companyfacts", cfp, "--taxonomy", "us-gaap",
                        "--year-from", "2024", "--year-to", "2025", "--out", outp],
                       capture_output=True, text=True)
    ok = False
    if r.returncode == 0 and os.path.exists(outp):
        tab = json.load(open(outp))
        ok = (tab.get("2025", {}).get("revenue") == 120
              and tab["2025"].get("revenue__concept") == "Revenues")
    check("概念切换年份逐年回退（2025 由 Revenues 补位）", ok,
          (r.stderr or r.stdout)[-200:])

print("== 9.7 引擎边界护栏（v2.10：hold_years/非正价值/负估值/口径标记） ==")
import reverse_dcf as rdx  # noqa: E402

# --- A. hold_years 边界校验（旧版：0 抛未捕获 ZeroDivisionError、-1 静默出错数）---
for bad_hy in [0, -1, 2.5]:
    raised = False
    try:
        rdx.expected_return(100, [{"name": "基准", "value_per_share": 150.0,
                                   "probability": 1.0}], bad_hy)
    except SystemExit:
        raised = True
    except ZeroDivisionError:
        raised = False
    check(f"hold_years={bad_hy} 被显式拦截", raised)
check("hold_years=1 合法", isinstance(
    rdx.expected_return(100, [{"name": "基准", "value_per_share": 150.0,
                               "probability": 1.0}], 1)["expected_annualized_irr"], float))

# --- B. 非正内在价值：irr 与 total_return 口径必须一致（旧版 -1.0 vs -1.16 矛盾）---
rneg = rdx.expected_return(100, [
    {"name": "悲观", "value_per_share": -10.0, "probability": 0.3},
    {"name": "基准", "value_per_share": 150.0, "probability": 0.5},
    {"name": "乐观", "value_per_share": 200.0, "probability": 0.2}], 5)
s_neg = rneg["scenarios"][0]
check("负价值情景 irr = -100%", abs(s_neg["annualized_irr"] + 1.0) < 1e-12)
check("负价值情景 total_return 同为 -100%（口径一致，不再 <-100%）",
      abs(s_neg["total_return"] + 1.0) < 1e-12, str(s_neg["total_return"]))
check("负价值情景 V_H 归零", abs(s_neg["value_per_share_terminal"]) < 1e-12)
check("负价值情景打标 value_is_non_positive", s_neg["value_is_non_positive"] is True)
check("穿透标志 pessimistic_equity_wiped_out",
      rneg["pessimistic_equity_wiped_out"] is True)
check("保留悲观原始价值供区分归零/穿透",
      abs(rneg["pessimistic_value_per_share"] + 10.0) < 1e-12)
check("has_non_positive_scenario 置位", rneg["has_non_positive_scenario"] is True)
rpos = rdx.expected_return(100, [{"name": "悲观", "value_per_share": 80.0,
                                  "probability": 0.3},
                                 {"name": "基准", "value_per_share": 150.0,
                                  "probability": 0.5},
                                 {"name": "乐观", "value_per_share": 200.0,
                                  "probability": 0.2}], 5)
check("全正情景不误报穿透",
      rpos["pessimistic_equity_wiped_out"] is False
      and rpos["has_non_positive_scenario"] is False)

# --- C. 口径标记与 Jensen 间隙（防下游拿 total_return 年化当闸门二）---
check("gate2 判定字段被显式指定",
      rpos["gate2_decision_field"] == "expected_annualized_irr")
check("两个回报字段均带 _basis 标记",
      "expected_annualized_irr_basis" in rpos and "expected_total_return_basis" in rpos)
gap_expected = ((1 + rpos["expected_total_return"]) ** (1 / 5) - 1) \
    - rpos["expected_annualized_irr"]
check("jensen_gap 计算正确且为正（凹性）",
      abs(rpos["jensen_gap_vs_annualized_total"] - gap_expected) < 1e-12
      and rpos["jensen_gap_vs_annualized_total"] > 0,
      str(rpos["jensen_gap_vs_annualized_total"]))
check("闸门二仍以主 IRR 字段比门槛",
      rpos["beats_index"] == (rpos["expected_annualized_irr"] > rpos["index_hurdle"]))

# --- D. implied-growth 三态区分（旧版负市值与超区间同为一句"无解"+exit 0）---
g_ok, st_ok = rdx.solve_implied_growth(30000, 2000, 0.10, 0.025, 10)
check("正常市值 status=ok", st_ok == "ok" and g_ok is not None)
g_neg, st_neg = rdx.solve_implied_growth(-5000, 2000, 0.10, 0.025, 10)
check("负经营市值 status=negative_operating_value",
      st_neg == "negative_operating_value" and g_neg is None)
g_zero, st_zero = rdx.solve_implied_growth(0, 2000, 0.10, 0.025, 10)
check("零经营市值同归负估值分支", st_zero == "negative_operating_value")
g_oor, st_oor = rdx.solve_implied_growth(1e12, 2000, 0.10, 0.025, 10)
check("超增速区间 status=out_of_range",
      st_oor == "out_of_range" and g_oor is None)
pneg = run(["implied-growth", "--market-cap", "10000", "--base-oe", "2000",
            "--deduct", "15000"])
check("CLI 负估值以非零退出码中断（不再静默 exit 0）", pneg.returncode == 2,
      f"rc={pneg.returncode}")
check("CLI 负估值输出重大信号提示", "重大信号" in pneg.stdout)
poor = run(["implied-growth", "--market-cap", "1000000000", "--base-oe", "1"])
check("CLI 超区间仍为 exit 0（性质不同）", poor.returncode == 0)

# --- E. 回归：P=V0 铁律与已归档 4 案例数字不受本次改动影响 ---
riron = rdx.expected_return(100, [{"name": "基准", "value_per_share": 100.0,
                                   "probability": 1.0}], 5, discount_rate=0.10)
check("回归：P=V0 时 IRR 恒等于折现率",
      abs(riron["expected_annualized_irr"] - 0.10) < 1e-9)
_ARCHIVED = {"招行": (40.53, [(34.0, 0.3), (47.0, 0.5), (58.0, 0.2)], 0.121490),
             "平安": (56.81, [(41.54, 0.3), (66.68, 0.5), (88.90, 0.2)], 0.118491)}
for nm, (pr, sc_, expect) in _ARCHIVED.items():
    rr = rdx.expected_return(pr, [{"name": str(i), "value_per_share": v,
                                   "probability": p} for i, (v, p) in enumerate(sc_)], 5)
    check(f"回归：{nm} 期望 IRR 与归档一致",
          abs(rr["expected_annualized_irr"] - expect) < 1e-5,
          f"{rr['expected_annualized_irr']:.6f} vs {expect}")

print("== 9.8 P0 静默错误护栏（v2.11：永续上限/间距/fade语义/负基期/诊断结构化） ==")
import reverse_dcf as rdp  # noqa: E402


def _rejects(fn, *a, **kw):
    """返回 True 表示按预期以 SystemExit 拒绝。"""
    try:
        fn(*a, **kw)
        return False
    except SystemExit:
        return True


# --- A. 永续增速上限（此前文档写了纪律、代码零校验）---
check("永续增速 8% 超上限被拒（此前静默照算）",
      _rejects(rdp.dcf_value, 100, 0.10, 0.10, 0.08, 10))
check("永续增速 5% 在默认上限内放行",
      isinstance(rdp.dcf_value(100, 0.05, 0.10, 0.05, 10, min_spread=0.01), float))
check("显式放宽 terminal_g_cap 后放行（须报告论证）",
      isinstance(rdp.dcf_value(100, 0.10, 0.12, 0.06, 10,
                               terminal_g_cap=0.07), float))

# --- B. r 与 g 的安全间距（Gordon 分母趋零导致价值爆炸）---
check("r=10%/g=9.99% 被拒（此前得出 6915× OE 且不报错）",
      _rejects(rdp.dcf_value, 100, 0.05, 0.10, 0.0999, 10))
check("r=10%/g=9% 间距 1pct < 2pct 被拒",
      _rejects(rdp.dcf_value, 100, 0.05, 0.10, 0.09, 10))
check("r=10%/g=2.5% 间距充足放行",
      isinstance(rdp.dcf_value(100, 0.05, 0.10, 0.025, 10), float))
check("g>=r 仍被拒（原有校验未回退）",
      _rejects(rdp.dcf_value, 100, 0.05, 0.10, 0.10, 10))

# --- C. fade 静默失效与语义反转 ---
check("years=1 且 fade 被拒（此前静默忽略 fade）",
      _rejects(rdp.dcf_value, 100, 0.30, 0.10, 0.025, 1, fade=True))
check("years=1 不带 fade 正常放行",
      isinstance(rdp.dcf_value(100, 0.30, 0.10, 0.025, 1), float))
check("growth<terminal_g 且 fade 被拒（语义反转：衰减变爬升）",
      _rejects(rdp.dcf_value, 100, 0.01, 0.10, 0.025, 10, fade=True))
check("growth<terminal_g 但不带 fade 放行（衰退型公司的正当用法）",
      isinstance(rdp.dcf_value(100, 0.01, 0.10, 0.025, 10), float))

# --- D. 负/零基期拒绝（亏损公司应改走反向DCF+单位经济）---
for bad in [-50, 0]:
    check(f"base_oe={bad} 被拒（此前静默产出负内在价值）",
          _rejects(rdp.dcf_value, bad, 0.10, 0.10, 0.025, 10))

# --- E. 求解器豁免：二分法需试探 growth<terminal_g，不得被 fade 检查打断 ---
g_ok, st_ok = rdp.solve_implied_growth(30000, 2000, 0.10, 0.025, 10, fade=True)
check("solve_implied_growth 在 fade 下仍能求解（_solver_mode 豁免生效）",
      st_ok == "ok" and g_ok is not None, f"{st_ok} {g_ok}")
check("求解器仍受永续上限约束（护栏未被豁免掉）",
      _rejects(rdp.solve_implied_growth, 30000, 2000, 0.10, 0.08, 10))

# --- F. 终值占比诊断结构化（此前只在 CLI print，下游拿不到）---
_, _, _, dc = rdp.dcf_value(100, 0.35, 0.10, 0.03, 10, split=True)
check("高增长触发 critical 且置买入阻断标志",
      dc["level"] == "critical" and dc["blocks_margin_of_safety_only_buy"] is True,
      f"{dc['level']} ratio={dc['terminal_value_ratio']:.3f}")
_, _, _, dw = rdp.dcf_value(100, 0.20, 0.10, 0.025, 10, split=True)
check("中档占比触发 warning 且不阻断",
      dw["level"] == "warning" and dw["blocks_margin_of_safety_only_buy"] is False,
      f"{dw['level']} ratio={dw['terminal_value_ratio']:.3f}")
check("诊断含 spread 供下游核对假设间距",
      abs(dw["spread"] - (0.10 - 0.025)) < 1e-12)

# --- G. CLI 层：拒绝须为非零退出码 + JSON 落盘含诊断 ---
pbad = run(["forward-value", "--base-oe", "100", "--growth", "0.05",
            "--discount-rate", "0.10", "--terminal-growth", "0.099"])
check("CLI 间距不足以非零退出码中断", pbad.returncode != 0, f"rc={pbad.returncode}")
with tempfile.TemporaryDirectory() as td:
    fv = os.path.join(td, "fv.json")
    pgood = run(["forward-value", "--base-oe", "1000", "--growth", "0.08",
                 "--discount-rate", "0.10", "--terminal-growth", "0.025",
                 "--years", "10", "--shares", "100", "-o", fv])
    ok_json = False
    if pgood.returncode == 0 and os.path.exists(fv):
        d = json.load(open(fv))
        ok_json = ("terminal_diagnostics" in d
                   and "value_per_share" in d
                   and abs(d["terminal_diagnostics"]["terminal_value_ratio"]
                           - d["terminal_diagnostics"]["terminal_pv"]
                           / d["operating_value"]) < 1e-9)
    check("forward-value -o 落盘含结构化终值诊断", ok_json,
          (pgood.stderr or pgood.stdout)[-200:])

# --- H. 回归：归档案例的常规假设不得被新护栏误伤 ---
_ARCHIVED_DCF = [
    ("NVDA 正常化基期", 72800, 0.10, 0.10, 0.025, 10),
    ("腾讯保守轨", 219000, 0.05, 0.10, 0.025, 10),
    ("伊利", 9000, 0.04, 0.09, 0.025, 10),
]
for nm, boe, g_, r_, tg_, yr_ in _ARCHIVED_DCF:
    try:
        v_ = rdp.dcf_value(boe, g_, r_, tg_, yr_)
        okv = v_ > 0
    except SystemExit:
        okv = False
    check(f"回归：{nm} 常规假设未被误伤", okv)

print("== 9.9 数据链路门禁（v2.11：静默崩溃/来源误报/覆盖率/搬运完整性） ==")


def run_validate(path, extra=None):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "validate_data.py"), path] + (extra or []),
        capture_output=True, text=True)


def run_transcription(raw, draft, extra=None):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "check_transcription.py"),
         "--raw", raw, "--draft", draft] + (extra or []),
        capture_output=True, text=True)


import copy as _c9
from datetime import date as _d9

_y = _d9.today().year


def _draft(nyears=5, with_bs=True, with_pub=True):
    rows = []
    for i in range(nyears):
        yr = _y - nyears + i
        r = {"year": yr, "revenue": 1000.0 + i * 50, "net_income": 150.0 + i * 5,
             "ocf": 180.0 + i * 5, "capex": 40.0, "d_and_a": 30.0,
             "total_equity": 800.0, "total_debt": 100.0, "cash": 150.0,
             "shares_diluted": 100.0, "gross_profit": 400.0}
        if with_bs:
            r["total_assets"] = 1500.0
            r["total_liabilities"] = 700.0
        if with_pub:
            r["publish_date"] = f"{yr + 1}-02-01"
        rows.append(r)
    d = {"company": "链路测试", "ticker": "DL", "currency": "USD", "unit": "million",
         "company_type": "平台/网络效应型", "accounting_standard": "US-GAAP",
         "fiscal_year_end": "12-31", "annual": rows, "crosscheck": []}
    for r in rows[-3:]:
        d["crosscheck"].append({
            "year": r["year"], "source": f"10-K {r['year']} (EDGAR)",
            "revenue": r["revenue"], "net_income": r["net_income"],
            "ocf": r["ocf"], "shares_diluted": r["shares_diluted"]})
    return d


# --- A. 静默崩溃：无 publish_date 时旧版因 date 变量遮蔽裸崩（0错0警+rc=1）---
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "financials_nopub.json")
    json.dump(_draft(with_pub=False), open(p, "w"), ensure_ascii=False)
    r = run_validate(p)
    check("无 publish_date 不再静默崩溃（旧版 UnboundLocalError）",
          "UnboundLocalError" not in (r.stdout + r.stderr), (r.stdout + r.stderr)[-160:])
    check("无 publish_date 仍能正常产出校验结论",
          "入口校验" in r.stdout and r.returncode in (0, 1), f"rc={r.returncode}")
    check("崩溃兜底：内部异常用退出码 3 与数据不合格(1)区分",
          "退出码 3" in open(os.path.join(SCRIPTS, "validate_data.py"),
                            encoding="utf-8").read())

# --- B. 来源判定顺序：官方原文 + 降级措辞并存时不得误报 ---
with tempfile.TemporaryDirectory() as td:
    d = _draft()
    d["crosscheck"][-1]["source"] = "20-F 2025 披露接口值（Q4 业绩公告交叉）"
    p = os.path.join(td, "financials_src.json")
    json.dump(d, open(p, "w"), ensure_ascii=False)
    r = run_validate(p)
    check("含『20-F』的来源不因含『接口』被判非官方（PDD 误报修复）",
          "非官方披露原文" not in r.stdout, r.stdout[-200:])
    check("但会提示措辞混用（仅警告不阻断）",
          "来源措辞含降级词" in r.stdout, r.stdout[-200:])
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("vd", os.path.join(SCRIPTS, "validate_data.py"))
    _vd = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_vd)
    check("is_official_source: 纯降级来源仍判非官方",
          _vd.is_official_source("四季度加总估算") is False)
    check("is_official_source: 官方标识优先命中",
          _vd.is_official_source("20-F 披露接口值") is True)

# --- C. 覆盖率哨兵：缺资产负债表致勾稽大面积未执行时必须阻断 ---
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "financials_nobs.json")
    json.dump(_draft(with_bs=False), open(p, "w"), ensure_ascii=False)
    r = run_validate(p)
    check("勾稽覆盖率 0% 时阻断（旧版仅 WARN 后判通过）",
          r.returncode == 1 and "覆盖率哨兵" in r.stdout, f"rc={r.returncode}")
    r2 = run_validate(p, ["--skip-crosscheck"])
    check("竞对底稿降级为警告不阻断",
          "覆盖率哨兵" in r2.stdout and r2.returncode == 0, f"rc={r2.returncode}")
    mp = os.path.join(td, "manifest.json")
    json.dump({"reconciliation_coverage_waiver": {"reason": "早年未披露"},
               "files": [{"name": "x", "source": "EDGAR", "grade": "A级"}],
               "adversarial_check": "已检索无发现"},
              open(mp, "w"), ensure_ascii=False)
    r3 = run_validate(p)
    check("manifest 登记豁免后放行（显式承担而非静默）",
          r3.returncode == 0 and "已登记豁免" in r3.stdout, f"rc={r3.returncode}")
    with tempfile.TemporaryDirectory() as td2:
        p4 = os.path.join(td2, "financials_ok.json")
        json.dump(_draft(with_bs=True), open(p4, "w"), ensure_ascii=False)
        r4 = run_validate(p4)
        check("勾稽 100% 覆盖不触发哨兵（不误伤合格底稿）",
              "覆盖率哨兵" not in r4.stdout and r4.returncode == 0, f"rc={r4.returncode}")

# --- D. 信息广度：对立面检索未留痕须提示 ---
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "financials_adv.json")
    json.dump(_draft(), open(p, "w"), ensure_ascii=False)
    json.dump({"files": [{"name": "a", "source": "EDGAR", "grade": "A级"}]},
              open(os.path.join(td, "manifest.json"), "w"), ensure_ascii=False)
    r = run_validate(p)
    check("manifest 无对立面检索留痕时告警（10 案例仅 1 个登记）",
          "对立面检索" in r.stdout, r.stdout[-200:])
    json.dump({"files": [{"name": "a", "source": "EDGAR", "grade": "A级"}],
               "adversarial_check": {"date": "2026-09-01", "result": "无发现"}},
              open(os.path.join(td, "manifest.json"), "w"), ensure_ascii=False)
    r2 = run_validate(p)
    check("登记后不再告警", "对立面检索" not in r2.stdout, r2.stdout[-200:])

# --- E. 搬运完整性：抽取有值→底稿为空必须报错（GOOG/TSM 实证事故）---
with tempfile.TemporaryDirectory() as td:
    raw = {str(_y - 3): {"revenue": 1000e6, "net_income": 150e6, "ocf": 180e6,
                         "assets": 1500e6, "liabilities": 700e6, "equity": 800e6},
           str(_y - 2): {"revenue": 1050e6, "net_income": 155e6, "ocf": 185e6,
                         "assets": 1600e6, "liabilities": 750e6, "equity": 850e6}}
    rp = os.path.join(td, "raw.json")
    json.dump(raw, open(rp, "w"))
    d = {"company": "T", "currency": "USD", "unit": "million", "annual": [
        {"year": _y - 3, "revenue": 1000.0, "net_income": 150.0, "ocf": 180.0},
        {"year": _y - 2, "revenue": 1050.0, "net_income": 155.0, "ocf": 185.0}]}
    dp = os.path.join(td, "draft.json")
    json.dump(d, open(dp, "w"))
    r = run_transcription(rp, dp)
    check("搬运丢失资产负债表科目被判 ERROR", r.returncode == 1, f"rc={r.returncode}")
    check("报错指明丢失字段与年份", "total_assets" in r.stdout and "搬运丢失" in r.stdout)
    for row in d["annual"]:
        yy = str(row["year"])
        row["total_assets"] = raw[yy]["assets"] / 1e6
        row["total_liabilities"] = raw[yy]["liabilities"] / 1e6
        row["total_equity"] = raw[yy]["equity"] / 1e6
    json.dump(d, open(dp, "w"))
    r2 = run_transcription(rp, dp)
    check("补齐后搬运校验通过", r2.returncode == 0, r2.stdout[-200:])
    d["annual"][0]["revenue"] = 1200.0
    json.dump(d, open(dp, "w"))
    r3 = run_transcription(rp, dp)
    check("数值搬错（1000→1200）被捕获",
          r3.returncode == 1 and "搬运不一致" in r3.stdout, r3.stdout[-200:])

# --- F. 口径合法差异豁免：cash 用 cash_sti 口径不得误报 ---
with tempfile.TemporaryDirectory() as td:
    raw = {str(_y - 2): {"revenue": 1000e6, "cash": 12e6, "cash_sti": 86e6}}
    rp, dp = os.path.join(td, "r.json"), os.path.join(td, "d.json")
    json.dump(raw, open(rp, "w"))
    json.dump({"company": "T", "unit": "million", "annual": [
        {"year": _y - 2, "revenue": 1000.0, "cash": 86.0}]}, open(dp, "w"))
    r = run_transcription(rp, dp)
    check("底稿 cash 取 cash_sti 口径不误报（GOOG 真实形态）",
          "cash" not in r.stdout.replace("cash_sti", ""), r.stdout[-200:])

print("== 9.10 所有者视角指标（v2.12：分红幻觉/字段别名/每股兜底） ==")
with tempfile.TemporaryDirectory() as td:
    def _cm(draft):
        pth = os.path.join(td, "f.json")
        json.dump(draft, open(pth, "w"), ensure_ascii=False)
        out = os.path.join(td, "m.json")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "compute_metrics.py"),
                            pth, "-o", out], capture_output=True, text=True)
        return (json.load(open(out)) if os.path.exists(out) else None), r

    def _base(**kw):
        rows = []
        for i in range(6):
            r = {"year": 2020 + i, "revenue": 1000.0, "net_income": 100.0,
                 "ocf": 120.0, "capex": 40.0, "d_and_a": 40.0,
                 "total_equity": 800.0, "total_debt": 100.0, "cash": 100.0,
                 "shares_diluted": 100.0}
            r.update(kw)
            rows.append(r)
        return {"company": "OT", "ticker": "OT", "currency": "CNY", "unit": "million",
                "company_type": "品牌消费品", "annual": rows}

    # A. 字段别名：底稿写 dividends（非 dividends_paid）也要读到
    d, _ = _cm(_base(dividends=30.0))
    ca = d["capital_allocation"]
    check("字段别名：底稿 `dividends` 被正确累计（中国建筑静默 null 的根因）",
          ca["cum_dividends"] == 180.0, str(ca["cum_dividends"]))
    check("分红口径标记为披露值", ca.get("dividends_basis") == "dividends_paid")

    # B. 分红幻觉：股东回报 > 累计 FCF
    d, _ = _cm(_base(ocf=50.0, capex=60.0, dividends=30.0))
    ca = d["capital_allocation"]
    check("累计 FCF 为负时覆盖倍数为负", ca["fcf_cover_shareholder_return"] < 0)
    check("分红幻觉警报触发（回报靠融资而非经营）",
          any("分红幻觉" in a for a in d["alerts"]), str(d["alerts"])[:120])
    check("shareholder_return_funded_by_fcf 标记为 False",
          ca["shareholder_return_funded_by_fcf"] is False)

    # C. 判别力：真金白银分红不得误报
    d, _ = _cm(_base(ocf=200.0, capex=40.0, dividends=30.0))
    check("充沛 FCF 覆盖分红不触发幻觉警报",
          not any("分红幻觉" in a for a in d["alerts"]),
          str(d["capital_allocation"]["fcf_cover_shareholder_return"]))
    check("覆盖倍数 >1.5x 时连'偏薄'也不报",
          not any("覆盖偏薄" in a for a in d["alerts"]))

    # D. 覆盖偏薄档（1.0~1.5x）
    d, _ = _cm(_base(ocf=120.0, capex=40.0, dividends=64.0))
    cov = d["capital_allocation"]["fcf_cover_shareholder_return"]
    check("1.0~1.5x 触发覆盖偏薄提示（非幻觉）",
          1.0 <= cov < 1.5 and any("覆盖偏薄" in a for a in d["alerts"]), f"cov={cov:.2f}")

    # E. 每股分红兜底（港股/A股底稿常只有 dividend_per_share）
    d, _ = _cm(_base(dividend_per_share=0.3))
    ca = d["capital_allocation"]
    check("仅有 dividend_per_share 时用×股本兜底推算",
          ca["cum_dividends"] == 180.0, str(ca["cum_dividends"]))
    check("推算口径被显式标记（与披露值可区分）",
          "estimated" in str(ca.get("dividends_basis")), str(ca.get("dividends_basis")))

    # F. 完全无分红回购：给 warning 而非静默
    d, _ = _cm(_base())
    check("无分红回购数据时提示所有者口径缺口",
          any("所有者口径缺口" in w for w in d.get("warnings", [])),
          str(d.get("warnings"))[:120])
    check("无回报数据时覆盖倍数为 None（不伪造）",
          d["capital_allocation"]["fcf_cover_shareholder_return"] is None)

print("== 9.11 生意视角：所有者收益率 + 驱动因子校验（v2.13） ==")
with tempfile.TemporaryDirectory() as td:
    def _mk(**kw):
        rows = []
        for i in range(6):
            r = {"year": 2020 + i, "revenue": 1000.0, "net_income": 100.0,
                 "ocf": 150.0, "capex": 40.0, "d_and_a": 40.0,
                 "total_equity": 800.0, "total_debt": 100.0, "cash": 100.0,
                 "shares_diluted": 100.0}
            r.update(kw)
            rows.append(r)
        return {"company": "OY", "ticker": "OY", "currency": "CNY",
                "unit": "million", "company_type": "品牌消费品", "annual": rows}

    def _run(draft, mc=None):
        pth = os.path.join(td, "f.json")
        json.dump(draft, open(pth, "w"), ensure_ascii=False)
        out = os.path.join(td, "m.json")
        cmd = [sys.executable, os.path.join(SCRIPTS, "compute_metrics.py"), pth, "-o", out]
        if mc is not None:
            cmd += ["--market-cap", str(mc)]
        subprocess.run(cmd, capture_output=True, text=True)
        return json.load(open(out)) if os.path.exists(out) else None

    # A. 未传市值时 owner_yield 为 None（不伪造）
    d = _run(_mk(dividends=30.0))
    check("未传 --market-cap 时 owner_yield 为 None", d["owner_yield"] is None)

    # B. 传市值：OE 收益率与回本年数
    d = _run(_mk(dividends=30.0), mc=1000.0)
    oy = d["owner_yield"]
    check("传市值后算出所有者收益率", oy is not None and oy["owner_yield_current"] is not None,
          str(oy)[:80] if oy else "None")
    check("回本年数 = 1/收益率",
          abs(oy["payback_years"] - 1 / (oy["owner_yield_normalized"]
              or oy["owner_yield_current"])) < 1e-6)
    check("现金充沛时标记 cash_backed=True", oy["cash_backed"] is True, str(oy["cash_backed"]))
    check("现金充沛时无含金量警报",
          not any("所有者收益率含金量" in a for a in d["alerts"]))

    # C. 含金量守卫：OE 收益率漂亮但回报靠融资（中国建筑形态）
    d = _run(_mk(ocf=50.0, capex=60.0, dividends=30.0), mc=1000.0)
    oy = d["owner_yield"]
    check("回报未被 FCF 覆盖时 cash_backed=False", oy["cash_backed"] is False)
    check("附带 caveat 说明不可落袋", "不可落袋" in (oy.get("caveat") or ""))
    check("触发所有者收益率含金量警报（防21%假低估被误读）",
          any("所有者收益率含金量" in a for a in d["alerts"]), str(d["alerts"])[:100])

    # D. 驱动因子校验器：正向/负向
    bd_ok = {"company": "T", "ticker": "T", "company_type": "制造业",
             "drivers": [{"year": 2020 + i, "volume": 100.0, "price": 10.0,
                          "volume_label": "出货量(万件)", "price_label": "ASP(元)",
                          "source": "年报 p.1", "source_level": "A"} for i in range(5)],
             "unit_economics": {"metric": "单件毛利", "value": 3.0, "source": "年报"}}
    pth = os.path.join(td, "bd.json")
    json.dump(bd_ok, open(pth, "w"), ensure_ascii=False)
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_business_drivers.py"), pth],
                       capture_output=True, text=True)
    check("驱动因子底稿合规时通过", r.returncode == 0, r.stdout[-160:])

    bd_bad = {"company": "T", "ticker": "T", "company_type": "制造业",
              "drivers": [{"year": 2024, "volume": 100.0}]}
    json.dump(bd_bad, open(pth, "w"), ensure_ascii=False)
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_business_drivers.py"), pth],
                       capture_output=True, text=True)
    check("驱动因子缺 source 时拒绝（底稿是唯一事实源）", r.returncode == 1, r.stdout[-160:])

    bd_empty = {"company": "T", "ticker": "T", "company_type": "互联网平台", "drivers": []}
    json.dump(bd_empty, open(pth, "w"), ensure_ascii=False)
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_business_drivers.py"), pth],
                       capture_output=True, text=True)
    check("drivers 为空时拒绝（收入是会计结果，量价才是生意）", r.returncode == 1)
    check("空 drivers 提示该类型建议口径（MAU/ARPU）",
          "MAU" in r.stdout or "ARPU" in r.stdout, r.stdout[-120:])

    bd_bank = {"company": "B", "ticker": "B", "company_type": "银行", "drivers": []}
    json.dump(bd_bank, open(pth, "w"), ensure_ascii=False)
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_business_drivers.py"), pth],
                       capture_output=True, text=True)
    check("金融类自动跳过驱动因子校验", r.returncode == 0 and "跳过" in r.stdout, r.stdout[-100:])

    # E. 量纲对齐：单位不同不应误报为口径不一致
    m_pth = os.path.join(td, "mm.json")
    json.dump({"series": [{"year": 2020 + i, "revenue": 1000.0} for i in range(5)]},
              open(m_pth, "w"))
    json.dump(bd_ok, open(pth, "w"), ensure_ascii=False)
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_business_drivers.py"),
                        pth, "--metrics", m_pth], capture_output=True, text=True)
    check("量×价与收入量纲不同(1000 vs 1000)仍能勾稽通过",
          r.returncode == 0 and "偏差" not in r.stdout, r.stdout[-160:])

print("== 9.12 数据源探测与登记（v2.14：源可插拔 + 缺源不静默） ==")
with tempfile.TemporaryDirectory() as td:
    CDS = os.path.join(SCRIPTS, "check_data_sources.py")

    r = subprocess.run([sys.executable, CDS], capture_output=True, text=True)
    check("探测器可运行且退出码 0（本机装有推荐源）", r.returncode == 0, r.stdout[-120:])
    check("报告中写出数据源真名 westock-data", "westock-data" in r.stdout)
    check("标注 ifind 为付费可选（不得成为硬依赖）",
          "付费" in r.stdout and ("可选" in r.stdout or "非必需" in r.stdout))
    check("列出已知缺口与降级成本（缺源不静默）",
          "已知缺口" in r.stdout and "capex" in r.stdout, r.stdout[-100:])
    check("声明源可插拔（唯一契约是底稿）",
          "不绑定" in r.stdout and "底稿" in r.stdout)

    # manifest 未登记 data_sources → 拒绝
    mf = os.path.join(td, "m1.json")
    json.dump({"files": []}, open(mf, "w"))
    r = subprocess.run([sys.executable, CDS, "--manifest", mf],
                       capture_output=True, text=True)
    check("manifest 未登记 data_sources 时退出码 1", r.returncode == 1, r.stdout[-100:])
    check("未登记时说明可追溯性理由", "追溯" in r.stdout)

    # manifest 已登记 → 通过
    mf2 = os.path.join(td, "m2.json")
    json.dump({"files": [], "data_sources": [
        {"source": "westock-data", "version": "1.0.4",
         "used_for": ["三表"], "level": "A"}]}, open(mf2, "w"))
    r = subprocess.run([sys.executable, CDS, "--manifest", mf2],
                       capture_output=True, text=True)
    check("manifest 已登记 data_sources 时通过", r.returncode == 0, r.stdout[-100:])

    # manifest 不存在 → 拒绝（不静默跳过）
    r = subprocess.run([sys.executable, CDS, "--manifest",
                        os.path.join(td, "nope.json")], capture_output=True, text=True)
    check("manifest 路径不存在时拒绝而非静默跳过", r.returncode == 1)

    # 版本比较工具的边界
    sys.path.insert(0, SCRIPTS)
    import importlib
    cds = importlib.import_module("check_data_sources")
    check("版本比较：1.0.4 < 1.0.6",
          cds._ver_tuple("1.0.4") < cds._ver_tuple("1.0.6"))
    check("版本比较：非数字段不崩溃",
          cds._ver_tuple("1.0.6-beta") == (1, 0, 6))
    sys.path.remove(SCRIPTS)

# 归档案例必须全部登记数据源（可追溯性回归）
_root = os.path.dirname(SCRIPTS)
_cases = sorted(glob.glob(os.path.join(_root, "cases", "*", "data", "manifest.json")))
_missing = []
for _p in _cases:
    try:
        _m = json.load(open(_p, encoding="utf-8"))
        if not (isinstance(_m, dict) and _m.get("data_sources")):
            _missing.append(os.path.basename(os.path.dirname(os.path.dirname(_p))))
    except Exception:  # noqa: BLE001
        _missing.append(os.path.basename(os.path.dirname(os.path.dirname(_p))))
check(f"全部 {len(_cases)} 个归档案例均登记 data_sources",
      not _missing, f"缺失：{_missing}")

print("== 9.13 命门科目交叉核对（v2.15：A4 强制科目缺核对即报错 + EDGAR 机器核对） ==")
with tempfile.TemporaryDirectory() as td:
    CCO = os.path.join(SCRIPTS, "crosscheck_official.py")

    def _fin(**kw):
        base = {"company": "T", "unit": "million",
                "annual": [{"year": y, "revenue": 1000.0 + (y - 2000), "net_income": 150.0,
                            "ocf": 180.0, "shares_diluted": 100.0,
                            "total_assets": 1500.0, "total_liabilities": 700.0,
                            "total_equity": 800.0} for y in (2023, 2024, 2025)]}
        base.update(kw)
        return base

    def _cc(over=None):
        over = over or {}
        out = []
        for y in (2023, 2024, 2025):
            e = {"year": y, "source": f"{y}年报（巨潮）", "revenue": 1000.0 + (y - 2000),
                 "net_income": 150.0, "ocf": 180.0, "shares_diluted": 100.0}
            e.update(over.get(y, {}))
            out.append(e)
        return out

    # A4：强制科目官方值缺失 → 必须是错误，不能只告警
    f1 = os.path.join(td, "f1.json")
    json.dump(_fin(crosscheck=_cc({y: {"shares_diluted": None}
                                   for y in (2023, 2024, 2025)})),
              open(f1, "w"))
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_data.py"), f1],
                       capture_output=True, text=True)
    check("A4 强制科目 shares_diluted 缺官方值 → 入口校验报错",
          r.returncode == 1 and "A4" in r.stdout, r.stdout[-160:])
    check("A4 报错文案给出 crosscheck_exempt 豁免出路",
          "crosscheck_exempt" in r.stdout)

    # crosscheck_exempt 显式豁免 → 降级为警告并要求报告披露
    f2 = os.path.join(td, "f2.json")
    json.dump(_fin(crosscheck=_cc({y: {"shares_diluted": None}
                                   for y in (2023, 2024, 2025)}),
                   crosscheck_exempt={"shares_diluted": "库存股口径不可比，另用市值反推校验"}),
              open(f2, "w"))
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_data.py"), f2],
                       capture_output=True, text=True)
    check("显式豁免后 A4 不再报错且标注已豁免",
          "已豁免" in r.stdout and "双源核对(A4)" not in r.stdout, r.stdout[-200:])

    # 体检模式：缺核对年份 → 报错
    f3 = os.path.join(td, "f3.json")
    json.dump(_fin(), open(f3, "w"))
    r = subprocess.run([sys.executable, CCO, "--financials", f3, "--audit"],
                       capture_output=True, text=True)
    check("体检模式：无 crosscheck 区块 → 退出码 1", r.returncode == 1)
    check("体检模式提示竞对可标 is_peer", "is_peer" in r.stdout)

    # 竞对底稿豁免
    f4 = os.path.join(td, "f4.json")
    json.dump(_fin(is_peer=True), open(f4, "w"))
    r = subprocess.run([sys.executable, CCO, "--financials", f4, "--audit"],
                       capture_output=True, text=True)
    check("竞对底稿(is_peer) 跳过命门核对并通过", r.returncode == 0, r.stdout[-120:])

    # EDGAR 机器核对：概念中途切换必须逐年回退取到（GOOG 失效模式）
    def _pt(s, e, v, form="10-K"):
        return {"start": s, "end": e, "val": v, "form": form, "fp": "FY"}
    cf = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [_pt("2023-01-01", "2023-12-31", 1023e6)]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            _pt("2024-01-01", "2024-12-31", 1024e6),
            _pt("2025-01-01", "2025-12-31", 1025e6)]}},
        "NetIncomeLoss": {"units": {"USD": [
            _pt(f"{y}-01-01", f"{y}-12-31", 150e6) for y in (2023, 2024, 2025)]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _pt(f"{y}-01-01", f"{y}-12-31", 180e6) for y in (2023, 2024, 2025)]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            _pt("2023-01-01", "2023-12-31", 100e6),
            _pt("2024-01-01", "2024-12-31", 100e6),
            _pt("2025-01-01", "2025-12-31", 93e6)]}},  # 2025 故意与底稿不符
    }}}
    cfp = os.path.join(td, "cf.json")
    json.dump(cf, open(cfp, "w"))
    f5 = os.path.join(td, "f5.json")
    json.dump(_fin(), open(f5, "w"))
    r = subprocess.run([sys.executable, CCO, "--financials", f5,
                        "--companyfacts", cfp], capture_output=True, text=True)
    check("EDGAR 机器核对：概念中途切换仍逐年取到收入",
          "2023 revenue" in r.stdout and "❌ 2023 revenue" not in r.stdout,
          r.stdout[-200:])
    check("EDGAR 机器核对：股本单位正确缩放（不误报 100% 偏差）",
          "偏差 100.0%" not in r.stdout, r.stdout[-200:])
    check("EDGAR 机器核对：逮出真实不一致的那一年（2025 股本）",
          r.returncode == 1 and "❌ 2025 shares_diluted" in r.stdout,
          r.stdout[-200:])

    # 极端值反推体检：底稿与官方完全一致时必须 0 错误
    f6 = os.path.join(td, "f6.json")
    fin6 = _fin()
    fin6["annual"][2]["shares_diluted"] = 93.0
    json.dump(fin6, open(f6, "w"))
    r = subprocess.run([sys.executable, CCO, "--financials", f6,
                        "--companyfacts", cfp], capture_output=True, text=True)
    check("极端值体检：底稿与官方逐项一致 → 0 错误",
          r.returncode == 0, r.stdout[-200:])

# 归档主底稿必须全部通过命门核对体检（可追溯性回归）
_PRIMARY = {"cscec": "financials_CSCEC.json", "nvidia": "financials_NVDA.json",
            "weibo": "financials_WB.json", "yili": "financials_yili.json",
            "tencent": "financials_tencent.json", "popmart": "financials_popmart.json",
            "pdd": "financials_pdd.json", "goog": "financials_goog.json",
            "tsm": "financials_tsm.json", "cmb": "financials_cmb.json",
            "pingan_china": "financials_pingan_insurance.json"}
_root = os.path.dirname(SCRIPTS)
_bad = []
for _c, _fn in _PRIMARY.items():
    _p = os.path.join(_root, "cases", _c, "data", _fn)
    if not os.path.exists(_p):
        continue
    _r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "crosscheck_official.py"),
                         "--financials", _p, "--audit"],
                        capture_output=True, text=True)
    if _r.returncode != 0:
        _bad.append(_c)
check("全部归档主底稿通过命门科目核对体检", not _bad, f"未通过：{_bad}")

# 平安股本口径断裂修正回归：evps 序列不得再出现机械腰斩
_pa = os.path.join(_root, "cases", "pingan_china", "data",
                   "financials_pingan_insurance.json")
if os.path.exists(_pa):
    _d = json.load(open(_pa, encoding="utf-8"))
    _sh = {r["year"]: r.get("shares_diluted") for r in _d["annual"]}
    check("平安股本全期统一为总股本口径（不再混入 H 股 8890）",
          8890 not in _sh.values(), f"仍含 8890：{_sh}")
    _ev = [r.get("evps") for r in _d["annual"] if r.get("evps")]
    _drop = [i for i in range(1, len(_ev)) if _ev[i] < _ev[i - 1] * 0.7]
    check("平安 evps 序列无机械腰斩（口径断裂已修）", not _drop,
          f"仍有断点 idx={_drop}: {_ev}")

print()
if FAILED:
    print(f"结果：{len(FAILED)} 项失败 → {FAILED}")
    sys.exit(1)
print("结果：全部通过。")
