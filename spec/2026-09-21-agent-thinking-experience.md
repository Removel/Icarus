# Agent Thinking Experience｜Agent 思考过程与 TUI 运行体验规格

## 文档定位

设计日期：2026-09-21。

本文定义 Agent 思考内容从模型输出到公共历史与 TUI 展示的跨应用契约，并统一 TUI 的运行中
追加输入、工具结果展示和本地命令入口。该能力必须同时修改 Agent、Gateway 和 TUI，无法由任一
应用独立完成，因此放在仓库根目录 `spec/`。

应用级设计：

- [Agent 思考事件、持久化与工具预览设计](../apps/agent/docs/spec/2026-09-21-agent-thinking-experience/arch.md)；
- [Gateway 思考与工具输出协议设计](../apps/gateway/docs/spec/2026-09-21-agent-thinking-experience/arch.md)；
- [TUI 运行卡片、输入队列与命令注册设计](../apps/tui/docs/spec/2026-09-21-agent-thinking-experience/arch.md)。

实施计划：

- [Agent 实施计划](../apps/agent/docs/spec/2026-09-21-agent-thinking-experience/plan.md)；
- [Gateway 实施计划](../apps/gateway/docs/spec/2026-09-21-agent-thinking-experience/plan.md)；
- [TUI 实施计划](../apps/tui/docs/spec/2026-09-21-agent-thinking-experience/plan.md)。

根规格只定义跨应用必须一致的产品行为和公共协议。事件产生、RPC 透传、组件状态和测试分别由上述
应用文档承接。

## 背景

Model Provider 已能读取 OpenAI 兼容接口的 reasoning 字段和 Anthropic thinking block，
`ReActAgent` 也会把 reasoning 聚合进 `AgentResponse.reasoning`。当前缺口位于后续链路：

```text
Model Provider
→ ReActAgent stream
→ Agent Plugin Event
→ RuntimeUpdate / SessionStore
→ Gateway
→ TUI
```

现在只有最终文本和工具状态进入公共输出，thinking 在 `ReActAgent` 之后被忽略。TUI 的工具结果只
显示成功或失败，运行中输入绕过已有队列直接调用 steer，`exit` / `quit` 还存在独立于斜杠命令的
特殊分支。这些行为使运行过程难以理解，也让后续增加本地命令时需要持续修改 App 主流程。

## 目标

- 实时展示模型 thinking，并在历史恢复时支持回看。
- thinking 不进入 `Message`、Blackboard 或后续模型请求，不改变 Agent 行为。
- 持久化 step 级完整 thinking，不把流式 delta 写入数据库。
- TUI 使用一张可折叠运行卡片聚合 thinking、Tool 和运行中追加输入，最终 Assistant 回答独立展示。
- Tool 完成事件公开脱敏、限长的结果预览，超大完整结果继续只保存在 Agent 侧。
- 所有普通输入先进入同一个 TUI 队列，出队时再决定 `session.steer` 或 `session.submit`。
- 把 `/clear`、`/resume`、`/exit` 迁移到可注册的本地命令结构。

## 非目标

- 不把 thinking 作为对话上下文、Memory、Knowledge、Skill 或 TTS 输入。
- 不保证 Provider 一定产生 thinking，也不伪造缺失的 thinking。
- 不持久化 `assistant.thinking_delta`，不按原始 token 时间精确回放动画。
- 不新增读取 Agent 本地完整 Tool Result 文件的 Gateway RPC。
- 不改变 Tool 执行结果、模型可见 Tool Result 或 Tool Guard 预算。
- 不实现、不注册 `/compact`、`/btw`，也不预设其参数和业务语义。
- 不增加队列持久化、队列重排、并行 Task 或后台 Session 切换。

## 跨应用事件契约

### Thinking 实时事件

```json
{
  "type": "assistant.thinking_delta",
  "task_id": "task-id",
  "sequence": null,
  "payload": {
    "step": 1,
    "text": "正在检查当前实现……"
  }
}
```

