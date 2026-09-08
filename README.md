# AgentOpsHub

Enterprise AI Agent Platform — 企业技术支持后端。

**已实现：Phase 0–2（后端基础、持久化、内部 cloud/local-ready LLM Gateway）。**
当前包含 FastAPI、配置/日志、PostgreSQL Tenant/Ticket、Alembic、内部 LLM Gateway，以及离线模型测试和真实数据库测试。
尚无业务 CRUD HTTP 接口、认证、Agent runtime、RAG、MCP、前端、本地推理运行时或持久化 LLM tracing。
Redis/Qdrant 仍是基础设施预留，运行时仅使用 PostgreSQL。没有 benchmark 或性能声明。

## 本地 Windows 推荐环境：Conda + uv

Conda 只负责 Python 运行时隔离；uv 负责依赖解析、安装和锁定。
`environment.yml` 仅包含环境名称、Python 3.12 和 pip；
**`pyproject.toml` + `uv.lock` 是应用依赖唯一来源**，没有 requirements.txt。

前置条件：Conda、Git、uv；基础设施还需要 Docker Desktop Linux engine。
uv 可独立安装，见 [官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)。
本工作区已有忽略目录中的 uv，脚本可自动发现；新 clone 需在 PATH 提供 uv。

在仓库根目录的 Conda-enabled PowerShell 中：

```powershell
conda create -n agentopshub python=3.12 -y
conda activate agentopshub
python scripts/dev.py sync
python scripts/dev.py env-info
```

环境已经存在时跳过 create。shell 激活不可靠时，使用此次实际验证的确定性入口：

```powershell
conda run -n agentopshub python scripts/dev.py sync-check
conda run -n agentopshub python scripts/dev.py sync
conda run -n agentopshub python scripts/dev.py env-info
conda run -n agentopshub python -c "import fastapi, pydantic, pytest, sqlalchemy, asyncpg; print('imports-ok')"
```

`sync` 动态设置子进程的 `UV_PROJECT_ENVIRONMENT=sys.prefix`，运行：
`uv sync --locked --inexact --python <当前 sys.executable> --no-python-downloads`。
先用 dry-run 验证目标，再实际同步并检查解释器/包路径。`--inexact` 保留 Conda 的
pip/setuptools 等引导包，避免 exact sync 删除环境管理器安装的额外包；项目依赖仍采用锁定版本。
脚本拒绝向 base Conda 或未隔离的系统 Python 安装。升级/移除依赖时需要检查残留包，
不能把 inexact sync 当作环境清理命令。

测试、mypy、Ruff、Alembic、Uvicorn 都使用 **`sys.executable -m ...`**。
本地 Conda 工作流不要套用裸 `uv run`，以免它另建项目 .venv。
旧 Phase 0 .venv 已在 Conda 回归、HTTP 验证及 hook 迁移通过后删除。

修改依赖声明后，用 `python scripts/dev.py lock` 更新 uv.lock，再执行 `sync`。
它显式选择当前解释器且禁止自动下载 Python，不会无意升级已有锁定依赖。

## 启动应用与数据库

```powershell
conda run -n agentopshub python scripts/dev.py init-env
conda run -n agentopshub python scripts/dev.py infra-up
conda run -n agentopshub python scripts/dev.py db-upgrade
conda run -n agentopshub python scripts/dev.py db-current
conda run -n agentopshub python scripts/dev.py serve
```

`init-env` 创建 UTF-8 .env 并生成随机本地 PostgreSQL 密码，绝不覆盖已有 .env。
数据库连接信息直接复用 POSTGRES_* 配置；旧 Phase 0 .env 无需复制一份 DSN/密码。
API 默认地址 http://127.0.0.1:8000，开发文档 http://127.0.0.1:8000/docs。
production 环境禁用文档页面，但不代表已具备认证、TLS 或生产运维保障。

在另一终端验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

| 接口 | 语义 |
| --- | --- |
| GET /health | liveness；数据库不可用仍为 200，保持 Phase 0 status/service/version 契约 |
| GET /ready | 有界 SELECT 1；成功为 200，失败/未配置为确定性 503，无连接 URL/密码/stack trace |

ready 成功：`{"status":"ready","dependencies":{"postgresql":"ok"}}`。
ready 失败：`{"status":"not_ready","dependencies":{"postgresql":"unavailable"}}`。
它检查 PostgreSQL 连接和查询能力，不检查迁移版本、Redis 或 Qdrant；上线前单独运行迁移。
每个响应有服务端生成的 X-Request-ID；JSON 日志仅输出固定事件和允许的元数据。

