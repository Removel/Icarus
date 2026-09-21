# Gateway Thinking and Tool Output Protocol Design｜Gateway 思考与工具输出协议设计

## 文档定位

设计日期：2026-09-21。

本文定义 `apps/gateway` 和 `packages/gateway_protocol` 如何通过现有 JSON-RPC RuntimeUpdate 通路公开
Agent thinking 与安全 Tool 预览。跨应用产品契约见：

- [Agent Thinking Experience](../../../../../spec/2026-09-21-agent-thinking-experience.md)；
- [Agent 事件与持久化设计](../../../../agent/docs/spec/2026-09-21-agent-thinking-experience/arch.md)；
- [TUI 交互设计](../../../../tui/docs/spec/2026-09-21-agent-thinking-experience/arch.md)。

实施步骤见 [Gateway 实施计划](plan.md)。

Gateway 继续只负责协议校验、Domain/Wire 转换、RPC 路由、订阅和历史返回，不聚合 thinking、不读取
Agent 本地 Tool Result 文件，也不解释 Tool 名称。

## 当前基础

现有协议已经提供：

- `runtime.update` Notification；
- `session.subscribe` / `session.unsubscribe`；
- `session.get_history` 及 `history_cursor`；
- `session.submit` 和 `session.steer`；
- `RuntimeUpdateModel.type: str`、`payload: dict[str, Any]`、可选 `sequence`。

因此本期不增加新 RPC 方法、不增加协议版本握手，也不修改 Session History 分页结构。新增 update 类型
沿用同一条实时与历史链路。

## Wire 契约

### `assistant.thinking_delta`

实时 Notification 中的 `RuntimeUpdateModel`：

```json
{
  "workspace_key": "workspace-key",
  "session_id": "session-id",
  "task_id": "task-id",
  "type": "assistant.thinking_delta",
  "payload": {
    "step": 1,
    "text": "Checking the runtime path..."
  },
  "occurred_at": "2026-09-21T10:00:00Z",
  "sequence": null
}
```

约束：

- `task_id` 非空；
- `step` 是正整数；
- `text` 是非空字符串；
- `sequence` 必须为 `null`。

该类型只由实时订阅发送，不会出现在 `session.get_history`。Gateway 不缓存 delta 供断线补发。

### `assistant.thinking`

实时 Notification 与 `session.get_history.records` 使用同一 Wire Model：

```json
{
  "workspace_key": "workspace-key",
  "session_id": "session-id",
  "task_id": "task-id",
  "type": "assistant.thinking",
  "payload": {
    "step": 1,
    "text": "Checked the runtime path and found the projection gap.",
    "partial": false
  },
  "occurred_at": "2026-09-21T10:00:01Z",
  "sequence": 12
}
```

约束：

- `task_id` 非空；
- `step` 是正整数；
- `text` 是非空字符串；
- `partial` 是布尔值；
- 持久化记录具有正整数 `sequence`。

### `tool.completed` 扩展

保留现有 `step`、`call_id`、`tool_name`、`success`、`error`，增加以下字段：

```json
{
  "output_preview": {"path": "README.md", "content": "..."},
  "preview_truncated": true,
  "full_result_available": true,
  "preview_error": null
}
```

| 字段 | Wire 类型 | 说明 |
| --- | --- | --- |
| `output_preview` | 任意 JSON value 或 null | Agent 已脱敏、限长的输出 |
| `preview_truncated` | boolean | 公共预览是否省略内容 |
| `full_result_available` | boolean | Agent 侧是否保存了完整原始结果 |
| `preview_error` | string 或 null | 预览不可用的安全固定说明 |

这些字段由 Agent 生成。Gateway 不重新脱敏、不重新截断，也不从 `tool_name` 推断输出格式。公共契约
不定义本地结果路径；其不泄漏由 Agent 生产测试保证，Gateway 不另建一套字段白名单。

## 共享协议模型与校验边界

`RuntimeUpdateModel` 继续作为统一 Envelope，保持：

```python
class RuntimeUpdateModel(StrictWireModel):
    workspace_key: str
    session_id: str
    task_id: str | None
    type: str
    payload: dict[str, Any]
    occurred_at: datetime
    sequence: int | None = Field(default=None, ge=1)
```

本期不把 `type` 改为封闭 Literal，也不把 `payload` 改成大型 discriminated union。原因是 Gateway 的
兼容策略明确允许新 RuntimeUpdate 类型由旧客户端忽略；封闭枚举会使旧 Gateway 拒绝新 Agent 输出。

本期不增加按 update type 分支的 Gateway payload 模型。Agent 的 `RuntimeUpdate` 构造与定向测试负责
保证生产端字段和 JSON 兼容性；Gateway 继续验证通用 Envelope 并原样转换 payload；TUI Projector 在
消费已知类型时校验所需字段。这样既保留现有开放协议，也避免一个新字段让 Gateway 广播泵因专用
validator 失败而中断。Gateway 协议测试使用真实 Domain `RuntimeUpdate` 固定新增字段与 sequence 契约。

## RPC 与数据流

实时路径：

