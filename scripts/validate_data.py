#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_data.py — 数据底稿入口校验器（Phase 1 收尾强制运行，通过后才允许进入 Phase 2）

定位：verify_report.py 保证"报告 = 底稿"，本脚本保证"底稿 ≈ 事实"。
对标准格式财务底稿 JSON 做四类确定性检查：

1. 口径注册表：头字段 company/currency/unit 缺失即报错；
   accounting_standard/fiscal_year_end/fx_basis 缺失给警告（跨市场竞对对比必填）。
2. 三表勾稽：total_assets ≈ total_liabilities + total_equity（容差 1%）；
   gross_profit ≤ revenue；净利润量级 sanity。
3. 单位一致性：百万/亿混淆检测——净利润绝对值 > 收入 1.5 倍、OCF > 收入 2 倍等
   数量级异常即报错/警告。
4. 突变检测：核心科目同比变动超过 ±50% 必须在 spike_notes 中标注原因
   （真实业务变化 or 数据修正），未标注不放行。

以及 P0 双源交叉验证的机器检查：
5. crosscheck 区块：最近 3 个年度的 revenue/net_income/ocf/shares_diluted 必须与
   官方披露原文（年报 PDF/XBRL）核对并登记（含 source 出处），与 annual 行容差 1%。
   缺失 crosscheck 区块 → 校验失败。

输入格式 = compute_metrics.py 的标准底稿 JSON，外加可选/必填扩展字段：
{
  ..., "accounting_standard": "CAS|IFRS|US-GAAP",
  "fiscal_year_end": "12-31",
  "fx_basis": "报告币种未换算 / 按年末汇率折算 等（涉及换算时必填）",
  "annual": [ { ..., "total_assets": 1500.0, "total_liabilities": 700.0,
                "gross_profit": 400.0 }, ... ],
  "spike_notes": { "2020.revenue": "疫情停产，年报 MD&A p.12 确认" },
  "crosscheck": [
    { "year": 2024, "source": "2024年报 PDF p.45（巨潮）",
      "revenue": 1000.0, "net_income": 150.0, "ocf": 180.0, "shares_diluted": 100.0 },
    ...
  ]
}

用法：
    python3 validate_data.py <financials.json> [--skip-crosscheck] [--consensus consensus.json]
    --skip-crosscheck 仅限竞对公司使用（命门科目核对只强制 A 公司），
    使用后校验结果标注"未做双源核对"，报告脚注必须披露。
    --consensus 一致预期 JSON（可选）：做 C5 回落信号 lint（EPS[FY+2] < EPS[FY+1] →
    告警"利润含一次性成分嫌疑"，提示估值基期通用化处理）。

门禁补充（2026-08-31 GOOG/TSM 实证后新增）：
A1 年度覆盖哨兵：过了法定申报死线，底稿最新年报年仍落后 → 报错；
    只有 manifest.json 登记 `official_filing_missing`（写明年份与原因）才可显式豁免。
    起源：GOOG 案例中 FY2025 10-K 实际已申报（XBRL 概念标签切换导致抽取脚本静默丢年），
    当时被合理化为"未申报"并写进 manifest——合理化的异常必须被强制人工解释。
A2 命门原文级：最新年报年的 crosscheck.source 必须指向官方披露原文
    （10-K/20-F/审计报表/年报/EDGAR/巨潮/披露易/XBRL/官网），
    "四季加总/接口/估算"等降级来源仅在年报法定申报窗口内（死线前+60天）给警告，过后报错。
A3 一次性损益哨兵：底稿含 `interim` 区块且当期累计净利超过上年全年净利的 85%，
    或自报同比增幅超过 100% → spike_notes 必须含 `<年份>.net_income` 或
    `interim.net_income` 键，剖析一次性成分（GOOG H1'26 净利超上年全年是触发原型）。
退出码：0 通过（可含警告）；1 存在错误，禁止进入 Phase 2。
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

