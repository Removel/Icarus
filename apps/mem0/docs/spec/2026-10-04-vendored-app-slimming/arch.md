# Mem0 在 Icarus 中的裁剪设计

- 日期：2026-10-04，首次提交前核对日期。
- 状态：第一批已在隔离工作区实施、待提交；已验证 Mem0/dashboard 源码镜像和真实 PostgreSQL/历史持久化服务链，未调用远程模型。
- [跨应用约定](../../../../../spec/2026-10-04-vendored-app-slimming.md) · [第一批实施计划](plan.md)

## 背景与目标

Mem0 从 `mem0ai/mem0@c7ee362aff94a369af70f13f2b4f853f6793ff4c` 以普通源码导入 `apps/mem0`。原导入目录同时含 Python SDK/self-hosted server 与上游 TypeScript SDK、独立 CLI、十五种第三方集成、示例、文档站和社区自动化。第一批已裁撤这些外围；核心、服务与旧 UI 保留。

Icarus 的实际需求是运行修改后的 SDK 和 REST 服务，通过 HTTP 与 Agent/WebUI 交互，而不是发布完整 Mem0 产品生态。第一批只裁外围，保留 Python 核心和维护能力；上游来源冻结不妨碍 Icarus 补丁和安全维护。

## 当前代码架构与依据

| 层 | 现状 | 代码依据 |
| --- | --- | --- |
| 根控制面 | install/start/stop/status 通过 Mem0 compose adapter | `scripts/icarus/main.py` |
| 应用适配器 | 根 `.env` 的安全解析、环境校验、创建数据目录、调用 Compose | `scripts/icarus-compose.sh`、`scripts/icarus_compose.py` |
| 部署 | mem0 API、PostgreSQL/pgvector、dashboard 三服务；API :8888、dashboard :3000 | `server/docker-compose.yaml` |
| SDK | `Memory`/`AsyncMemory`、provider/config、SQLite history、OSS notices JSON | `mem0/`、`pyproject.toml` |
| REST | FastAPI、auth/API key/db/models/routers、alembic、速率限制/telemetry | `server/` |
| Agent 消费 | 经 HTTP adapter，不直接 import vendored SDK | `apps/agent/src/agent_orchestration/plugins/memory/mem0_http_adapter.py` |
| 服务构建 | context 为 app；server requirements + local editable SDK；复制 poetry.lock 但 pip 不消费锁 | `server/dev.Dockerfile` |

运行数据位于 `$ICARUS_DATA_DIR/services/mem0`；服务启动/停止与数据归属在本批不变。核心轮询/事件/Plugin 架构不受本批影响。

## 第一批删除、迁移与保留

### 删除外围

| 内容 | 功能 | 裁剪说明 |
| --- | --- | --- |
| `.claude-plugin`、`.codex-plugin`、`.cursor-plugin`、`.kimi-plugin`、`.agents`、marketplace.json | 外部 coding agent 插件分发清单 | Icarus 不依赖这些分发渠道 |
| integrations/ | Claude/Codex/Cursor/Kimi/OpenClaw/PI/n8n/Zapier/Vercel 等独立集成 | 不属于自建服务链路；随分发元数据删除 |
| examples/ | demo、notebooks、样例应用/扩展 | 不作为 app 运行输入；删前检查 retained tests 与资源引用 |
| mem0-ts/ | TypeScript SDK | Icarus 侧用 HTTP，不构建此 SDK |
| cli/ 与 mem0-cli skill | 独立 Node/Python CLI | 与根生命周期控制面不同，本仓库无消费路径 |
| 上游 docs 站 | mdx、图片、导航、llms、Platform/open-source 教程 | 保留 Icarus `docs/spec/`；已改 add.mdx 的 preserve_input_language 说明归入 MODIFICATIONS/Icarus README |
| 嵌套 .github/ | 上游 CI、release、CLI/plugin 发布、CLA/issue/vouch/labeler 自动化 | 根 CI 接管必要检查，不迁发布凭据及上游贡献门禁 |
| 上游治理、Makefile/pre-commit、失效 agent 指引 | 社区协作/开发入口 | 用根规范和本应用新的开发说明替代，不全仓 lint 重排 |
| 文档覆盖脚本、未调用的 prepare_local_env.py | 文档站校验/导入期 env 辅助 | 文档站工具随站裁撤；env 辅助按当前无调用路径判断，不臆测其来源 |

### 必须先保全的例外

