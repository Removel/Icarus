# Icarus

Icarus 希望通过长期共处逐渐理解用户、用户正在经历的事情，
以及人与项目、应用、设备和环境之间的关系。

长期存在不意味着模型需要持续运行。Icarus 将身份、经历、状态、能力和任务保存在用户自己的设备
上，在需要时创建一次 Agent Run 完成思考与行动，结束后释放计算资源，同时保留可恢复的连续性。
远程模型可以接收完成当前任务所必需的上下文，但不会成为用户长期数据和 Agent 身份的事实源。

项目当前处于本地 TUI 技术预览阶段，已经可以创建和恢复多个 Session，发送文本或图片，让 Agent
调用本地工具完成任务，并在退出后恢复对话内容与上下文。长期 Memory 的 Agent 侧能力已经接入，
使用时需要另行启动仓库内的自建 Mem0 与 OpenKB；持续环境感知、多端产品和更完整的自主成长仍属于后续方向。

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
│   ├── requirements.txt
│   └── scripts/
├── gateway/     本机 Agent 服务入口
│   ├── requirements.txt
│   └── scripts/
├── tui/         Textual 终端客户端
    ├── requirements.txt
    └── scripts/
├── mem0/        Apache-2.0 Mem0 源码与 Icarus 自建服务修改
└── openkb/      Apache-2.0 OpenKB 源码与 Icarus 自建服务修改
packages/        应用间共享的数据模型和环境配置
docs/            项目定位、路线图和待办
scripts/         整个仓库的安装、启动和测试编排
Makefile         根目录统一命令入口
```

各应用的说明放在自己的 `README.md`；架构设计和实施计划按功能聚合在
`docs/spec/YYYY-MM-DD-<feature>/` 中，分别使用 `arch.md`、`plan.md` 或带用途后缀的计划文件。
架构文档以当前源代码和测试为事实依据，用于描述架构与系统设计，不反向限制源代码演进。
不可拆分的跨应用需求使用根目录 `spec/YYYY-MM-DD-<feature>.md`。
完整产品定位见 [`docs/product-positioning.md`](docs/product-positioning.md)。
每个 App 使用自己的 `.venv` 和 requirements；根目录不集中安装某一种语言的依赖，只调用各 App
提供的脚本。

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
配置。Skill 支持发现、搜索、生产和演化，并保持明确的本地权限与持久化边界。MemoryPlugin
每轮先做有界自动召回，再启动主 Agent；主 Agent还可以显式读取、写入和维护记忆。KnowledgePlugin
提供按需查询、列举、读取、上传与重编译，不开放删除能力。

### 稳定的模型与客户端边界

模型厂商差异收敛在模型接入层，Agent 和 Plugin 不需要处理 OpenAI、Anthropic 等具体协议。TUI 和
未来 Backend 通过同一 Gateway 使用 Agent，只消费稳定的公共 RuntimeUpdate，不依赖内部 Python
Event 或 Plugin 拓扑。

### 可控任务与安全资源传递

每个 Task 都有明确身份、队列位置、运行状态和唯一终态，并支持取消与提交去重。图片不会以 Base64
或任意绝对路径穿过 RPC，而是先进入受控暂存目录，再由 Runtime 校验并导入对应 Session Asset。

### 非侵入式可观测性

Event 用于业务通信，Blackboard 表达当前上下文状态，Hook 只负责 Trace、日志和监督。观测包装器不
改变 Agent、模型或 Tool 的主流程行为，Session 的日志和 Trace 也按完整 Session 身份隔离。

## 快速开始

首次从源码安装全部 App 的运行依赖：

```bash
./bin/icarus install
```

如果根 `.env` 不存在，安装命令会从 `.example.env` 创建一个权限受限的空模板；安装完成后需要填写
运行所需配置。安装不会覆盖已有 `.env`，也不会启动任何服务。

安装完成后会在 `${ICARUS_BIN_DIR:-~/.local/bin}` 创建 `icarus` 和 `icarus-gateway` 软链接。需要使用
其他命令目录时可以执行：

```bash
ICARUS_BIN_DIR=/your/bin ./bin/icarus install
```

需要运行测试时，安装各 App 的开发依赖：

```bash
icarus install --dev
```

也可以只安装一个 App：

```bash
icarus install agent
icarus install gateway
icarus install tui
```

上述命令分别创建：

```text
apps/agent/.venv
apps/gateway/.venv
apps/tui/.venv
```

依赖始终属于各自 App；仓库根目录不会创建共享 `.venv`。Mem0 和 OpenKB 的服务端依赖通过
`icarus install mem0` 与 `icarus install openkb` 构建到各自 Docker 镜像中。`make install`、
`make install-dev` 和 `make install APP=<name>` 保留为开发兼容入口。

从根目录示例创建 `.env`，配置模型 API Key、外部服务 Secret 和绝对数据目录，并在
`apps/agent/settings.json` 中选择模型：

```dotenv
OPENAI_API_KEY=your-api-key
ANTHROPIC_API_KEY=your-api-key
ICARUS_DATA_DIR=/Users/you/.icarus
ICARUS_MEM0_API_KEY=your-local-service-key
ICARUS_MEM0_POSTGRES_PASSWORD=your-database-password
ICARUS_MEM0_JWT_SECRET=your-jwt-secret
ICARUS_MEM0_LLM_API_KEY=your-memory-model-key
ICARUS_MEM0_AUTH_DISABLED=false
ICARUS_OPENKB_API_TOKEN=your-local-service-token
ICARUS_OPENKB_LLM_API_KEY=your-knowledge-model-key
```

只需填写当前协议实际使用的 API Key。`ICARUS_DATA_DIR` 用于保存 `icarus.db`、Plugin State、Trace
和图片 Asset。Agent、Gateway、TUI、Mem0 和 OpenKB 都读取仓库根 `.env`。本版本不迁移旧 JSONL Session 数据；首次
使用需要配置不包含旧 Session 目录的新数据目录。

启动全部能力：

```bash
icarus start
```

该命令按 Mem0、OpenKB、Gateway 的顺序启动后台项目并等待健康检查，随后在当前终端打开 TUI。
TUI 退出后后台项目继续运行。使用统一状态和停止命令管理它们：

```bash
icarus status
icarus stop
```

也可以单独管理一个项目：

```bash
icarus start mem0
icarus start openkb
icarus start gateway

