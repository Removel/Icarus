# Agent Core Capability Completion Design｜Agent 基础能力补全设计

## 文档定位

本文定义 Icarus 进入产品化开发前需要补齐的 Agent 基础能力。本阶段不重新划分已经稳定的
Agent Kernel、Harness、Plugin Runtime、Blackboard、Persistence 和模型接入层边界，而是在现有
实现上完成必要的代码收敛与能力补全。

本文描述架构、行为契约与验收边界。具体实施步骤见
`apps/agent/docs/plan/agent-core-capability-completion-development-plan.md`。

相关文档：

- 产品方向：`docs/product-positioning.md`；
- 当前 Agent 路线：`docs/todo/agent-core.md`；
- 总体路线：`docs/todo/development-roadmap.md`。
- 实施计划：`apps/agent/docs/plan/agent-core-capability-completion-development-plan.md`。

## 1. 背景

### 1.1 核心结论

当前 Agent 已具备无状态 ReAct、同步与异步调用、流式输出、Tool 执行、任务取消、运行中
Context 注入、Plugin Runtime、Skill 主动发现与演化等主干能力。进入 Session 管理和多 UI
产品化之前，需要进一步补齐以下基础能力：

- 提取四种 ReAct 入口中的重复流程，保证行为一致；
- 由 Harness 对一次 Agent Run 设置 256 个模型 Step 的确定性上限；
- 使用统一错误 Event 暴露致命与非致命错误；
- 在每轮对话开始时根据模型返回的 Usage 对 Blackboard 历史执行简单 Compact；
- 将用户提供的本地图片接管到现有 Session `assets/`，并在 Context 中保存稳定引用。

这些能力完成后，产品层只需负责 Session、持久化、恢复、界面投影和用户操作，不需要重新进入
Agent Kernel 补充基础执行机制。

### 1.2 当前问题

| 当前状态 | 问题 | 影响 |
|---|---|---|
| `invoke`、`ainvoke`、`stream`、`astream` 分别实现 ReAct 循环 | 状态推进、Tool 回填和终态逻辑重复 | 修改一条路径时容易造成四种入口行为漂移 |
| Agent Run 没有最大模型 Step 限制 | 模型持续调用 Tool 时可能无限运行 | 无法提供确定性的运行上限 |
| `AgentErrorEvent` 只表达 Agent 致命失败 | 其他组件和非致命错误没有统一观察方式 | 产品层需要理解多种错误载体 |
| Blackboard 历史持续增长 | 长对话最终可能超过模型上下文窗口 | 后续请求无法继续 |
| `ImagePart` 只保存 URL | 本地文件依赖原始路径或需要调用方自行转换 | 原文件移动后引用失效，产品层需要了解 Provider 协议 |

### 1.3 已有基础

- `TaskChannel` 已记录当前 Step、取消状态和协议完整的历史检查点；
- `Usage` 已统一表达模型单次调用的输入与输出 Token；
- Blackboard 已拥有当前 Session 的有效对话历史；
- Hook 与 Trace 已覆盖 Agent、LLM、Tool、Plugin 和 Event；
- `DataPathResolver` 已创建 Session `assets/` 目录；
- Persistence 设计已约定本地图片复制到 `assets/`，Trace 不保存二进制内容；
- Provider Adapter 已负责 OpenAI 与 Anthropic 的消息协议差异。

## 2. 方案对比与取舍

| 决策点 | 采用方案 | 未采用方案 | 取舍理由 |
|---|---|---|---|
| ReAct 去重 | 提取私有公共流程，保留四个公开入口 | 建立新的通用状态机或执行框架 | 当前问题是重复代码，不需要重新设计 Kernel |
| 无限运行保护 | Harness 在第 257 个 Step 开始前截停 | 启发式循环检测、Token/金额预算、统一 Run 总超时 | 256 Step 已提供简单确定的硬上限，其他能力暂无真实需求 |
| 错误传播 | 一个 `TaskErrorEvent`，用 `fatal` 区分是否终止 | 每个组件定义独立 Error Event，或把所有错误都视为 Task 失败 | 用户可以观察所有错误，同时不让可恢复错误改变任务终态 |
| Compact 触发 | 使用上一轮最后一次模型调用返回的 Usage | 新增 tokenizer、精确预算器、多级摘要 | 复用已有事实，满足 85% 阈值判断即可 |
| 本地图片 | 导入现有 Session `assets/`，Context 保存相对引用 | 长期保存外部绝对路径、Provider `file_id` 或图片 Base64 | 本地引用稳定且由用户持有，不绑定原路径或模型厂商 |

