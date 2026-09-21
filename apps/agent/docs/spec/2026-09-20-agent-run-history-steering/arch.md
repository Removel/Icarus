# Agent Run 完整历史与运行中纠偏设计

## 文档定位

本文定义 Icarus Agent 在同一个 Session 内保存和重放完整 Agent Run 消息历史，以及在不停止
当前 Run 的情况下接收用户纠偏的行为。

本文恢复 `apps/agent/docs/spec/2026-08-22-agent-run-intervention/arch.md` 已确定的完整消息历史方向，并替代
`apps/agent/docs/spec/2026-08-15-plugin-eventbus-blackboard/arch.md` 中“跨 Run 只保存原始 User 与最终 Assistant”的
Product Conversation 投影方案。

本阶段只修改 `apps/agent`。Gateway、TUI、Provider Adapter、Tool 实现和数据库结构不在本阶段。

## 已确认结论

- 不新增 Turn 运行实体；现有 Task 就表示一次用户任务。
- 当前保持 `1 Task = 1 Agent Run`，但 Task 与 Run 的身份和职责不合并。
- Blackboard 是 Session 跨 Run 消息历史的唯一所有者。
- ReActAgent 在单次调用中持有 Blackboard 历史的工作副本，并追加当前 Run 消息。
- Agent Run 正常完成后，将完整 `task_messages` 提交给 Blackboard。
- 用户停止 Run 时，提交最近一个协议完整的安全前缀，并使用明确的 Assistant 中断消息闭合。
- 运行中用户纠偏属于当前 Task 和 Run，不创建新 Task，也不停止当前 Run。
- TUI 的草稿、输入队列和 `Ctrl+C` 优先级保持不变；本阶段不修改 TUI。

## 层级与所有权

```text
AgentRuntime
└── SessionRuntime
    └── Task
        └── Agent Run
            └── Step
                └── Tool Call
```

| 对象 | 职责 | 生命周期 |
| --- | --- | --- |
| AgentRuntime | 管理设备上的 SessionRuntime，并路由应用操作 | 进程级 |
| SessionRuntime | 组装并隔离一个 Session 的 Plugin、EventBus 和资源 | Session 加载期间 |
| Task | 一次用户任务，负责排队、状态和外部操作目标 | 从接受到终态 |
| Agent Run | Task 对应的一次 Kernel 执行 | 当前 Task 执行期间 |
| Step | 一次模型决策及其后续 Tool Batch | Agent Run 内 |
| Tool Call | 一个具体工具调用 | Step 内 |

`turn` 不是新的运行层级、Registry 或持久化实体。文档中如使用“一轮”，只表示一个 Task 的
自然语言含义。

## 单一完整历史模型

### 历史所有权

系统只维护一份权威的跨 Run 消息历史：

```text
Blackboard._messages
└── 当前 Session 已提交的完整、可重放 Message 序列
```

其他消息集合不是第二份历史：

| 数据 | 定位 |
| --- | --- |
| `ReActAgent._RunState.messages` | 当前 Run 使用的工作副本 |
| `TaskChannel.history_checkpoint` | 当前 Run 最近的协议完整安全前缀 |
| `trace.jsonl` | 诊断与审计记录，不作为模型恢复来源 |
| RuntimeUpdate | UI 和应用层事件投影，不作为模型恢复来源 |

ReActAgent 不直接修改 Blackboard。Agent Run 到达终态后，Blackboard 根据明确的终态事件提交
本 Run 增量。

### 完整历史包含什么

完整历史保存 Provider 无关的内部 `Message` 语义：

- 当前用户请求；
- 每个已经完成的 Assistant Message；
- Assistant 发起的 Tool Call；
- 与 Tool Call 一一匹配、协议完整的模型可见 Tool Result；超预算正文以内联 Preview 与 Session
  Tool Result 文件路径表达，原始长文本不重复内联到 Blackboard；
- 已经实际应用到模型请求的 Plugin Runtime Context；
- 已经实际应用到模型请求的用户 Steer；
- 最终 Assistant Message；
- 取消或安全截停时生成的明确 Assistant 终止消息。

完整历史不包含：

