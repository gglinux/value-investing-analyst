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

## 四态输出：命中 / 未命中 / 数据不足 / 不适用

**这是本脚本最重要的设计决定。** 底稿现有字段只够支撑一部分条款——
`accounts_receivable`、`inventory`、`goodwill`、`other_receivables`、
`interest_income` 等在现存 43 份底稿里普遍缺失。

对缺字段的条款，脚本输出 `insufficient_data` 而**不是** `pass`。理由：
"没查出问题"和"没查"是完全不同的两件事，把后者显示成前者，等于用一个绿灯
掩盖一个盲区——这正是 REPORT.md 曾据"0 反向错误"宣称系统无假阳性的同类错误。
排雷得分（forensic_score）也因此必须携带**覆盖率**，一个只检验了 3 条条款的
满分毫无意义。

第四态 `not_applicable`：条款对该公司形态**在定义上不成立**（银行/保险的
"存贷双高"、"利率倒挂"、"短债长投"，其资产负债表本身就是存贷两高与期限错配）。
它与 `insufficient_data` 必须分开：前者不进覆盖率分母（不是盲区，是无此题），
后者进分母（是盲区）。把金融类的 V4 标成 insufficient_data 会把覆盖率压低成
"证据不足"，标成 pass 又是伪绿灯，两个都错。

## 排雷得分（供 REQ-P1-05 尾部概率使用）

    score = Σ(命中条款权重)，veto 权重 10、redflag 权重 1
    arithmetic_coverage = 算术条款已检验数 / 算术条款适用数
    manual_pending      = 人工条款未登记数（--manual 未提供）

`coverage` 保留为总口径（算术+人工），但下游 `p_tail` 映射应读
`arithmetic_coverage`：人工条款没填不是底稿盲区，是执行者还没做作业，两者
的补救路径完全不同。低覆盖率下的低分不构成"安全"证据。

## V4A 利率倒挂的复合判据（三版）

注册语义（alert_codes.py）是「< 同期存款基准 且 远低于融资成本」。一版只落了
固定 1.2% 前半句、且取数只扫 annual 行——全库底稿无一有 interest_income 行
字段，唯一一份利息收入数据（康美）躺在 phase0_arithmetic 证据链块里读不到，
这条 veto 从未在真实数据上运行过。本版起：

  ① 形态前提：与 V4 同阈值（双高）。同阈值设计保证 V4A 命中必伴随 V4 命中
     ——它是存贷双高的「验证器」，把证据从「形态可疑」升级为「经济性反常」，
     不独立排除任何公司；
  ② 利息收益率 < 同期存款基准（币种 × 年份查 DEPOSIT_BENCHMARK，--deposit-rate
     可覆盖；查不到落 insufficient_data 而非 hit——固定常数在低利率币种上会把
     真现金判成假现金，美股零利率期真现金收益率 0.1%~0.5%）；
  ③ 融资成本交叉验证：收益率 < 融资成本的一半（缺利息支出落 insufficient_data，
     veto 不半响）。

② 单独成立不 veto：公司全趴活期是懒，不是造假；② + ③ 同时成立只在一个
世界里说得通——账上的现金是假的。

## 回放时点（--as-of）

回测必须只用"当时能看到的"年报。底稿 annual 行若含 `publish_date`
（YYYY-MM-DD 前缀，允许带括号备注），`--as-of 2015-08-31` 会剔除
发布日晚于该日期的行；无 `publish_date` 的行按年报惯例视为次年 4 月 30 日发布。
不给 --as-of 则用全部行（实盘分析场景）。

用法：
    python3 scripts/forensic_screen.py <financials.json>
    python3 scripts/forensic_screen.py <financials.json> -o out.json
    python3 scripts/forensic_screen.py <financials.json> --as-of 2015-08-31
    python3 scripts/forensic_screen.py <financials.json> --deposit-rate 0.015
    python3 scripts/forensic_screen.py <financials.json> --manual manual.json
        # manual.json 提供无法从底稿推出的人工核查结果（审计意见/质押/前科等）
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alert_codes import unknown_codes  # noqa: E402
from schema_meta import _fin_type as is_financial_company  # noqa: E402

