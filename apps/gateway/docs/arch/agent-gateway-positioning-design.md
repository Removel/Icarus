# Agent Gateway Architecture Positioning｜Agent Gateway 架构定位

## 1. 文档定位

本文只确定 Agent Gateway 在 Icarus 总体架构中的位置、职责和依赖方向，回答：

- Icarus 是否需要 Gateway；
- Gateway 是什么，不是什么；
- Gateway、设备级 AgentRuntime、SessionRuntime 和 PluginRuntimeHost 的关系；
- Backend、TUI、WebUI 和 GUI 如何访问 Agent。

本文同时记录已经确认的第一阶段协议和迁移边界；具体文件、测试与交付顺序见对应实施计划。

## 2. 核心结论

Icarus 需要一个独立 Agent Gateway 应用，作为设备级 AgentRuntime 唯一的轻量网络入口。

```text
WebUI / GUI
      │
      ▼
   Backend ─────────────┐
                        │
TUI ────────────────────┤
                        ▼
                 Agent Gateway
                        │
                        ▼
                  AgentRuntime
                  ├── SessionRuntime A
                  │   └── PluginRuntimeHost A
                  ├── SessionRuntime B
                  │   └── PluginRuntimeHost B
                  └── SessionRuntime C
                      └── PluginRuntimeHost C
```

对应关系为：

```text
一台设备 / 一个 Icarus 实例
├── 一个 Agent Gateway
└── 一个 AgentRuntime
    └── 多个 SessionRuntime
        └── 每个 Session 一套独立 Plugin 实例与 EventBus
```

Gateway 使用 FastAPI 暴露网络连接，以 WebSocket 承载 JSON-RPC 2.0，并使用 Pydantic 校验协议
数据。第一阶段不引入额外 JSON-RPC 框架。

## 3. 运行层级

### 3.1 AgentRuntime：设备级唯一运行管理器

AgentRuntime 是一台设备上唯一的 Icarus Agent 运行管理器。它负责：

- 启动和停止整个 Agent 系统；
- 创建新 Session；
- 打开、定位、恢复和卸载已有 Session；
- 管理多个已加载的 SessionRuntime；
- 将提交、取消和状态查询路由到对应 Session；
- 聚合多个 Session 的状态与输出；
- 停止时收束全部 Session。

AgentRuntime 不处理 HTTP、WebSocket 和 JSON-RPC，也不依赖 Gateway、Backend 或 UI。

“设备级唯一”是部署和所有权约束，不要求实现为 Python 全局单例。

### 3.2 SessionRuntime：单 Session 执行单元

每个已加载 Session 对应一个 SessionRuntime。它拥有一套相互隔离的：

- PluginRuntimeHost；
- Plugin 实例和 Capability；
- EventBus；
- Blackboard 和 Session History；
- UserInput Runtime Task Queue；
- AgentPlugin、TaskChannel 和活动 Run；
- ToolRegistry、RuntimeUpdatePlugin 和 Session 级持久状态。

SessionRuntime 负责当前 Session 的启动、状态恢复、任务提交、任务取消、输出订阅、快照和关闭。

每个 Task 在 SessionRuntime 中创建一次无状态 Agent Run。Session 连续性来自 Blackboard、Plugin
State 和 Persistence，不依赖恢复旧 ReActAgent 对象、Python 协程或模型连接。

### 3.3 PluginRuntimeHost：Session 级 Plugin 生命周期 Host

当前 PluginRuntimeHost 继续作为 SessionRuntime 内部组件，负责：

- 发现 Manifest 并解析 Plugin 依赖图；
- 为当前 Session 创建 Plugin 实例；
- 注册 Capability、Tool 和 Event 订阅；
- 启动 PluginManager 与 EventBus；
- 恢复当前 Session 对应的 Plugin State；
- 停止时 quiesce、drain、snapshot 和清理。

当前 Plugin 通过实例隔离区分 Session，而不是由一套共享 Plugin 根据 `session_id` 分流：

```text
Session A Event → EventBus A → Plugin A
Session B Event → EventBus B → Plugin B
```

内部 Event 通过 `task_id` 区分同一 Session 内的 Task；Session 身份由 SessionRuntime、
PluginRuntimeHost、固定的 SessionIdentity 和 Hook/Persistence Context 确定。

Python 已导入模块可以由进程自然复用；Manifest、配置、Capability、Tool 和 Event 拓扑在每个
SessionRuntime 启动时解析，并在该 SessionRuntime 生命周期内冻结。新建或重新恢复的 Session
可以使用当时最新的 Plugin 与配置；已加载 Session 不受中途变化影响。第一阶段不把有状态 Plugin
改造成共享的多 Session 单例。

