# Icarus

Icarus 希望通过长期共处逐渐理解用户、用户正在经历的事情，
以及人与项目、应用、设备和环境之间的关系。

长期存在不意味着模型需要持续运行。Icarus 将身份、经历、状态、能力和任务保存在用户自己的设备
上，在需要时创建一次 Agent Run 完成思考与行动，结束后释放计算资源，同时保留可恢复的连续性。
远程模型可以接收完成当前任务所必需的上下文，但不会成为用户长期数据和 Agent 身份的事实源。

项目当前提供本地 TUI 与单用户 WebUI。TUI 支持文本和图片输入；WebUI 提供记忆管理、知识库和
Agent 文本对话，可创建和恢复 Session、查看工具执行，并在刷新后恢复历史。长期 Memory 的
Agent 侧能力已经接入，人物档案与关系由独立 rela-mem 服务提供；根控制面统一管理本地组件或连接已有
远程服务。持续环境感知、多端产品和更完整的自主成长仍属于后续方向。

## 产品方向

- **属于用户**：身份、会话、状态、能力配置和可恢复数据以本地为持久化事实源。
- **长期连续**：进程、界面和模型调用可以结束；再次启动后仍然是同一个 Icarus。
- **低打扰存在**：未来的观察、整理和认知不要求每次都转化为一条对话或通知。
- **理解而非画像**：长期认知应区分观察、事实、推断、时效和不确定性，并允许用户纠正。
- **渐进成长**：通过经历积累上下文和 Skill，通过 Plugin 获得新的感知与行动能力。

## 项目结构

```text
apps/
├── agent/       Agent 执行、模型接入、工具、Plugin 和本地持久化
│   ├── pyproject.toml / requirements.txt
│   ├── src/icarus/       可复用 core/runtime 框架
│   ├── src/agent_app/    Icarus 插件、模型适配与应用服务
│   └── scripts/
├── gateway/     本机 Agent 服务入口
│   ├── requirements.txt
│   └── scripts/
├── tui/         Textual 终端客户端
│   ├── requirements.txt
│   └── scripts/
├── mem0/        Apache-2.0 Mem0 源码与 Icarus 自建服务修改
├── openkb/      Apache-2.0 OpenKB 源码与 Icarus 自建服务修改
├── rela-mem/    独立人物身份、档案和关系服务（不运行 LLM）
└── webui/       记忆、知识库、对话工作台及 Node 同源代理入口
icarus.toml      公开组件拓扑、依赖、命令和环境变量绑定
icarus.local.toml.example  本机覆盖示例（复制后的 .local.toml 不提交）
infra/           独立 PostgreSQL / FalkorDB Compose 定义
packages/        应用间共享的数据模型和环境配置
docs/            项目定位、路线图、仓库基建 spec 与 SDD 模板
.agents/skills/  开发期技能参考（不自动运行）
skills/          生产基础技能源约定（当前没有 bundle）
scripts/         整个仓库的安装、启动和测试编排
Makefile         根目录统一命令入口
```

各应用的说明放在自己的 `README.md`；应用设计和实施计划按功能聚合在
`apps/<app>/docs/spec/YYYY-MM-DD-<feature>/` 中；仓库基建使用根
`docs/spec/YYYY-MM-DD-<feature>/`，分别使用 `arch.md`、`plan.md` 或带用途后缀的计划文件。
开发与验证入口见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
架构文档以当前源代码和测试为事实依据，用于描述架构与系统设计，不反向限制源代码演进。
不可拆分的跨应用需求使用根目录 `spec/YYYY-MM-DD-<feature>.md`。
完整产品定位见 [`docs/product-positioning.md`](docs/product-positioning.md)。
Python App 使用自己的 `.venv` 和 requirements；WebUI 使用应用目录内的 pnpm workspace。
根控制面仅使用 Python 3.11+ 标准库，不集中安装某一种语言的依赖；应用依赖和镜像构建仍归各 App，
独立中间件定义归 `infra/`。当前原生进程管理依赖 Linux `/proc` 身份与监听端口校验，不声明 macOS/Windows 原生管理兼容。

