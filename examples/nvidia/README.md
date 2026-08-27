# NVDA 黄金样例（2026-08-27）

用本 skill 对英伟达（NASDAQ: NVDA）做的一次完整分析，作为复算与校准参照。

## 结论速览

- 档位：**观察等价格**（双闸门全不过：安全边际为负 + 期望年化回报为负）
- 综合分：76/100；红队未降档
- 估值（正常化基期）：悲观 63.14 / 基准 127.14 / 乐观 238.19 美元；现价 209.66 较基准值高估约 65%
- 期望回报率：期望年化 IRR **-10.6%**，亏损概率 **80%**，跑输指数约 19.6 个百分点
- 买点 ≈ 76 美元（基准值 ×0.6，周期高位 40% 安全边际）

> 本样例的价值不在这份报告本身，而在于它同时保留了**初版错误**与**修订后结果**的对照：
> 初版用峰值基期 121,123 百万美元得出基准价值 211.65 美元、买点 148；改用正常化基期 72,756 后，
> 基准价值降到 127.14、亏损概率从 30% 升到 80%。同一公司同一假设，仅基期不同——
> 这个差异正是 `compute_metrics.py normalization` 关卡存在的原因。详见 `decision-log.md`。

## 文件

- `英伟达_价值投资分析报告_20260827.html` — 最终交付报告（修订后口径，59 项数字校验通过）
- `decision-log.md` — 决策日志，含初版 vs 修订版对照
- `data/` — 数据底稿（唯一事实源；已排除 31MB 的 SEC 原文抓取，可按 `workpapers/` 重新拉取）
- `workpapers/extract_financials.py` — 从 SEC EDGAR XBRL companyfacts 抽取年报数据的脚本

## 怎么复算

```bash
# 在 skill 根目录执行；DATA 指向 data/ 即可
python3 scripts/validate_data.py examples/nvidia/data/financials_NVDA.json
python3 scripts/compute_metrics.py examples/nvidia/data/financials_NVDA.json -o /tmp/metrics.json
# 看 normalization.base_oe_recommended（周期高位时为正常化基期）

python3 scripts/reverse_dcf.py expected-return \
  --price 209.66 --hold-years 5 \
  --scenarios "悲观:63.14:0.3,基准:127.14:0.5,乐观:238.19:0.2" \
  --index-hurdle 0.09

# 校验报告数字与底稿一致
python3 scripts/verify_report.py \
  examples/nvidia/英伟达_价值投资分析报告_20260827.html \
  --data-dir examples/nvidia/data
```

## 数据来源等级

年报/季报财务：SEC EDGAR XBRL（A 级）；行情与一致预期：westock-data + 财报后媒体综述（A/B 级）；
行业 capex、诉讼进展：公开报道（C 级，仅旁证）。数据截止 2026-08-27，时效已过，仅供方法参照，不构成投资建议。
