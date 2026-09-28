# AgentOpsHub Roadmap

每阶段独立验收、小步提交。本文为计划，不将未来能力描述为已完成。
Phase 0-6 accepted. Phase 7A Bounded Context Engineering + Knowledge Agent Loop is implemented and locally validated. Phase 7B has not started.

| 阶段 | 目标 | 可验证的退出条件 |
| --- | --- | --- |
| Phase 0 ✅ | 架构与 bootstrap | 最小 API、配置/日志、测试、uv lock、质量工具、Compose、Windows 文档、初始提交 |
| Phase 1 ✅ | Conda 环境与持久化基础 | 专用 Conda + uv、SQLAlchemy/Alembic、Tenant/Ticket repository、事务与租户条件、/ready、真实 PG 迁移/隔离/回滚测试 |
| Phase 2 ✅ | Cloud/local-ready LLM gateway | OpenAI-compatible 协议、mock transport 测试、timeout/retry/fallback、usage/cost unknown 语义和预算 |
| Phase 3 ✅ | Tool Runtime Foundation | 类型化 contract、registry/executor、可信租户 context、写策略、三个内置工具、超时/取消、真实 PG 隔离/回滚与安全日志 |
| Phase 4 complete | Bounded LangGraph 1.2 Agent Runtime | Trusted Runtime context, Gateway/Executor, budgets/deadline, duplicate-ID protection, offline and real PG tests |
| Phase 5 complete | Deterministic Knowledge Ingestion & Reference Scenario Foundation | Trusted tenant/namespace, UTF-8 parsing/chunking, immutable revisions, idempotency, provenance, real PostgreSQL isolation/concurrency and synthetic scenario corpora |
| Phase 6 complete | Retrieval Evaluation Foundation + Deterministic Lexical Baseline | PostgreSQL FTS, trusted/latest-only SQL, fixed 16-document/48-query synthetic benchmark, reproducible metrics and observed failure analysis |
| Phase 7A complete | Bounded Context Engineering + Knowledge Agent Loop | Trusted runtime namespace, query-only READ_ONLY tool, bounded provenance-preserving evidence, real PostgreSQL and deterministic Agent integration evaluation |
| Phase 7B next | Budgeted Live Agent Task Evaluation + Minimal Agent Tracing | Explicit live-model budget, task outcome rubric, privacy-safe minimal tracing; separate model quality from scripted integration correctness |
| Phase 8 | Lexical vs Dense Retrieval Baseline + Qdrant, if still justified | Same contracts/datasets/metrics; explicit embedding abstraction and justified provider/model; compare with lexical; no Hybrid/RRF initially |
| Phase 9 | Lexical vs Dense error analysis + Hybrid/RRF adoption decision | Adopt fusion only if measured complementary failures and gains justify it |
| Phase 10 | Reranking evaluation, if justified | Compare against retained simpler retrieval baselines |
| Phase 11 | Memory | Explicit authorization, retention and deletion |
| Phase 12 | Agent Skills | Reviewed instruction/tool composition |
| Phase 13 | MCP | Preserve trusted context and tool authorization |
| Phase 14 | Agent Harness / AgentSpec | Reproducible specifications |
| Phase 15 | Grounding / Hallucination Evaluation | Separate retrieval from generation quality |
| Phase 16 | OpenTelemetry Agent Observability | Privacy-safe telemetry |
| Phase 17 | Containment / HITL / Policy | Permission, approval and side-effect control |
| Later | A2A with SupportOps/DataCopilot, AG-UI, Local Model Runtime | Independent adoption and deployment decisions |


安全边界从相关模块第一版开始落实，不等到后续安全阶段才补权限。
Phase 1 的租户上下文测试使用显式测试身份；生产认证接入前不开放真实企业数据。
Phase 4 必须保留默认拒绝写策略；审批/幂等完成前不可自动批准或重放有副作用工具。

## Phase 0 验收清单

- ARCHITECTURE.md / ROADMAP.md 和 src layout。
- pyproject.toml / uv.lock / .env.example / .gitignore / .gitattributes。
- FastAPI factory、GET /health、Pydantic settings、JSON logging、请求 ID、通用 500。
- PostgreSQL / Redis / Qdrant 开发 Compose 与有界就绪探测。
- pytest、Ruff、strict mypy、pre-commit、Windows/Linux CI 配置。
- Python 开发脚本及 PowerShell 等价命令、实际测试与 git diff --check。
- 初始 README 只列当前实现；没有未经运行的性能或质量数字。
- MIT LICENSE、敏感文件检查、初始 Git commit。

## 已完成基础与后续边界