## 3. 目标与非目标

### 3.1 目标

- 四个 ReAct 入口共享相同的状态推进与结果处理规则；
- 一次 Agent Run 最多执行 256 个模型 Step，超过后确定性结束；
- 致命与非致命错误均可通过同一种 Event 被产品层和其他订阅者观察；
- Blackboard 历史接近模型上下文上限时自动替换为一条摘要；
- 本地图片进入 Context 后不再依赖用户原始文件的位置；
- 所有改造保持 ReActAgent 无状态，并保持 Plugin Runtime 与 EventBus 通用。

### 3.2 非目标

本阶段不实现：

- Memory、长期用户理解和个人世界模型；
- Knowledge、RAG 或知识库 Plugin；
- Session 列表、业务对话持久化、恢复和切换；
- TUI、GUI、WebUI 的图片选择、粘贴、预览或会话管理；
- Observer、Connector、计划任务和后台认知；
- 多 Agent 或 Subagent 调度；
- Agent Run 总超时、Token/金额预算和启发式循环检测；
- 完整权限审批、全局 Tool 资源调度或通用沙箱；
- OpenAI Chat Completions 到 Responses API 的迁移。

## 4. 方案

### 4.1 整体结构

```text
产品输入
  │
  ├─ 文本
  └─ 本地图片路径
         │
         ▼
UserInputPlugin + PersistenceSession
  ├─ 将图片复制到 Session assets/
  └─ 生成只含稳定引用的 ImagePart
         │
         ▼
BlackboardPlugin
  ├─ 维护当前有效历史与上一轮上下文 Token
  ├─ 每轮开始检查 85% 阈值
  ├─ 必要时调用 HistoryCompactor
  └─ 发布压缩后历史 + 当前输入
         │
         ▼
AgentPlugin / Harness
  ├─ 创建 Run Control
  ├─ 在新 Step 前检查 max_steps=256
  └─ 将异常转换为 TaskErrorEvent
         │
         ▼
ReActAgent
  ├─ 四个入口复用共同处理逻辑
  ├─ 调用 LLM
  └─ 执行并回填 Tool Result
         │
         ▼
Provider Adapter
  ├─ URL 图片直接转换
  └─ Asset 引用解析后转换为厂商协议
```

职责保持如下：

| 组件 | 本阶段职责 | 明确不负责 |
|---|---|---|
| ReActAgent | 模型决策、Tool 调用、结果回填和共享流程 | Session、文件存储、Compact 触发、产品错误展示 |
| AgentPlugin / TaskChannel | Harness 限制、Run 终态和错误边界 | 解释业务错误内容 |
| BlackboardPlugin | 当前有效历史、Compact 触发和历史替换 | 原始业务会话持久化、图片文件读写 |
| PersistenceSession | Session Asset 导入与安全解析 | Provider 协议转换、图片展示 |
| Provider Adapter | 将统一 Message 和 ImagePart 转换为厂商请求 | 决定 Session 目录和产品生命周期 |
| EventBus | 按来源 Plugin 路由 Error Event | 根据 `fatal` 修改 Task 状态 |
| Hook / Trace | 保存完整技术证据和异常堆栈 | 控制主流程 |

### 4.2 ReAct 共同流程提取

公开接口保持不变：

```python
invoke(...) -> AgentResponse
ainvoke(...) -> AgentResponse
stream(...) -> Iterator[Event]
astream(...) -> AsyncIterator[Event]
```

本次只提取已经重复出现的私有逻辑：

- 构建初始 Message 列表和 Task 消息起点；
- 获取本次 Run 的 Tool 快照和 ToolDefinition；
- 在 Step 前执行 Harness 检查、取消检查和运行中 Context 注入；
- 接收完整 LLMResponse，累计总 Usage 并保存最后一次调用 Usage；
- 判断最终回答或 ToolCall；
- 执行 Tool Batch，并按模型请求顺序写回 Tool Result；
- 更新协议完整的历史检查点；
- 构造 AgentResponse 或流式终态 Event。