Plugin 快照以 `state_version` 作为持久状态格式的兼容契约；`plugin_version` 和 `manifest_hash` 只记录
来源并用于诊断。核心 Plugin 恢复失败会使 SessionRuntime 恢复失败；非核心 Plugin 恢复失败时由
Host 警告并禁用该 Plugin，再按现有 Capability 和 Event 依赖级联处理。第一阶段不做状态迁移。

每个 SessionRuntime 继续独立持有当前 PersistenceRuntime、HookRegistry、Trace Writer 和 Logger
Handler，不新增共享 Persistence 层。Logger Handler 只处理与自身完整 SessionIdentity 匹配的日志，
SessionRuntime 卸载时只关闭自己的 Persistence 资源。

### 3.4 PluginRuntime：单 Plugin Worker

PluginRuntime 仍表示单个 Plugin 的 inbox、顺序消费 Worker、运行状态和统计。它位于
PluginRuntimeHost 内部，不改变现有定位。

完整层级是：

```text
AgentRuntime             设备级，多 Session
└── SessionRuntime       Session 级，独立执行环境
    └── PluginRuntimeHost
        └── PluginRuntime
            └── Plugin
                └── Task / Agent Run
```

## 4. AgentRuntimeService 的迁移结果

迁移前 `AgentRuntimeService` 固定绑定一个 Workspace 和 Session，并创建一套 Hook、Persistence、Tool、
PluginManager、OutputBridge 与 PluginRuntimeHost。它实际是单 Session 的组装器、生命周期所有者和
应用入口。

目标架构不长期保留：

```text
Gateway → AgentRuntimeService → AgentRuntime → SessionRuntime
```

因为 AgentRuntimeService 与设备级 AgentRuntime 会形成重复门面，其实现已经迁移为 SessionRuntime；
Gateway 和 TUI 也已完成迁移，旧 Service、公开导出和旧测试已经删除，没有保留兼容入口。

因此最终关系是：

```text
Gateway → AgentRuntime → SessionRuntime → PluginRuntimeHost
```

## 5. Gateway 定位

Agent Gateway 是独立应用：

```text
apps/gateway/
```

它负责：

- 运行 FastAPI 网络服务；
- 建立和管理 WebSocket 连接；
- 承载并校验 JSON-RPC 2.0；
- 将外部调用映射到 AgentRuntime；
- 序列化和推送 AgentRuntime 已提供的公共消息；
- 对外暴露一个 Runtime 和多个 Session。

Gateway 不拥有 Session、Task、队列、对话历史或 Plugin 状态，也不解释具体 Plugin Event。内部
Event 到公共 RuntimeUpdate 的投影属于 Agent 应用层，并纳入本轮 Runtime 改造；Gateway 只负责
JSON-RPC 序列化、连接和路由。

## 6. Gateway 不是 Plugin

Plugin 位于 SessionRuntime 内部，用于扩展 Agent 的感知、能力或业务处理。Gateway 位于
AgentRuntime 外部，需要在 Runtime 尚未启动、启动失败或已经停止时仍能表达网络状态。

错误关系：

```text
Session Runtime
└── Gateway Plugin
    └── 对外暴露并控制 Runtime
```

正确关系：

```text
Gateway App
└── AgentRuntime
    └── SessionRuntime
        └── Plugins
```

Gateway 不直接访问 PluginManager、EventBus、Blackboard、AgentPlugin 或 ReActAgent。

## 7. Gateway 与 Backend

| 层 | 定位 | 主要职责 | 不负责 |
|---|---|---|---|
| Agent Gateway | Agent 执行面的轻量网络入口 | Runtime 控制、Session 路由、RPC 和公共输出 | 用户产品、页面 API、文件处理和跨设备产品逻辑 |
| Backend | 产品控制面与业务后端 | 用户、权限、对话索引、产品 API、资源准备和多个 Runtime 协调 | ReAct、Tool、Blackboard 和 Plugin 调度 |
| AgentRuntime | 设备级 Agent 运行管理器 | 多 Session 生命周期、路由、状态与输出聚合 | 网络协议和产品界面 |
| SessionRuntime | 单 Session 执行环境 | Queue、Task、Agent Run、Plugin、Context 和 Session State | 网络与跨 Session 产品组织 |

Gateway 不随 WebUI 功能增加而增长大量产品接口。Backend 通过 Gateway 的稳定 RPC 使用 Agent，
不导入 Agent 内部模块，也不复制 Agent 执行状态机。

## 8. 各交互面的访问路径