```text
AgentRuntime publishes RuntimeUpdate
→ Gateway subscription filter by workspace/session
→ RuntimeUpdateModel.from_domain
→ runtime.update Notification
→ subscribed TUI
```

历史路径：

```text
SessionStore persisted RuntimeUpdate
→ session.get_history(after_sequence, limit)
→ RuntimeUpdateModel.from_domain for each record
→ SessionHistoryModel
```

`assistant.thinking_delta` 只经过实时路径；`assistant.thinking` 和扩展后的 `tool.completed` 同时经过实时
与历史路径。Gateway 不负责把 delta 拼成完整块，也不在网络连接级维护 thinking buffer。

## `session.steer` 与队列语义

TUI 的“所有普通输入先排队”是客户端状态机变化，不改变 Gateway RPC：

- 运行中出队仍调用 `session.steer`；
- idle 出队仍调用 `session.submit`；
- `session.steer` 增加可选非空字符串 `submission_id`，新 TUI 传入队列项已有的稳定 ID；
- Gateway 把 `submission_id` 原样传给 AgentRuntime，不自行缓存或生成重试记录；
- Gateway 继续返回现有结构化 steer 状态，如 `accepted`、`already_finished` 和 `not_found`；
- Gateway 不接受“如果不能 steer 就自动 submit”的复合请求。

动态降级必须由 TUI 在收到权威结果并对账 Task 状态后完成，避免 Gateway 在一个 RPC 内隐式创建
新 Task 或改变 FIFO 顺序。

当连接在响应送达前中断时，Gateway 不判断请求是否已生效。客户端重连后使用同一 `submission_id`、
相同参数重试原 `session.steer`，由 AgentRuntime 返回首次 accepted 结果或重新判断此前无副作用的拒绝。

## 顺序与重连

- 有 sequence 的 update 继续服从 Session 级严格连续顺序。
- 无 sequence 的 thinking/text delta 可以与持久化 update 交错到达。
- Agent 保证同一发布链路中 completed thinking 先于对应 Tool / Assistant 完整事件；Gateway 保持收到
顺序，不重新排序。
- 断线后 TUI 使用 `session.get_history(after_sequence=last_sequence)` 恢复完整 thinking 和 Tool 预览。
- Gateway 不补发断线期间无 sequence 的 delta；完整记录是对账事实。
- 慢连接继续使用现有有界发送队列和关闭策略，不为 thinking 创建无限缓存。

## 安全边界

- Gateway 只接受 Agent 已生成的脱敏 `output_preview`；不访问 Tool 对象或执行层。
- Gateway 不接收或公开 Agent 本地 Tool Result 路径，也不增加 download/read RPC。
- RPC Error 不包含 Tool 原始输出、thinking 原文之外的内部异常、Agent 文件路径或 Python traceback。
- Thinking 本身是用户请求输出的一部分，会进入该 Session 的公共历史；其访问控制与现有 Session
  History 完全一致。
- Gateway 日志不新增整段 thinking 或 Tool preview 的默认 info 日志，避免形成第二份内容存储。

## 兼容性

- 新 update 继续使用字符串 type；旧客户端可以忽略。
- `tool.completed` 只增加字段；读取旧历史时新 TUI 必须允许字段缺失。
- 现有 `session.subscribe`、`session.get_history`、`runtime.update`、`session.submit`、
  `session.steer` 的 method 和响应 Envelope 不变；steer 参数只增加可选 `submission_id`，旧客户端仍可
  省略，新客户端用它获得进程内网络重试幂等。
- `history_cursor` 只计算持久化记录；thinking delta 不消耗 cursor。
- 不增加数据库 schema、Gateway 状态或 Session 切换协议。

## 错误处理

- 通用 Envelope 或 JSON payload 无效：沿用现有 Domain/Wire 构造错误边界，不增加类型专用分支。
- 历史中已知新增类型损坏：沿用 `history_corrupt` 安全错误，不返回部分错误记录。
- 单个订阅发送失败：沿用当前连接隔离，不影响 Agent Task 和其他客户端。
- 未知 update 类型：保持现有开放 Envelope 行为，由客户端的 projector registry 决定是否忽略。

## 测试范围

- thinking delta 的 Wire 转换保留 `sequence=None` 和 payload。
- 完整 thinking 在实时 Notification 与 History 响应中格式一致。
- Tool preview 支持 JSON 标量、对象、数组、null、截断、错误和完整结果可用性。
- 新增已知类型和未知类型都保持现有开放 Envelope 兼容行为。
- History cursor 不被 delta 消耗。
- 重连分页返回完整 thinking 且不返回 delta。
- Gateway 不接收本地结果路径，也不新增文件接口。
- `session.submit` / `session.steer` 现有协议回归通过。

## 完成标准

- Gateway 通过现有协议实时透传 thinking delta。
- `session.get_history` 返回 step 级完整 thinking 与 Tool 安全预览。
- 新增格式有真实 Domain/Wire 协议测试，Envelope 仍保持可扩展。
- 没有新增聚合状态、本地文件访问或复合 submit/steer 语义。
- Gateway 小测试、`make test-gateway` 和 `git diff --check` 通过。