# ── 阈值（全部取自 forensic-checklist.md，改动须同步该文档）──────────
TH_CASH_RATIO = 0.25          # 货币资金/总资产
TH_DEBT_RATIO = 0.25          # 有息负债/总资产
TH_YIELD_VS_FINANCING = 0.5   # V4A：收益率须 < 融资成本的此倍数才算「远低于」
# V4A 同期存款基准（一年期，近似值——判据是「显著低于」，粗粒度即可，不需要
# 精确到 bp）。币种 × 年份段查表；查不到时用 --deposit-rate 显式指定，否则
# V4A 落 insufficient_data 而不是按全球统一常数判 hit——固定常数在低利率币种
# （USD 2010-2015 约 0.25%）上会把真现金判成假现金。改动须同步 forensic-checklist.md。
DEPOSIT_BENCHMARK = {
    "CNY": [(1990, 2014, 0.030), (2015, 2015, 0.020), (2016, 2019, 0.015),
            (2020, 2024, 0.0145), (2025, 2031, 0.011)],
    "USD": [(1990, 2015, 0.0025), (2016, 2021, 0.005), (2022, 2022, 0.020),
            (2023, 2031, 0.045)],
    "HKD": [(1990, 2022, 0.005), (2023, 2031, 0.035)],
    "JPY": [(1990, 2023, 0.0015), (2024, 2031, 0.003)],
    "EUR": [(1990, 2022, 0.002), (2023, 2031, 0.030)],
}
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

# 货币资金候选字段。苹果等美股底稿把短期投资并入现金口径
# （cash_and_short_term_investments），不加别名会让 V4/V4A/R21 在这类底稿上全部
# 落成 insufficient_data，把一个字段命名差异显示成盲区。
CASH_FIELDS = ("cash", "cash_and_equivalents", "cash_and_short_term_investments",
               "cash_and_investments")


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


def _pa(data, name, year):
    """phase0_arithmetic 证据链块兜底取值（形如 interest_income_2016 的年份键）。

    该块是执行者刻意登记的结构化排雷证据链（报告 vnum/vchart 的事实源），
    一版脚本只扫 annual 行导致全库唯一一份利息收入数据（康美）被无视、
    V4A 从未在真实数据上运行过。annual 行优先，本块兜底，两者单位同源。
    """
    pa = (data or {}).get("phase0_arithmetic")
    if isinstance(pa, dict):
        v = pa.get(f"{name}_{year}")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _currency_of(data):
    """币种：顶层 currency 优先，meta.currency（REQ-P0-03）兜底。"""
    for cur in (data.get("currency"), (data.get("meta") or {}).get("currency")):
        if isinstance(cur, str) and cur.strip():
            return cur.strip().upper()
    return None


def _benchmark_rate(currency, year):
    for lo, hi, rate in DEPOSIT_BENCHMARK.get(currency or "", []):
        if lo <= year <= hi:
            return rate
    return None


class Clause:
    """一条排雷条款的检验结果。"""

    def __init__(self, cid, code, level, name):
        self.cid, self.code, self.level, self.name = cid, code, level, name
        self.status = "insufficient_data"   # hit / pass / insufficient_data / not_applicable
        self.detail = ""
        self.missing = []
        self.hint = ""                      # insufficient_data 时给人工的定向提示

    def hit(self, detail):
        self.status, self.detail = "hit", detail
        return self

    def ok(self, detail=""):
        self.status, self.detail = "pass", detail
        return self

    def na(self, missing, hint=""):
        self.status = "insufficient_data"
        self.missing = list(missing)
        self.hint = hint
        self.detail = f"缺字段 {self.missing}——未检验（不等于通过）"
        if hint:
            self.detail += f"｜提示：{hint}"
        return self

    def not_applicable(self, reason):
        """条款对该公司形态在定义上不成立。不进覆盖率分母。"""
        self.status, self.detail = "not_applicable", f"不适用：{reason}"
        return self

    def to_dict(self):
        d = {"id": self.cid, "code": self.code, "level": self.level,
             "name": self.name, "status": self.status,
             "detail": self.detail, "missing_fields": self.missing}
        if self.hint:
            d["hint"] = self.hint
        return d


