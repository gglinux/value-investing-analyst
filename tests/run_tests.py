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
import re
import shutil
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
            cmd += ["--market-cap-million", str(mc)]
        subprocess.run(cmd, capture_output=True, text=True)
        return json.load(open(out)) if os.path.exists(out) else None

    # A. 未传市值时 owner_yield 为 None（不伪造）
    d = _run(_mk(dividends=30.0))
    check("未传 --market-cap-million 时 owner_yield 为 None", d["owner_yield"] is None)

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

# 决策日志 key_variables 结构化五字段完整性（复盘闭环的前提）
# 纪律（SKILL.md 分析闭环机制）：结构化 key_variables 必须含
# name/call/proxy/falsify + check_by（截止日）+ data_source（取数路径）。
# 没有截止日的命题永远处于未决态、永远不进复盘分母——判断永远不被计分。
_KV_REQUIRED = {"name", "call", "proxy", "falsify", "check_by", "data_source"}
_kv_bad = []
for _dl in glob.glob(os.path.join(_root, "cases", "*", "decision-log.json")):
    _case = os.path.basename(os.path.dirname(_dl))
    _doc = json.load(open(_dl, encoding="utf-8"))
    _entries = _doc if isinstance(_doc, list) else [_doc]
    for _entry in _entries:
        if not isinstance(_entry, dict):
            continue  # 旧日志的非结构化条目豁免（不追溯改写）
        for _v in _entry.get("key_variables") or []:
            if isinstance(_v, dict):  # 旧日志的纯字符串条目豁免（不追溯改写）
                _miss = _KV_REQUIRED - set(_v)
                if _miss:
                    _kv_bad.append(f"{_case}:{_v.get('name', '?')} 缺 {sorted(_miss)}")
                elif not re.match(r"^\d{4}-\d{2}-\d{2}$", str(_v["check_by"])):
                    _kv_bad.append(f"{_case}:{_v['name']} check_by 非 YYYY-MM-DD")
check("结构化 key_variables 五字段齐全（截止日+取数路径强制）",
      not _kv_bad, "; ".join(_kv_bad)[:300])
_cmb_dl = os.path.join(_root, "cases", "cmb", "decision-log.json")
if os.path.exists(_cmb_dl):
    _kv = json.load(open(_cmb_dl, encoding="utf-8"))[0]["key_variables"]
    check("招行三个关键变量已回填 check_by + data_source",
          all(isinstance(v, dict) and v.get("check_by") and v.get("data_source")
              for v in _kv), str(_kv)[:200])

print("== 10. 三情景门禁 check_scenarios（v2.15）==")
import check_scenarios as cs  # noqa: E402


def _scen(**over):
    """微博原始三情景（悲观=基准调低增速）作为负向基线，可局部覆盖成合规版。"""
    d = {
        "company": "T", "ticker": "T", "currency": "USD",
        "price": 6.99, "moat": "narrow", "discount_rate": 0.10, "hold_years": 5,
        "dividend_yield": 0.087, "intrinsic_value_growth": 0.0,
        "default_probabilities": {"悲观": 0.3, "基准": 0.5, "乐观": 0.2},
        "scenarios": [
            {"name": "悲观", "value_per_share": 15.11, "probability": 0.3,
             "method": "dcf_owner_earnings", "method_note": "g=-5%",
             "stressed_assumptions": ["growth", "terminal_growth"],
             "non_operating_addback_per_share": 8.29},
            {"name": "基准", "value_per_share": 18.07, "probability": 0.5,
             "method": "dcf_owner_earnings", "non_operating_addback_per_share": 8.29},
            {"name": "乐观", "value_per_share": 20.93, "probability": 0.2,
             "method": "dcf_owner_earnings", "non_operating_addback_per_share": 8.29},
        ],
    }
    d.update(over)
    return d


def _run_cs(d, metrics=None):
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "s.json")
        json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        mp = None
        if metrics is not None:
            mp = os.path.join(td, "m.json")
            json.dump(metrics, open(mp, "w", encoding="utf-8"), ensure_ascii=False)
        return cs.check(fp, mp)


_, errs, warns, info = _run_cs(_scen())
_txt = " ".join(errs)
check("S2 悲观走 DCF 被拒（与基准同源）", any(e.startswith("S2 悲观情景方法") for e in errs), _txt[:120])
check("S3 只压增速被拒", any("只压了增速类假设" in e for e in errs))
check("S4 离散度哨兵报出比值", info["bear_base_ratio"] is not None)
check("S5 非经营资产共用折价被拒", any(e.startswith("S5") for e in errs))
check("S6 悲观值>现价须显式承认", any(e.startswith("S6") for e in errs))
check("悲观/现价 = 2.16（微博原始口径复现）",
      abs(info["bear_vs_price"] - 2.16) < 0.01, str(info["bear_vs_price"]))

# 合规版：独立方法 + 多项压力 + 分层折价 + 概率挂证据
GOOD = _scen(scenarios=[
    {"name": "悲观", "value_per_share": 6.20, "probability": 0.45,
     "method": "liquidation", "method_note": "净现金+投资3折+主业最差年 [E:fin.json]",
     "method_inputs": {"shares_m": 100, "components": [
         {"item": "净现金", "amount_m": 500, "haircut": 1.0},
         {"item": "投资组合", "amount_m": 400, "haircut": 0.3}]},
     "stressed_assumptions": ["base_oe", "non_operating_discount", "margin"],
     "non_operating_addback_per_share": 2.30,
     "probability_evidence": "[E:phase3.md] DAU 连续下滑"},
    {"name": "基准", "value_per_share": 18.07, "probability": 0.40,
     "method": "dcf_owner_earnings", "non_operating_addback_per_share": 8.29,
     "probability_evidence": "[E:phase3.md] 广告企稳"},
    {"name": "乐观", "value_per_share": 20.93, "probability": 0.15,
     "method": "dcf_owner_earnings", "non_operating_addback_per_share": 8.29,
     "probability_evidence": "[E:phase3.md] 分流见顶"},
])
_, errs, _, info = _run_cs(GOOD)
check("合规三情景通过", not errs, " ".join(errs)[:200])
check("合规版悲观/基准 ≤0.85", info["bear_base_ratio"] <= cs.DISPERSION_MAX)

# S2b 悲观值算术重算：标签必须兑现为算术（堵「标签写 liquidation、数字随手填」）
check("S2b 合规版重算值与登记值一致",
      abs(info.get("bear_recomputed_value", 0) - 6.20) < 1e-6, str(info)[:150])
_forged = json.loads(json.dumps(GOOD))
_forged["scenarios"][0].pop("method_inputs")
_forged["scenarios"][0]["value_per_share"] = 14.91  # 旧 DCF 15.11 随手减 0.2
_, errs, _, _ = _run_cs(_forged)
check("S2b 无 method_inputs 的 liquidation 标签被拒（实测绕过路径复现）",
      any(e.startswith("S2b") and "method_inputs" in e for e in errs), " ".join(errs)[:150])
_forged2 = json.loads(json.dumps(GOOD))
_forged2["scenarios"][0]["value_per_share"] = 14.91  # 有 inputs 但数字与算术不符
_, errs, _, info = _run_cs(_forged2)
check("S2b 登记值偏离重算值 >2% 被拒（数字不是算出来的）",
      any(e.startswith("S2b") and "偏差" in e for e in errs), " ".join(errs)[:150])
_incomp = json.loads(json.dumps(GOOD))
_incomp["scenarios"][0]["method_inputs"] = {"shares_m": 100}
_, errs, _, _ = _run_cs(_incomp)
check("S2b inputs 不完整无法重算被拒",
      any(e.startswith("S2b") and "不完整" in e for e in errs), " ".join(errs)[:150])
_pb = json.loads(json.dumps(GOOD))
_pb["scenarios"][0].update({"method": "pb_trough", "method_inputs":
                            {"trough_pb": 0.5, "bvps": 12.4}})
_, errs, _, info = _run_cs(_pb)
check("S2b pb_trough 重算通过（0.5×12.4=6.20）",
      not any(e.startswith("S2b") for e in errs)
      and abs(info["bear_recomputed_value"] - 6.20) < 1e-6, " ".join(errs)[:150])
_wm = json.loads(json.dumps(GOOD))
_wm["scenarios"][0].update({"method": "worst_year_margin", "method_inputs":
                            {"worst_margin": 0.10, "revenue_m": 1240,
                             "crisis_multiple": 5, "shares_m": 100}})
_, errs, _, info = _run_cs(_wm)
check("S2b worst_year_margin 重算通过（0.10×1240×5/100=6.20）",
      not any(e.startswith("S2b") for e in errs)
      and abs(info["bear_recomputed_value"] - 6.20) < 1e-6, " ".join(errs)[:150])
_bad_h = json.loads(json.dumps(GOOD))
_bad_h["scenarios"][0]["method_inputs"]["components"][1]["haircut"] = 1.55
_, errs, _, _ = _run_cs(_bad_h)
check("S2b 折价率越界（>1）被拒",
      any(e.startswith("S2b") and "越界" in e for e in errs), " ".join(errs)[:150])

# S7 概率偏离默认但未挂证据 → 拒
_bad_p = json.loads(json.dumps(GOOD))
for s in _bad_p["scenarios"]:
    s.pop("probability_evidence", None)
_, errs, _, _ = _run_cs(_bad_p)
check("S7 概率偏离默认且无 [E:] 指针被拒", any(e.startswith("S7") for e in errs))
# 概率等于默认值时不要求证据（负向：不得误报）
_dflt = json.loads(json.dumps(GOOD))
for s, p in zip(_dflt["scenarios"], (0.3, 0.5, 0.2)):
    s["probability"] = p
    s.pop("probability_evidence", None)
_, errs, _, _ = _run_cs(_dflt)
check("概率等于默认值时不误报 S7", not any(e.startswith("S7") for e in errs), " ".join(errs)[:150])

# S7b iv-growth 证据指针：非零必须挂 [E:]，与 S7 同等强制
_ivg = json.loads(json.dumps(GOOD))
_ivg["intrinsic_value_growth"] = 0.05
_, errs, _, _ = _run_cs(_ivg)
check("S7b iv-growth 非零且无 [E:] 指针被拒",
      any(e.startswith("S7b") and "iv_growth_evidence" in e for e in errs), " ".join(errs)[:150])
_ivg["iv_growth_evidence"] = "过去 5 年 OE 复合增速 8%，保守取 5% [E:fin.json]"
_, errs, _, info = _run_cs(_ivg)
check("S7b 挂上 [E:] 指针后通过", not any(e.startswith("S7b") for e in errs), " ".join(errs)[:150])
_ivg["intrinsic_value_growth"] = 5.0
_, errs, _, _ = _run_cs(_ivg)
check("S7b 量纲哨兵：5.0 判为百分数误填被拒",
      any(e.startswith("S7b") and "20%" in e for e in errs), " ".join(errs)[:150])
_, errs, _, _ = _run_cs(GOOD)
check("S7b iv-growth 为 0 时不要求证据", not any(e.startswith("S7b") for e in errs))
_no_ivg = json.loads(json.dumps(GOOD))
del _no_ivg["intrinsic_value_growth"]
_, errs, warns, _ = _run_cs(_no_ivg)
check("S7b 缺 iv-growth 字段仅告警不报错",
      not any(e.startswith("S7b") for e in errs)
      and any("intrinsic_value_growth" in w for w in warns), str(warns)[:150])

print("== 10.5 价值陷阱闸门（S8）==")
# 微博真实收入形态：2021 见顶后回撤 22%，末年微涨 0.14% —— 严格连续口径会被翘尾破坏
WB_REV = [334.2, 477.9, 655.8, 1150.1, 1718.5, 1766.9, 1689.9, 2257.1,
          1836.3, 1759.8, 1754.7, 1757.2]
WB_METRICS = {"chart_series": {"years": list(range(2014, 2026)), "revenue": WB_REV}}
check("严格连续下滑口径被末年翘尾破坏（0 年）",
      cs.revenue_decline_streak(WB_METRICS) == 0)
_stag = cs.revenue_peak_stagnation(WB_METRICS)
check("峰值回撤停滞口径仍能识别萎缩", _stag["stagnating"] is True, str(_stag))
check("峰值年与回撤幅度正确", _stag["peak_year"] == 2021
      and abs(_stag["drawdown_from_peak"] - 0.2214) < 0.01, str(_stag))
_, errs, _, info = _run_cs(GOOD, WB_METRICS)
check("价值陷阱闸门触发（MoS 61% + 峰值回撤停滞）", info["value_trap_triggered"] is True)
check("缺催化剂被拒", any("缺 `value_trap.catalyst`" in e for e in errs))
check("缺时间上限被拒", any("catalyst_deadline" in e for e in errs))
check("verdict_cap 非法被拒", any("verdict_cap" in e for e in errs))
_trap_ok = json.loads(json.dumps(GOOD))
_trap_ok["value_trap"] = {"catalyst": "特别分红 [E:phase3.md]",
                          "catalyst_deadline": "2028-12-31",
                          "verdict_cap": "小仓位试探"}
_, errs, _, _ = _run_cs(_trap_ok, WB_METRICS)
check("补齐催化剂+时间上限+降档后通过", not errs, " ".join(errs)[:200])
_trap_core = json.loads(json.dumps(_trap_ok))
_trap_core["value_trap"]["verdict_cap"] = "核心买入"
_, errs, _, _ = _run_cs(_trap_core, WB_METRICS)
check("价值陷阱标的不得进核心买入", any("verdict_cap" in e for e in errs))
# 负向：健康标的（收入持续增长）不得触发价值陷阱
HEALTHY = {"chart_series": {"years": list(range(2016, 2027)),
                            "revenue": [100 * 1.15 ** i for i in range(11)]}}
_, _, _, info = _run_cs(GOOD, HEALTHY)
check("收入持续增长的标的不触发价值陷阱", info["value_trap_triggered"] is False)

print("== 10.55 股息率量纲哨兵（S9，闸门二不收敛下限的输入防护）==")
# 归档实测：同名字段 dividend_yield_ttm 单位不统一——招行 0.0511（小数）、
# 腾讯 1.17 / 伊利 5.14（百分数）、英伟达 _pct 后缀但值 0.13（=0.13%）。
# 「不收敛下限 = 股息率 + 内在价值增速」把股息率当加数，错 100× 会让闸门自动过闸。
check("_pct 后缀按百分数解释",
      abs(cs.normalize_yield("dividend_yield_ttm_pct", 0.13)[0] - 0.0013) < 1e-9)
check("_frac 后缀按小数解释",
      cs.normalize_yield("dividend_yield_ttm_frac", 0.0514)[0] == 0.0514)
check("裸字段小数值原样", cs.normalize_yield("dividend_yield_ttm", 0.0511)[0] == 0.0511)
_v, _b, _amb = cs.normalize_yield("dividend_yield_ttm", 5.14)
check("裸字段 >0.20 判为百分数误填并标记歧义",
      abs(_v - 0.0514) < 1e-9 and _amb is True, f"{_v} {_amb}")
check("带后缀字段不标歧义", cs.normalize_yield("dividend_yield_ttm_pct", 13.0)[2] is False)
# 快照取值优先带后缀字段（腾讯真实形态：裸字段 1.17 + _frac 0.0117）
_sv, _sk, _sb, _sa = cs.snapshot_dividend_yield(
    {"dividend_yield_ttm": 1.17, "dividend_yield_ttm_frac": 0.0117})
check("快照优先取 _frac 字段消歧", _sk == "dividend_yield_ttm_frac"
      and abs(_sv - 0.0117) < 1e-9 and _sa is False, f"{_sk} {_sv}")
_, errs, _, _ = _run_cs(_scen(dividend_yield=5.14, scenarios=GOOD["scenarios"]))
check("S9 股息率 5.14 被拒（百分数误填成小数）",
      any(e.startswith("S9") and "20%" in e for e in errs), " ".join(errs)[:150])


def _run_cs_snap(d, snap):
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "s.json"); sp = os.path.join(td, "snap.json")
        json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(snap, open(sp, "w", encoding="utf-8"), ensure_ascii=False)
        return cs.check(fp, None, sp)


_, errs, _, info = _run_cs_snap(GOOD, {"dividend_yield_ttm_frac": 0.087})
check("S9 与快照一致时通过", not any(e.startswith("S9") for e in errs), " ".join(errs)[:150])
_, errs, _, _ = _run_cs_snap(GOOD, {"dividend_yield_ttm_frac": 0.012})
check("S9 与快照不一致被拒", any(e.startswith("S9") and "不一致" in e for e in errs))
_, _, warns, info = _run_cs_snap(GOOD, {"dividend_yield_ttm": 8.70})
check("S9 快照裸字段歧义发出告警",
      any("单位有歧义" in w for w in warns)
      and info["snapshot_dividend_yield"]["unit_ambiguous"] is True, str(warns)[:150])

print("== 10.6 闸门二换维度（reverse_dcf gate2）==")
import reverse_dcf as rd  # noqa: E402
_mos_w, _h_w = rd.moat_irr_hurdle("wide", 0.10, 5)
_mos_n, _h_n = rd.moat_irr_hurdle("narrow", 0.10, 5)
check("宽护城河门槛由 25% 安全边际反推 ≈16.5%", abs(_h_w - 0.1650) < 0.002, f"{_h_w:.4f}")
check("窄护城河门槛由 40% 安全边际反推 ≈21.8%", abs(_h_n - 0.2183) < 0.002, f"{_h_n:.4f}")
check("无护城河不给门槛（不给买入结论）", rd.moat_irr_hurdle("none", 0.10, 5) == (None, None))
_sc = [{"name": "悲观", "value_per_share": 6.20, "probability": 0.45},
       {"name": "基准", "value_per_share": 18.07, "probability": 0.40},
       {"name": "乐观", "value_per_share": 20.93, "probability": 0.15}]
_r = rd.expected_return(6.99, _sc, 5, 0.13, 0.087, 0.10, moat="narrow", iv_growth=0.0)
_g = _r["gate2"]
check("gate2 五项齐备（四参与 + 一诊断）",
      set(_g) >= {"consistency_expected_irr", "expected_irr_floor", "no_convergence_floor",
                  "pessimistic_irr", "loss_probability", "pass",
                  "independent_checks", "participating_checks", "diagnostic_only"})
check("不收敛下限 = 股息率 + 内在价值增速",
      abs(_g["no_convergence_floor"]["value"] - 0.087) < 1e-9)
check("独立项 = 下限 + 悲观年化 + 亏损概率（REQ-P0-04）",
      _g["independent_checks"] == ["no_convergence_floor", "pessimistic_irr", "loss_probability"])
check("参与判定项 = ①' IRR≥r + 三独立项，护城河反推门槛只诊断",
      _g["participating_checks"] == ["expected_irr_floor", "no_convergence_floor",
                                     "pessimistic_irr", "loss_probability"]
      and _g["diagnostic_only"] == ["consistency_expected_irr"])
check("①' 期望 IRR 下限门槛 = 折现率 r",
      abs(_g["expected_irr_floor"]["hurdle"] - 0.10) < 1e-9)
check("④ 亏损概率门槛默认 30%", abs(_g["loss_probability"]["hurdle"] - 0.30) < 1e-9)
check("期望 IRR 项被标注为自洽性校验而非独立证据",
      "自洽性校验" in _g["consistency_expected_irr"]["basis"])
check("effective_hurdle 标注 diagnostic_only（不构成当前门槛）",
      (_g.get("effective_hurdle") is None)
      or _g["effective_hurdle"].get("status") == "diagnostic_only")
# 护城河反推门槛不达标但 ①'②③④ 全过 → 新口径放行（神华 2015 形态：IRR 17.8% < 21.8% 但 > r）
_sc_sh = [{"name": "悲观", "value_per_share": 9.0, "probability": 0.30},
          {"name": "基准", "value_per_share": 14.0, "probability": 0.50},
          {"name": "乐观", "value_per_share": 18.0, "probability": 0.20}]
_r_sh = rd.expected_return(8.0, _sc_sh, 5, 0.13, 0.07, 0.10, moat="narrow", iv_growth=0.0)
check("护城河反推门槛不达标不再单独否决闸门二（诊断项）",
      _r_sh["gate2"]["consistency_expected_irr"]["pass"] is False
      and _r_sh["gate2"]["expected_irr_floor"]["pass"] is True
      and _r_sh["gate2"]["pass"] is True
      and "GATE2_1_IRR_FAIL" in _r_sh["gate2"]["codes"],
      str({k: _r_sh["gate2"][k] for k in ("pass", "codes")}))
# 期望 IRR < r（买在价值之上）→ ①' 拦住，即使 ②③ 过
_sc_hi = [{"name": "悲观", "value_per_share": 9.5, "probability": 0.30},
          {"name": "基准", "value_per_share": 10.0, "probability": 0.50},
          {"name": "乐观", "value_per_share": 10.5, "probability": 0.20}]
_r_hi = rd.expected_return(10.5, _sc_hi, 5, 0.13, 0.08, 0.10, moat="wide", iv_growth=0.0)
check("期望 IRR < 折现率 → ①' 不过 → 闸门二不过（GATE2_1B_IRR_BELOW_R）",
      _r_hi["gate2"]["expected_irr_floor"]["pass"] is False
      and _r_hi["gate2"]["pass"] is False
      and "GATE2_1B_IRR_BELOW_R" in _r_hi["gate2"]["codes"])
# 亏损概率 > 30% → ④ 拦住
_sc_lp = [{"name": "悲观", "value_per_share": 3.0, "probability": 0.45},
          {"name": "基准", "value_per_share": 18.0, "probability": 0.40},
          {"name": "乐观", "value_per_share": 25.0, "probability": 0.15}]
_r_lp = rd.expected_return(6.99, _sc_lp, 5, 0.13, 0.087, 0.10, moat="narrow", iv_growth=0.0)
check("亏损概率 45% > 30% → ④ 不过（GATE2_4_LOSS_PROB_FAIL）",
      _r_lp["loss_probability"] > 0.30
      and _r_lp["gate2"]["loss_probability"]["pass"] is False
      and _r_lp["gate2"]["pass"] is False
      and "GATE2_4_LOSS_PROB_FAIL" in _r_lp["gate2"]["codes"],
      f"lossP={_r_lp['loss_probability']}")
check("亏损概率门槛可调（--loss-prob-hurdle 0.5 放行 45%）",
      rd.expected_return(6.99, _sc_lp, 5, 0.13, 0.087, 0.10, moat="narrow", iv_growth=0.0,
                         loss_prob_hurdle=0.50)["gate2"]["loss_probability"]["pass"] is True)
import alert_codes as _ac106  # noqa: E402
check("新告警码已注册", not _ac106.unknown_codes(["GATE2_1B_IRR_BELOW_R", "GATE2_4_LOSS_PROB_FAIL"]))
# A/B 回归：任何 should_fail 案例在新口径下 pass = REQ-P0-04 回退条件
_ab = subprocess.run([sys.executable, os.path.join(SCRIPTS, "gate2_ab.py"), "--assert"],
                     capture_output=True, text=True)
check("gate2_ab --assert：新口径未放行任何 should_fail 案例（回退条件未触发）",
      _ab.returncode == 0, (_ab.stdout[-400:] + _ab.stderr[-200:]))