- System Prompt；System Prompt 在每次 Run 组装请求时单独加入；
- Provider 隐藏的 chain-of-thought 或 `LLMResponse.reasoning`；
- 尚未完成的流式 Assistant 增量；
- 未闭合 Tool Batch；
- 已接受但尚未应用的 Runtime Context 或 Steer；
- 仅用于 UI、恢复或内部控制的临时消息。

因此，“完整”表示完整的可重放协议消息链，不表示保存隐藏推理或不确定的执行片段。

## 正常 Agent Run

### Run 启动

Blackboard 为当前 Run 生成初始快照：

```text
stable System Prompt
+ Blackboard 完整 Session History
+ current User Prompt
```

ReActAgent 基于该快照创建当前 Run 的局部消息列表，并记录
`task_message_start`。`task_message_start` 之前是已提交的 Session History，之后是当前 Run 的消息。

### Run 执行

ReActAgent 在局部列表中按实际执行顺序追加消息：

```text
user(current request)
assistant(optional visible content, tool_calls=[call-a, call-b])
tool(result for call-a)
tool(result for call-b)
assistant(optional visible content, tool_calls=[call-c])
tool(result for call-c)
user(applied steer)
assistant(final)
```

并发 Tool 可以乱序完成，但写回消息列表时必须保持原 Tool Call 顺序。本文把一条 Assistant
Message 声明的全部 `tool_calls` 及其结果称为一个 Tool Group；它可能被 ToolExecutor 拆成多个执行
批次，但只有整个 Tool Group 的每个 Tool Call 都有且只有一个匹配 `tool_call_id` 的终态 Tool Result
时，才形成可提交检查点。

普通的、没有 `tool_calls` 的 Assistant Message 不能直接跟 Tool Result。它默认是 Final Assistant；
只有在完成竞争中已经接受了 Runtime Context 或 Steer 时，才可以形成
`assistant(draft) -> user(correction/context) -> assistant(...)` 并继续下一个 Step。

内部消息序列遵循下面的语法，而不是简单要求每条消息机械地 User/Assistant 交替：

```text
system                                      # 整个请求恰好一次
(user                                       # 当前任务或安全点输入
  -> assistant(tool_calls)
  -> tool(result-1) ... tool(result-N)       # 必须完整闭合并保持调用顺序
)*
user -> assistant(final)                    # 正常结束
```

在 Tool Group 闭合后，下一项可以是新的 Assistant Step，也可以是运行中输入产生的 User；在普通
Assistant 后，只有已接受的运行中输入才能开启后续 Step。任何情况下，Tool Result 都不能跟在没有
声明对应 `tool_calls` 的 Assistant 后面。Provider Adapter 可以把内部连续 Tool Message 转成厂商需要的
内容块，但不能修复或改变 Tool 配对语义。

### 正常完成提交

`AgentCompletedEvent.response.task_messages` 是当前 Run 的完整增量。Blackboard 将其整体追加到
`_messages`，不重新拼接原始 User 和最终 Assistant，也不重复写入旧 Session History 或 System Prompt。

如果非 ReActAgent 的兼容调用没有返回 `task_messages`，可以用当前已组装的 `input_prompt` 与最终
Assistant 构造最小合法回退；生产 ReActAgent 路径缺少 `task_messages` 应记录非致命诊断。

## 用户停止与安全历史

### 安全检查点

TaskChannel 只在协议完整边界保存 `history_checkpoint`：

1. 第一次模型请求前，当前 User Prompt 已完整；
2. 每个完整 Tool Group 的所有 Tool Result 已按调用顺序写回后；
3. 下一次模型请求前，已接受的 Runtime Context 或 Steer 已完成注入后。

不得在 Assistant Tool Call 与对应 Tool Result 之间建立可提交检查点。

### Stop 提交规则

Run 已启动后收到 Stop：

```text
AgentCancelledEvent.task_messages
→ 读取最近安全检查点
→ 校验 Tool Call / Tool Result 完整性
→ 丢弃未完成的流式 Assistant 输出
→ 追加明确的 Assistant 中断消息
→ 提交 Blackboard 历史
```

默认中断消息为：

```text
Operation interrupted.
```