## 配置

优先级：构造参数 > 环境变量 > 根目录 .env > 默认值。脚本从任意工作目录调用会切回仓库根。

| 变量 | 默认值/用途 |
| --- | --- |
| AGENTOPSHUB_APP_NAME | AgentOpsHub |
| AGENTOPSHUB_ENVIRONMENT | development；也支持 test / production |
| AGENTOPSHUB_LOG_LEVEL | INFO；支持标准大写日志级别 |
| AGENTOPSHUB_HOST / AGENTOPSHUB_PORT | 127.0.0.1 / 8000 |
| POSTGRES_HOST / POSTGRES_PORT | 127.0.0.1 / 5432 |
| POSTGRES_DB / POSTGRES_USER | agentopshub / agentopshub |
| POSTGRES_PASSWORD | 无代码默认密码；SecretStr；缺失/空值时不配置 engine |
| AGENTOPSHUB_DATABASE_POOL_SIZE | 5，max_overflow=0 |
| AGENTOPSHUB_DATABASE_CONNECT_TIMEOUT | 3 秒：连接与 pool checkout 超时 |
| AGENTOPSHUB_DATABASE_COMMAND_TIMEOUT | 5 秒：数据库命令超时 |
| AGENTOPSHUB_DATABASE_READY_TIMEOUT | 2 秒：ready 总 deadline |

上述超时和池大小是配置值，**不是实测性能数字**。URL 使用 SQLAlchemy URL.create 转义密码。
不记录 SQL 参数、密码、请求正文、认证头或原始异常文本；未允许的第三方日志消息会被省略。
Phase 0 的 LLM_* 占位变量不再用于新配置；内部 Gateway 使用 AGENTOPSHUB_LLM__... 嵌套配置。已有 .env 不会被自动改写。

## 持久化与事务

- `app/db/models`：Tenant、Ticket；UUID 主键、时区感知时间戳、唯一/FK/CHECK 约束。
- `TenantRepository`：create / get_by_id / get_by_slug。
- `TicketRepository`：create / get_by_id / list / update / delete；所有接口必须提供 tenant_id。
- 查询、更新、删除 SQL 同时限制 ticket_id 和 tenant_id；列表按 tenant_id 过滤。
- Python StrEnum + VARCHAR CHECK 保存 status/priority 的值，避免原生 enum 的独立类型生命周期。
- PostgreSQL 默认时间和 BEFORE UPDATE trigger 维护时间戳，直接 SQL 更新也有效。
- 应用 lifespan 创建一个惰性 AsyncEngine，退出时 dispose；每个事务创建独立 AsyncSession。
- `Database.transaction()` / function-scoped `TransactionSession` 负责 commit/rollback；repository 只 flush。

当前租户隔离是 repository 查询边界，调用方必须提供可信 tenant ID；尚无认证或 RLS。
没有对外开放 tenant/ticket 业务路由，不应将这些内部 API 当作已完成的权限系统。

## Alembic

schema 必须由 Alembic 管理，不在启动时 create_all 或自动迁移。
`alembic.ini`、`backend/migrations/env.py` 使用当前 Settings，文件中没有连接凭据。

```powershell
conda run -n agentopshub python -m alembic upgrade head
conda run -n agentopshub python -m alembic current
conda run -n agentopshub python -m alembic check
```

初始 revision：`20260908_01`。`downgrade base` 会删除本 revision 的表和数据，
本次往返验证仅在测试 runner 创建的临时数据库执行，不对未知数据库执行降级。

## 测试与质量检查

```powershell
conda run -n agentopshub python scripts/dev.py check
conda run -n agentopshub python scripts/dev.py test-integration
conda run -n agentopshub python scripts/dev.py hooks
conda run -n agentopshub python scripts/dev.py compose-check
```

`check` 执行 Ruff lint/format、strict mypy、pytest + branch coverage、Git 工作区/index whitespace 检查。
没有隔离数据库上下文时，integration 测试明确 skip；这不能作为数据库验收结果。
`test-integration` 创建独立 Compose project、随机数据库/密码、主机临时端口和 tmpfs PostgreSQL，
执行 **完整测试集**，然后启动真实 Uvicorn，检查 DB 停止前后的 /health 与 /ready。
finally 只清理本次创建的容器/网络。它不接受外部测试 DSN，不读取开发数据库状态。
fixture 校验 run ID 与测试数据库名，串行清理测试表，测试实际 commit、rollback 和数据库约束。
不要对同一个测试数据库启用并行 workers。

