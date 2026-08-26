# Agent Run Intervention Design｜Agent 运行中介入设计

## 文档定位

本文定义 Icarus 的“陷入内核”机制：一次 Agent Run 执行期间，内部 Plugin 可以向目标 Task
补充上下文；用户侧可以请求确定性取消。本文只定义首期机制和边界，不包含具体开发步骤。

实现计划应放在 `apps/agent/docs/plan/`，并在本文评审通过后单独编写。

## 当前执行链

当前一次用户输入按以下路径执行：

```text
AgentRuntimeService.submit
→ UserInputPlugin 创建并排队 Task
→ UserInputEvent
→ Context Provider Plugin 并行准备上下文
→ BlackboardPlugin 生成最终 input_prompt
→ BlackboardContextReadyEvent
→ AgentPlugin 创建后台 asyncio.Task
→ ReActAgent.astream
→ LLM Step → Tool Batch → Tool Result → 下一 LLM Step
→ AgentCompletedEvent / TaskErrorEvent
→ InputFinishedEvent
```

当前已经具备两个扩展基础：

- `AgentPlugin.consume()` 创建后台执行后立即返回，Plugin Runtime 可以继续接收 Event；
- ReActAgent 的 `messages` 是单次调用局部状态，可以在明确的安全边界追加运行中上下文。

当前缺少：

- `task_id` 到活动 Agent Run 的索引；
- 面向单个 Run 的运行中信息通道；
- 运行中补充信息的接收、应用和关闭语义；
- 面向单个 Task 的确定性取消入口和终态；
- 业务 Run 身份与现有 Hook `run_id` 的统一来源。

## 设计目标

- 允许当前 Agent Run 接收内部 Plugin 在主执行流之外提供的补充信息；
- 允许用户侧调用方通过代码控制确定性取消当前 Task；
- 支持 Memory、Knowledge、Supervisor 等不同 Plugin 来源；
- 保证补充信息不会破坏 Assistant ToolCall 与 ToolResult 的消息顺序；
- 保证取消请求优先于普通补充信息；
- 保证完成、取消和迟到操作只有一个明确结果；
- 保持 ReActAgent 实例无状态，运行状态只属于单次调用；
- 保持 EventBus 只按来源路由，不解释领域 Event；
- 保持 Hook 只观测，不控制主流程。

## 非目标

首期不实现：

- 一个 Task 内自动重试或创建多个 Agent Run；
- 排队 Task 的暂停、恢复或优先级调整；
- 通用优先级消息队列；
- 通过自然语言识别“停止”等控制意图；
- 已发生文件写入或外部调用的事务回滚；
- 强制终止任意同步线程；
- 多 Agent 调度；
- Memory、Knowledge 的完整产品实现。

## 身份与层级

```text
Session
└── Task
    └── Agent Run
        └── Step
            └── Tool Call
```

| 身份 | 含义 | 首期规则 |
|---|---|---|
| `session_id` | 一个 AgentRuntimeService 持有的长期会话 | 由 Runtime 边界确定 |
| `task_id` | 一次用户目标及其 Plugin 事件链 | 外部操作的目标 ID |
| `run_id` | 一次 Agent Kernel 执行 | 当前每个 Task 创建一个 Run |
| `step` | Run 内一次 LLM 决策序号 | 从 1 单调递增 |
| `tool_call_id` | 一次模型工具调用 | 使用模型返回的 ID |

首期保持 `1 Task = 1 Agent Run`，但不把两种身份合并。Task 是应用层和产品层控制目标，
Run 是 Kernel 执行实例。未来显式恢复或重试可以在同一个 Task 下创建新 Run，而不改变 Task
身份。

## 操作类型

功能由 Event 类型表达，字段只携带该操作的数据。

```python
@dataclass(frozen=True, kw_only=True)
class TaskContextInputEvent(Event):
    content: str


@dataclass(frozen=True, kw_only=True)
class TaskCancelRequestedEvent(Event):
    reason: str | None = None
```

两种 Event 都继承 `Event.task_id`、`event_id` 和 `occurred_at`。

首期不增加 `priority`、`intent`、`operation_type` 或任意 `metadata` 字典：

