## ADDED Requirements

### Requirement: 异步 Skill 提炼队列
系统 SHALL 在 Reflect Node 通过 `AsyncSkillQueue.submit(candidate)` 异步提交候选 skill，主流程不阻塞；后台 consumer 线程消费队列。

#### Scenario: 主流程不阻塞
- **WHEN** Reflect Node 调用 submit
- **THEN** 函数立即返回，候选 skill 进入队列等待异步处理

### Requirement: Dedup 门 - Embedding 去重
候选 skill 入库前 MUST 先通过 Dedup 门：计算 embedding，与已有 skill 余弦相似度 > 0.85 视为重复，丢弃。

#### Scenario: 重复 skill 被拦截
- **WHEN** 候选 skill embedding 与现有 skill A 相似度 0.92
- **THEN** 该候选不入库，日志记录 "rejected: duplicate of skill A"

### Requirement: Critic 门 - LLM 自我评估
候选 skill MUST 通过 Critic LLM 评估，按"具体性 / 可复用性 / 正交性"三维度打分（每维 0-1），三维度加权 < 0.7 则拒绝。

#### Scenario: 低质量 skill 被拒
- **WHEN** Critic 输出 specificity=0.4, reusability=0.5, orthogonality=0.6
- **THEN** 加权得分 < 0.7，候选不入库，日志记录 "rejected: low quality"

### Requirement: Sandbox 门 - Golden Set 子集验证
候选 skill MUST 在 Golden Set 抽样 10 条（按 intent 分层）上做 dry-run，对比注入前后准确率，下降超过 2% 则拒绝。

#### Scenario: Sandbox 检测到回归
- **WHEN** baseline accuracy=0.85, with_skill accuracy=0.81
- **THEN** 差值 0.04 > 0.02，候选不入库，日志记录 "rejected: regression detected"

#### Scenario: Sandbox 通过准入
- **WHEN** baseline accuracy=0.85, with_skill accuracy=0.86
- **THEN** 通过 sandbox，skill 入库，confidence_init=0.6

### Requirement: 动态 Confidence 调权
入库后的 skill MUST 在使用过程中累积 success_count / fail_count，confidence 按 Wilson Score 公式动态计算。

#### Scenario: 多次成功提升 confidence
- **WHEN** skill 累计 success=18, fail=2
- **THEN** Wilson lower bound 提升，confidence 重新计算后更高

### Requirement: Skill KB 走 RAG 检索注入
Agent 调用 Skill 库 MUST 走 RAG top-k 检索（k=3），按 query 相似度返回相关 skill 注入 prompt，禁止全量拼接。

#### Scenario: 按 query 检索 skill
- **WHEN** 用户提交 query "对账昨日订单"
- **THEN** Skill KB 检索返回最相似的 3 条 skill 注入到 system prompt