- 只发布非空 `text`。
- 仅用于在线流式展示，不进入 SessionStore，因此没有持久化 `sequence`。
- 断线期间错过的 delta 不补发；客户端通过完整 thinking 记录恢复最终状态。

### Thinking 完整记录

```json
{
  "type": "assistant.thinking",
  "task_id": "task-id",
  "sequence": 12,
  "payload": {
    "step": 1,
    "text": "正在检查当前实现并定位数据流。",
    "partial": false
  }
}
```

- 每个有 thinking 的模型 step 最多写入一条完整记录。
- 正常完成该模型响应时 `partial=false`。
- 取消或失败发生在 thinking 流中时，已经产生的内容以 `partial=true` 收束并保存。
- 完全没有 thinking 的 step 不产生空记录。
- TUI 使用 `(task_id, step)` 将完整记录与实时临时块对账，不重复显示。

事件顺序遵循：

```text
(assistant.thinking_delta | assistant.text_delta) *
→ assistant.thinking ?
→ assistant.message ?
→ tool.started* / task.error / task.finished
```

正常模型响应中，完整 thinking 先于同 step 的完整 Assistant Message，Message 又先于该 step 的 Tool；
开始 Tool、进入下一 step 或到达 Task 终态前，当前 thinking 必须先收束。异常时没有完整 Message 也可以
直接进入 error/finished，但已产生 thinking 必须先以 partial 收束。

### Tool 完成记录

`tool.completed` 保留已有字段并增加安全预览：

```json
{
  "type": "tool.completed",
  "payload": {
    "step": 1,
    "call_id": "call-id",
    "tool_name": "read_file",
    "success": true,
    "output_preview": {"path": "README.md", "content": "..."},
    "preview_truncated": true,
    "full_result_available": true,
    "preview_error": null,
    "error": null
  }
}
```

- `output_preview` 是 Agent 对现有 `ToolExecutionResult.output` 做递归脱敏和确定性限长后的
  JSON 兼容值；没有输出时为 `null`。公共预览上限固定为 2,000 个保守估算 token，不新增配置项。
- 预览限长沿用 Tool Result 的稳定序列化与 Head/Tail 策略，不把完整超大结果复制进公共历史。
- `preview_truncated` 明确表示公共预览是否截断。
- `full_result_available` 只在 metadata 有非空 `result_file` 且 `result_file_complete=true` 时为 true；
  即使 preview 生成失败也保持该安全 bool。公共协议不暴露 Agent 本地路径或可解引用标识。
- Agent 在公开预览前移除 Tool Guard 内部省略标记中可能包含的本地结果路径。
- 失败的 `error` 继续单独脱敏，不把错误伪装成普通输出。
- `tool.started.arguments` 继续由 Agent 脱敏。

## Thinking 与对话历史边界

Thinking 属于可观察的运行记录，不属于模型消息历史：

| 数据 | 实时输出 | SessionStore | Blackboard | 后续模型请求 |
| --- | --- | --- | --- | --- |
| `assistant.thinking_delta` | 是 | 否 | 否 | 否 |
| `assistant.thinking` | 是 | 是 | 否 | 否 |
| `assistant.text_delta` | 是 | 否 | 否 | 否 |
| `assistant.message` | 是 | 是 | 按现有 Run 规则 | 是 |

`AgentResponse.reasoning` 可以继续作为本次调用结果的诊断字段，但不能被转换为 `Message` 或追加到
`task_messages`。历史恢复只从 SessionStore 读取公共 RuntimeUpdate，不从 Trace 或 Blackboard 推断
thinking。

## TUI 用户体验

### 运行卡片

每个 Task 使用一张运行卡片聚合中间过程：