同步与异步 I/O 仍然由各自的小型驱动负责；流式入口仍在原有位置发送文本和 Tool 生命周期
Event。不为消除代码行数而引入反射、动态分派或新的公共状态机。允许使用私有运行数据载体
保存 `messages`、`steps`、`usage`、`last_usage` 和 `reasoning_parts`，但该类型不进入公共接口。
每次完整模型响应后，ReActAgent 将协议完整消息与对应 `last_usage` 一起写入 Run Control 检查点。

### 4.3 最大 Step

模型配置之外新增独立的 Agent 运行配置：

```python
class AgentSettings(BaseModel):
    max_steps: int = Field(default=256, ge=1)
```

`ConfigModel.agent` 持有 `AgentSettings`。`TaskChannelRegistry` 在 Runtime 装配时接收该值，并在
`UserInputPlugin` 创建 TaskChannel 时为当前 Task 固化快照。一次 Task 已被接受后修改配置，不影响
当前 Run，只影响之后创建的 TaskChannel。

`max_steps` 表示一次 Agent Run 允许发起的最大模型调用次数，而不是 ToolCall 数量。Step 从 1
开始：

- Step 1 到 Step 256 可以正常执行；
- Step 256 返回最终回答时，Run 正常完成；
- Step 256 返回 ToolCall 时，已启动的完整 Tool Batch 仍按现有规则完成并保存检查点；
- 准备进入 Step 257 时，Harness 抛出 `MaxStepsExceededError`；
- 不再发起新的模型调用或 Tool；
- 最近一次协议完整检查点保持为最终可用历史。

检查通过现有 `AgentRunControl` 在 `_prepare_step` 对应的稳定边界执行。`ReActAgent` 不读取全局
Settings，也不自行判断业务终止；它只调用 Run Control 提供的检查接口。

未传入 Run Control 的独立 ReActAgent 调用保持当前行为，不隐式读取应用配置。

### 4.4 统一 Task 错误 Event

新增统一的可序列化错误事件，并替代当前只表达 Agent 致命失败的 `AgentErrorEvent`。由于
AgentPlugin、BlackboardPlugin、UserInputPlugin 和其他 Plugin 都可能发布该事件，类型放在编排层
通用 `agent_orchestration/events/task_error.py`，不放在 Agent capability 中：

```python
@dataclass(frozen=True, kw_only=True)
class TaskErrorEvent(Event):
    fatal: bool
    code: str
    error_type: str
    error_message: str
    step: int | None = None
    run_id: str | None = None
    task_messages: tuple[Message, ...] = ()
    last_usage: Usage | None = None
```

字段语义：

| 字段 | 含义 |
|---|---|
| `fatal` | 是否导致当前 Task 无法继续 |
| `code` | 稳定的程序判断值，例如 `max_steps_exceeded` |
| `error_type` | 原始异常类型或领域错误类型 |
| `error_message` | 可安全传给功能消费者的错误摘要 |
| `step` | 错误发生时的模型 Step；Run 启动前可以为空 |
| `run_id` | 所属 Agent Run；Compact 等 Run 前错误可以为空 |
| `task_messages` | 可提交的协议完整 Task 检查点；通常为空，仅受控截停等明确场景携带 |
| `last_usage` | 上述检查点最后一次模型调用的 Usage；没有可提交检查点时为空 |

来源 Plugin 由 EventBus 的 `source_plugin_id` 表达，不在 Event 中重复保存。Python Exception 对象和
完整堆栈不能进入 Event；它们继续通过 Hook 写入 Trace。`run_id` 仍保留在 Event 中，因为
OutputBridge 对外暴露的是 `(source_plugin_id, event)`，不会暴露 EventBus 内部的
`PublishedEvent.hook_run_id`。

错误处理遵循三条独立规则：

1. 所有 `TaskErrorEvent` 都可以被 OutputBridge、产品 UI 或其他显式订阅者观察；
2. 只有 `fatal=True` 才由 Task 生命周期所有者将 Task 收束为 `failed`；
3. 同一个失败只由最靠近故障且拥有该阶段生命周期的 Plugin 转换并发布一次，上层消费已有
   Event，不再次包装发布。