- Event 类型已经表达操作；
- Cancel 天然高于 Context；
- AgentPlugin 不根据文本判断是否取消；
- 未来新增操作时增加新类型，而不是继续扩充 Optional 字段。

Event 只描述请求，不包含执行方法，也不反向依赖 AgentPlugin、Harness 或 ReActAgent。

## 来源与入口

内核处理保持来源无关，但入口按职责区分：Context 只来自 Plugin Event，Cancel 同时支持应用
Service 和 Plugin Event。

```text
WebUI / TUI
→ Backend
→ 根据 session_id 定位 AgentRuntimeService
→ AgentRuntimeService.cancel_task
→ AgentPlugin.handle_task_operation

Memory / Knowledge / Supervisor Plugin
→ EventBus
→ TaskContextInputEvent / TaskCancelRequestedEvent
→ AgentPlugin.consume
→ AgentPlugin.handle_task_operation
```

一个 AgentRuntimeService 当前固定对应一个 Session，因此 Service 内部只需要 `task_id`。
多 Session 后端负责在调用 Service 前根据 `session_id` 定位正确 Runtime。

Cancel 的 Service 直接调用和 Plugin Event 不实现两套逻辑，二者汇入同一个内部处理函数。
Service 入口避免确定性取消受到普通 Plugin Event 积压影响。Context 不暴露 Service API，避免
WebUI/TUI 或 Backend 把普通用户输入绕过 Task 输入生命周期直接写入 Kernel。

“来源无关”表示 TaskChannel 和 Kernel 不依赖 Memory、Knowledge 或 Supervisor 的具体类型，
不表示功能面向最终用户，也不表示 AgentPlugin 自动接收所有 Plugin Event。Plugin Runtime
继续使用显式来源订阅；AgentRuntimeService 在装配时把允许发起 Task 操作的 Plugin 来源订阅给
AgentPlugin。新增来源只调整应用拓扑，不修改 EventBus、TaskChannel 或 ReActAgent。
AgentPlugin 的 `accepts_event()` 仅接收 Context Ready 和两种 Task 操作 Event，避免无关 Event
进入其 inbox。

## 活动执行与运行中通道

TaskChannel 在 UserInputPlugin 接受 Task 时创建，早于 Skill/Blackboard 上下文准备和 Agent Run。
AgentRuntimeService 创建一个共享 TaskChannelRegistry，并注入 UserInputPlugin 与 AgentPlugin：

```python
@dataclass
class TaskChannel:
    task_id: str
    context_queue: deque[RuntimeContextRecord]
    cancel_requested: asyncio.Event
    cancel_reason: str | None
    accepting_context: bool
```

TaskChannel 使用锁保护 Context 接收、取消和最终关闭。`context_queue` 是锁内的有序记录
集合，不对外暴露可绕过状态检查的裸队列。

```python
@dataclass
class ActiveAgentRun:
    channel: TaskChannel
    execution_task: asyncio.Task[None]
    execution_started: asyncio.Event
```

共享 Registry 和 AgentPlugin 分别维护：

```python
TaskChannelRegistry: task_id → TaskChannel
_active_runs: dict[str, ActiveAgentRun]
```

TaskChannel 属于 Task，在 Task 被接受后即可接收操作；ActiveAgentRun 只在 AgentPlugin 真正启动
Kernel 执行后存在。AgentPlugin 负责根据 `task_id` 找到通道和活动 Run；ReActAgent 仅通过每次
调用显式传入的最小运行控制接口读取通道。两者都不保存到可复用 Agent 实例中。

`ActiveAgentRun` 不重复保存 `task_id`、`run_id` 或状态：`task_id` 是活动索引的 Key，`run_id`
和状态由 TaskChannel 统一持有。`execution_started` 用于区分“协程已创建”和“协程已经进入”，
避免在协程首次调度前取消时丢失 AgentCancelledEvent。

### Agent Run 启动前

```text
submit 接受 Task
→ UserInputPlugin 创建 TaskChannel
→ Task 进入 Context Preparing
→ BlackboardContextReadyEvent
→ AgentPlugin 检查 TaskChannel
→ 未取消：创建 run_id 和 ActiveAgentRun
→ 已取消：拒绝启动 Agent Run
```

