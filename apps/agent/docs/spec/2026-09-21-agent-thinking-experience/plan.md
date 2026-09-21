# Agent Thinking Event, Persistence, and Tool Preview Implementation Plan｜Agent 思考事件、持久化与工具预览实施计划

## 目标

基于以下已确认设计，在不改变 ReActAgent 无状态边界和 Blackboard 完整消息历史的前提下，让 Agent
公开模型 thinking，并为 TUI 提供安全的 Tool 输出预览：

- [跨应用规格](../../../../../spec/2026-09-21-agent-thinking-experience.md)；
- [Agent 设计](arch.md)。

完成后：

- Provider 已有 `reasoning_delta` 进入 Agent Stream Event；
- thinking delta 只实时发布，step 级完整 thinking 持久化；
- 取消和失败保留已产生的 partial thinking；
- thinking 不进入 `Message`、Blackboard 或下一次模型请求；
- `tool.completed` 提供最多 2,000 个保守估算 token 的脱敏预览；
- Gateway 可以通过现有 RuntimeUpdate 通路透传新数据。

## 实施原则

- 每个阶段先补失败测试，再修改生产代码。
- 同步 `stream()` 和异步 `astream()` 必须保持相同行为。
- 高频 delta 不写数据库、不打开默认 Event Flow Trace。
- 不修改 Provider Adapter 已有的厂商解析逻辑，除非回归测试证明其行为不符合当前公共类型。
- 不把 TUI 样式、Gateway Wire Model 或命令逻辑放入 `apps/agent`。
- 不顺带修改 Memory、Knowledge、Skill、TTS 或其他 Plugin。

## 依赖顺序

```text
能力层事件与 ReActAgent
→ AgentPlugin step 收束
→ RuntimeUpdate 投影与持久化
→ Tool 公共预览
→ Gateway/TUI 集成
```

前三阶段建立跨应用事实源，Gateway 和 TUI 只能在这些类型及 payload 固定后接入。

## 阶段一：增加 Thinking Stream Event

### 更新文件

- `apps/agent/src/agent_orchestration/capability/types.py`
- `apps/agent/src/agent_orchestration/capability/__init__.py`
- `apps/agent/src/agent_orchestration/capability/react_agent.py`
- `apps/agent/test/agent_orchestration/capability/test_react_agent_stream.py`
- `apps/agent/test/agent_orchestration/capability/test_react_agent.py`

### 开发内容

1. 增加 `AgentThinkingDeltaEvent(step, text)`：
   - 继承通用 `Event`；
   - `trace_event_flow = False`；
   - 与 `AgentTextDeltaEvent` 使用相同 step 编号语义。
2. 增加 `AgentThinkingCompletedEvent(step, text, partial=False)`：
   - 作为 AgentPlugin 的 step 级收束事件；
   - 与 delta 一样设置 `trace_event_flow = False`，避免正文被复制到诊断 trace；
   - 不增加 Provider 字段、Message 或 Tool 依赖。
3. 从 capability package 导出两个事件。
4. 在 `ReActAgent.stream()` 与 `ReActAgent.astream()` 的 chunk 循环中：
   - 非空 `reasoning_delta` 先发布 `AgentThinkingDeltaEvent`；
   - 非空 `text_delta` 继续发布 `AgentTextDeltaEvent`；
   - 原 chunk 仍完整加入聚合列表。
5. 保留 `_aggregate_stream_chunks()` 对 `LLMResponse.reasoning` 的聚合。
6. 不把 reasoning 写入 `response.message.content`、`state.messages` 或 `task_messages`。

### 定向测试

- 同步流和异步流均按 chunk 发布 thinking delta。
- 同一 chunk 同时带 reasoning 和 text 时，thinking event 在 text event 之前。
- 空 reasoning delta 不产生事件。
- 最终 `AgentResponse.reasoning` 仍是所有 reasoning delta 的拼接。
- Tool step 与 Final step 都使用正确 step 编号。
- 原先“reasoning 不作为流事件”的断言改为：reasoning 只作为专用 thinking 事件流出。
- 捕获第二次 LLM 请求，断言其中没有 thinking 文本或额外 Message。

### 验证命令

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/capability/test_react_agent_stream.py \
  apps/agent/test/agent_orchestration/capability/test_react_agent.py -q