- `tests/test_oss_to_platform_migrate.py:14` 确实执行 `scripts/oss-to-platform-migrate.sh`。脚本迁到 `tests/fixtures/`，只改定位，保留脚本内容及原测试断言；不以“无外部调用”删掉全量测试输入。
- `server/scripts/seed.sh`、`reset_admin_password.py`、`prune_request_logs.py` 属于初始化、密码恢复、日志清理，第一批和未来 UI 裁撤均不自动删除。上游 server Makefile 删除前把入口写入 server README。
- SDK 的 `mem0/memory/oss_notices_config.json` 是打包/runtime 资源，不属于待删插件 JSON。
- `mem0/AGENTS.md`、`tests/AGENTS.md` 中仍适用的 provider/API/testing 约束更新后保留；被删入口与文档站指引替换为 Icarus 路径。
- 上游 LICENSE/skill LICENSE、server migration/init-db、原 tests 全留。Docker metadata 所需 LICENSE 必须通过 wheel/image 实际验证，不能只看 COPY 清单。

### 开发 skill 迁移

五个 bundle 整体迁至根 `.agents/skills/`：`mem0`、`mem0-integrate`、`mem0-test-integration`、`mem0-vercel-ai-sdk`、`mem0-oss-to-platform`。各自 LICENSE、references/client/辅助脚本原样带走；修旧本地目录和 mem0-cli 链接。

这些是开发参考或受审批的上游流程，不作为 Icarus 运行时组件。Platform/Vercel/OSS-to-Platform 内容不会因保留 skill 而自动安装、集成或执行，不得覆盖根 SDD/分支授权规则。

## 当前结构（第一批已实施，待提交）

```text
apps/mem0/
├── README.md
├── LICENSE
├── MODIFICATIONS.md
├── pyproject.toml
├── poetry.lock
├── requirements-dev.txt
├── mem0/                      # Python 核心、providers、runtime JSON
├── server/                    # API/auth/db/alembic/routers/init-db
│   ├── dev.Dockerfile
│   ├── requirements.txt
│   ├── docker-compose.yaml
│   ├── README.md
│   ├── dashboard/             # 第一批保留
│   └── scripts/               # 保留运维能力
├── scripts/
│   ├── icarus-compose.sh
│   ├── icarus_compose.py
│   ├── install.sh
│   └── test.sh
├── tests/
│   └── fixtures/oss-to-platform-migrate.sh
└── docs/spec/2026-10-04-vendored-app-slimming/
    ├── arch.md
    └── plan.md
```

`.venv` 是本地忽略的开发环境，不跟踪；不向根/Agent 环境安装 SDK 服务依赖。

## 开发、测试和基建边界

`install.sh --dev` 创建 app .venv，安装本地 `[test,dev]`；默认 runner 运行 memory 两文件加布局/脚本契约测试。`--full` 执行整个保留 tests，provider/server 额外环境需单独准备，失败不隐藏。

server tests 的 bare import 与 auth fixture、可选 provider 的 import 必须按同环境基线区分。缺依赖/skip 不等于完整回归通过。完整失败集合由实际命令记录，不在设计里虚构 nodeid。

仓库层接管 CI、文本/SDD/治理、安装和 Makefile 聚合；本应用拥有 pyproject/poetry.lock、Docker/compose、dev/server deps 和测试。保留 poetry.lock 不宣称现有 pip 安装是锁定安装，也不在裁剪中切换整个依赖策略。

PR6 尚未合入的 history 审计、hatch icarus env 与 `docs/spec/2026-10-01-memory-history-attributes/` 合入后保留，不被文档站清理误删。

## 声明、验收与第二批

保留 Mem0 LICENSE 和每份迁移 skill 的原 LICENSE；MODIFICATIONS 追加冻结/裁剪/迁移/测试夹具记录，root notice 同时指 app 与根 skill。许可证内容不重写为 Icarus 所有。

验收包括：保留资源哈希、技能链接、原迁移脚本测试、默认/完整套件对比、Agent service_config 契约、wheel JSON/LICENSE、完整 Docker 构建。模型/服务 smoke 单独授权，用隔离测试数据，不重启用户当前服务或覆盖 `.env`。

第二批只评估 dashboard 删除和 compose 服务数 3→2；PR6 合入及 WebUI 实际验收是前提。后台 auth/API-key/运维能力不因 UI 不再暴露而裁撤；具体第二批计划另写。