# 缺 iv_growth → 不可评，绝不能当作通过（静默通过是最危险的形态）
_r2 = rd.expected_return(6.99, _sc, 5, 0.13, 0.087, 0.10, moat="narrow")
check("缺 --iv-growth 时闸门二不可评（pass=None，不得视为通过）",
      _r2["gate2"]["pass"] is None and "no_convergence_floor" in _r2["gate2"]["missing_inputs"])
# 不收敛下限不达标必须拦住（价值陷阱的定量特征）
_r3 = rd.expected_return(6.99, _sc, 5, 0.13, 0.0, 0.10, moat="narrow", iv_growth=0.0)
check("零股息+零增长 → 不收敛下限 0% 不达标 → 闸门二不过",
      _r3["gate2"]["no_convergence_floor"]["pass"] is False and _r3["gate2"]["pass"] is False)
check("此时期望 IRR 仍可能达标（证明两项确实不同维度）",
      _r3["gate2"]["consistency_expected_irr"]["pass"] is True
      and _r3["gate2"]["expected_irr_floor"]["pass"] is True)
_r4 = rd.expected_return(6.99, _sc, 5, 0.13, 0.087, 0.10, moat="none", iv_growth=0.05)
check("无护城河直接不过闸门二", _r4["gate2"]["pass"] is False)
check("旧字段 beats_index 仍在（向后兼容）", "beats_index" in _r)

print("== 10.7 核验强度标签 + 首屏门禁 ==")
import verification_strength as vst  # noqa: E402
_fin_full = {"company": "T", "annual": [
    {"year": y, "total_assets": 100, "total_liabilities": 60, "total_equity": 40}
    for y in range(2015, 2026)],
    "crosscheck": [{"year": y, "source": f"{y}年报 PDF p.45（巨潮）"} for y in (2023, 2024, 2025)]}
_v = vst.assess(_fin_full)
check("三项达标 → A 级", _v["grade"] == "A", str(_v["grade"]))
check("勾稽覆盖率 100%", abs(_v["reconciliation"]["coverage"] - 1.0) < 1e-9)
check("原文比对等级 full", _v["crosscheck"]["level"] == "full")
check("始终声明未经原文机器逐字比对",
      _v["crosscheck"]["machine_verified_against_source_text"] is False)
_fin_gap = json.loads(json.dumps(_fin_full))
for r_ in _fin_gap["annual"][:9]:
    r_["total_assets"] = None
_v2 = vst.assess(_fin_gap)
check("勾稽覆盖率 18% 场景被判 C（0 错误不再掩盖没检查）",
      _v2["grade"] == "C" and _v2["reconciliation"]["level"] == "weak",
      f"{_v2['grade']} {_v2['reconciliation']['coverage']:.2f}")
_fin_bank = {"company": "B", "company_type": "银行", "annual": [
    {"year": y, "total_assets": 100, "total_equity": 8, "gross_loans": 60,
     "npl_balance": 1, "provision_balance": 2} for y in range(2016, 2026)],
    "crosscheck": [{"year": y, "source": f"{y}年报（巨潮）"} for y in (2023, 2024, 2025)]}
_v3 = vst.assess(_fin_bank)
check("银行专属 schema 不因缺 total_liabilities 被误判",
      _v3["reconciliation"]["coverage"] == 1.0, str(_v3["reconciliation"]))
check("银行自动按 15 年窗口要求 → 10 年判 partial",
      _v3["data_window"]["required_years"] == 15
      and _v3["data_window"]["level"] == "partial", str(_v3["data_window"]))
_fin_nostress = {"company": "T", "annual": [
    {"year": y, "total_assets": 100, "total_liabilities": 60, "total_equity": 40}
    for y in range(2023, 2027)],
    "crosscheck": [{"year": y, "source": f"{y}年报（巨潮）"} for y in (2024, 2025, 2026)]}
_v4 = vst.assess(_fin_nostress)
check("窗口未覆盖任何系统性压力年 → weak",
      _v4["data_window"]["level"] == "weak"
      and not _v4["data_window"]["stress_years_covered"], str(_v4["data_window"]))
_badge = vst.badge_html(_v)
check("徽章含机器可比对属性", 'data-verification-strength="1"' in _badge
      and 'data-grade="A"' in _badge)
check("徽章含未经原文比对声明", "未经原文机器逐字比对" in _badge)

with tempfile.TemporaryDirectory() as td:
    ddir = os.path.join(td, "data"); os.makedirs(ddir)
    json.dump({"kpi": 0.123}, open(os.path.join(ddir, "m.json"), "w"))
    json.dump(_v, open(os.path.join(ddir, "verification_strength.json"), "w"),
              ensure_ascii=False)
    _vn = '<span class="vnum" data-src="m.json" data-path="kpi" data-fmt="pct1">12.3%</span>'
    _hdr = '<header class="report-header"><div class="verdict-banner">x</div></header>'

    def _vr(name, content):
        fp = os.path.join(td, name + ".html")
        open(fp, "w", encoding="utf-8").write(content)
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "verify_report.py"),
                               fp, "--data-dir", ddir], capture_output=True, text=True)

    r = _vr("nobadge", _hdr + _vn)
    check("完整报告缺核验强度徽章被拒",
          r.returncode == 1 and "core-verification" in r.stdout, r.stdout[-160:])
    r = _vr("ok", _hdr + _vn + _badge)
    check("徽章与底稿一致的报告通过", r.returncode == 0, r.stdout[-200:])
    r = _vr("tamper", _hdr + _vn + _badge.replace('data-grade="A"', 'data-grade="C"'))
    check("篡改徽章等级被逮住",
          r.returncode == 1 and "badge:grade" in r.stdout, r.stdout[-160:])
    r = _vr("nodecl", _hdr + _vn + _badge.replace("未经原文机器逐字比对", "已完整核验"))
    check("删掉「未经原文机器逐字比对」声明被拒",
          r.returncode == 1, r.stdout[-160:])
    r = _vr("frag", _vn)
    check("片段 HTML 不强制徽章（不误伤）", r.returncode == 0, r.stdout[-160:])

# ═══════════════════════════════════════════════════════════════════
print("== 11 告警码注册表与回放断言基础设施 ==")
# 第一批复核结论：回放协议承诺的「一键重跑全部断言」此前无实现，根因是告警
# 为自由文本、无稳定代号。本节守护新建的基础设施——它是后续一切判别逻辑改动
# 的验收前提（没有秤就不能调重量）。
sys.path.insert(0, SCRIPTS)
import alert_codes as AC

check("注册表非空", len(AC.ALERTS) > 40, str(len(AC.ALERTS)))
check("Phase 0 代号数 = 6 否决 + 21 红旗 + 4 A股 = 31",
      sum(1 for c, (layer, _) in AC.ALERTS.items()
          if layer.startswith("phase0")) == 32,  # 含 V4A 利率倒挂 + R21 持续经营
      str(sum(1 for c, (l, _) in AC.ALERTS.items() if l.startswith("phase0"))))
check("每个断言组的代号都已注册",
      all(not AC.unknown_codes(codes) for codes in AC.ASSERTIONS.values()),
      str({k: AC.unknown_codes(v) for k, v in AC.ASSERTIONS.items() if AC.unknown_codes(v)}))
check("档位序数表五档齐全", sorted(AC.VERDICT_ORDINAL.values()) == [0, 1, 2, 3, 4])

# 海控案的等价关系（复核发现 F4）：引擎输出「全期平均亏损」而非「周期高位」，
# 断言不得因此漏判——这个等价必须是显式且被测试锁定的。
check("周期高位断言可由 NORM_BASE_UNUSABLE 满足（海控 F4 等价关系）",
      AC.assertion_satisfied("CYCLE_PEAK", ["NORM_BASE_UNUSABLE"]))
check("周期高位断言也可由 NORM_CYCLE_PEAK 满足",
      AC.assertion_satisfied("CYCLE_PEAK", ["NORM_CYCLE_PEAK"]))
check("无相关代号时周期高位断言不满足",
      not AC.assertion_satisfied("CYCLE_PEAK", ["M_DILUTION"]))
# 福耀负样本：观察级/非造假形态的代号绝不能让「造假类告警」断言误触发
check("准则切换不计入造假类告警（福耀负样本零误杀）",
      not AC.assertion_satisfied("ANY_FRAUD_ALERT", ["M_ACCOUNTING_STANDARD_SWITCH"]))
check("分红幻觉不计入造假类告警",
      not AC.assertion_satisfied("ANY_FRAUD_ALERT", ["M_DIVIDEND_ILLUSION"]))
check("持续经营存疑不计入造假类告警（柯达不是造假是烧光了，REQ-P0-02）",
      not AC.assertion_satisfied("ANY_FRAUD_ALERT", ["P0_R21_GOING_CONCERN"]))
check("存贷双高计入造假类告警",
      AC.assertion_satisfied("ANY_FRAUD_ALERT", ["P0_V4_DEPOSIT_LOAN_DOUBLE_HIGH"]))

# AlertBag：未注册代号必须当场炸掉，而不是静默产生一个查不到的告警
try:
    AC.AlertBag().add("NO_SUCH_CODE", "x")
    check("AlertBag 拒绝未注册代号", False, "未抛异常")
except KeyError:
    check("AlertBag 拒绝未注册代号", True)
bag = AC.AlertBag().add("M_DILUTION", "稀释了")
check("AlertBag 同时产出文本与代号",
      bag.messages == ["稀释了"] and bag.codes == ["M_DILUTION"])
try:
    AC.assertion_satisfied("NO_SUCH_ASSERTION", [])
    check("未注册断言当场报错", False, "未抛异常")
except KeyError:
    check("未注册断言当场报错", True)

# 引擎三脚本必须真的输出代号字段（回归保护：谁把字段删了立刻暴露）
_r = cm.compute(base(mk_rows([0.10] * 11)))
check("compute_metrics 输出 alert_codes 字段", "alert_codes" in _r)
check("compute_metrics 输出 alerts_structured 字段", "alerts_structured" in _r)
check("alerts 与 alert_codes 等长",
      len(_r["alerts"]) == len(_r["alert_codes"]),
      f"{len(_r['alerts'])} vs {len(_r['alert_codes'])}")
check("所有产出代号均已注册", not AC.unknown_codes(_r["alert_codes"]),
      str(AC.unknown_codes(_r["alert_codes"])))

# ---- 豁免注册表（语境层）：治理声明在注册表、算术判据在引擎、抑制必须留痕 ----
check("豁免注册表 covers 全部已注册", not AC.validate_exemptions(),
      str(AC.validate_exemptions()))
check("豁免求值器与注册表条目同步",
      set(cm._EXEMPTION_EVALUATORS) == set(AC.EXEMPTIONS),
      f"{sorted(cm._EXEMPTION_EVALUATORS)} vs {sorted(AC.EXEMPTIONS)}")
check("EX_REINVEST_GROWTH 只覆盖烧钱告警族三码",
      set(AC.EXEMPTIONS["EX_REINVEST_GROWTH"]["covers"]) ==
      {"M_DIVIDEND_ILLUSION", "M_FCF_QUALITY", "M_OWNER_YIELD_NOT_CASH_BACKED"},
      str(AC.EXEMPTIONS["EX_REINVEST_GROWTH"]["covers"]))
check("稀释告警不在任何豁免语境（豁免面禁止外溢）",
      AC.exemption_covers("M_DILUTION") == [])


def _ex_rows(div, growth=1.15, capex_ratio=0.9, buyback=5.0):
    rows = mk_rows([0.15] * 11, growth=growth, capex_ratio=capex_ratio)
    for r in rows:
        r["dividends_paid"] = div
        r["buyback"] = buyback
    return rows


# 命中豁免：三码不进 alert_codes，exemptions 块留证据与被抑制清单
_r_ex = cm.compute(base(_ex_rows(2.0)), market_cap=10000.0)
check("再投入豁免命中：M_FCF_QUALITY 不触发",
      "M_FCF_QUALITY" not in _r_ex["alert_codes"])
check("再投入豁免命中：M_OWNER_YIELD_NOT_CASH_BACKED 不触发",
      "M_OWNER_YIELD_NOT_CASH_BACKED" not in _r_ex["alert_codes"]
      and any(s["code"] == "M_OWNER_YIELD_NOT_CASH_BACKED"
              for s in _r_ex["exemptions"]["suppressed"]))
check("豁免证据落盘（exemptions.fired 带判据字段值）",
      _r_ex["exemptions"]["fired"].get("EX_REINVEST_GROWTH", {})
      .get("ocf_all_positive") is True)
check("低分红非零公司经豁免抑制 M_DIVIDEND_ILLUSION（路径保持）",
      "M_DIVIDEND_ILLUSION" not in _r_ex["alert_codes"]
      and any(s["code"] == "M_DIVIDEND_ILLUSION" and s["by"] == "EX_REINVEST_GROWTH"
              for s in _r_ex["exemptions"]["suppressed"]))

# 存在性前置：确认零分红（cum_dividends ≤ 0，区别于缺失）→ 前提不成立，
# 走前置路径而非豁免路径
_r_zero = cm.compute(base(_ex_rows(0.0)))
check("零分红前置：M_DIVIDEND_ILLUSION 跳过",
      "M_DIVIDEND_ILLUSION" not in _r_zero["alert_codes"])
check("零分红前置留痕（前提不成立 warning）",
      any("前提不成立" in w for w in _r_zero["warnings"]))
check("零分红走前置路径而非豁免路径（不进 suppressed 清单）",
      not any(s["code"] == "M_DIVIDEND_ILLUSION"
              for s in _r_zero["exemptions"]["suppressed"]))

# 负控制：收入下滑的再投入不豁免（收缩期再投入≠主动扩张）→ 三码照常触发
_r_dec = cm.compute(base(_ex_rows(2.0, growth=0.95)))
check("收入下滑：M_DIVIDEND_ILLUSION 照常触发",
      "M_DIVIDEND_ILLUSION" in _r_dec["alert_codes"])
check("收入下滑：M_FCF_QUALITY 照常触发",
      "M_FCF_QUALITY" in _r_dec["alert_codes"])
check("收入下滑：豁免未命中", not _r_dec["exemptions"]["fired"])

# 缺失≠零：分红数据缺失且不满足豁免时不禁声（缺失不禁声纪律）
_rows_miss = mk_rows([0.15] * 11, growth=0.95, capex_ratio=0.9)
for r in _rows_miss:
    r["buyback"] = 5.0
_r_miss = cm.compute(base(_rows_miss))
check("分红缺失≠零：告警照常触发",
      "M_DIVIDEND_ILLUSION" in _r_miss["alert_codes"])

# 断言 runner 端到端：第一批六案例应为 5 全绿 + 1 已知失败（茅台），0 回归
_rr = subprocess.run([sys.executable, os.path.join(SCRIPTS, "run_backtest_assertions.py"),
                      "--batch", "1"], capture_output=True, text=True, cwd=ROOT)
check("断言 runner 可运行且无回归失败", _rr.returncode == 0,
      (_rr.stdout + _rr.stderr)[-300:])
check("断言 runner 复现第一批战绩（5 全绿 + 1 已知失败）",
      "5/6 全绿" in _rr.stdout and "1 已知失败" in _rr.stdout,
      _rr.stdout[-300:])
check("茅台档位轨失败被识别为已知失败而非回归",
      "已知失败：档位轨未命中" in _rr.stdout, _rr.stdout[-300:])

# ═══════════════════════════════════════════════════════════════════
print("== 12 量纲哨兵 / 护城河一致性 / 快照三角校验（阶段二） ==")
# 实证依据：OBS-600660-01（福耀 566294→56630、海控 2535137→253514 两例 10 倍错位）
# 与 moat-framework.md:35（11 案例仅 4 例用标准评级词）。

def _ms(case, mc):
    fin = os.path.join(ROOT, "backtest", case, "data")
    f = [x for x in os.listdir(fin) if x.startswith("financials")][0]
    o = os.path.join(tempfile.mkdtemp(prefix="ms_"), "out.json")
    subprocess.run([sys.executable, os.path.join(SCRIPTS, "compute_metrics.py"),
                    os.path.join(fin, f), "--market-cap-million", str(mc), "-o", o],
                   capture_output=True)
    return json.load(open(o, encoding="utf-8"))

_r_ok = _ms("600660.SH_2018-12-31", 56630)
check("量纲哨兵不误伤正确市值（福耀 56630 百万）",
      "M_UNIT_SUSPECT" not in _r_ok["alert_codes"])
check("owner_yield 现带 pb 字段", "pb" in (_r_ok.get("owner_yield") or {}))
_r_bad = _ms("600660.SH_2018-12-31", 566294)
check("量纲哨兵逮住 10 倍错位（福耀 566294）",
      "M_UNIT_SUSPECT" in _r_bad["alert_codes"])
_r_hk = _ms("601919.SH_2021-07-31", 2535137)
check("量纲哨兵逮住海控历史错位（2535137）",
      "M_UNIT_SUSPECT" in _r_hk["alert_codes"])

# ── F/P2-8：CLI 等式校验 + 单位归一 + provenance（关闭传参绕过快照的缝隙）──
print("== 12b CLI 等式校验 + 单位归一 + provenance（F/P2-8） ==")
with tempfile.TemporaryDirectory() as td:
    def _eq_draft(unit="million"):
        return {"company": "EQ", "ticker": "EQ", "currency": "CNY",
                "unit": unit, "company_type": "制造业",
                "annual": [{"year": 2020 + i, "revenue": 1000.0,
                            "net_income": 100.0, "ocf": 150.0, "capex": 40.0,
                            "d_and_a": 40.0, "total_equity": 800.0,
                            "total_debt": 100.0, "cash": 100.0,
                            "shares_diluted": 100.0, "dividends": 30.0}
                           for i in range(6)]}

    def _eq_cli(draft, snap=None, mc=None, old_param=False):
        p = os.path.join(td, "d.json")
        json.dump(draft, open(p, "w"), ensure_ascii=False)
        o = os.path.join(td, "o.json")
        cmd = [sys.executable, os.path.join(SCRIPTS, "compute_metrics.py"), p]
        if mc is not None:
            cmd += (["--market-cap", str(mc)] if old_param
                    else ["--market-cap-million", str(mc)])
        if snap is not None:
            sp = os.path.join(td, "s.json")
            json.dump(snap, open(sp, "w"), ensure_ascii=False)
            cmd += ["--snapshot", sp]
        cmd += ["-o", o]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return (json.load(open(o)) if os.path.exists(o) else None), r

    def _eq_warns(d):
        return [w for w in (d or {}).get("warnings", [])
                if "等式" in w or "矛盾" in w or "跳过" in w]

    # 1. 单位归一：底稿 unit=亿，传参固定百万，引擎内部 ÷100 换算
    d, r = _eq_cli(_eq_draft("yi"), mc=1000.0)
    check("亿底稿传百万：内部换算到亿（1000 百万 = 10 亿）",
          (d.get("owner_yield") or {}).get("market_cap") == 10.0,
          str((d.get("owner_yield") or {}).get("market_cap")))
    check("provenance 块记录百万原值",
          d["provenance"]["market_cap_million"] == 1000.0)

    # 2. A+H 分部等式校验：分部口径一致 → 零 EQUATION WARN
    snap_ah = {
        "price": {"value": 20.0, "currency": "CNY"},
        "price_a": 20.0, "price_h": 18.0,
        "fx": {"cny_per_hkd": 0.9},
        "shares": {"a_million": 4000.0, "h_million": 1000.0},
        "market_cap": {"value": 96200.0, "unit": "million", "currency": "CNY"},
    }  # 分部推导 = 20×4000 + 18×1000×0.9 = 96,200 百万
    d, r = _eq_cli(_eq_draft(), snap=snap_ah, mc=96200.0)
    check("A+H 分部口径传参一致：零等式 WARN", not _eq_warns(d), str(_eq_warns(d)))

    # 3. 容差内日期漂移（5%）不报警——拦 10 倍错位不拦日常波动
    d, r = _eq_cli(_eq_draft(), snap=snap_ah, mc=96200.0 * 1.05)
    check("容差内偏差（5%）：不报警", not _eq_warns(d), str(_eq_warns(d)))

    # 4. 10 倍量纲错位：矛盾 WARN（双汇「元」错位的拦截位）
    d, r = _eq_cli(_eq_draft(), snap=snap_ah, mc=962000.0)
    check("10 倍错位：传参与快照矛盾 WARN",
          any("矛盾" in w for w in _eq_warns(d)), str(_eq_warns(d)))

    # 5. A+H 缺分部拆分：跳过留痕，不禁声（缺证据不裁决）
    snap_ah_nobreak = dict(snap_ah)
    snap_ah_nobreak["shares"] = {"value": 5000.0, "unit": "million_shares"}
    d, r = _eq_cli(_eq_draft(), snap=snap_ah_nobreak, mc=100000.0)
    check("A+H 缺分部拆分：跳过留痕",
          any("跳过" in w for w in _eq_warns(d)), str(_eq_warns(d)))

    # 6. 未传市值时从快照推导（单一事实源，免手抄）
    d, r = _eq_cli(_eq_draft(), snap=snap_ah)
    check("快照推导市值进 owner_yield",
          (d.get("owner_yield") or {}).get("market_cap") == 96200.0,
          str((d.get("owner_yield") or {}).get("market_cap")))
    check("provenance 记录快照来源", d["provenance"]["snapshot"].endswith("s.json"))

    # 7. 旧参数名拒绝：allow_abbrev=False 防 --market-cap 前缀静默生效
    d, r = _eq_cli(_eq_draft(), mc=1000.0, old_param=True)
    check("旧参数名 --market-cap 被拒绝（单位进参数名）",
          r.returncode != 0 and "unrecognized" in (r.stderr or ""),
          f"rc={r.returncode}, err={(r.stderr or '')[:80]}")

    # 8. 快照缺 market_cap 且未传参：拒绝执行（存在性前置，不静默按 0 算）
    snap_nomcap = {"price": {"value": 20.0, "currency": "CNY"},
                   "shares": {"value": 5000.0, "unit": "million_shares"}}
    d, r = _eq_cli(_eq_draft(), snap=snap_nomcap)
    check("快照缺市值且未传参：拒绝执行",
          r.returncode != 0 and "无法推导市值" in (r.stderr or ""),
          f"rc={r.returncode}, err={(r.stderr or '')[:80]}")

# 护城河一致性：报告 data-moat 与底稿 scenarios.moat
_case_dir = os.path.join(ROOT, "backtest", "600519.SH_2015-08-31")
_rpt = open(os.path.join(_case_dir, "report.html"), encoding="utf-8").read()
_ddir = os.path.join(_case_dir, "data")

def _moat_run(html):
    fp = os.path.join(tempfile.mkdtemp(prefix="moat_"), "case.html")
    open(fp, "w", encoding="utf-8").write(html)
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "verify_report.py"),
                           fp, "--data-dir", _ddir], capture_output=True, text=True)

_r = _moat_run(_rpt)
check("data-moat 与底稿一致的报告通过", "moat" not in _r.stdout or _r.returncode == 0,
      _r.stdout[-200:])
_r = _moat_run(_rpt.replace('data-moat="wide"', 'data-moat="narrow"'))
check("篡改 data-moat 为 narrow 被逮住",
      _r.returncode == 1 and "moat:rating" in _r.stdout, _r.stdout[-200:])
