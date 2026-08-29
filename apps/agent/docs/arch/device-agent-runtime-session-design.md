# Device Agent Runtime and Session Runtime Design｜设备级 Agent Runtime 与 Session Runtime 设计

## 1. 文档定位

本文记录 Agent 应用层从原单 Session `AgentRuntimeService` 演进到设备级多 Session Runtime 的
已实现边界。本文确定对象、所有权、公开契约和新旧 Session 的运行逻辑；实施步骤见同应用 plan。
Gateway 的网络定位见：

`apps/gateway/docs/arch/agent-gateway-positioning-design.md`。

## 2. 迁移前实现事实

迁移前 `AgentRuntimeService` 管理一个固定 Session。每个实例会创建独立的：

- HookRegistry 和 PersistenceRuntime；
- ToolRegistry；
- PluginManager 和 EventBus；
- OutputBridgePlugin（目标架构中重构为 RuntimeUpdatePlugin）；
- 绑定 `workspace_path + session_id` 的 PluginRuntimeHost；
- Agent、Blackboard、UserInput、Skill、Persistence 等 Plugin 实例。

当前 Plugin 不共享后再根据 `session_id` 分流。基础 Event 只显式包含 `task_id`，Session 主要通过
独立实例、EventBus、SessionIdentity 与 Hook/Persistence Context 隔离。

该单 Session 执行链已经具备良好的 Session 隔离，并已迁移为 SessionRuntime；当前 AgentRuntime
提供设备级多 Session 管理入口。

## 3. 当前结构

```text
AgentRuntime                     # 一台设备一个逻辑实例
├── SessionRuntime A             # 一个 Session 一套执行环境
│   └── PluginRuntimeHost A
│       ├── PluginRuntime(agent)
│       ├── PluginRuntime(blackboard)
│       ├── PluginRuntime(user-input)
│       └── ...
├── SessionRuntime B
│   └── PluginRuntimeHost B
└── SessionRuntime C
    └── PluginRuntimeHost C
```

对象层级：

```text
AgentRuntime
└── SessionRuntime
    └── PluginRuntimeHost
        └── PluginRuntime
            └── Plugin
                └── Task
                    └── Agent Run
                        └── Model Step / Tool Call
```

## 4. AgentRuntime 定位

AgentRuntime 是设备级唯一的运行管理器，负责：

- 整个 Agent 系统的启动和停止；
- Session Registry；
- 创建新 Session；
- 打开、恢复、复用和卸载旧 Session；
- 将提交、取消和状态查询路由到对应 SessionRuntime；
- 聚合各 SessionRuntime 发布的公共 RuntimeUpdate，并发布自身拥有的 Session 生命周期 Update；
- 停止时收束全部已加载 SessionRuntime。

它不执行 ReAct、不保存第二套 Blackboard、不维护网络连接，也不访问 Gateway 协议。

## 5. SessionRuntime 定位

SessionRuntime 是单 Session 的组装、生命周期和应用操作单元，负责：

- 持有 SessionIdentity；
- 构造和启动 PluginRuntimeHost；
- 取得 UserInput、Task Control 和 Output 等 Capability；
- 接收当前 Session 的任务；
- 管理当前 Session 的 Runtime Queue 与任务控制；
- 恢复和保存 Session Plugin State；
- 关闭当前 Session 的 Plugin 与后台任务。

原 `AgentRuntimeService` 的单 Session 组装、生命周期和应用操作职责已经迁移到 SessionRuntime。
当前只对外暴露 AgentRuntime；Gateway 不经过 AgentRuntimeService。旧 Service、公开导出和对应旧
测试已经删除，没有保留兼容门面。

## 6. Plugin 与 Session

当前状态型 Plugin 天然绑定一个 Session：

- BlackboardPlugin 直接持有当前 Session 的消息、Compact 标记和 Task State；
- UserInputPlugin 直接持有当前 Session 的 PersistenceSession、FIFO Queue 和 active task；
- AgentPlugin 直接持有当前 Session 的 TaskChannelRegistry 和 active runs；
- PersistencePlugin 直接持有当前 SessionIdentity；
- SkillPlugin 依赖当前 Session 的 Blackboard conversation 和 Session State；
- 迁移前的 OutputBridgePlugin 只输出一套 Session Event；当前已按职责重构为 RuntimeUpdatePlugin。

