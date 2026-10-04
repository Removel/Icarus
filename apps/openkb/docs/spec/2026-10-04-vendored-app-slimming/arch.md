# OpenKB 在 Icarus 中的裁剪设计

- 日期：2026-10-04，首次提交前核对日期。
- 状态：第一批已在隔离工作区实施、待提交；业务核心与 UI 未改变，源码镜像/安装包资源/Workbench/隔离服务与重建持久化已验证。
- [跨应用约定](../../../../../spec/2026-10-04-vendored-app-slimming.md) · [第一批实施计划](plan.md)

## 背景与目标

OpenKB 从 `VectifyAI/OpenKB@ff54396e575ee6feb0113b631a34caa082b441cc` 以普通源码导入 `apps/openkb`。Icarus 通过 REST 消费知识查询、编译、阅读等能力，同时现有容器仍构建独立 Workbench。

第一批去掉上游演示和分发元数据，开发导航 skill 归根 `.agents`，但保留包核心、CLI/API 的实现、三个 deck 运行时 skill、Workbench、锁文件及完整测试。上游来源冻结不等于禁止 Icarus 自己维护。

## 当前代码架构与依据

| 层 | 现状 | 代码依据 |
| --- | --- | --- |
| 根控制面 | 通过 compose adapter install/start/stop/status | `scripts/icarus/main.py` |
| 应用适配器 | 解析根 env、校验 Secret、管理 app 数据目录 | `scripts/icarus_compose.py` |
| 容器入口 | 预建 managed KB，启动 FastAPI | `scripts/prepare_managed_kb.py`、`icarus-container-start.sh` |
| 业务核心 | 编译/lint/query/chat、schema/convert/index、locks/mutation、deck/skill/watch | `openkb/` |
| CLI | 命令入口且包含 API/初始化依赖的 helper | `openkb/cli.py`、api.py/api_helpers.py/documents.py 的 imports |
| REST | API、source/page/config/kbs/graph/output 等路由 | `openkb/api.py` 和 api_* 模块 |
| Workbench | React/Vite frontend 构建到 openkb/web，由 API 挂载 | `frontend/`、`Dockerfile.icarus` |
| Agent 消费 | 通过独立 HTTP adapter，不 import OpenKB 包 | `apps/agent/src/agent_orchestration/plugins/knowledge/openkb_http_adapter.py` |

数据配置/知识库/备份在 `$ICARUS_DATA_DIR/services/openkb`，容器映射 `/data/config`、`/data/kbs`、`/data/backups`。第一批不改运行数据、鉴权、服务数量或端口 :7566。

CLI 保留的主要事实是服务内部直接引用 helper；不能据此推断内部 Agent 执行所有 CLI 命令。当前 `agent/query.py` 暴露函数工具 `list_skills/read_skill`，扫描逻辑在 `agent/skills.py`。

## 内容处置

| 处置 | 路径 | 功能与理由 |
| --- | --- | --- |
| 删除 | 根 `.claude-plugin/marketplace.json` | 上游插件分发清单，不是 KB runtime 的 marketplace 产物 |
| 删除 | 嵌套 `.github/workflows/` | 上游 CI/PyPI publish 在 monorepo 不发现；保留策略到根 CI，不迁发布凭据 |
| 删除 | examples/ | 样例 wiki、slides、REST/SSE 教程、演示 skill；README 本地链接同步修 |
| 删除 | scripts/prepare_local_env.py | 当前无生命周期调用的导入期辅助脚本，不作为运行链路 |
| 删除前迁规则 | app .gitignore | 缓存、原始数据、wiki、web bundle、maintainer-local docs 规则吸收根约定 |
| 移动 | skills/openkb/ | 开发导航说明与 references，迁到根 `.agents/skills/openkb` |
| 保留 | openkb/、tests/ | 整个核心与维护回归；不趁清理拆 cli.py 或弱化模块门禁 |
| 保留 | frontend/、Node build、openkb/web artifacts | 第一批 UI/build 输入；第二批另评估 |
| 保留 | 三个 deck skill | 源/包运行时读取，不能当重复开发文档删 |
| 保留 | pyproject.toml、uv.lock、Dockerfile/Compose、config.yaml.example | 精确依赖与部署契约，配置模板仍是非 env 配置参考 |
| 保留 | LICENSE、MODIFICATIONS、AGENTS/CLAUDE、assets/、docs/golden-principles.md、docs/spec/ | 来源/规则/架构图与 Icarus 具体设计计划 |