# 金融类（银行/保险/券商）在定义上不适用的条款：其资产负债表本身就是
# 高现金+高负债（V4）、利息收入是主营而非现金真实性信号（V4A）、
# 短借长贷是商业模式而非期限错配（R9）。R1 净利/OCF 背离对银行也不成立
# （OCF 受存贷款净增额主导，与利润无稳定关系）。
FIN_NOT_APPLICABLE = {
    "V4": "金融类资产负债表天然存贷两高，条款定义不成立",
    "V4A": "金融类利息收入为主营收入，非现金真实性信号",
    "R1": "金融类 OCF 由存贷款/保费净增额主导，与净利无稳定关系",
    "R9": "金融类短借长贷为商业模式本身，非期限错配",
}


# ── 条款实现 ────────────────────────────────────────────────────────
# 每条都是纯算术：同一份底稿两个人跑出同一结论。无法算术化的（审计意见、
# 造假前科、掏空迹象）走 --manual 人工输入，不在此处臆断。

def c_deposit_loan_double_high(rows, manual, ctx):
    """V4 存贷双高：货币资金与有息负债同时 > 总资产 25%。康美原型。"""
    c = Clause("V4", "P0_V4_DEPOSIT_LOAN_DOUBLE_HIGH", "veto", "存贷双高")
    last = rows[-1]
    cash = _g(last, *CASH_FIELDS)
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


def c_interest_inversion(rows, manual, ctx):
    """V4A 利率倒挂：双高形态下现金收益跑输存款基准且远低于融资成本。

    三重复合判据（设计说明见文件头 docstring）：
      ① 形态前提：与 V4 同阈值——命中必伴随 V4，不独立排除公司；
      ② 收益率 < 同期存款基准（币种×年份查表，--deposit-rate 覆盖）；
      ③ 收益率 < 融资成本一半（注册描述「远低于融资成本」的落码）。
    任一环节数据缺失 → insufficient_data，veto 不半响。
    """
    c = Clause("V4A", "P0_V4A_INTEREST_INVERSION", "veto", "利率倒挂")
    if len(rows) < 2:
        return c.na(["annual>=2"])
    last, prev = rows[-1], rows[-2]
    y = last["year"]
    data = ctx.get("data") or {}

    # ① 形态前提（与 V4 同阈值：本条是存贷双高的验证器，形态不成立即无验证对象）
    cash = _g(last, *CASH_FIELDS)
    debt = _g(last, "total_debt", "interest_bearing_debt")
    ta = _g(last, "total_assets")
    miss = [n for n, v in (("cash", cash), ("total_debt", debt), ("total_assets", ta))
            if v is None]
    if miss or not ta:
        return c.na(miss or ["total_assets>0"])
    cr, dr = cash / ta, debt / ta
    if not (cr > TH_CASH_RATIO and dr > TH_DEBT_RATIO):
        return c.ok(f"{y}：双高形态未成立（现金占比 {cr:.1%} / 有息负债占比 {dr:.1%}），无验证对象")

    # 利息收入与平均货币资金：annual 行优先，phase0_arithmetic 证据链块兜底
    ii = _g(last, "interest_income")
    if ii is None:
        ii = _pa(data, "interest_income", y)
    c0 = _g(prev, *CASH_FIELDS)
    avg = (cash + c0) / 2 if c0 is not None else _pa(data, "avg_cash", y)
    if ii is None or avg is None or avg <= 0:
        m = [n for n, v in (("interest_income", ii),) if v is None]
        if avg is None or avg <= 0:
            m.append("cash(前一年)" if c0 is None else "avg_cash>0")
        return c.na(m, hint="双高形态已成立——须补利息收入与平均货币资金完成验证"
                            "（可登记于 phase0_arithmetic.interest_income_<year>）")
    yld = ii / avg

    # ② 同期存款基准
    bm = ctx.get("deposit_rate") or _benchmark_rate(_currency_of(data), y)
    if bm is None:
        ccy = _currency_of(data) or "币种未知"
        return c.na(["deposit_rate"],
                    hint=f"{ccy} {y} 无存款基准，用 --deposit-rate 指定后完成验证")
    if yld >= bm:
        return c.ok(f"{y}：利息收益率 {yld:.2%} ≥ 同期存款基准 {bm:.2%}"
                    f"——现金在赚存款级利息，无倒挂")

    # ③ 融资成本交叉验证：利息支出/平均有息负债（预计算值或现场算）
    ie = _g(last, "interest_expense")
    if ie is None:
        ie = _pa(data, "interest_expense", y)
    cost = _pa(data, "cost_of_debt", y)
    if cost is None and ie is not None:
        d0 = _g(prev, "total_debt", "interest_bearing_debt")
        avg_d = (debt + d0) / 2 if d0 is not None else _pa(data, "avg_ibd", y)
        if avg_d is not None and avg_d > 0:
            cost = ie / avg_d
    if cost is None:
        return c.na(["interest_expense"],
                    hint="收益率已低于存款基准，缺利息支出无法完成「远低于融资成本」验证"
                         "（可登记 phase0_arithmetic.interest_expense_<year>）")
    if yld >= TH_YIELD_VS_FINANCING * cost:
        return c.ok(f"{y}：收益率 {yld:.2%} 低于基准 {bm:.2%}，但未低于融资成本 "
                    f"{cost:.2%} 的一半——倒挂证据不完整，仅提示")
    return c.hit(f"{y}：双高形态（现金占比 {cr:.1%} / 有息负债占比 {dr:.1%}）下"
                 f"利息收益率仅 {yld:.2%}——低于存款基准 {bm:.2%}，更远低于融资成本 "
                 f"{cost:.2%}（借钱付 {cost:.1%} 却让现金趴着只收 {yld:.1%}，"
                 f"年利差损失约 {(cost - yld) * avg:.0f}）——经济性不自洽，"
                 f"假现金拿不出真利息")