因此第一阶段继续为每个 SessionRuntime 创建一套 Plugin 实例、EventBus 和 PluginRuntimeHost。
不把这些 Plugin 改成内部维护 `sessions[session_id]` 的多租户单例。

Python 已导入模块可以由进程自然复用；Manifest、配置、Capability、Tool 和 Event 拓扑在每个
SessionRuntime 启动时解析，并在该 SessionRuntime 生命周期内冻结。配置或 Plugin 目录发生变化时，
已加载 Session 不热更新；之后新建或重新恢复的 Session 使用当时的最新配置。此前文档所说的
“下一次 Runtime 启动生效”，在新的设备级 Runtime 语义下修正为“下一次 SessionRuntime 启动生效”。

所有有持久状态的 Plugin 必须通过现有 StateProvider 机制提供 Session 快照和恢复能力；无状态
Plugin 可以继续声明空 `state_scopes`。Skill 的 Catalog 与 Repository 仍可读取 global/workspace
目录，但 Skill Job、通知和其他运行状态改为按 SessionIdentity 保存，不再由多个 SessionRuntime
分别覆盖同一份 Workspace Plugin State。

### 6.1 Plugin 状态兼容

`state_version` 是 Plugin 持久状态格式的兼容契约。`plugin_version` 和 `manifest_hash` 继续写入
快照，用于记录状态来源和诊断，但它们发生变化本身不阻止恢复。只要 `state_version` 与当前
Plugin 声明相同，Host 就把旧状态交给当前 StateProvider 恢复。

如果 `state_version` 不同，或者 StateProvider 实际恢复失败：

- `required_plugin_ids` 中的核心 Plugin：SessionRuntime 恢复失败，回滚已经启动的 Plugin，失败实例
  不进入 Session Registry；
- 非核心 Plugin：记录警告、停止并禁用该 Plugin，保留原快照，不阻止 SessionRuntime 继续恢复；
- Host 按现有显式 Capability 依赖和 Event 发布者依赖递归禁用受影响的 Plugin；
- 依赖级联触及核心 Plugin 时，SessionRuntime 恢复失败。

第一阶段不新增状态迁移器，也不自动丢弃或覆盖不兼容快照。Plugin 改变持久状态格式时必须提升
对应的 `state_version`。

### 6.2 Persistence 多 Session 隔离

第一阶段保持当前所有权模型：每个 SessionRuntime 独立持有一套现有 `PersistenceRuntime`、
HookRegistry、Trace Writer 和 Logger Handler，并在卸载时只关闭自己的资源。不新增设备级共享
Persistence、Session Adapter 或引用计数。

为避免同一进程中的多个 Logger Handler 重复写入，每个 SessionRuntime 的 Handler 只接受
HookContext 中完整 SessionIdentity 与自身一致的日志；其他 Session 或缺少完整 SessionIdentity 的
日志不由该 Handler 写入。Trace Hook 继续注册在当前 SessionRuntime 独立的 HookRegistry 中。

### 6.3 Plugin 后台工作

未来 Plugin 可以在 `consume()` 或 Tool 返回后继续运行后台工作，但这些工作必须由所属
PluginRuntime 统一创建和跟踪。第一阶段不新增 TaskManagerPlugin 或独立 Tracker 对象，只在现有
PluginRuntime 中保存活动后台 Task、最近后台工作变化时间和安全错误摘要，并向所属 Plugin 绑定
受控启动入口。

Plugin 继续拥有具体业务 Job 的状态、结果、取消和持久化语义；PluginRuntime 只负责通用生命周期、
异常观测、空闲判断和退出等待。EventBus 仍只传递业务 Event，不把后台 Task 状态变成新的内部
路由协议。任何脱离当前调用继续运行的长期 asyncio Task 必须登记；线程工作必须由已登记协程包裹，
不得创建脱离 PluginRuntime 生命周期的裸线程。

## 7. Agent 生命周期

“一个 Session 对应一个 Agent”在产品语义上成立，在代码上准确表示为：