```

## 阶段二：在 AgentPlugin 按 Step 收束 Thinking

### 更新文件

- `apps/agent/src/agent_orchestration/plugins/agent/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/agent/manifest.json`
- `apps/agent/test/agent_orchestration/plugins/agent/test_plugin.py`

### 开发内容

1. 在 `_run_agent()` 的单次 Run 局部状态中增加：
   - 当前 thinking step；
   - thinking parts；
   - 已完成 thinking step 集合。
2. 提取 `_publish_completed_thinking(...)` helper：
   - 空 parts 直接返回；
   - 拼接并清空 parts；
   - 防止同一 step 重复发布；
   - 发布 `AgentThinkingCompletedEvent(partial=...)`。
3. 收到 `AgentThinkingDeltaEvent` 时先处理 step 切换并把文本加入局部 thinking buffer，再执行当前
   stream loop 的 cancellation check，随后才发布原 delta。这样取消恰好发生在模型已经产出 delta 之后时，
   catch 路径仍能把该文本收束为 partial thinking，不会因现有“先检查取消、再处理 stream event”顺序
   丢失最后一段已产出内容。对其他 stream event 保留现有 cancellation 优先级；不要为了这条规则把 Tool
   或 completed event 在取消后错误发布。
4. 收到 `AgentMessageCompletedEvent` 时，先以 `partial=False` 收束同 step thinking，再发布 Message。
5. Tool started、Agent completed 和下一 step 是防御性边界：只有已经观察到同 step Message completion
   才可按 `partial=False` 收束；缺少权威 Message 边界或发生 step 跳变时按 `partial=True` 收束并记录诊断，
   step 倒退直接报协议错误。
6. `CancelledError` 的受控取消路径在 `AgentCancelledEvent` 前以 `partial=True` 收束。
7. 普通异常和取消竞争路径在 `TaskErrorEvent` / `AgentCancelledEvent` 前以 `partial=True` 收束。
8. Thinking parts 与已有 `partial_text` 严格分开；现有可见 Assistant 文本收束逻辑不变。
9. 在 Agent Plugin manifest 的 `published_events` 增加两个 thinking 事件。

### 事件顺序断言

```text
正常 Tool step:
thinking_delta* → thinking(partial=false) → assistant.message → tool.started

正常 Final step:
thinking_delta* → thinking(partial=false) → assistant.message → agent.completed

取消/失败:
thinking_delta* → thinking(partial=true) → cancelled/error
```

如果模型已经发出完整 Message 后才发生 Tool 错误，该 step 的 thinking 仍为 `partial=false`；partial 只描述
thinking block 是否在模型响应完成前被中断，不描述整个 Task 是否成功。

### 定向测试

- 多个 delta 合成一条 completed thinking。
- 多 step 分别收束，不串接文本。
- Message、Tool 和终态前的顺序正确。
- 空 thinking 不发布 completed event。
- 取消、Provider 抛错和取消竞争只收束一次，且 `partial=True`。
- 模型 yield thinking delta 后、Plugin 处理前恰好收到取消时，该 delta 至少进入最终 partial thinking，
  不因 cancellation check 丢失。
- 缺失 Message completion、step 跳变和 step 倒退分别得到 partial 或协议错误，不被误标为正常完成。
- 已完成模型响应后的 Tool 失败不把 thinking 改为 partial。
- 可见 `partial_text` 与 thinking 分别生成正确事件。
- manifest 中事件路径可加载且与实际类型一致。

### 验证命令

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/agent/test_plugin.py -q
```

## 阶段三：增加 RuntimeUpdate 投影与持久化边界

### 更新文件