## 技术特色

### 本地状态与按需运行

长期状态保存在 `ICARUS_DATA_DIR`，模型推理按任务启动。SessionRuntime 可以卸载和重新恢复，
不需要永久保留 Agent 对象、协程或模型连接。这样既保留会话连续性，也避免“长期 Agent”等同于
持续占用计算资源。

### 多 Session 隔离

一个本机 Runtime 可以同时管理多个 Session。每个 Session 拥有独立的 Plugin 实例、Blackboard、
任务队列、Tool Registry 和持久状态；同一 Session 的多个客户端共享同一执行事实，不同 Session
可以并发运行。

### 可恢复的完整会话体验

模型上下文和界面历史分别使用适合各自职责的数据保存：Blackboard 保存下一轮 Agent 使用的上下文，
公共会话记录保存用户在界面中看到的消息、助手文本、完整 thinking、Tool 安全预览、错误和任务终态。使用相同 Session ID
重启后，TUI 会一次性恢复退出时的 Conversation，再继续接收新的实时输出。

Session 元数据和公共 Conversation 由 Agent Application 的 `SessionStore` 统一保存到
`ICARUS_DATA_DIR/icarus.db`。SQLAlchemy 负责表映射、查询、连接与事务；Plugin State、图片 Asset、
Trace 和日志继续保存在 Session 文件目录中。Gateway、TUI 和未来 Backend 都不直接访问数据库，
只通过 AgentRuntime 的应用接口使用 Session 数据。

### 可扩展的 Plugin 与 Skill

Plugin 通过 Manifest 声明 Capability、Tool、Event 和状态范围，并由运行时解析依赖关系和生命周期。
配置与 Plugin 拓扑在单个 SessionRuntime 生命周期内保持稳定，新建或重新加载 Session 时读取最新
配置。`apps/agent` 通过本地 editable distribution 提供 `icarus` 与 `agent_app`；Agent/Gateway 各自安装
同一个项目，不共用虚拟环境。模型角色使用带唯一 `name` 的 `model_settings` 数组，由 `model_roles` 选择。
旧固定角色配置仍可加载，新的代码统一按名称查找。

Plugin manifest v2 使用稳定事件 ID 和按契约命名的 Capability，`@handles` 声明消费入口，
`build_tools()` 是工具导出的唯一入口。Skill 支持发现、搜索、生产和演化，并保持明确的本地权限与持久化边界。MemoryPlugin
每轮先做有界自动召回，再启动主 Agent；主 Agent还可以显式读取、写入和维护记忆。KnowledgePlugin
提供按需查询、列举、读取、上传与重编译，不开放删除能力。

### 稳定的模型与客户端边界

模型厂商差异收敛在模型接入层，Agent 和 Plugin 不需要处理 OpenAI、Anthropic 等具体协议。TUI、
WebUI 和未来 Backend 通过同一 Gateway 使用 Agent，只消费稳定的公共 RuntimeUpdate，不依赖内部 Python
Event 或 Plugin 拓扑。

### 可控任务与安全资源传递

每个 Task 都有明确身份、队列位置、运行状态和唯一终态，并支持取消与提交去重。图片不会以 Base64
或任意绝对路径穿过 RPC，而是先进入受控暂存目录，再由 Runtime 校验并导入对应 Session Asset。

### 值改写与独立记录

Event 用于业务通信，Blackboard 持有模型工作历史和上下文状态。Hook 仅在登记的值传递点按优先级
改写参数或结果，不能改变路由身份、工具调用身份和控制流。Trace、日志与路由计数由独立运行时记录器
和插件后端承担，不依赖注册观测 Hook；记录仍按完整 Session 身份隔离。

## 快速开始

