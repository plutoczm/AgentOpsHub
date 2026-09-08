# AgentOpsHub Roadmap

每阶段独立验收、小步提交。本文为计划，不将未来能力描述为已完成。
Phase 0 和 Phase 1 已完成；Phase 2 尚未开始，不会自动进入下一阶段。

| 阶段 | 目标 | 可验证的退出条件 |
| --- | --- | --- |
| Phase 0 ✅ | 架构与 bootstrap | 最小 API、配置/日志、测试、uv lock、质量工具、Compose、Windows 文档、初始提交 |
| Phase 1 ✅ | Conda 环境与持久化基础 | 专用 Conda + uv、SQLAlchemy/Alembic、Tenant/Ticket repository、事务与租户条件、/ready、真实 PG 迁移/隔离/回滚测试 |
| Phase 2 | Model gateway | OpenAI-compatible 协议、mock transport 测试、timeout/retry/fallback、usage/cost unknown 语义和预算 |
| Phase 3 | 文档摄取与 dense RAG | 文档状态机、解析/语义分块、embedding adapter、Qdrant adapter、幂等摄取/删除与引用 |
| Phase 4 | Hybrid retrieval 与评测基线 | BM25、RRF、独立 evaluation package、人工审核 relevance labels、Recall/MRR/Hit Rate 与原始运行产物 |
| Phase 5 | Stateful Agent | LangGraph、intent/planning/tool/reflection、对话与 memory、五个工具、执行预算、持久化 checkpoint |
| Phase 6 | 审批与 MCP | HITL 暂停/恢复/拒绝、幂等写操作、MCP server/client、schema/权限/超时测试 |
| Phase 7 | 安全与运维强化 | 认证授权、租户隔离系统测试、注入对抗集、OTel tracing、限流、审计、备份恢复和故障演练 |
| Phase 8 | Reranking 与真实对照实验 | 固定数据的 dense/hybrid/hybrid+reranking 对比、答案评分、延迟/token/cost 产物与复现说明 |
| Phase 9 | UI 与展示部署 | 上传/对话/引用/审批/trace 展示，经过验证的演示与部署文档 |

安全边界从相关模块第一版开始落实，不等到 Phase 7 才补权限。
Phase 1 的租户上下文测试使用显式测试身份；生产认证接入前不开放真实企业数据。
Phase 5 在 Phase 6 完成审批之前必须拒绝未经批准的有副作用工具。

## Phase 0 验收清单

- ARCHITECTURE.md / ROADMAP.md 和 src layout。
- pyproject.toml / uv.lock / .env.example / .gitignore / .gitattributes。
- FastAPI factory、GET /health、Pydantic settings、JSON logging、请求 ID、通用 500。
- PostgreSQL / Redis / Qdrant 开发 Compose 与有界就绪探测。
- pytest、Ruff、strict mypy、pre-commit、Windows/Linux CI 配置。
- Python 开发脚本及 PowerShell 等价命令、实际测试与 git diff --check。
- 初始 README 只列当前实现；没有未经运行的性能或质量数字。
- MIT LICENSE、敏感文件检查、初始 Git commit。

## Phase 1 已完成与下一阶段建议

已完成 Conda 迁移和旧 .venv 退役、事务/连接池生命周期、Tenant/Ticket、租户查询边界、
确定命名约束、Alembic 初始迁移与往返、PostgreSQL readiness、隔离真实数据库测试。
没有实现认证或 HTTP 工单 CRUD；传入 tenant_id 由调用方负责可信性，尚无 RLS。

Phase 2 建议仅实现自有模型协议与 OpenAI-compatible gateway 的 transport mock 测试，
先验证 timeout/retry/fallback 和 usage/cost unknown 语义，再按显式预算启用真实 provider 验证。
不在 Phase 2 同时堆叠 LangGraph、RAG 或 MCP。本次没有开始任何 LLM 功能。

## 实验与提交规则

测试 fixture 可以是明确标注的合成边界输入，但不得充当真实 benchmark 数据或业务实验结论。
付费模型集成测试需显式启用并配置预算；CI 默认无 key、离线 mock transport。
每次提交先通过相关测试和 staged review，禁止提交 .env、凭据、企业原始文档和本地存储。
uv.lock 与依赖声明一同提交；README 数字必须引用公开可复现的命令及运行产物。