icarus stop mem0
icarus stop openkb
icarus stop gateway
icarus stop tui

icarus status mem0
```

`agent` 是 Gateway 进程内加载的能力，不是独立进程，因此随 Gateway 启停。Mem0 作为一个项目管理
其 API、PostgreSQL 和 Dashboard 服务组。停止命令不会删除 `$ICARUS_DATA_DIR` 下的 Memory、
Knowledge、Session 或日志数据。

服务已经启动时，可以只打开 TUI：

```bash
cd /path/to/workspace
icarus tui --session-id my-session
```

`icarus tui` 不会隐式启动 Gateway。Gateway 不可用时会提示使用 `icarus start gateway` 或
`icarus start`。不传 `--session-id` 时生成一个新 Session ID；建议在需要后续恢复时显式指定 ID。
无子命令的旧形式暂时兼容：

```bash
icarus --session-id my-session
```

统一命令不会改变调用者当前目录；执行 `icarus start` 或 `icarus tui` 时的目录就是 Agent
Workspace。Makefile 继续作为开发快捷入口：

```bash
cd /path/to/workspace
make -f /absolute/path/to/Icarus/Makefile \
  start ARGS="--session-id my-session"
```

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
- 长会话历史暂未分页；
- Backend、WebUI、GUI 和远程认证尚未接入。
- Memory 依赖本机 Mem0；服务不可用时当前轮在 1 秒内降级为无记忆运行。
- Knowledge 依赖本机 OpenKB；服务不可用时当前 Tool 失败，但 Session 保持可用。

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