### 1. 配置拓扑与业务设置

在仓库根执行；需要 Python 3.11+、各应用自己的运行依赖，以及本地 Compose 组件所需的 Docker Compose。
WebUI 安装需要 Node.js 22.12+ 和应用锁定的 pnpm（可由 Corepack 提供）。

```bash
cp -n .example.env .env
./bin/icarus config validate
./bin/icarus config show
./bin/icarus config show rela-mem
```

`config validate/show` 是只读配置入口，不启动服务、不调用 Docker、不读取或解析 Secret 值；目前没有图形配置 UI。
`show` 输出有效注册元数据和来源文件。未创建本地覆盖时使用默认全本地部署。
如已有 `icarus.local.toml`，先检查它是否已把组件切成 remote。

| 配置 | 所有权 |
| --- | --- |
| `icarus.toml` | 公开拓扑、组件命令、依赖、端口与环境变量名 |
| `icarus.local.toml`（不提交） | 本机 local/remote、地址与部署方式覆盖 |
| `.env` 或进程环境（不提交） | Secret、绝对 `ICARUS_DATA_DIR` 及应用所需环境设置 |
| `apps/agent/settings.json`、rela-mem 身份 JSON | 模型、Plugin、用户/Agent 身份等业务配置 |

填写 `.example.env` 中当前协议所需的模型 Key、Mem0/OpenKB 凭据，以及本地关系服务使用的
`RELA_MEM_API_TOKEN`、`RELA_MEM_PG_PASSWORD`、`RELA_MEM_FALKORDB_PASSWORD`；不要将凭据写入 TOML。
rela-mem 部署前复制并调整其 `settings.example.json` 中的身份，使用 `RELA_MEM_SETTINGS_FILE` 指向独立文件，
不要把示例身份当作自己的部署身份。完整说明见 [rela-mem README](apps/rela-mem/README.md)。

已有远程数据库时，才复制覆盖示例并修改地址：

```bash
cp -n icarus.local.toml.example icarus.local.toml
```

`icarus.local.toml` 的示例片段如下（本机隧道示例，不代表自动建立隧道；Compose 消费方须改为容器可达地址）：

```toml
[components.postgres]
mode = "remote"
host = "localhost"
port = 25432

[components.falkordb]
mode = "remote"
host = "localhost"
port = 26379
```

若整个应用已经部署，改为覆盖其 `mode` 与 `endpoint`，例如：

```toml
[components.rela-mem]
mode = "remote"
endpoint = "https://relationship.example.invalid"
```

remote 组件只探测，不在远端安装、启动或停止，也不递归启动它内部的本地数据库。remote 数据库的角色、
数据库和权限须预先配置；TCP `reachable` 不等于认证成功。容器不能通过自己的 loopback 访问宿主机隧道，
Compose 消费方必须使用容器可达的地址；不要把无 TLS 数据库开放到公网。

拓扑优先级为本次 CLI 覆盖 > 旁侧 local TOML > base TOML > 默认值。`--config PATH` 可在动词前后使用一次，
读取其旁侧 `<stem>.local.toml`；运维相对路径仍以仓库根解析。环境先合并 `.env` 与进程（进程优先），
再由声明的拓扑导出覆盖连接变量，冲突告警仅列变量名。`ICARUS_ENV_FILE` 可为控制面选择替代 dotenv。
应用不读取根 TOML；Agent 的三种插件端点和 rela-mem 的数据库字段通过环境覆盖，业务配置不被改写。

`ICARUS_DATA_DIR` 保存 `icarus.db`、Plugin State、Trace、图片 Asset 和服务数据。
本版本不迁移旧 JSONL Session 数据；首次使用需配置不包含旧 Session 目录的新数据目录。

### 2. 安装不等于启动