EventBus 只路由，不解释 `fatal`。UserInputPlugin、AgentPlugin 和 BlackboardPlugin 仍各自处理
自己拥有的状态，不能建立一个新的全局错误控制器。具体终态衔接为：

- Agent Run 内错误由 AgentPlugin 收束 Run，并发布 TaskErrorEvent；
- Compact 错误由 BlackboardPlugin 发布，且不再发布 Context Ready；
- 图片导入错误由 UserInputPlugin 在发布 UserInputEvent 前处理；
- UserInputPlugin 消费 AgentPlugin 或 BlackboardPlugin 的致命 TaskErrorEvent，结束当前输入并
  发布唯一的 `InputFinishedEvent(status="failed")`；
- 非致命 TaskErrorEvent 只用于观察，不触发上述终态转换。

因为 Plugin Runtime 按“来源 Plugin + Event 类型”建立订阅，同一个 Plugin 同时声明发布和消费
TaskErrorEvent 时可能形成自路由。BlackboardPlugin 和 UserInputPlugin 必须在 `accepts_event()` 中
限制允许的来源：UserInputPlugin 只接受 AgentPlugin 和 BlackboardPlugin 的错误，BlackboardPlugin
拒绝自身全部 Event，并只接受 AgentPlugin 的错误。发布者直接更新自身状态，不依赖消费自己刚
发布的错误 Event。

ReActAgent 不再定义或构造 AgentErrorEvent。模型请求、Step 上限和其他不可恢复异常保持 Python
异常语义，穿过 ObservableAgent 以便 Hook/Trace 记录完整异常证据，再由 AgentPlugin 转换为唯一的
TaskErrorEvent。Tool 普通失败不是异常终止：AgentPlugin 在转发
`AgentToolCompletedEvent(result.success=False)` 后额外发布一个非致命 TaskErrorEvent。
BlackboardPlugin 收到 `ContextContributionEvent(status="failed")` 时同样发布一次非致命
`context_provider_failed`，同时继续使用其余 Context 组装 Prompt。

典型错误映射：

| 场景 | `code` | `fatal` | 主流程行为 |
|---|---|---:|---|
| 超过 256 Step | `max_steps_exceeded` | `True` | Agent Run 失败，保留最后检查点 |
| Compact 调用失败 | `compact_failed` | `True` | 保留旧历史，本轮不启动 ReAct |
| 单次文本明显超过窗口 | `input_too_long` | `True` | 本轮不启动 ReAct |
| 本地图片导入失败 | `image_import_failed` | `True` | 不发布 UserInputEvent，本轮结束 |
| Session Asset 缺失或不可读 | `image_asset_unavailable` | `True` | 模型调用前结束 |
| Agent Run 未分类异常 | `agent_run_failed` | `True` | Agent Run 失败 |
| Tool 执行失败 | `tool_execution_failed` | `False` | ToolExecutionResult 仍回填模型，Run 继续 |
| 可选 Context 来源失败 | `context_provider_failed` | `False` | 使用其余 Context 继续 |
| Provider 未返回 Usage | `usage_unavailable` | `False` | 本轮继续，本次不更新 Compact 标记 |

Tool 失败仍必须保留 `AgentToolCompletedEvent(result.success=False)`，因为该 Event 描述 Tool 生命周期，
`ToolExecutionResult` 还要回填模型。额外的 `TaskErrorEvent(fatal=False)` 用于统一错误观察。产品消费者
应选择一种呈现方式，例如把错误内联在对应 Tool 卡片中，不能把两个 Event 重复渲染为两条错误。

`max_steps_exceeded` 是受控截停。该错误携带 Step 256 后最近的协议完整 `task_messages` 与
`last_usage`，Blackboard 按正常检查点提交它们，使下一轮可以从截停位置继续。其他意外致命失败
默认不携带可提交检查点，保持现有“失败 Task 不写入跨轮历史”的语义。
最后一个 Tool Batch 的 ToolResult 尚未再次提交给模型，因此它不包含在 `last_usage` 中；第一阶段
接受这一简单口径，不为该边缘情况增加 tokenizer 或额外模型调用。

取消不是错误，继续使用 `AgentCancelledEvent` 和 `InputFinishedEvent(status="cancelled")`；
`AgentCancelledEvent` 增加可选 `last_usage`，与它携带的安全检查点对应。Runtime
启动失败、Manifest 校验失败和 Plugin 装载失败发生在 Task 之外，继续使用启动异常和诊断，不转换为
TaskErrorEvent。