_r = _moat_run(_rpt.replace('data-moat="wide"', 'data-moat="极强"'))
check("自造词（极强）被逮住",
      _r.returncode == 1 and "moat:rating" in _r.stdout, _r.stdout[-200:])
_r = _moat_run(_rpt.replace(' data-moat="wide"', ""))
check("完整报告缺 data-moat 标记被逮住",
      _r.returncode == 1 and "moat:rating" in _r.stdout, _r.stdout[-200:])
_r = _moat_run("<html><body><p>片段</p></body></html>")
check("片段 HTML 不强制护城河标记（不误伤）",
      _r.returncode == 0 or "moat" not in _r.stdout, _r.stdout[-120:])

# 快照三角校验
_ms_ok = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_market_snapshot.py"),
    os.path.join(ROOT, "backtest", "600519.SH_2015-08-31", "data",
                 "market_snapshot_MAOTAI_2015H1.json")], capture_output=True, text=True)
check("茅台快照三角校验通过", _ms_ok.returncode == 0, _ms_ok.stdout[-200:])
_ms_ah = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_market_snapshot.py"),
    os.path.join(ROOT, "backtest", "601919.SH_2021-07-31", "data",
                 "market_snapshot_601919_2021.json")], capture_output=True, text=True)
check("海控 A/H 分计价快照修正后通过（不误伤双重上市）",
      _ms_ah.returncode == 0, (_ms_ah.stdout + _ms_ah.stderr)[-300:])
# 负向：把海控快照临时改回错位值应 FAIL
_hk_fp = os.path.join(ROOT, "backtest", "601919.SH_2021-07-31", "data",
                      "market_snapshot_601919_2021.json")
_hk_orig = open(_hk_fp, encoding="utf-8").read()
try:
    open(_hk_fp, "w", encoding="utf-8").write(
        _hk_orig.replace('"value_cny_million": 253514', '"value_cny_million": 2535137'))
    _ms_bad = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_market_snapshot.py"),
                              _hk_fp], capture_output=True, text=True)
    check("10 倍单位错位被三角校验逮住（海控 2535137）",
          _ms_bad.returncode == 1 and "SNAPSHOT_TRIANGLE" in _ms_bad.stdout,
          _ms_bad.stdout[-200:])
finally:
    open(_hk_fp, "w", encoding="utf-8").write(_hk_orig)

# ═══════════════════════════════════════════════════════════════════
print("== 13 触发价可达性 + 重评承接（阶段三） ==")
# 实证：茅台 166.93 五年未触及（OBS-2015-08-01）、福耀触发价真实触发无人接手
# （OBS-600660-04）。两例同根源：触发器不是一等公民。

def _trig(trigger, snap=None, **kw):
    cmd = [sys.executable, os.path.join(SCRIPTS, "trigger_reachability.py"),
           "--trigger", str(trigger)]
    if snap:
        cmd += ["--snapshot", snap]
    for k, v in kw.items():
        cmd += [f"--{k}", str(v)]
    return subprocess.run(cmd, capture_output=True, text=True)

_mt_snap = os.path.join(ROOT, "backtest", "600519.SH_2015-08-31", "data",
                        "market_snapshot_MAOTAI_2015H1.json")
_r = _trig(166.93, snap=_mt_snap)
check("茅台触发价 166.93 判为带内可达", _r.returncode == 0, _r.stdout[-200:])
check("茅台带内位置约 14.8%", "14.8%" in _r.stdout, _r.stdout[-200:])
check("贴近下沿时提示自查更长窗口", "52 周窗口无法识别" in _r.stdout,
      _r.stdout[-200:])
_r = _trig(100, snap=_mt_snap)
check("触发价低于 52 周最低判为历史区间之外",
      _r.returncode == 1 and "out_of_history" in _r.stdout, _r.stdout[-200:])
_r = _trig(146.5, snap=_mt_snap)  # (146.5-145.5)/144.5 = 0.7% < 10%
check("带内底部 10% 以内判为可达性低",
      _r.returncode == 1 and "low_reachability" in _r.stdout, _r.stdout[-200:])
_r = _trig(166.93, low52w=145.5, high52w=290.0, price=195.37)
check("无快照时 CLI 参数可用", _r.returncode == 0, _r.stdout[-200:])
_r = _trig(166.93)
check("缺 52 周区间时如实拒绝判定（退出码 2）", _r.returncode == 2,
      _r.stdout[-200:])

# 承接门禁：观察等价格必须有 data-reeval-trigger
# 注意：迷你 HTML 必须带至少一个合法 vnum 标签——verify_report 对「完整报告
# 无任何 vnum」会提前退出，触发器检查就执行不到了（提前退出本身是合理的，
# 无数字报告先死在溯源这一关）。
_VNUM = ('<span class="vnum" data-src="market_snapshot_MAOTAI_2015H1.json" '
         'data-path="price_cny" data-fmt="num2">195.37</span>')
_watch_html = ('<html><body><div class="report-header">x</div>'
               f'<p>结论档位：<b style="font-size:20px">观察等价格</b></p>'
               f'<p>现价 {_VNUM} 元。</p>'
               '<span data-moat="wide"></span></body></html>')
def _vr3(html):
    fp = os.path.join(tempfile.mkdtemp(prefix="trig_"), "r.html")
    open(fp, "w", encoding="utf-8").write(html)
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "verify_report.py"),
                           fp, "--data-dir", _ddir], capture_output=True, text=True)
_r = _vr3(_watch_html)
check("观察等价格缺承接标记被逮住",
      _r.returncode == 1 and "trigger:reeval" in _r.stdout, _r.stdout[-200:])
_r = _vr3(_watch_html.replace("结论档位",
                              '<span data-reeval-trigger="watchlist_v1"></span>结论档位'))
check("带承接标记后该门禁通过", "trigger:reeval" not in _r.stdout, _r.stdout[-200:])
# 拒绝档位提及「观察等价格」不得误伤（柯达案实证）
_reject_html = ('<html><body><div class="report-header">x</div>'
                f'<p>结论档位：<b style="font-size:20px">拒绝</b></p>'
                f'<p>现价 {_VNUM} 元。</p>'
                '<p>最终档位：拒绝（闸门一不过 → 最高「观察等价格」）</p>'
                '<span data-moat="none"></span></body></html>')
_r = _vr3(_reject_html)
check("拒绝档位提及观察等价格不误伤（柯达形态）",
      "trigger:reeval" not in _r.stdout, _r.stdout[-200:])
# 触发价带内位置与快照一致性机器校验
_r = _vr3(_watch_html.replace("结论档位",
    '<span data-reeval-trigger="w1" data-trigger-price="166.93" '
    'data-trigger-band-pct="14.8"></span>结论档位'))
check("触发价带内位置与快照一致时通过", "trigger:band" not in _r.stdout,
      _r.stdout[-200:])
_r = _vr3(_watch_html.replace("结论档位",
    '<span data-reeval-trigger="w1" data-trigger-price="166.93" '
    'data-trigger-band-pct="80"></span>结论档位'))
check("触发价带内位置谎报被逮住（80% vs 实际 14.8%）",
      _r.returncode == 1 and "trigger:band" in _r.stdout, _r.stdout[-200:])
_r = _vr3(_watch_html.replace("结论档位",
    '<span data-reeval-trigger="w1" data-trigger-price="166.93"></span>结论档位'))
check("声明触发价但缺带内分位被逮住",
      _r.returncode == 1 and "trigger:band" in _r.stdout, _r.stdout[-200:])

# ═══════════════════════════════════════════════════════════════════
print("== 14 S2c 最差年必须是实证压力年（阶段四） ==")
# 两案例硬证据：茅台悲观取 2006 年 31.5%（序列最早年、公司幼年期，而真实政策
# 冲击期 2013-14 净利率仅从 50.3% 降到 47.6%）；苹果取 FY2007 的 14.6%
#（iPhone 刚发布、仍是 Mac+iPod 公司，实际压力年 FY2013 是 21.7%）。
# 判据：worst_year 是序列最早年 **且** 利润率秩相关 > 0.5 ⇒ 该年低是规模/
# 阶段效应，不是危机。这是本轮唯一的判别逻辑改动，故双向锁定。
import check_scenarios as CS

_mt_scen = os.path.join(ROOT, "backtest", "600519.SH_2015-08-31", "data",
                        "scenarios_MAOTAI_2015H1.json")
_mt_met = os.path.join(ROOT, "backtest", "600519.SH_2015-08-31", "data",
                       "metrics_MAOTAI_2015H1.json")

def _s2c(mi_over, scen=_mt_scen, met=_mt_met):
    d = json.load(open(scen, encoding="utf-8"))
    b = [s for s in d["scenarios"] if s["name"] == "悲观"][0]
    b["method_inputs"].update(mi_over)
    fp = os.path.join(tempfile.mkdtemp(prefix="s2c_"), "s.json")
    json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
    _, errs, _, _ = CS.check(fp, metrics_path=met)
    return [e for e in errs if e.startswith("S2c")]

# 现状（已修正为 2014 实证压力年）应无 S2c 报错
check("茅台修正后（2014 实证压力年）S2c 通过", not _s2c({}))
# 退回幼年期取值应被逮住
check("worst_year=2006（序列最早年+长期上行）被逮住",
      any("规模/阶段效应" in e for e in _s2c(
          {"worst_year": 2006, "worst_margin": 0.315})),
      str(_s2c({"worst_year": 2006, "worst_margin": 0.315}))[:200])
check("报错含剔除首年后的实际最差年提示",
      any("剔除首年后" in e for e in _s2c(
          {"worst_year": 2006, "worst_margin": 0.315})))
# 字段缺失
check("缺 worst_year 被逮住",
      any("缺 `method_inputs.worst_year`" in e for e in _s2c({"worst_year": None})))
check("缺压力事件证据被逮住",
      any("worst_year_stress_evidence" in e for e in
          _s2c({"worst_year_stress_evidence": "无指针的说明"})))
# worst_margin 与该年实际净利率不符
check("worst_margin 与该年实际净利率不符被逮住",
      any("与 2014 年实际净利率" in e for e in _s2c({"worst_margin": 0.20})),
      str(_s2c({"worst_margin": 0.20}))[:200])
# worst_year 不在序列内
check("worst_year 不在序列年份内被逮住",
      any("不在 metrics 净利率序列年份" in e for e in _s2c({"worst_year": 1999})))
# 苹果同根因
_ap_scen = os.path.join(ROOT, "backtest", "AAPL_2016-04-30", "data",
                        "scenarios_AAPL_2016Q2.json")
_ap_met = os.path.join(ROOT, "backtest", "AAPL_2016-04-30", "data",
                       "metrics_AAPL_2016Q2.json")
check("苹果修正后（FY2013 实证压力年）S2c 通过",
      not _s2c({}, _ap_scen, _ap_met))
check("苹果退回 FY2007 幼年期被逮住（同根因第 2 案例）",
      any("规模/阶段效应" in e for e in _s2c(
          {"worst_year": 2007, "worst_margin": 0.146}, _ap_scen, _ap_met)))

# ---- S2d pb_trough 谷底 PB 口径纪律（OBS-600660-02，与 S2c 同类缺陷）----
# 福耀案实证：初版用前复权价 ÷ 当年账面 BPS 得 2.0-2.2，按不复权价重建后真实
# 区间为 1.66-2.46——前复权价已扣除后续分红除权影响，系统性低估谷底 PB。
_fy_scen = os.path.join(ROOT, "backtest", "600660.SH_2018-12-31", "data",
                        "scenarios_600660.json")
_fy_met = os.path.join(ROOT, "backtest", "600660.SH_2018-12-31", "data",
                       "metrics_600660.json")

def _s2d(mi_over, scen=_fy_scen, met=_fy_met):
    d = json.load(open(scen, encoding="utf-8"))
    b = [s for s in d["scenarios"] if s["name"] == "悲观"][0]
    b["method_inputs"].update(mi_over)
    fp = os.path.join(tempfile.mkdtemp(prefix="s2d_"), "s.json")
    json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
    _, errs, _, _ = CS.check(fp, metrics_path=met)
    return [e for e in errs if e.startswith("S2d")]

check("福耀回填口径后 S2d 通过", not _s2d({}))
check("缺 trough_pb_evidence 被逮住",
      any("缺 `method_inputs.trough_pb_evidence`" in e
          for e in _s2d({"trough_pb_evidence": None})))
check("未声明不复权口径被逮住",
      any("未声明行情口径" in e for e in _s2d(
          {"trough_pb_evidence": "2015 年谷底 PB 1.66 [E:x.json]"})))
check("未标明期间最低点被逮住",
      any("未标明价格样本为期间最低点" in e for e in _s2d(
          {"trough_pb_evidence": "不复权价 10.58 ÷ BPS 6.4 = 1.66 [E:x.json]"})))
check("S2d 只管 pb_trough，worst_year_margin 不受影响",
      not [e for e in _s2c({}) if e.startswith("S2d")])

# 不误伤：S2c 只管 worst_year_margin，其余独立方法不受影响
for _c, _mth in (("EK_2011-06-30", "peer_death_analogy"),
                 ("600660.SH_2018-12-31", "pb_trough"),
                 ("601919.SH_2021-07-31", "pb_trough")):
    _f = sorted(glob.glob(os.path.join(ROOT, "backtest", _c, "data",
                                       "scenarios*.json")))
    _d = json.load(open(_f[0], encoding="utf-8"))
    _bm = [s for s in _d["scenarios"] if s["name"] == "悲观"][0]["method"]
    _mp = sorted(glob.glob(os.path.join(ROOT, "backtest", _c, "data",
                                        "metrics*.json")))
    _, _errs, _, _ = CS.check(_f[0], metrics_path=_mp[0] if _mp else None)
    check(f"{_c} 用 {_bm}，S2c 不适用（不误伤拒绝样本）",
          _bm == _mth and not [e for e in _errs if e.startswith("S2c")],
          str([e for e in _errs if e.startswith("S2c")])[:150])

# 有效门槛披露（纯披露，不改判定）
_eh = subprocess.run([sys.executable, os.path.join(SCRIPTS, "reverse_dcf.py"),
    "expected-return", "--scenarios-file", _mt_scen, "--moat", "wide",
    "--iv-growth", "0.06", "-o", os.path.join(tempfile.mkdtemp(), "e.json")],
    capture_output=True, text=True)
_eh_o = json.load(open(os.path.join(
    os.path.dirname(_eh.args[-1]), "e.json"), encoding="utf-8"))["gate2"]
check("闸门二输出 effective_hurdle 区块", "effective_hurdle" in _eh_o)
_e = _eh_o.get("effective_hurdle") or {}
check("有效门槛严于名义门槛（悲观按权重进入①，与闸门一不同源）",
      _e.get("effective_margin_of_safety_required", 0) >
      _e.get("nominal_margin_of_safety_required", 1),
      f"有效 {_e.get('effective_margin_of_safety_required')} vs 名义 {_e.get('nominal_margin_of_safety_required')}")
check("落差 >5pct 时输出披露代号",
      "GATE_EFFECTIVE_HURDLE_GAP" in _eh_o["codes"], str(_eh_o["codes"]))
check("披露代号不参与任何断言判定（纯披露）",
      not [k for k, v in AC.ASSERTIONS.items() if "GATE_EFFECTIVE_HURDLE_GAP" in v])

# ═══════════════════════════════════════════════════════════════════
print("== 14.3 REQ-P0-05 隔离协议 + P0-06 时点 + P0-07 源裁决 + P0-08 规则版本 ==")
import prepare_case as PC  # noqa: E402
import crosscheck_official as CCO  # noqa: E402
import run_backtest_assertions as RBA  # noqa: E402
# --- P0-05 隔离：pre 模式 vs audit 模式 ---
_mt_case = os.path.join(ROOT, "backtest", "600519.SH_2015-08-31")
_sc_check = subprocess.run([sys.executable, os.path.join(SCRIPTS, "prepare_case.py"),
    "--seal-check", _mt_case], capture_output=True, text=True)
check("P0-05 --seal-check（pre）检出一二批旧流程污染（答案与 verdict 同 commit）",
      _sc_check.returncode == 1 and "隔离检查失败" in _sc_check.stdout, _sc_check.stdout[:200])
_sc_audit = subprocess.run([sys.executable, os.path.join(SCRIPTS, "prepare_case.py"),
    "--seal-check", _mt_case, "--audit"], capture_output=True, text=True)
check("P0-05 --audit 模式不把 answer 文件存在当污染（只报 git 时序）",
      "工作区存在答案文件" not in _sc_audit.stdout and "模式 audit" in _sc_audit.stdout, _sc_audit.stdout[:200])
check("P0-05 别名反查：福特（ticker F）不做单字母子串搜索",
      "F" not in PC._case_aliases("F_2005-06-30") and "福特" in PC._case_aliases("F_2005-06-30"))
check("P0-05 别名反查：中石油目录名 → 中文别名",
      "中石油" in PC._case_aliases("601857.SH_2007-11-05"))
_iso = subprocess.run([sys.executable, os.path.join(SCRIPTS, "prepare_case.py"),
    "--isolation-report"], capture_output=True, text=True)
check("P0-05 --isolation-report 输出批次执行率且 legacy 批次不计入验收",
      _iso.returncode == 0 and "legacy" in _iso.stdout, _iso.stdout[:200])
# --- P0-08 规则版本快照 ---
_snap_d = PC.snapshot_rules()
check("P0-08 快照含 skill_commit / thresholds / missing 三段",
      all(k in _snap_d for k in ("skill_commit", "thresholds", "missing")))
check("P0-08 RULES_REGISTRY 全部取到（missing 为空，无静默 null）",
      _snap_d["missing"] == [], str(_snap_d["missing"]))
_th = _snap_d["thresholds"]
check("P0-08 关键阈值不为 null：折现率/悲观门槛/MoS/排雷阈值",
      _th.get("discount_rate_default") == 0.10 and _th.get("pessimistic_hurdle_default") == 0.0
      and _th.get("mos_wide") == 0.25 and _th.get("forensic_thresholds", {}).get("TH_CASH_RATIO") == 0.25,
      str({k: _th.get(k) for k in ("discount_rate_default", "pessimistic_hurdle_default", "mos_wide")}))
import reverse_dcf as _RD08  # noqa: E402
check("P0-08 快照值 == 引擎实际默认（argparse 引用模块常量，非各自硬编码）",
      _th["discount_rate_default"] is _RD08.DEFAULT_DISCOUNT_RATE
      and _th["index_hurdle_default"] == _RD08.DEFAULT_INDEX_HURDLE)
# --- P0-06 时点校验 ---
_tmpd6 = tempfile.mkdtemp()
_case6 = os.path.join(_tmpd6, "backtest", "TEST_2024-12-31")
os.makedirs(os.path.join(_case6, "data"))
json.dump({"batch": 4, "replay_date": "2024-12-31"}, open(os.path.join(_case6, "meta.json"), "w"))
_fin_fake = {"company": "Test", "currency": "CNY", "unit": "million",
    "meta": {"schema_version": 0, "unit": "百万", "currency": "CNY", "data_vintage": "2024-04-30"},
    "annual": [{"year": 2023, "revenue": 100, "net_income": 10, "ocf": 15, "publish_date": "2024-04-30",
                "total_assets": 200, "total_equity": 80, "shares_diluted": 10},
               {"year": 2024, "revenue": 110, "net_income": 11, "ocf": 16, "publish_date": "2025-04-30",
                "total_assets": 210, "total_equity": 85, "shares_diluted": 10}]}
_fin6_path = os.path.join(_case6, "data", "financials_test.json")
def _run_vd6(d):
    json.dump(d, open(_fin6_path, "w", encoding="utf-8"), ensure_ascii=False)
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_data.py"), _fin6_path],
                          capture_output=True, text=True)
_vd6 = _run_vd6(_fin_fake)
check("P0-06 行级 publish_date > replay_date（vintage 本身合规）→ ERROR",
      "[ERROR] 时点正确性（REQ-P0-06）：2024 行 publish_date 2025-04-30" in _vd6.stdout, _vd6.stdout[:300])
check("P0-06 vintage 早于行级 publish_date 最大值 → 一致性 ERROR",
      "早于行级 publish_date 最大值" in _vd6.stdout, _vd6.stdout[:300])
import copy as _c6
_f6b = _c6.deepcopy(_fin_fake)
_f6b["meta"]["data_vintage"] = "2025-04-30"
_f6b["meta"]["point_in_time_waiver"] = {"reason": "测试豁免", "affected_years": [2024]}
_vd6b = _run_vd6(_f6b)
check("P0-06 显式豁免 point_in_time_waiver → 降 WARN、要求报告披露",
      not any("[ERROR] 时点正确性" in l for l in _vd6b.stdout.splitlines())
      and "已豁免" in _vd6b.stdout, _vd6b.stdout[:300])
_f6c = _c6.deepcopy(_fin_fake)
_f6c["meta"]["data_vintage"] = "2025-04-30"
_f6c["annual"][1]["restated_from"] = {"revenue": {"original": 105}}
_vd6c = _run_vd6(_f6c)
check("P0-06 restated_from 缺 reason → ERROR（不再只是 WARN）",
      "缺 reason" in _vd6c.stdout and "[ERROR] 重述处理" in _vd6c.stdout, _vd6c.stdout[:300])
_vd6d = subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_data.py"), _fin6_path,
                        "--replay-date", "2026-01-01"], capture_output=True, text=True)
check("P0-06 --replay-date 显式参数覆盖 meta.json",
      "回放时点 2026-01-01" in _vd6d.stdout or "时点正确性" not in _vd6d.stdout, _vd6d.stdout[:200])
check("P0-06 NFLX 历史案例：追溯豁免后通过入口校验（用户裁决：历史数据不动）",
      subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_data.py"),
                      os.path.join(ROOT, "backtest", "NFLX_2016-12-31", "data", "financials_NFLX_2016.json")],
                     capture_output=True, text=True).returncode == 0)
# --- P0-07 源裁决：阈值分级行为 + 3% 注入阻断 + 源 tier ---
check("P0-07 三级阈值常量：命门 1%, 资产负债表 3%, 其他 5%",
      CCO.TOL == 0.01 and CCO.TOL_BALANCE_SHEET == 0.03 and CCO.TOL_OTHER == 0.05)
check("P0-07 tol_for 分级：revenue→block / total_assets→warn / 其他→register",
      CCO.tol_for("revenue", CCO.CORE_FIELDS)[1] == "block"
      and CCO.tol_for("total_assets", CCO.CORE_FIELDS) == (0.03, "warn")
      and CCO.tol_for("capex", CCO.CORE_FIELDS) == (0.05, "register"))
check("P0-07 源 tier 推断：10-K→1 / 官网→2 / westock→3 / 研报→4 / 新浪转引→5",
      [CCO.source_tier(s) for s in ("FY2015 10-K", "公司官网投资者关系", "westock 接口", "券商研报", "新浪转引")] == [1, 2, 3, 4, 5])
import validate_data as _VD07  # noqa: E402
check("P0-07 阈值单点定义：validate_data.TOL 即 crosscheck_official.TOL",
      _VD07.TOL is CCO.TOL)