def c_ocf_profit_divergence(rows, manual, ctx):
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


def c_receivables_surge(rows, manual, ctx):
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


def c_inventory_surge(rows, manual, ctx):
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


def c_goodwill_heavy(rows, manual, ctx):
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


def c_other_receivables(rows, manual, ctx):
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


def c_financing_vs_return(rows, manual, ctx):
    """R11 累计融资 > 累计分红回购的 3 倍（抽血机器）。"""
    c = Clause("R11", "P0_R11_FINANCING_VS_RETURN", "redflag", "融资与回报失衡")
    fin = sum(x for x in (_g(r, "equity_raised", "financing_raised") for r in rows)
              if x is not None)
    div = sum(abs(x) for x in (_g(r, "dividends_paid") for r in rows) if x is not None)
    buyback = sum(abs(x) for x in (_g(r, "buyback", "share_repurchase") for r in rows)
                  if x is not None)
    has_fin = any(_g(r, "equity_raised", "financing_raised") is not None for r in rows)
    if not has_fin:
        # 退化路径：无融资字段时用股本膨胀做**提示**而非命中。
        # 一版曾把「股本 >1.5 倍且无分红回购」直接算 hit，结果 Zoom（IPO 前
        # 优先股转普通股，股本膨胀是上市而非抽血）与苹果（1:7 拆股、股本
        # 膨胀 7 倍但回购全球第一）都被误杀。股本变动不区分 IPO/拆股/增发，
        # 不够格做红旗判据，只够格提醒人去查融资总额。
        s0 = _g(rows[0], "shares_diluted")
        s1 = _g(rows[-1], "shares_diluted")
        if s0 and s1 and s0 > 0 and s1 / s0 > 1.5:
            ratio = s1 / s0
            return c.na(["equity_raised"],
                        hint=f"股本自 {rows[0]['year']} 增至 {ratio:.2f} 倍"
                             f"{'且期间无分红回购' if (div + buyback) <= 0 else ''}"
                             f"——须人工核实是增发抽血还是 IPO/拆股（后者不构成红旗）")
        return c.na(["equity_raised"])
    if (div + buyback) <= 0:
        return c.hit(f"累计融资 {fin:.0f}，累计分红回购为 0——纯抽血") if fin > 0 else c.ok("无融资无回报")
    ratio = fin / (div + buyback)
    if ratio > TH_FIN_VS_RETURN:
        return c.hit(f"累计融资/(分红+回购) = {ratio:.1f} > {TH_FIN_VS_RETURN}"
                     f"——长期从市场取钱多于回馈")
    return c.ok(f"融资/回报 = {ratio:.1f}")


def c_nonrecurring(rows, manual, ctx):
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


