# Agent Thinking Event, Persistence, and Tool Preview Design｜Agent 思考事件、持久化与工具预览设计

## 文档定位

设计日期：2026-09-21。

本文定义 `apps/agent` 如何把 Provider 已提供的 reasoning 转换为公共 thinking 更新，如何在模型响应
边界收束并持久化，以及如何为 Tool 完成事件生成安全预览。跨应用产品契约见：

- [Agent Thinking Experience](../../../../../spec/2026-09-21-agent-thinking-experience.md)；
- [Gateway 协议设计](../../../../gateway/docs/spec/2026-09-21-agent-thinking-experience/arch.md)；
- [TUI 交互设计](../../../../tui/docs/spec/2026-09-21-agent-thinking-experience/arch.md)。

实施步骤见 [Agent 实施计划](plan.md)。

本文补充现有 [Agent Stream Event](../2026-08-15-agent-stream-event/arch.md)、
[Agent Run 完整历史](../2026-09-20-agent-run-history-steering/arch.md) 和
[Tool Execution Guard](../2026-09-20-tool-execution-guard/arch.md)，不改变其中的 Provider、Blackboard、
Tool Guard 和完整消息历史边界。

## 当前实现

- OpenAI 兼容 Provider 已从 `reasoning_content` / `reasoning` 读取 reasoning。
- Anthropic Provider 已处理 `thinking` block 和 `thinking_delta`。
- `LLMStreamChunk.reasoning_delta` 与 `LLMResponse.reasoning` 已是 Provider 无关公共类型。
- `ReActAgent` 聚合 reasoning，但只发布 `AgentTextDeltaEvent`，因此 reasoning 不会离开能力层。
- reasoning 不属于 `Message`；`AgentResponse.task_messages` 和 Blackboard 当前不会保存它。
- `AgentRuntime` 排除 `assistant.text_delta` 的持久化，其他公共 RuntimeUpdate 由 SessionStore 分配连续
  `sequence`。历史读取保留旧 text delta 的兼容合并。
- Tool Guard 已把完整超大结果保存到 Session 级文件，并为模型生成确定性、限长的 Head/Tail 结果。
- `RuntimeUpdatePlugin` 目前只在 `tool.completed` 中公开 success 和 error。

## 设计原则

1. Provider 差异止于 `model_provider`；上层只处理 `reasoning_delta`。
2. ReActAgent 继续无状态，不依赖 SessionStore、Gateway 或 TUI。
3. Thinking 是输出与观察数据，不是模型消息。
4. 实时 delta 与持久化完整记录分离，沿用 Assistant 文本的既有模式。
5. Tool 公共预览在 Agent 边界生成；Gateway 不解释业务对象或本地文件。
6. UI 投影失败不能改变 Agent 主流程或 Tool 执行结果。

## 内部事件

在能力层增加两个 Provider 无关事件：

```python
@dataclass(frozen=True, kw_only=True)
class AgentThinkingDeltaEvent(Event):
    trace_event_flow: ClassVar[bool] = False

    step: int
    text: str


@dataclass(frozen=True, kw_only=True)
class AgentThinkingCompletedEvent(Event):
    trace_event_flow: ClassVar[bool] = False

    step: int
    text: str
    partial: bool = False
```

`AgentThinkingDeltaEvent` 是 ReActAgent 的原始运行流事件。
`AgentThinkingCompletedEvent` 是 AgentPlugin 在 step 边界生成的收束事件。两者均保持 `task_id`、
`occurred_at` 和 Event 基类语义。两个事件都关闭 Event Flow Trace，避免 thinking 正文除公共
RuntimeUpdate 历史外再复制到诊断 trace；完整块的 step、长度和发布失败仍可由专用诊断记录。

Agent Plugin manifest 的 `published_events` 和 RuntimeUpdatePlugin manifest 的 `consumed_events` 必须
同步声明新增事件，不通过未声明事件或反向依赖绕过 Plugin Runtime。

## ReActAgent 输出

同步和异步流保持一致：