## 两类 skill 与运行时产物边界

开发 `openkb` skill 教 agent 通过 status/list/query 等命令导航 KB，单搬文件不会安装 CLI。开发文档必须说明：可以从仓库根通过 compose exec 调用容器 CLI；返回 `/data/kbs/...` 是容器路径，后续读取应在容器执行或显式映射到宿主数据目录。

三个 deck runtime skill：

- `openkb-deck-neon`：暗色霓虹/玻璃拟态 HTML 演示稿规范，默认生成主题。
- `openkb-deck-editorial`：暖米色/衬线/印刷风 HTML 演示稿规范，可选主题。
- `openkb-html-critic`：对生成 HTML 做视觉/结构检查与补丁，不改原演示文本事实。

三者仍留 `apps/openkb/skills/`。Hatch force-include 本来就只有三条，安装包资源在 `openkb/_skills/`；源码模式还扫描 app 顶层 skills。根 `.agents` 不加入 runtime bundled roots。

测试中 `.claude-plugin/marketplace.json` 多为临时 KB 的生成物：`test_marketplace.py`、`test_skill_cli.py`、`test_skill_chat_slash.py` 继续验证生成/分享能力，不删功能或测试来配合仓库分发元数据删除。

## 当前结构（第一批已实施，待提交）

```text
apps/openkb/
├── README.md
├── LICENSE
├── MODIFICATIONS.md
├── pyproject.toml
├── uv.lock
├── Dockerfile.icarus
├── docker-compose.yaml
├── config.yaml.example
├── AGENTS.md
├── CLAUDE.md
├── assets/openkb-architecture.webp
├── openkb/                    # CLI/API/core/agent/deck/skill/prompts/templates
├── frontend/                  # 第一批保留
├── skills/
│   ├── openkb-deck-neon/
│   ├── openkb-deck-editorial/
│   └── openkb-html-critic/
├── scripts/
│   ├── icarus-compose.sh
│   ├── icarus_compose.py
│   ├── icarus-container-start.sh
│   ├── prepare_managed_kb.py
│   ├── install.sh
│   └── test.sh
├── tests/                     # 原套件及新布局/脚本回归
└── docs/
    ├── golden-principles.md
    └── spec/2026-10-04-vendored-app-slimming/
        ├── arch.md
        └── plan.md
```

`docs/.gitignore` 必须允许 Icarus docs/spec，仍忽略 maintainer-local 内容。dev `.venv` 和 generated `openkb/web` 不入 Git。

## 开发、测试与构建契约

`install.sh --dev` 使用 `uv sync --locked --extra dev --extra api`，只创建 app .venv。`dev` 本身不含 FastAPI；api 当前是 web extra 的兼容别名，不把改 flag 描述为删 UI 依赖。

默认测试包括 API/remove/config、runtime marketplace、skill 与真实 deck prompt，加布局/脚本回归。全量包含全部保留 tests，既有 file-size/type/lint 债务以实际基线对比，不为绿门禁扩大 _GRANDFATHERED 或重写业务核心。

uv.lock 对 dev/CI 确实被 --locked 消费；当前 Docker 是 pip install .[web]，没有消费 uv.lock。两者事实分别声明，构建成功不等于 Docker 传递依赖锁已固定；本批不迁整个构建模型。

PR6 合入后保留 source ID/report/managed-KB template 代码、对应测试和 docs/spec；显式补入默认报告子集，根 CI 收敛重复 job。

## 声明、验收与第二批

原 app/LICENSE 和 MODIFICATIONS 保留；导航 skill 原无自己的 LICENSE，独立迁根时附原 OpenKB LICENSE、来源说明，根 notice 记录新路径，不覆盖上游作者归属。

验收包括源/wheel 真实 deck 资源、API helper import/create_app、runtime marketplace 原测试、default/full 基线、uv lock check、完整源码镜像 build 与 Workbench bundle。服务/模型 smoke 另获授权，不指向现有 KB。

第二批删 frontend 要先确认 PR6 WebUI 的真实服务路径及仍缺 Deck/Skill/watch/settings 等界面能力，随后另写应用 plan。API、CLI helpers、三 runtime skill 不因旧界面裁撤而自动删。
