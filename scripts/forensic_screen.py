#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forensic_screen.py — Phase 0 排雷条款算术化（REQ-P0-02）

## 为什么需要这个脚本

`references/forensic-checklist.md` 有 30 项排雷条款，此前**全部依赖人工核对**，
脚本层一条算术条款都没有。康美案的"存贷双高"是执行者人工登记后才进 codes 的。

问题不在于人工核对做不对，而在于它**在真实使用中必然被跳过或敷衍**。回测里能
命中，是因为执行者知道这是回测、知道这题有雷。真实分析一家看起来正常的公司时，
没有人会把 30 项逐条算一遍。排雷是"不错买"的第一道也是最重要的一道防线，
把它交给耐心和警觉，等于没有防线。

## 三态输出：命中 / 未命中 / 数据不足

**这是本脚本最重要的设计决定。** 底稿现有字段只够支撑一部分条款——
`accounts_receivable`、`inventory`、`goodwill`、`other_receivables`、
`interest_income` 等在现存 43 份底稿里普遍缺失。

对缺字段的条款，脚本输出 `insufficient_data` 而**不是** `pass`。理由：
"没查出问题"和"没查"是完全不同的两件事，把后者显示成前者，等于用一个绿灯
掩盖一个盲区——这正是 REPORT.md 曾据"0 反向错误"宣称系统无假阳性的同类错误。
排雷得分（forensic_score）也因此必须携带**覆盖率**，一个只检验了 3 条条款的
满分毫无意义。

## 排雷得分（供 REQ-P1-05 尾部概率使用）

    score = Σ(命中条款权重)，veto 权重 10、redflag 权重 1
    coverage = 已检验条款数 / 可算术条款总数

下游 `p_tail` 映射必须同时读这两个值：低覆盖率下的低分不构成"安全"证据。

用法：
    python3 scripts/forensic_screen.py <financials.json>
    python3 scripts/forensic_screen.py <financials.json> -o out.json
    python3 scripts/forensic_screen.py <financials.json> --manual manual.json
        # manual.json 提供无法从底稿推出的人工核查结果（审计意见/质押/前科等）
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alert_codes import unknown_codes  # noqa: E402

# ── 阈值（全部取自 forensic-checklist.md，改动须同步该文档）──────────
TH_CASH_RATIO = 0.25          # 货币资金/总资产
TH_DEBT_RATIO = 0.25          # 有息负债/总资产
TH_INTEREST_YIELD = 0.012     # 利息收入/平均货币资金 低于此值 = 利率倒挂
TH_NI_OVER_OCF = 1.5          # 净利润 > OCF 的倍数
TH_NI_OVER_OCF_YEARS = 3      # 连续年数
TH_AR_VS_REV = 1.5            # 应收增速/收入增速
TH_AR_YEARS = 2
TH_INV_VS_REV = 1.5           # 存货增速/收入增速
TH_GOODWILL_EQUITY = 0.30     # 商誉/净资产
TH_OTHER_AR_ASSETS = 0.05     # 其他应收/总资产
TH_FIN_VS_RETURN = 3.0        # 累计融资/(累计分红+回购)
TH_NONRECURRING = 0.60        # 扣非净利/净利 低于此值
TH_SHORT_DEBT_RATIO = 0.60    # 短期借款/有息负债，配合长期资产占比

VETO_WEIGHT, REDFLAG_WEIGHT = 10, 1


def _g(row, *names):
    """按候选字段名取值（底稿命名不完全统一）。"""
    for n in names:
        v = row.get(n)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _growth(series):
    """相邻年增速序列，跳过基数 <=0 的年份。"""
    out = []
    for a, b in zip(series, series[1:]):
        if a is not None and b is not None and a > 0:
            out.append(b / a - 1.0)
        else:
            out.append(None)
    return out