```bash
./bin/icarus install              # 默认集合：原生依赖 + 应用镜像构建 + 中间件镜像拉取
./bin/icarus install gateway      # 包含 embedded Agent 及其 required 依赖
./bin/icarus install rela-mem     # 构建应用镜像，拉取 required 本地数据库镜像
./bin/icarus install --dev        # 应用自己的开发/测试环境，不构建服务镜像
```

安装不启动服务、不创建 `.env`，也不自动安装全局命令链接。Agent、Gateway、TUI 的 `.venv` 分别归应用；
Mem0/OpenKB/rela-mem 的运行依赖在各自镜像内。需要独立 rela-mem Python 环境时使用其
`scripts/install.sh`。可选执行 `bash scripts/install-commands.sh`，将 `icarus` / `icarus-gateway`
链接到 `${ICARUS_BIN_DIR:-~/.local/bin}` 后，才可在任意目录使用短命令。Makefile 保留为开发快捷入口。

### 3. 启停与诊断

**从旧 rela-mem 三合一 Compose 切换前，必须先手动停止旧项目。** 新旧项目不能同时挂载相同数据库目录；
控制面没有挂载碰撞自动保护，不迁移、不清空数据。原生进程的受管启停当前仅支持具有可读 `/proc` 的 Linux。

```bash
./bin/icarus start                # 默认后台集合就绪后，最后打开 TUI
./bin/icarus start gateway        # 自动满足 Agent 的 required Mem0/OpenKB 依赖
./bin/icarus start rela-mem       # 自动启动 required 本地 postgres/falkordb，或探测 remote
./bin/icarus start webui          # 原生生产服务；上游是 optional，不隐式启动上游
./bin/icarus status
./bin/icarus logs rela-mem --no-follow
./bin/icarus logs gateway         # 默认持续跟随
./bin/icarus stop rela-mem        # 只停止应用，保留共享数据库
./bin/icarus stop                 # 默认 required 闭包逆序停止、owner 去重，保留持久数据
```

默认集合包含 Gateway、进程内 Agent、Mem0、OpenKB、rela-mem、WebUI 和前台 TUI；独立 PostgreSQL/FalkorDB
由 rela-mem 的 required 边加入。Mem0 仍整体管理 API、私有 PostgreSQL 与 Dashboard。Argus 不登记。
`agent` 没有独立进程，显式启停请操作 `gateway`。重复 start 复用就绪实例；配置改变需明确 stop/start。
required 不可达阻止依赖分支，optional 只警告/降级；无关分支继续。未知端口服务不会被接管或停止。

TUI 退出不停止后台服务；只打开已有 Gateway 的 TUI 可使用：

```bash
cd /path/to/workspace
icarus tui --session-id my-session
# 显式地址只跳过默认 Gateway 的检查，不启动依赖
icarus tui --gateway-url ws://127.0.0.1:8765/rpc --session-id my-session
```

`icarus tui` 和旧形式 `icarus --session-id my-session` 不隐式启动 Gateway；未传 ID 时创建新 Session。
前台 TUI 保留调用者目录作为 Workspace；后台组件按注册的 `workdir` 运行。
更多状态、安全停止与中间件操作见 [infra 运维说明](infra/README.md) 和
[控制面架构](docs/spec/2026-10-19-component-lifecycle/arch.md)。

Mem0 要求 Docker Compose，读取仓库根 `.env`，并把 PostgreSQL、history、模型缓存和备份放在
`$ICARUS_DATA_DIR/services/mem0`。`ICARUS_MEM0_LLM_API_KEY` 为空时复用 `OPENAI_API_KEY`；默认 LLM
使用 OpenAI-compatible DeepSeek Endpoint 与 `deepseek-v4-flash`，Embedding 使用本地 FastEmbed。

OpenKB 读取同一个根 `.env`，把 config、知识库与备份放在
`$ICARUS_DATA_DIR/services/openkb`，并按 `settings.json` 的
`runtime.plugin_config.knowledge.knowledge_base` 创建默认知识库。专用 LLM Key 为空时复用
`OPENAI_API_KEY`；默认使用 `deepseek/deepseek-v4-flash`。