- 一个 Session 对应一套独立 AgentPlugin、AgentFactory、Blackboard 与 Plugins；
- ReActAgent 保持无状态，不作为旧 Session 对象反序列化；
- 每个 Task 开始时，AgentPlugin 从 AgentFactory 获取本次 Agent Run 使用的 Agent；
- Task 完成后 Run 结束；
- Session 连续性由 Blackboard、Plugin State、History 和 Persistence 保持。

## 8. 新 Session 流程

```text
调用方请求新对话
→ AgentRuntime 创建 SessionIdentity
→ 创建并注册 SessionRuntime
→ SessionRuntime 创建 PluginRuntimeHost
→ Host 构建 Plugin Graph 和实例
→ 初始化空 Session 元数据与 Plugin State
→ Session Ready
→ 提交首个 Task
→ Blackboard 准备上下文
→ AgentPlugin 创建本次 Agent Run
→ 原有 Agent 流程
```

## 9. 旧 Session 流程

已加载时：

```text
请求旧对话
→ AgentRuntime 找到 SessionRuntime
→ 直接提交新 Task
```

尚未加载时：

```text
请求旧对话
→ Persistence 确认 Session 存在
→ AgentRuntime 创建 SessionRuntime
→ SessionRuntime 创建 PluginRuntimeHost 和当前版本 Plugin 对象
→ Host 从本地恢复 Blackboard、Plugin State、Asset 与元数据
→ Session Ready
→ 提交新 Task
```

不恢复旧 ReActAgent、旧 Plugin 对象、协程、模型连接或未完成 Python 调用栈。

### 9.1 并发 Resume

Session Registry 使用完整 `SessionIdentity`，即 `workspace_key + session_id`，作为加载键。同一个
SessionIdentity 在设备级 AgentRuntime 中最多只有一个活动 SessionRuntime。

- Session 已加载：所有调用方复用同一个 SessionRuntime；
- Session 正在恢复：后续调用等待同一次恢复结果，不再创建第二套 PluginRuntimeHost；
- 恢复失败：所有等待调用方收到同一失败结果，失败实例不进入活动 Registry，后续可以重试；
- 不同 SessionIdentity：可以并发恢复和运行；
- Session unload 与 resume：按同一身份串行化，避免状态快照和恢复同时发生。

多个 Backend/TUI 客户端可以同时观察或提交到同一个已恢复 Session，但它们共享一个 Runtime Queue、
Blackboard 和 Plugin State，不分别恢复多份会话对象。

### 9.2 SessionRuntime 卸载

对外统一使用 `unload_session(SessionIdentity)` 表达释放已加载的 SessionRuntime。卸载只释放运行
实例，不删除 SessionIdentity、快照、Asset 或其他持久化数据；以后仍可重新 resume。删除 Session
数据是另一个产品操作，不在第一阶段范围内。

SessionRuntime 只在以下情况关闭：

- 调用方显式请求 unload；
- 连续 6 小时没有状态变化，并且到期复检确认可以安全卸载；
- AgentRuntime 整体停止并收束全部已加载 SessionRuntime；
- SessionRuntime 启动或恢复失败，需要清理半初始化实例。

网络连接断开、TUI 或 Backend 退出、客户端切换到其他 Session，以及 Session 暂时空闲，都不会
立即关闭 SessionRuntime。普通 unload 遇到运行中或排队中的 Task 时返回“Session 正忙”，不隐式
取消任务；调用方应先等待任务结束或显式取消，再重新 unload。

正常卸载复用现有 Host 生命周期：停止接受新 Task，quiesce，drain，snapshot，停止 Plugin 和后台
任务，关闭当前 SessionRuntime 独立的 PersistenceRuntime，最后从活动 Registry 移除。

### 9.3 六小时空闲卸载

第一阶段由 AgentRuntime 自动卸载连续 6 小时没有状态变化的 SessionRuntime，以释放内存中的
Plugin、Queue 和其他运行资源。连接或订阅是否仍然存在不影响空闲判定；自动卸载后，下一次提交
或其他需要运行实例的操作按现有 single-flight resume 流程重新加载该 SessionRuntime。

只有同时满足以下条件才可以自动卸载：

- SessionRuntime 已 Ready；
- 没有运行中的 Task；
- Runtime Queue 中没有排队 Task；
- 没有未结束的 Plugin 后台工作，例如 Skill Job；
- 距离最后一次状态变化已经达到 6 小时。