_mt_fin_p = glob.glob(os.path.join(ROOT, "backtest", "600519.SH_2015-08-31", "data", "financials_*.json"))[0]
_inj = json.load(open(_mt_fin_p, encoding="utf-8"))
_cc_last = sorted(_inj["crosscheck"], key=lambda r: r["year"])[-1]
_row = next(r for r in _inj["annual"] if r["year"] == _cc_last["year"])
_cc_last["revenue"] = round(_row["revenue"] * 1.03, 2)          # 命门 3% → 阻断
_cc_last["total_assets"] = round(_row["total_assets"] * 1.04, 2)  # 资产负债表 4% → 告警
_inj_p = os.path.join(tempfile.mkdtemp(), "financials_inj.json")
json.dump(_inj, open(_inj_p, "w", encoding="utf-8"), ensure_ascii=False)
_cco = subprocess.run([sys.executable, os.path.join(SCRIPTS, "crosscheck_official.py"),
                       "--financials", _inj_p, "--audit"], capture_output=True, text=True)
check("P0-07 验收：注入 3% 命门差异 → --audit 模式非零退出 + 差异表 ⛔阻断",
      _cco.returncode == 1 and "⛔阻断" in _cco.stdout and "revenue" in _cco.stdout, _cco.stdout[-400:])
check("P0-07 同时注入 4% 资产负债表差异 → ⚠告警（不阻断级）",
      "⚠告警" in _cco.stdout and "total_assets" in _cco.stdout, _cco.stdout[-400:])
check("P0-07 差异表含裁决方向（以 tier 更高的源为准）",
      "以 官方源为准（tier 1）" in _cco.stdout, _cco.stdout[-400:])
_vd7 = subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_data.py"), _inj_p],
                      capture_output=True, text=True)
check("P0-07 validate_data 消费端同样阻断（REQ-P0-07 标签）",
      _vd7.returncode != 0 and "双源核对(REQ-P0-07)" in _vd7.stdout, _vd7.stdout[:300])
_inj["crosscheck_exempt"] = {"revenue": {"adopted_value": _row["revenue"], "adopted_source": "年报原文",
    "rejected_value": _cc_last["revenue"], "rejected_source": "测试注入", "reason": "测试结构化豁免"}}
json.dump(_inj, open(_inj_p, "w", encoding="utf-8"), ensure_ascii=False)
_cco2 = subprocess.run([sys.executable, os.path.join(SCRIPTS, "crosscheck_official.py"),
                        "--financials", _inj_p, "--audit", "--write"], capture_output=True, text=True)
check("P0-07 五要素结构化豁免 → 阻断解除、差异表标已豁免",
      _cco2.returncode == 0 and "已豁免" in _cco2.stdout, _cco2.stdout[-300:])
_inj_w = json.load(open(_inj_p, encoding="utf-8"))
check("P0-07 --write 将差异表落盘到 crosscheck_conflicts（供报告附录）",
      len(_inj_w.get("crosscheck_conflicts") or []) == 2
      and any(c["resolved"] for c in _inj_w["crosscheck_conflicts"]))
check("P0-07 legacy 字符串豁免仍接受但提示迁移",
      CCO.exempt_detail({"ocf": "旧格式理由"}, "ocf")[0] is True
      and "legacy" in CCO.exempt_detail({"ocf": "旧格式理由"}, "ocf")[2][0])
# --- P0-05/08 消费端：lint_verdict 交叉校验 + runner 排除 contaminated ---
_tmp_case = os.path.join(ROOT, "backtest", "_TMP_TEST_2020-12-31")
os.makedirs(_tmp_case, exist_ok=True)
try:
    json.dump({"batch": 4, "replay_date": "2020-12-31"}, open(os.path.join(_tmp_case, "meta.json"), "w"))
    _v = {"final_verdict": "拒绝", "verdict_ordinal": 1, "gate1": {}, "gate2": {}, "codes": ["GATE1_FAIL"],
          "codes_provenance": {"engine_derived": ["GATE1_FAIL"], "manually_recorded": []},
          "frozen_before_diff": True, "frozen_at": "2026-09-11"}
    open(os.path.join(_tmp_case, "diff.md"), "w").write("# test\n")
    open(os.path.join(_tmp_case, "report.html"), "w").write("<html></html>")
    _vp = os.path.join(_tmp_case, "verdict.json")
    json.dump(_v, open(_vp, "w"), ensure_ascii=False)
    _l1 = subprocess.run([sys.executable, os.path.join(SCRIPTS, "run_backtest_assertions.py"),
                          "--lint-verdict", _vp], capture_output=True, text=True)
    check("P0-08 lint：第四批 verdict 缺 rules_snapshot → 不通过",
          _l1.returncode == 1 and "缺 rules_snapshot" in _l1.stdout, _l1.stdout[:300])
    _snap_dirty = dict(_snap_d); _snap_dirty["dirty"] = True
    _v["rules_snapshot"] = _snap_dirty
    json.dump(_v, open(_vp, "w"), ensure_ascii=False)
    _l2 = subprocess.run([sys.executable, os.path.join(SCRIPTS, "run_backtest_assertions.py"),
                          "--lint-verdict", _vp], capture_output=True, text=True)
    check("P0-08 lint：rules_snapshot.dirty=true → 拒绝（hash 不代表实际代码）",
          _l2.returncode == 1 and "dirty=true" in _l2.stdout, _l2.stdout[:300])
    # REVIEW-REQ-P0-08 §1 防复发：原版 `and "mos_wide" not in th` 括号绑定错位，快照只要
    # reverse_dcf 可导入就必有 mos_wide，三项关键阈值非空校验恒不触发（实测
    # thresholds={"mos_wide":0.25} 可原样放行）。快照在但内容不全必须拒绝。
    _snap_bad = dict(_snap_d, dirty=False)
    _snap_bad["thresholds"] = {"mos_wide": 0.25}
    _v["rules_snapshot"] = _snap_bad
    json.dump(_v, open(_vp, "w"), ensure_ascii=False)
    _l2b = subprocess.run([sys.executable, os.path.join(SCRIPTS, "run_backtest_assertions.py"),
                           "--lint-verdict", _vp], capture_output=True, text=True)
    check("P0-08 lint：thresholds 只有 mos_wide（折现率/悲观门槛缺失）→ 拒绝（兼容键不得短路关键阈值）",
          _l2b.returncode == 1
          and "缺关键阈值 discount_rate_default" in _l2b.stdout
          and "缺关键阈值 pessimistic_hurdle_default" in _l2b.stdout, _l2b.stdout[:300])
    _snap_bad2 = dict(_snap_d, dirty=False)
    _snap_bad2["thresholds"] = {"mos_wide": 0.25, "mos_requirement": {"wide": 0.4, "narrow": 0.25},
                                "pessimistic_hurdle_default": 0.0}
    _v["rules_snapshot"] = _snap_bad2
    json.dump(_v, open(_vp, "w"), ensure_ascii=False)
    _l2c = subprocess.run([sys.executable, os.path.join(SCRIPTS, "run_backtest_assertions.py"),
                           "--lint-verdict", _vp], capture_output=True, text=True)
    check("P0-08 lint：只缺 discount_rate_default（mos_wide 存在）→ 仍拒绝",
          _l2c.returncode == 1 and "缺关键阈值 discount_rate_default" in _l2c.stdout, _l2c.stdout[:300])
    _v["rules_snapshot"] = dict(_snap_d, dirty=False)
    json.dump(_v, open(_vp, "w"), ensure_ascii=False)
    open(os.path.join(_tmp_case, "answer.json"), "w").write("{}")
    _l3 = subprocess.run([sys.executable, os.path.join(SCRIPTS, "run_backtest_assertions.py"),
                          "--lint-verdict", _vp], capture_output=True, text=True)
    check("P0-05 lint：seal-check 失败（工作区有 answer.json）且未标 contaminated → 不通过",
          _l3.returncode == 1 and "未标 `contaminated: true`" in _l3.stdout, _l3.stdout[:400])
    _v["contaminated"] = True
    json.dump(_v, open(_vp, "w"), ensure_ascii=False)
    _l4 = subprocess.run([sys.executable, os.path.join(SCRIPTS, "run_backtest_assertions.py"),
                          "--lint-verdict", _vp], capture_output=True, text=True)
    check("P0-05 lint：标了 contaminated 后 seal-check 失败不再阻塞",
          _l4.returncode == 0, _l4.stdout[:300])
    json.dump({"expected_verdict_set": [3, 4], "must_trigger": []},
              open(os.path.join(_tmp_case, "answer.json"), "w"))
    _case_obj = RBA.load_case(_tmp_case)
    _res = RBA.check_case(_case_obj)
    check("P0-05 runner：contaminated 案例三轨不计分、不产生回归红灯",
          _res.get("contaminated") and _res["sample_role"] == "unscored"
          and not _res["regressions"] and "不计分" in _res["verdict_track"], str(_res)[:300])
    _fpfn = RBA.fp_fn_summary([r for r in [_res] if not r.get("contaminated")])
    check("P0-05 runner：contaminated 不进 FP/FN 分母", _fpfn["fp_rate"] is None and _fpfn["fn_rate"] is None)
    _res2 = RBA.check_case(RBA.load_case(os.path.join(ROOT, "backtest", "NFLX_2016-12-31")))
    check("P0-08 runner：历史 verdict 无快照 → rules_version=unknown 披露",
          str(_res2.get("rules_version", "")).startswith("unknown"))
finally:
    import shutil as _sh
    _sh.rmtree(_tmp_case, ignore_errors=True)
_asof = subprocess.run([sys.executable, os.path.join(SCRIPTS, "run_backtest_assertions.py"),
                        "--as-of", "HEAD", "--case", "ZM_2021-10-31"], capture_output=True, text=True)
check("P0-08 --as-of HEAD 在临时 worktree 重跑并自动清理",
      "[as-of] 按 skill 版本" in _asof.stdout and "ZM_2021-10-31" in _asof.stdout
      and "via-asof" not in subprocess.run(["git", "worktree", "list"], capture_output=True, text=True, cwd=ROOT).stdout,
      _asof.stdout[:300] + _asof.stderr[:200])

# ═══════════════════════════════════════════════════════════════════
print("== 14.5 底稿 schema 强类型化行为测试（REQ-P0-03） ==")
# 教训：一版 schema 校验只查「字段是否存在」，平安底稿 unit=百万 却填了 shares 单位
# 「亿股」的数量级，strict 标签照发。这里只测行为（拦不拦得住），不测字段清单。
import schema_meta as SM  # noqa: E402
import copy as _c145
_mt_fin_path = glob.glob(os.path.join(ROOT, "backtest", "600519.SH_2015-08-31",
                                      "data", "financials_*.json"))[0]
_mt_fin = json.load(open(_mt_fin_path, encoding="utf-8"))
_e0, _w0 = SM.validate_full(_mt_fin, _mt_fin_path)
check("茅台 strict 底稿量纲自洽（基线无 ERROR）", not _e0, str(_e0)[:200])
# 注入：股本单位声明错 1e4 倍 → 每股收入 2821 元 > CNY 上界 1000 → strict 升级 ERROR
_bad = _c145.deepcopy(_mt_fin)
_bad["meta"]["shares_unit"] = "万股"
_e1, _w1 = SM.validate_full(_bad, "inject")
check("注入错误 shares_unit（万股）→ 量纲哨兵在 strict 档报 ERROR",
      any("每股" in e or "unit_sanity" in e or "量纲" in e for e in _e1), str(_e1)[:200])
# 同一注入在 legacy 档只是 WARN（过渡档不阻断，但要能看见）
_bad_legacy = _c145.deepcopy(_bad)
_bad_legacy["meta"]["schema_version"] = 0
_e2, _w2 = SM.validate_full(_bad_legacy, "inject")
check("同一注入在 legacy 档降为 WARN（不阻断但可见）",
      not [e for e in _e2 if "每股" in e] and any("每股" in w or "量纲" in w for w in _w2),
      f"errors={str(_e2)[:120]} warns={str(_w2)[:120]}")
# 显式豁免：unit_sanity_waiver 让 BRK.A 这类每股天价合法通过
_waived = _c145.deepcopy(_bad)
_waived["meta"]["unit_sanity_waiver"] = "测试：每股收入超上界为真实（BRK.A 形态）"
_e3, _ = SM.validate_full(_waived, "inject")
check("unit_sanity_waiver 显式豁免后 strict 不再报量纲 ERROR",
      not [e for e in _e3 if "每股" in e], str(_e3)[:200])
# source_ref 可定位锚：strict 档无锚 → ERROR；申报文件+年份算锚
check("has_locator：页码/附注/URL/申报文件+年份为锚，裸文字不是",
      SM.has_locator("2023年报 p.45") and SM.has_locator("20-F 2023")
      and SM.has_locator("https://www.sec.gov/Archives/edgar/data/x") and not SM.has_locator("公司官网"))
_noanchor = _c145.deepcopy(_mt_fin)
_noanchor["meta"]["source_ref"] = "公司官网与行情终端"
_e4, _ = SM.validate_full(_noanchor, "inject")
check("strict 档 source_ref 无可定位锚 → ERROR",
      any("source_ref" in e for e in _e4), str(_e4)[:200])
# 快照↔底稿跨文件比对（OBS-600660-01）：底稿单位改错 10 倍时市销率越界
import check_market_snapshot as CMS  # noqa: E402
_fy_dir = os.path.join(ROOT, "backtest", "600660.SH_2018-12-31", "data")
_fy_snap = glob.glob(os.path.join(_fy_dir, "market_snapshot*.json"))
_fy_fin = glob.glob(os.path.join(_fy_dir, "financials_600660*.json")) or \
    [f for f in glob.glob(os.path.join(_fy_dir, "financials_*.json")) if "peer" not in f]
if _fy_snap and _fy_fin:
    _ce, _cw = [], []
    CMS.check_against_financials(_fy_snap[0], _fy_fin[0], _ce, _cw)
    check("福耀快照↔底稿跨文件比对基线无 ERROR", not _ce, str(_ce)[:200])
    _tmpd = tempfile.mkdtemp()
    _fin10 = json.load(open(_fy_fin[0], encoding="utf-8"))
    for _row in _fin10.get("annual", []):
        for _k in ("revenue", "net_income", "total_assets", "total_equity", "ocf", "cash", "total_debt"):
            if isinstance(_row.get(_k), (int, float)):
                _row[_k] = _row[_k] * 100
    _fin10_path = os.path.join(_tmpd, "fin_x100.json")
    json.dump(_fin10, open(_fin10_path, "w", encoding="utf-8"), ensure_ascii=False)
    _ce2, _cw2 = [], []
    CMS.check_against_financials(_fy_snap[0], _fin10_path, _ce2, _cw2)
    check("底稿数值 ×100 后快照↔底稿市销率越界被拦（SNAPSHOT_FIN_PS）",
          any("SNAPSHOT_FIN_PS" in e for e in _ce2), str(_ce2)[:200])
else:
    check("福耀案快照/底稿文件存在（跨文件比对测试前置）", False, f"{_fy_snap} {_fy_fin}")
# 三版审查修订（2026-09-11）：双源一致性 + 全行哨兵。
# 缺口动因：迁移「只增不改」让顶层 unit/currency 与 meta 块并存，而计算端
# （compute_metrics 市值换算）读顶层、校验端读 meta——分歧=校验照过、计算
# 静默错位，OBS-600660-01 的新形态。实测注入分歧当时 0 错误 0 警告。
_div_unit = _c145.deepcopy(_mt_fin)
_div_unit["unit"] = "亿元"          # 计算端读这个做换算
_e5, _ = SM.validate_full(_div_unit, "inject")
check("顶层 unit=亿元 与 meta.unit=百万 量纲分歧 → ERROR（双源真相）",
      any("量纲分歧" in e for e in _e5), str(_e5)[:200])
_div_cur = _c145.deepcopy(_mt_fin)
_div_cur["currency"] = "USD"
_e6, _ = SM.validate_full(_div_cur, "inject")
check("顶层 currency=USD 与 meta CNY 分歧 → ERROR",
      any("`currency`" in e and "分歧" in e for e in _e6), str(_e6)[:200])
_div_std = _c145.deepcopy(_mt_fin)
_div_std["accounting_standard"] = "IFRS"
_e7, _ = SM.validate_full(_div_std, "inject")
check("顶层 accounting_standard 与 meta.standard 分歧 → ERROR",
      any("accounting_standard" in e for e in _e7), str(_e7)[:200])
check("同义写法放行：顶层 million ↔ meta 百万（乘数相等）不报分歧",
      not any("分歧" in e for e in _e0), str(_e0)[:200])
# 全行哨兵：单位声明错位影响所有行，只查最新行会漏「历史行数值错位」。
# 教训：茅台 2006 年每股收入仅 ~5 元，×100=520 仍在界内——测试场景必须
# 真正越界才证明覆盖，否则是假阴性测试。
_row14 = _c145.deepcopy(_mt_fin)
_rows_s = sorted(_row14["annual"], key=lambda r: r.get("year", 0))
_rows_s[-2]["revenue"] = _rows_s[-2]["revenue"] * 100   # 非最新行 ×100
_e8, _w8 = SM.validate_full(_row14, "inject")
check("非最新行数值 ×100 越界被全行哨兵抓出（旧版只查最新行会漏）",
      any("每股收入越界" in x and "最新越界" not in x.split("，")[0] for x in (_e8 + _w8)),
      str((_e8 + _w8))[:200])
_pa_path = os.path.join(ROOT, "cases", "pingan_china", "data",
                        "financials_pingan_insurance.json")
_pa_fin = json.load(open(_pa_path, encoding="utf-8"))
_epa, _ = SM.validate_full(_pa_fin, _pa_path)
check("平安 OBS-SCHEMA-01：12/12 年聚合为 1 条错误并定性「声明错位」",
      len(_epa) == 1 and "12/12" in _epa[0] and "声明错位" in _epa[0], str(_epa)[:200])

# ═══════════════════════════════════════════════════════════════════
print("== 14.6 排雷算术化行为测试（REQ-P0-02） ==")
import forensic_screen as FS  # noqa: E402
def _fs_case(case, as_of=None, **kw):
    _d = os.path.join(ROOT, "backtest", case, "data")
    _fs = [f for f in glob.glob(os.path.join(_d, "financials_*.json"))
           if "peer" not in f and "audit" not in f]
    return FS.screen(json.load(open(_fs[0], encoding="utf-8")), as_of=as_of, **kw)
_km = _fs_case("600518.SH_2017-12-31", as_of="2017-12-31")
check("康美 2017：V4 存贷双高命中（一票否决 → 排除）",
      _km["verdict"].startswith("排除") and "P0_V4_DEPOSIT_LOAN_DOUBLE_HIGH" in _km["alert_codes"],
      str(_km["alert_codes"]))
_ek = _fs_case("EK_2011-06-30", as_of="2011-06-30")
check("柯达 2011：R21 持续经营存疑命中", "P0_R21_GOING_CONCERN" in _ek["alert_codes"], str(_ek["alert_codes"]))
for _neg in ("600660.SH_2018-12-31", "AAPL_2016-04-30", "ZM_2021-10-31", "NFLX_2016-12-31"):
    _r = _fs_case(_neg, as_of=_neg.rsplit("_", 1)[1])
    check(f"{_neg}：排雷无命中（负样本零误杀）", not _r["alert_codes"] and _r["verdict"] == "通过",
          str(_r["alert_codes"]))
_zm = _fs_case("ZM_2021-10-31")
_r11 = [c for c in _zm["clauses"] if c["id"] == "R11"][0]
check("R11 股本膨胀代理判据只提示不命中（Zoom IPO 形态）",
      _r11["status"] == "insufficient_data" and "R11" in _zm["hints"], str(_r11)[:200])
check("四态计数含 not_applicable 且覆盖率拆分为 arithmetic_coverage + manual_pending",
      "not_applicable" in _zm["counts"] and "arithmetic_coverage" in _zm
      and _zm["manual_pending"] == ["V1", "V2", "V3", "V5", "V6"])
# 金融类：V4/V4A/R1/R9 不适用且不进分母
_bank = {"company_type": "银行", "annual": [
    {"year": y, "revenue": 1000, "net_income": 300, "ocf": -50, "total_assets": 30000,
     "total_equity": 2500, "cash": 9000, "total_debt": 20000, "short_term_debt": 18000}
    for y in range(2019, 2025)]}
_rb = FS.screen(_bank)
_na_ids = [c["id"] for c in _rb["clauses"] if c["status"] == "not_applicable"]
check("银行：V4/V4A/R1/R9 判 not_applicable（存贷两高是商业模式，不是雷）",
      set(_na_ids) == {"V4", "V4A", "R1", "R9"} and not _rb["alert_codes"], f"{_na_ids} {_rb['alert_codes']}")
check("not_applicable 不进 arithmetic_coverage 分母",
      "/7 条" in _rb["arithmetic_coverage_basis"], _rb["arithmetic_coverage_basis"])
_bank_as_ind = dict(_bank, company_type="制造业")
check("同一底稿标成制造业 → V4 存贷双高命中（证明是类型驱动而非字段缺失）",
      "P0_V4_DEPOSIT_LOAN_DOUBLE_HIGH" in FS.screen(_bank_as_ind)["alert_codes"])
# --as-of 时点纪律
_asof = {"annual": [{"year": 2013, "publish_date": "2014-03-20", "revenue": 1, "net_income": 1, "ocf": 1},
                    {"year": 2014, "publish_date": "2015-03-25", "revenue": 1, "net_income": 1, "ocf": 1},
                    {"year": 2015, "revenue": 1, "net_income": 1, "ocf": 1}]}
_kept, _drop = FS.filter_as_of(sorted(_asof["annual"], key=lambda r: r["year"]), "2015-08-31")
check("--as-of 2015-08-31：按 publish_date 剔除未发布行，无字段按次年 4/30 推断",
      [r["year"] for r in _kept] == [2013, 2014] and _drop == [2015], f"{[r['year'] for r in _kept]} {_drop}")
check("NFLX 2016-12-31 回放剔除 publish_date=2017-01-27 的 2014 行",
      _fs_case("NFLX_2016-12-31", as_of="2016-12-31")["rows_dropped_by_as_of"] == [2014])
check("cash 别名 cash_and_short_term_investments 被 V4 识别",
      [c for c in FS.screen({"annual": [{"year": 2020, "cash_and_short_term_investments": 30,
                                          "total_debt": 30, "total_assets": 100}]})["clauses"]
       if c["id"] == "V4"][0]["status"] == "hit")
# V4A 三重复合判据（2026-09-11 三版：一版固定 1.2% 只落了半句且从未在真实数据上运行过）
check("康美 2017：V4A 首次在真实数据上命中（phase0_arithmetic 兜底取数，"
      "收益率 0.84% < CNY2016 基准 1.5% 且 < 融资成本 4.86% 的一半）",
      "P0_V4A_INTEREST_INVERSION" in _km["alert_codes"], str(_km["alert_codes"]))
_v4a_km = [c for c in _km["clauses"] if c["id"] == "V4A"][0]
check("V4A 命中详情含量化利差（融资成本与年利差损失）",
      "融资成本" in _v4a_km["detail"] and "利差损失" in _v4a_km["detail"],
      _v4a_km["detail"][:150])