class Clause:
    """一条排雷条款的检验结果。"""

    def __init__(self, cid, code, level, name):
        self.cid, self.code, self.level, self.name = cid, code, level, name
        self.status = "insufficient_data"   # hit / pass / insufficient_data
        self.detail = ""
        self.missing = []

    def hit(self, detail):
        self.status, self.detail = "hit", detail
        return self

    def ok(self, detail=""):
        self.status, self.detail = "pass", detail
        return self

    def na(self, missing):
        self.status = "insufficient_data"
        self.missing = list(missing)
        self.detail = f"缺字段 {self.missing}——未检验（不等于通过）"
        return self

    def to_dict(self):
        return {"id": self.cid, "code": self.code, "level": self.level,
                "name": self.name, "status": self.status,
                "detail": self.detail, "missing_fields": self.missing}


# ── 条款实现 ────────────────────────────────────────────────────────
# 每条都是纯算术：同一份底稿两个人跑出同一结论。无法算术化的（审计意见、
# 造假前科、掏空迹象）走 --manual 人工输入，不在此处臆断。

def c_deposit_loan_double_high(rows, manual):
    """V4 存贷双高：货币资金与有息负债同时 > 总资产 25%。康美原型。"""
    c = Clause("V4", "P0_V4_DEPOSIT_LOAN_DOUBLE_HIGH", "veto", "存贷双高")
    last = rows[-1]
    cash = _g(last, "cash", "cash_and_equivalents")
    debt = _g(last, "total_debt", "interest_bearing_debt")
    ta = _g(last, "total_assets")
    miss = [n for n, v in (("cash", cash), ("total_debt", debt), ("total_assets", ta))
            if v is None]
    if miss or not ta:
        return c.na(miss or ["total_assets>0"])
    cr, dr = cash / ta, debt / ta
    if cr > TH_CASH_RATIO and dr > TH_DEBT_RATIO:
        return c.hit(f"{last['year']}：货币资金/总资产 {cr:.1%} > {TH_CASH_RATIO:.0%} "
                     f"且 有息负债/总资产 {dr:.1%} > {TH_DEBT_RATIO:.0%}"
                     f"——趴着大量现金却大举借债，需管理层合理解释")
    return c.ok(f"{last['year']}：现金占比 {cr:.1%} / 有息负债占比 {dr:.1%}")


def c_interest_inversion(rows, manual):
    """V4A 利率倒挂：利息收入/平均货币资金 < 1.2%。假现金拿不出真利息。"""
    c = Clause("V4A", "P0_V4A_INTEREST_INVERSION", "veto", "利率倒挂")
    if len(rows) < 2:
        return c.na(["annual>=2"])
    last, prev = rows[-1], rows[-2]
    ii = _g(last, "interest_income")
    c1, c0 = _g(last, "cash", "cash_and_equivalents"), _g(prev, "cash", "cash_and_equivalents")
    if ii is None or c1 is None or c0 is None:
        return c.na([n for n, v in (("interest_income", ii), ("cash", c1)) if v is None]
                    or ["cash(前一年)"])
    avg = (c1 + c0) / 2
    if avg <= 0:
        return c.na(["平均货币资金>0"])
    y = ii / avg
    if y < TH_INTEREST_YIELD:
        return c.hit(f"{last['year']}：利息收入/平均货币资金 = {y:.2%} < "
                     f"{TH_INTEREST_YIELD:.1%}——账上现金产不出应有利息，现金真实性存疑")
    return c.ok(f"{last['year']}：利息收益率 {y:.2%}")


def c_ocf_profit_divergence(rows, manual):
    """R1 净利润连续 3 年 > OCF 的 1.5 倍。"""
    c = Clause("R1", "P0_R1_OCF_PROFIT_DIVERGENCE", "redflag", "利润与经营现金流背离")
    recent = rows[-TH_NI_OVER_OCF_YEARS:]
    if len(recent) < TH_NI_OVER_OCF_YEARS:
        return c.na([f"annual>={TH_NI_OVER_OCF_YEARS}"])
    bad, detail = [], []
    for r in recent:
        ni, ocf = _g(r, "net_income"), _g(r, "ocf", "operating_cash_flow")
        if ni is None or ocf is None:
            return c.na(["net_income/ocf"])
        detail.append(f"{r['year']}: NI {ni:.0f} / OCF {ocf:.0f}")
        # OCF <= 0 且有正利润：比 1.5 倍更严重的形态，直接算命中
        if ni > 0 and (ocf <= 0 or ni > TH_NI_OVER_OCF * ocf):
            bad.append(r["year"])
    if len(bad) == TH_NI_OVER_OCF_YEARS:
        return c.hit(f"连续 {TH_NI_OVER_OCF_YEARS} 年净利润 > {TH_NI_OVER_OCF}×经营现金流"
                     f"（{bad}）——利润没有变成钱｜{'; '.join(detail)}")
    return c.ok(f"背离年份 {bad or '无'}（未达连续 {TH_NI_OVER_OCF_YEARS} 年）")