运行产物在忽略目录 `.artifacts/phase1-pytest.txt`、`phase1-coverage.json`、
`phase1-migrations.json`、`phase1-http.json`；实际结果摘要见 [progress.md](progress.md)。
测试 fixture 是明确的合成输入，不是 benchmark 数据集。

其他已验证命令：`python scripts/dev.py lint`、`format-check`、`typecheck`、`hooks-install`。
提交前运行 hooks-install，使 pre-commit 使用当前 Conda 解释器，随后在已激活环境中 `git commit`。
凭据规则检查 Git index，拒绝 dotenv/私钥/常见 key，以及 Python 凭据字面量；
它不把 `password=settings...` 等动态取值误判为硬编码密码。仍需人工审阅 staged diff。

## Docker 与跨平台边界

开发 Compose 保留 PostgreSQL、Redis、Qdrant，仅发布 127.0.0.1 端口，使用 named volumes。
`infra-up` 检查三项基础设施；应用 /ready 只检查 PostgreSQL。
`infra-down` 保留数据卷；PostgreSQL 密码只在空卷初始化时生效，改 .env 不会修改既有数据库密码。
测试 Compose 独立于开发 Compose，数据仅存放 tmpfs；密码由 runner 动态注入。
本机探测绕过系统 HTTP 代理。当前开发资源上限仍沿用 Phase 0，不代表实测占用。

**Conda 是 Windows 本地推荐方案，不是所有贡献者的强制要求。**
GitHub Actions 继续使用 setup-uv + 标准 Python 的临时环境，不安装 Conda。
CI 配置保留 Windows/Linux、Python 3.12/3.13 质量矩阵；Linux job 执行真实 PostgreSQL 测试。
远程 CI 需要实际推送后才能确认结果。本次没有推送。

AnyIO `<4.15` 兼容上限保持不变；升级 Starlette 时应凭真实测试结果重新评估。

## 内部 LLM Gateway（Phase 2）

`app/llm` 提供 normalized request/response、OpenAICompatibleProvider 和 LLMGateway。
应用依赖内部合约，provider/model/endpoint 通过 profile 与逻辑 route 选择；不按 cloud/local 分支。
DeepSeek、Qwen、generic 是配置模式，复用同一个 transport。它们已通过 MockTransport 验证，
**本次没有调用真实云模型或本地模型，不能据此声称某个在线模型已验证兼容。**

沿用 httpx2 2.12.0 AsyncClient，不引入厂商 SDK。每个启用 profile 复用一个客户端，lifespan
退出时关闭；standalone 调用方需 await gateway.close()。只实现非流式文本 Chat Completions。
没有 `/llm/chat`、`/completion`、`/proxy` 等未认证代理接口，也没有自动 smoke/模型探测。
零 provider、缺少云 key、可选 localhost 服务离线均不影响启动；/ready 仍仅检查 PostgreSQL。

### Profile 与路由

.env.example 给出全部禁用的 DeepSeek、Qwen、generic 示例。Qwen endpoint 随账户区域/工作空间
配置，示例 .invalid 地址必须替换；generic localhost 地址只是未来端点示例，不包含模型服务器。
每个 profile 包含 name/kind/base_url/default_model/enabled/api_key/api_key_required/
deployment_type/capabilities/timeout/pricing。只允许无 URL 凭据/query/fragment 的 HTTP(S) URL；
允许 localhost、LAN 和私有域名。端点由受信任配置管理，不能直接采纳终端用户 URL。

api_key 为 SecretStr，不显示在 repr；必需 key 缺失在调用时抛 ConfigurationError。
generic 可显式设置 api_key_required=false；缺少可选 key 时不发送 Authorization，无需伪造 key。
DeepSeek/Qwen kind 保持必需认证。重定向关闭，TLS 默认验证，环境代理不会被隐式采用。

一个 Route 是有序 ModelTarget 列表；target 引用 profile，可覆盖 model 与能力声明。
例如 cloud→cloud、local→cloud、cloud→local、local-only 都可表示，已用 mock 验证。
route 定义也代表允许的数据转发范围；没有自动隐私分类或“本地优先”策略。