上下文准备阶段收到补充信息时，记录保存在 TaskChannel，并在第一次 LLM Step 前注入。此阶段
收到取消时，UserInputPlugin 停止等待上下文准备结果并发布取消终态；之后迟到的
BlackboardContextReadyEvent 不得启动 Agent Run。

## 补充信息记录与批量注入

每条补充信息独立记录：

```python
@dataclass(frozen=True)
class RuntimeContextRecord:
    event_id: str
    task_id: str
    source_id: str
    content: str
    received_at: datetime
```

同一个安全检查点前到达的记录按 FIFO 取出，合并为一条实际模型消息：

```text
<runtime_context>
1. 配置文件是 settings.json
2. 只修改测试环境
3. 不要调整模型名称
</runtime_context>
```

```python
Message(role="user", content=[TextPart(merged_text)])
```

原始记录不合并，Trace 可以分别记录每条信息的来源、到达时间和处理结果。模型消息合并是
为了保持输入紧凑，并避免连续多条 User Message 影响厂商协议。

## ReAct 安全响应点

补充信息只在消息协议完整的边界注入。

```text
[检查取消]
→ [取出并注入补充信息]
→ 调用 LLM Step
→ [检查取消]
→ 有 ToolCall？
    ├── 是
    │   → [检查取消]
    │   → 执行完整 Tool Batch
    │   → 写入全部 ToolResult
    │   → [检查取消]
    │   → 返回下一轮 LLM 前检查点
    └── 否
        → [最终关闭检查]
        → 有已接受补充信息：注入并增加一个 Step
        → 无补充信息：关闭接收窗口并提交 Completed
```

补充信息检查点收敛为：

1. 每次 LLM 调用前；
2. 准备发布 Completed 前。

Tool Batch 完成后的处理自然进入下一次 LLM 前检查点。

禁止在以下位置插入 User Message：

- LLM 正在生成 Token 时；
- Assistant ToolCall 与对应 ToolResult 之间；
- 同一个 Tool Batch 正在执行时。

## 补充信息与最终完成竞争

TaskChannel 必须提供原子“检查并关闭”操作，避免以下竞态：

```text
Agent 检查队列为空
→ 补充信息被接受
→ Agent 发布 Completed
```

最终关闭规则：

- 已接受补充信息存在：Run 保持开放，取出该批次并增加一个 LLM Step；
- 没有补充信息：原子关闭 Context 接收窗口，然后允许发布 Completed；
- 关闭后到达：返回 `already_finished`，不进入 TaskChannel；
- 每条接受的补充信息最多进入一个注入批次。

TaskChannelRegistry 在 Task 结束后删除活动通道，并按完成顺序保留最多 1024 个
`task_id → run_id` 墓碑。墓碑存在时迟到操作返回 `already_finished`；淘汰后返回
`not_found`。这使短期重试具有稳定结果，同时避免长 Session 无限制增长。

## Session 历史

所有补充操作都写入技术 Trace；只有被当前 Run 实际应用的补充信息写入 Session 业务历史。

Blackboard 仍然是 Session History 的唯一所有者，历史按 Message 保存、按协议完整边界提交。
正常完成和取消共用相同的消息格式，不新增另一套 Step History 模型。

成功 Task 的历史顺序为：

```text
原始 User Message
已应用 Runtime Context（如果存在）
Assistant ToolCall
对应的全部 ToolResult
...
最终 Assistant Message
```

原始 Event 独立保留，Session History 保存模型实际看到的合并 Message。这样下一轮 Task 可以
继续使用已应用的 Memory、Knowledge 或监督信息。

`AgentResponse.messages` 继续保存一次 ReAct 调用的完整消息快照；`task_message_start` 标识当前
Task 在该快照中的起点。Blackboard 只提交 `response.task_messages`，因此不会重复写入 System
Prompt 或已有 Session History。

TaskChannel 在以下安全边界保存当前 Task 的 `history_checkpoint`：

1. 每次 LLM 调用前，此时 User Input 和本轮已应用的 Plugin Context 已构成合法输入；
2. 当前 Assistant ToolCall 的全部 ToolResult 回填后，此时 Tool 协议重新完整；
3. 最终 Assistant Message 完成后。