def c_receivables_surge(rows, manual):
    """R2 应收增速 > 收入增速 1.5 倍且连续 2 年。"""
    c = Clause("R2", "P0_R2_RECEIVABLES_SURGE", "redflag", "应收账款剪刀差")
    ar = [_g(r, "accounts_receivable", "receivables") for r in rows]
    rev = [_g(r, "revenue") for r in rows]
    if any(x is None for x in ar):
        return c.na(["accounts_receivable"])
    g_ar, g_rev = _growth(ar), _growth(rev)
    bad = [rows[i + 1]["year"] for i in range(len(g_ar))
           if g_ar[i] is not None and g_rev[i] is not None
           and g_rev[i] > 0 and g_ar[i] > TH_AR_VS_REV * g_rev[i]]
    if len(bad) >= TH_AR_YEARS and _consecutive(bad):
        return c.hit(f"应收增速 > {TH_AR_VS_REV}×收入增速，连续年份 {bad}"
                     f"——收入可能靠放宽信用政策堆出")
    return c.ok(f"剪刀差年份 {bad or '无'}")


def c_inventory_surge(rows, manual):
    """R5 存货增速远超收入增速。"""
    c = Clause("R5", "P0_R5_INVENTORY_SURGE", "redflag", "存货剪刀差")
    inv = [_g(r, "inventory", "inventories") for r in rows]
    rev = [_g(r, "revenue") for r in rows]
    if any(x is None for x in inv):
        return c.na(["inventory"])
    g_inv, g_rev = _growth(inv), _growth(rev)
    bad = [rows[i + 1]["year"] for i in range(len(g_inv))
           if g_inv[i] is not None and g_rev[i] is not None
           and g_rev[i] > 0 and g_inv[i] > TH_INV_VS_REV * g_rev[i]]
    if len(bad) >= 2 and _consecutive(bad):
        return c.hit(f"存货增速 > {TH_INV_VS_REV}×收入增速，连续年份 {bad}"
                     f"——须核对是否有扩产逻辑支撑，否则可能是滞销或虚增存货")
    return c.ok(f"剪刀差年份 {bad or '无'}")


def c_goodwill_heavy(rows, manual):
    """R6 商誉/净资产 > 30%。"""
    c = Clause("R6", "P0_R6_GOODWILL_HEAVY", "redflag", "商誉占比过高")
    last = rows[-1]
    gw, eq = _g(last, "goodwill"), _g(last, "total_equity")
    if gw is None:
        return c.na(["goodwill"])
    if eq is None or eq <= 0:
        return c.na(["total_equity>0"])
    ratio = gw / eq
    if ratio > TH_GOODWILL_EQUITY:
        return c.hit(f"{last['year']}：商誉/净资产 = {ratio:.1%} > {TH_GOODWILL_EQUITY:.0%}"
                     f"——报表由并购堆成，减值风险集中")
    return c.ok(f"{last['year']}：商誉占净资产 {ratio:.1%}")


def c_other_receivables(rows, manual):
    """R7 其他应收款异常大额（资金体外循环通道）。"""
    c = Clause("R7", "P0_R7_OTHER_RECEIVABLES", "redflag", "其他应收款异常")
    last = rows[-1]
    oar, ta = _g(last, "other_receivables"), _g(last, "total_assets")
    if oar is None:
        return c.na(["other_receivables"])
    if ta is None or ta <= 0:
        return c.na(["total_assets>0"])
    ratio = oar / ta
    if ratio > TH_OTHER_AR_ASSETS:
        return c.hit(f"{last['year']}：其他应收/总资产 = {ratio:.1%} > "
                     f"{TH_OTHER_AR_ASSETS:.0%}——资金体外循环的常见通道")
    return c.ok(f"{last['year']}：其他应收占比 {ratio:.1%}")


