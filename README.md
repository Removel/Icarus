# Icarus

Icarus 希望通过长期共处逐渐理解用户、用户正在经历的事情，
以及人与项目、应用、设备和环境之间的关系。

长期存在不意味着模型需要持续运行。Icarus 将身份、经历、状态、能力和任务保存在用户自己的设备
上，在需要时创建一次 Agent Run 完成思考与行动，结束后释放计算资源，同时保留可恢复的连续性。
远程模型可以接收完成当前任务所必需的上下文，但不会成为用户长期数据和 Agent 身份的事实源。

项目当前处于本地 TUI 技术预览阶段，已经可以创建和恢复多个 Session，发送文本或图片，让 Agent
调用本地工具完成任务，并在退出后恢复对话内容与上下文。持续环境感知、长期 Memory、多端产品和
更完整的自主成长仍属于后续方向。

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
└── tui/         Textual 终端客户端
    ├── requirements.txt
    └── scripts/
packages/        应用间共享的数据模型和环境配置
docs/            项目定位、路线图和待办
scripts/         整个仓库的安装、启动和测试编排
Makefile         根目录统一命令入口
```

各应用的说明、设计和实施计划分别放在自己的 `README.md`、`docs/arch/` 和 `docs/plan/` 中。
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
公共会话记录保存用户在界面中看到的消息、助手文本、Tool、错误和任务终态。使用相同 Session ID
重启后，TUI 会一次性恢复退出时的 Conversation，再继续接收新的实时输出。

### 可扩展的 Plugin 与 Skill

Plugin 通过 Manifest 声明 Capability、Tool、Event 和状态范围，并由运行时解析依赖关系和生命周期。
配置与 Plugin 拓扑在单个 SessionRuntime 生命周期内保持稳定，新建或重新加载 Session 时读取最新
配置。Skill 支持发现、搜索、生产和演化，并保持明确的本地权限与持久化边界。

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

安装全部 App 的运行依赖：

```bash
make install
```

安装完成后会在 `${ICARUS_BIN_DIR:-~/.local/bin}` 创建 `icarus` 和 `icarus-gateway` 软链接。需要使用
其他命令目录时可以执行：

```bash
ICARUS_BIN_DIR=/your/bin make install
```

需要运行测试时，安装各 App 的开发依赖：

```bash
make install-dev
```

也可以只安装一个 App：

```bash
make install-agent
make install-gateway
make install-tui
make install-commands
```

上述命令分别创建：

```text
apps/agent/.venv
apps/gateway/.venv
apps/tui/.venv
```

从示例创建 `apps/agent/.env`，配置模型 API Key 和绝对数据目录，并在
`apps/agent/settings.json` 中选择模型：

```dotenv
OPENAI_API_KEY=your-api-key
ANTHROPIC_API_KEY=your-api-key
ICARUS_DATA_DIR=/Users/you/.icarus
```

只需填写当前协议实际使用的 API Key。`ICARUS_DATA_DIR` 用于保存 Workspace、Session、会话记录、
Plugin State、Trace 和图片 Asset。Gateway 与 TUI 都会读取 `apps/agent/.env`。

同时启动本机 Gateway 和 TUI：

```bash
make start
```

也可以继续使用独立命令：

```bash
# 终端一
icarus-gateway

# 终端二
cd /path/to/workspace
icarus --session-id my-session
```

`make start` 会把命令执行时的当前目录作为 Agent Workspace。可以用 `ARGS` 传递 TUI 参数：

```bash
cd /path/to/workspace
make -f /absolute/path/to/Icarus/Makefile \
  start ARGS="--session-id my-session"
```

当前目录会作为 Agent Workspace；不传 `--session-id` 时生成一个新 Session ID。建议在需要后续恢复
时显式指定 ID。也可以从仓库根目录分别启动两个 App：

```bash
# 终端一
make gateway

# 终端二
make tui ARGS="--session-id my-session"
```

使用同一 Workspace 和 Session ID 再次启动时，TUI 会在进入 Ready 前一次性恢复已持久化的
Conversation，包括用户消息、助手文本、Tool、错误和中断终态，然后继续接收实时流。旧 Session
不会从内部 Trace 迁移展示历史；它们从升级后产生的新任务开始记录。

`Enter` 把消息提交到 TUI 本地队列；Agent 运行期间输入框仍可编辑，待发送消息会显示在输入框上方，
并在当前轮次结束后按 FIFO 自动发送。受支持终端可用 `Shift+Enter` 换行，所有支持的终端都可用
`Ctrl+J` 换行。

在 macOS 上复制截图或浏览器图片后，可在 Composer 中按 `Ctrl+V` 插入 `[#imageN]` 并随消息提交。
图片先写入 `$ICARUS_DATA_DIR/incoming/`，RPC 只传 ResourceRef；Runtime 接受任务前将其导入 Session
Asset。Windows/Linux 的系统剪贴板图片读取暂未实现。

`Ctrl+C` 会依次处理当前草稿、撤回最新排队消息、取消正在运行的 Task，或在完全空闲时
退出。取消过程中会显示 `Cancelling`，并保留已经输出的内容；收到取消终态后才继续调度队列。
输入 `exit`、`quit`，或在空输入时按 `Ctrl+D` 也会退出。Textual 退出后恢复启动前的终端画面。

各 App 的直接启动脚本：

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
- 流式显示 Agent 回复、Tool 调用、错误和任务状态；
- Agent 工作期间继续编辑并按 FIFO 排队后续消息；
- 取消当前任务，并保留已经产生的输出；
- 提交文本以及 macOS 剪贴板图片；
- 持久化会话内容和模型上下文；
- 使用相同 Session ID 恢复退出时的 Conversation，并继续之前的对话；
- 恢复异常退出前已经产生的部分回复和 Tool 状态，并标记中断任务；
- Gateway 断线后重新连接并对账当前任务状态；
- 自动卸载长时间空闲的 Session，同时保留本地数据供下次恢复。

当前已经形成可完整体验的本机闭环：

```text
启动 Gateway → 启动 TUI → 创建或恢复 Session → 提交任务
→ 查看模型与 Tool 执行 → 退出 → 使用同一 Session ID 恢复并继续对话
```

## 当前边界

- Gateway 作为独立进程运行；`make start` 可以统一启动 Gateway 与 TUI，单独运行 TUI 时不会隐式
  创建本地 Runtime；
- TUI 尚未提供 Session 列表和切换界面，需要通过 `--session-id` 记住并指定会话；
- Gateway 首次不可用时不会持续后台重连；
- TUI 未被 Runtime 接受的 Pending Queue 不跨 TUI 进程持久化；
- 长会话历史暂未分页；
- Backend、WebUI、GUI 和远程认证尚未接入。

## 测试

```bash
make test

# 或分别执行
make test-agent
make test-gateway
make test-tui
```