已完成 Conda 迁移和旧 .venv 退役、事务/连接池生命周期、Tenant/Ticket、租户查询边界、
确定命名约束、Alembic 初始迁移与往返、PostgreSQL readiness、隔离真实数据库测试。
没有实现认证或 HTTP 工单 CRUD；传入 tenant_id 由调用方负责可信性，尚无 RLS。

Phase 2 已实现自有模型协议与 OpenAI-compatible gateway 的 transport mock 测试，
已验证 timeout/retry/fallback 和 usage/cost unknown 语义；真实 provider 验证仍需后续显式预算。
不在 Phase 2 同时堆叠 LangGraph、RAG 或 MCP。Phase 2 已按上述边界实现 Gateway；Agent/LangGraph now compose it in Phase 4; Phase 7A later added internal knowledge retrieval; live-model quality remains unmeasured.

## 实验与提交规则

测试 fixture 可以是明确标注的合成边界输入，但不得充当真实 benchmark 数据或业务实验结论。
付费模型集成测试需显式启用并配置预算；CI 默认无 key、离线 mock transport。
每次提交先通过相关测试和 staged review，禁止提交 .env、凭据、企业原始文档和本地存储。
uv.lock 与依赖声明一同提交；README 数字必须引用公开可复现的命令及运行产物。

## 后续独立阶段：Local Model Runtime（未开始）

面向 RTX 5060 Ti 8 GB 的后续可选实验：小型量化模型、Ollama/llama.cpp 候选路径，
可选受支持服务器上的 vLLM；通过相同 generic OpenAI-compatible profile 接入。
届时真实测量 VRAM、TTFT、tokens/sec、端到端延迟，以及 cloud/local 质量、费用和隐私取舍。
当前不安装上述运行时，不下载权重，不宣称模型大小或性能支持。

## Phase 3 完成与停止点

已实现 system_status、ticket_search、ticket_create；未增加依赖或数据库表，未公开执行接口。
工具严格校验输入/输出，tenant_id 来自可信 context；READ_ONLY 不 commit，WRITE 默认拒绝，
服务拥有事务并在提交前构造输出，executor 不做任何自动重试。
真实 PostgreSQL 全套 266 tests passed（含 53 integration）；具体证据见 progress.md。

## Phase 4 completion and stopping point

Bounded LangGraph 1.2 runtime composes accepted Gateway/Executor with trusted context,
whole-run deadline, semantic budgets and same-run call-ID replay protection.
Sequential tools retain individual transactions; different IDs are not business idempotency.
No graph retry, checkpointer, Store, public execution endpoint or external tracing.

Exact validation results and process-environment recovery are recorded in progress.md.
This historical Phase 4 boundary remains intact. Phase 5 now adds an independent
deterministic workflow; ingestion code is not inserted into Agent graph nodes.

## Phase 5 completion and stopping point

Shared reference-scenario contracts and deterministic knowledge storage are implemented.
SupportOps and DataCopilot remain independent repositories, without submodules or remote
protocol integration. Six synthetic documents produce 18 default chunks with repeated
ingestion, history, provenance, rollback and tenant/namespace/concurrency checks.
Exact executed quality results are recorded in progress.md.

Phase 5 remains accepted. Phase 6 now adds retrieval/evaluation without changing ingestion
semantics. Later A2A, AG-UI and local model runtime retain independent adoption gates.

Do not assume dense > lexical, hybrid > dense, reranking > hybrid or semantic >
deterministic chunking. Retain simpler implementations until controlled experiments
justify extra cost and failure modes.


## Phase 6 completion

PostgreSQL FTS lexical retrieval and a deterministic source-level evaluator are implemented.
This is not BM25, Dense, Hybrid RAG or answer generation. The fixed synthetic benchmark
exposes paraphrase misses (0/4 per scenario), ordinary wording misses (2/4 per scenario),
acronym and chunk-boundary limitations. Actual tables and caveats are in
evaluation/retrieval/measured-results.md; quality evidence is in progress.md.

Phase 6 is retained as the lexical baseline. Its paraphrase and ordinary-wording failures
make Dense worth comparing, but do not establish that it improves Agent task outcomes.

## Phase 7A completion and stopping point

Phase 7A connects bounded evidence assembly, trusted namespace context, the explicit
`knowledge_search` tool, PostgreSQL FTS and the existing AgentRuntime. The deterministic
integration dataset passed twice; real-model quality remains unmeasured. Full results and
limits are in `evaluation/knowledge-agent/measured-results.md`.

STOP after Phase 7A. Do not begin Phase 7B, MCP, or Dense retrieval in this delivery. The
next stage, if authorized, is Phase 7B — Budgeted Live Agent Task Evaluation + Minimal Agent
Tracing. This measures whether real-model behavior uses evidence correctly before any
retrieval quality expansion is considered.