新建或 resume 的 SessionRuntime 在进入 Ready 时初始化最后活动时间，之后再按状态变化刷新。

以下变化刷新最后活动时间：Runtime 接受新 Task；Task 完成、失败或取消；Plugin 后台 Job 创建、
推进或结束；其他会修改 Session 持久状态的操作。Session 状态查询、Session 列表读取、
RuntimeUpdate 订阅、心跳和健康检查等只读行为不刷新时间。

到期检查不能直接按旧时间戳关闭实例。AgentRuntime 必须先进入与该 Session resume/unload 共用的
串行区，再重新检查最后活动时间、Task、Runtime Queue 和 Plugin 后台工作；任一条件不再满足时，
放弃本次自动卸载。条件仍满足时，调用与显式 unload 相同的快照和关闭流程。

## 10. 客户端队列与 Runtime 队列

客户端队列与 Runtime Queue 同时保留：

- 客户端队列负责发送前缓冲、等待确认、连接失败、重试和 UI 恢复；
- Runtime Queue 负责接受后的 Task 排队、执行顺序、取消和终态。

Runtime 返回 `task_id` 后，Runtime 对执行事实负责；客户端仍可保留状态投影和未确认请求，以增加
故障处理空间。提交使用调用方生成的 `submission_id` 做 Session 内有界内存去重：相同 ID 与相同
内容返回原 `InputAccepted`，相同 ID 与不同内容返回冲突；进程重启后不保证去重。

## 11. 复用与改造范围

直接复用：

- ReActAgent、AgentFactory 和 AgentPlugin；
- PluginRuntimeHost、PluginManager、EventBus 和 PluginRuntime；
- Blackboard、UserInput、Skill 和 Persistence Plugin 的现有单 Session 行为；
- OutputBridgePlugin 的 Event 消费和订阅广播实现按职责拆分复用；
- Tool、Run Control、Task Error、Compact、Usage 和图片 Asset；
- 当前单 Session 的状态恢复、取消和清理测试。

需要新增或改造：

- 设备级 AgentRuntime 与 Session Registry；
- 当前 AgentRuntimeService 到 SessionRuntime 的职责迁移；
- Session create/open/resume/unload 生命周期；
- 同 Session single-flight resume 与不同 Session 并发恢复；
- 多 Session 请求路由、公共 RuntimeUpdate 投影、输出聚合和状态查询；
- Skill Plugin State 全部按 SessionIdentity 隔离；
- Persistence 的 SessionIdentity 路由及多 Session 日志、Trace 和状态写入安全；
- Gateway 使用的稳定公开接口；
- 当前 TUI 对单 Session Service 和内部 Event 的依赖。

## 12. AgentRuntime 应用契约

AgentRuntime 是 Agent 应用层唯一公开入口。Gateway 使用扁平的 `workspace_path + session_id` 参数，
不负责构造内部 SessionIdentity。第一阶段公开能力包括：

- 启动和停止设备级 Runtime；
- 创建并立即加载一个新 Session；
- 向已存在 Session 提交任务，未加载时自动 single-flight resume；
- 取消已加载 Session 中的 Task；
- 卸载 SessionRuntime；
- 查询单个 Session、Workspace 下的 Session 列表和已加载 Session 的 Task 状态；
- 读取 Session 的持久化公共会话记录，不要求加载 SessionRuntime；
- 订阅设备级 RuntimeUpdate。

`create_session` 遇到已存在的本地 Session 时失败，不转成 resume；`submit` 遇到不存在的 Session 时
失败，不隐式创建。`cancel_task` 不为取消请求恢复已卸载 Session。每次 create 或 resume 重新调用
`get_config()`，得到该 SessionRuntime 生命周期内冻结的配置快照。

每个 SessionIdentity 使用独立的 mutation lock 串行 create、resume、submit、cancel、unload 和 Registry
替换；状态查询不取得该锁，只读取最新不可变投影。严格 single-flight resume 使用同一个共享恢复
Task，使同时等待的调用方得到同一次成功或失败。生命周期统一表达为 `loading`、`ready`、`running`、
`unloading`、`unloaded` 和 `failed`；`failed` 只表示最近一次内存加载失败，不是持久化业务状态。

