## ADDED Requirements

### Requirement: Golden Set 数据结构
系统 SHALL 沉淀 ≥ 50 条业务级 case 到 `tests/eval/golden_set.jsonl`，每条 case schema 包含：`id` / `query` / `expected_sql` / `expected_result_summary` / `intent_label` / `difficulty`（easy/medium/hard）。

#### Scenario: 加载并解析
- **WHEN** 调用 `load_golden_set("tests/eval/golden_set.jsonl")`
- **THEN** 返回 List[GoldenCase]，每条字段完整可解析

#### Scenario: 覆盖五大场景
- **WHEN** 统计 Golden Set 分类
- **THEN** 覆盖：单表查询 ≥10 / 多表 join ≥10 / 时间窗口对账 ≥10 / 数值差异 ≥10 / 边界异常 ≥10

### Requirement: 四维 Metric
系统 SHALL 计算每次 run 的四个 metric：Exec-Accuracy / Semantic-Match / Latency / Token Cost。

#### Scenario: Exec-Accuracy 计算
- **WHEN** Agent 输出 SQL，executor 执行后结果集 hash == expected_sql 执行结果 hash
- **THEN** 该 case 算 exec_accuracy=1，否则 0

#### Scenario: Semantic-Match 计算
- **WHEN** Agent 输出自然语言答案
- **THEN** 用 LLM-as-Judge 评估是否与 expected_result_summary 语义等价，返回 0/1

#### Scenario: Latency / Token Cost 自动采集
- **WHEN** 一条 case 跑完
- **THEN** Eval Harness 从 OTel span 提取 latency_ms 和 token_count，写入报告

### Requirement: Runner 同时支持 v1 / v2
Eval Runner MUST 支持 `--target v1|v2` 参数，对两个版本跑同一套 Golden Set，输出可对比的报告。

#### Scenario: v1 baseline 跑通
- **WHEN** 执行 `python -m tests.eval.runner --target v1`
- **THEN** 在 legacy 代码上跑 50 条 case，输出 `reports/v1_baseline.md`

#### Scenario: v2 vs v1 对比
- **WHEN** 执行 `python -m tests.eval.runner --target v2 --compare v1`
- **THEN** 输出对比表，含四个 metric 的 v1/v2 数值与差值

### Requirement: Regression 防退化
系统 SHALL 在 CI 集成评测脚本，任何 commit 跑全集 Golden Set，若 exec_accuracy 下降 > 2% 则失败。

#### Scenario: Regression 触发
- **WHEN** 改动导致 exec_accuracy 从 0.88 降至 0.83
- **THEN** CI 失败，输出退化的 case 列表