### 4.5 Blackboard Compact

#### 触发时机

Compact 在 Blackboard 收到新一轮 `UserInputEvent` 后、发布 `BlackboardContextReadyEvent` 前检查
一次。检查对象仅为此前已经提交的 Blackboard 历史，不包含本轮用户输入。ReActAgent 不知道
Compact 是否发生。

模型配置增加上下文窗口：

```python
class LLMConfig(BaseModel):
    model_name: str
    context_window: int = Field(gt=0)
    max_tokens: int
    temperature: float
    default_think_level: ThinkMode
```

仓库当前 `deepseek-v4-pro` 和 `deepseek-v4-flash` 配置按官方 1M Context Length 填写
`context_window=1000000`；该值仍以显式配置为事实源，不从模型名推导。

触发阈值固定为 85%，第一阶段不增加可配置的预算策略：

```text
blackboard_context_tokens >= context_window * 0.85
```

#### Token 记录

不引入 tokenizer。`AgentResponse.usage` 继续表示 Run 内所有模型调用的总消耗，同时新增
`AgentResponse.last_usage` 表示最后一次模型调用 Usage。Blackboard 在成功提交一轮历史时，按
以下值更新当前上下文 Token 标记：

```text
last_usage.input_tokens + last_usage.output_tokens
```

这里有意复用 Provider 返回的实际 Usage，并允许包含 System Prompt、ToolDefinition 等固定开销。
该值用于简单、偏保守地判断下一轮是否接近窗口，不作为精确计费或 Token 预算。不得把所有
Step 的 `input_tokens` 累加后当成上下文长度，因为相同历史会在多个 Step 中被重复计算。

Provider 未返回 Usage 时：

- 正常提交本轮历史；
- 不把缺失 Usage 记为 0；
- 保留此前的上下文 Token 标记；
- 发布 `usage_unavailable` 非致命错误；
- 本次不根据未知数据触发 Compact。

正常完成时从 `AgentCompletedEvent.response.last_usage` 取得标记；受控的最大 Step 截停从
TaskErrorEvent 取得；取消后若 Blackboard 提交了 `AgentCancelledEvent.task_messages`，取消事件也需要
携带与该检查点对应的可选 `last_usage`。三条路径遵循同一规则：有值则更新，无值则保留旧标记并
发布 `usage_unavailable`。

#### 压缩与替换

Blackboard 使用普通内部组件 `HistoryCompactor`，通过统一 `BaseLLM` 调用当前 thinking 模型。它不是
Plugin，不注册 Capability，也不进入 ReAct Tool 集合。其模型适配器在 Runtime 生命周期内复用，
不在每轮压缩时重新创建。

Compact 输入由固定 System Prompt 和当前全部旧历史组成，要求模型：

- 保留用户目标、约束、决定、重要事实、未完成事项和必要结果；
- 将图片中已经参与对话的重要信息转写成文字；
- 不保留只在旧历史中有意义的 `[image#N]` 展示编号；
- 不生成新的事实或推断。

第一版固定 System Prompt 为：

```text
你负责压缩一段对话历史，以便另一个模型只阅读摘要也能继续当前工作。
只输出一份自包含摘要，不要回答用户，不要继续执行任务。
保留用户目标、明确约束、已作决定、已验证事实、重要路径与接口、关键错误、当前状态和未完成事项。
对已经从图片中得到的重要信息只保留文字事实，不保留 [image#N] 等展示编号。
保留后续操作需要精确复用的名称、数值和字符串。
不要添加原历史中不存在的事实、推断或建议。
```

Compact 成功后，Blackboard 原子地把全部旧历史替换为一条 User Message：

```text
<conversation_summary>
...
</conversation_summary>
```

Compact 输出自身的 `output_tokens` 成为新的上下文 Token 标记，因为此时 Blackboard 中的旧历史
只剩摘要；下一次正常 Run 成功后再用该 Run 的 `last_usage` 更新标记。原始历史、Compact 输入、
输出、Usage 和耗时已经由现有 LLM Hook 保留在 Trace 中；Blackboard 不同时保存原历史和摘要
两份有效上下文。