# 美股零利率防误杀：真现金收益率 0.4% ≥ USD 基准 0.25% → pass
_usd = {"currency": "USD", "annual": [
    {"year": 2012, "cash": 40, "total_debt": 40, "total_assets": 100},
    {"year": 2013, "cash": 40, "total_debt": 40, "total_assets": 100,
     "interest_income": 0.16, "interest_expense": 1.2}]}
_ru = FS.screen(_usd)
_v4a_usd = [c for c in _ru["clauses"] if c["id"] == "V4A"][0]
check("美股零利率期真现金（0.4% ≥ USD 基准 0.25%）V4A 不误杀",
      _v4a_usd["status"] == "pass" and "P0_V4A_INTEREST_INVERSION" not in _ru["alert_codes"],
      _v4a_usd["detail"])
# 合成正例：双高 + 0.5% 收益率 + 4% 融资成本 → 命中
_hit2 = {"currency": "CNY", "annual": [
    {"year": 2015, "cash": 40, "total_debt": 40, "total_assets": 100},
    {"year": 2016, "cash": 40, "total_debt": 40, "total_assets": 100,
     "interest_income": 0.2, "interest_expense": 1.6}]}
check("合成正例：双高 ∧ 0.5% < 基准 1.5% ∧ < 融资成本 4% 一半 → V4A 命中",
      "P0_V4A_INTEREST_INVERSION" in FS.screen(_hit2)["alert_codes"])
# 形态不成立 → pass 无验证对象（验证器不独立猎雷）
_nomorph = {"currency": "CNY", "annual": [
    {"year": 2015, "cash": 10, "total_debt": 45, "total_assets": 100},
    {"year": 2016, "cash": 10, "total_debt": 45, "total_assets": 100,
     "interest_income": 0.02, "interest_expense": 1.6}]}
check("双高形态未成立 → V4A pass（无验证对象，不独立排除）",
      [c for c in FS.screen(_nomorph)["clauses"] if c["id"] == "V4A"][0]["status"] == "pass")
# 币种未知 → insufficient_data 提示 --deposit-rate，不按常数误判
_nocc = {"annual": _hit2["annual"]}
_v4a_nocc = [c for c in FS.screen(_nocc)["clauses"] if c["id"] == "V4A"][0]
check("币种未知且未指定基准 → insufficient_data（提示 --deposit-rate）而非 hit/pass",
      _v4a_nocc["status"] == "insufficient_data" and "--deposit-rate" in _v4a_nocc.get("hint", ""),
      _v4a_nocc.get("hint", ""))
check("--deposit-rate 覆盖后复合判据可完成",
      [c for c in FS.screen(_nocc, deposit_rate=0.015)["clauses"]
       if c["id"] == "V4A"][0]["status"] == "hit")
# 低于基准但缺利息支出 → insufficient（「远低于融资成本」未验证，veto 不半响）
_noie = {"currency": "CNY", "annual": [
    {"year": 2015, "cash": 40, "total_debt": 40, "total_assets": 100},
    {"year": 2016, "cash": 40, "total_debt": 40, "total_assets": 100,
     "interest_income": 0.2}]}
_v4a_noie = [c for c in FS.screen(_noie)["clauses"] if c["id"] == "V4A"][0]
check("低于存款基准但缺利息支出 → insufficient_data（veto 不半响）",
      _v4a_noie["status"] == "insufficient_data" and "利息支出" in _v4a_noie.get("hint", ""),
      _v4a_noie.get("hint", ""))

# ═══════════════════════════════════════════════════════════════════
print("== 14.7 成长股通道（REQ-P1-01，reverse_dcf growth）==")
# 动因：B2-09 Netflix 案（全回测最深假阴性 2 档）——纯 OE 框架对「当期 OE 极小
# 但单元经济已证」的公司无语言可说。通道 = 成熟期稳态利润×到达概率折回；
# 测试锁定四件事：数值数学、单元经济硬拒绝、基率锚挂钩、档位带语义。
import reverse_dcf as _RD  # noqa: E402  (已在 7.55 导入，幂等)

# --- A. 数值数学：成功分支折回 + 概率加权 + 失败残值 ---
_gc = _RD.growth_channel_value(7200.0, 0.25, 10, 0.10,
                               terminal_multiple=20.0, failure_equity_value=646.0)
check("终值 = 成熟OE×倍数", abs(_gc["terminal_value_mature"] - 144000.0) < 1e-6)
check("成功分支现值 = 终值/(1+r)^N",
      abs(_gc["pv_today"] - 144000.0 / 1.1 ** 10) < 1e-6)
check("概率加权价值 = p×PV + (1−p)×残值",
      abs(_gc["probability_weighted_value"] - (0.25 * 144000 / 1.1 ** 10 + 0.75 * 646)) < 1e-6)
check("Gordon 交叉核对 = OE×(1+g)/(r−g)",
      abs(_gc["gordon_cross_check_tv"] - 7200 * 1.025 / 0.075) < 1e-6)
check("终值占比结构性披露为 1.0（档位上限的依据）",
      _gc["terminal_value_ratio"] == 1.0)

# --- B. 极端值反推（体检纪律）：MC = 概率加权价值 ⇒ implied p = 供给的 p ---
# 另一个铁律：MC = PV（p=1 的成功分支）⇒ implied p = 100%；
# MC = PV + (PV−F)/2 ⇒ implied p = 50%。反解公式错一步就暴露。
_pv = _gc["pv_today"]
_imp_full = (144000 / 1.1 ** 10 - 646) / (_pv - 646)   # p=1
_imp_half = (646 + (_pv - 646) / 2 - 646) / (_pv - 646)  # p=0.5
check("极端值反推：MC=PV ⇒ implied p=1（数学自洽）", abs(_imp_full - 1.0) < 1e-9)
check("极端值反推：MC=P/2+F/2 ⇒ implied p=0.5", abs(_imp_half - 0.5) < 1e-9)

# --- C. 单元经济硬拒绝（区分 Netflix 与乐视的第一道门）---
with tempfile.TemporaryDirectory() as _td:
    _gargs = ["growth", "--market-cap", "53128", "--current-revenue", "8832",
              "--mature-oe", "7200", "--arrival-prob", "0.5",
              "--contribution-margin", "-0.05",
              "--mature-state-basis", "x [E:a]", "--arrival-prob-basis", "y [E:b]"]
    _p_neg = run(_gargs)
    check("边际贡献率 ≤0 → 通道拒绝服务（exit 2）",
          _p_neg.returncode == 2 and "GROWTH_UNIT_ECONOMICS_UNPROVEN" in _p_neg.stdout,
          f"rc={_p_neg.returncode}")
    _p_ltv = run([a if a != "-0.05" else "0.30" for a in _gargs] + ["--ltv-cac", "0.7"])
    check("LTV/CAC <1 → 同样拒绝（exit 2）",
          _p_ltv.returncode == 2 and "LTV/CAC" in _p_ltv.stdout, f"rc={_p_ltv.returncode}")

# --- D. 裸概率禁止（与 S7 概率纪律同源）---
    _p_naked = run(["growth", "--market-cap", "53128", "--current-revenue", "8832",
                    "--mature-oe", "7200", "--arrival-prob", "0.5",
                    "--contribution-margin", "0.30",
                    "--mature-state-basis", "x [E:a]", "--arrival-prob-basis", "无证据"])
    check("到达概率未挂 [E:] → 拒绝（exit 1）且提示注册码",
          _p_naked.returncode == 1 and "GROWTH_ARRIVAL_PROB_UNANCHORED" in
          (_p_naked.stderr or "") + (_p_naked.stdout or ""),
          f"rc={_p_naked.returncode} stderr={(_p_naked.stderr or '')[:120]}")

# --- E. 基率锚挂钩（REQ-P1-05 接口预留）---
_a1, _m1 = _RD.revenue_growth_base_rate(8832, 0.1505)
check("NFLX 形态：<100亿规模 × 15.05% 所需增速 → 锚 25%（≥10% 插值行）",
      _a1 == 0.25 and _m1["interpolated"] is True and _m1["scale_band"] == "<100亿美元",
      str(_m1))
_a2, _m2 = _RD.revenue_growth_base_rate(8832, 0.22)
check("所需增速 ≥20% → 用 ≥20% 行（同为上界）", _a2 == 0.10 and _m2["interpolated"] is False)
_a3, _m3 = _RD.revenue_growth_base_rate(8832, 0.05)
check("所需增速低于表内最低档 10% → 无上界约束（anchor=None）",
      _a3 is None and "无上界约束" in _m3.get("note", ""), str(_m3))
_a4, _m4 = _RD.revenue_growth_base_rate(8832, -0.02)
check("成熟态不高于当期规模 → 无增长基率约束", _a4 is None)
_a5, _m5 = _RD.revenue_growth_base_rate(60000, 0.15)
check("规模 ≥500亿美元 → 分档正确（≥10% 行 10%）", _a5 == 0.10 and
      _m5["scale_band"] == "≥500亿美元", str(_m5))

# --- F. CLI 端到端：Netflix 2016 形态（验收锚）---
with tempfile.TemporaryDirectory() as _td:
    _fp = os.path.join(_td, "growth.json")
    _p_nflx = run(["growth", "--market-cap", "53128", "--current-revenue", "8832",
                   "--mature-revenue", "36000", "--mature-oe-margin", "0.20",
                   "--terminal-multiple", "20", "--arrival-prob", "0.25",
                   "--years-to-maturity", "10", "--shares", "436.456",
                   "--contribution-margin", "0.44", "--failure-equity-value", "646",
                   "--mature-state-basis", "300M会员×$10×12=360亿×20% [E:q.json]",
                   "--arrival-prob-basis", "基率锚25%取等值 [E:vg.md]", "-o", _fp])
    check("growth 模式运行成功（NFLX 2016 验收形态）", _p_nflx.returncode == 0,
          (_p_nflx.stderr or "")[:200])
    if _p_nflx.returncode == 0:
        _g = json.load(open(_fp))
        # 每股价值 = (0.25×(36000×0.20×20)/1.1^10 + 0.75×646)/436.456 ≈ 32.91
        check("每股价值数学正确（≈32.91，vs 旧 OE 通道 13.71）",
              abs(_g["value_per_share"] - 32.91) < 0.05, str(_g["value_per_share"]))
        _imp = _g["implied"]["implied_arrival_prob"]
        check("现价隐含到达概率 ≈ 96%（<100%，非透支）", 0.90 < _imp < 1.0, str(_imp))
        check("隐含概率 vs 基率锚 3.8 倍 → GROWTH_IMPLIED_VS_BASERATE_GAP",
              "GROWTH_IMPLIED_VS_BASERATE_GAP" in _g["codes"])
        check("GROWTH_TERMINAL_DOMINATED 恒随通道输出",
              "GROWTH_TERMINAL_DOMINATED" in _g["codes"])
        check("p=25% 不高于锚 25% → 不触发 ABOVE_BASERATE",
              "GROWTH_ARRIVAL_PROB_ABOVE_BASERATE" not in _g["codes"])
        check("档位带=观察等价格、上限=小仓位试探",
              _g["verdict_band"]["suggestion"] == "观察等价格"
              and _g["verdict_band"]["cap"] == "小仓位试探")
        check("Gordon 交叉核对口径同时输出",
              _g["implied"]["gordon_cross_check_implied_prob"] > 1.0)
    # 透支形态：市值抬到连必然到达都解释不了
    _p_ovr = run(["growth", "--market-cap", "53128", "--current-revenue", "8832",
                  "--mature-oe", "7200", "--arrival-prob", "0.25",
                  "--contribution-margin", "0.44",
                  "--mature-state-basis", "x [E:a]", "--arrival-prob-basis", "y [E:b]"])
    # 无 --terminal-multiple → Gordon 口径：PV=37936 < MC → implied p ≥ 1
    if _p_ovr.returncode == 0 or "透支" in (_p_ovr.stdout or ""):
        check("Gordon 口径下现价隐含 p ≥100% → 透支拒绝档带 + 注册码",
              "GROWTH_PRICE_IMPLIES_CERTAIN_ARRIVAL" in (_p_ovr.stdout or "")
              and "拒绝（透支）" in (_p_ovr.stdout or ""), _p_ovr.stdout[-300:])

# --- G. p 高于基率锚 → ABOVE_BASERATE 警示码 ---
with tempfile.TemporaryDirectory() as _td:
    _fp2 = os.path.join(_td, "g2.json")
    _p_hi = run(["growth", "--market-cap", "20000", "--current-revenue", "8832",
                 "--mature-revenue", "36000", "--mature-oe-margin", "0.20",
                 "--terminal-multiple", "20", "--arrival-prob", "0.50",
                 "--years-to-maturity", "10", "--shares", "436.456",
                 "--contribution-margin", "0.44",
                 "--mature-state-basis", "x [E:a]", "--arrival-prob-basis", "y [E:b]",
                 "-o", _fp2])
    check("p=50% > 锚 25% → GROWTH_ARRIVAL_PROB_ABOVE_BASERATE",
          _p_hi.returncode == 0 and "GROWTH_ARRIVAL_PROB_ABOVE_BASERATE" in _p_hi.stdout)

# --- G2. 直传 --mature-oe 而缺 --mature-revenue → 锚不可用显式告警（量纲修复回归）---
# 旧实现用 mature_oe/current_revenue 当所需 CAGR 代理：7200/8832 → −2.02% ≤ 0
# → 锚静默关闭，还打印事实错误的"成熟态不高于当期规模"。修复后必须显式告警。
with tempfile.TemporaryDirectory() as _td:
    _fp3 = os.path.join(_td, "g3.json")
    _p_dn = run(["growth", "--market-cap", "20000", "--current-revenue", "8832",
                 "--mature-oe", "7200", "--terminal-multiple", "20",
                 "--arrival-prob", "0.25", "--years-to-maturity", "10",
                 "--contribution-margin", "0.44",
                 "--mature-state-basis", "x [E:a]", "--arrival-prob-basis", "y [E:b]",
                 "-o", _fp3])
    check("直传 --mature-oe → 运行成功且输出 GROWTH_ANCHOR_UNAVAILABLE",
          _p_dn.returncode == 0 and "GROWTH_ANCHOR_UNAVAILABLE" in _p_dn.stdout,
          f"rc={_p_dn.returncode}")
    check("锚不可用时禁止再打印量纲错误的『成熟态不高于当期规模』",
          "成熟态不高于当期规模" not in (_p_dn.stdout or ""))
    if _p_dn.returncode == 0:
        _g3 = json.load(open(_fp3))
        check("锚不可用：required_cagr=None、basis=unavailable、anchor=None",
              _g3["arrival"]["required_cagr"] is None
              and _g3["arrival"]["required_cagr_basis"] == "unavailable"
              and _g3["arrival"]["base_rate_anchor"] is None)
        check("GROWTH_ANCHOR_UNAVAILABLE 落盘进 codes（回放断言可判定）",
              "GROWTH_ANCHOR_UNAVAILABLE" in _g3["codes"])
    _a6, _m6 = _RD.revenue_growth_base_rate(8832, None)
    check("required_cagr=None → note 指明缺收入口径（非『不高于当期规模』）",
          _a6 is None and "mature-revenue" in _m6.get("note", ""), str(_m6))
    # 正向回归：给了 --mature-revenue 时锚正常、不触发 UNAVAILABLE
    check("给了 --mature-revenue → 不触发 GROWTH_ANCHOR_UNAVAILABLE（F 节锚 25% 回归）",
          "GROWTH_ANCHOR_UNAVAILABLE" not in _g["codes"])

# --- H. check_scenarios 接线：growth_terminal_backcast 可作基准方法 ---
import copy as _copy  # noqa: E402
_scen_growth = _copy.deepcopy(GOOD)
_scen_growth["scenarios"][1]["method"] = "growth_terminal_backcast"
_d, _e, _w, _i = _run_cs(_scen_growth)
check("基准情景 method=growth_terminal_backcast 不被 S2 误拦",
      not any(x.startswith("S2") for x in _e), str(_e))
# 反向：悲观情景用 growth_terminal_backcast（DCF 系）必须被拦——它不是独立下行估计
_scen_bad = _copy.deepcopy(GOOD)
_scen_bad["scenarios"][0]["method"] = "growth_terminal_backcast"
_d2, _e2, _w2, _i2 = _run_cs(_scen_bad)
check("悲观情景用 growth_terminal_backcast → S2 拦截（非独立方法）",
      any(x.startswith("S2 悲观情景方法") for x in _e2), str(_e2))

# --- I. 告警码全部已注册（写错必须被逮住）---
_growth_codes = ["GROWTH_UNIT_ECONOMICS_UNPROVEN", "GROWTH_ARRIVAL_PROB_UNANCHORED",
                 "GROWTH_ARRIVAL_PROB_ABOVE_BASERATE", "GROWTH_PRICE_IMPLIES_CERTAIN_ARRIVAL",
                 "GROWTH_IMPLIED_VS_BASERATE_GAP", "GROWTH_TERMINAL_DOMINATED"]
check("六个 GROWTH_* 码全部在 ALERTS 注册表", not AC.unknown_codes(_growth_codes),
      str(AC.unknown_codes(_growth_codes)))
_docs_local = open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8").read() + "".join(
    open(os.path.join(ROOT, "references", _r), encoding="utf-8").read()
    for _r in os.listdir(os.path.join(ROOT, "references")) if _r.endswith(".md"))
check("文档已接入成长通道（growth-framework/company-types/valuation-guide/SKILL）",
      all(_kw in _docs_local for _kw in
          ("growth_terminal_backcast", "GROWTH_UNIT_ECONOMICS_UNPROVEN", "成熟期稳态利润")))

# ═══════════════════════════════════════════════════════════════════
print("== 14.8 持仓型控股 SOTP 通道（REQ-P1-02，reverse_dcf sotp + compute_metrics sotp_screen）==")
# 动因：OBS-2019-06-01 软银案（方向与 Netflix 相反——过乐观）——持仓型控股的
# OE 被并表错位+非现金重估+口径重分类三重污染，owner yield 21.1% 对股息率
# 0.43% 是数学不可能。通道 = 持仓表+经营业务−母公司净债，×(1−控股折价)；
# 测试锁定：三段式数学、持仓表六要素硬拒绝、净债口径门、折价基率带、
# 隐含折价反解、sotp_screen 双向失真识别、软银/腾讯两验收锚。

# --- A. 三段式数学（核心函数）---
_sv = _RD.sotp_channel_value([
    {"name": "上市持仓A", "stake_pct": 66.49, "valuation_method": "market_price",
     "gross_value": 1000.0, "liquidity": "listed_stake", "liquidity_haircut": 0.9,
     "evidence": "x [E:a]"},
    {"name": "私募持仓B", "stake_pct": None, "valuation_method": "fair_value_disclosed",
     "gross_value": 500.0, "liquidity": "private_fund", "liquidity_haircut": 0.6,
     "evidence": "y [E:b]"},
], net_debt=200.0, holding_discount=0.40, operating_value=300.0)
check("持仓毛值 = Σ gross_value", abs(_sv["portfolio_gross_value"] - 1500.0) < 1e-9)
check("持仓净值 = Σ(毛值×变现折价)",
      abs(_sv["portfolio_net_value"] - (1000 * 0.9 + 500 * 0.6)) < 1e-9)
check("equity NAV = 持仓净值+经营−净债", abs(_sv["equity_nav"] - 1300.0) < 1e-9)
check("可投资价值 = NAV×(1−控股折价)", abs(_sv["investable_value"] - 780.0) < 1e-9)
check("持仓占 equity NAV = 1200/1300", abs(_sv["holdings_share_of_equity_nav"] - 1200 / 1300) < 1e-9)
check("逐项明细保留六要素（可审计）",
      len(_sv["holdings_detail"]) == 2 and
      all(k in _sv["holdings_detail"][0] for k in
          ("name", "stake_pct", "valuation_method", "liquidity",
           "gross_value", "liquidity_haircut", "evidence")))

# --- B. 持仓表六要素硬拒绝（区分『结构化记录』与 add-back 补丁的第一道门）---
_H1 = {"name": "上市持仓A", "stake_pct": 66.49, "valuation_method": "market_price",
       "gross_value": 11020000, "liquidity": "listed_stake", "liquidity_haircut": 1.0,
       "evidence": "回放日市价×持股 [E:a]"}


def _wht(items, path):
    with open(path, "w", encoding="utf-8") as _f:
        json.dump({"as_of": "2019-06-28", "currency": "JPY", "unit": "million",
                   "items": items}, _f, ensure_ascii=False)
    return path


def _sotp_cli(hf, extra=None):
    return run(["sotp", "--holdings-file", hf,
                "--net-debt", "6200000", "--net-debt-basis", "parent_standalone",
                "--holding-discount", "0.40",
                "--holding-profile", "asian_conglomerate_no_convergence",
                "--holding-discount-basis", "历史NAV折价30-50%中枢 [E:x]",
                "--market-cap", "10890000", "--shares", "2107.667"]
               + (extra or []))


with tempfile.TemporaryDirectory() as _td:
    _no_liq = dict(_H1); del _no_liq["liquidity"]
    _p1 = _sotp_cli(_wht([_no_liq], os.path.join(_td, "h1.json")))
    check("缺 liquidity 字段 → 通道拒绝服务（exit 2）",
          _p1.returncode == 2 and "SOTP_HOLDINGS_TABLE_INVALID" in _p1.stdout,
          f"rc={_p1.returncode}")
    _bad_vm = dict(_H1); _bad_vm["valuation_method"] = "拍脑袋"
    _p2 = _sotp_cli(_wht([_bad_vm], os.path.join(_td, "h2.json")))
    check("估值方法白名单外 → exit 2",
          _p2.returncode == 2 and "白名单" in _p2.stdout, f"rc={_p2.returncode}")
    _no_e = dict(_H1); _no_e["evidence"] = "无证据裸数字"
    _p3 = _sotp_cli(_wht([_no_e], os.path.join(_td, "h3.json")))
    check("持仓缺 [E:] 证据 → exit 2（裸数字禁止）",
          _p3.returncode == 2 and "[E:]" in _p3.stdout, f"rc={_p3.returncode}")
    _bad_hc = dict(_H1); _bad_hc["liquidity_haircut"] = 1.5
    _p4 = _sotp_cli(_wht([_bad_hc], os.path.join(_td, "h4.json")))
    check("变现折价率 >1 → exit 2（SOTP 保守口径不接受溢价）",
          _p4.returncode == 2 and "越界" in _p4.stdout, f"rc={_p4.returncode}")
    _p5 = _sotp_cli(os.path.join(_td, "not_exist.json"))
    check("持仓表文件不存在 → 非零退出（不静默降级）", _p5.returncode != 0)
    # 折价未挂 [E:] → exit 1 + 注册码提示（与 growth UNANCHORED 同语义）
    _p6 = _sotp_cli(_wht([_H1], os.path.join(_td, "h6.json")),
                    ["--holding-discount-basis", "无依据的裸折价"])
    check("控股折价未挂 [E:] → 拒绝（exit 1）且提示注册码",
          _p6.returncode == 1 and "SOTP_HOLDING_DISCOUNT_UNANCHORED" in
          (_p6.stderr or "") + (_p6.stdout or ""),
          f"rc={_p6.returncode}")

# --- C. 软银 2019-06 端到端（验收锚①：纯控股形态）---
_SBT_H = os.path.join(ROOT, "backtest", "9984.T_2019-06-30", "data",
                      "sotp_holdings_REQ-P1-02.json")