- `apps/agent/src/runtime_update.py`
- `apps/agent/src/agent_orchestration/plugins/runtime_update/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/runtime_update/manifest.json`
- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/test/agent_orchestration/plugins/runtime_update/test_plugin.py`
- `apps/agent/test/application/test_agent_runtime.py`
- `apps/agent/test/application/test_session_store.py`
- `apps/agent/test/application/test_runtime_update_stream.py`
- `apps/agent/test/application/test_session_runtime.py`

### 开发内容

1. `RuntimeUpdateType` 增加：
   - `assistant.thinking_delta`；
   - `assistant.thinking`。
2. `RuntimeUpdatePlugin` 导入并投影两个 Event：
   - delta payload 为 `step`, `text`；
   - completed payload 为 `step`, `text`, `partial`；
   - 两者都拒绝空文本。
3. RuntimeUpdate Plugin manifest 的 `consumed_events` 增加两个完整类型路径。
4. 把 `assistant.thinking_delta` 加入 `AgentRuntime` 两处分支共用的非持久化类型集合。为了避免遗漏，
   提取模块级常量，例如 `TRANSIENT_UPDATE_TYPES`，由已加载和未知 Session 分支共同使用。
5. `assistant.thinking` 继续走通用 `SessionStore.append_update()`，不增加数据库列或专用表。
6. `get_session_history()` 不对 thinking 执行 legacy delta 合并；只保留当前 text delta 兼容函数。

### 定向测试

- RuntimeUpdatePlugin 对两个事件生成精确 payload。
- 空 thinking 不发布 RuntimeUpdate。
- thinking delta 被实时订阅者收到，但 `sequence is None`。
- SessionStore 中没有 thinking delta；history cursor 不因 delta 增长。
- completed thinking 获得 sequence，夹在相邻持久化 update 中时序号连续。
- `get_session_history()` 返回完整 thinking，重启后仍可读取。
- 旧 `assistant.text_delta` 历史合并测试保持不变。
- unknown Session 收到 transient thinking delta 不触发“unknown Session”持久化错误。

### 验证命令

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/runtime_update/test_plugin.py \
  apps/agent/test/application/test_runtime_update_stream.py \
  apps/agent/test/application/test_session_store.py \
  apps/agent/test/application/test_agent_runtime.py \
  apps/agent/test/application/test_session_runtime.py -q
```

## 阶段四：实现安全 Tool 输出预览

### 新增文件

- `apps/agent/src/agent_orchestration/plugins/runtime_update/tool_preview.py`
- `apps/agent/test/agent_orchestration/plugins/runtime_update/test_tool_preview.py`

### 更新文件

- `apps/agent/src/agent_orchestration/plugins/runtime_update/plugin.py`
- `apps/agent/src/agent_orchestration/tools/result_budget.py`
- `apps/agent/test/agent_orchestration/plugins/runtime_update/test_plugin.py`
- `apps/agent/test/agent_orchestration/tools/test_result_budget.py`

### 开发内容

1. 在 `tool_preview.py` 定义普通 helper，不注册为 Plugin：

   ```text
   PUBLIC_TOOL_PREVIEW_MAX_TOKENS = 2_000
   build_public_tool_projection(result, redactor) -> PublicToolProjection
   ```

2. helper 输入 `ToolExecutionResult`，输出不可变结构：
   - `output_preview`；
   - `preview_truncated`；
   - `full_result_available`；
   - `preview_error`；
   - `safe_error`，供 RuntimeUpdatePlugin 填回既有 `error` 字段，不新增 Wire 字段。
3. 从允许列表读取非空 `metadata.result_file` 与严格为 true 的 `metadata.result_file_complete`，先独立
   计算 `full_result_available`，不复制路径值或其他任意 metadata；后续 preview 失败时仍保留该 bool。
4. Tool Guard 的模型可见 Head/Tail 标记可能已包含已知 `result_file` 路径；先从 output 和 error 中精确
   移除该内部引用，再使用 `Redactor` 递归脱敏。
5. 在 Redactor 前先做公共安全归一化：保留 JSON 标量/容器及已有 dataclass、Enum、Path、bytes 语义，
   不支持的任意 Python 对象转换为固定安全占位值；不能让 Redactor 对未知对象的 `repr` 进入公共历史。
6. 使用 `result_budget` 的稳定 JSON 序列化、保守 Token 计数和 Head/Tail 算法，把 output 限制到
   2,000 token。
7. 在 `result_budget.py` 从现有内部 Head/Tail 算法提取公开纯函数
   `preview_json_value(value, *, max_tokens, counter, head_ratio, omission_marker)`，返回 preview value 与
   truncated 标志；`render_tool_result()` 改为复用该函数并保持现有行为与测试不变。公共调用传入不含
   路径的 omission marker，不能复用模型可见的本地文件提示文本。
8. `preview_truncated` 为以下条件的逻辑或：
   - Agent Tool Guard 已设置 `metadata.result_truncated`；
   - 公共 2,000-token 上限发生二次截断。
9. RuntimeUpdatePlugin 把 helper 结果加入 `tool.completed.payload`，保留全部已有字段，并使用
   `safe_error` 作为既有 `error` 值；不得另走只脱敏但未去路径的分支。
