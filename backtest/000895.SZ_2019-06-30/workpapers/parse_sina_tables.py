#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双汇发展 000895.SZ — 新浪财经历史三表解析 → financials_000895_2019.json
对齐鞍钢 000898 管线 annual 20 字段行结构。
数据源: money.finance.sina.com.cn vDOWN_{ProfitStatement,BalanceSheet,CashFlow} (A股CAS, 全量下载于回放复核日)
信息纪律: 仅使用 FY2014-2018 年报 + 2019Q1 (报表期 ≤2019-03-31)。
"""
import json, os

RAW = os.path.join(os.path.dirname(__file__), '..', 'data', 'raw')
TARGET_COLS = ['20141231', '20151231', '20161231', '20171231', '20181231', '20190331']

def load(fname):
    path = os.path.join(RAW, fname)
    with open(path, encoding='utf-8') as f:
        lines = [ln.rstrip('\n').split('\t') for ln in f if ln.strip()]
    header = lines[0]
    tbl = {}
    for ln in lines[1:]:
        row = ln[0]
        vals = {}
        for i, col in enumerate(header[1:], start=1):
            v = ln[i].strip() if i < len(ln) else ''
            vals[col] = v
        tbl[row] = vals
    return header, tbl

def num(tbl, row, col, default=None):
    v = tbl.get(row, {}).get(col, '')
    if v in ('', '--'):
        return default
    return float(v)

ph, pl = load('pl_utf8.txt')
bh, bs = load('bs_utf8.txt')
ch, cf = load('cf_utf8.txt')
unit_note = pl.get('单位', {}).get('20181231', '')
print(f"单位: {unit_note}")

def fnum(x):
    return round(x, 2) if x is not None else None

out = {'quarterly': [], 'annual': []}
for col in TARGET_COLS:
    y = col[:4]
    is_q1 = col.endswith('0331')
    revenue   = num(pl, '营业收入', col)
    rev_total = num(pl, '一、营业总收入', col)
    cost      = num(pl, '营业成本', col)
    op_profit = num(pl, '三、营业利润', col)
    total_pl  = num(pl, '四、利润总额', col)
    tax       = num(pl, '减：所得税费用', col)
    ni        = num(pl, '五、净利润', col)
    ni_parent = num(pl, '归属于母公司所有者的净利润', col)
    minority  = num(pl, '少数股东损益', col)
    eps_basic = num(pl, '基本每股收益(元/股)', col)
    eps_dil   = num(pl, '稀释每股收益(元/股)', col)
    fin_exp   = num(pl, '财务费用', col)
    tax_sur   = num(pl, '营业税金及附加', col)
    sell_exp  = num(pl, '销售费用', col)
    adm_exp   = num(pl, '管理费用', col)
    rd_exp    = num(pl, '研发费用', col)

    cash   = num(bs, '货币资金', col)
    inv    = num(bs, '存货', col)
    ta     = num(bs, '资产总计', col)
    tl     = num(bs, '负债合计', col)
    eq     = num(bs, '所有者权益(或股东权益)合计', col)
    eq_par = num(bs, '归属于母公司股东权益合计', col)
    min_eq = num(bs, '少数股东权益', col)
    paid_in= num(bs, '实收资本(或股本)', col)
    st_borrow = num(bs, '短期借款', col)
    lt_borrow = num(bs, '长期借款', col)
    bond      = num(bs, '应付债券', col)
    cur_lt    = num(bs, '一年内到期的非流动负债', col)
    unretained= num(bs, '未分配利润', col)

    ocf       = num(cf, '经营活动产生的现金流量净额', col)
    capex     = num(cf, '购建固定资产、无形资产和其他长期资产所支付的现金', col)
    div_paid  = num(cf, '分配股利、利润或偿付利息所支付的现金', col)
    dep_fixed = num(cf, '固定资产折旧、油气资产折耗、生产性物资折旧', col)
    amort_int = num(cf, '无形资产摊销', col)
    amort_ltd = num(cf, '长期待摊费用摊销', col)
    dep_total = (dep_fixed or 0) + (amort_int or 0) + (amort_ltd or 0)
    cfo_sales = num(cf, '销售商品、提供劳务收到的现金', col)

    rec = {
        'period': col, 'year': y, 'is_q1': is_q1,
        'revenue': fnum(revenue), 'revenue_total': fnum(rev_total),
        'cost_of_revenue': fnum(cost), 'gross_profit': fnum((revenue or 0) - (cost or 0)),
        'op_profit': fnum(op_profit), 'total_profit': fnum(total_pl), 'income_tax': fnum(tax),
        'net_income': fnum(ni), 'net_income_parent': fnum(ni_parent), 'minority_interest_pnl': fnum(minority),
        'eps_basic': eps_basic, 'eps_diluted': eps_dil,
        'finance_expense': fnum(fin_exp), 'tax_and_surcharges': fnum(tax_sur),
        'selling_expense': fnum(sell_exp), 'admin_expense': fnum(adm_exp), 'rd_expense': fnum(rd_exp),
        'cash': fnum(cash), 'inventory': fnum(inv),
        'total_assets': fnum(ta), 'total_liabilities': fnum(tl),
        'total_equity': fnum(eq), 'equity_parent': fnum(eq_par), 'minority_interest': fnum(min_eq),
        'paid_in_capital': fnum(paid_in), 'unretained_profit': fnum(unretained),
        'total_debt': fnum((st_borrow or 0) + (lt_borrow or 0) + (bond or 0) + (cur_lt or 0)),
        'st_borrow': fnum(st_borrow), 'lt_borrow': fnum(lt_borrow),
        'ocf': fnum(ocf), 'capex': fnum(capex), 'dividends_paid': fnum(div_paid),
        'd_and_a': fnum(dep_total), 'cfo_sales_receipts': fnum(cfo_sales),
    }
    (out['quarterly'] if is_q1 else out['annual']).append(rec)

# 打印摘要
print(f"{'期':<10}{'收入':>10}{'成本':>10}{'毛利':>9}{'归母':>8}{'OCF':>9}{'capex':>8}{'D&A':>7}{'总资产':>10}{'股本':>9}{'分红支付':>9}")
for r in out['annual'] + out['quarterly']:
    print(f"{r['period']:<10}{r['revenue']:>10.0f}{r['cost_of_revenue']:>10.0f}{r['gross_profit']:>9.0f}"
          f"{r['net_income_parent']:>8.0f}{r['ocf']:>9.0f}{r['capex']:>8.0f}{r['d_and_a']:>7.1f}"
          f"{r['total_assets']:>10.0f}{r['paid_in_capital']:>9.0f}{r['dividends_paid']:>9.0f}")

# 毛利率/净利率/ROE 速览（定位句核心检验点：净利率是否失效）
print("\n--- 利润率速览 ---")
print(f"{'期':<10}{'毛利率':>8}{'归母净利率':>10}{'经营OCF/收入':>12}{'ROE(归母)':>10}")
for r in out['annual']:
    g = r['gross_profit'] / r['revenue']
    m = r['net_income_parent'] / r['revenue']
    o = r['ocf'] / r['revenue']
    e = r['net_income_parent'] / r['equity_parent']
    print(f"{r['period']:<10}{g:>8.2%}{m:>10.2%}{o:>12.2%}{e:>10.2%}")

with open(os.path.join(RAW, 'parsed_tables.json'), 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("\nsaved → data/raw/parsed_tables.json")