with tempfile.TemporaryDirectory() as _td:
    _fp = os.path.join(_td, "sb.json")
    _p_sb = _sotp_cli(_SBT_H, ["-o", _fp])
    check("sotp 模式运行成功（软银 2019-06 验收形态）", _p_sb.returncode == 0,
          (_p_sb.stderr or "")[:200])
    if _p_sb.returncode == 0:
        _sb = json.load(open(_fp))
        # 原案 ADJ3：equity NAV 23.3万亿×0.6÷21.0767亿股 = 6,633（23.3 为四舍五入）
        check("每股 6,630 ≈ 原案 6,633（±0.5%，ADJ3 口径复现）",
              abs(_sb["value_per_share"] - 6633) / 6633 < 0.005,
              str(_sb["value_per_share"]))
        check("equity NAV ≈ 23.29 万亿（毛 NAV 29.49 − 净债 6.2）",
              abs(_sb["equity_nav"] - 23290000) < 1000, str(_sb["equity_nav"]))
        check("现价隐含控股折价 ≈ 53%（与底稿 nav_discount 一致）",
              abs(_sb["implied"]["implied_holding_discount"] - 0.5324) < 0.005,
              str(_sb["implied"]["implied_holding_discount"]))
        check("SOTP_HOLDINGS_DOMINATED（持仓 100%+ of NAV，非经营资产主导）",
              "SOTP_HOLDINGS_DOMINATED" in _sb["codes"])
        check("隐含 53% vs 采用 40% 分歧 13pct → SOTP_IMPLIED_DISCOUNT_GAP",
              "SOTP_IMPLIED_DISCOUNT_GAP" in _sb["codes"])
        check("折价 40% 落在 30-50% 基率带内（不触发 BELOW_BASE_RATE）",
              _sb["base_rate_band"]["discount_in_band"] and
              "SOTP_DISCOUNT_BELOW_BASE_RATE" not in _sb["codes"])
        check("档位带=观察等价格（= 官方 {1,2} 且管线实际档位 2）",
              _sb["verdict_band"]["suggestion"] == "观察等价格")
        check("档位上限=小仓位试探（持仓主导结构纪律）",
              _sb["verdict_band"]["cap"] == "小仓位试探")
        check("折价 ±10pct 敏感性输出（valuation-guide 强制项）",
              _sb["sensitivity"]["holding_discount_pm10pct"][0] is not None)

# --- D. 腾讯形态端到端（验收锚②：经营+投资双轮，分列而不越权）---
_TC_H = os.path.join(ROOT, "cases", "tencent", "data", "sotp_holdings_REQ-P1-02.json")
with tempfile.TemporaryDirectory() as _td:
    _fp = os.path.join(_td, "tc.json")
    _p_tc = run(["sotp", "--holdings-file", _TC_H,
                 "--net-debt", "-58200", "--net-debt-basis", "parent_standalone",
                 "--operating-value", "3590630",
                 "--operating-value-basis",
                 "正常化 OE 219,013×g5%×10y DCF [E:valuation.json]",
                 "--holding-discount", "0.10",
                 "--holding-profile", "operating_with_portfolio",
                 "--holding-discount-basis", "经营主导小带 0-15% 取 10 [E:vg]",
                 "--market-cap", "3811730", "--shares", "9103.147", "--fx", "1.087",
                 "-o", _fp])
    check("sotp 模式运行成功（腾讯经营+投资双轮形态）", _p_tc.returncode == 0,
          (_p_tc.stderr or "")[:200])
    if _p_tc.returncode == 0:
        _tc = json.load(open(_fp))
        check("三段分列齐备（持仓 671,220 / 经营 3,590,630 / 净现金 58,200）",
              abs(_tc["portfolio_net_value"] - 671220) < 1 and
              abs(_tc["operating_value"] - 3590630) < 1 and
              _tc["net_debt"] == -58200)
        check("equity NAV = 4,320,050（三段加总）",
              abs(_tc["equity_nav"] - 4320050) < 1, str(_tc["equity_nav"]))
        check("持仓占比 16% <50% → 无 SOTP_HOLDINGS_DOMINATED（经营主导不越权）",
              "SOTP_HOLDINGS_DOMINATED" not in _tc["codes"] and
              _tc["holdings_share_of_equity_nav"] < 0.5)
        check("档位带=标准双闸门裁定（通道只做分列，不加额外上限）",
              _tc["verdict_band"]["suggestion"] == "标准双闸门裁定"
              and _tc["verdict_band"]["cap"] is None)
        check("fx 换算：每股 427.11 CNY = 464.27 HKD",
              abs(_tc["value_per_share"] - 427.11) < 0.5 and
              abs(_tc["value_per_share_quote_ccy"] - 464.27) < 0.5)
        check("隐含折价 12% vs 采用 10% 分歧 <10pct → 无 GAP 码",
              "SOTP_IMPLIED_DISCOUNT_GAP" not in _tc["codes"])

# --- E. 净债口径门 + 折价越带（并表错位与乐观折价的机器防线）---
with tempfile.TemporaryDirectory() as _td:
    _hf = _wht([_H1], os.path.join(_td, "h.json"))
    _p_con = _sotp_cli(_hf, ["--net-debt-basis", "consolidated"])
    check("净债合并口径 → SOTP_NET_DEBT_CONSOLIDATION_BASIS（软银案教训）",
          _p_con.returncode == 0 and
          "SOTP_NET_DEBT_CONSOLIDATION_BASIS" in _p_con.stdout)
    _p_low = _sotp_cli(_hf, ["--holding-discount", "0.15",
                             "--holding-discount-basis", "带外 [E:x]"])
    check("折价 15% < 基率带下界 30% → SOTP_DISCOUNT_BELOW_BASE_RATE",
          _p_low.returncode == 0 and "SOTP_DISCOUNT_BELOW_BASE_RATE" in _p_low.stdout,
          _p_low.stdout[-200:])
    # 隐含折价数学自洽（体检纪律）：MC=NAV ⇒ implied=0；MC=NAV/2 ⇒ implied=0.5
    _fp2 = os.path.join(_td, "math.json")
    _p_m = _sotp_cli(_hf, ["--market-cap", str(11020000 - 6200000), "-o", _fp2])
    if _p_m.returncode == 0:
        _m = json.load(open(_fp2))
        check("极端值反推：MC=equity NAV ⇒ 隐含折价 0（市场未计折价）",
              abs(_m["implied"]["implied_holding_discount"]) < 1e-9)
    _p_m2 = _sotp_cli(_hf, ["--market-cap", str((11020000 - 6200000) / 2)])
    if _p_m2.returncode == 0 and "implied_holding_discount" in _p_m2.stdout:
        check("极端值反推：MC=NAV/2 ⇒ 隐含折价 50%（数学自洽）",
              "50%" in _p_m2.stdout)

    # --- E2. 伯克希尔式溢价形态 → SOTP_PRICE_IMPLIES_NO_DISCOUNT（极性修复回归）---
    # 旧打印条件 implied_d >= 1.0 是死分支（MC>0 时 implied=1−MC/NAV 恒<1），
    # 从 growth 的 implied_p>=1.0 复制未翻转极性——溢价形态曾无任何提示。
    _nav = 11020000 - 6200000  # 4,800,000 = equity NAV（haircut=1.0、无经营业务）
    _fp3 = os.path.join(_td, "prem.json")
    _p_prem = _sotp_cli(_hf, ["--market-cap", str(_nav * 1.25), "-o", _fp3])
    check("溢价形态（MC=1.25×NAV）→ 输出 SOTP_PRICE_IMPLIES_NO_DISCOUNT",
          _p_prem.returncode == 0 and "SOTP_PRICE_IMPLIES_NO_DISCOUNT"
          in _p_prem.stdout, f"rc={_p_prem.returncode}")
    check("溢价形态文案指出通道不适用（非『≥100%』旧死条件文案）",
          "伯克希尔式" in _p_prem.stdout and "≥100%" not in _p_prem.stdout)
    if _p_prem.returncode == 0:
        _pm = json.load(open(_fp3))
        check("溢价形态隐含折价 = −25%（数学自洽）且新码落盘 codes",
              abs(_pm["implied"]["implied_holding_discount"] + 0.25) < 1e-9
              and "SOTP_PRICE_IMPLIES_NO_DISCOUNT" in _pm["codes"])
        check("溢价形态档位带仍为拒绝（透支）——investable<MC 判定方向不受影响",
              _pm["verdict_band"]["suggestion"] == "拒绝（透支）")
    # 正向回归：折价形态（MC=NAV/2，隐含 +50%）不触发新码
    check("折价形态（隐含 +50%）→ 不触发 NO_DISCOUNT（E 段数学用例回归）",
          "SOTP_PRICE_IMPLIES_NO_DISCOUNT" not in (_p_m2.stdout or ""))

# --- F. sotp_screen：经营性 OE / look-through 分列与双向失真识别 ---
def _rows_with_inv(inv_seq, div_seq=None):
    _rows = mk_rows([0.10, 0.10, 0.10, 0.10])
    for _r, _v in zip(_rows, inv_seq):
        if _v is not None:
            _r["investment_income"] = _v
    if div_seq:
        for _r, _v in zip(_rows, div_seq):
            if _v is not None:
                _r["dividend_income"] = _v
    return _rows


_r_hi = cm.compute({"company": "T", "ticker": "T", "currency": "CNY", "unit": "million",
                    "annual": _rows_with_inv([10, 10, 10, 100], [0, 0, 0, 80])},
                   market_cap=None)
check("重估推高型：最新年占比 86% ≥50% → distortion",
      _r_hi["sotp_screen"]["applicable"] and _r_hi["sotp_screen"]["distortion"])
check("重估推高型 → M_OWNER_YIELD_CONSOLIDATION_DISTORTION 触发",
      "M_OWNER_YIELD_CONSOLIDATION_DISTORTION" in _r_hi["alert_codes"])
check("look-through 收益分列（dividend_income 单列）",
      _r_hi["sotp_screen"]["look_through_income_latest"] == 80)
check("operating_oe = OE − 投资收益（经营性口径单列）",
      abs(_r_hi["series"][-1]["operating_oe"] -
          (_r_hi["series"][-1]["owner_earnings"] - 100)) < 1e-9)

_r_dn = cm.compute({"company": "T2", "ticker": "T2", "currency": "CNY", "unit": "million",
                    "annual": _rows_with_inv([5, 5, 5, -60])}, market_cap=None)
check("减值压低型（腾讯 2023 形态）：负投资收益同判 distortion（abs 口径）",
      _r_dn["sotp_screen"]["distortion"] and
      "M_OWNER_YIELD_CONSOLIDATION_DISTORTION" in _r_dn["alert_codes"])

_r_pure = cm.compute({"company": "T3", "ticker": "T3", "currency": "CNY", "unit": "million",
                      "annual": mk_rows([0.10, 0.10, 0.10, 0.10])}, market_cap=None)
check("阴性对照：经营主导型（无投资收益字段）→ not applicable 零误伤",
      not _r_pure["sotp_screen"]["applicable"] and
      "M_OWNER_YIELD_CONSOLIDATION_DISTORTION" not in _r_pure["alert_codes"])

_SBT_FIN = os.path.join(ROOT, "backtest", "9984.T_2019-06-30", "data",
                        "sotp_demo_financials_REQ-P1-02.json")
if os.path.exists(_SBT_FIN):
    with open(_SBT_FIN, encoding="utf-8") as _f:
        _sb_fin = json.load(_f)
    _r_sb = cm.compute(_sb_fin, market_cap=10889000)
    check("软银真实数据：FY2018 占比 92% → distortion（OBS-2019-06-01 首次机器识别）",
          _r_sb["sotp_screen"]["distortion"] and
          "M_OWNER_YIELD_CONSOLIDATION_DISTORTION" in _r_sb["alert_codes"])
    check("软银经营性 OE ≈ 990,011（剔除重估 1,302,838 后的量级）",
          abs(_r_sb["sotp_screen"]["operating_oe_latest"] - 990011) < 2000,
          str(_r_sb["sotp_screen"]["operating_oe_latest"]))
    check("软银 look-through 收益 = 2,051,422（从被投企业收到的分红）",
          _r_sb["sotp_screen"]["look_through_income_latest"] == 2051422)

# --- G. 告警码注册 + 分层命名 + 文档接入 ---
_sotp_codes = ["SOTP_HOLDINGS_TABLE_INVALID", "SOTP_NET_DEBT_CONSOLIDATION_BASIS",
               "SOTP_HOLDING_DISCOUNT_UNANCHORED", "SOTP_DISCOUNT_BELOW_BASE_RATE",
               "SOTP_IMPLIED_DISCOUNT_GAP", "SOTP_HOLDINGS_DOMINATED",
               "SOTP_PRICE_IMPLIES_NO_DISCOUNT",
               "M_OWNER_YIELD_CONSOLIDATION_DISTORTION"]
check("七个 SOTP 通道码全部在 ALERTS 注册表",
      not AC.unknown_codes(_sotp_codes), str(AC.unknown_codes(_sotp_codes)))
check("分层命名表含 SOTP_* 行",
      "`SOTP_*`" in open(os.path.join(SCRIPTS, "alert_codes.py"), encoding="utf-8").read())
check("文档已接入 SOTP 通道（company-types 卡四/valuation-guide/SKILL）",
      all(_kw in _docs_local for _kw in
          ("reverse_dcf.py sotp", "SOTP_HOLDINGS_DOMINATED",
           "M_OWNER_YIELD_CONSOLIDATION_DISTORTION")))
_PROMPT_SOTP = open(os.path.join(ROOT, "backtest", "PROMPT.md"),
                    encoding="utf-8").read()
check("PROMPT 已写入持仓型控股通道纪律段", "REQ-P1-02" in _PROMPT_SOTP and
      "sotp_holdings_REQ-P1-02" in _PROMPT_SOTP and
      "SOTP_HOLDINGS_TABLE_INVALID" in _PROMPT_SOTP)
import check_scenarios as _CS  # noqa: E402
check("check_scenarios：sotp 在 DCF_METHODS（基准/乐观）且 sotp_asset_floor 在独立方法白名单",
      "sotp" in _CS.DCF_METHODS and "sotp_asset_floor" in _CS.INDEPENDENT_METHODS and
      "REQ-P1-02" in _CS.DCF_METHODS["sotp"])

# ═══════════════════════════════════════════════════════════════════
print("== 14.9 护城河评级连续化（REQ-P1-03，平滑 MoS 门槛 + 边界带双档）==")
# 动因：神华 2015 案（diff.md 第 42 行）——"窄"要 40%（实际 37.7% 差 2.3pct）、
# 闸门二①要 21.83%，评"宽"则 25%/16.5% 双放行，一字之差档位跳 2 档。
# 测试锁定：平滑函数数学（锚点/连续/带内 ≥ legacy）、词=投影一致性、
# 裸分数禁止、神华边界带双档报告、legacy 词路径字节兼容、
# 12 案"评级 1 级变动 → 档位跳 2 级 = 0"验收。

# --- A. 平滑函数数学 ---
_f = _RD.mos_requirement_from_score
check("平滑锚点：35→50% / 65→40% / 100→25%",
      abs(_f(35) - 0.50) < 1e-12 and abs(_f(65) - 0.40) < 1e-12
      and abs(_f(100) - 0.25) < 1e-12)
check("分带边界连续：65 分左右极限 = 40%（阶跃归零处）",
      abs(_f(65 - 1e-6) - 0.40) < 1e-8 and abs(_f(65 + 1e-6) - 0.40) < 1e-8)
check("单调递减（35→100 全程）",
      all(_f(s) <= _f(s - 1) + 1e-12 for s in range(36, 101)))
check("s<35 → None（不给买入结论，政策边界）",
      _f(34.9) is None and _f(0) is None)
try:
    _f(101); _inv_ok = False
except ValueError:
    _inv_ok = True
check("得分越界（>100）→ ValueError", _inv_ok)
check("带内处处 ≥ legacy 常数（通道建设非阈值放松，仅锚点相等）",
      all(_f(s) >= 0.40 - 1e-12 for s in range(35, 65))
      and all(_f(s) >= 0.25 - 1e-12 for s in range(65, 101))
      and _f(65) == 0.40 and _f(100) == 0.25)

# --- B. 词投影与边界带 ---
check("分带投影：≥65 wide / ≥35 narrow / <35 none",
      _RD.moat_word_from_score(65) == "wide"
      and _RD.moat_word_from_score(64.9) == "narrow"
      and _RD.moat_word_from_score(35) == "narrow"
      and _RD.moat_word_from_score(34.9) == "none")
_b60 = _RD.moat_boundary_band(60)
check("边界带检测：60/70 ∈ 宽窄带 [60,70]，59.9/70.1 ∉",
      _b60["in_band"] and _RD.moat_boundary_band(70)["in_band"]
      and not _RD.moat_boundary_band(59.9)["in_band"]
      and not _RD.moat_boundary_band(70.1)["in_band"])
check("边界带检测：30/40 ∈ 窄无带 [30,40]，相邻词正确",
      _RD.moat_boundary_band(30)["in_band"]
      and _RD.moat_boundary_band(40)["in_band"]
      and _RD.moat_boundary_band(36)["adjacent_words"] == ("none", "narrow"))
check("边界带外（如 50 分）不触发", not _RD.moat_boundary_band(50)["in_band"])

# --- C. 反推门槛：得分路径与 legacy 在锚点衔接 ---
_r65, _h65 = _RD.moat_irr_hurdle("narrow", 0.10, 5, score=65)
_r100, _h100 = _RD.moat_irr_hurdle("wide", 0.10, 5, score=100)
_rL, _hL = _RD.moat_irr_hurdle("narrow", 0.10, 5)
check("score=65 反推门槛 = legacy 窄锚（连续性衔接实证）",
      abs(_r65 - _rL) < 1e-12 and abs(_h65 - _hL) < 1e-12)
check("score=100 反推门槛 = legacy 宽锚 16.5%",
      abs(_r100 - 0.25) < 1e-12 and abs(_h100 - 0.16515) < 1e-3)
check("score<35 → (None, None)（不给买入结论）",
      _RD.moat_irr_hurdle("narrow", 0.10, 5, score=30) == (None, None))

# --- D. 强制纪律：裸分数禁止 / 词-得分不一致（子进程端到端）---
_SH = os.path.join(ROOT, "backtest", "601088.SH_2015-12-31", "data", "scenarios.json")
_p_nobasis = run(["expected-return", "--scenarios-file", _SH, "--moat-score", "60"])
check("得分无 basis → 硬拒绝（exit 1）+ MOAT_SCORE_BASIS_MISSING",
      _p_nobasis.returncode == 1 and "MOAT_SCORE_BASIS_MISSING" in
      (_p_nobasis.stderr or "") + (_p_nobasis.stdout or ""),
      f"rc={_p_nobasis.returncode}")
with tempfile.TemporaryDirectory() as _td13:
    _sd = json.load(open(_SH, encoding="utf-8"))
    _sd["moat"] = "wide"    # 与得分 60 的投影 narrow 故意不一致
    _p_mismatch = os.path.join(_td13, "mismatch.json")
    json.dump(_sd, open(_p_mismatch, "w", encoding="utf-8"), ensure_ascii=False)
    _p_mm = run(["expected-return", "--scenarios-file", _p_mismatch,
                 "--moat-score", "60",
                 "--moat-score-basis", "x [E:a]"])
    check("词与得分投影不一致 → 硬拒绝 + MOAT_SCORE_WORD_MISMATCH",
          _p_mm.returncode == 1 and "MOAT_SCORE_WORD_MISMATCH" in
          (_p_mm.stderr or "") + (_p_mm.stdout or ""),
          f"rc={_p_mm.returncode}")

# --- E. 神华边界带双档报告（验收演示端到端）---
_ms_demo = os.path.join(ROOT, "backtest", "601088.SH_2015-12-31", "data",
                        "expected_return_moat_score_REQ-P1-03.json")
check("神华得分演示文件存在", os.path.exists(_ms_demo))
if os.path.exists(_ms_demo):
    _d = json.load(open(_ms_demo, encoding="utf-8"))
    _ms = _d["moat_score"]
    check("得分 60 → narrow + 平滑门槛 41.67%（> legacy 40%）",
          _ms["word"] == "narrow" and abs(_ms["mos_requirement"] - 0.416667) < 1e-4
          and _ms["legacy_requirement"] == 0.40)
    check("闸门一：MoS 37.7% < 41.7% → 不过，触发价 14.02",
          _ms["gate1_pass"] is False
          and abs(_ms["gate1_margin_of_safety"] - 0.3773) < 1e-3
          and abs(_ms["gate1_trigger_price"] - 14.0233) < 1e-3)
    check("闸门二①诊断门槛随平滑 MoS（22.5% ≠ legacy 21.83%）",
          abs(_ms["gate2_diagnostic_hurdle"] - 0.2252) < 1e-3)
    _dr = _ms["dual_report"]
    check("边界带触发：60 ∈ [60,70] + MOAT_BOUNDARY_BAND_DUAL",
          _ms["boundary_band"]["in_band"]
          and "MOAT_BOUNDARY_BAND_DUAL" in _d["gate2"]["codes"])
    check("双档报告：±5 分两侧（55/65）门槛 43.3%/40.0%",
          len(_dr["rows"]) == 2
          and abs(_dr["rows"][0]["mos_requirement"] - 0.43333) < 1e-4
          and abs(_dr["rows"][1]["mos_requirement"] - 0.40) < 1e-4)
    check("65 分侧触发价 14.42 = 归档 legacy 触发价（连续性锚实证）",
          abs(_dr["rows"][1]["trigger_price"] - 14.424) < 1e-2)
    check("敏感性标注「结论对护城河判断敏感」",
          "结论对护城河判断敏感" in _dr["sensitivity_note"])

# --- F. legacy 词路径字节兼容（基线不动）---
_p_word = run(["expected-return", "--scenarios-file", _SH,
               "-o", os.path.join(tempfile.gettempdir(), "_ms_legacy.json")])
_wj = os.path.join(tempfile.gettempdir(), "_ms_legacy.json")
_word_res = json.load(open(_wj, encoding="utf-8")) if os.path.exists(_wj) else {}
check("仅评级词（无得分）→ 输出无 moat_score 键（legacy 兼容）",
      _p_word.returncode == 0 and "moat_score" not in _word_res)
check("legacy ①门槛 = 21.83%（神华窄锚不动）",
      _word_res and abs(_word_res["gate2"]["consistency_expected_irr"]["hurdle"]
                        - 0.2183) < 1e-3)

# --- G. 12 案验收：评级 1 级变动 → 档位跳 2 级 = 0 ---
# 档位代理（裁决层语义的机器化下界）：none→1；闸门一/二任一不过→2（观察等价格）；
# 双过→3（小仓位试探——核心买入须裁决层按核验强度/股东回报加码，不是评级
# 传导变量）。"评级 1 级变动"操作化为分带边界的 ε 穿越（knife-edge 情形，
# 即需求所述"两个同样认真的分析师在边界上分歧"），对 35/65 两边界各测一次。
def _tier_313(word, g1, g2):
    if word == "none":
        return 1
    if g1 is not True:
        return 2
    if g2 is not True:
        return 2
    return 3

