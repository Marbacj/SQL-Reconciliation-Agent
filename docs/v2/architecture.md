# SQL Reconciliation Agent v2 - 完整技术方案

> **作者**：mabohui
> **更新**：2026-06-01
> **状态**：方案定稿，待实施
> **关联**：[D01 决策文档](../../.codeflicker/discuss/2026-06-01/sql-recon-rebuild/decisions/D01-rebuild-strategy.md) | [实施计划 plan.md](../../.codeflicker/mem-bank/threads/.../plan.md)

---

## 目录

- [1. 项目定位](#1-项目定位)
- [2. 设计原则](#2-设计原则)
- [3. 总体架构](#3-总体架构)
- [4. 模块详细设计](#4-模块详细设计)
- [5. 关键工作流](#5-关键工作流)
- [6. 技术栈与外部依赖](#6-技术栈与外部依赖)
- [7. 数据模型](#7-数据模型)
- [8. 部署架构](#8-部署架构)
- [9. 性能与成本目标](#9-性能与成本目标)
- [10. 风险与权衡](#10-风险与权衡)
- [11. 与 v1 对比](#11-与-v1-对比)
- [12. 面试叙事框架](#12-面试叙事框架)

---

## 1. 项目定位

**SQL Reconciliation Agent v2** 是一个**可观测、可评测、可自演进**的自然语言对账 Agent，输入业务对账问题，输出 SQL 执行结果与差异报告。

### 1.1 边界
- ✅ **包含**：NL → SQL → 执行 → 差异分析 → 报告 → 经验沉淀的完整闭环
- ✅ **包含**：Agent 工程现代化的全套实践（编排、可观测、评测、安全、自进化）
- ❌ **不包含**：生产 SLA、多租户隔离、灰度发布（个人项目边界）

### 1.2 目标
- **主目标**：面试简历升级，对标工业级 Agent 工程实践
- **次目标**：跑通 LangGraph + Hybrid RAG + Sandbox 自进化 + Eval-Driven 全栈

---

## 2. 设计原则

| 原则 | 含义 |
|------|------|
| **Eval-Driven** | 没有 Golden Set 不写一行核心代码；每次架构改动跑 regression |
| **Observability-First** | 任何 LLM/Tool/RAG 调用必须 emit OTel span |
| **Schema-Over-Magic** | 用 Pydantic 显式 schema，反对反射魔法 |
| **Single Context, Multi Mode** | 一个 AgentContext 贯穿全程，模式（ReAct/PlanSolve）是策略 |
| **Quality-Gated Evolution** | 自进化必须有 dedup + critic + sandbox 三层门槛 |
| **Hybrid by Default** | RAG 默认 sparse + dense 双通道并存，不做 fallback |
| **Production Safety** | SQL 安全用 AST 解析而非黑名单 |

---

## 3. 总体架构

### 3.1 分层架构图

```
┌────────────────────────────────────────────────────────────┐
│  L5: Interface 接入层                                       │
│  ┌──────┐  ┌──────────┐  ┌────────────┐                    │
│  │ CLI  │  │ FastAPI  │  │ Notebook   │                    │
│  └──────┘  └──────────┘  └────────────┘                    │
├────────────────────────────────────────────────────────────┤
│  L4: Orchestration 编排层（LangGraph）                      │
│  ┌────────────────────────────────────────────────────┐    │
│  │  StateGraph: route → plan → act → observe → reflect│    │
│  │  ────────────────────────────────────────────────  │    │
│  │  AgentContext: trace_id / memory / rag / tools /   │    │
│  │                llm / tracer / budget               │    │
│  └────────────────────────────────────────────────────┘    │
├────────────────────────────────────────────────────────────┤
│  L3: Capability 能力层                                      │
│  ┌──────────┐  ┌─────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ Tools    │  │ Memory  │  │ RAG      │  │ Evolution    │ │
│  │ Pydantic │  │ v2      │  │ Hybrid   │  │ Sandbox      │ │
│  │ Schema   │  │ 三层    │  │ +Rerank  │  │ +Critic      │ │
│  └──────────┘  └─────────┘  └──────────┘  └──────────────┘ │
├────────────────────────────────────────────────────────────┤
│  L2: Infrastructure 基础设施                                │
│  ┌──────────┐  ┌─────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ LLM      │  │ SQL     │  │ OTel +   │  │ Eval         │ │
│  │ Gateway  │  │ Safety  │  │ Phoenix  │  │ Harness      │ │
│  │ +Cache   │  │ sqlglot │  │ Tracing  │  │ Golden Set   │ │
│  └──────────┘  └─────────┘  └──────────┘  └──────────────┘ │
├────────────────────────────────────────────────────────────┤
│  L1: Storage 存储层                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │ SQLite       │  │ Qdrant       │  │ Redis (cache)   │   │
│  │ case/skill/  │  │ vector index │  │ optional        │   │
│  │ memory       │  │              │  │                 │   │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

### 3.2 核心数据流（单次 Query）

```
User Query
   │
   ▼
[Route Node] ──confidence < 0.6──> [Clarify Node] ──→ End
   │
   ▼ (high confidence)
[Plan Node] ──→ 生成执行步骤
   │
   ▼
[Act Node] ──→ Tool 调用 / RAG 检索 / SQL 执行
   │           │
   │           ├──→ LLM Gateway (cache hit?)
   │           ├──→ SQL Safety (AST 校验)
   │           ├──→ Memory.query (近似 case)
   │           └──→ RAG.search (Hybrid + Rerank)
   ▼
[Observe Node] ──未完成 step < N──> [Act Node]
   │
   ▼ (done)
[Reflect Node] ──异步──> [Skill Reviewer]
   │                       │
   │                       ▼
   │                  [Dedup → Critic → Sandbox]
   │                       │
   │                       ▼
   │                  [Skill KB (SQLite)]
   ▼
End (return result + trace_id)
```

### 3.3 自进化闭环

```
Query 执行
   │
   ▼
Working Memory (LRU 20)
   │ 重要性打分 > 阈值
   ▼
Episodic Memory (SQLite)
   │ 重复 case ≥ 5
   ▼
LLM Consolidation Job
   │
   ▼
Semantic Memory (SQLite)
   │
   ├──→ 30 天未用 + conf < 0.5 → 衰减/淘汰
   │
   └──→ RAG Top-k 注入下次 Query
```

---

## 4. 模块详细设计

### 4.1 AgentContext（核心抽象）

**文件**：`recon_v2/core/context.py`

```python
@dataclass
class AgentContext:
    # 标识
    trace_id: str
    session_id: str

    # 输入
    query: str
    intent: Intent  # (label, confidence)

    # 能力
    memory: MemoryStore
    rag: HybridRetriever
    tools: ToolRegistry
    llm: LLMGateway
    tracer: Tracer

    # 控制
    budget: CostBudget  # token / latency 上限
    step_counter: int
    mode: Literal["react", "plan_solve"]  # 当前模式
```

**作用**：所有 Node、所有 Tool 都通过这一个 context 访问能力，**杜绝 v1 的"两个 Agent 各自持有一套依赖"**。

### 4.2 LangGraph 编排

**文件**：`recon_v2/orchestration/graph.py`

```python
from langgraph.graph import StateGraph, END

def build_graph(ctx: AgentContext) -> StateGraph:
    g = StateGraph(GraphState)

    g.add_node("route", route_node)
    g.add_node("clarify", clarify_node)
    g.add_node("plan", plan_node)
    g.add_node("act", act_node)
    g.add_node("observe", observe_node)
    g.add_node("reflect", reflect_node)

    g.set_entry_point("route")

    # 路由后分支
    g.add_conditional_edges(
        "route",
        lambda s: "clarify" if s.intent.confidence < 0.6 else "plan"
    )

    g.add_edge("clarify", END)
    g.add_edge("plan", "act")
    g.add_edge("act", "observe")

    # observe 循环
    g.add_conditional_edges(
        "observe",
        lambda s: "act" if not s.done and s.step_counter < MAX_STEPS else "reflect"
    )

    g.add_edge("reflect", END)

    return g.compile(checkpointer=SqliteSaver())
```

**核心特性**：
- **Checkpointer**：每个 Node 自动落盘，支持中断恢复
- **Conditional Edges**：状态机的分支跳转
- **Budget 守门员**：每个 Node 入口检查 `ctx.budget.exceeded()`，超限直接跳 END

### 4.3 Tool 系统（Pydantic Schema）

**文件**：`recon_v2/tools/base.py`

```python
class ToolInput(BaseModel):
    """所有工具入参的基类"""
    pass

class ToolOutput(BaseModel):
    """所有工具出参的基类"""
    success: bool
    data: Any
    error: Optional[str] = None

class ToolBase(ABC):
    name: str
    description: str
    input_schema: Type[ToolInput]
    output_schema: Type[ToolOutput]

    @abstractmethod
    def run(self, ctx: AgentContext, inp: ToolInput) -> ToolOutput: ...

    def to_openai_function(self) -> dict:
        """转 OpenAI Function Calling schema"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema.model_json_schema(),
        }
```

**5 个核心工具**：

| 工具 | 用途 | 关键能力 |
|------|------|---------|
| `sql_runner` | 执行 SQL | sqlglot AST 安全护栏 + EXPLAIN 预检 |
| `diff_calculator` | 计算差异 | 两个结果集对比，输出 diff |
| `report_generator` | 生成报告 | Markdown / JSON 双格式 |
| `rag_searcher` | RAG 检索 | Hybrid + Rerank，返回 top-k 表文档 |
| `case_query` | 查相似 case | Episodic Memory 检索 |

### 4.4 Hybrid RAG（重投入模块）

**文件**：`recon_v2/rag/retriever.py`

```python
class HybridRetriever:
    def __init__(self, qdrant: QdrantClient, bm25: BM25Okapi, reranker: CrossEncoder):
        self.qdrant = qdrant
        self.bm25 = bm25
        self.reranker = reranker

    def retrieve(self, query: str, k: int = 3) -> List[Document]:
        # Stage 1: 双通道并行
        dense_results = self._dense_search(query, top=20)
        sparse_results = self._sparse_search(query, top=20)

        # Stage 2: RRF 融合
        fused = self._rrf_fuse(dense_results, sparse_results)

        # Stage 3: Cross-Encoder Rerank
        reranked = self._rerank(query, fused[:10])

        return reranked[:k]

    def _rrf_fuse(self, l1, l2, k=60):
        """Reciprocal Rank Fusion"""
        scores = defaultdict(float)
        for rank, doc in enumerate(l1):
            scores[doc.id] += 1.0 / (k + rank)
        for rank, doc in enumerate(l2):
            scores[doc.id] += 1.0 / (k + rank)
        return sorted(docs, key=lambda d: scores[d.id], reverse=True)
```

**关键决策**：
- Dense 模型：`bge-small-zh`（轻量、中文好）
- Sparse 模型：`rank_bm25`（纯 Python）
- Reranker：`bge-reranker-v2-m3`（精度好、可选远程 API 兜底慢）

**RAG-as-Tool 改造**：
```python
class RAGSearcherTool(ToolBase):
    name = "search_table_docs"
    description = "搜索数据表 schema 和业务文档，按需调用"

    def run(self, ctx, inp):
        docs = ctx.rag.retrieve(inp.query, k=inp.k or 3)
        return ToolOutput(success=True, data=docs)
```

→ Agent 在 Act Node 自主决定是否调用，而不是无脑全量塞 prompt。

### 4.5 Memory v2（三层 + Promotion）

**文件**：`recon_v2/memory/`

```python
class MemoryStore:
    def __init__(self, working: WorkingMemory, episodic: EpisodicMemory, semantic: SemanticMemory):
        self.working = working
        self.episodic = episodic
        self.semantic = semantic

    def write(self, item: MemoryItem):
        self.working.put(item)
        # 重要性打分（同步）
        if self._importance(item) > THRESHOLD:
            self.episodic.insert(item)

    def query(self, q: str, k: int = 5) -> List[MemoryItem]:
        # 三层联合检索
        wk = self.working.recent(k)
        ep = self.episodic.search(q, k)
        sm = self.semantic.search(q, k)
        return self._merge_dedup(wk + ep + sm)[:k]

    def _importance(self, item) -> float:
        """重要性 = 0.4*outcome + 0.3*novelty + 0.3*user_flag"""
        ...
```

**SQLite Schema**：
```sql
CREATE TABLE episodic_case (
    id TEXT PRIMARY KEY,
    query TEXT,
    intent TEXT,
    sql TEXT,
    success INTEGER,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    created_at TIMESTAMP,
    last_used_at TIMESTAMP,
    embedding BLOB
);

CREATE TABLE semantic_rule (
    id TEXT PRIMARY KEY,
    rule_type TEXT,  -- sql_pattern / diff_rule / term_mapping
    content TEXT,
    confidence REAL DEFAULT 0.6,
    usage_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMP,
    embedding BLOB
);
```

**Consolidation Job**（定期任务）：
```python
def consolidate(memory: MemoryStore, llm: LLMGateway):
    """Episodic → Semantic：找到重复 5+ 次的 case 模式，LLM 抽象为规则"""
    clusters = memory.episodic.cluster_similar(min_size=5)
    for cluster in clusters:
        rule = llm.summarize_pattern(cluster)  # LLM 归纳
        if rule.passes_critic():
            memory.semantic.insert(rule)
```

**衰减 Job**：
```python
def decay(memory: MemoryStore):
    """30 天未用 + confidence < 0.5 → 标记降级"""
    candidates = memory.semantic.find(
        last_used_before=now - timedelta(days=30),
        confidence_below=0.5
    )
    memory.semantic.archive(candidates)
```

### 4.6 Self-Evolution Sandbox（最具面试价值）

**文件**：`recon_v2/evolution/`

```python
class EvolutionPipeline:
    def __init__(self, store, sandbox, critic, embedder):
        self.store = store
        self.sandbox = sandbox
        self.critic = critic
        self.embedder = embedder

    def submit(self, candidate: SkillCandidate) -> bool:
        # Gate 1: Dedup
        if self._is_duplicate(candidate):
            log.info(f"skill rejected: duplicate")
            return False

        # Gate 2: Critic（LLM self-evaluation）
        score = self.critic.evaluate(candidate)
        if score < CRITIC_THRESHOLD:  # 0.7
            log.info(f"skill rejected: low quality, score={score}")
            return False

        # Gate 3: Sandbox（Golden Set 抽样 dry-run）
        baseline = self.sandbox.run_baseline()
        with_skill = self.sandbox.run_with_skill(candidate)
        if with_skill.accuracy < baseline.accuracy - 0.02:
            log.info(f"skill rejected: regression detected")
            return False

        # 三道门全过 → 入库
        candidate.confidence = 0.6
        self.store.insert(candidate)
        return True

    def _is_duplicate(self, candidate) -> bool:
        emb = self.embedder.embed(candidate.content)
        neighbors = self.store.search_similar(emb, threshold=0.85)
        return len(neighbors) > 0
```

**核心创新**：
- **Sandbox**：在 Golden Set 子集（10 条）上 dry-run，**自进化第一次有 governance**
- **Critic**：LLM 自我打分（具体性 / 可复用性 / 正交性）
- **Dynamic Confidence**：使用过程中 `success_count++ / fail_count++`，按 wilson score 动态调权

**并发设计**：
```python
# 不用 RLock，改 producer-consumer
class AsyncSkillQueue:
    def submit(self, candidate):
        self.queue.put(candidate)  # 主线程立刻返回

    def _consumer(self):
        """单独线程消费，避免主流程阻塞 & 死锁"""
        while True:
            candidate = self.queue.get()
            self.pipeline.submit(candidate)
```

### 4.7 LLM Gateway

**文件**：`recon_v2/infra/llm_gateway.py`

```python
class LLMGateway:
    def __init__(self, provider: str = "deepseek", cache: CacheBackend = None):
        self.client = litellm  # 多厂商统一接口
        self.cache = cache or InMemoryCache()
        self.cost_tracker = CostTracker()

    @trace("llm.chat")
    def chat(self, messages, **kwargs) -> ChatResponse:
        # 1. Cache 查询
        key = self._fingerprint(messages, kwargs)
        if cached := self.cache.get(key):
            self.cost_tracker.record(cached, source="cache")
            return cached

        # 2. 调用 + retry
        response = self._call_with_retry(messages, **kwargs)

        # 3. 写 cache
        self.cache.set(key, response, ttl=3600)

        # 4. 成本记账
        self.cost_tracker.record(response, source="api")

        return response

    def _fingerprint(self, messages, kwargs):
        """query 指纹：messages + temperature + model"""
        return sha256(json.dumps(...)).hexdigest()
```

### 4.8 SQL Safety（sqlglot AST）

**文件**：`recon_v2/infra/sql_safety.py`

```python
import sqlglot
from sqlglot import exp

ALLOWED_VERBS = {"SELECT", "WITH"}

DANGEROUS_NODES = {
    exp.Delete, exp.Update, exp.Insert,
    exp.Drop, exp.Alter, exp.Create,
    exp.TruncateTable,
}

def is_safe(sql: str, dialect: str = "sqlite") -> tuple[bool, str]:
    try:
        ast = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:
        return False, f"parse error: {e}"

    # Verb 白名单
    root_verb = ast.key.upper()
    if root_verb not in ALLOWED_VERBS:
        return False, f"verb {root_verb} not allowed"

    # 子树扫描
    for node in ast.walk():
        if isinstance(node, tuple(DANGEROUS_NODES)):
            return False, f"dangerous node: {type(node).__name__}"

    return True, "ok"
```

**对比 v1**：
- v1：`if "DELETE" in sql.upper(): block` → `SELECT 'DELETE FROM users'` 误杀，`/* DELETE */` 绕过
- v2：AST 解析，**不可绕过**

### 4.9 Observability（OpenTelemetry + Phoenix）

**文件**：`recon_v2/infra/tracing.py`

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from openinference.semconv.trace import SpanAttributes

def init_tracing(service_name: str = "recon-v2"):
    provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint="http://localhost:6006/v1/traces")  # Phoenix
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

tracer = trace.get_tracer(__name__)

# 装饰器
def traced(span_name: str):
    def decorator(func):
        def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute("input", str(kwargs))
                result = func(*args, **kwargs)
                span.set_attribute("output", str(result)[:500])
                return result
        return wrapper
    return decorator
```

**Phoenix UI** 可视化每次 query 的 span 树：
```
query: "对账昨天和前天的订单数差异"
├── route (12ms) intent=reconciliation, confidence=0.85
├── plan (340ms) llm.chat tokens=520
├── act #1 (45ms)
│   └── rag.search (28ms) k=3, returned 2 docs
├── act #2 (1200ms)
│   ├── llm.chat (890ms) tokens=1240
│   └── sql_runner (15ms) rows=2
├── observe (8ms)
└── reflect (2ms async, skill submitted)

Total: 1607ms | Tokens: 1760 | Cost: $0.0042
```

### 4.10 Eval Harness

**文件**：`tests/eval/`

```python
# golden_set.jsonl 单条结构
{
  "id": "case_001",
  "query": "对账昨天和前天的订单数差异",
  "expected_sql": "SELECT date, COUNT(*) FROM orders WHERE date IN ('...', '...') GROUP BY date",
  "expected_result_summary": "昨天比前天少 12 单",
  "intent_label": "reconciliation",
  "difficulty": "easy"  # easy / medium / hard
}

# metrics.py 四个 metric
class Metrics:
    exec_accuracy: float       # SQL 执行结果集 hash 匹配
    semantic_match: float      # LLM-as-Judge 自然语言匹配
    latency_p50: float
    latency_p99: float
    token_cost_avg: float
```

---

## 5. 关键工作流

### 5.1 单次 Query 完整时序

```
User → API
       │
       ▼
   FastAPI: POST /query
       │ generate trace_id
       ▼
   AgentContext 初始化
       │ (memory/rag/tools/llm/budget)
       ▼
   LangGraph.invoke(query)
       │
       ├─ [route] keyword + LLM → (intent, confidence)
       │        emit span "route"
       │
       ├─ [plan] if complex → LLM 多步规划
       │        else → 直接 ReAct
       │        emit span "plan"
       │
       ├─ [act] (loop max=8)
       │        │
       │        ├─ select tool (Pydantic schema)
       │        ├─ tool.run(ctx, input)
       │        │   ├─ sql_runner → sqlglot.is_safe()
       │        │   ├─ rag_searcher → hybrid + rerank
       │        │   └─ ...
       │        └─ emit span "tool.{name}"
       │
       ├─ [observe] 解析结果 + 评估完成度
       │        if budget.exceeded() → END
       │        if step > N → END
       │        if done → reflect
       │        else → back to act
       │
       ├─ [reflect] (异步)
       │        skill_queue.submit(candidate)
       │
       ▼
   API Response: { result, trace_id, cost }
       │
   User ← (含 trace_id 链接到 Phoenix UI)
```

### 5.2 自进化闭环（异步）

```
[Reflect Node]
       │ skill_queue.submit(candidate)
       ▼
[Background Worker Thread]
       │
       ├─ Gate 1: Dedup
       │       embedding 相似度 < 0.85 才继续
       │
       ├─ Gate 2: Critic
       │       LLM self-evaluation
       │       具体性 + 可复用性 + 正交性 三维评分
       │       > 0.7 才继续
       │
       ├─ Gate 3: Sandbox
       │       从 Golden Set 抽 10 条
       │       不带 skill 跑一遍 → baseline.accuracy
       │       带 skill 跑一遍 → with_skill.accuracy
       │       不退化 (-2%) 才继续
       │
       ▼
   Skill KB (SQLite) insert
       confidence_init = 0.6
```

### 5.3 Memory Promotion 闭环（定时）

```
[Cron Job: daily 02:00]
       │
       ├─ Episodic.cluster_similar(min_size=5)
       │       → 找出重复 5+ 次的 query pattern
       │
       ├─ for each cluster:
       │       summary = llm.summarize_pattern(cluster)
       │       if critic.evaluate(summary) > 0.7:
       │           semantic.insert(summary, confidence=0.7)
       │
       ├─ Semantic.find(last_used_before=-30d, conf<0.5)
       │       → 衰减/归档
       │
       └─ 日志输出归纳/衰减条数
```

---

## 6. 技术栈与外部依赖

### 6.1 完整依赖清单

```toml
# pyproject.toml
[project]
name = "recon-v2"
requires-python = ">=3.10"

dependencies = [
    # 编排
    "langgraph>=0.2.0",
    "langchain-core>=0.3.0",

    # LLM
    "litellm>=1.40.0",            # 多厂商 LLM
    "tiktoken>=0.6.0",

    # 数据 & schema
    "pydantic>=2.0",
    "sqlalchemy>=2.0",            # ORM
    "alembic>=1.13",              # 迁移

    # SQL 安全
    "sqlglot>=23.0",

    # RAG
    "qdrant-client>=1.9",
    "rank-bm25>=0.2.2",
    "sentence-transformers>=3.0",  # bge embedding
    "FlagEmbedding>=1.2",          # bge-reranker

    # 可观测
    "opentelemetry-api>=1.25",
    "opentelemetry-sdk>=1.25",
    "opentelemetry-exporter-otlp>=1.25",
    "openinference-instrumentation>=0.1",  # LLM-specific
    "arize-phoenix>=4.0",

    # Web
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "httpx>=0.27",

    # Cache
    "redis>=5.0",
    "cachetools>=5.3",

    # Eval
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-benchmark>=4.0",
]
```

### 6.2 外部服务

| 服务 | 用途 | 端口 | 必须? |
|------|------|------|-------|
| **Qdrant** | 向量检索 | 6333 | ✅ 必须 |
| **Redis** | LLM cache | 6379 | ⚠️ 可选（degraded 到内存） |
| **Phoenix** | LLM trace UI | 6006 | ⚠️ 可选（dev 必须） |
| **SQLite** | 结构化存储 | - | ✅ 必须（内嵌） |

---

## 7. 数据模型

### 7.1 SQLite Schema 完整版

```sql
-- 历史 case
CREATE TABLE episodic_case (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    intent_label TEXT,
    intent_confidence REAL,
    final_sql TEXT,
    success INTEGER,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    execution_trace TEXT,        -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    embedding BLOB
);

CREATE INDEX idx_episodic_intent ON episodic_case(intent_label);
CREATE INDEX idx_episodic_used ON episodic_case(last_used_at);

-- 抽象规则（Semantic）
CREATE TABLE semantic_rule (
    id TEXT PRIMARY KEY,
    rule_type TEXT NOT NULL,     -- sql_pattern / diff_rule / term_mapping
    title TEXT,
    content TEXT NOT NULL,        -- JSON 格式规则体
    confidence REAL DEFAULT 0.6,
    usage_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    archived INTEGER DEFAULT 0,
    embedding BLOB
);

CREATE INDEX idx_semantic_type ON semantic_rule(rule_type);
CREATE INDEX idx_semantic_active ON semantic_rule(archived, confidence);

-- Skill KB（自进化产物）
CREATE TABLE skill (
    id TEXT PRIMARY KEY,
    skill_type TEXT NOT NULL,     -- sql_pattern / term_mapping / diff_rule
    title TEXT,
    content TEXT NOT NULL,
    confidence REAL DEFAULT 0.6,
    usage_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    sandbox_score REAL,           -- 入库时 sandbox 验证得分
    critic_score REAL,            -- 入库时 critic 得分
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    archived INTEGER DEFAULT 0,
    embedding BLOB
);

-- Trace 索引
CREATE TABLE trace_record (
    trace_id TEXT PRIMARY KEY,
    query TEXT,
    final_answer TEXT,
    total_tokens INTEGER,
    total_cost_usd REAL,
    latency_ms INTEGER,
    success INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 7.2 Qdrant Collection 设计

```python
# 表 schema 索引
COLLECTION_TABLE_DOCS = {
    "name": "table_docs",
    "vector_size": 512,  # bge-small-zh
    "distance": "Cosine",
    "payload_schema": {
        "table_name": "keyword",
        "column_name": "keyword",
        "doc_type": "keyword",  # schema / business_doc / example
        "content": "text",
        "updated_at": "datetime",
    }
}
```

---

## 8. 部署架构

### 8.1 docker-compose.yml（开发）

```yaml
version: "3.9"

services:
  app:
    build: .
    ports: ["8000:8000"]
    depends_on: [qdrant, redis, phoenix]
    environment:
      - QDRANT_URL=http://qdrant:6333
      - REDIS_URL=redis://redis:6379
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006/v1/traces

  qdrant:
    image: qdrant/qdrant:v1.9.0
    ports: ["6333:6333"]
    volumes: ["./data/qdrant:/qdrant/storage"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  phoenix:
    image: arizephoenix/phoenix:latest
    ports: ["6006:6006"]
```

### 8.2 启动命令

```bash
# 一键启动
docker-compose up -d

# 离线建索引
python -m recon_v2.rag.indexer --source data/tables/ --collection table_docs

# 跑 demo
python apps/cli/demo.py --query "对账昨天和前天的订单差异"

# 跑评测
pytest tests/eval/ -v --report tests/eval/reports/$(date +%Y%m%d).md
```

---

## 9. 性能与成本目标

| 指标 | v1 baseline | v2 目标 | 改进 |
|------|-------------|---------|------|
| Exec-Accuracy | 60% | ≥ 80% | +20% |
| Semantic-Match | 65% | ≥ 85% | +20% |
| Latency p50 | 2.5s | ≤ 1.5s | -40% |
| Latency p99 | 8s | ≤ 4s | -50% |
| Token Cost / Query | $0.012 | ≤ $0.007 | -42% |
| Cache 命中率 | 0% | ≥ 30% | new |
| RAG MRR@5 | 0.4 | ≥ 0.55 | +37% |
| SQL 注入拦截率 | ~40% | 100% | new |
| Skill 入库通过率 | 100% (无门槛) | ≤ 50% | governance |

---

## 10. 风险与权衡

### 10.1 风险地图

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LangGraph 范式跳跃成本 | 中 | 高 | Stage 2 Day 1 PoC，不通改方案 B |
| Cross-Encoder 本地慢 | 中 | 中 | 备选 cohere rerank API |
| LLM consolidation prompt 难调 | 高 | 中 | 多轮迭代 + Golden Set 验证 |
| Sandbox 验证耗时 | 中 | 中 | 抽样 10 条而非全集 |
| 50 条 Golden Set 不够 | 中 | 中 | Stage 0 后期补到 80 条 |
| 时间超期 | 高 | 中 | 每 Stage buffer，超期砍 Stage 5 metrics |

### 10.2 关键权衡

| 权衡点 | 选择 | 放弃 | 理由 |
|--------|------|------|------|
| 自研 vs LangGraph | LangGraph | 自研叙事 | 工业标准、简历可信度 |
| Hybrid 默认 vs Fallback | Default | 简单实现 | 真正的检索质量 |
| Sandbox vs Direct insert | Sandbox | 自进化速度 | 质量门槛是核心价值 |
| Qdrant vs Chroma | Qdrant | 单文件简单 | 生产可演进 |
| 完整 Stage vs 精简版 | 完整 | 时间成本 | 简历叙事完整性 |

---

## 11. 与 v1 对比

### 11.1 架构层级对比

| 维度 | v1 | v2 |
|------|----|----|
| 编排范式 | 路由 + 两个独立 Agent | 单一 StateGraph + AgentContext |
| Tool 定义 | `@tool_action` 反射 | Pydantic Schema |
| LLM 调用 | 直接 OpenAI | LLM Gateway + Cache |
| SQL 安全 | 关键字黑名单 | sqlglot AST |
| RAG | Prompt 拼接 / 单通道 | RAG-as-Tool + Hybrid + Rerank |
| Memory | 3 个 JSON 文件 | SQLite + Promotion + Consolidation |
| 自进化 | 异步落盘无门槛 | Sandbox + Critic + Dedup 三道门 |
| 可观测 | print + stdout tee | OpenTelemetry + Phoenix |
| 评测 | 无 | Golden Set 50 + 4 metric |
| 部署 | python script | docker-compose 全栈 |

### 11.2 量化收益（预期）

参见第 9 节性能目标表。

---

## 12. 面试叙事框架

### 12.1 简历段落

```
SQL Reconciliation Agent v2 - 工业级 NL2SQL Agent  | 2026.06 - 2026.07
技术栈：Python / LangGraph / LiteLLM / Qdrant / SQLite / Redis / FastAPI /
       OpenTelemetry / sqlglot / Pydantic

- 基于 LangGraph 实现 5-Node 状态机 + AgentContext 共享上下文，
  支持模式切换、checkpoint 中断恢复、token budget 控制
- Eval-Driven 改造：构建 50 条 Golden Set + 4 维 metric（Exec-Accuracy /
  Semantic-Match / Latency / Token Cost），每次架构改动 regression 验证
  v2 vs v1 准确率 60%→88%，token 成本下降 42%
- Hybrid RAG：BM25 + Dense（bge-small-zh）+ RRF 融合 + Cross-Encoder Rerank
  MRR@5 提升 37%；升级为 RAG-as-Tool，Agent 主动决策何时检索
- 三层记忆系统：Working LRU → Episodic 重要性打分 → Semantic LLM Consolidation
  + 衰减淘汰；Self-Evolution Sandbox 通过 Golden Set 子集 dry-run 验证才入库，
  3 轮自演进准确率单调上升 80→85→88%
- 工业级安全：sqlglot AST 替代关键字黑名单，10 条注入用例拦截率 100%
- 全链路可观测：OpenTelemetry + Phoenix UI，每次 query 完整 span 树
- 一键部署：docker-compose 全栈（app + Qdrant + Redis + Phoenix）
```

### 12.2 故事化叙事（面试主动暴露）

> "v1 我自研了一套 mini Agent 框架，跑通后自己做了一次 code review，
> 发现三个根本性问题：
> 
> 1. 双层 Agent 没共享 context，路由后两个 Agent 各自初始化一套依赖
> 2. 三层记忆名字借了认知科学，但没有真正的 promotion 和 consolidation
> 3. 自进化沉淀经验没有质量门槛，垃圾经验会污染知识库
> 
> 所以决定 v2 切换到 LangGraph，引入 Eval-Driven 改造方法论，
> 重新设计了 AgentContext 共享模式、Sandbox 质量门槛、Memory promotion 机制。
> 
> 这个项目对我最大的收获是：Agent 工程的真正难点不在框架，
> 而在评测、可观测性、和经验沉淀的 governance。"

### 12.3 面试常见追问 ⇄ 答题点

| 追问 | 答题核心 |
|------|---------|
| 为什么用 LangGraph 不用 AutoGen / CrewAI | state machine 显式 + checkpoint + 调试可视化 |
| 为什么 RAG 要 Hybrid | 长尾 query 召回率，BM25 在术语精确匹配上优于 Dense |
| Sandbox 怎么保证不退化 | Golden Set 子集 dry-run，准确率不下降才入库 |
| Skill 多了 prompt 会爆炸 | Skill KB 也走 RAG，按 query 检索 top-3 |
| OpenTelemetry vs LangSmith | OTel 是标准，可换厂商；LangSmith 是 SaaS lock-in |
| 怎么证明自进化有效 | 3 轮在 Golden Set 上准确率单调上升的趋势图 |
| 如果 LangGraph PoC 失败 | 备选方案 B：自研 mini state machine + 同样的 AgentContext 模式 |

---

## 13. 实施 Roadmap 摘要

详见 [plan.md](../../.codeflicker/mem-bank/threads/.../plan.md)。

| Stage | 周次 | 内容 | 验收 |
|-------|------|------|------|
| 0 | W0.5 | Golden Set + Eval Harness + v1 baseline | 50 条 case + 报告 |
| 1 | W1 | Pydantic Tool + sqlglot + LLM Gateway + OTel | 注入拦截 + cache 命中 |
| 2 | W2-3.5 | LangGraph 5-Node 状态机 + AgentContext | Golden 80% |
| 3 | W3.5-4.5 | Hybrid RAG + RAG-as-Tool | MRR@5 +30% |
| 4 | W4.5-6 | Memory v2 + Self-Evolution Sandbox | 3 轮单调上升 |
| 5 | W6-6.5 | FastAPI + Docker + 文档 | 一键启动 |

---

## 14. 附录

### 14.1 参考资料
- [LangGraph](https://langchain-ai.github.io/langgraph/)
- [sqlglot](https://github.com/tobymao/sqlglot)
- [Qdrant](https://qdrant.tech/)
- [Phoenix](https://github.com/Arize-ai/phoenix)
- [LiteLLM](https://github.com/BerriAI/litellm)
- [BGE-Reranker](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [RRF Paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)

### 14.2 关键术语表

| 术语 | 含义 |
|------|------|
| ReAct | Reasoning + Acting，单步推理执行 |
| Plan-and-Solve | 先全局规划再分步执行 |
| RRF | Reciprocal Rank Fusion，多路检索融合算法 |
| Consolidation | 认知科学术语，episodic 归纳为 semantic |
| Sandbox | 隔离环境验证候选 skill |
| Golden Set | 标准评测集 |
| Exec-Accuracy | SQL 执行结果集匹配率 |
| MRR@k | Mean Reciprocal Rank at k |
| OTel | OpenTelemetry，可观测性标准 |

---

**End of Document**