WebUI 和 GUI 固定通过 Backend：

```text
WebUI / GUI → Backend → Agent Gateway → AgentRuntime
```

本地 TUI 默认连接已经运行的 Gateway：

```text
TUI → Agent Gateway → AgentRuntime
```

TUI 不再直接创建单 Session AgentRuntimeService，也不因自身退出而默认结束长期运行的 Runtime。
未来远程 TUI 可以增加通过 Backend 连接的模式。

## 9. 通信定位

```text
FastAPI
└── WebSocket
    └── JSON-RPC 2.0
        └── Pydantic 协议模型
```

- FastAPI 负责网络应用和连接生命周期；
- WebSocket 负责双向长连接传输；
- JSON-RPC 2.0 规定请求、响应、通知和错误的标准外壳；
- Pydantic 校验 Icarus 的业务参数、返回值和公共输出。

第一阶段不引入额外 JSON-RPC 框架，只实现实际使用的标准必要子集。具体方法、参数和 Update
由第一阶段实施计划确定。

客户端不得依赖 `source_plugin_id`、Python Event 类名、EventBus 拓扑或具体 Plugin。

## 10. 新旧 Session 的运行逻辑

### 10.1 新 Session

```text
Backend / TUI 发起新对话
→ Gateway 转换为统一 Runtime 调用
→ AgentRuntime 创建 SessionIdentity 与 SessionRuntime
→ SessionRuntime 创建并启动 PluginRuntimeHost
→ Host 按当前配置初始化该 Session 的 Plugin、Capability、Tool 与空状态
→ Session Ready
→ 首条输入进入 Runtime Task Queue
→ Blackboard 准备上下文
→ AgentPlugin 创建本次无状态 Agent Run
→ 进入原有 Agent 流程
```

### 10.2 已加载的旧 Session

```text
Backend / TUI 选择旧对话
→ Gateway 转换请求
→ AgentRuntime 找到已加载 SessionRuntime
→ 直接提交新 Task
→ 进入原有 Agent 流程
```

### 10.3 尚未加载的旧 Session

```text
Backend / TUI 选择旧对话
→ Gateway 转换请求
→ AgentRuntime 从 Persistence 确认 Session 存在
→ 创建新的 SessionRuntime 和 PluginRuntimeHost
→ Host 按恢复时的当前配置创建 Plugin 对象并冻结该 Session 的运行图
→ 按 SessionIdentity 恢复 Blackboard、Plugin State、Session Asset 和元数据
→ Session Ready
→ 提交新 Task
→ 进入原有 Agent 流程
```

恢复的是持久状态，不是旧 Agent 对象、旧 Plugin 对象、协程、模型连接或执行到一半的调用栈。

## 11. 双队列定位

客户端与 Runtime 都可以维护队列，但职责不同。

客户端队列负责：

- 草稿和等待发送；
- Gateway 不可用时保留内容；
- 等待 Runtime 接受确认；
- 连接失败、重试和界面恢复；
- 展示客户端视角的发送状态。

Runtime 队列负责：

- 请求被接受后的 Task 排队；
- Session 内执行顺序；
- 任务取消、运行状态和唯一终态；
- 为多个客户端提供共同的执行事实。

责任边界是：

```text
Runtime 尚未确认接受 → 客户端负责保留与恢复
Runtime 已返回 task_id → Runtime 对执行事实负责，客户端保留状态投影
```

两边不是互相替代，也不能各自声称同一任务的执行状态为最终事实。提交由客户端生成
`submission_id`，AgentRuntime 在同一 Session 内做有界内存去重；进程重启后不保证去重。

## 12. 非字符串资源边界

图片、文档和其他非字符串内容不通过 Gateway JSON-RPC 透传二进制，也不由 Gateway 上传、解析或
转换。

```text
WebUI / GUI
→ Backend 接收、处理并保存到 Runtime 可访问的本地存储
→ 生成稳定资源标识
→ RPC 只传资源标识
→ Runtime 根据标识读取资源
```

本地 TUI 直连 Gateway 时，也由本地调用侧先准备资源与标识。第一阶段使用受控本地暂存目录中的
ResourceRef：Gateway 只校验并转发标识，AgentRuntime 在返回
`task_id` 前解析标识、检查路径边界和文件签名，并复制到目标 Session `assets/`。之后继续复用现有
ImagePart、Blackboard、`resolve_image()` 和 Provider 转换链。

## 13. 公共更新与订阅

