# 基率表（Base Rates）——三张表的统一参考

机器事实源在 `scripts/reverse_dcf.py`（本文件是可读镜像，改数字改引擎、同步此处）。三张表各管一个"想象力天花板"：

## 一、行业收入增速基率（REQ-P1-05，`INDUSTRY_GROWTH_BASE_RATES`）

乐观情景的 `growth_assumption` **不得超过本行业 p80**——超限须 `optimistic_growth_support` 挂 [E:]（Phase 4.5 变异认知）才放行（`check_scenarios.py` S10 拦截，`BASERATE_OPTIMISTIC_ABOVE_P80`）。口径：长期（10 年窗口）收入 CAGR 横截面分位，p50 ≈ 行业名义收入中枢、p80 ≈ 周期上行/高增长带门限；相对排序按 Damodaran 行业数据集校准。锚是拦"20 年 20%"级别想象力的纪律线，不是统计断言。白名单纪律：新行业先登记再使用。

| industry | p50 | p80 | 案例锚 |
|---|---|---|---|
| food_processing | 3% | 7% | 双汇：必选消费≈名义GDP |
| steel | 2% | 8% | 鞍钢：量平、价周期波动 |
| pharma | 5% | 12% | 康美：医药（含中药） |
| liquor_premium | 8% | 15% | 茅台：高端白酒量价 |
| auto_parts | 4% | 10% | 福耀：≈全球汽车产量+1 |
| coal_energy | 2% | 8% | 神华：煤炭+电力 |
| shipping | 3% | 12% | 中远海控：运价振幅 |
| holding_investment | 5% | 12% | 软银：控股投资净值 |
| consumer_electronics | 6% | 15% | 苹果：硬件+平台 |
| imaging_legacy | -5% | 3% | 柯达：结构性衰退 |
| streaming_media | 15% | 30% | Netflix：内容订阅 |
| saas_communications | 15% | 30% | Zoom：视频通信 |

## 二、规模收入达成基率（`REVENUE_CAGR_BASE_RATES`，成长股通道）

Mauboussin《The Base Rate Book》美股 1950-2015（到达概率挂此锚，`--base-rates-file` 可覆盖）：≥500 亿美元档 10 年 CAGR≥20% 达成率 1%、≥10% 达成 10%；100-500 亿 3%/15%；<100 亿 10%/25%（插值）。详见 valuation-guide 第二步基率检验表。

## 三、控股折价基率带（`HOLDING_DISCOUNT_BANDS`，SOTP 通道）

软银档为仓库内实证（equity NAV 折价 30-50%），其余带注估计；`--discount-bands-file` 可覆盖。见 company-types 卡四。