## 13. 公共 RuntimeUpdate

迁移前的 OutputBridgePlugin 已按职责重构并改名为 RuntimeUpdatePlugin：

```text
内部 source_plugin_id + Event
→ RuntimeUpdatePlugin
→ RuntimeUpdate
→ AgentRuntime 设备级广播
```

RuntimeUpdatePlugin 只识别已声明的内部 Event 并投影，不再向外发送原 Event，也不维护设备级客户端
订阅。AgentRuntime 只聚合公共 Update、广播多个独立有界订阅队列，并直接发布自己拥有的 Session
生命周期 Update。订阅队列溢出时关闭慢订阅并返回明确错误，不阻塞 Runtime，也不静默丢弃单条
Update。

公共信封保持扁平：`workspace_key`、`session_id`、可选 `task_id`、`type`、JSON 兼容 `payload` 和
`occurred_at`。第一阶段类型包括 Session 生命周期、Task 接受/开始/结束/错误/累计 Usage、助手文本
增量、Tool 开始/完成和 Context Compact。成功且存在累计 Usage 时先发布 `task.usage`，再发布
`task.finished`；失败和取消不把 `last_usage` 伪装成累计值。

当前实时链路复用现有 FIFO，不提供断线补发。下一阶段为 Session 历史恢复增加 Session 内 sequence
与持久化公共会话记录；它只解决 Session 历史和历史/实时交接，不扩展为设备级全局事件日志。

### 13.1 持久化会话记录与状态 Checkpoint

为了让 TUI、Backend、GUI 和 WebUI 在打开已有 Session 时恢复完整展示，Agent 应用层为每个 Session
持久化一份公共会话记录：

```text
sessions/<session_id>/conversation.jsonl
```

它是客户端会话展示的持久化事实源，使用 RuntimeUpdate 的公共语义，不保存内部
`source_plugin_id + Event`、Hook Trace、System Prompt 或完整 AgentResponse。`trace.jsonl` 继续只用于
观测和诊断；Blackboard State 继续作为下一次 Agent Run 的模型上下文事实源。三者职责不能互相
替代：

| 数据 | 用途 | 是否作为 TUI 历史来源 |
|---|---|---|
| Blackboard State | 下一轮模型上下文 | 否 |
| `conversation.jsonl` | 跨客户端会话展示 | 是 |
| `trace.jsonl` | Hook 观测与故障诊断 | 否 |

每条持久化记录包含 `schema_version=1`、RuntimeUpdate 的 SessionIdentity、`task_id`、`type`、
`payload`、`occurred_at`，并增加当前 Session 内单调递增的 `sequence`。公共 RuntimeUpdate 对会话记录
类型携带该 sequence，
用于按序读取、去重以及历史与实时 Update 的无缝交接；它不承诺跨 Session 全局有序。Session 生命周期
Update 不写入会话记录，也不占用会话 sequence。

会话记录覆盖：

- `user.message`：用户可见原文和已导入 Session Asset 的稳定资源引用；
- `task.accepted`、`task.started`、`task.usage` 和 `task.finished`；
- `assistant.text_delta`；
- `tool.started` 和 `tool.completed`；
- `task.error`；
- `context.compacted`。

`user.message` payload 保持扁平：`text` 是用户可见原文，`resources` 是已经导入 Session 的
`resource_id + media_type` 列表。图片只记录稳定 Asset 引用和媒体类型，不记录 Base64、调用方绝对
路径或暂存路径。`session.submit`
中的 `prompt` 保持用户可见原文；纯图片默认请求和附件顺序提示由 SessionRuntime 在提交给 Blackboard
前生成，不能反向污染 `user.message`。`InputQueuedEvent` 携带已接受的用户原文与稳定 Asset 引用，
RuntimeUpdatePlugin 按 `user.message → task.accepted` 的顺序投影，使所有客户端看到同一份用户输入。

AgentRuntime 在调用 SessionRuntime 前生成 `task_id` 并先持久化 `user.message`；SessionRuntime 和
UserInputPlugin 接受该 task_id，避免应用层与 Plugin 各自生成任务身份。RuntimeUpdatePlugin 继续负责
内部执行 Event 的公共投影；AgentRuntime 负责为可展示 Update 分配
sequence，并通过当前 Session 的 PersistenceRuntime 追加会话记录。所有可展示记录都必须先成功
追加并 flush，再进入设备级广播。同一 Session 的 append、sequence 分配和历史读取边界串行，不影响
不同 Session 并发。写入失败必须成为明确的 Session 持久化错误，不能静默广播一条无法恢复的
Update。