现有 OutputBridgePlugin 按职责重构为 Session 级 RuntimeUpdatePlugin，只负责把声明过的内部 Event
投影成公共 RuntimeUpdate。AgentRuntime 负责聚合多个 Session 的 Update、发布自身拥有的 Session
生命周期 Update，并向 Gateway 提供一个设备级订阅流。Gateway 维护连接关注的 Session 集合并过滤，
AgentRuntime 不感知 WebSocket 订阅关系。

公共 RuntimeUpdate 包含 `workspace_key`、`session_id`、可选 `task_id`、稳定 `type`、JSON 兼容
`payload` 和 `occurred_at`。第一阶段覆盖 Session 生命周期、Task 接受/开始/结束/错误/累计 Usage、
助手文本增量、Tool 开始/完成和 Context Compact，不暴露 `source_plugin_id`、Python Event 类名、
System Prompt、完整 History 或完整 AgentResponse。

AgentRuntime 和 Gateway 的每个订阅/连接使用独立有界队列。慢消费者溢出时关闭对应订阅或连接并
返回明确错误，不阻塞 Runtime，也不静默丢弃单条 Update。第一阶段不提供全局 sequence、Update
持久化、游标或断线补发；重连后通过 Session 和 Task 状态查询重建。

## 14. 生命周期

```text
Gateway 启动
→ AgentRuntime 启动
→ Gateway 对外接受调用
→ 按需创建或恢复多个 SessionRuntime
```

停止时：

```text
Gateway 停止接受新调用
→ 收束外部连接
→ AgentRuntime 收束全部 SessionRuntime
→ 各 PluginRuntimeHost 保存状态并停止 Plugin 与后台任务
→ Gateway 退出
```

普通 TUI 或 Backend 连接断开不隐式停止 Runtime、Session 或当前 Task。

释放已加载 SessionRuntime 的操作统一称为 `unload_session(SessionIdentity)`。它保留 Session 快照、
Asset 和其他持久化数据，以后仍可 resume；删除 Session 数据不属于第一阶段。显式 unload、
AgentRuntime 整体停止或启动/恢复失败清理会关闭 SessionRuntime，客户端切换 Session 或 Session
短时空闲不会立即关闭。普通 unload 遇到运行中或排队中的 Task 时返回忙，不隐式取消。

AgentRuntime 会自动卸载连续 6 小时没有状态变化，并且没有运行中或排队 Task、没有未结束 Plugin
后台工作的 SessionRuntime。连接和 RuntimeUpdate 订阅不阻止自动卸载，只读查询、订阅、心跳和
健康检查也不刷新空闲时间。到期后必须在与 resume/unload 共用的 Session 串行区内重新检查全部
条件，仍然空闲时才执行同一 unload 流程；下一次提交会自动 single-flight resume。

## 15. 当前实现与端到端链路

当前实现：

```text
TUI / Backend
└── Gateway WebSocket / JSON-RPC 2.0
    └── AgentRuntime
        ├── SessionRuntime A → PluginRuntimeHost A
        └── SessionRuntime B → PluginRuntimeHost B
```

启动链路：

```text
icarus-gateway
→ FastAPI lifespan 启动设备级 AgentRuntime
→ AgentRuntime 启动 RuntimeUpdate 聚合循环与空闲 Session 清理循环
→ Gateway 建立一个设备级 RuntimeUpdate 订阅并开放 /health 与 /rpc

icarus
→ TUI 从 apps/agent/.env 取得 ICARUS_DATA_DIR
→ GatewayClient 连接 ws://127.0.0.1:8765/rpc
→ 查询指定 Session；不存在时创建 SessionRuntime
→ 订阅该 Session 的 RuntimeUpdate
→ TUI Ready
```

用户提交一条文本消息后的执行链路：

```text
TUI 为消息保留 submission_id
→ session.submit(workspace_path, session_id, prompt, resources)
→ Gateway 校验 JSON-RPC/Pydantic 参数并调用 AgentRuntime.submit
→ AgentRuntime 按 SessionIdentity 串行修改；未加载时 single-flight resume
→ ResourceRef 在返回 task_id 前导入 Session assets
→ SessionRuntime 将任务加入 UserInput Runtime Queue
→ Runtime 返回 task_id，提交责任从 TUI 队列切换到 Runtime
→ Blackboard 准备当前 Session 上下文
→ AgentPlugin 创建本 Task 的无状态 Agent Run
→ 模型 Step、Tool Call 与 Plugin Event 按原有执行链运行
→ RuntimeUpdatePlugin 投影内部 Event
→ AgentRuntime 聚合 RuntimeUpdate 并更新 Session/Task 状态
→ Gateway 按连接订阅过滤并发送 runtime.update Notification
→ TUI 按 RuntimeUpdate.type 投影文本、Tool、Usage、错误和终态
```