def c_short_debt_long_asset(rows, manual, ctx):
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


def c_going_concern(rows, manual, ctx):
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
        cash = _g(last, *CASH_FIELDS)
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


_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")


def _publish_date(row):
    """annual 行的发布日（YYYY-MM-DD）。无 publish_date 时按年报惯例视为次年 4 月 30 日。

    底稿里的 publish_date 常带括号备注（"2015-03-26(分红预案日,年报同期,惯例推算)"），
    只取前缀日期。
    """
    raw = row.get("publish_date")
    if isinstance(raw, str):
        m = _DATE_RE.match(raw)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    y = row.get("year")
    if isinstance(y, int):
        return f"{y + 1}-04-30"
    return None


def filter_as_of(rows, as_of):
    """只保留在 as_of（YYYY-MM-DD）当天或之前已发布的年报行。

    回测的时点纪律：2015-08-31 看茅台，不能用 2015 年报（2016-03 才出）。
    没有 publish_date 的行按惯例日推断——宁可少用一年，不可偷看一年。
    返回 (kept_rows, dropped_years)。
    """
    if not as_of:
        return rows, []
    if not _DATE_RE.match(as_of):
        raise ValueError(f"--as-of 须为 YYYY-MM-DD，得到 {as_of!r}")
    kept, dropped = [], []
    for r in rows:
        pd = _publish_date(r)
        if pd is None or pd <= as_of:
            kept.append(r)
        else:
            dropped.append(r.get("year"))
    return kept, dropped


def screen(data, manual=None, as_of=None, deposit_rate=None):
    manual = manual or {}
    rows = sorted([r for r in (data.get("annual") or []) if isinstance(r, dict)],
                  key=lambda r: r.get("year", 0))
    rows, dropped_years = filter_as_of(rows, as_of)
    if not rows:
        return {"error": "annual 为空，无法排雷"
                         + (f"（--as-of {as_of} 剔除了 {dropped_years}）" if dropped_years else "")}

    is_fin = is_financial_company(data)
    ctx = {"data": data, "deposit_rate": deposit_rate}
    clauses = []
    for f in ARITHMETIC_CLAUSES:
        c = f(rows, manual, ctx)
        if is_fin and c.cid in FIN_NOT_APPLICABLE:
            c.not_applicable(FIN_NOT_APPLICABLE[c.cid])
        clauses.append(c)
    n_arith = len(clauses)

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

    arith, manual_cl = clauses[:n_arith], clauses[n_arith:]
    hits = [c for c in clauses if c.status == "hit"]
    checked = [c for c in clauses if c.status in ("hit", "pass")]
    na = [c for c in clauses if c.status == "insufficient_data"]
    not_app = [c for c in clauses if c.status == "not_applicable"]

    veto_hits = [c for c in hits if c.level == "veto"]
    redflag_hits = [c for c in hits if c.level == "redflag"]
    score = VETO_WEIGHT * len(veto_hits) + REDFLAG_WEIGHT * len(redflag_hits)

    # 覆盖率三个口径，分母都剔除 not_applicable（无此题 ≠ 盲区）：
    #   coverage             总口径（算术+人工），向后兼容
    #   arithmetic_coverage  算术条款：底稿字段盲区，补救=补底稿字段
    #   manual_pending       人工条款未登记数：执行者作业未做，补救=填 --manual
    applicable = [c for c in clauses if c.status != "not_applicable"]
    coverage = len(checked) / len(applicable) if applicable else 0.0
    arith_app = [c for c in arith if c.status != "not_applicable"]
    arith_checked = [c for c in arith_app if c.status in ("hit", "pass")]
    arithmetic_coverage = len(arith_checked) / len(arith_app) if arith_app else 0.0
    manual_pending = [c.cid for c in manual_cl if c.status == "insufficient_data"]

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

    arith_na = [c for c in arith_app if c.status == "insufficient_data"]
    if arithmetic_coverage < 0.5:
        basis += (f"｜⚠ 算术覆盖率仅 {arithmetic_coverage:.0%}（{len(arith_na)} 条因缺字段未检验）"
                  f"——本结论证据强度低，不得表述为『已排雷』")
    if manual_pending:
        basis += f"｜人工条款未登记 {len(manual_pending)} 条 {manual_pending}（须 --manual 补齐）"
    if is_fin:
        basis += f"｜金融类：{[c.cid for c in not_app]} 不适用"
    if dropped_years:
        basis += f"｜--as-of {as_of} 剔除未发布年份 {dropped_years}"

    return {
        "verdict": verdict,
        "basis": basis,
        "alert_codes": codes,
        "forensic_score": score,
        "score_basis": f"veto×{VETO_WEIGHT}×{len(veto_hits)} + "
                       f"redflag×{REDFLAG_WEIGHT}×{len(redflag_hits)}",
        "coverage": round(coverage, 3),
        "coverage_basis": f"{len(checked)}/{len(applicable)} 条已检验（不含不适用）",
        "arithmetic_coverage": round(arithmetic_coverage, 3),
        "arithmetic_coverage_basis": f"{len(arith_checked)}/{len(arith_app)} 条算术条款已检验",
        "manual_pending": manual_pending,
        "company_is_financial": is_fin,
        "as_of": as_of,
        "rows_used": [r.get("year") for r in rows],
        "rows_dropped_by_as_of": dropped_years,
        "counts": {"hit": len(hits), "pass": len(checked) - len(hits),
                   "insufficient_data": len(na), "not_applicable": len(not_app),
                   "total": len(clauses)},
        "insufficient_fields": sorted({m for c in na for m in c.missing}),
        "hints": {c.cid: c.hint for c in clauses if c.hint},
        "clauses": [c.to_dict() for c in clauses],
        # 下游（REQ-P1-05 尾部概率）必须同时读 score 与 arithmetic_coverage：
        # 低覆盖率下的低分不是「安全」的证据。
        "downstream_note": "p_tail 映射须同时消费 forensic_score 与 arithmetic_coverage；"
                           "arithmetic_coverage < 0.5 时低分不构成安全证据；"
                           "manual_pending 非空时结论未闭环",
    }