取消时，AgentCancelledEvent 携带最近的 `history_checkpoint`。Blackboard 提交该消息片段：

- 第一次 LLM 处理中取消：保留已经交给模型的 User Input 和 Plugin Context；
- 后续 LLM 处理中取消：保留此前所有完整 Tool Step；
- Tool Batch 中取消：回退到该 Assistant ToolCall 之前的检查点；
- Agent Run 启动前取消：没有检查点，不提交本 Task。

以下信息不写入业务历史：

- Run 关闭后被拒绝的信息；
- 已接受但在注入前发生取消的信息；
- 正在流式生成但尚未完成的 Assistant Message；
- 缺少任一对应 ToolResult 的 Assistant ToolCall Batch。

## 确定性取消

取消不进入 `messages`，也不交给模型判断。

```text
TaskCancelRequestedEvent / AgentRuntimeService.cancel_task
→ AgentPlugin 找到 TaskChannel 和可选 ActiveAgentRun
→ 原子 PREPARING_CONTEXT / RUNNING → CANCELLING
→ 关闭 Context 接收窗口
→ 设置 cancel latch
→ 若 Run 已启动则 execution_task.cancel()
→ 若仍在准备上下文则阻止 Run 启动
→ 等待 Agent 执行协程退出并清理
→ Run 已启动时发布 AgentCancelledEvent
→ InputFinishedEvent(status="cancelled")
```

取消入口立即返回“请求已接受”，但只有执行协程确认退出后才能发布 `CANCELLED` 终态。

首期取消保证：

- 接受取消后不再启动新的 LLM Step；
- 接受取消后不再启动新的 Tool Batch；
- 不再接受新的补充信息；
- 不发布正常 Completed；
- 只把取消 Task 最近的协议完整消息前缀写入 Session History；
- 已经发生的文件或外部副作用不回滚。

能力限制：

- 尚未开始的 Tool 可以确定阻止；
- 异步 LLM 和原生异步 Tool 传播 `CancelledError`，Harness 等待其取消清理完成；
- 已经进入 `asyncio.to_thread()` 的同步 Tool 无法被 Python 强制终止，Agent 立即停止等待并
  隔离其迟到结果；
- BashTool 在首期改为持有异步子进程句柄；取消时先发送 `terminate()`，宽限期内未退出再
  执行 `kill()`，并等待子进程回收；
- 不可强制停止的工作结果不得继续驱动后续 Agent Step；
- 已经发生的文件写入或外部副作用不回滚。

Tool 的取消能力按执行事实分为三类，不作为模型可选择的参数：

| 能力 | 首期行为 |
|---|---|
| 尚未启动 | Harness 阻止启动 |
| 可取消异步操作 | 取消执行并等待清理 |
| 不可取消同步操作 | 停止等待、隔离结果，允许底层工作自行结束 |

## 状态与终态

首期 Agent Run 状态：

```text
CREATED → RUNNING → COMPLETED
                  → FAILED
                  → CANCELLING → CANCELLED
```

TaskChannel 状态覆盖 Agent Run 启动前的窗口：

```text
ACCEPTED → PREPARING_CONTEXT → RUNNING → COMPLETED
   │          │                  │       → FAILED
   └──────────┴──────────────────┴───────→ CANCELLING → CANCELLED
```

规则：

- `ACCEPTED / PREPARING_CONTEXT / RUNNING → CANCELLING` 只允许成功一次；
- Cancel 先取得状态转换权时，Completed 不得提交；
- Completed 先关闭 Run 时，Cancel 返回 `already_finished`；
- 重复 Cancel 返回当前取消状态，不重复发布终态；
- 未找到 Task 返回 `not_found`；
- Context 在 `ACCEPTED`、`PREPARING_CONTEXT` 和 `RUNNING` 时接受；
- 每个 Run 只发布一个 Terminal Event。

首期新增终态：

```python
AgentCancelledEvent(task_id, step, reason, task_messages, last_usage)
InputFinishedEvent(task_id, status="cancelled", run_id)
```