```text
Run
├── Thinking step 1          collapsed / expanded
├── Assistant progress       optional intermediate text
├── Tool read_file           collapsed / expanded
├── Added by you             correction
├── Thinking step 2          collapsed / expanded
└── Tool run_command         collapsed / expanded

Icarus
└── final assistant Markdown
```

- 所有 thinking 创建时默认展开并流式更新，包括实时输出和历史恢复。
- 新 thinking、Tool 开始和 Task 终态都不自动折叠已有 thinking。
- Tool 默认折叠；展开后按 JSON、文本/命令、文件摘要或错误展示。
- 运行中的 `user.correction` 在卡片内显示为 `Added by you`，不再投影成新的顶层用户消息。
- 运行中 assistant text 先作为卡片内的候选输出流式显示；如果同 step 后续开始 Tool，或进入下一 step，
  它作为中间过程留在卡片内。只有 Task 成功完成时最后一个未被后续 step/Tool 消费的候选提升为独立
  Assistant Markdown 消息，避免 Tool step 带文本时破坏卡片的时间顺序。
- 用户可以手动折叠或重新展开 thinking；完整记录对账保留用户当前选择，不强制改回默认状态。
- 截断预览分别提示“完整结果保存在 Agent 侧”或“完整结果不可用”，具体取决于
  `full_result_available`；TUI 不直接读取 Agent 文件系统，也不展示本地路径。

### 统一输入队列

所有非命令普通输入先进入同一个本地 FIFO 队列。队列项不提前标记为“追加”或“新对话”；真正出队
时根据当时状态选择：

```text
队首出队
├── 当前 Task 仍运行且可追加 → session.steer
└── 当前 Task 已结束 / idle  → session.submit
```

- 同一时间最多一个队列项处于发送握手中。
- RPC 接受后才从队首移除；失败或取消发送时保留原队首。
- `session.submit` 和 `session.steer` 都携带同一个队列项的稳定 `submission_id`。AgentRuntime 对两条
  路径分别做有界、进程内幂等记录；steer 只缓存已经接受、可能产生副作用的操作。连接断开后使用
  同一 ID 重试不会重复创建 Task 或重复追加。
- `session.steer` 返回 `already_finished` 等不可追加结果时，保留队首、对账 Task 状态，并在 idle 后按
  `session.submit` 自动降级为新对话。
- `already_cancelling` 保留队首并等待终态；`invalid_content` 等确定性拒绝保留队首但暂停自动重试，
  由用户通过现有 `Ctrl+C` 撤回编辑。
- submit/steer 的 `invalid_resource` / `resource_unavailable` 使用相同的可编辑队首阻塞语义；协议
  冲突或未知状态才进入 fatal 边界，不能把普通可修正输入错误误判为 Runtime 整体失败。
- 网络结果不确定时锁定队首及原始 reservation；即使 Task 状态已经变化，连接恢复后也必须先以相同
  `submission_id`、相同 RPC 类型和相同目标 Task 重试并确定首次结果，不能直接改走另一条路由。
- Reservation 记录连接代次；若重连先成功、旧 RPC 后失败，客户端立即在新代次精确重试，每个连接代次
  最多尝试一次，避免永久等待下一次重连或重复并发发送。
- FIFO 决定发送顺序，`Ctrl+C` 继续从队尾撤回并恢复到 Composer，形成 LIFO 撤回。
- 新对话与追加使用相同的编辑、附件、撤回和临时文件生命周期。

### 本地命令

本次只迁移三个已经有明确行为的命令：

```text
CommandRegistry
├── /clear
├── /resume
└── /exit
```

- 命令在普通消息入队前解析，不发送给 Agent，也不写入 Session 历史。
- 命令定义包含名称、帮助信息、参数解析和异步 handler。
- `/clear`、`/resume` 保留当前 idle 门禁和附件拒绝规则。
- `/exit` 统一现有退出路径，在应用仍接受输入时可立即触发正常 shutdown；带附件时拒绝执行并恢复草稿。
- 未知 `/xxx` 显示 `Unknown command: /xxx`，不进入队列。
- 未知命令、参数错误、附件错误或 idle 门禁失败时恢复完整 draft、附件和原 submission ID。
- 裸 `exit` 和 `quit` 不再特殊处理，作为普通用户消息进入队列。
- 注册表保留新增命令的稳定入口，但本次不注册空壳命令或推测未来业务语义。