### Timeout、retry 与 fallback

默认 HTTP connect/read/write/pool 超时为 5/30/10/5 秒，单次总上限 45 秒，gateway 总上限
90 秒（含重试等待与 fallback）。这些是可配置上限，不是实测延迟指标。
整体 deadline 结束抛 LLMTimeoutError；调用方取消向下传播，不会被重试吞掉。

| 错误 | 同目标重试 | 默认 fallback |
| --- | --- | --- |
| 连接/传输失败、超时、408、429、5xx | 有界重试 | 尝试耗尽后进入下一候选 |
| 401 / 403 | 否 | 否；Route.fallback_on_authentication=true 才允许其他候选 |
| 400 / 404 / 409 / 其他非暂时 HTTP 错误 | 否 | 否 |
| 配置、能力、响应格式、结构化输出错误 | 否 | 否 |

max_attempts 包含首次调用，默认 2，上限 5；指数退避默认 full jitter，最大等待可配置。
只支持 Retry-After 的非负秒数，受 max_delay 限制；HTTP-date 格式暂忽略。
不保证请求重试不产生额外计费，生成请求可能已在 provider 侧完成。
所有候选按策略失败后抛 GatewayExhaustedError，携带安全 attempt history；非重试错误直接报告。

### 合约、usage、费用与结构化输出

Message 支持 system/user/assistant/tool 和文本；ToolDefinition/ToolCall 仅表示函数协议数据，
不执行工具。LLMRequest 包含 messages、route 或显式 target、temperature、max_output_tokens、
stop、JSON/结构化请求及 tools；未知字段和不合法参数会被拒绝。
LLMResponse 返回 provider/model、部署类型、消息、finish reason、tool calls、usage、
单次 attempt history、整体单调时钟耗时、request ID、provider request ID 和可选 cost。
日志中的 model 使用配置目标，不信任响应中任意 model 文本。

usage 缺失保持 None；仅在 input/output 都已知且 total 缺失时相加，provider 提供的 total 保留。
ModelPricing 使用 Decimal、币种及可选 effective_date，输入/输出分别按百万 token 单价计算。
没有内置厂商价目表。缺少价格或任一必要计数时 cost=None；本地模型也不默认免费。
费用只估计成功响应，失败尝试的计费未知，不是整次调用账单。

capabilities 明确声明 tool_calling/json_mode/structured_output，默认全部 false。
结构化请求优先使用 native json_schema；只有 json_mode 时发送 JSON mode 和 schema 指令。
两种能力均未声明则明确失败。`generate_structured(request=..., response_model=...)` 最终始终
执行严格 JSON 语法与 Pydantic v2 校验；NaN/Infinity、非法 JSON、schema 不匹配或截断响应
抛 StructuredOutputError，不自动重新生成。attempt history 的 success 表示 HTTP/协议成功，
不代表随后的应用 schema 校验一定成功。

日志只增加 llm_attempt/llm_result 固定事件和允许元数据；prompt、响应正文、API key、
Authorization、原始错误正文默认不记录。兼容请求 ID context，独立使用无需 HTTP 请求。
provider 响应缓冲有上限；工具参数仍是不可信数据，未来执行者须验证和授权。

### 验证与明确边界

```powershell
conda run -n agentopshub python -m pytest backend/tests/llm -q
conda run -n agentopshub python scripts/dev.py check
conda run -n agentopshub python scripts/dev.py test-integration
```

LLM 测试阻止默认网络 transport，使用 MockTransport 经过真实序列化/HTTP/解析代码；
退避 sleep 和随机数可注入，测试不真实等待重试间隔。deadline 测试只等待短事件循环定时器。
全套验收保留真实 PostgreSQL 回归；无需模型 key、互联网模型服务、GPU 或本地模型服务器。

没有安装 CUDA/ML 框架或下载权重。Local endpoint 支持不等于本地运行时管理；
Agent、LangGraph、RAG、MCP、embedding、持久化 LLM tracing 均未实现。

## 设计与 License

[ARCHITECTURE.md](ARCHITECTURE.md) 区分现有实现与未来设计；
[ROADMAP.md](ROADMAP.md) 记录阶段状态。Phase 2 已实现；Phase 3 尚未开始。
MIT，见 [LICENSE](LICENSE)。