替换成功后，Blackboard 在 Context Ready 前发布
`BlackboardCompactedEvent(before_tokens, after_tokens)`。该 Event 不携带摘要正文，只为后续产品展示
和业务观察提供稳定事实；它不是 Task 终态。

Compact 失败时：

- 不修改 Blackboard 历史和 Token 标记；
- Blackboard 先将当前 Task 状态标记为不再等待 Agent，再发布错误，避免 InputFinished 到达后遗留
  未完成状态；
- 发布 `TaskErrorEvent(fatal=True, code="compact_failed")`；
- 当前 Task 进入 failed，本轮 ReAct 不启动；
- 不使用接近上限的旧历史继续冒险请求模型。

#### 单次超长输入

本轮用户输入不参与 Compact。第一阶段只做一个保守的极端值检查：当输入文本的 UTF-8 字节数
不小于 `context_window * 4` 时，直接返回 `input_too_long`。该检查只用于挡住明显不可能成功的
输入，不是 Token 估算；不裁剪用户原文，也不保证提前识别所有超长输入。其余情况由 Provider
正常校验，Provider 拒绝时按 `model_request_failed` 处理。

该检查与 Compact 一样位于 Blackboard 发布 Context Ready 之前；命中时使用相同的 Task 状态
收束方式，不发布 Context Ready，也不创建 Agent Run。

#### Blackboard 状态

Blackboard Session 状态除 `messages` 外增加当前上下文 Token 标记。Compact 替换和新一轮成功
提交必须一起更新内存状态；新增字段为可选值，因此 Session State 继续使用 v1，恢复不含该字段的
旧状态时保留 messages 并将 Token 标记初始化为 unknown。现有 Plugin 状态快照只保存恢复接缝，不在本阶段扩展为业务 Session
列表、跨进程产品恢复或对话切换。

`HistoryCompactor` 由 BlackboardPlugin 持有。Blackboard Factory 根据当前 ConfigModel 创建一份
专用于 Compact 的 thinking BaseLLM，并使用现有 ObservableLLM 接入 Hook；该适配器在 Plugin
生命周期内复用，并在 BlackboardPlugin 停止时关闭。第一阶段不新增共享模型池，也不反向依赖
AgentPlugin 的私有 AgentFactory。

### 4.6 本地图片稳定引用

#### 统一类型

`ImagePart` 改为三个扁平字段：

```python
ImageSourceType = Literal["url", "asset"]

@dataclass(frozen=True)
class ImagePart:
    source: str
    source_type: ImageSourceType = "url"
    media_type: str | None = None
```

现有 `ImagePart("https://...")`、`ImagePart(url=...)` 和
`ImagePart(url, media_type)` 调用仍表示 URL，以减少已有调用方迁移成本。

应用提交边界额外接受 `str | Path` 形式的本地图片路径；`ImagePart` 继续只表达已经标准化的 URL
或 Session Asset，不增加只在输入瞬间有意义的 `file` 状态。原始路径只允许短暂存在于
UserInputPlugin 的待处理输入中，不进入 UserInputEvent、Blackboard、Agent Context 或 Trace。

```python
AgentRuntimeService.submit(
    prompt: str,
    input_images: list[ImagePart | str | Path] | None = None,
) -> InputAccepted
```

#### 导入与存储

不新增顶层 AssetStore。现有 `PersistenceSession` 增加小型资源能力：

```python
import_image(path: str | Path) -> ImagePart
resolve_image(image: ImagePart) -> Path
```

导入规则：

1. 校验源文件存在、可读，并将内容限制为 JPEG、PNG、GIF 或 WebP；
2. 读取内容并计算 SHA-256；
3. 根据识别出的媒体类型使用规范扩展名，复制到当前 Session 的 `assets/<sha256>.<ext>`；
4. 同一 Session 内相同内容复用已有文件；
5. 返回 `source_type="asset"`、`source="assets/<sha256>.<ext>"` 的 ImagePart；
6. Context 和 Trace 不保存用户原始绝对路径或图片二进制。

`resolve_image` 只接受当前 Session `assets/` 下的安全相对引用，拒绝绝对路径和目录逃逸，并在
文件不存在或不可读时抛出明确异常。第一阶段不实现自动删除、引用计数、跨 Session 去重或云同步。