def c_financing_vs_return(rows, manual):
    """R11 累计融资 > 累计分红回购的 3 倍（抽血机器）。"""
    c = Clause("R11", "P0_R11_FINANCING_VS_RETURN", "redflag", "融资与回报失衡")
    fin = sum(x for x in (_g(r, "equity_raised", "financing_raised") for r in rows)
              if x is not None)
    div = sum(abs(x) for x in (_g(r, "dividends_paid") for r in rows) if x is not None)
    buyback = sum(abs(x) for x in (_g(r, "buyback", "share_repurchase") for r in rows)
                  if x is not None)
    has_fin = any(_g(r, "equity_raised", "financing_raised") is not None for r in rows)
    if not has_fin:
        # 退化路径：无融资字段时用股本膨胀做代理，仅在明显膨胀时给提示
        s0 = _g(rows[0], "shares_diluted")
        s1 = _g(rows[-1], "shares_diluted")
        if s0 and s1 and s0 > 0 and s1 / s0 > 1.5 and (div + buyback) <= 0:
            return c.hit(f"股本自 {rows[0]['year']} 增至 {s1 / s0:.2f} 倍且期间无分红回购"
                         f"——代理判据（缺 equity_raised 字段），须人工核实融资总额")
        return c.na(["equity_raised"])
    if (div + buyback) <= 0:
        return c.hit(f"累计融资 {fin:.0f}，累计分红回购为 0——纯抽血") if fin > 0 else c.ok("无融资无回报")
    ratio = fin / (div + buyback)
    if ratio > TH_FIN_VS_RETURN:
        return c.hit(f"累计融资/(分红+回购) = {ratio:.1f} > {TH_FIN_VS_RETURN}"
                     f"——长期从市场取钱多于回馈")
    return c.ok(f"融资/回报 = {ratio:.1f}")


def c_nonrecurring(rows, manual):
    """R4 扣非净利占比 < 60%。"""
    c = Clause("R4", "P0_R4_NONRECURRING_PROP", "redflag", "非经常性损益撑利润")
    last = rows[-1]
    ni = _g(last, "net_income")
    dn = _g(last, "net_income_deducted", "net_income_ex_nonrecurring")
    if dn is None:
        return c.na(["net_income_deducted"])
    if ni is None or ni <= 0:
        return c.ok("当期净利非正，本条不适用")
    ratio = dn / ni
    if ratio < TH_NONRECURRING:
        return c.hit(f"{last['year']}：扣非净利/净利 = {ratio:.1%} < {TH_NONRECURRING:.0%}"
                     f"——利润主要来自非经常性损益")
    return c.ok(f"{last['year']}：扣非占比 {ratio:.1%}")


def c_short_debt_long_asset(rows, manual):
    """R9 短债长投：短期借款占有息负债过半且长期资产占比高。"""
    c = Clause("R9", "P0_R9_SHORT_DEBT_LONG_ASSET", "redflag", "短债长投期限错配")
    last = rows[-1]
    sd = _g(last, "short_term_debt", "short_debt")
    td = _g(last, "total_debt")
    nca = _g(last, "non_current_assets", "long_term_assets")
    ta = _g(last, "total_assets")
    if sd is None or td is None:
        return c.na(["short_term_debt/total_debt"])
    if td <= 0:
        return c.ok("无有息负债")
    sr = sd / td
    if nca is not None and ta and ta > 0:
        lr = nca / ta
        if sr > TH_SHORT_DEBT_RATIO and lr > 0.5:
            return c.hit(f"{last['year']}：短期借款占有息负债 {sr:.1%}，"
                         f"长期资产占总资产 {lr:.1%}——短钱长用，流动性错配")
        return c.ok(f"短债占比 {sr:.1%} / 长期资产占比 {lr:.1%}")
    if sr > TH_SHORT_DEBT_RATIO:
        return c.hit(f"{last['year']}：短期借款占有息负债 {sr:.1%} > "
                     f"{TH_SHORT_DEBT_RATIO:.0%}（缺长期资产字段，未做第二重判据）")
    return c.ok(f"短债占比 {sr:.1%}")


