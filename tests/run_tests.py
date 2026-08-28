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

print()
if FAILED:
    print(f"结果：{len(FAILED)} 项失败 → {FAILED}")
    sys.exit(1)
print("结果：全部通过。")