使用同一 Workspace 和 Session ID 再次启动时，TUI 会在进入 Ready 前从 SessionStore 一次性恢复
已持久化的 Conversation，包括用户消息、助手文本、完整 thinking、Tool 安全预览、错误和中断终态，
然后继续接收实时流。thinking 的流式 delta 不入库，恢复时使用每个模型 step 的完整记录。旧 JSONL
Session 不读取、不迁移，也不从内部 Trace 推断展示历史。

Runtime 完全空闲时可以输入 `/resume`，从当前 Workspace 的非空 Session 列表中选择并恢复；输入
`/clear` 会保留当前非空 Session 并开始新对话。`/exit` 通过同一命令注册表执行正常退出。三个命令
都是 TUI 本地命令，不发送给 Agent；`/clear` 和 `/resume` 在当前有任务、提交握手或待发送消息时会
直接拒绝，不会排队。裸 `exit` 和 `quit` 是普通用户消息。

`Enter` 把消息提交到 TUI 本地队列；Agent 运行期间输入框仍可编辑，待发送消息会显示在输入框上方。
队首在发送时根据实时状态动态路由：Agent 正在运行时追加到当前 Task，空闲时作为新 Task 提交；
所有消息继续按 FIFO 发送。受支持终端可用 `Shift+Enter` 换行，所有支持的终端都可用 `Ctrl+J` 换行。

在 macOS 上复制截图或浏览器图片后，可在 Composer 中按 `Ctrl+V` 插入 `[#imageN]` 并随消息提交。
图片先写入 `$ICARUS_DATA_DIR/incoming/`，RPC 只传 ResourceRef；Runtime 接受任务前将其导入 Session
Asset。Windows/Linux 的系统剪贴板图片读取暂未实现。

`Ctrl+C` 会依次处理当前草稿、撤回最新排队消息、取消正在运行的 Task，或在完全空闲时
退出。取消过程中会显示 `Cancelling`，并保留已经输出的内容；收到取消终态后才继续调度队列。
输入 `/exit`，或在空输入时按 `Ctrl+D` 也会退出。Textual 退出后恢复启动前的终端画面。

各 App 的底层脚本仍保留用于开发和调试，但不是推荐的用户入口：

```bash
./apps/gateway/scripts/start.sh
./apps/tui/scripts/start.sh --session-id my-session
```

Gateway 默认监听：

```text
HTTP health: http://127.0.0.1:8765/health
WebSocket RPC: ws://127.0.0.1:8765/rpc
```

### 启动 WebUI

统一入口管理原生生产 WebUI，默认打开 <http://127.0.0.1:8080>：

```bash
./bin/icarus install webui         # 安装 workspace 依赖并构建静态页面
./bin/icarus start gateway        # 对话所需；自动满足 required 服务
./bin/icarus start webui
./bin/icarus status webui
```

WebUI 的 Gateway/Mem0/OpenKB 是 optional 上游，壳与代理可在上游不可用时启动，但业务请求会失败，
状态显示降级。对话页填写服务端工作区绝对路径，选择或新建会话。

开发时也可从 `apps/webui` 执行 `corepack pnpm install --frozen-lockfile` 和
`corepack pnpm --filter @icarus/shell dev`，打开 <http://127.0.0.1:5173>；开发进程不由控制面管理。
生产入口支持单用户 Basic 认证、HTTP/WebSocket 代理，非本机绑定需要认证，远程访问还需 HTTPS。
绕过控制面部署与 `.env.local` 的步骤见 [WebUI README](apps/webui/README.md) 和
[部署说明](apps/webui/docs/deployment.md)。

## 当前能力