如果安全前缀末尾已经是普通 Assistant Message，则不再追加第二条 Assistant，而是在请求发送前
保证下一条用户消息可以合法衔接。停止状态由 Task 终态记录表达，不依赖模型从自然语言猜测。

具体场景如下：

| 停止位置 | 提交内容 |
| --- | --- |
| Agent Run 启动前 | 没有 Run 安全前缀，不向 Blackboard 提交伪造历史 |
| 第一次模型生成中 | 当前 User Prompt + Assistant 中断消息 |
| 完整 Tool Group 之后 | User、已闭合 Tool 链 + Assistant 中断消息 |
| Tool Group 执行中 | 回退到该 Group 之前的安全检查点，再闭合 |
| 已应用 Steer 之后 | 保留 Steer，再闭合 |
| Steer 尚未应用 | 不写入历史，记录为 `discarded_by_stop` |

已经发生的文件写入和外部副作用不回滚。未能确定完成状态的副作用不得伪造成成功 Tool Result。

### Failed Run

普通非受控失败不从临时消息猜测可提交内容。只有终态事件明确携带 `task_messages`，并且安全
检查点已经由普通 Assistant Message 闭合时才允许提交。本阶段不为 Failed Run 生成新的回复文案；
没有闭合检查点时不追加当前 Task。

本阶段继续沿用 `max_steps_exceeded` 等受控截停携带安全检查点的机制，不扩展新的失败恢复系统。

## 运行中用户纠偏

### 语义

用户 Steer 表示：

> 用户在当前 Agent Run 尚未结束时修改或补充要求；Agent 不停止当前 Run，在下一个安全边界按新
> 要求继续执行。

Steer 不创建新 Task、不创建新 Run，也不等同于 Plugin Runtime Context。

新增独立事件：

```python
@dataclass(frozen=True, kw_only=True)
class TaskSteerRequestedEvent(Event):
    content: str
```

TaskChannel 内部记录必须区分来源语义：

```text
context          Memory、Knowledge、Skill 或 Supervisor 的补充信息
user_correction  用户对当前任务的权威纠偏
```

两者可以复用同一个有序队列、锁和原子关闭机制，但不得使用同一种模型标记。

### Agent 应用入口

本阶段增加以下 Agent 应用接口：

```text
AgentRuntime.steer_task(workspace_path, session_id, task_id, content, resources, display_text)
→ 导入 resources 为当前 Session assets/ImagePart
→ SessionRuntime.steer_task(task_id, content, input_images, display_text)
→ AgentPlugin.handle_task_operation(...)
→ TaskChannel.add_steer(...)
```

Gateway 通过 `session.steer` 暴露该接口。TUI 在已有活动 Task 时把普通提交作为 Steer；空闲时仍
通过 `session.submit` 创建新 Task。文本和图片遵循同一分流规则。

### 应用边界

Steer 只在以下安全点生效：

1. 第一次 LLM Step 前；
2. 完整 Tool Batch 结束后的下一次 LLM Step 前；
3. Agent 准备正常完成时，通过原子的 `close_or_drain` 检查决定是否增加一个 Step。

Steer 不得插入：

- 正在进行的模型请求内部；
- Assistant Tool Call 与对应 Tool Result 之间；
- 同一个 Tool Batch 的多个 Tool Result 之间。

### 消息格式

第一次 LLM Step 前到达的 Steer 与当前 User Prompt 合并，避免生成连续 User Message：

```text
<runtime_context>
...Plugin 补充信息...
</runtime_context>

<user_request>
...原始用户请求...
</user_request>

<user_correction>
...运行中用户纠偏...
</user_correction>
```

进入后续 Step 的 Steer 使用独立 User Message：

```text
<user_correction>
...运行中用户纠偏...
</user_correction>
```

如果同一安全点前到达多条 Steer，按接收顺序合并在同一条 User Message 中。Plugin Runtime
Context 排在前面，用户 Steer 排在后面，保证用户纠偏是这一消息中最后生效的指令。

### 接受、应用与竞争

`steer_task()` 只同步返回请求是否被接收；`applied` 和 `discarded_by_stop` 是请求被接收后的处置
结果，不能由同步返回值提前承诺。Hook/Trace 记录完整处置；只有真正 drain 的 Steer 才发布
`user.correction` RuntimeUpdate，供 UI 展示和 Session 恢复，未应用内容不进入公共历史。