如果持久化记录中某个 Task 已有任意记录，但没有 `task.finished`，并且当前 AgentRuntime 中也不存在
该活动 Task，则把它视为上次进程异常结束。历史读取会为该 Task 持久化唯一的
`task.finished(status="interrupted", recovered=true)` 记录。已产生的用户消息、助手文本、Tool 和错误
继续保留；未完成 Tool 由客户端在该终态下标记为 interrupted，不伪造成功或失败结果。

本功能不读取或迁移旧 Session 的 `trace.jsonl`。没有 `conversation.jsonl` 的旧 Session 返回空展示
历史；它仍可按现有 Blackboard State 恢复模型上下文。旧 Session 升级后的新 Task 从新的 journal
起点开始记录。

会话展示持久化不能代替 Plugin State 持久化。AgentRuntime 处理 `task.finished` 时要求所属
SessionRuntime 先执行 checkpoint；SessionRuntime 等待当前 EventBus 与 Plugin inbox drain，再由
PluginRuntimeHost 和现有 StateCoordinator 对已声明的 StateProvider 执行 snapshot。checkpoint 成功后
才持久化和广播 `task.finished`。Host 停止时仍保留完整 snapshot；终态 checkpoint 用于避免进程在
下一次 unload 前退出而丢失最后几轮 Blackboard 与 Plugin State。该流程复用现有 Host 状态能力，不让
Plugin 直接操作文件，不新增 TaskManagerPlugin 或第二套 Plugin 状态接口。checkpoint 失败时发布安全的
持久化错误并将公共 Task 终态收束为 failed，不能让客户端误以为上下文已经可恢复。

## 14. 本地资源提交

调用方先把图片写入 Runtime 允许访问的受控本地暂存目录，RPC 只提交稳定 ResourceRef，不提交
Base64 或任意绝对路径。AgentRuntime 在 submit 返回 `task_id` 前完成 Resource ID 解析、安全校验和
目标 Session `assets/` 导入，再把现有 `ImagePart(source_type="asset")` 放入 Runtime Queue。之后
继续复用 Blackboard、`resolve_image()` 和 Provider 转换链。

## 15. 架构约束

- 一个设备只有一个逻辑 AgentRuntime；
- 一个已加载 Session 只有一个 SessionRuntime；
- SessionRuntime 之间的 Plugin 实例、EventBus、Blackboard 和 Queue 相互隔离；
- 同一个 SessionIdentity 最多存在一个活动 SessionRuntime；
- Task 只属于一个 Session；
- AgentRuntime 不实现第二套 Task Queue 或 Blackboard；
- SessionRuntime 不感知 Gateway、Backend 或 UI；
- 网络连接断开不自动关闭 Session 或取消 Task；
- Runtime 停止时必须收束所有 SessionRuntime。
- Plugin 与配置在单个 SessionRuntime 生命周期内保持稳定，新启动的 SessionRuntime 才读取变化；
- AgentRuntime 对外只发布公共 RuntimeUpdate，不暴露内部 `source_plugin_id + Event`。
- 第一阶段不设置活动 SessionRuntime 数量上限；只按连续 6 小时无状态变化且无 Task、排队工作或
  Plugin 后台工作执行自动空闲卸载，不实现基于数量或内存压力的淘汰。
- SessionRuntime、AgentRuntime 和 Gateway 不长期保留 AgentRuntimeService 兼容层；
- RuntimeUpdatePlugin 与 AgentRuntime 设备级广播职责分离，Gateway 不接收内部 Plugin Event。

## 16. 一句话定义

> AgentRuntime 是一台设备唯一的多 Session 管理器；SessionRuntime 是由当前单 Session
> AgentRuntimeService 演进而来的独立执行环境，每个 SessionRuntime 复用 PluginRuntimeHost 并拥有
> 一套隔离的 Agent、Plugin、Blackboard、EventBus 和任务队列。
