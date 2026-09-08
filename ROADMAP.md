# AgentOpsHub Roadmap

每阶段独立验收、小步提交。本文为计划，不将未来能力描述为已完成。
Phase 0/1/2 已接受；Phase 3 Tool Runtime Foundation 实现及本地验收完成。Phase 4 尚未开始。

| 阶段 | 目标 | 可验证的退出条件 |
| --- | --- | --- |
| Phase 0 ✅ | 架构与 bootstrap | 最小 API、配置/日志、测试、uv lock、质量工具、Compose、Windows 文档、初始提交 |
| Phase 1 ✅ | Conda 环境与持久化基础 | 专用 Conda + uv、SQLAlchemy/Alembic、Tenant/Ticket repository、事务与租户条件、/ready、真实 PG 迁移/隔离/回滚测试 |
| Phase 2 ✅ | Cloud/local-ready LLM gateway | OpenAI-compatible 协议、mock transport 测试、timeout/retry/fallback、usage/cost unknown 语义和预算 |
| Phase 3 ✅ | Tool Runtime Foundation | 类型化 contract、registry/executor、可信租户 context、写策略、三个内置工具、超时/取消、真实 PG 隔离/回滚与安全日志 |
| Phase 4 | LangGraph Agent Runtime | 复用 Gateway/ToolExecutor，可信上下文、有限工具循环、步数/时限预算、分类错误与取消；禁止自动重放 WRITE |
| Phase 5 | RAG Ingestion | 文档状态机、解析/分块、embedding 与存储适配、幂等摄取/删除与来源 |
| Phase 6 | Hybrid Retrieval + Reranking | BM25/dense、RRF、可选 reranker、knowledge_search、引用与独立检索评测 |
| 后续 | 审批、幂等与 MCP | HITL 暂停/恢复/拒绝、写幂等协议、MCP server/client、schema/权限/超时测试 |
| 后续 | 安全与运维强化 | 认证授权、RLS 评估、注入对抗集、OTel/持久化 tracing、限流、审计、备份和故障演练 |
| 后续 | UI 与展示部署 | 上传/对话/引用/审批/trace 展示，经过验证的演示与部署文档 |

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
不在 Phase 2 同时堆叠 LangGraph、RAG 或 MCP。Phase 2 已按上述边界实现 Gateway；没有开始 Agent、LangGraph 或 RAG。

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

建议下一步仅为 Phase 4 LangGraph Agent Runtime：有限状态/工具循环、Gateway 与 Executor 适配、
可信上下文、预算、错误/取消与离线 graph 测试。持久化 checkpoint、HITL 和写幂等应单独明确边界。
本次停止于 Phase 3；未安装 LangGraph，未开始 Phase 4/5/6，也未实现 RAG、MCP 或 SQL 工具。