```python
for chunk in llm_stream:
    if chunk.reasoning_delta:
        yield AgentThinkingDeltaEvent(step=state.steps, text=...)
    if chunk.text_delta:
        yield AgentTextDeltaEvent(step=state.steps, text=...)
    chunks.append(chunk)
```

ReActAgent 仍使用全部 chunk 聚合 `LLMResponse.reasoning`，但不得：

- 把 reasoning 转换为 `TextPart`；
- 把 reasoning 添加到 Assistant `Message.content`；
- 把 reasoning 写入 `state.messages` 或 `task_messages`；
- 为没有 reasoning 的 Provider 生成占位内容。

同一个 Provider chunk 同时含 reasoning 和 text 时，先发布 thinking delta，再发布 text delta，保持该
chunk 内由思考向可见输出过渡的稳定顺序。

## AgentPlugin 收束状态

每个活动 Run 在 AgentPlugin 的局部执行协程内维护：

```text
thinking_step: int
thinking_parts: list[str]
thinking_completed_steps: set[int]
```

不把缓冲放入 ReActAgent、Blackboard 或全局 Plugin 状态。处理规则：

1. 收到非空 `AgentThinkingDeltaEvent`：
   - step 变化时先把上一 step 以 `partial=true` 防御性收束；
   - 追加当前文本；
   - 立即发布带 Task 身份的 delta。
2. 收到 `AgentMessageCompletedEvent`：
   - 先将同 step thinking 以 `partial=false` 收束；
   - 再发布完整 Assistant Message。
3. 收到同 step 的 `AgentToolStartedEvent` 或 `AgentCompletedEvent`：
   - 只有该 step 已收到 `AgentMessageCompletedEvent` 时，遗留缓存才按 `partial=false` 防御性收束；
   - 未收到权威 Message 边界却遇到其他边界时，按 `partial=true` 收束并记录诊断。
4. 收到更大 step 的首个事件：先将上一 step 未收束缓存按 `partial=true` 收束；step 倒退是协议错误。
5. 受控取消、Provider 异常或其他 Run 失败：
   - 在 `AgentCancelledEvent` / `TaskErrorEvent` 前把现有缓存以 `partial=true` 收束。
6. 空缓存不发布；同一 `(task_id, step)` 最多发布一个 completed event。

正常路径以 `AgentMessageCompletedEvent` 为权威模型响应边界。Tool 前和 Task 终态检查是防御性保障，
避免未来新增 Agent 实现遗漏收束。

Thinking 缓冲与当前可见文本的 `partial_text` 分开维护。取消时，两者分别收束：thinking 进入
`assistant.thinking(partial=true)`，可见文本继续按已有规则进入 Assistant Message；两者不能互相拼接。

## RuntimeUpdate 投影

`RuntimeUpdateType` 增加：

```text
assistant.thinking_delta
assistant.thinking
```

`RuntimeUpdatePlugin` 投影如下：

| Event | RuntimeUpdate | Payload |
| --- | --- | --- |
| `AgentThinkingDeltaEvent` | `assistant.thinking_delta` | `step`, `text` |
| `AgentThinkingCompletedEvent` | `assistant.thinking` | `step`, `text`, `partial` |

空文本在投影层再次过滤。`RuntimeUpdatePlugin` 不重新聚合 delta，不从
`AgentResponse.reasoning` 猜测缺失的完整块。

## 持久化和历史

`AgentRuntime` 的非持久化集合扩展为：

```text
session.lifecycle
assistant.text_delta
assistant.thinking_delta
```

因此：

- delta 直接发布给实时订阅者，`sequence=None`；
- 完整 `assistant.thinking` 通过 SessionStore 写入并获得连续 sequence；
- `session.get_history` 只返回完整 thinking，不返回本次新增的碎片；
- 不修改数据库表结构，update type 和 JSON payload 继续使用现有列；
- 不迁移旧记录，也不扩展旧 `assistant.text_delta` 合并函数来处理新 thinking。

重启时无法收束进程被强制终止前仅存在内存中的最后一段 delta，这是实时非持久化设计的明确边界。
受控取消和普通异常路径必须收束，正常进程退出应先走已有 quiesce/drain。