_max_delta, _cases_tested = 0, 0
for _d13 in sorted(glob.glob(os.path.join(ROOT, "backtest", "*") + os.sep)):
    _sfs = [f for f in glob.glob(os.path.join(_d13, "data", "scenarios*.json"))
            if "audit" not in os.path.basename(f) and "REQ-" not in os.path.basename(f)]
    if not _sfs:
        continue    # 康美案（Phase 0 排除）：无评级无情景，档位由排雷定，评级免疫
    _sd = json.load(open(_sfs[0], encoding="utf-8"))
    _scen = [{"name": s["name"], "value_per_share": float(s["value_per_share"]),
              "probability": float(s["probability"])} for s in _sd["scenarios"]]
    try:
        _res = _RD.expected_return(
            float(_sd["price"]), _scen, int(_sd.get("hold_years", 5)), 0.09,
            float(_sd.get("dividend_yield", 0.0)),
            float(_sd.get("discount_rate", 0.10)),
            moat=_sd.get("moat"), iv_growth=_sd.get("intrinsic_value_growth"))
    except SystemExit:
        continue
    _base = next((s["value_per_share"] for s in _scen
                  if s["name"] in ("基准", "base")), None)
    if not _base:
        continue
    _mos = 1.0 - float(_sd["price"]) / _base
    _g2p = _res["gate2"]["pass"]
    for _edge in (35.0, 65.0):
        _w_lo = _RD.moat_word_from_score(_edge - 0.5)
        _w_hi = _RD.moat_word_from_score(_edge + 0.5)
        _r_lo = _RD.mos_requirement_from_score(_edge - 0.5)
        _r_hi = _RD.mos_requirement_from_score(_edge + 0.5)
        _g1_lo = _mos >= _r_lo if _r_lo is not None else False
        _g1_hi = _mos >= _r_hi if _r_hi is not None else False
        _g2_lo = False if _w_lo == "none" else _g2p
        _g2_hi = False if _w_hi == "none" else _g2p
        _delta = abs(_tier_313(_w_hi, _g1_hi, _g2_hi)
                     - _tier_313(_w_lo, _g1_lo, _g2_lo))
        _max_delta = max(_max_delta, _delta)
    _cases_tested += 1
check(f"12 案验收：评级 1 级变动（边界 ε 穿越）档位跳 2 级 = 0"
      f"（实测 {_cases_tested} 案 maxΔ={_max_delta}）",
      _max_delta <= 1 and _cases_tested >= 11)

# --- H. 对照：legacy 阶跃 + 旧闸门二（①参与）下神华确实跳 2 档 ---
# 用神华档数据复现 diff.md 第 42 行实证——证明旧机制的问题真实存在、
# 且新机制（上项测试）已把它归零。
_sh_sd = json.load(open(_SH, encoding="utf-8"))
_sh_scen = [{"name": s["name"], "value_per_share": float(s["value_per_share"]),
             "probability": float(s["probability"])} for s in _sh_sd["scenarios"]]
_sh_res = _RD.expected_return(
    float(_sh_sd["price"]), _sh_scen, 5, 0.09,
    float(_sh_sd.get("dividend_yield", 0.0)), 0.10,
    moat="narrow", iv_growth=_sh_sd.get("intrinsic_value_growth"))
_sh_g = _sh_res["gate2"]
_sh_mos = 1.0 - float(_sh_sd["price"]) / 24.04
# 旧口径闸门二 = ①②③ 全过（REQ-P0-04 之前）；旧档位口径 = 双闸全过即
# 核心买入候选（4）——diff.md 第 42 行"档位直接跳到 3~4"的量化复现。
def _old_gate2(word):
    _h = _RD.moat_irr_hurdle(word, 0.10, 5)[1]
    _c1 = _sh_res["expected_annualized_irr"] >= _h if _h is not None else None
    _checks = [_c1, _sh_g["no_convergence_floor"]["pass"],
               _sh_g["pessimistic_irr"]["pass"]]
    if word == "none":
        return False
    if any(c is None for c in _checks):
        return None
    return all(_checks)

def _old_tier(word, g1, g2):
    if word == "none":
        return 1
    if g1 is not True:
        return 2
    if g2 is not True:
        return 2
    return 4   # 旧口径：双闸全过 = 核心买入候选

_tier_narrow = _old_tier("narrow", _sh_mos >= 0.40, _old_gate2("narrow"))
_tier_wide = _old_tier("wide", _sh_mos >= 0.25, _old_gate2("wide"))
check("对照实证：legacy 阶跃 + 旧闸门二/旧档位口径下，神华 窄→宽 档位跳 2 档"
      f"（{_tier_narrow}→{_tier_wide}，diff.md 第 42 行问题复现）",
      _tier_narrow == 2 and _tier_wide == 4)

# --- I. check_scenarios S1b（scenarios.json 得分字段校验）---
def _run_cs(path):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "check_scenarios.py"), path],
        capture_output=True, text=True)

with tempfile.TemporaryDirectory() as _td14:
    _tpl = {"price": 10, "moat": "wide", "discount_rate": 0.1, "hold_years": 5,
            "scenarios": [
                {"name": "悲观", "value_per_share": 5, "probability": 0.25,
                 "method": "liquidation", "method_inputs": {}},
                {"name": "基准", "value_per_share": 20, "probability": 0.5,
                 "method": "dcf_owner_earnings"},
                {"name": "乐观", "value_per_share": 30, "probability": 0.25,
                 "method": "dcf_owner_earnings"}]}
    _bad = dict(_tpl, moat_score=50)   # 词 wide 与投影 narrow 不一致 + 无 basis
    _p1 = os.path.join(_td14, "bad.json")
    json.dump(_bad, open(_p1, "w", encoding="utf-8"), ensure_ascii=False)
    _cs_r = _run_cs(_p1)
    _cs_out = _cs_r.stdout + _cs_r.stderr
    check("S1b：词与得分投影不一致 → FAIL + MOAT_SCORE_WORD_MISMATCH",
          "MOAT_SCORE_WORD_MISMATCH" in _cs_out and "[FAIL]" in _cs_out)
    check("S1b：得分缺 [E:] basis → FAIL + MOAT_SCORE_BASIS_MISSING",
          "MOAT_SCORE_BASIS_MISSING" in _cs_out)
    _good = dict(_tpl, moat="narrow", moat_score=50,
                 moat_score_basis="A 30 [E:x]；B 10 [E:y]；C +10 [E:z]",
                 moat_sources=["成本优势"])
    _p2 = os.path.join(_td14, "good.json")
    json.dump(_good, open(_p2, "w", encoding="utf-8"), ensure_ascii=False)
    _cs_r2 = _run_cs(_p2)
    check("S1b：合法得分三字段（词=投影 + [E:]）不触发 S1b 错误",
          "MOAT_SCORE_WORD_MISMATCH" not in _cs_r2.stdout
          and "MOAT_SCORE_BASIS_MISSING" not in _cs_r2.stdout)

# --- J. 码注册与文档接线 ---
_moat_codes = ["MOAT_SCORE_BASIS_MISSING", "MOAT_SCORE_WORD_MISMATCH",
               "MOAT_BOUNDARY_BAND_DUAL"]
check("三个 MOAT_* 码全部在 ALERTS 注册表",
      not AC.unknown_codes(_moat_codes), str(AC.unknown_codes(_moat_codes)))
check("分层命名表含 MOAT_* 行",
      "`MOAT_*`" in open(os.path.join(SCRIPTS, "alert_codes.py"),
                         encoding="utf-8").read())
_docs_313 = open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8").read() + "".join(
    open(os.path.join(ROOT, "references", _r), encoding="utf-8").read()
    for _r in os.listdir(os.path.join(ROOT, "references")) if _r.endswith(".md"))
check("文档已接入连续化（moat-framework 第二节半/valuation-guide/SKILL）",
      all(_kw in _docs_313 for _kw in
          ("REQ-P1-03", "第二节半", "mos_requirement_from_score",
           "MOAT_BOUNDARY_BAND_DUAL", "data-moat-score")))
_prompt_313 = open(os.path.join(ROOT, "backtest", "PROMPT.md"),
                   encoding="utf-8").read()
check("PROMPT 已写入护城河定量得分纪律段（第四批起强制）",
      "REQ-P1-03" in _prompt_313 and "moat_score" in _prompt_313
      and "MOAT_BOUNDARY_BAND_DUAL" in _prompt_313)

# ═══════════════════════════════════════════════════════════════════
print("== 14.10 REQ-P1-04 折现率与情景概率的证据传导 ==")
import copy as _copy104
import reverse_dcf as _rd104  # noqa: E402
import check_scenarios as _cs104  # noqa: E402
from alert_codes import unknown_codes as _uc104  # noqa: E402

# A. 分层表数学
_rate_a, _comp_a = _rd104.stratified_discount_rate("cyclical", 0.0282)
check("A 分层：cyclical@2.82% Rf → 11%（下限 10% + 溢价 1pct）",
      abs(_rate_a - 0.11) < 1e-12 and _comp_a["industry_premium"] == 0.01)
_rate_b, _ = _rd104.stratified_discount_rate("stable", 0.035)
check("A 分层：stable@3.5% Rf → 10%（下限绑定，premium 0）",
      abs(_rate_b - 0.10) < 1e-12)
_rate_c, _ = _rd104.stratified_discount_rate("speculative_growth", None)
check("A 分层：speculative_growth 无 Rf → 12%", abs(_rate_c - 0.12) < 1e-12)
_rate_d, _ = _rd104.stratified_discount_rate("financials", 0.045)
check("A 分层：financials@4.5% Rf → 11%（Rf+4pct=8.5%<10% 下限绑定 +1pct）",
      abs(_rate_d - 0.11) < 1e-12)
try:
    _rd104.stratified_discount_rate("nope")
    check("A 分层：未知行业档硬拒绝", False)
except SystemExit:
    check("A 分层：未知行业档硬拒绝", True)

# B. 概率映射锚点与调整
for _s, _pe, _po in ((35, .35, .15), (65, .30, .20), (100, .25, .25)):
    _m = _rd104.map_scenario_probabilities(_s)
    check(f"B 映射锚点：得分 {_s} → 悲观 {_pe:.0%}/乐观 {_po:.0%}",
          abs(_m["probabilities"]["悲观"] - _pe) < 1e-9
          and abs(_m["probabilities"]["乐观"] - _po) < 1e-9)
    check(f"B 映射锚点：得分 {_s} → 基准恒 50%",
          abs(_m["probabilities"]["基准"] - 0.50) < 1e-9)
_m60 = _rd104.map_scenario_probabilities(60)
check("B 映射：得分 60 → 悲观 30.83%/乐观 19.17%（两翼线性、基准 50%）",
      abs(_m60["probabilities"]["悲观"] - 0.308333) < 1e-4
      and abs(_m60["probabilities"]["乐观"] - 0.191667) < 1e-4)
_prev = 1.0
for _s in range(35, 101):
    _pp = _rd104.map_scenario_probabilities(_s)["probabilities"]["悲观"]
    if _pp > _prev + 1e-12:
        check("B 映射：悲观权重随得分单调递减", False)
        break
    _prev = _pp
else:
    check("B 映射：悲观权重随得分单调递减", True)
check("B 映射：得分 <35 → None（无买入结论，传导无意义）",
      _rd104.map_scenario_probabilities(30) is None)
_m_vp = _rd104.map_scenario_probabilities(75, "strong", 0.30)
check("B 传导链：strong −5pp 后被红队下界 30% 吸收（茅台式演示）",
      abs(_m_vp["probabilities"]["悲观"] - 0.30) < 1e-9
      and any("红队" in s[0] for s in _m_vp["chain"]))
_m_wk = _rd104.map_scenario_probabilities(65, "weak", None)
check("B 传导链：weak 悲观 +5pp（三问答不出 → 更悲观）",
      abs(_m_wk["probabilities"]["悲观"] - 0.35) < 1e-9)
_m_cl = _rd104.map_scenario_probabilities(35, "weak", 0.55)
check("B 传导链：红队 55% 服从映射，clamp 上界 60% 内",
      abs(_m_cl["probabilities"]["悲观"] - 0.55) < 1e-9)
try:
    _rd104.map_scenario_probabilities(65, "omg")
    check("B 传导链：非法 variant 硬拒绝", False)
except SystemExit:
    check("B 传导链：非法 variant 硬拒绝", True)

# C. 引擎 derivation 块（合成三情景）
_scen104 = [{"name": "悲观", "value_per_share": 8, "probability": .30},
            {"name": "基准", "value_per_share": 20, "probability": .50},
            {"name": "乐观", "value_per_share": 28, "probability": .20}]
def _er104(**kw):
    _d = dict(price=10.0, scenarios=_copy104.deepcopy(_scen104), hold_years=5,
              discount_rate=0.10, moat="narrow", iv_growth=0.03,
              dividend_yield=0.02, moat_score=60,
              moat_score_basis="x [E:t]", prob_derivation={
                  "moat_score": 60, "variant_perception": "strong",
                  "red_team_pessimistic": 0.30,
                  "rationale_ref": "传导链 [E:demo]"})
    _d.update(kw)
    return _rd104.expected_return(**_d)

_r_ok = _er104()
check("C 引擎：derivation 块过 → probability_derivation 输出含 rationale_ref/映射/采用/传导链",
      _r_ok["probability_derivation"]["rationale_ref"].startswith("传导链")
      and abs(_r_ok["probability_derivation"]["mapped_probabilities"]["悲观"] - .30) < 1e-9
      and len(_r_ok["probability_derivation"]["transmission_chain"]) >= 3)
check("C 引擎：derivation 块过 → 敏感性表三行（−10/0/+10pp）+ 档位建议",
      len(_r_ok["probability_derivation"]["sensitivity_pm10pp"]["rows"]) == 3
      and all(r.get("tier_suggestion") for r in
              _r_ok["probability_derivation"]["sensitivity_pm10pp"]["rows"]
              if r.get("valid")))
_sc_low = _copy104.deepcopy(_scen104)
_sc_low[0]["probability"], _sc_low[2]["probability"] = .26, .24
try:
    _rd104.expected_return(price=10.0, scenarios=_sc_low, hold_years=5,
                           discount_rate=.10, moat="narrow", iv_growth=.03,
                           moat_score=60, moat_score_basis="x [E:t]",
                           prob_derivation={"moat_score": 60,
                                            "rationale_ref": "r [E:x]"})
    check("C 引擎：偏离映射 >2pp 无论证 → PROB_DERIVATION_MISMATCH 硬拒", False)
except SystemExit as _e:
    check("C 引擎：偏离映射 >2pp 无论证 → PROB_DERIVATION_MISMATCH 硬拒",
          "PROB_DERIVATION_MISMATCH" in str(_e))
_sc_low2 = _copy104.deepcopy(_sc_low)
try:
    _rd104.expected_return(price=10.0, scenarios=_sc_low2, hold_years=5,
                           discount_rate=.10, moat="narrow", iv_growth=.03,
                           moat_score=60, moat_score_basis="x [E:t]",
                           prob_derivation={"moat_score": 60,
                                            "rationale_ref": "r [E:x]",
                                            "deviation_rationale": "无证据理由"})
    check("C 引擎：deviation_rationale 缺 [E:] → 仍硬拒", False)
except SystemExit as _e:
    check("C 引擎：deviation_rationale 缺 [E:] → 仍硬拒",
          "PROB_DERIVATION_MISMATCH" in str(_e))
_sc_far = _copy104.deepcopy(_scen104)
_sc_far[0]["probability"], _sc_far[2]["probability"] = .15, .35   # 偏离 −15.8pp
try:
    _rd104.expected_return(price=10.0, scenarios=_sc_far, hold_years=5,
                           discount_rate=.10, moat="narrow", iv_growth=.03,
                           moat_score=60, moat_score_basis="x [E:t]",
                           prob_derivation={"moat_score": 60,
                                            "rationale_ref": "r [E:x]",
                                            "deviation_rationale": "论证 [E:x]"})
    check("C 引擎：偏离映射 >10pp → PROB_DERIVATION_OUT_OF_RANGE 硬拒（论证也不救）", False)
except SystemExit as _e:
    check("C 引擎：偏离映射 >10pp → PROB_DERIVATION_OUT_OF_RANGE 硬拒（论证也不救）",
          "PROB_DERIVATION_OUT_OF_RANGE" in str(_e))
_sc_rt = _copy104.deepcopy(_scen104)
_sc_rt[0]["probability"], _sc_rt[1]["probability"] = .25, .55   # 低于红队下界 30%
try:
    _rd104.expected_return(price=10.0, scenarios=_sc_rt, hold_years=5,
                           discount_rate=.10, moat="narrow", iv_growth=.03,
                           moat_score=60, moat_score_basis="x [E:t]",
                           prob_derivation={"moat_score": 60,
                                            "red_team_pessimistic": 0.30,
                                            "rationale_ref": "r [E:x]",
                                            "deviation_rationale": "论证 [E:x]"})
    check("C 引擎：采用悲观 < 红队下界 → 硬拒（红队下界不可被论证突破）", False)
except SystemExit as _e:
    check("C 引擎：采用悲观 < 红队下界 → 硬拒（红队下界不可被论证突破）",
          "PROB_DERIVATION_MISMATCH" in str(_e))
for _bad_pd, _label, _moat_arg in (
        ({"rationale_ref": "裸的"}, "rationale 缺 [E:]", "narrow"),
        ({"moat_score": None, "rationale_ref": "r [E:x]"}, "缺得分", "narrow"),
        ({"moat_score": 30, "rationale_ref": "r [E:x]"}, "得分 <35 无买入结论", None)):
    try:
        _rd104.expected_return(price=10.0, scenarios=_copy104.deepcopy(_scen104),
                               hold_years=5, discount_rate=.10, moat=_moat_arg,
                               iv_growth=.03, moat_score_basis="x [E:t]",
                               prob_derivation=_bad_pd)
        check(f"C 引擎：{_label} → PROB_DERIVATION_INVALID 硬拒", False)
    except SystemExit as _e:
        check(f"C 引擎：{_label} → PROB_DERIVATION_INVALID 硬拒",
              "PROB_DERIVATION_INVALID" in str(_e))

# D. ±10pp 敏感性与翻档码（合成翻档形态：悲观 IRR=0、基准/乐观 IRR≈16.8%，
# 采用悲观 33% 时期望 IRR 11.3% ≥ r；+10pp 到 43% 时 9.6% < r → 闸门二翻档）
# 数学约束（证明见测试外注释）：闸门一过 ⇒ 基准 IRR > r，翻档只能由
# expected_irr_floor 跨越 r 触发——悲观 IRR 必须压到恰为 0（不违反③）。
_B = 20.0
_P = 0.74 * _B                      # MoS 26% > score100 门槛 25% → 闸门一过
_VP = _P / (1.10 ** 5)              # 悲观 V_H = P → IRR 恰 0（③ 过、无亏损情景）
_sc_flip = [{"name": "悲观", "value_per_share": _VP, "probability": .33},
            {"name": "基准", "value_per_share": _B, "probability": .47},
            {"name": "乐观", "value_per_share": _B, "probability": .20}]
_r_flip = _rd104.expected_return(
    price=_P, scenarios=_sc_flip, hold_years=5, discount_rate=.10,
    moat="wide", iv_growth=.06, moat_score=100,
    moat_score_basis="x [E:t]",
    prob_derivation={"moat_score": 100, "variant_perception": "neutral",
                     "rationale_ref": "r [E:x]",
                     "deviation_rationale": "论证 [E:x]"})   # 悲观 +8pp 在可调范围内
_sens = _r_flip["probability_derivation"]["sensitivity_pm10pp"]
check("D 敏感性：翻档形态被识别（PROB_SENSITIVITY_TIER_FLIP）",
      _sens["tier_flip"] and "PROB_SENSITIVITY_TIER_FLIP" in _r_flip["gate2"]["codes"])
_tiers_d = [r["tier_suggestion"] for r in _sens["rows"] if r.get("valid")]
check("D 敏感性：档位建议确实随 ±10pp 变化", len(set(_tiers_d)) > 1)

# E. DR 块（分层一致性 + 市场校准 floor）
_r_dr = _rd104.expected_return(
    price=10.0, scenarios=_copy104.deepcopy(_scen104), hold_years=5,
    discount_rate=.10, moat="narrow", iv_growth=.03,
    dr_derivation={"industry_tier": "standard", "market": "US",
                   "rationale_ref": "r [E:x]"})
check("E DR：standard 档 10% 一致 → discount_rate_derivation 输出含 rationale_ref",
      _r_dr["discount_rate_derivation"]["rate"] == 0.10
      and "[E:" in _r_dr["discount_rate_derivation"]["rationale_ref"])
check("E DR：market=US → 不收敛下限门槛切 5%（P0-04③ 口径修复）",
      _r_dr["gate2"]["no_convergence_floor"]["hurdle"] == 0.05)
_r_jp = _rd104.expected_return(
    price=10.0, scenarios=_copy104.deepcopy(_scen104), hold_years=5,
    discount_rate=.10, moat="narrow", iv_growth=.03,
    floor_hurdle=None,
    dr_derivation={"industry_tier": "standard", "market": "JP",
                   "rationale_ref": "r [E:x]"})
check("E DR：market=JP → floor 门槛 3%（JGB+3pct）",
      _r_jp["gate2"]["no_convergence_floor"]["hurdle"] == 0.03)
_r_explicit = _rd104.expected_return(
    price=10.0, scenarios=_copy104.deepcopy(_scen104), hold_years=5,
    discount_rate=.10, moat="narrow", iv_growth=.03, floor_hurdle=0.07,
    dr_derivation={"industry_tier": "standard", "market": "US",
                   "rationale_ref": "r [E:x]"})
check("E DR：显式 --floor-hurdle 优先于市场校准", 
      _r_explicit["gate2"]["no_convergence_floor"]["hurdle"] == 0.07)
try:
    _rd104.expected_return(
        price=10.0, scenarios=_copy104.deepcopy(_scen104), hold_years=5,
        discount_rate=.10, moat="narrow", iv_growth=.03,
        dr_derivation={"industry_tier": "cyclical", "market": "CN",
                       "rationale_ref": "r [E:x]"})
    check("E DR：cyclical 11% ≠ 声明 10% → DR_STRATIFIED_RATE_MISMATCH 硬拒", False)
except SystemExit as _e:
    check("E DR：cyclical 11% ≠ 声明 10% → DR_STRATIFIED_RATE_MISMATCH 硬拒",
          "DR_STRATIFIED_RATE_MISMATCH" in str(_e))
try:
    _rd104.expected_return(
        price=10.0, scenarios=_copy104.deepcopy(_scen104), hold_years=5,
        discount_rate=.10, moat="narrow", iv_growth=.03,
        dr_derivation={"industry_tier": "standard", "rationale_ref": "裸"})
    check("E DR：rationale 缺 [E:] → DR_DERIVATION_UNANCHORED 硬拒", False)
except SystemExit as _e:
    check("E DR：rationale 缺 [E:] → DR_DERIVATION_UNANCHORED 硬拒",
          "DR_DERIVATION_UNANCHORED" in str(_e))

# F. legacy 兼容：无 derivation 块输出无新键（12 案基线不动）
_r_legacy = _rd104.expected_return(
    price=10.0, scenarios=_copy104.deepcopy(_scen104), hold_years=5,
    discount_rate=.10, moat="narrow", iv_growth=.03)