`AgentCancelledEvent` 只描述已经启动的 Agent Run；上下文准备阶段被取消的 Task 没有 Agent Run，
因此不伪造 AgentCancelledEvent，直接由 UserInputPlugin 发布 InputFinishedEvent。
`AgentCompletedEvent` 表达正常完成；Task 内错误统一使用 `TaskErrorEvent`，并由 `fatal` 区分是否
终止。

已启动 Run 的 `AgentCancelledEvent.task_messages` 携带最近安全检查点。`InputFinishedEvent.run_id`
用于让 Blackboard 在终态乱序时区分“Run 启动前取消”和“等待 AgentCancelledEvent 的运行中取消”。

## Harness 与组件职责

### ReActAgent

- 保持无状态；
- 维护单次调用的 `messages`、Step、Usage 和 ToolResult；
- 在稳定边界读取显式传入的运行控制接口；
- 在最终运行结果中标记当前 Task 消息起点，供 Blackboard 提交完整消息链；
- 不依赖 AgentPlugin、EventBus、Blackboard 或具体业务 Plugin。

### AgentPlugin

- 创建业务 `run_id` 和 ActiveAgentRun；
- 维护 `task_id → ActiveAgentRun`；
- 接收 Service 的 Cancel 操作和 Plugin 来源路由 Event；
- 将 Context 写入 TaskChannel；
- 立即发起代码层取消；
- 发布原始 Agent Stream 和唯一终态；
- Run 完成后移除活动索引。

### UserInputPlugin

- Task 被接受时创建 TaskChannel，再将 PendingInput 放入 FIFO；
- 等待 Agent 终态或 cancel latch，二者先到者决定后续流程；
- 上下文准备阶段取消时，不等待 AgentPlugin 产生终态；
- Run 尚未启动时直接收口 cancelled；Run 已启动时等待 AgentCancelledEvent；
- 发布唯一 InputFinishedEvent，并在 Task 结束后关闭对应 TaskChannel。

### AgentRuntimeService

- 作为当前 Session 的应用入口；
- 提供 `cancel_task(task_id, reason)`；
- 不直接修改 ReActAgent 的消息或内部状态；
- 返回明确的接受或拒绝结果。

### Plugin Runtime / EventBus

- 继续只按来源路由；
- 不识别 Context 或 Cancel Event；
- 不增加全局优先级队列；
- 不承担 Task 状态机。

### BlackboardPlugin

- 保持 Session History 所有权；
- 成功时提交当前 Task 的完整 Message 链，包括 ToolCall 和 ToolResult；
- 取消时提交最近的协议完整消息前缀；受控的最大 Step 截停也可提交安全检查点，其他失败和
  Run 启动前取消不提交；
- 不直接修改正在执行的 ReActAgent。

### Hook / Persistence

- `task.operation` 记录操作请求和明确结果状态；
- `task.context(applied)` 按原始 event_id 记录实际进入模型的补充信息；
- Agent 与 Event 流的既有 Hook 记录完成、失败和取消终态；
- 使用业务 `run_id` 关联 Agent、LLM 和 Tool 轨迹；
- 不改变操作结果或执行顺序。

## 错误与结果语义

Service Cancel 和 Plugin Event 操作需要返回明确结果，不用异常表达正常竞争：

```text
accepted
not_found
not_running
already_cancelling
already_finished
invalid_content
```

系统异常仍通过异常和 Hook 记录。EventBus 来源无法同步获得返回值时，AgentPlugin 发布对应的
操作结果 Event，携带原请求 `event_id`，用于异步关联。Context 来源使用发布方的 `plugin_id`；
WebUI/TUI 不直接提交 Context。用户侧 Cancel 通过 Backend 调用 Service。

Service Cancel 只返回 TaskOperationResult，不额外发布结果 Event；只有 EventBus 请求发布异步
结果 Event，避免上层订阅收到未请求的控制响应。

## 首期实现范围

首期实现：

- 两种明确操作 Event；
- Service Cancel 入口；
- Plugin Context Event 入口；
- Task 接受时创建的 `task_id → TaskChannel`；
- Agent 启动时创建的 `task_id → ActiveAgentRun`；
- Context FIFO 与取消 latch；
- LLM 前及 Completed 前检查点；
- Context Batch 合并并直接保留在当前 Task 消息链；
- 完整 ToolCall / ToolResult 历史与取消安全检查点提交；
- Task 级确定性取消和 cancelled 终态；
- 操作生命周期 Trace；
- 定向单元与集成测试。