## Tool 公共预览

### 数据来源

`AgentToolCompletedEvent.result` 已是 Tool Guard 最终化后的 `ToolExecutionResult`。公共预览只从该对象
生成，不重新执行工具、不读取任意文件，也不改变模型可见结果。

投影步骤：

1. 读取已经受 Tool Guard 限长的 `result.output`。
2. 先转换为公共安全值：保留 JSON 标量/容器以及现有受支持的 dataclass、Enum、Path、bytes 语义，未知
   Python 对象使用固定占位值而不是 `repr`；同时精确移除已知内部 `result_file` 引用。
3. 使用 `Redactor.redact()` 递归脱敏，保证结果是 JSON 兼容值。
4. 使用 Tool Result 现有稳定序列化、Token Counter fallback 和 Head/Tail 预览规则，把公共预览限制
   在 `PUBLIC_TOOL_PREVIEW_MAX_TOKENS = 2_000` 个保守估算 token 内；该常量不增加用户配置项，也
   不得使用无限制 `repr`。
5. 从允许列表复制预览元数据，不把任意 Tool metadata 暴露到公共协议。
6. error 与 output 共用“先移除已知内部 result_file 引用、再脱敏”的安全入口；error 随后使用
   `Redactor.redact_text()`，不能由 RuntimeUpdatePlugin 绕过 helper 直接复制原字符串。

`tool.completed.payload` 增加：

| 字段 | 类型 | 来源 |
| --- | --- | --- |
| `output_preview` | JSON value / null | 脱敏且限长后的 `result.output` |
| `preview_truncated` | bool | `result_truncated` 或公共预览再次截断 |
| `full_result_available` | bool | metadata 有非空 `result_file` 且 `result_file_complete=true` |
| `preview_error` | string / null | 预览生成失败时的固定安全说明 |

公共 payload 不包含 `metadata.result_file` 的值。Tool Guard 的模型可见 Head/Tail 省略标记目前也可能
在 output 或 error 中嵌入该路径，因此公共投影 helper 必须在脱敏和二次限长前，递归移除对象字段或
字符串中与该已知内部引用相同的路径，替换为不透明的 Agent-side 提示。不得从 Tool 输出猜测其他路径
并任意删除。

预览构建发生异常时，RuntimeUpdatePlugin 捕获该投影异常并生成安全降级 payload：

```json
{
  "output_preview": null,
  "preview_truncated": true,
  "full_result_available": true,
  "preview_error": "Tool output preview unavailable"
}
```

示例假设完整结果已保存；实际值仍由安全 metadata bool 计算。即使预览内容生成失败，
`full_result_available` 也先独立从允许列表字段计算，不能因为 preview fallback 就统一谎报为 false。
`success` 和经过脱敏的 `error` 仍按原事件发布。降级不抛回 AgentPlugin，不把已成功的 Tool 改成失败。

## Blackboard 与 Hook 边界

- Blackboard 只消费现有完整 Run Message，不消费 thinking 事件。
- `AgentCompletedEvent.response.task_messages` 不包含 reasoning。
- `TaskChannel.history_checkpoint` 不保存 thinking。
- RuntimeUpdate 是产品历史投影，不成为模型恢复来源。
- Hook 可以记录 thinking 事件的存在、step 和长度，但不得默认复制原文到新的高频 Trace 字段。
- AgentPlugin 仍只发布原始执行流；样式、折叠和文字标签全部留在 TUI。

## Steer 幂等边界

统一 TUI 队列会在连接中断后重试尚未获得结果的 steer。`session.submit` 已使用 `submission_id` 做
进程内幂等，steer 必须复用相同保障，否则“服务端已接受、客户端未收到响应”会把同一用户补充应用
两次。

AgentRuntime 的 `steer_task` 接受可选 `submission_id`：