| 场景 | 同步结果 | 后续处置 |
| --- | --- | --- |
| Task 正在准备或运行 | `accepted` | 在安全点记录 `applied` 并进入完整历史 |
| Run 已原子关闭 | `already_finished` | 不接受、不记录为已应用 |
| Task 正在取消 | `already_cancelling` | 不接受 |
| 内容为空 | `invalid_content` | 不接受 |
| Stop 先于 Steer 应用 | 先前已返回 `accepted` | 记录 `discarded_by_stop`，不进入历史 |
| Steer 先完成应用 | 先前已返回 `accepted` | 保留在安全检查点，后续 Stop 不删除 |

Agent 层不把 `already_finished` 的 Steer 自动转换为新 Task。是否排队为下一条输入属于 UI 或其他
调用方策略。

图片先复用现有 `incoming -> Session assets -> ImagePart` 导入链路，再与 Steer 文本一起进入同一条
User Message。TUI 仅在 `session.steer` 返回 `accepted` 后删除自己拥有的临时图片；若返回
`already_finished`、`already_cancelling`、`not_found` 或 `not_running`，完整输入保留在本地队列，
待 Session 回到空闲后作为新 Task 提交。

## Plugin Runtime Context

`TaskContextInputEvent` 保持来源无关，用于 Plugin 提供运行中信息。它与 Steer 的共同点是都通过
TaskChannel 在安全边界进入当前 Run；区别是：

| 类型 | 权威来源 | 模型标记 | 是否是用户指令 |
| --- | --- | --- | --- |
| Runtime Context | Plugin | `<runtime_context>` | 否 |
| Steer | 用户 | `<user_correction>` | 是 |

第一次 LLM Step 前已经到达的 Runtime Context 必须合并到当前 User Prompt 的
`<user_request>` 之前，不再追加成第二条 User Message。这样既保留完整上下文，又保证当前用户请求
仍是初始消息中最后的用户指令。

运行中晚到的 Context 继续在安全边界注入。若同一边界同时存在 Context 和 Steer，则合并为一条
User Message，顺序固定为 `runtime context -> user correction`。

## 历史合法性

Blackboard 只接受满足以下条件的 Run 增量：

- 不包含 System Message；
- 第一条是当前 Task 的 User Message；
- Tool Result 只能出现在对应 Assistant Tool Call 之后；
- 每个 Tool Call 最多对应一个 Tool Result；
- 已提交的 Tool Call 必须有终态 Tool Result；
- Completed Run 和闭合后的 Cancelled Run 以非空、无 Tool Call 的 Assistant Message 结束；
- Failed Run 只有在检查点已经由普通 Assistant Message 闭合时才提交；
- 不包含未完成的流式 Assistant；
- 不包含未应用的 Runtime Context 或 Steer。

正常完成或取消提交前，Blackboard 使用一个普通内部组件校验消息序列。对当前新 Run，校验失败时
拒绝提交并发布诊断错误，不能把损坏消息写入权威历史。

旧 Session State 继续按现有 Message 格式读取，不修改原文件。发送给 Provider 前在请求副本上执行
兼容修复：

- 删除没有对应 Tool Call 的孤立 Tool Result；
- 删除或裁剪缺少终态 Tool Result 的 Tool Call；
- 删除空 Assistant；
- 合并无法恢复 Task 边界的连续普通 User Message；
- 丢弃最终仍以 User 结束的旧历史后缀，避免它与当前 User Request 竞争；
- 保持 Tool Call 与 Tool Result 的原始顺序。

旧版本已经丢弃的 Tool Call、Tool Result 和中间 Assistant 无法恢复，不伪造这些内容。从升级后完成
的第一个 Run 开始，Blackboard 持续追加完整历史。

## 持久化与恢复

Blackboard 继续通过现有 Session Plugin State 保存 `messages`，现有 Message 序列化结构已经包含
`tool_calls` 和 `tool_call_id`，本阶段不新增数据库表，也不要求迁移文件格式。

恢复时：