首期不要求：

- 真实 Memory/Knowledge 产品接入；
- 同步线程中的 Tool 强制终止；
- Task 自动重试；
- 一个 Task 多 Run；
- 通用 Operation 框架或固定完整枚举。

## 验收标准

- Agent 执行期间，已授权 Plugin 可以向同一个活动 Task 提交补充信息；
- 同一检查点前的多条信息按接收顺序合并，并至少影响后续一次 LLM Step；
- ToolCall 与 ToolResult 之间不会插入补充 User Message；
- Completed 竞争中不会出现“已接受但未应用仍成功完成”；
- 取消请求由代码立即介入，不由模型判断；
- 接受取消后不启动新的 LLM Step 或 Tool Batch；
- 一个 Run 最多发布一个终态；
- 一个 Task 最多发布一个 InputFinishedEvent；
- 取消 Task 只写入最近的协议完整 Session History；
- 已应用 Context Batch 在成功后写入 Session History；
- 正常完成的 ToolCall 和 ToolResult 写入 Session History；
- EventBus、Hook、Blackboard 和 ReActAgent 的既有依赖方向不变。

## 代码变动范围

### 新增运行控制模块

建议新增 `apps/agent/src/agent_orchestration/run_control/`：

```text
run_control/
├── __init__.py
├── events.py       TaskContextInputEvent / TaskCancelRequestedEvent / 结果 Event
├── channel.py      TaskChannel、Context FIFO、取消 latch、原子关闭
├── registry.py     task_id → TaskChannel
└── types.py        RuntimeContextRecord、AppliedContextBatch、TaskChannelStatus、操作结果类型
```

该模块只依赖通用 Event 和模型 Message 类型，不依赖 AgentPlugin、Blackboard、具体业务 Plugin
或应用层。

### Agent Runtime 应用入口

修改 `apps/agent/src/application/agent_runtime_service.py`：

- 创建并持有一个 TaskChannelRegistry；
- 将同一个 Registry 注入 UserInputPlugin 和 AgentPlugin；
- 保存 AgentPlugin 引用；
- 新增 `cancel_task(task_id, reason)`；
- Cancel 入口只委托 AgentPlugin 的统一操作处理，不直接访问 ReAct messages。

修改 `apps/agent/src/application/__init__.py`，导出调用方需要的结果类型。OutputBridge 无需
理解新 Event，只继续广播已订阅来源的公开结果。

### UserInputPlugin

修改：

- `plugins/user_input/plugin.py`；
- `plugins/user_input/events.py`；
- `plugins/user_input/__init__.py`。

内容：

- 接受 Task 时创建 TaskChannel；
- FIFO Worker 同时等待 Agent 终态与 cancel latch；
- 上下文准备期间取消时阻止 Task 继续并发布 InputFinishedEvent；
- `InputFinishedEvent.status` 增加 `cancelled`；
- 保证每个 Task 只发布一个 InputFinishedEvent。

### AgentPlugin

修改 `plugins/agent/plugin.py`，并在需要时新增同目录下的运行句柄文件：

- `_tasks: set[asyncio.Task]` 改为 `_active_runs: dict[task_id, ActiveAgentRun]`；
- 消费 BlackboardContextReadyEvent 时检查 TaskChannel，再决定是否启动 Run；
- 为 Run 创建业务 run_id；
- 处理 TaskContextInputEvent 与 TaskCancelRequestedEvent；
- 通过 `accepts_event()` 拒绝订阅来源中的其他 Event；
- Service Cancel 和 EventBus 操作汇入同一内部函数；
- Service Cancel 直接返回结果，EventBus 入口才发布结果 Event；
- 取消执行协程，并将 CancelledError 转换为唯一 AgentCancelledEvent；
- 正常、失败或取消后原子关闭 Run 并移除活动索引。

修改 `plugins/agent/__init__.py` 和顶层 `plugins/__init__.py`，只导出公共 Event 与结果类型。

