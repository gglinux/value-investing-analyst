# 告警码注册表（alert registry）

> **本文件是文档镜像，不是事实源。** 唯一事实源是 `scripts/alert_codes.py`——代号、
> 断言组、档位序数全部定义在那里，引擎与回放 runner 共用同一份 import。
> 新增或修改代号请改 `.py`，然后同步本文件。
>
> 之所以这样安排：若代号表以 markdown 为准，引擎写错代号不会被发现（静默产生一个
> 查不到的告警）；定义在 Python 里，`AlertBag.add()` 会对未注册代号直接抛
> `KeyError`，写错当场炸掉。

---

## 一、这套东西解决什么问题

`backtest/PROMPT.md` 第九节承诺：「每次改引擎都应能一键重跑全部断言，立刻知道有没有
把修好的东西弄坏。」第一批 6 案例复核发现该承诺**无法兑现**，根因有三：

1. `compute_metrics.py` 的告警是自由文本，且每个案例的字符串都不同
   （`"稀释警报：收入总量CAGR 26.5% 显著高于每股CAGR 23.6%…"`），无法机器匹配；
2. `check_scenarios.py` 虽有 S1–S9 编号，但编号**拼在中文句子开头**，不是可查询字段；
3. Phase 0 排雷**完全没有代码**（人工按 `forensic-checklist.md` 逐条过），引擎里查不到痕迹。

于是「改完折现率，柯达/康美/海控/福耀四个该拒绝的案例还被拒绝吗」这件事，
只能人工重读 6 份报告，每改一次代码重做一遍——回测成果无法复用。

**修法**：每条规则固定一个代号；引擎输出代号 + 中文说明两个字段；案例落盘一个扁平
`codes` 列表；断言判定退化为集合运算。

验证入口：

```bash
python3 scripts/run_backtest_assertions.py --batch 1          # 断言判定
python3 scripts/run_backtest_assertions.py --rerun            # 附带引擎漂移检测
python3 scripts/run_backtest_assertions.py --baseline backtest/assertion_baseline.json --rerun
```

退出码：**只有「回归失败」才是红灯**；已登记的「已知失败」（如茅台档位轨）单独列示但不阻塞。

---

## 二、分层命名

| 前缀 | 层 | 产出方式 |
|---|---|---|
| `P0_V*` | Phase 0 一票否决 | 人工登记（代号必须取自本表） |
| `P0_R*` | Phase 0 红旗 | 人工登记 |
| `P0_C*` | Phase 0 A股防割附加 | 人工登记 |
| `M_*` | 定量画像告警 | `compute_metrics.py` 自动 |
| `NORM_*` | 周期正常化与基期纪律 | `compute_metrics.py` 自动 |
| `S*` | 三情景门禁十一项 | `check_scenarios.py` 自动 |
| `GATE*` | 双闸门结果 | `reverse_dcf.py expected-return` 自动（闸门一在报告层，人工登记） |

`forensic-checklist.md` 的 6 否决 + 20 红旗 + 4 A股附加 = 30 项，与福耀案
「0/6 + 0/20 + 0/4 = 0/30 误杀」口径一致（另有 `P0_V4A` 利率倒挂为 V4 的加强验证器）。

---

## 三、断言组：为什么需要「任一满足」

`ASSERTIONS` 把一个回放断言映射到**一组**代号，任一命中即算满足。这不是为了放水，
而是为了让一个真实存在的等价关系可被机器判定、且可被审查。

**海控案（第一批复核发现 F4）**：官方要求断言「周期高位」，但引擎实际输出的是
`NORM_BASE_UNUSABLE`——因为该案全期净利率均值为负，均值路径在数学上失效，引擎
**拒绝给出「周期高位」标签**而不是硬给一个错标签。第一批靠人工「两层合读」裁定命中，
既不可复现也不可审查。

现在这个等价写在 `ASSERTIONS["CYCLE_PEAK"] = {NORM_CYCLE_PEAK, NORM_BASE_UNUSABLE}`，
并被 `tests/run_tests.py` 第 11 节锁定。

反向同样重要：**观察级、非造假形态的代号绝不能进造假类断言组**。海控的 IFRS16 准则
切换（`M_ACCOUNTING_STANDARD_SWITCH`）与福耀的分红幻觉（`M_DIVIDEND_ILLUSION`）都
刻意排除在 `ANY_FRAUD_ALERT` 之外——否则「负样本零误杀」这类断言会产生假阳性。
这两条也有测试锁定。

主要断言组：