1. 读取 Blackboard Session State；
2. 反序列化完整 Message；
3. 保留原始持久化内容；
4. 每次构造 Provider 请求时使用校验后的副本；
5. 新 Run 终态只向 Blackboard 追加本 Run 的安全增量。

RuntimeUpdate 和 Trace 继续承担展示与诊断职责，不成为 Blackboard History 的替代来源。

## Context 预算影响

恢复完整历史后，Tool Result 会显著加快上下文增长。Tool Execution Guard 已为新增 Tool Result
提供单结果和 Batch 预算，并把超限正文外置到 Session 文件；当前 85% Compact 阈值仍只计算 Blackboard
历史粗略 Token，且没有完整覆盖 System Prompt、Tool Schema、图片和输出预留。

历史恢复本身仍不等于完整 Context 治理。后续必须继续：

1. Active Run 工作集预算；
2. 完整 Wire Request 预算；
3. 历史 Compact 的安全边界与质量治理；
4. 基于实际 Provider 请求的 Context Pressure 与 Compact。

在完整 Wire Request 预算完成前，完整历史仍可能增加 Token、延迟和成本；Tool Execution Guard 只先
约束新增 Tool Result，不替代后续的请求级治理。

## 代码改动范围

本阶段限制在：

- `agent_orchestration/plugins/blackboard/`：恢复完整 Run 提交、取消闭合和历史校验；
- `agent_orchestration/run_control/`：区分 Runtime Context 与 User Steer，记录接受和应用状态；
- `agent_orchestration/plugins/agent/`：通过应用层直接入口接收 Steer 并返回操作结果；
- `agent_orchestration/capability/react_agent.py`：在安全边界合并或追加 Steer；
- `application/session_runtime.py` 与 `application/agent_runtime.py`：提供 Agent 层 Steer API；
- 对应的 `apps/agent/test/`；
- 与本设计冲突的 Agent 架构文档。

本阶段不修改：

- Gateway RPC；
- TUI 输入与 `Ctrl+C` 状态机；
- Provider Adapter；
- Tool 实现；
- SessionStore 数据库 Schema；
- AgentRuntime、SessionRuntime、Task、Run 的层级；
- Redirect、一个 Task 多 Run、Active Run Budget 和完整 Request Assembler。

## 测试与验收

### Blackboard

- Completed Run 完整提交 User、所有 Assistant、Tool Call、Tool Result 和已应用 Steer；
- 下一 Run 收到上一 Run 的完整历史；
- 相同终态事件重复到达不会重复提交；
- Run 启动前取消不提交伪造历史；
- 运行中取消提交安全前缀并以 Assistant 中断消息闭合；
- 不完整 Tool Batch 不进入历史；
- 旧 Session 的非法消息只在请求副本上修复。

### Run Control 与 ReActAgent

- Steer 在第一次 LLM Step 前合并进当前 User Message；
- Steer 在完整 Tool Batch 后作为 User Correction 注入；
- 多条 Steer 保持 FIFO；
- Runtime Context 位于 User Correction 之前；
- 已接受 Steer 会阻止 Run 提前完成，并触发后续 LLM Step；
- Stop 丢弃未应用 Steer，但保留已应用 Steer；
- Trace 能区分 Steer 的 `accepted`、`applied` 与 `discarded_by_stop`；
- Tool Call 与 Tool Result 之间永远不插入 Steer。

### 应用接口

- AgentRuntime 和 SessionRuntime 能按 `task_id` 向活动 Run 发送 Steer；
- 不存在、已完成和正在取消的 Task 返回明确结果；
- 本阶段没有 Gateway 或 TUI 依赖新增接口。

### 验证顺序

```text
Blackboard 定向测试
→ Run Control 与 ReActAgent 定向测试
→ SessionRuntime / AgentRuntime 定向测试
→ make test-agent
→ git diff --check
```

## 后续工作

本设计落地并验证后，再分别设计和实现：

1. Gateway/TUI 的显式 Steer 入口；
2. Redirect：仅中断当前模型请求并在同一 Run 内重定向；
3. Tool Result 和 Active Run 预算；
4. 完整 Provider Request 的 Context Pressure；
5. 长会话 Replay 与多 Provider 消息兼容性验证。