### ReActAgent 与观测包装器

修改：

- `capability/base_agent.py`；
- `capability/react_agent.py`；
- `capability/types.py`；
- `hooks/wrappers/observable_agent.py`。

内容：

- Agent 调用显式接收最小 Run Control 接口；
- 在每次 LLM 前、Tool Batch 启动前和 Completed 前执行控制检查；
- 将 Context FIFO 合并为一条结构化 User Message；
- 在 AgentResponse 中返回完整 messages 和当前 Task 起点，供 Blackboard 提交历史；
- ObservableAgent 使用业务 run_id 建立 HookContext，不再为 Runtime 调用生成另一套 ID；
- 独立调用方未提供 Run Control 时，ObservableAgent 仍可创建观测用 run_id。

首期生产链仍以 `astream` 为主，但 `BaseAgent` 四种入口都接受同一个可选 Run Control 参数。
`invoke`、`ainvoke`、`stream` 和 `astream` 必须在对应的 LLM 前、Tool Batch 前和完成前使用
一致的检查语义；具体循环去重可以在实现中按最小方式完成，不允许四种入口产生不同控制结果。

### Blackboard 与上下文历史

修改：

- `plugins/blackboard/state.py`；
- `plugins/blackboard/plugin.py`。

内容：

- 识别 AgentCancelledEvent 和 `InputFinishedEvent(status="cancelled")`；
- 正常完成时提交 `AgentResponse.task_messages` 的完整 Tool 轨迹；
- 取消时提交 AgentCancelledEvent 携带的最近安全消息前缀；
- Run 启动前取消和普通失败 Task 不提交 Session History；最大 Step 截停提交明确携带的安全检查点；
- 保留双终态乱序保护和历史提交幂等。

BlackboardContextReadyEvent 无需重新加入原始 ContextBlock 或 Context Error。

### Tool 执行与 Bash

修改：

- `tools/builtin/bash_tool.py`；

内容：

- ReActAgent 在每个 Tool Batch 启动前检查取消；
- 复用现有 BaseTool / ToolExecutor 的异步取消传播和同步线程结果隔离；
- BashTool 增加原生异步子进程实现，保存进程句柄并在取消时执行
  `terminate → grace period → kill → wait`；
- 不修改 ToolDefinition，不向模型暴露取消能力字段。

### Skill、EventBus 与 Persistence

SkillPlugin 只增加取消终态清理，不改召回和维护逻辑。EventBus 与 PluginRuntime 不增加优先队列，
仅通过现有来源订阅传递新 Event。AgentPlugin 通过现有 HookDispatcher 记录操作请求、结果与
Context 应用，并让业务 run_id 贯穿 Agent、LLM 和 Tool Hook；不赋予 Hook 控制能力。

### TUI 与后端接入

核心机制完成后再修改 TUI：

- `Ctrl+C` 调用 `AgentRuntimeService.cancel_task(task_id)`；
- 显示 Cancelling 和 Cancelled；
- 保留已显示的部分输出；
- 取消终态后再调度下一条本地队列消息。

WebUI/Backend 只负责使用 `session_id` 定位 Runtime，再传递 `task_id` 和明确的 Cancel 操作，
不解析自然语言控制意图，也不直接提交运行中 Context。

## 测试范围与时机

正式功能完成前只运行受影响模块的定向测试：

- TaskChannel 的 FIFO、原子关闭、重复取消和完成竞争；
- UserInputPlugin 的准备阶段取消与唯一终态；
- AgentPlugin 的活动 Run 索引和来源无关操作；
- ReActAgent 的 LLM 前、Tool 前、Completed 前检查点；
- Context Batch 顺序、消息协议和历史提交；
- 异步 Tool 取消、同步 Tool 结果隔离和 Bash 子进程终止；
- Blackboard/Skill 的取消清理。

首期功能完成并通过定向测试后，再运行 Agent 全量测试、compileall 和 diff check。TUI 接入作为
独立步骤运行 TUI Pilot、Replay 和 Snapshot。最终实现已通过 Agent 335 项测试、TUI 86 项测试
（其中 7 项 Snapshot），以及 compileall 和 diff check。