- 创建和恢复多个相互隔离的 Session；
- 使用 RunCard 流式显示 thinking、Assistant 中间进展、Tool 安全预览、追加内容和任务状态；
- Agent 工作期间继续编辑并按 FIFO 排队，发送时自动选择追加当前 Task 或创建新 Task；
- 取消当前任务，并保留已经产生的输出；
- 提交文本以及 macOS 剪贴板图片；
- 持久化会话内容和模型上下文；
- 使用相同 Session ID 恢复退出时的 Conversation，并继续之前的对话；
- 使用 `/resume` 列出并切换当前 Workspace 的非空 Session，使用 `/clear` 开始新对话，使用 `/exit` 退出；
- 恢复异常退出前已经产生的部分回复和 Tool 状态，并标记中断任务；
- Gateway 断线后重新连接并对账当前任务状态；
- 自动卸载长时间空闲的 Session，同时保留本地数据供下次恢复。
- 自动召回全局和当前 Workspace 记忆，并由主 Agent 显式维护 Mem0 记忆。
- 按需查询、读取、上传和重编译 OpenKB 知识，不向 Agent 开放删除接口。
- WebUI 支持记忆新增、归属与范围、详情修正、属性历史和暂停恢复；知识资料导入、阅读、编辑、
  文档关联与质量报告管理；Agent 文本流、工具状态、取消和断线后历史恢复。

当前已经形成可完整体验的本机闭环：

```text
启动 Gateway → 启动 TUI → 创建或恢复 Session → 提交任务
→ 查看模型与 Tool 执行 → 退出 → 使用同一 Session ID 恢复并继续对话
```

## 当前边界

- Gateway 作为独立后台进程运行；`icarus start` 启动完整能力，`icarus tui` 单独打开 TUI 且不会隐式
  创建本地 Runtime；
- Session 列表暂不支持搜索、筛选、重命名或删除非空 Session；
- Gateway 首次不可用时不会持续后台重连；
- TUI 未被 Runtime 接受的 Pending Queue 不跨 TUI 进程持久化；
- WebUI 对话按游标读取历史；TUI 当前仍一次性恢复 Conversation。
- WebUI 为单用户部署，支持入口认证；独立多用户 Backend、GUI 和 Gateway 远程认证尚未接入。
- Memory 依赖已配置的 Mem0（本地或 remote）；自动召回默认截止时间为 5 秒，可通过
  `runtime.plugin_config.memory.recall.deadline_ms` 配置为 1–30000 毫秒，超时后当前轮降级。
- Knowledge 依赖已配置的 OpenKB；关系工具依赖 rela-mem。服务不可用时相关 Tool 失败，但 Session 保持可用；
  这与控制面启动时的 required/optional 预检是两个不同边界。

## 第三方源码

`apps/mem0` 来自 [mem0ai/mem0](https://github.com/mem0ai/mem0)，使用 Apache License 2.0。
导入版本、Icarus 修改和分发说明见 `THIRD_PARTY_NOTICES.md` 与
`apps/mem0/MODIFICATIONS.md`。该目录是 Monorepo 普通源码，不是 Git submodule。

## 测试

```bash
make test

# 或分别执行
make test-agent
make test-gateway
make test-tui
```

WebUI 的回归、lint、格式、类型和构建命令见 [应用验证说明](apps/webui/README.md#验证)。
浏览器回归默认使用模拟后端；真实服务与模型验收需要显式开启
[集成测试开关](apps/webui/test/integration/README.md)。

## 分支与贡献

应用开发使用 `feat/<app-name>`，开始前同步目标远端的 `feature`，完成后向 `feature` 提 PR。
`feature` 汇总开发成果，`dev` 用于下一次发布前的验证，正式分支保存线上版本；当前远端正式分支
实际名为 `main`（也称 master 的角色）。推进顺序为 `feat/<app-name>` → `feature` → `dev` → `main`。
核对 PR 的目标仓库和分支，避免混用不同远端的 `feature`；详细约定见 [AGENTS.md](AGENTS.md#git)。