UserInputPlugin 已经依赖 Persistence Runtime 与 Session，因此由它在发布 UserInputEvent 前完成导入。
导入成功后再发布只含标准化 ImagePart 的 UserInputEvent；导入失败则发布致命 TaskErrorEvent 和
failed InputFinishedEvent，不启动 Blackboard/Agent。Blackboard 和 ReActAgent 只传递结构化
ImagePart，不复制、不解析文件。

#### Provider 转换

Provider Adapter 通过构造时注入的简单解析函数取得 Asset 的本地 Path，不直接依赖
PersistencePlugin 或 Session 目录结构：

```python
resolve_image(ImagePart) -> Path
```

转换规则：

| Provider 路径 | URL 图片 | Session Asset |
|---|---|---|
| OpenAI Chat Completions | `image_url` | 读取本地文件并转换为 Data URL |
| Anthropic Messages | URL source | 读取本地文件并转换为 Base64 source |

AgentPlugin Manifest 显式依赖现有 `persistence/runtime` 和 `persistence/session` Capability；其 Factory
据此构造 PersistenceSession，并把 session-bound `resolve_image` callable 注入 LLMFactory/Provider
Adapter。这是装配依赖，不允许 model_provider 导入 PersistencePlugin。独立 AgentFactory 不注入
resolver 时仍可处理纯文本和 URL 图片，遇到 Asset 引用则抛出明确的图片解析错误。

OpenAI Files API 的 `file_id` 可以作为未来 Provider 缓存，但不能成为 Blackboard 的事实源。当前
不为了本地图片迁移到 Responses API。

`[image#1]` 只是产品层的展示别名。TUI 可以显示文本占位，WebUI 可以显示缩略图，但提交给
Agent 的始终是 Prompt 与结构化 `ImagePart`，展示编号不承担文件寻址职责。

### 4.7 兼容性策略

- BaseAgent 四个公开入口和扁平参数保持不变；
- `ImagePart` 的第一个位置参数继续接受现有 URL；
- URL 图片无需 PersistenceSession，行为保持不变；
- `TaskErrorEvent` 替换 `AgentErrorEvent` 时同步迁移 AgentPlugin、UserInputPlugin、Blackboard、
  OutputBridge、TUI Projector、Replay 和测试，不长期保留两套错误事件；
- 各 Plugin Manifest 按真实发布者和消费者声明通用 TaskErrorEvent，EventBus 仍然只按来源 Plugin
  建立路由；
- TUI 为 Agent、Blackboard 和 UserInput 三个来源注册显式 Projector，共享错误字段映射但继续按来源
  投影；Tool 失败不重复显示，Compact 成功事件在当前 TUI 中识别但不展示；
- `AgentCompletedEvent`、`AgentCancelledEvent` 和 `InputFinishedEvent` 的现有终态语义不变；
- ToolExecutionResult 继续进入模型上下文，不被 Error Event 替代；
- Compact 只修改 Blackboard 当前有效历史，不修改 ReActAgent 的调用协议；
- Blackboard Session State v1 向后兼容不含 Token 标记的旧快照；
- 所有新增 Settings 在 Run 或当前输入开始时形成稳定快照，中途修改只影响下一次边界。

## 5. 自测与验收

### 5.1 ReAct

- `invoke`、`ainvoke`、`stream`、`astream` 对相同模型脚本产生一致的 Message、Tool 顺序、
  Usage、Step 和最终结果；
- 并行安全 Tool 仍并发执行，副作用 Tool 仍形成顺序屏障；
- 运行中 Context、取消、完整 Tool Batch 和历史检查点语义不回归；
- 流式入口仍按原顺序发送文本、Tool 开始、Tool 完成和唯一终态。

### 5.2 Harness 与错误

- Step 256 可以正常完成；只有准备进入 Step 257 时触发 `max_steps_exceeded`；
- 截停后不再启动模型调用或 Tool，历史停在最后完整检查点；
- max_steps 截停携带安全检查点和 last_usage，Blackboard 提交后下一轮可以继续；
- 致命错误只产生一个 TaskErrorEvent 和一个 failed InputFinishedEvent；
- 非致命错误可以被 OutputBridge 观察，但不改变 TaskChannel 状态；
- Tool 失败既回填模型，也能被统一错误订阅观察，产品投影不会重复显示；
- Cancel 与致命错误竞争时仍只有一个 Agent/Task 终态。