def c_going_concern(rows, manual):
    """R21 持续经营存疑：净资产为负，或 OCF 连续 ≥2 年为负且现金不足覆盖。

    柯达 2011 原型。此前 Phase 0 对这一形态零覆盖——它不是造假，
    所以造假类条款全部沉默；但它是「本金可能永久损失」的最直白算术信号，
    在排雷层拦下比等到估值层的不收敛下限更早、更便宜。
    """
    c = Clause("R21", "P0_R21_GOING_CONCERN", "redflag", "持续经营存疑")
    last = rows[-1]
    eq = _g(last, "total_equity")
    if eq is not None and eq < 0:
        return c.hit(f"{last['year']}：净资产为负（{eq:.0f}）——资不抵债，"
                     f"股东权益已被侵蚀殆尽")
    recent = rows[-2:]
    if len(recent) < 2:
        return c.na(["annual>=2"])
    ocfs = [_g(r, "ocf", "operating_cash_flow") for r in recent]
    if any(o is None for o in ocfs):
        return c.na(["ocf"])
    if all(o < 0 for o in ocfs):
        cash = _g(last, "cash", "cash_and_equivalents")
        burn = abs(sum(ocfs) / len(ocfs))
        seq = ", ".join(f"{r['year']}:{o:.0f}" for r, o in zip(recent, ocfs))
        if cash is not None and burn > 0:
            years_left = cash / burn
            if years_left < 3:
                return c.hit(
                    f"经营现金流连续 2 年为负（{seq}），"
                    f"现金 {cash:.0f} / 年均失血 {burn:.0f} = 仅可支撑 {years_left:.1f} 年")
            return c.ok(f"OCF 连续为负但现金可支撑 {years_left:.1f} 年")
        return c.hit(f"经营现金流连续 2 年为负（{seq}），缺现金字段无法评估可支撑年限")
    if eq is None:
        return c.na(["total_equity"])
    return c.ok(f"{last['year']}：净资产 {eq:.0f}，OCF 未连续为负")


def _consecutive(years):
    """年份列表中是否存在连续两年。"""
    s = sorted(set(years))
    return any(b - a == 1 for a, b in zip(s, s[1:]))


# 人工输入类条款：无法从财务底稿算出，必须由执行者核查后填 --manual
MANUAL_CLAUSES = [
    ("V1", "P0_V1_AUDIT_OPINION", "veto", "审计非标意见", "audit_opinion_adverse"),
    ("V2", "P0_V2_FRAUD_HISTORY", "veto", "财务造假前科", "fraud_history"),
    ("V3", "P0_V3_PLEDGE_HIGH", "veto", "实控人高比例质押", "pledge_over_70pct"),
    ("V5", "P0_V5_AUDITOR_CFO_CHURN", "veto", "审计师/CFO 频繁更换", "auditor_cfo_churn"),
    ("V6", "P0_V6_CONTROLLER_TUNNELING", "veto", "大股东掏空迹象", "controller_tunneling"),
]

ARITHMETIC_CLAUSES = [
    c_deposit_loan_double_high, c_interest_inversion, c_ocf_profit_divergence,
    c_receivables_surge, c_inventory_surge, c_goodwill_heavy,
    c_other_receivables, c_financing_vs_return, c_nonrecurring,
    c_short_debt_long_asset, c_going_concern,
]


