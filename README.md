# AgentOpsHub

Enterprise AI Agent Platform — 面向企业技术支持场景的后端项目。

**当前：Phase 0 / Architecture & Bootstrap。** 已有可运行的 FastAPI 骨架与开发工具；
尚未实现 Agent、LLM 调用、RAG、数据库业务读写、认证或前端。没有 benchmark 或性能声明。

## 已实现

- FastAPI application factory、GET /health、开发环境 OpenAPI 页面。
- Pydantic v2 配置校验、环境变量和 UTF-8 .env 加载。
- JSON 日志、服务端生成的 X-Request-ID、通用 500 响应。
- pytest、Ruff、strict mypy、pre-commit 与 staged secrets 检查。
- uv 依赖锁定、跨平台 Python 操作脚本。
- PostgreSQL、Redis、Qdrant 本地开发 Compose 配置和基础设施探测命令。
- Windows/Linux CI 工作流配置；远程运行状态需要实际推送后确认。

## 环境与安装

Python 3.12+，Git，uv。仅启动基础设施时需要 Docker Desktop 的 Linux containers engine。
不需要 GPU、本地 LLM、模型 API key。初始验证以 Python 3.12 为基线；CI 配置测试 3.12/3.13。

uv 安装入口：[官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)。
若本机已有可用 Python，可在用户自行选定的工具环境执行 `python -m pip install uv`；
Windows Store 的 python 占位程序不是真正的 Python，必要时使用解释器完整路径。

在仓库根目录打开 PowerShell（这些 uv/Python 命令也适用于其他 shell）：

```powershell
uv sync --locked
uv run --locked python scripts/dev.py init-env
uv run --locked python scripts/dev.py serve
```

`uv sync` 可按 .python-version 获取 Python；依赖只进入项目 .venv。
`init-env` 以 UTF-8 创建 .env，并生成随机本地 PostgreSQL 密码，已有文件保持原样。
默认 API 地址为 http://127.0.0.1:8000，文档为 http://127.0.0.1:8000/docs。
在另一终端验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

接口契约：`{"status":"ok","service":"AgentOpsHub","version":"0.1.0"}`。
此响应只证明 API 进程存活，不代表基础设施或模型已就绪。

也可直接运行 `uv run --locked uvicorn app.main:create_app --factory --no-access-log`。
需要使用 .env 的 host/port 时通过 `scripts/dev.py serve` 启动。

## 配置

从仓库根目录运行；脚本会主动切回该目录。优先级为构造参数、环境变量、.env、默认值。

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| AGENTOPSHUB_APP_NAME | AgentOpsHub | health 与 API 标题 |
| AGENTOPSHUB_ENVIRONMENT | development | development / test / production |
| AGENTOPSHUB_LOG_LEVEL | INFO | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| AGENTOPSHUB_HOST | 127.0.0.1 | dev.py serve 监听地址 |
| AGENTOPSHUB_PORT | 8000 | 1–65535，非法配置启动失败 |

production 模式关闭文档页面，不会自动补齐认证/TLS 等生产能力。
POSTGRES_* / REDIS_PORT / QDRANT_* 由 Compose 使用。LLM_* 是未来预留字段，
当前应用不读取、不校验它们，也不会发送模型请求。
日志只输出固定事件和允许元数据，不记录正文、认证头、原始异常或任意第三方消息文本。

## Docker Desktop 基础设施

先启动 Docker Desktop，并确认 `docker info` 可连接 Linux engine，然后运行：

```powershell
uv run --locked python scripts/dev.py infra-up
uv run --locked python scripts/dev.py infra-check
docker compose ps
uv run --locked python scripts/dev.py infra-down
```

`infra-up` 等待 PostgreSQL/Redis healthcheck，再从主机探测 Qdrant /readyz。
Qdrant 容器本身未配置 healthcheck，因此单独 `docker compose up --wait` 不能证明它已就绪。
`infra-down` 停止并移除本项目容器/网络，保留数据卷。

默认本机端口：PostgreSQL 5432、Redis 6379、Qdrant HTTP 6333 / gRPC 6334。
端口冲突时修改 .env 中对应 *_PORT；所有服务仅发布到 127.0.0.1。
named volumes 保存数据，避免 Windows 目录映射的 Qdrant 存储兼容问题。
容器内存上限 PostgreSQL 768 MiB、Redis 256 MiB、Qdrant 1 GiB，合计 2 GiB；
这些是配置上限，**不是实测内存占用或性能数据**，不包含 Docker VM 与操作系统开销。
首次拉取镜像需要联网；开发 Compose 不包含生产 TLS/完整认证/备份配置。
PostgreSQL 密码仅在空数据卷初始化时生效，修改 .env 不会自动更改已有数据库密码。

## 开发与验证

```powershell
uv run --locked python scripts/dev.py check
uv run --locked python scripts/dev.py hooks
uv run --locked python scripts/dev.py compose-check
```

| 操作 | PowerShell / 跨平台命令 |
| --- | --- |
| 测试和 coverage | `uv run --locked python scripts/dev.py test` |
| Lint | `uv run --locked python scripts/dev.py lint` |
| 格式检查 / 格式化 | `uv run --locked python scripts/dev.py format-check` / `format` |
| 严格类型检查 | `uv run --locked python scripts/dev.py typecheck` |
| 本地安装提交 hook | `uv run --locked python scripts/dev.py hooks-install` |
| staged secret 检查 | `uv run --locked python scripts/dev.py secrets` |
| Compose 语法与变量检查 | `uv run --locked python scripts/dev.py compose-check` |

单元/API 测试不依赖 Docker 和外部模型；测试输入是明确的合成边界 fixture，不是 benchmark 数据。
`check` 执行 lint、format-check、mypy、pytest 和 Git 工作区/index whitespace 检查。
`compose-check` 仅校验配置，不代表镜像已成功拉取或服务能启动。
CI 配置含 Windows/Linux 质量检查和 Linux Docker integration job；本地结果见 [progress.md](progress.md)。

## Git workflow 与密钥

未激活虚拟环境时可用 `uv run --locked git commit -m "描述本次修改"`，让 hook 使用项目 Python。

小步修改 → 相关测试 → `git add <明确文件>` → `git diff --cached` → secrets/hooks → commit。
禁止提交 .env、API keys、数据库文件或企业私有文档。脚本扫描 Git index，拒绝环境凭据文件、
private keys、常见 sk- key 与 dotenv 凭据赋值；它不是完整 DLP 系统，仍需审阅 staged diff。
不把 .env.example 的占位符替换成真实 key。远程 CI 默认无需任何 provider secret。

## 设计与下一步

[ARCHITECTURE.md](ARCHITECTURE.md) 说明目标架构与已实现边界；
[ROADMAP.md](ROADMAP.md) 定义后续阶段；[evaluation/README.md](evaluation/README.md) 预留独立评测包。
下一阶段建议先实现 PostgreSQL/Alembic 与 tenant/ticket repository；本次未开始 Phase 1。

## License

MIT，见 [LICENSE](LICENSE)。

## 当前环境注意事项

本次工作区的 uv 位于忽略目录 `.tools/uv-package/bin/uv.exe`，没有修改全局 PATH。
已同步环境可直接使用 `./.venv/Scripts/python.exe scripts/dev.py check` 或 `serve`；
新 clone 按上面的官方 uv 安装流程操作。

当前锁定组合为 Starlette 1.6 与 AnyIO 4.14.2；AnyIO 4.15 的弃用别名与
Starlette TestClient 冲突，因此 resolver 暂设 `<4.15` 兼容上限。
升级 Starlette 时应移除上限并重跑测试；测试仍将警告视为错误。
