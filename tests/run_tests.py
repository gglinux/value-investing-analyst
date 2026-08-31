#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_tests.py — 脚本引擎回归测试（任何人改 scripts/ 前后必须各跑一次）

覆盖场景：
  1. 正常公司：指标齐全，基期=当期
  2. 周期高位：强制正常化基期 + alert
  3. 周期低位：向上正常化基期 + alert
  4. 当期亏损：正常化不适用，基期为 None
  5. 金融股门控：compute_metrics 必须拒绝执行
  6. capex 拆分：披露口径优先于启发式
  7. reverse_dcf：forward-value 与 expected-return 输出合理性
  7.5 股息口径：含息 IRR 叠加正确、亏损概率含股息缓冲、门槛比较用含息口径
  7.6 银行管道：compute_metrics_bank 指标正确、非银行拒绝、低拨备 alert
  8. verify_report 负向：篡改的 vnum/vchart 与幽灵 [E:] 指针必须被逮住

用法：python3 tests/run_tests.py   （在 skill 根目录运行）
退出码：0 全过；1 有失败。
"""
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

print("== 7.5 股息口径（高股息误杀防线） ==")
with tempfile.TemporaryDirectory() as td:
    fp0 = os.path.join(td, "er0.json")
    fp5 = os.path.join(td, "er5.json")
    scen = "悲观:80:0.3,基准:105:0.5,乐观:130:0.2"
    p0 = run(["expected-return", "--price", "100", "--hold-years", "5",
              "--scenarios", scen, "-o", fp0])
    p5 = run(["expected-return", "--price", "100", "--hold-years", "5",
              "--scenarios", scen, "--dividend-yield", "0.06", "-o", fp5])
    check("含息模式运行成功", p0.returncode == 0 and p5.returncode == 0)
    if p0.returncode == 0 and p5.returncode == 0:
        e0, e5 = json.load(open(fp0)), json.load(open(fp5))
        check("含息IRR = 不含息IRR + 股息率",
              abs(e5["expected_annualized_irr_incl_div"]
                  - (e5["expected_annualized_irr"] + 0.06)) < 1e-9)
        check("含息后亏损概率下降（股息缓冲）",
              e5["loss_probability"] < e0["loss_probability"],
              f"{e0['loss_probability']} -> {e5['loss_probability']}")
        check("门槛比较用含息口径", e5["beats_index"] ==
              (e5["expected_annualized_irr_incl_div"] > e5["index_hurdle"]))

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

    def vr(path):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_data.py"), path],
                              capture_output=True, text=True)
    r1 = vr(gp)
    check("好银行底稿通过", r1.returncode == 0, r1.stdout[-200:])
    r2 = vr(bp)
    check("坏银行底稿被拒", r2.returncode == 1, r2.stdout[-200:])
    check("不良率超界被逮住", "不良率" in r2.stdout, r2.stdout[-200:])

print()
if FAILED:
    print(f"结果：{len(FAILED)} 项失败 → {FAILED}")
    sys.exit(1)
print("结果：全部通过。")