SPIKE_KEYS = ["revenue", "net_income", "ocf", "capex", "total_equity", "shares_diluted"]
SPIKE_THRESHOLD = 0.5
CROSSCHECK_KEYS = ["revenue", "net_income", "ocf", "shares_diluted"]
CROSSCHECK_MIN_YEARS = 3
TOL = 0.01  # 1% 相对容差

# A1/A2 共用：官方披露原文来源特征（降级来源：季度加总/接口/估算/推算）
OFFICIAL_SOURCE_HINTS = ["10-K", "10K", "20-F", "20F", "审计", "年报", "annual report",
                         "Annual Report", "EDGAR", "巨潮", "披露易", "cninfo", "hkexnews",
                         "XBRL", "官网"]
DOWNGRADE_SOURCE_HINTS = ["加总", "接口", "估算", "推算"]


def issuer_wait_days(data):
    """按会计准则推断年报法定申报等待天数（年报日之后）。"""
    std = str(data.get("accounting_standard") or "").upper()
    if "US" in std and "GAAP" in std:
        return 75   # 美股本国申报人 10-K（large accelerated 60 天，取宽容 75）
    return 120      # FPI 20-F / A股 / 港股：4 个月


def annual_deadline(year, fiscal_md, wait_days):
    """年报法定申报死线：次年会计年度起始日 + 等待天数。"""
    y0 = int(year) + 1
    base = date(y0, 1, 1)   # 简化：以自然年为近似财年（财年日用于提示，不影响死线判断主体）
    return base + timedelta(days=wait_days)