正常成功任务的主要公共 Update 顺序是：

```text
task.accepted
→ task.started
→ assistant.text_delta / tool.started / tool.completed ...
→ task.usage（存在累计 Usage 时）
→ task.finished
```

TUI 断开只关闭客户端连接，不取消 Task 或卸载 SessionRuntime。重连后重新订阅，并使用 Session 与
Task 状态查询恢复当前投影。

## 16. 已落地与后续工作

### 16.1 已落地

- Gateway 是独立 App，不是 Plugin；
- 一台设备运行一个 Gateway 和一个逻辑 AgentRuntime；
- AgentRuntime 管理多个 SessionRuntime；
- 一个 SessionRuntime 对应一套独立 PluginRuntimeHost、Plugin 实例和 EventBus；
- Plugin 通过实例隔离区分 Session，通过 `task_id` 区分 Session 内 Task；
- Plugin 与配置在单个 SessionRuntime 生命周期内冻结，下一次 SessionRuntime 启动时读取变化；
- Plugin 状态以 `state_version` 判断格式兼容，核心恢复失败阻止 Session Ready，非核心恢复失败由
  Host 禁用并级联依赖；
- 每个 SessionRuntime 独立持有 Persistence 资源，Logger Handler 只写入与自身 SessionIdentity 匹配
  的日志；
- 连续 6 小时没有状态变化且没有 Task、排队工作或 Plugin 后台工作时自动 unload；连接和订阅不
  阻止卸载，下一次提交自动 resume；
- 每个 Task 创建一次无状态 Agent Run；
- 原 AgentRuntimeService 的实现已迁移为 SessionRuntime，旧 Service 已删除且不保留兼容入口；
- Gateway 直接调用 AgentRuntime，不长期保留重复的 RuntimeService 层；
- WebUI/GUI 通过 Backend 访问 Gateway，TUI 默认连接已有 Gateway；
- Gateway 使用 FastAPI、WebSocket、JSON-RPC 2.0 和 Pydantic；
- 不引入第三方 JSON-RPC 框架；
- Gateway 不负责 Backend 产品能力或文件数据面；
- 客户端发送队列与 Runtime 执行队列分层协作。
- 内部 Plugin Event 到公共 RuntimeUpdate 的投影由 Agent 应用层负责。
- AgentRuntime 提供设备级 RuntimeUpdate 流，Gateway 按连接关注的 Session 过滤；
- 提交使用 Session 内有界内存 `submission_id` 去重，不保证进程重启后的幂等；
- TUI 图片先写入受控暂存目录，RPC 只传 ResourceRef，Runtime 接受 Task 前导入 Session Asset；
- Gateway 与 AgentRuntime 第一阶段同进程运行，默认只监听本机地址。

### 16.2 后续工作

- Backend、WebUI 和 GUI 对 Gateway 公共协议的产品接入；
- 非本机部署前的认证、权限、进程发现与管理；
- 按真实调用需求继续设计非图片资源标识和访问校验。

## 17. 架构约束

- Gateway 不解释 ReAct、Tool、Blackboard 或具体 Plugin 业务；
- Gateway 不直接访问 SessionRuntime 内部组件；
- Gateway 不成为 Session、Task、队列或历史的事实源；
- AgentRuntime 不依赖 Gateway、Backend 或 UI；
- SessionRuntime 之间的 Plugin 实例和状态相互隔离；
- Backend 不复制 Agent 执行状态机；
- UI 不直接消费内部 Plugin Event；
- 网络连接断开不能隐式终止 Runtime 或当前 Task；
- unload 只释放 SessionRuntime，不删除 Session 持久化数据；
- 第一阶段不设置活动 SessionRuntime 数量上限，也不按数量或内存压力淘汰；仅执行已定义的 6 小时
  空闲卸载；
- Gateway 不接受 Base64、任意绝对路径或未受控文件 URI；
- 第一阶段不依赖 RuntimeUpdate 历史补发，重连通过只读状态查询恢复；
- 后续拆分进程时，Gateway 外部协议保持稳定。

## 18. 一句话定义

> Agent Gateway 是 Icarus 独立、轻量的网络通信应用：它以 FastAPI WebSocket 上的 JSON-RPC 2.0
> 向 TUI 和 Backend 暴露设备级 AgentRuntime；AgentRuntime 管理多个相互隔离的 SessionRuntime，
> 每个 SessionRuntime 复用现有 PluginRuntimeHost 并拥有一套独立 Agent 与 Plugin 环境。