- 新 TUI 必须传入 `PendingMessage.submission_id`；
- AgentRuntime 在 Session Entry 中维护有界 `submission_id -> fingerprint + accepted TaskOperationResult`；
- fingerprint 包含目标 `task_id`、content、resources 和 display text；
- 已接受记录遇到相同 ID 和相同 fingerprint 时返回首次结果，不再次导入资源或调用 SessionRuntime；
- 已接受记录遇到相同 ID 和不同 fingerprint 时抛出既有 `SubmissionConflictError`；
- `already_finished`、`not_running`、`invalid_content` 等无副作用结果不写幂等记录，后续重试可根据当前
  Task 状态重新判断，也允许 TUI 在确认拒绝后编辑内容；
- 这里的“无副作用”特指不会把 correction 加入 TaskChannel；现有附件导入按内容寻址，同一资源重试只会
  命中相同 Session asset。不要为了避免一次无害的内容寻址导入新增带竞争窗口的 preflight API；实际是否
  accepted 仍由 TaskChannel 在加入队列时原子判断；
- 记录在 Session Entry 生命周期内保留，使用现有 submission history limit 做有界淘汰；
- 省略 ID 的旧调用继续执行，但不承诺网络重试幂等。

SessionRuntime 使用该 ID 作为 `TaskSteerRequestedEvent.event_id`，使 Hook 与
`TaskSteerAppliedEvent.request_event_id` 保持稳定。幂等记录不是 Session 业务历史，不写入 Blackboard；
它与现有 submit 幂等记录一样不提供跨 Agent 进程重启保证。

## 错误与边界情况

- Provider 输出空 delta：忽略。
- Provider 只返回最终 reasoning 而无 reasoning delta：本期不从最终字段补发流式事件；避免因厂商
  差异在上层推测时序。该 reasoning 仍保留在 `AgentResponse.reasoning`。
- step 变化前缺少正常完成事件：上一段以 partial 收束并记录诊断。
- RuntimeUpdate 发布失败：沿用 Plugin Runtime / AgentRuntime 现有错误边界，不写入 Blackboard。
- Tool output 含 secret：Redactor 在持久化前处理。
- Tool output 不可 JSON 序列化：公共 helper 将不支持的对象替换为固定安全占位值，不使用可能包含路径、
  secret 或超长正文的任意 `repr`；若仍失败则使用固定降级 payload。

## 测试范围

### 能力层

- 同步与异步流按 chunk 发布 thinking delta。
- reasoning 与 text 同 chunk 时顺序稳定。
- `LLMResponse.reasoning` 仍正确聚合。
- `Message`、`state.messages`、`task_messages` 和下一次 LLM 请求不含 thinking。
- 没有 reasoning 时不发布事件。

### AgentPlugin

- 正常 step 在 Assistant Message 前发布完整 thinking，`partial=false`。
- Tool step、最终回答 step 和多 step 分别只收束一次。
- 取消、Provider 失败和边界缺失时保存 `partial=true`。
- thinking 与可见 partial text 分开收束。
- manifest 声明与实际事件一致。

### Runtime 与持久化

- delta 无 sequence 且 SessionStore 中不存在。
- 完整 thinking 获得连续 sequence，并可由 `get_session_history` 返回。
- restart / reconnect 后历史无 delta 碎片。
- 旧 assistant delta 历史兼容逻辑保持通过。

### Tool 预览

- dict、list、文本、空输出和不可直接序列化值均形成稳定 payload。
- 嵌套敏感字段和文本凭证被脱敏。
- 长输出保留头尾、标记截断并公开完整结果可用性，但不公开 Agent 本地引用。
- 已知 Tool Result 内部路径不会出现在对象、数组或字符串形式的公共预览中。
- 预览生成异常不改变 Tool success，也不终止 Task。

## 完成标准

- Provider reasoning 能通过内部 Event 和 RuntimeUpdate 到达 Gateway。
- 新数据每个 step 最多一条持久化 thinking，数据库无新增 delta 碎片。
- 取消或异常前的非空 thinking 被保留为 partial。
- thinking 与 Blackboard / 模型历史的隔离有直接回归测试。
- `tool.completed` 含安全、限长、可渲染的预览字段。
- Agent 相关小测试、`make test-agent` 和 `git diff --check` 通过。