def load_manifest(dirname):
    fp = os.path.join(dirname, "manifest.json")
    if not os.path.exists(fp):
        return None
    try:
        return json.load(open(fp, "r", encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def is_official_source(src):
    """判定 crosscheck.source 是否指向官方披露原文。

    ⚠️ 顺序敏感（v2.11 修正）：旧实现先扫降级词再扫官方词，只要出现
    "接口/加总/估算"任一字样就直接判降级——导致 “20-F 2025 披露接口值
    （Q4 业绩公告交叉）” 这类**确实引用了 20-F 原文**、只是措辞里带了
    "接口" 的来源被误判为降级，PDD 归档底稿因此 A2 报错。

    正确语义：官方原文标识（10-K/20-F/年报/EDGAR/巨潮…）是**强证据**，
    一旦命中即认定为官方；降级词只在**没有任何官方标识**时才作为判定依据。
    否则会出现"越写清楚数据怎么来的、越容易被门禁误杀"的反向激励——
    这会逼分析师把来源写得含糊，直接摧毁溯源纪律本身。
    """
    s = str(src or "")
    has_official = any(h in s for h in OFFICIAL_SOURCE_HINTS)
    if has_official:
        return True
    return False


def source_wording_note(src):
    """官方来源但措辞混入降级词时，返回提示语（仅警告，不阻断）。"""
    s = str(src or "")
    if not any(h in s for h in OFFICIAL_SOURCE_HINTS):
        return None
    hit = [h for h in DOWNGRADE_SOURCE_HINTS if h in s]
    if hit:
        return (f"来源措辞含降级词 {hit}，但已标明官方原文——"
                "请明确该科目究竟取自原文还是接口，避免口径混淆")
    return None


def rel_diff(a, b):
    if a is None or b is None:
        return None
    base = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / base


def main():
    ap = argparse.ArgumentParser(description="数据底稿入口校验")
    ap.add_argument("input", help="标准格式财务底稿 JSON")
    ap.add_argument("--skip-crosscheck", action="store_true",
                    help="跳过双源核对检查（仅限竞对公司，报告须披露）")
    ap.add_argument("--consensus", default=None,
                    help="可选：一致预期 JSON 路径，启用 C5 回落信号 lint")
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    errors, warns = [], []

    # 0. 行业类型门控（金融股禁走通用管道）
    FINANCIAL_TYPES = {"bank", "银行", "broker", "券商",
                       "securities", "金融", "financial"}
    BANK_TYPES = {"bank", "银行"}
    INSURANCE_TYPES = {"insurance", "保险", "保险集团", "寿险", "财险",
                      "insurance_group", "life", "p&c"}
    ctype = str(data.get("company_type", "")).strip().lower()
    is_bank = ctype in BANK_TYPES
    is_insurance = ctype in INSURANCE_TYPES
    if ctype in FINANCIAL_TYPES and not is_bank and not is_insurance:
        errors.append(f"行业门控：company_type={data.get('company_type')} 为金融类，"
                      "通用底稿/compute_metrics 管道不适用（利息收支/浮存金/准备金口径不同），"
                      "请按 metric-playbook 银行/保险专属指标集单独建稿")
    elif not is_insurance and not ctype:
        warns.append("行业门控：company_type 缺失——Phase 0 必须判定商业模式类型（metric-playbook 十类）"
                     "并写入底稿头字段，金融类严禁走通用管道")

    # 1. 口径注册表
    for k in ("company", "currency", "unit"):
        if not data.get(k):
            errors.append(f"口径注册表：头字段 `{k}` 缺失")
    for k in ("accounting_standard", "fiscal_year_end"):
        if not data.get(k):
            warns.append(f"口径注册表：`{k}` 缺失——跨市场竞对对比时必填，图表脚注需注明")

    # 1.5 前视偏差防线：年报数据必须记录发布日（publish_date），复盘校准时
    # 按发布日截断"当时市场知道什么"——2025 年报 3 月底才发布，1 月的分析不该用它。
    rows = sorted(data.get("annual", []), key=lambda r: r.get("year", 0))
    missing_pub = [r.get("year") for r in rows[-3:] if not r.get("publish_date")]
    if missing_pub:
        warns.append(f"前视偏差：最近年度 {missing_pub} 缺 `publish_date`（年报发布日，"
                     "A股接口有 InfoPublDate 现成可用；EDGAR 用 filing date）——"
                     "复盘校准协议依赖该字段按发布日截断信息集")

    # 1.6 信息时效检查：分析日距最新登记的财报发布日超过 100 天时，
    # 极可能存在未消化的新季报/盈利预告（腾讯 AI capex +176% 是季中爆出的教训）。
    # 提示分析师核对最新季报，核对结果写入 manifest 的 latest_quarter_checked。
    pub_dates = [r.get("publish_date") for r in rows if r.get("publish_date")]
    if pub_dates:
        try:
            # 注意：严禁在此处写 `from datetime import date` ——
            # 函数内的局部 import 会把 `date` 变成整个函数的局部名，
            # 遮蔽模块级 import；当底稿没有任何 publish_date 时该语句不执行，
            # 后续 A1 哨兵的 date.today() 即抛 UnboundLocalError，
            # 且因裸崩在 report() 之前，表现为「0 错误 0 警告 + 退出码 1」的
            # 静默失败（NVDA 底稿正是此形态）。模块级已 import，直接用。
            latest_pub = max(datetime.strptime(d, "%Y-%m-%d").date() for d in pub_dates)
            gap = (date.today() - latest_pub).days
            if gap > 100:
                warns.append(
                    f"信息时效：底稿最新发布日 {latest_pub}（距今 {gap} 天 > 100 天）——"
                    "必须核对期间的季报/盈利预告是否有未消化的剧变，"
                    "核对结果写入 manifest 的 `latest_quarter_checked` 字段")
        except ValueError:
            warns.append("信息时效：publish_date 格式无法解析（应为 YYYY-MM-DD），时效检查跳过")
    if not rows:
        errors.append("annual 为空")
        report(errors, warns, args)
        return

    # === A1 年度覆盖哨兵：过了法定申报死线最新年报年仍落后 →
    # 禁止静默用季度加总/老数据充当年报年报年（GOOG 实证：抽取脚本静默丢年被
    # 合理化为"未申报"写进 manifest，异常没有触发人工复核，数据等级从 A 静默降 B）===
    data_dir = os.path.dirname(os.path.abspath(args.input))
    manifest = load_manifest(data_dir)
    max_year = int(rows[-1].get("year"))
    wait = issuer_wait_days(data)
    expected = max_year
    today = date.today()
    for cand in range(max_year, max_year + 2):
        if annual_deadline(cand, data.get("fiscal_year_end"), wait) <= today:
            expected = cand
    if expected > max_year:
        exemption = (manifest or {}).get("official_filing_missing") or {}
        exempt_years = exemption.get("years", exemption.get("year"))
        if isinstance(exempt_years, int):
            exempt_years = [exempt_years]
        exempt_hit = isinstance(exempt_years, list) and expected in exempt_years
        msg = (f"年度覆盖哨兵(A1)：底稿最新年报年 {max_year}，"
               f"{expected} 年报法定申报死线（+{wait}天）已过——"
               "底稿缺该年年报数据。若公司已延迟申报，请在 manifest.json 登记 "
               '`official_filing_missing`（year/years + reason）；'
               "否则严禁以季度加总/上年数据充当年报年报年")
        if args.skip_crosscheck:
            warns.append(msg + "（竞对底稿降级为警告）")
        elif exempt_hit:
            warns.append(msg + f"（manifest 已登记豁免: {exemption.get('reason', '无原因说明')}）")
        else:
            errors.append(msg)

    # === A3 一次性损益哨兵：interim 累计净利超上年全年 85% 或同比 >100%，
    # spike_notes 必须剖析一次性成分（GOOG H1'26 净利 > FY2025 全年为触发原型）===
    interim = data.get("interim")
    if isinstance(interim, dict) and interim.get("net_income") is not None:
        latest_ni = rows[-1].get("net_income")
        im_ni = float(interim["net_income"])
        period = str(interim.get("period") or "")
        im_year = period[:4] if period[:4].isdigit() else str(max_year + 1)
        key_hit = any(str(k).startswith(f"{im_year}.") or "interim" in str(k)
                      for k in (data.get("spike_notes") or {}))
        hit_super = latest_ni and abs(im_ni) > abs(float(latest_ni)) * 0.85
        yoy = interim.get("yoy_net_income_growth")
        hit_yoy = isinstance(yoy, (int, float)) and yoy > 1.0
        if (hit_super or hit_yoy) and not key_hit:
            if hit_super:
                ratio = im_ni / float(latest_ni)
                why = f"当期累计净利已达上年全年的 {ratio:.0%}"
            else:
                why = f"自报同比 +{yoy:.0%}"
            errors.append(f"一次性损益哨兵(A3)：interim({period}) 净利 {im_ni} "
                          f"{why}——极可能含一次性损益，spike_notes 必须含 "
                          f"`{im_year}.net_income` 或 `interim.net_income` 键逐项剖析"
                          "（估值基期须做通用化处理，见 valuation-guide 自定义基期清单）")

    # === C5 一致预期回落信号（软门）：EPS[FY+2] < EPS[FY+1] →
    # 卖方"自认利润虚胖"，提示通用化基期（GOOG FY2027 < FY2026 为识别原型）===
    if getattr(args, "consensus", None):
        try:
            cons = json.load(open(args.consensus, "r", encoding="utf-8"))
            eps_map = cons.get("eps_consensus_usd") or cons.get("eps_consensus") or {}
            pts = sorted(((int(y), float(v["avg"])) for y, v in eps_map.items()
                          if str(y).isdigit() and isinstance(v, dict) and v.get("avg")),
                         key=lambda t: t[0])
            for i in range(len(pts) - 1):
                (y1, e1), (y2, e2) = pts[i], pts[i + 1]
                if e2 < e1 * 0.99:
                    warns.append(f"一致预期回落信号(C5)：EPS[{y2}]={e2} < EPS[{y1}]={e1}——"
                                 "卖方普遍认定利润含一次性成分，估值基期必须做通用化处理并外部锚定")
                    break
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            warns.append(f"consensus lint：{args.consensus} 解析失败（{e}），C5 检查跳过")

    def g(row, key):
        v = row.get(key)
        return float(v) if v is not None else None

    # === 银行/保险专属旁路：通用三表勾稽/单位突变/OCF突变对金融无意义，改查金融勾稽 ===
    if is_bank:
        for r in rows:
            y = r.get("year")
            opin, nii, nonii = g(r, "operating_income"), g(r, "net_interest_income"), g(r, "non_interest_income")
            ni, ta, te, gl = g(r, "net_income"), g(r, "total_assets"), g(r, "total_equity"), g(r, "gross_loans")
            npl, prov = g(r, "npl_balance"), g(r, "provision_balance")
            if opin is None:
                errors.append(f"{y}: 银行底稿缺 `operating_income`（营业收入）")
            if nii is not None and nonii is not None and opin is not None:
                if abs(opin - (nii + nonii)) / max(opin, 1e-9) > 0.05:
                    errors.append(f"{y}: 营收({opin}) ≠ 利息净收入+非息收入({nii}+{nonii})，偏差>5%")
            if npl is not None and gl:
                nplr = npl / gl
                if not (0.0 <= nplr <= 0.15):
                    errors.append(f"{y}: 不良率 {nplr:.2%} 超 0~15% 合理带，疑似单位/科目错误")
            if prov is not None and npl:
                cov = prov / npl
                if cov < 1.0:
                    warns.append(f"{y}: 拨备覆盖率 {cov:.0%} < 100%（监管红线 120%~150%），资产质量警报")
            if ni is not None and opin is not None and ni > opin * 0.6:
                warns.append(f"{y}: 净利率({ni}/{opin}) 超 60%，银行一般 25%~45%，请复核单位")
        warns.append("已启用银行专属校验（跳过三表勾稽/单位突变/OCF突变，改查营收拆分/不良率/拨备覆盖）")
    elif is_insurance:
        # 保险专属勾稽（v2.8 新增）：EV/NBV/综合成本率/偿付能力/股息/利差联查
        warns.append("已启用保险专属校验（跳过实业三表勾稽/单位突变，改查 EV/NBV/COR/偿付能力/股息覆盖）")
        for r in rows:
            y = r.get("year")
            ev, nbv = g(r, "embedded_value"), g(r, "nbv")
            cori, solv = g(r, "combined_ratio_pnc"), g(r, "solvency_ratio")
            roe, div_ps = g(r, "roe"), g(r, "dividend_per_share")
            ni, rev, eq = g(r, "net_income"), g(r, "revenue"), g(r, "total_equity")
            # EV 必须为正且通常大于净资产（有效业务价值 EV - 净资产 = 有效业务价值）；
            # 若 EV < 净资产×0.8，要么是口径错（如拿了分部 EV），要么是商誉/假设问题
            if ev is not None and eq is not None and ev < eq * 0.8:
                warns.append(f"{y}: 集团内含价值({ev}) < 归母净资产×80%({eq})——"
                             "请核对是集团 EV 还是寿健险分部 EV，后者需注明口径")
            # NBV 必须为正但必须合理（< EV 的 30%，超 30% 是糟糕的前视/双重计算信号）
            if nbv is not None and ev is not None and nbv > ev * 0.30:
                errors.append(f"{y}: 一年新业务价值({nbv}) > 内含价值×30%({ev}×0.3)——"
                              "NBV 占比过高，请核对是否把 '当期销售新业务价值' 误作 '一年新业务价值'")
            # COR 只能在 85%~115% 之间；<85% 是超常规盈利或数据错，>115% 是承保亏损
            if cori is not None and not (0.85 <= cori <= 1.15):
                errors.append(f"{y}: 综合成本率 {cori:.1%} 超 85%~115% 合理带，疑似单位或口径错误")
            # 偿付能力充足率 < 100% 是监管红线；< 150% 是监管关注区
            if solv is not None and solv < 1.0:
                errors.append(f"{y}: 综合偿付能力充足率 {solv:.0%} < 100%——监管底线突破，一票否决")
            elif solv is not None and solv < 1.5:
                warns.append(f"{y}: 综合偿付能力充足率 {solv:.0%} < 150%——进入监管关注区，须跟踪")
            # ROE 对保险是核心质量指标（中位 12%）；<5% 是经营警报
            if roe is not None and roe < 0.05:
                warns.append(f"{y}: ROE {roe:.1%} < 5%——保险经营水准低，请复核利差/承保利润真实性")
        # 保险命门突变检测（独立索引循环，避免嵌套里 index() 错位）
        notes_i = data.get("spike_notes", {}) or {}
        for i in range(1, len(rows)):
            prev, cur = rows[i - 1], rows[i]
            y = cur.get("year")
            for k in ["embedded_value", "nbv", "dividend_per_share", "solvency_ratio"]:
                a, b = g(prev, k), g(cur, k)
                if a is None or b is None or abs(a) < 1e-9:
                    continue
                chg = (b - a) / abs(a)
                if abs(chg) > 0.5 and f"{y}.{k}" not in notes_i:
                    errors.append(f"{y}: 保险命门 `{k}` 同比变动 {chg:+.0%} > ±50%（保险行业正常区间<30%），"
                                  f"spike_notes 缺少 `{y}.{k}` 的原因标注")
    else:
        # 2 & 3. 勾稽与单位 sanity（逐年）
        for r in rows:
            y = r.get("year")
            rev, ni = g(r, "revenue"), g(r, "net_income")
            gp, ocf = g(r, "gross_profit"), g(r, "ocf")
            ta, tl, eq = g(r, "total_assets"), g(r, "total_liabilities"), g(r, "total_equity")

            if ta is not None and tl is not None and eq is not None:
                d = rel_diff(ta, tl + eq)
                if d is not None and d > TOL:
                    errors.append(f"{y}: 勾稽失败 资产({ta}) ≠ 负债({tl})+权益({eq})，偏差 {d:.1%}"
                                  "（注意：total_equity 若为归母口径需并入少数股东权益后再核）")
            elif ta is None or tl is None:
                warns.append(f"{y}: 缺 total_assets/total_liabilities，无法做三表勾稽")

            if rev is not None and gp is not None and gp > rev * (1 + TOL):
                errors.append(f"{y}: 毛利({gp}) > 收入({rev})，疑似科目或单位错误")
            if rev is not None and ni is not None and abs(ni) > abs(rev) * 1.5:
                errors.append(f"{y}: |净利润|({ni}) > 收入×1.5({rev})，疑似百万/亿单位混淆")
            if rev is not None and ocf is not None and abs(ocf) > abs(rev) * 2:
                warns.append(f"{y}: |经营现金流|({ocf}) > 收入×2，量级异常，请复核")

        # 4. 突变检测
        notes = data.get("spike_notes", {}) or {}
        for i in range(1, len(rows)):
            prev, cur = rows[i - 1], rows[i]
            y = cur.get("year")
            for k in SPIKE_KEYS:
                a, b = g(prev, k), g(cur, k)
                if a is None or b is None or abs(a) < 1e-9:
                    continue
                chg = (b - a) / abs(a)
                if abs(chg) > SPIKE_THRESHOLD:
                    key = f"{y}.{k}"
                    if key not in notes:
                        errors.append(f"{y}: `{k}` 同比变动 {chg:+.0%} 超过 ±50%，"
                                      f"spike_notes 缺少 `{key}` 的原因标注（业务变化或数据修正）")

    # 5. 双源交叉验证（银行命门科目改：营业收入/归母净利润；实业：收入/归母净利润/经营现金流/股本）
    if args.skip_crosscheck:
        warns.append("已跳过双源核对（--skip-crosscheck）：仅限竞对公司；报告脚注必须披露该公司未做原文核对")
    else:
        cc = data.get("crosscheck") or []
        by_year = {r.get("year"): r for r in rows}
        if len(cc) < CROSSCHECK_MIN_YEARS:
            errors.append(f"双源核对：crosscheck 区块不足 {CROSSCHECK_MIN_YEARS} 个年度"
                          "（命门科目须与年报原文核对：收入/归母净利润/经营现金流/股本）")
        xkeys = ["operating_income", "net_income"] if is_bank else CROSSCHECK_KEYS
        for entry in cc:
            y = entry.get("year")
            src = entry.get("source")
            if not src:
                errors.append(f"双源核对 {y}: 缺 source 出处（年报页码/XBRL 标签）")
            elif not is_official_source(src):
                # A2 命门原文级：最新年报年在窗口期外禁止降级来源充数
                in_window = annual_deadline(int(y), None, wait) + timedelta(days=60) > today
                a2msg = (f"命门原文级(A2) {y}: source「{src}」非官方披露原文"
                         "（须 10-K/20-F/审计报表/年报/EDGAR/巨潮/披露易/XBRL）；"
                         "季度加总/接口估算仅在法定申报死线 +60 天窗口期内允许")
                if y == max_year and not in_window:
                    errors.append(a2msg)
                else:
                    warns.append(a2msg)
            else:
                note = source_wording_note(src)
                if note:
                    warns.append(f"双源核对 {y}: {note}")
            row = by_year.get(y)
            if row is None:
                errors.append(f"双源核对 {y}: annual 中无该年度数据")
                continue
            for k in xkeys:
                ov = entry.get(k)
                dv = row.get(k)
                if ov is None:
                    warns.append(f"双源核对 {y}: 官方值缺 `{k}`")
                    continue
                d = rel_diff(float(ov), float(dv) if dv is not None else None)
                if d is None:
                    errors.append(f"双源核对 {y}: 底稿缺 `{k}`，无法比对")
                elif d > TOL:
                    errors.append(f"双源核对 {y}: `{k}` 底稿({dv}) vs 官方({ov}) 偏差 {d:.1%}，"
                                  "以官方披露为准修正底稿并在 spike_notes 记录差异原因")

    # 5.5 校验覆盖率哨兵（v2.11 新增）——最危险的失效形态：
    # "0 错误"可能意味着"检查全通过"，也可能意味着"因为缺数据，检查根本没跑"。
    # 二者在旧输出里完全无法区分：缺 total_assets 只给一条 WARN，
    # 而三表勾稽是最核心的取证检查，缺科目即整年跳过。
    # 实证：GOOG 11 年仅 2 年可勾稽、TSM 11 年仅 2 年，两者都以 "0 错误/通过" 收尾，
    # 勾稽覆盖率 18% —— 报告却据此宣称"数据已通过入口校验"。
    # 现按覆盖率分级：主体公司核心检查覆盖率过低即阻断，逼数据补齐或显式豁免。
    if not is_bank and not is_insurance:
        n = len(rows)
        tie_ok = sum(1 for r in rows
                     if r.get("total_assets") is not None
                     and r.get("total_liabilities") is not None
                     and r.get("total_equity") is not None)
        cov = tie_ok / n if n else 0.0
        cov_msg = (f"校验覆盖率哨兵：三表勾稽仅覆盖 {tie_ok}/{n} 年（{cov:.0%}）——"
                   "缺 total_assets/total_liabilities 的年份直接跳过勾稽，"
                   "『0 错误』在这些年份不代表数据可信，只代表没检查。")
        exempt = (manifest or {}).get("reconciliation_coverage_waiver") or {}
        if cov < 0.6:
            if args.skip_crosscheck:
                warns.append(cov_msg + "（竞对底稿降级为警告）")
            elif exempt.get("reason"):
                warns.append(cov_msg + f"（manifest 已登记豁免: {exempt.get('reason')}）")
            else:
                errors.append(
                    cov_msg + " 主体公司要求 ≥60%：请补齐资产负债表科目"
                    "（EDGAR XBRL 的 Assets/Liabilities/StockholdersEquity 概念，"
                    "注意含 NCI 口径），或在 manifest.json 登记 "
                    "`reconciliation_coverage_waiver`（reason 写明为何无法补齐）")
        elif cov < 1.0:
            warns.append(cov_msg + " 已达 60% 门槛，但仍建议补全。")

    # 5.6 信息广度与溯源元数据哨兵（v2.11 新增）
    # 数据质量不只是"数字对不对"，还包括"该看的信息有没有看"。
    # SKILL.md/data-sourcing.md 明确要求：对立面检索（做空报告/造假/处罚/诉讼）
    # 必须执行且未命中也要在 manifest 登记；manifest 须逐文件记录来源等级。
    # 实证：10 个归档案例中登记了 adversarial_check 的只有 1 个（GOOG），
    # weibo 的 manifest 只有 files 一个键 —— 纯文档纪律没有执行力。
    # 信息流里全是公司自己说的话，是最贵的错误的起点，故升级为可机器校验的关卡。
    if not args.skip_crosscheck:
        mf = manifest or {}
        mf_s = json.dumps(mf, ensure_ascii=False)
        if not manifest:
            warns.append("信息广度哨兵：data/ 下无 manifest.json——"
                         "无法核验数据来源等级、对立面检索、季报核对等过程留痕")
        else:
            ADV_HINTS = ["adversarial", "对立面", "做空", "short_seller",
                         "做空报告", "财务造假", "监管处罚", "集体诉讼"]
            if not any(h in mf_s for h in ADV_HINTS):
                warns.append(
                    "信息广度哨兵：manifest 未见对立面检索留痕（Phase 0 强制步）——"
                    "须检索『公司名 + 做空报告/财务造假/监管处罚/集体诉讼/审计意见』，"
                    "命中进排雷清单，未命中也要登记 `adversarial_check`"
                    "（含检索日期与结论）。信息流里全是公司自己说的话")
            if "files" not in mf:
                warns.append("溯源元数据：manifest 缺 `files` 清单（逐文件记录来源/等级/"
                             "抓取时间/覆盖期间/是否过入口校验）")
            else:
                files = mf.get("files")
                graded = 0
                total_f = 0
                if isinstance(files, dict):
                    items = files.values()
                elif isinstance(files, list):
                    items = files
                else:
                    items = []
                for it in items:
                    total_f += 1
                    s = json.dumps(it, ensure_ascii=False) if not isinstance(it, str) else it
                    if any(g in s for g in ["A级", "B级", "C级", "A 级", "B 级", "C 级",
                                            "grade", "等级", "source"]):
                        graded += 1
                if total_f and graded / total_f < 0.5:
                    warns.append(
                        f"溯源元数据：manifest.files 中仅 {graded}/{total_f} 条登记了"
                        "来源/等级——数据分级是降级协议的前提，未分级等于无法判断"
                        "哪些结论建立在 B/C 级数据上")

    report(errors, warns, args)


def report(errors, warns, args):
    print(f"入口校验：{'失败' if errors else '通过'}（错误 {len(errors)} / 警告 {len(warns)}）")

    for e in errors:
        print("  [ERROR]", e)
    for w in warns:
        print("  [WARN] ", w)
    if errors:
        print("→ 修正底稿后重跑本脚本；错误未清零禁止进入 Phase 2。")
        sys.exit(1)
    print("→ 校验通过。结果摘要（含警告）写入 manifest 与报告附录。")
    sys.exit(0)


if __name__ == "__main__":
    # 崩溃兜底：数据门禁最危险的失效不是"报错"，而是"以退出码 1 静默退出"——
    # 与"数据不合格被拒"完全无法区分，调用方（人或 CI）会当作正常拒绝处理，
    # 而真实原因是校验器自身有 bug、一条检查都没跑完。
    # NVDA 底稿曾因 date 变量遮蔽在此形态下崩了，stdout 全空、退出码 1。
    # 故内部异常统一转成退出码 3 + 显式标注，与 1（数据不合格）彻底分开。
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        print("入口校验：校验器内部异常（非数据不合格）——退出码 3")
        print(f"  [FATAL] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        print("→ 这是脚本 bug，不是底稿问题。修脚本后重跑；"
              "严禁把本次退出当作『数据已校验』或『数据不合格』。")
        sys.exit(3)