### 5.3 Compact

- 未达到 85% 时不调用 Compactor；
- 达到阈值时只压缩旧历史，不包含当前用户输入；
- 成功后 Blackboard 只保留一条摘要，下一次 ReAct 只能看到摘要和当前输入；
- Compact 后更新上下文 Token 标记；
- Compact 失败时历史不变、本轮不启动 ReAct；
- Usage 缺失时产生非致命错误，不把未知值记为 0；
- Compact 输入和结果可以在 Trace 中定位。
- 成功时只发布一个不含摘要正文的 BlackboardCompactedEvent，并先于 Context Ready 到达；

### 5.4 本地图片

- 导入后移动或删除原始文件，Session Asset 仍可使用；
- 同内容重复导入不会创建重复文件；
- 相对路径逃逸、缺失文件、不可读文件和不支持格式被拒绝；
- Context 和 Trace 不包含原始绝对路径或图片二进制；
- OpenAI Chat Completions 能收到 Data URL；
- Anthropic Messages 能收到 SDK 支持的本地图片 Source；
- URL 图片和现有纯文本调用保持兼容。

### 5.5 验证顺序

```text
受影响的最小测试文件
→ apps/agent/test/agent_orchestration 对应目录
→ apps/agent/test 全量测试
→ compileall
→ git diff --check
→ 有可用凭据时执行真实模型 Tool、Compact 和图片冒烟
```

## 6. 里程碑

按依赖顺序拆为四个实施里程碑：

| 顺序 | 里程碑 | 完成标志 |
|---:|---|---|
| 1 | ReAct 去重与 256 Step Harness | 四入口共享处理规则，Step 257 确定性截停 |
| 2 | 通用 TaskErrorEvent | 致命与非致命错误均可观察，终态保持唯一 |
| 3 | Blackboard Compact | 85% 阈值、摘要替换、失败保留和 Trace 闭环 |
| 4 | 本地图片稳定引用 | Session Asset 导入、统一引用和双 Provider 转换闭环 |

每个里程碑将实现、测试和对应文档更新放在同一个逻辑变更中。全部完成后再开始 Session、恢复、
会话切换和 UI 展示等产品化设计。

## 7. 风险

| 风险 | 影响 | 规避方式 |
|---|---|---|
| ReAct 去重改变事件顺序 | TUI、取消或历史提交行为回归 | 先固化四入口现有测试，再逐项提取纯公共逻辑 |
| 同一错误被 Tool Event 和 Error Event 重复展示 | 用户看到重复错误 | Tool Event 负责生命周期，Error Event 负责统一观察，产品投影只选择一种呈现 |
| Provider Usage 缺失或口径不同 | Compact 触发不稳定 | 未知不记零、不估算；记录非致命错误并等待后续有效 Usage |
| 85% 判断包含固定 Prompt/Tool 开销 | Compact 比纯历史计算略早 | 接受偏保守结果，不为精确率引入 tokenizer 和预算系统 |
| Compact 摘要遗漏历史信息 | 后续对话理解下降 | 固定 Prompt 明确保留项，原始轨迹留在 Trace，失败时不替换 |
| Session Asset 文件缺失 | 历史图片无法发送给模型 | 调用前安全解析并发布明确错误，不回退到已失效原始路径 |
| Provider 图片能力不同 | 上层出现厂商分支 | 所有协议转换留在 Provider Adapter，上层只使用 ImagePart |

## 8. 完成标准

本阶段完成时应满足：

- Agent 能通过四个公开入口一致地完成多步 ReAct 与 Tool 执行；
- 任一 Runtime Agent Run 都不会执行超过 256 个模型 Step；
- 产品消费者可以通过一个 Error Event 观察所有 Task 内错误，并可靠区分是否致命；
- 长对话在下一轮开始时可以按 85% 阈值压缩，且 Blackboard 只向模型提供压缩后的历史；
- 本地图片在进入 Agent 后不再依赖外部原始路径；
- Memory、Knowledge、Session 产品能力和 UI 交互没有被提前引入；
- 现有 Plugin、Skill、取消、Tool 与 Hook 行为通过回归验证。