10. helper 抛错时捕获并生成固定安全降级；Tool 的 `success` 仍按原事实投影，`error` 至少经过同一
   已知路径移除与 Redactor 后再公开。
11. 不把 `images` 的二进制数据、Agent 本地路径、任意 metadata 或 Tool 实例写入 RuntimeUpdate。

### 定向测试

- dict/list/scalar/string/null 输出保持 JSON 兼容和确定顺序。
- 长字符串与深层对象保留头尾且不超过 2,000 个保守 token。
- `api_key`、Authorization header、Bearer token 和嵌套 secret 被脱敏后才持久化。
- `result_truncated` 与公共二次截断均正确设置最终标志。
- 完整结果可用性正确，但本地路径和其他 metadata 不泄漏。
- preview helper 异常时仍保留由允许列表字段得到的完整结果可用性，不统一误报为 false。
- Tool Guard 已嵌入 output/error 省略标记的已知本地路径会被替换。
- 既有 `error` 字段不经旁路泄漏 result_file，helper 正常和降级路径都覆盖。
- Tool failure 同时保留安全 error 与 output preview。
- 不可序列化对象使用不含 `repr` 的固定安全 fallback；人为注入的 helper 异常走固定
  `preview_error`。
- 预览失败时 `success=True` 仍保持成功。

### 验证命令

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/runtime_update/test_tool_preview.py \
  apps/agent/test/agent_orchestration/plugins/runtime_update/test_plugin.py \
  apps/agent/test/agent_orchestration/tools/test_result_budget.py -q
```

## 阶段五：为 Steer 增加进程内幂等键

### 更新文件

- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/src/application/session_runtime.py`
- `apps/agent/test/application/test_agent_runtime.py`
- `apps/agent/test/application/test_session_runtime.py`
- `apps/agent/test/agent_orchestration/run_control/test_channel.py`

### 开发内容

1. `AgentRuntime.steer_task()` 增加可选 `submission_id`；空字符串拒绝，省略时保留旧客户端兼容路径。
2. 在 Session Entry 中增加与 submit 分离的有界 steer 记录，记录：
   - `submission_id`；
   - 由 task_id、content、resources、display text 生成的 fingerprint；
   - 首次 accepted `TaskOperationResult`。
3. 在 `entry.mutation_lock` 内先检查幂等记录：
   - 同 ID/同 fingerprint 返回首次结果；
   - 同 ID/不同 fingerprint 抛出 `SubmissionConflictError`；
   - 未命中记录时沿用内容寻址资源导入，再调用一次 SessionRuntime；实际是否 accepted 由 TaskChannel
     在加入 correction 时原子判断。不要新增会在预检和实际加入之间产生竞争窗口的 `can_steer` API。
4. 只有 `accepted` 结果写入记录；它是唯一会把 correction 加入 TaskChannel 的有副作用结果。非接受
   状态不缓存，使同一队列项在状态对账或用户编辑后可以重新判断。
5. steer 记录使用现有 `submission_history_limit` 独立有界淘汰，不能覆盖 submit 记录；也不能把 submit
   和 steer 的同名 ID 视为同一个操作。
6. `SessionRuntime.steer_task()` 接收可选 `submission_id`。非空时显式构造
   `TaskSteerRequestedEvent(event_id=submission_id, ...)`；省略时构造参数中不能传 `event_id=None`，必须让
   Event 基类的 default factory 生成 UUID。TaskChannel 已有 event_id 通路继续生成稳定
   `TaskSteerAppliedEvent.request_event_id`。
7. 不把幂等记录写入 SessionStore 或 Blackboard，不宣称跨进程重启幂等。

### 定向测试

- 同 ID/同 payload 连续 steer 只调用一次 SessionRuntime，返回相同结果。
- 同 ID/不同 task、文本、资源或 display text 抛 submission conflict。
- 非 accepted 结果不占用 ID，状态变化后相同请求可以重新判断。
- 非 accepted 重试可能再次执行资源导入 helper，但相同内容必须命中同一 Session asset，且不能产生
  RuntimeContextRecord；accepted 结果命中幂等记录时不得再次调用导入 helper。
- 第一次 accepted 响应丢失后重试不产生第二条 RuntimeContextRecord。
- accepted 结果响应丢失后重试返回原结果；非接受结果允许随 Task 当前状态变化。
- accepted 结果的同 ID 重试不再次调用资源导入；非 accepted 重试即使再次调用 helper，也只命中同一
  内容寻址 asset。