check("F legacy：无块 → 无 probability_derivation/discount_rate_derivation 键",
      "probability_derivation" not in _r_legacy
      and "discount_rate_derivation" not in _r_legacy)
check("F legacy：无块 → floor 门槛仍 6%（默认不变）",
      _r_legacy["gate2"]["no_convergence_floor"]["hurdle"] == 0.06)

# G. 神华演示端到端（验收锚）
_sh_demo = json.load(open(os.path.join(
    ROOT, "backtest/601088.SH_2015-12-31", "data",
    "prob_expected_return_REQ-P1-04.json"), encoding="utf-8"))
check("G 神华演示：红队下界把悲观从原案 25% 上调到 30%（传导进入数字）",
      _sh_demo["probability_derivation"]["adopted_probabilities"]["悲观"] == 0.30
      and abs(_sh_demo["probability_derivation"]["mapped_probabilities"]["悲观"] - .30) < 1e-9)
check("G 神华演示：期望 IRR 16.60%（原案 25/50/25 为 18.46%，悲观上调的代价显式化）",
      abs(_sh_demo["expected_annualized_irr"] - 0.166) < 5e-3)
check("G 神华演示：敏感性表三行 + 稳健结论（±10pp 档位不动）",
      _sh_demo["probability_derivation"]["sensitivity_pm10pp"]["tier_flip"] is False)
_aapl_demo = json.load(open(os.path.join(
    ROOT, "backtest/AAPL_2016-04-30", "data",
    "dr_expected_return_REQ-P1-04.json"), encoding="utf-8"))
check("G AAPL 演示：US floor 门槛 5% 且 rationale_ref 落盘",
      _aapl_demo["gate2"]["no_convergence_floor"]["hurdle"] == 0.05
      and "[E:" in _aapl_demo["discount_rate_derivation"]["rationale_ref"])

# H. S7c 门禁正反
def _s7c_case(pd=None, dr=None, probs=None):
    _d = {"price": 10, "moat": "narrow", "discount_rate": 0.1,
          "hold_years": 5,
          "scenarios": [
              {"name": "悲观", "value_per_share": 5, "probability": .3,
               "method": "liquidation", "method_inputs": {}},
              {"name": "基准", "value_per_share": 20, "probability": .5,
               "method": "dcf_owner_earnings"},
              {"name": "乐观", "value_per_share": 30, "probability": .2,
               "method": "dcf_owner_earnings"}]}
    if probs:
        for _s, _p in zip(_d["scenarios"], probs):
            _s["probability"] = _p
    if pd:
        _d["probability_derivation"] = pd
    if dr:
        _d["discount_rate_derivation"] = dr
    _fd = tempfile.mkdtemp(prefix="s7c104_")
    _p = os.path.join(_fd, "s7c_case.json")
    json.dump(_d, open(_p, "w", encoding="utf-8"), ensure_ascii=False)
    _e, _w = [], []
    try:
        _ignored, _e, _w, _i = _cs104.check(_p)
    except SystemExit as _ex:
        return ["EXIT:" + str(_ex)[:40]], []
    finally:
        shutil.rmtree(_fd, ignore_errors=True)
    return _e, _w
_e_ok, _w_ok = _s7c_case(pd={"moat_score": 60, "variant_perception": "neutral",
                             "rationale_ref": "r [E:x]"})
check("H S7c：合法 probability_derivation → 无 S7c 错误（合成底稿的其余 S 检查不计）",
      not any(m.startswith("S7c") for m in _e_ok))
_e_bad, _ = _s7c_case(pd={"moat_score": 60, "rationale_ref": "裸"})
check("H S7c：rationale 缺 [E:] → PROB_DERIVATION_INVALID",
      any("S7c-VAR" in _m for _m in _e_bad))
_e_dev, _ = _s7c_case(pd={"moat_score": 60, "rationale_ref": "r [E:x]"},
                      probs=[.22, .58, .20])   # 悲观偏离映射 30.83% 达 −8.8pp 无论证
check("H S7c：偏离 >2pp 无论证 → S7c-DEV",
      any("S7c-DEV" in _m for _m in _e_dev))
_e_rng, _ = _s7c_case(pd={"moat_score": 60, "rationale_ref": "r [E:x]",
                          "deviation_rationale": "论证 [E:x]"},
                      probs=[.15, .65, .20])
check("H S7c：偏离 >10pp 即使有论证 → S7c-RANGE",
      any("S7c-RANGE" in _m for _m in _e_rng))
_e_dr, _ = _s7c_case(dr={"industry_tier": "cyclical", "rationale_ref": "r [E:x]"})
check("H S7c：DR 不一致 → DR_STRATIFIED_RATE_MISMATCH（S7c-DR）",
      any("S7c-DR" in _m and "分层折现率" in _m for _m in _e_dr))
_e_none, _ = _s7c_case()
check("H S7c：无块 → 无 S7c 消息（legacy 零新增，基线不动）",
      not any(m.startswith("S7c") for m in _e_none))

# I. 告警码注册与文档接线
check("I 七码全部注册",
      not _uc104(["DR_INDUSTRY_TIER_UNKNOWN", "DR_STRATIFIED_RATE_MISMATCH",
                  "DR_DERIVATION_UNANCHORED", "PROB_DERIVATION_INVALID",
                  "PROB_DERIVATION_MISMATCH", "PROB_DERIVATION_OUT_OF_RANGE",
                  "PROB_SENSITIVITY_TIER_FLIP"]))
_docs104 = (open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8").read()
            + open(os.path.join(ROOT, "references", "valuation-guide.md"),
                   encoding="utf-8").read()
            + open(os.path.join(ROOT, "references", "report-spec.md"),
                   encoding="utf-8").read())
check("I 文档已接线（valuation-guide 两段 + report-spec ②e + SKILL 指针）",
      all(_kw in _docs104 for _kw in
          ("REQ-P1-04", "discount_rate_derivation", "probability_derivation",
           "map_scenario_probabilities", "②e 参数依据卡",
           "discount_rate_rationale_ref")))
_prompt104 = open(os.path.join(ROOT, "backtest", "PROMPT.md"),
                  encoding="utf-8").read()
check("I PROMPT 已写入证据传导纪律段（第四批起强制）",
      "REQ-P1-04" in _prompt104 and "probability_derivation" in _prompt104
      and "DR_STRATIFIED_RATE_MISMATCH" in _prompt104
      and "PROB_SENSITIVITY_TIER_FLIP" in _prompt104)

# ═══════════════════════════════════════════════════════════════════
print("== 15 脚本接入完整性（元测试） ==")
# 教训：阶段二写了 check_market_snapshot.py、跑通了、验证它能逮住海控存量错误，
# 但**忘了在 SKILL.md 里引用它**——脚本存在 ≠ agent 会执行。SKILL.md 是 agent
# 的唯一行动依据，没被它引用的脚本就是死代码。本节从根上防止此类疏漏。
_SKILL = open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8").read()
_REFS = "".join(
    open(os.path.join(ROOT, "references", _r), encoding="utf-8").read()
    for _r in os.listdir(os.path.join(ROOT, "references")) if _r.endswith(".md"))
_DOCS = _SKILL + _REFS
# 例外清单及豁免理由（新增例外必须在此显式登记，否则测试失败）
_EXEMPT = {
    "alert_codes.py": "被 5 个脚本 import 的共享模块，不由 agent 直接调用",
    "run_backtest_assertions.py": "回放测试资产，属 backtest/ 协议而非分析主流程",
    "install-hooks.sh": "仓库开发工具（git hooks 安装），非分析流程",
    "prepare_case.py": "回放隔离协议资产（答案密封/揭示闸门），由回测会话在 Step 4 调用，非分析主流程",
    "schema_meta.py": "底稿元数据 schema 校验模块（REQ-P0-03），被 validate_data.py import",
    "migrate_schema.py": "存量底稿 meta 块迁移工具（REQ-P0-03 配套），一次性迁移脚本，非分析主流程",
    "forensic_screen.py": "Phase 0 排雷算术化（REQ-P0-02），在 SKILL.md Phase 0 与 forensic-checklist.md 中引用",
    "gate2_ab.py": "REQ-P0-04 闸门二新旧口径 A/B 回归工具，由 tests/run_tests.py 10.6 调用，非分析主流程",
}
_scripts = sorted(f for f in os.listdir(SCRIPTS)
                  if f.endswith((".py", ".sh")) and not f.startswith("_"))
# 引用可写成 `scripts/xxx.py` 或裸文件名 `xxx.py`（后者见 SKILL.md 9.5 与
# references/data-sourcing.md 对 extract_edgar_annual.py 的引用），两种都算接入。
_orphans = [f for f in _scripts if f not in _EXEMPT and f not in _DOCS]
check("无孤儿脚本（未被 SKILL.md/references 引用且未登记豁免）",
      not _orphans,
      f"孤儿脚本 {_orphans} —— 要么在文档接入，要么在 _EXEMPT 登记理由")
# 豁免理由必须非空且说明「为何不由 agent 在分析流程中直接调用」——
# 被文档提及不等于会被调用（alert_codes 是 import 的共享模块、
# install-hooks 是仓库开发工具），故不以「文档是否提及」作为判据。
check("每个豁免项都有非空理由",
      all(isinstance(v, str) and len(v) >= 10 for v in _EXEMPT.values()),
      str({k: v for k, v in _EXEMPT.items() if not (isinstance(v, str) and len(v) >= 10)}))
for _f, _why in _EXEMPT.items():
    check(f"豁免脚本仍存在：{_f}", os.path.exists(os.path.join(SCRIPTS, _f)),
          "豁免清单引用了不存在的脚本，应清理")
# 关键门禁必须带强制措辞，否则 agent 可能当成可选建议
for _f, _kw in (("check_market_snapshot.py", "强制"),
                ("check_scenarios.py", "强制"),
                ("verify_report.py", "强制"),
                ("compute_metrics.py", "强制")):
    _idx = _SKILL.find(f"scripts/{_f}")
    _ctx = _SKILL[max(0, _idx - 300):_idx + 300] if _idx >= 0 else ""
    check(f"{_f} 在 SKILL.md 中带强制措辞",
          _idx >= 0 and any(k in _ctx for k in ("强制", "必须", "才允许", "禁止")),
          f"上下文未见强制措辞，agent 可能视为可选")
# 保险管道：脚本存在则文档不得称「暂无」
check("保险管道文档与代码一致",
      not (os.path.exists(os.path.join(SCRIPTS, "compute_metrics_insurance.py"))
           and "保险/券商暂无脚本管道" in _SKILL),
      "compute_metrics_insurance.py 存在但 SKILL.md 仍称『保险暂无脚本管道』")

# ---- 回放协议一致性：PROMPT.md 不得含答案，且与代码约定同步 ----
_PROMPT = open(os.path.join(ROOT, "backtest", "PROMPT.md"), encoding="utf-8").read()
_ANSWERS_FP = os.path.join(ROOT, "backtest", "ANSWERS.md")
check("答案已移出 PROMPT.md（存在 ANSWERS.md）", os.path.exists(_ANSWERS_FP))
# 答案特征串必须只在 ANSWERS.md、不在 PROMPT.md——PROMPT 要求「全文投喂」，
# 答案留在里面等于第一批 6/6 结构性污染的根源。
_ANS_MARKERS = ["事后 5 年约 8 倍", "核心买入 / 小仓位试探",
                "事后 300 亿现金造假", "事后长期下跌超 80%"]
_leaked = [m for m in _ANS_MARKERS if m in _PROMPT]
check("PROMPT.md 中无答案明文残留", not _leaked, f"泄漏 {_leaked}")
if os.path.exists(_ANSWERS_FP):
    _ANS = open(_ANSWERS_FP, encoding="utf-8").read()
    _RUNNER_SRC = open(os.path.join(SCRIPTS, "run_backtest_assertions.py"),
                       encoding="utf-8").read()
    check("ANSWERS.md 保有已执行批次（一/二批）答案",
          all(b in _ANS for b in ("**第一批**", "**第二批**")))
    # 2026-09-09 起：未执行批次（三/四批）答案从 ANSWERS.md 密封迁出（B 档文件闸门，
    # scripts/prepare_case.py --seal），明文不得回流。
    _SEALED = os.path.join(ROOT, "backtest", "sealed_answers")
    _enc = sorted(f for f in os.listdir(_SEALED) if f.endswith(".enc")) if os.path.isdir(_SEALED) else []
    check("密封库保有第三/四/五批全部 18 案", len(_enc) == 18,
          f"密封文件 {len(_enc)} 个：{_enc}")
    check("ANSWERS.md 无未执行批次明文残留（三/四批已密封）",
          ("**第三批**" not in _ANS or "已密封" in _ANS) and "**第四批" not in _ANS.replace(
              "**第四批（假阳性专项", ""), "")
    check("PROMPT 已写入第四批假阳性专项",
          "第四批" in _PROMPT and "假阳性专项" in _PROMPT)
    check("PROMPT 已写入三条计分轨",
          "三条独立计分轨" in _PROMPT and "假阳性轨" in _PROMPT)
    check("PROMPT 已写入放松性改动红灯规则",
          "红灯规则" in _PROMPT)
    check("PROMPT 隔离协议不硬依赖 subagent（三档降级）",
          all(x in _PROMPT for x in ("A 档", "B 档", "C 档")))
    check("runner 已实现假阳性轨且不接受 known_failures 豁免",
          "false_positive_track" in _RUNNER_SRC and "假阳性绕过 known_failures 豁免" in _RUNNER_SRC)
    check("ANSWERS.md 带禁止提前阅读的警示", "Step 4 之前禁止打开" in _ANS)
# PROMPT 的档位序数须与 alert_codes.VERDICT_ORDINAL 一致
for _v, _o in AC.VERDICT_ORDINAL.items():
    check(f"PROMPT 档位序数与代码一致：{_v}={_o}",
          f"{_v}={_o}" in _PROMPT.replace(" ", ""),
          "第九节档位序数与 alert_codes.VERDICT_ORDINAL 不同步")
check("PROMPT 已写入断言 runner 用法",
      "run_backtest_assertions.py" in _PROMPT)
check("PROMPT 已写入 answer.json 与 meta.json 分离",
      "answer.json" in _PROMPT and "不含任何答案" in _PROMPT)
check("PROMPT 元问题含假阳性（第 4 问）", "假阳性成本" in _PROMPT)
check("PROMPT 已写入 2 案例门槛按「档位不匹配」计",
      "档位不匹配的案例数" in _PROMPT)

# ── 13. 隔离证据机器校验（PROMPT 第七节 B 档 + 第八之二闭环，2026-09-10）──
print("\n== 13. 隔离证据机器校验（batch>=3 硬校验） ==")
sys.path.insert(0, SCRIPTS)
import run_backtest_assertions as _RBA  # noqa: E402

_res = {"failures": []}
_RBA.check_isolation_evidence({"meta": {"batch": 2}, "dir": "/nonexistent"}, _res)
check("批次<3 案例豁免隔离证据校验（历史案已按污染降级）", not _res["failures"])

with tempfile.TemporaryDirectory() as _td:
    _res = {"failures": []}
    _RBA.check_isolation_evidence({"meta": {"batch": 3}, "dir": _td}, _res)
    check("batch=3 无 answer_source.md 判隔离缺失",
          any("answer_source.md" in f for f in _res["failures"]), str(_res["failures"]))
    # 造出 answer_source.md 后，git 时序证据不可验仍判缺失（tempdir 不在仓库）
    open(os.path.join(_td, "answer_source.md"), "w").write("x")
    _res = {"failures": []}
    _RBA.check_isolation_evidence({"meta": {"batch": 3}, "dir": _td}, _res)
    check("batch=3 有 reveal 痕迹但无 git 时序证据仍判缺失",
          any("git 首次提交" in f for f in _res["failures"]), str(_res["failures"]))

_t = _RBA._git_first_commit_time(os.path.join(ROOT, "SKILL.md"))
check("_git_first_commit_time 对仓库内文件返回正时间戳",
      isinstance(_t, int) and _t > 0, str(_t))
check("_git_first_commit_time 对不存在路径返回 None",
      _RBA._git_first_commit_time(os.path.join(ROOT, "_no_such_file_.xyz") + "/verdict.json") is None)

# ── 13.5 FP/FN 双向统计（REQ-P0-01 假阳性专项前置，2026-09-10）──
print("\n== 13.5 FP/FN 双向统计（REQ-P0-01） ==")


def _mkcase(name, exp, got, must=None, fired=None, fp_control=False, batch=1):
    return {"name": name, "dir": "/nonexistent/" + name, "meta": {"batch": batch},
            "verdict": {"verdict_ordinal": got, "codes": fired or [],
                        "codes_provenance": {"engine_derived": fired or []}},
            "answer": {"expected_verdict_set": exp, "must_trigger": must or [],
                       "fp_control": fp_control, "known_failures": []}}


_r_neg = _RBA.check_case(_mkcase("neg_ok", [0, 1], 1))
check("期望 <3 → sample_role=negative", _r_neg["sample_role"] == "negative")
check("negative 且档位 <3 → 无假阳性", not _r_neg["false_positive"])
_r_fp = _RBA.check_case(_mkcase("neg_fp", [1, 2], 3))
check("negative 且档位 ≥3 → 假阳性", _r_fp["false_positive"])
check("假阳性进入 regressions（不接受豁免）",
      any("假阳性轨红灯" in x for x in _r_fp["regressions"]))
_r_pos = _RBA.check_case(_mkcase("pos_fn", [3, 4], 2))
check("期望 ≥3 → sample_role=positive", _r_pos["sample_role"] == "positive")
check("positive 且档位 <3 → 假阴性", _r_pos["false_negative"])
check("档位=2 → abstained", _r_pos["abstained"])
_r_mix = _RBA.check_case(_mkcase("mixed", [2, 3], 2))
check("期望跨 3 → sample_role=mixed，不计 FP 也不计 FN",
      _r_mix["sample_role"] == "mixed" and not _r_mix["false_positive"]
      and not _r_mix["false_negative"])
_r_un = _RBA.check_case(_mkcase("unscored", None, 2))
check("官方不约束档位 → sample_role=unscored", _r_un["sample_role"] == "unscored")
_r_ctrl_hit = _RBA.check_case(_mkcase("ctrl_hit", [0, 1], 1, must=["CYCLE_PEAK"],
                                      fired=["NORM_CYCLE_PEAK"], fp_control=True))
check("fp_control 对照：must_trigger 命中且无假阳性 → 红灯命中",
      _r_ctrl_hit["fp_control"] and _r_ctrl_hit["fp_control_redlight_hit"])
_r_ctrl_miss = _RBA.check_case(_mkcase("ctrl_miss", [0, 1], 3, must=["CYCLE_PEAK"],
                                       fired=[], fp_control=True))
check("fp_control 对照：放行且漏判 → 红灯未命中",
      _r_ctrl_miss["fp_control"] and not _r_ctrl_miss["fp_control_redlight_hit"])

_s = _RBA.fp_fn_summary([_r_neg, _r_fp, _r_pos, _r_mix, _r_un, _r_ctrl_hit, _r_ctrl_miss])
check("FP 率分母 = negative 样本数（4）", _s["negative_n"] == 4, str(_s))
check("FP 率 = 2/4（neg_fp + ctrl_miss）", abs(_s["fp_rate"] - 0.5) < 1e-9, str(_s["fp_rate"]))
check("FN 率分母 = positive 样本数（1），FN 率 = 1/1", _s["positive_n"] == 1 and _s["fn_rate"] == 1.0)
check("对照红灯命中率 = 1/2", _s["fp_control_n"] == 2 and abs(_s["fp_control_redlight_hit_rate"] - 0.5) < 1e-9)
check("弃权率分母只含有角色样本（6，剔除 unscored）", _s["scored_n"] == 6, str(_s["scored_n"]))
_s_none = _RBA.fp_fn_summary([_r_pos])
check("无负向样本 → fp_rate=None（表述为「未被检验」而非 0）", _s_none["fp_rate"] is None)
check("目标区间不对称：FP 目标 < FN 目标",
      _RBA.FP_RATE_TARGET < _RBA.FN_RATE_TARGET)
check("PROMPT 已写入 FP/FN 目标区间与 fp_control 字段",
      "FP ≤10%、FN ≤40%" in _PROMPT and "fp_control" in _PROMPT)
check("PROMPT 已写入执行顺序 1 → 2 → 4 → 3 → 5（假阳性前置）",
      "1 → 2 → 4 → 3 → 5" in _PROMPT)
check("PROMPT 已写入假阳性对照替补池", "假阳性对照替补池" in _PROMPT)
_bl = json.load(open(os.path.join(ROOT, "backtest", "assertion_baseline.json"), encoding="utf-8"))
check("assertion_baseline.json 含 _fp_fn 段", "_fp_fn" in _bl and "false_positives" in _bl["_fp_fn"])

check("PROMPT 已写入批次收官闭环（第八之二节）",
      "八之二" in _PROMPT and "闭环六步" in _PROMPT and "移交清单" in _PROMPT)
check("闭环声明 observations.md 为未决项事实源（不设独立路线图文件）",
      "未决项台账" in _PROMPT and "不设独立路线图文件" in _PROMPT)
check("SKILL-UPGRADE.md 已删除（并入 BATCH2_FINDINGS 第八节）",
      not os.path.exists(os.path.join(ROOT, "SKILL-UPGRADE.md")))
with open(os.path.join(ROOT, "backtest", "BATCH2_FINDINGS.md"), encoding="utf-8") as _f:
    _B2 = _f.read()
check("路线图快照已并入 BATCH2_FINDINGS（8.1-8.7 齐全）",
      all(s in _B2 for s in ("## 八、优化路线图快照", "8.1 两批教训分层", "8.4 P2", "8.7 第三批检验场")))
check("BATCH2_FINDINGS 无 SKILL-UPGRADE 活引用（仅第八节来源声明可提及）",
      "见 SKILL-UPGRADE" not in _B2 and "SKILL-UPGRADE 审核建议" not in _B2)

# ===== 膨胀守卫（MAINTENANCE.md 红线的机器执行）=====
check("MAINTENANCE.md 存在（维护纪律已从 SKILL.md 迁出）",
      os.path.exists(os.path.join(ROOT, "MAINTENANCE.md")))
_skill_size = os.path.getsize(os.path.join(ROOT, "SKILL.md"))
check("SKILL.md 体积红线 ≤ 45KB（只减不增纪律）",
      _skill_size <= 45 * 1024,
      f"当前 {_skill_size / 1024:.1f}KB 超线——红线是意识闸：确有价值则按 MAINTENANCE.md 第 1 节压缩等量后上调，过程性叙述则搬批次归档")
_ref_total = sum(os.path.getsize(p)
                 for p in glob.glob(os.path.join(ROOT, "references", "*.md")))
check("references/ 总体积红线 ≤ 150KB",
      _ref_total <= 150 * 1024,
      f"当前 {_ref_total / 1024:.1f}KB 超线——过程性叙述归宿是 backtest/BATCH*_FINDINGS.md")

print()
if FAILED:
    print(f"结果：{len(FAILED)} 项失败 → {FAILED}")
    sys.exit(1)
print("结果：全部通过。")