def main():
    ap = argparse.ArgumentParser(description="Phase 0 排雷条款算术化（REQ-P0-02）")
    ap.add_argument("input", help="财务底稿 JSON")
    ap.add_argument("--manual", help="人工核查结果 JSON（审计意见/质押/前科等）")
    ap.add_argument("--as-of", dest="as_of",
                    help="回放时点 YYYY-MM-DD：只用该日已发布的年报行（回测必填）")
    ap.add_argument("--deposit-rate", dest="deposit_rate", type=float,
                    help="V4A 同期存款基准利率（小数，如 0.015），覆盖币种×年份查表")
    ap.add_argument("-o", "--output", help="结果 JSON 输出路径")
    args = ap.parse_args()

    if args.deposit_rate is not None and not (0 < args.deposit_rate < 0.5):
        print("❌ --deposit-rate 须为 (0, 0.5) 内的小数（如 0.015 = 1.5%）")
        sys.exit(2)

    data = json.load(open(args.input, encoding="utf-8"))
    manual = json.load(open(args.manual, encoding="utf-8")) if args.manual else {}
    res = screen(data, manual, as_of=args.as_of, deposit_rate=args.deposit_rate)

    if res.get("error"):
        print(f"❌ {res['error']}")
        sys.exit(2)

    icon = "⛔" if res["verdict"].startswith("排除") else "✅"
    print(f"{icon} Phase 0 排雷：{res['verdict']}")
    print(f"   依据：{res['basis']}")
    print(f"   排雷得分 {res['forensic_score']}（{res['score_basis']}）｜"
          f"算术覆盖率 {res['arithmetic_coverage']:.0%}（{res['arithmetic_coverage_basis']}）｜"
          f"人工待登记 {len(res['manual_pending'])} 条")
    if res["as_of"]:
        print(f"   回放时点 {res['as_of']}：使用年份 {res['rows_used']}"
              + (f"，剔除 {res['rows_dropped_by_as_of']}" if res['rows_dropped_by_as_of'] else ""))
    if res["alert_codes"]:
        print(f"   告警码：{res['alert_codes']}")
    print()
    for c in res["clauses"]:
        mark = {"hit": "⛔", "pass": "  ", "insufficient_data": "？",
                "not_applicable": "－"}[c["status"]]
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