- 省略 submission_id 的旧调用仍可执行。
- 旧调用生成非空且唯一的 Event ID，而不是把 None 传入 Event 覆盖默认 UUID。
- Event、Hook 和 applied correction 使用稳定 request_event_id。
- 有界淘汰后不影响现有 submit 幂等记录。

### 验证命令

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/application/test_agent_runtime.py \
  apps/agent/test/application/test_session_runtime.py \
  apps/agent/test/agent_orchestration/run_control/test_channel.py -q
```

## 阶段六：Agent 端到端回归

### 更新文件

- `apps/agent/test/application/test_session_runtime.py`
- `apps/agent/test/application/test_agent_runtime.py`
- `apps/agent/test/agent_orchestration/plugins/blackboard/test_message_history.py`
- `README.md`
- `apps/agent/README.md`
- `apps/agent/docs/spec/2026-08-15-agent-stream-event/arch.md`
- `apps/agent/docs/spec/2026-08-18-plugin-event-flow-current-state/arch.md`
- `apps/agent/docs/spec/2026-09-04-session-store/arch.md`
- `apps/agent/docs/spec/2026-09-20-agent-run-history-steering/arch.md`
- `apps/agent/docs/spec/2026-09-20-tool-execution-guard/arch.md`
- `docs/todo/agent-core.md`

### 开发内容

1. 构造包含 reasoning、可见文本、Tool 和最终回答的两 step Run。
2. 通过 SessionRuntime/AgentRuntime 的真实 Plugin 流收集 RuntimeUpdate。
3. 断言实时顺序、持久化历史、Tool preview 和 Task 终态。
4. 再执行下一轮模型请求，断言 Blackboard 快照只含既有 Message，不含任一 thinking 原文。
5. 覆盖受控取消或 Provider 异常，确认 partial thinking 可恢复。
6. 保留原有 Agent 完整消息历史、steer 和 Tool Result 预算测试。
7. 同步现有事实文档：
   - Stream Event 和当前 Plugin Event Flow 增加两个 thinking 事件及 manifest 路径；
   - SessionStore 明确 delta 不持久化、完整 thinking 作为公共历史记录；
   - Run History 保持“thinking 不进入 Blackboard/模型历史”，同时说明它进入独立 RuntimeUpdate 历史；
   - Tool Guard 说明公共预览复用既有预算结果且不开放文件读取；
   - 根 README、Agent README 与 `docs/todo/agent-core.md` 只描述实际完成能力并链接本 spec。

### 验证命令

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/application/test_session_runtime.py \
  apps/agent/test/application/test_agent_runtime.py \
  apps/agent/test/agent_orchestration/plugins/blackboard/test_message_history.py -q

make test-agent
git diff --check
```

## 与 Gateway/TUI 的集成门禁

进入 Gateway 和 TUI 实施前，以下协议必须在测试中固定：

```text
assistant.thinking_delta = {step: int, text: non-empty str}, sequence=None
assistant.thinking       = {step: int, text: non-empty str, partial: bool}
tool.completed           = existing fields
                           + output_preview
                           + preview_truncated
                           + full_result_available
                           + preview_error
session.steer            = existing params + optional submission_id
```

如果实施中需要修改字段名或 partial 语义，必须先同步根规格、三个应用设计与本计划，再修改消费者。

## 建议提交边界

实际获得提交授权后，Agent 部分建议保持三个逻辑提交：

1. `feat(agent): publish and persist thinking updates`
   - 阶段一至三及其测试。
2. `feat(agent): expose safe tool output previews`
   - 阶段四及其测试。
3. `feat(agent): make task steering idempotent`
   - 阶段五至六及其测试。

不把 Gateway/TUI 代码或无关格式化混入 Agent 提交。

## 完成标准

- 同步和异步 ReAct 流都发布 Provider 无关 thinking event。
- 每个有内容的 step 最多一条持久化完整 thinking；delta 不占 sequence。
- 正常、取消、失败和多 step 顺序均有回归测试。
- thinking 与 Message、Blackboard、下一轮模型请求的隔离被直接验证。
- Tool preview 脱敏、限长、失败降级、完整结果可用性和路径不泄漏均有定向测试。
- `make test-agent` 与 `git diff --check` 通过。