def screen(data, manual=None):
    manual = manual or {}
    rows = sorted([r for r in (data.get("annual") or []) if isinstance(r, dict)],
                  key=lambda r: r.get("year", 0))
    if not rows:
        return {"error": "annual 为空，无法排雷"}

    clauses = [f(rows, manual) for f in ARITHMETIC_CLAUSES]

    for cid, code, level, name, key in MANUAL_CLAUSES:
        c = Clause(cid, code, level, name)
        v = manual.get(key)
        if v is None:
            c.na([f"manual.{key}"])
        elif v:
            c.hit(f"人工核查登记：{manual.get(key + '_note') or '已确认命中'}")
        else:
            c.ok(f"人工核查登记：未发现（{manual.get(key + '_note') or '无备注'}）")
        clauses.append(c)

    hits = [c for c in clauses if c.status == "hit"]
    checked = [c for c in clauses if c.status in ("hit", "pass")]
    na = [c for c in clauses if c.status == "insufficient_data"]

    veto_hits = [c for c in hits if c.level == "veto"]
    redflag_hits = [c for c in hits if c.level == "redflag"]
    score = VETO_WEIGHT * len(veto_hits) + REDFLAG_WEIGHT * len(redflag_hits)
    coverage = len(checked) / len(clauses) if clauses else 0.0

    codes = sorted({c.code for c in hits})
    unknown = unknown_codes(codes)
    if unknown:
        raise KeyError(f"未注册的告警码 {unknown}，请先在 scripts/alert_codes.py 登记")

    # 结论：veto 命中即排除；红旗 >=3 默认排除（checklist 规则）
    if veto_hits:
        verdict = "排除"
        basis = f"一票否决项命中 {len(veto_hits)} 条：{[c.cid for c in veto_hits]}"
    elif len(redflag_hits) >= 3:
        verdict = "排除（红旗 ≥3）"
        basis = f"红旗命中 {len(redflag_hits)} 条：{[c.cid for c in redflag_hits]}"
    else:
        verdict = "通过"
        basis = f"红旗命中 {len(redflag_hits)} 条（< 3）"

    if coverage < 0.5:
        basis += (f"｜⚠ 覆盖率仅 {coverage:.0%}（{len(na)} 条因缺字段未检验）"
                  f"——本结论证据强度低，不得表述为『已排雷』")

    return {
        "verdict": verdict,
        "basis": basis,
        "alert_codes": codes,
        "forensic_score": score,
        "score_basis": f"veto×{VETO_WEIGHT}×{len(veto_hits)} + "
                       f"redflag×{REDFLAG_WEIGHT}×{len(redflag_hits)}",
        "coverage": round(coverage, 3),
        "coverage_basis": f"{len(checked)}/{len(clauses)} 条已检验",
        "counts": {"hit": len(hits), "pass": len(checked) - len(hits),
                   "insufficient_data": len(na), "total": len(clauses)},
        "insufficient_fields": sorted({m for c in na for m in c.missing}),
        "clauses": [c.to_dict() for c in clauses],
        # 下游（REQ-P1-05 尾部概率）必须同时读 score 与 coverage：
        # 低覆盖率下的低分不是「安全」的证据。
        "downstream_note": "p_tail 映射须同时消费 forensic_score 与 coverage；"
                           "coverage < 0.5 时低分不构成安全证据",
    }


def main():
    ap = argparse.ArgumentParser(description="Phase 0 排雷条款算术化（REQ-P0-02）")
    ap.add_argument("input", help="财务底稿 JSON")
    ap.add_argument("--manual", help="人工核查结果 JSON（审计意见/质押/前科等）")
    ap.add_argument("-o", "--output", help="结果 JSON 输出路径")
    args = ap.parse_args()

    data = json.load(open(args.input, encoding="utf-8"))
    manual = json.load(open(args.manual, encoding="utf-8")) if args.manual else {}
    res = screen(data, manual)

    if res.get("error"):
        print(f"❌ {res['error']}")
        sys.exit(2)

    icon = "⛔" if res["verdict"].startswith("排除") else "✅"
    print(f"{icon} Phase 0 排雷：{res['verdict']}")
    print(f"   依据：{res['basis']}")
    print(f"   排雷得分 {res['forensic_score']}（{res['score_basis']}）｜"
          f"覆盖率 {res['coverage']:.0%}（{res['coverage_basis']}）")
    if res["alert_codes"]:
        print(f"   告警码：{res['alert_codes']}")
    print()
    for c in res["clauses"]:
        mark = {"hit": "⛔", "pass": "  ", "insufficient_data": "？"}[c["status"]]
        print(f" {mark} [{c['id']:<4}] {c['name']:<16} {c['detail']}")
    if res["insufficient_fields"]:
        print(f"\n未检验条款所缺字段：{res['insufficient_fields']}")
        print("  注意：`insufficient_data` ≠ 通过。补齐字段后重跑才能主张已排雷。")

    if args.output:
        json.dump(res, open(args.output, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"\n已写入 {args.output}")
    sys.exit(0)


if __name__ == "__main__":
    main()