| 断言 ID | 满足它的代号 | 用于 |
|---|---|---|
| `CASH_INTEREST_CONTRADICTION` | `P0_V4_DEPOSIT_LOAN_DOUBLE_HIGH`、`P0_V4A_INTEREST_INVERSION` | 康美 must_trigger |
| `OCF_PROFIT_DIVERGENCE` | `P0_R1_OCF_PROFIT_DIVERGENCE`、`M_FCF_QUALITY` | 康美 must_trigger |
| `CYCLE_PEAK` / `CURRENT_BASE_FORBIDDEN` | `NORM_CYCLE_PEAK`、`NORM_BASE_UNUSABLE` | 海控 must_trigger |
| `NOT_CONVERGING_FLOOR_LOW` | `GATE2_2_FLOOR_FAIL` | 柯达 must_trigger（二选一） |
| `PEAK_DRAWDOWN_STAGNANT` | `M_PEAK_DRAWDOWN_STAGNANT` | 柯达 must_trigger（二选一） |
| `VALUE_TRAP` | `S8_VALUE_TRAP` | 茅台 must_not_trigger |
| `ANY_FRAUD_ALERT` | 全部 `P0_V*` + 造假类 `P0_R*` | 福耀 must_not_trigger（负样本） |
| `PHASE0_VETO_FIRED` | 全部 `P0_V*` | 柯达 must_not_trigger（防误杀） |
| `GATE1_PASSED` / `GATE2_ALL_PASSED` | `GATE1_PASS` / `GATE2_PASS` | 苹果 must_trigger |

---

## 四、案例文件契约

**`verdict.json`**（Step 3 落盘，新增三个字段）

```json
{
  "codes": ["GATE1_FAIL", "GATE2_FAIL", "NORM_BASE_UNUSABLE", "..."],
  "codes_provenance": {
    "engine_derived": ["由 compute_metrics/check_scenarios/reverse_dcf 重跑产出"],
    "manually_recorded": ["Phase 0 排雷与闸门一（无脚本层）"]
  },
  "verdict_ordinal": 2
}
```

**`answer.json`**（Step 4 才创建，与 meta.json 严格分离——meta.json 跑前冻结、不得含答案）

```json
{
  "expected_verdict_set": [3, 4],
  "must_trigger": ["CYCLE_PEAK"],
  "must_trigger_any": [["NOT_CONVERGING_FLOOR_LOW", "PEAK_DRAWDOWN_STAGNANT"]],
  "must_not_trigger": ["VALUE_TRAP"],
  "known_failures": ["verdict_track"],
  "actual_5y_total_return": 8.062,
  "earnings_driven_return": null,
  "multiple_driven_return": null,
  "outcome_note": "…",
  "answer_source": "…"
}
```

**档位序数**：`排除=0 / 拒绝=1 / 观察等价格=2 / 小仓位试探=3 / 核心买入=4`。
`expected_verdict_set: null` 表示官方不约束档位（如福耀）→ 档位轨不计分。

**两条独立计分轨**（不得互相抵扣、不得合并为单一「命中」）：
- **告警轨**：`must_trigger` 命中数 / 漏判数 / `must_not_trigger` 误触发数
- **档位轨**：`verdict_ordinal` 是否落入 `expected_verdict_set`

第一批曾把福耀同时计入「命中」与「错误拒绝」两个互斥的桶，头条战绩因此偏乐观。
分轨计分是对该问题的结构性修正。

**`known_failures`**：登记**已知且已记录在案**的失败类型（`verdict_track` /
`must_trigger` / `must_not_trigger` / `engine_drift`）。只有未登记的失败才算回归。
若某项登记的失败已消失，runner 会提示「登记过期」，要求移除——防止拿旧登记
掩盖新问题。

---

## 五、新增一条告警时的完整流程

1. 在 `scripts/alert_codes.py` 的 `ALERTS` 登记代号与语义（**先做这步**，否则
   `AlertBag.add()` 会抛 `KeyError`）；
2. 引擎侧用 `alerts.add("代号", f"中文说明…")` 产出（`compute_metrics.py`），
   或确保消息以已映射的 `S*` 前缀开头（`check_scenarios.py`，映射表见该文件
   `_S_PREFIX_TO_CODE`）；
3. 若该告警会被回放断言引用，在 `ASSERTIONS` 建立断言组；
4. 若它属于造假形态，加入 `_FRAUD_VETO` / `_FRAUD_REDFLAG`；**若不属于，
   务必不要加**（否则负样本断言假阳性）；
5. 跑 `python3 tests/run_tests.py`（第 11 节守护本基础设施）；
6. 跑 `python3 scripts/run_backtest_assertions.py --rerun` 确认既有案例无漂移。

`check_scenarios.py` 的 `derive_codes()` 对任何无法映射前缀的消息会输出
`[BUG] …` 并记入 `unmapped_messages`——**漏映射意味着断言会漏判该规则，
必须当 bug 修，不得静默放过。**