## 状态竞争与失败处理

- Provider 不支持 thinking：正常展示 Tool 和最终回答，不显示空 thinking。
- thinking delta 到达后断线：临时块保留；重连历史中的完整记录以 `(task_id, step)` 对账替换。
- 取消或失败：Agent 保存已经产生的 partial thinking；TUI 折叠该块并展示现有终态。
- Tool 预览生成失败：仍发布 `tool.completed` 的成功/失败事实，并使用安全的预览错误说明；不得使
  Agent Task 因 UI 投影失败而失败。
- steer 发送期间 Task 结束：队首不丢失，对账后改走 submit。
- submit/steer 网络失败：队首不移除，TUI 显示正在对账；连接恢复后精确重试原 reservation。
- 未知 RuntimeUpdate：旧客户端继续忽略；新客户端保持现有诊断计数。

## 跨应用职责

| 应用 | 本期职责 | 明确不负责 |
| --- | --- | --- |
| Agent | 发布 thinking 事件；收束 step；持久化完整块；生成安全 Tool 预览；保持 thinking 与 Message 隔离 | TUI 样式、RPC 连接、命令解析 |
| Gateway | 通过现有 RuntimeUpdate、订阅和历史接口稳定透传新增字段与类型 | 聚合 thinking、读取 Tool 文件、解释 Tool 业务语义 |
| TUI | 运行卡片、历史对账、统一队列动态路由、CommandRegistry 与三个现有命令 | 访问 Blackboard、读取 Agent 文件、决定 thinking 是否进入模型 |

## 兼容性

- `RuntimeUpdateModel.type` 继续是字符串，新增类型无需协议版本切换。
- `assistant.text_delta` 继续实时且不入库；旧历史 delta 合并逻辑保留，本期不做数据迁移。
- `tool.completed` 只增加可选字段，已有字段不删除。
- 旧 TUI 可以忽略 thinking；新 TUI 对缺少 Tool 预览字段的旧历史继续显示原有状态。
- `session.submit`、`session.steer`、`session.get_history` 和 `runtime.update` 方法名与基本语义不变。
- `session.steer` 增加可选 `submission_id`；新 TUI 必须发送，旧客户端省略时仍可调用，但不获得
  跨网络重试幂等保证。
- ReActAgent 继续无状态，Provider 差异继续只存在于 `model_provider`。

## 验收标准

- 支持 thinking 的 Provider 可在 TUI 中实时显示最新 thinking。
- 每个有内容的 step 最多持久化一条 `assistant.thinking`，delta 不进入数据库。
- 取消和失败前已显示的 thinking 可在恢复历史后看到，并标记为 partial。
- thinking 不出现在 Blackboard、`task_messages` 或下一轮模型输入中。
- TUI 恢复历史时不重复 thinking，且默认展开所有历史 thinking。
- thinking、Tool 和 `user.correction` 聚合在 Task 运行卡片，最终回答独立显示。
- Tool 结果预览经过脱敏和限长，失败与截断状态清晰。
- 运行中输入、临界时刻输入和 idle 输入都先进入同一队列，并在出队时正确选择 steer 或 submit。
- 发送失败、late steer 和 Task 结束竞争不会丢失队列内容或附件。
- steer 响应丢失后使用同一 `submission_id` 重试不会把同一内容追加两次。
- `/clear`、`/resume`、`/exit` 全部通过注册表执行；未知斜杠命令被拦截；裸 `exit` / `quit` 成为普通消息。
- Agent、Gateway、TUI 的相关小测试和应用测试通过，`git diff --check` 通过。
