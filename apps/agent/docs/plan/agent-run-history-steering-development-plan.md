# Agent Run 完整历史与运行中纠偏开发计划

## 目标

基于 `apps/agent/docs/arch/agent-run-history-steering-design.md`，在不新增 Turn、不修改 Gateway/TUI
且不改变 Runtime 层级的前提下完成两项 Agent 能力：

- Blackboard 恢复保存并跨 Run 重放完整、协议合法的 Agent Run 消息；
- 当前 Task 可以接收用户 Steer，并在不停止 Agent Run 的情况下于安全边界生效。

本计划优先恢复历史正确性，再增加 Steer。每个任务先增加会失败的行为测试，再修改实现。

## 范围

### 本期实现

- Completed Run 提交完整 `AgentResponse.task_messages`；
- Cancelled Run 提交最近安全检查点，并以明确的 Assistant 中断消息闭合；
- 仅在终态事件明确携带安全 `task_messages` 时提交 Failed Run；
- 新 Session 从升级后的第一个 Run 开始持续保存完整消息；
- 旧 Session 在 Provider 请求副本上执行兼容修复，不原地改写存档；
- 新增 Agent 层 `steer_task` 接口；
- 用户 Steer 与 Plugin Runtime Context 使用不同语义和消息标记；
- 已应用 Steer 进入完整历史，Stop 丢弃尚未应用的 Steer；
- 定向测试、Agent 全量测试和冲突文档同步。

### 本期不实现

- Gateway RPC 和 TUI 的 Steer 入口；
- TUI 草稿、输入队列和 `Ctrl+C` 状态机调整；
- Redirect 或中断当前模型请求后重试；
- 一个 Task 多个 Agent Run；
- Provider Adapter 修改；
- Tool Result Spill、Batch Budget、Active Run Budget；
- SessionStore 数据库 Schema 或 RuntimeUpdate 新类型；
- 隐藏 reasoning 跨 Run 重放；
- Git 分支、提交或推送。

## 当前基线与目标差异

| 能力 | 当前实现 | 本计划完成后 |
| --- | --- | --- |
| Completed 历史 | Blackboard 重建原始 User + Final Assistant | 直接提交完整 `task_messages` |
| Tool Call / Result | 当前 Run 结束后不进入 Blackboard | 完整配对后跨 Run 保留 |
| 中间 Assistant | 当前 Run 结束后丢失 | 作为协议消息保留 |
| Runtime Context | 当前 Run 内使用，完成后丢失 | 已应用内容随完整 Run 保留 |
| Cancelled 历史 | 可能只提交孤立 User | 安全前缀 + Assistant 中断闭合 |
| Failed 历史 | 从混合消息中查找可见 Assistant | 只接受明确安全检查点 |
| 用户 Steer | 没有 Agent 应用接口 | 同 Task、同 Run、安全点应用 |
| 旧历史 | 原样发送 | 请求副本上兼容修复 |

## 实施顺序

```text
消息合法性组件与回归测试
  ↓
Blackboard 恢复完整历史提交
  ↓
Cancelled / Failed 安全闭合
  ↓
TaskChannel 区分 Context 与 Steer
  ↓
ReAct 四入口应用 Steer
  ↓
SessionRuntime / AgentRuntime 暴露 Steer
  ↓
文档同步与全量验证
```

后续任务依赖前一步建立的不变量，不并行修改同一条消息链。

## 任务一：固化完整历史与消息合法性

### 新增文件

- `apps/agent/src/agent_orchestration/plugins/blackboard/message_history.py`
- `apps/agent/test/agent_orchestration/plugins/blackboard/test_message_history.py`

### 更新文件

- `apps/agent/test/agent_orchestration/plugins/blackboard/test_plugin.py`

### 开发内容

1. 先增加回归测试，固定可提交 Run 增量的结构：
   - 第一条为当前 Task 的 User Message；
   - 一条 Assistant 声明的全部 `tool_calls` 与其结果构成一个 Tool Group；
   - Assistant Tool Call 后紧跟与该 Tool Group 对应的一个或多个 Tool Result；
   - 每个 Tool Call 有且只有一个匹配 `tool_call_id` 的 Tool Result；
   - 并发 Tool Result 按 Tool Call 原始顺序排列；
   - Completed Run 最后一条是普通 Assistant Message；
   - Cancelled Run 闭合后最后一条是普通 Assistant Message；
   - Failed Run 只有在检查点已经由普通 Assistant Message 闭合时才可提交；
   - 不包含 System Message、未完成 Assistant 或孤立 Tool Result。
   这里“不包含 System Message”只约束单个 Run 的增量；完整 Provider 请求仍必须且只能在首位包含
   当前 SessionRuntime 的稳定 System Prompt。
2. 在 `message_history.py` 增加纯函数，不持有 Blackboard 或 Runtime 状态：
   - 校验新的 Run 增量是否可重放；
   - 将 Cancelled/受控 Failed 安全前缀闭合为合法序列；
   - 为旧 Session 构造兼容的请求副本。
3. 新 Run 校验失败时返回明确错误，不对消息做猜测式修复，也不写入 Blackboard 权威历史。
4. 旧 Session 兼容修复只操作深拷贝：
   - 删除孤立 Tool Result；
   - 裁掉没有完整结果组的 Tool Call；
   - 删除空 Assistant；
   - 合并无法恢复 Task 边界的连续普通 User；
   - 丢弃最终仍以 User 结束的旧历史后缀；
   - 保持有效 Tool Group 的顺序和内容。
5. 兼容修复不得修改 `_messages`、Session State 文件或旧消息对象。旧版本已经丢失的 Tool 内容
   不尝试恢复。
6. 校验器按 Tool Group 检查协议，而不是禁止所有连续同角色消息：多个 Tool Result 可以连续出现；
   普通 Assistant 后不能出现 Tool Result；只有完成竞争中已接受的运行中 User 输入可以让一个无
   Tool Call 的 Assistant 后继续新的 Step。

### 验收

- 合法的完整 Run 原样通过；
- 缺失、重复、乱序和孤立 Tool Result 被确定性识别；
- 新 Run 的非法消息被拒绝，不产生部分提交；
- 旧历史修复结果可发送，原始历史保持字节级等价；
- 同一个输入多次修复得到相同结果。

## 任务二：恢复 Blackboard 完整 Run 历史

### 更新文件

- `apps/agent/src/agent_orchestration/plugins/blackboard/plugin.py`
- `apps/agent/test/agent_orchestration/plugins/blackboard/test_plugin.py`

### 开发内容

1. 恢复小型 `_commit_task_messages()`：
   - 输入是当前 Run 增量，不接收包含旧 History 或 System Prompt 的完整请求；
   - 校验成功后一次性追加到 `_messages`；
   - 使用 `history_committed` 保证重复终态幂等；
   - 任一校验失败时保持 `_messages` 不变。
2. `AgentCompletedEvent` 优先提交 `event.response.task_messages`，内容包括：
   - 当前 User Prompt 与图片；
   - 中间 Assistant；
   - Tool Call；
   - Tool Result；
   - 已应用 Runtime Context 和 Steer；
   - Final Assistant。
   `task_messages` 不重复携带 System Prompt；下一 Run 由 Blackboard 重新组装
   `system_prompt + 完整 _messages + current user`。
3. 兼容非 ReAct 测试替身或调用方：缺少 `task_messages` 时，只用 Blackboard 已生成的
   `input_prompt` 和明确的最终 Assistant 构造最小合法消息，并发布非致命诊断；生产 ReAct 路径不应
   进入该回退。
4. `BlackboardContextReadyEvent.history_messages` 不直接返回 `_messages`，而是返回任务一生成的兼容
   请求副本，避免旧 Session 的损坏记录继续污染 Provider 请求。Blackboard 调用 HistoryCompactor
   时也使用合法副本，不能把旧损坏历史直接发送给压缩模型。
5. `_context_tokens` 在提交后根据完整 `_messages` 重新估算；估算必须覆盖 Message Content、
   `tool_calls` 的名称与参数、`tool_call_id` 和图片占位，不能只统计文本 Content。现有 Session State
   Message Schema 已能序列化这些字段，不升级状态版本。

### 定向测试

- Completed Run 的完整 Tool 轨迹整体写入 Blackboard；
- 下一 Task 的 `history_messages` 与上一 Run 完整增量一致；
- 中间 Assistant、图片、Tool Call 参数和 Tool Result 内容 round trip；
- 重复 Completed Event 不重复追加；
- 非 ReAct 回退只在缺少 `task_messages` 时发生；
- 完整历史保存、恢复和再次发送不丢字段。

## 任务三：收敛 Cancelled 与 Failed 历史

### 更新文件

- `apps/agent/src/agent_orchestration/plugins/blackboard/message_history.py`
- `apps/agent/src/agent_orchestration/plugins/blackboard/plugin.py`
- `apps/agent/test/agent_orchestration/plugins/blackboard/test_message_history.py`
- `apps/agent/test/agent_orchestration/plugins/blackboard/test_plugin.py`

### 开发内容

1. `AgentCancelledEvent.task_messages` 继续使用 `TaskChannel.history_checkpoint`，不从正在流式输出的
   UI 消息或 Trace 反推历史。
2. 对非空安全前缀执行闭合：
   - 尾部为 User 或完整 Tool Result Group 时，追加
     `Message("assistant", [TextPart("Operation interrupted.")])`；
   - 尾部已经是普通 Assistant 时直接作为闭合，不追加第二条 Assistant；
   - 含未闭合 Tool Call、孤立 Tool Result 或空 Assistant 时拒绝提交。
3. Run 启动前取消没有 `task_messages`，不写入伪造历史。
4. Fatal `TaskErrorEvent` 只有明确携带 `task_messages` 时才提交：
   - 安全前缀还必须已经由普通 Assistant Message 闭合；
   - `max_steps_exceeded` 等受控截停如果结束于 Tool Result，本阶段不提交该 Task；
   - 普通不可恢复异常没有安全前缀时不提交；
   - 删除当前 `_last_visible_assistant()` 猜测逻辑。
5. 保持 `agent_finished + input_finished` 双终态清理和重复事件幂等，不改变 UserInputPlugin 的取消
   生命周期。

### 定向测试

- 第一次 LLM 中 Stop：User + `Operation interrupted.`；
- 完整 Tool Batch 后 Stop：完整 Tool Group + 中断消息；
- Tool Batch 执行中 Stop：只提交 Batch 之前的检查点；
- 已有普通 Assistant 尾部不产生连续 Assistant；
- Run 启动前 Stop 不提交；
- 未携带安全检查点的 Failed Run 不提交；
- `max_steps_exceeded` 只有检查点已由普通 Assistant 闭合时才被下一 Run 重放；
- Agent/Input 终态乱序不会漏提交或重复提交。

## 任务四：为 TaskChannel 增加用户 Steer

### 更新文件

- `apps/agent/src/agent_orchestration/run_control/types.py`
- `apps/agent/src/agent_orchestration/run_control/channel.py`
- `apps/agent/src/agent_orchestration/run_control/registry.py`
- `apps/agent/src/agent_orchestration/run_control/events.py`
- `apps/agent/src/agent_orchestration/run_control/__init__.py`
- `apps/agent/test/agent_orchestration/run_control/test_channel.py`

### 开发内容

1. 新增 `TaskSteerRequestedEvent(content, input_images, display_text)`，复用现有 Event 身份字段。Steer 请求只走应用层直接
   调用，不发布到 EventBus，因此不新增 `TaskSteerResultEvent`。
2. 将当前运行中记录扩展为带类型的记录，类型固定为：
   - `context`：Plugin Runtime Context；
   - `user_correction`：用户 Steer。
3. 保持一个 FIFO 和一把锁，不为 Steer 创建第二条队列或新的状态机。`add_context()` 行为保持兼容，
   新增 `add_steer()` 使用同一接受窗口。
4. 一个安全点内的记录被组装为一条 User Message：
   - 所有 Runtime Context 按接收顺序放入 `<runtime_context>`；
   - 所有用户纠偏按接收顺序放入 `<user_correction>`；
   - Context 在前、Steer 在后。
5. Stop 原子关闭接受窗口，并把仍在 FIFO 中的记录归入 discarded：
   - 未应用 Steer 记录 `discarded_by_stop`；
   - 已经 drain 的 Steer 属于 Applied Batch，不得删除；
   - 不把 discarded 内容放入 `history_checkpoint`。
   - TaskChannel 只读暴露本次取消丢弃的记录，由 AgentPlugin 在 Run 收束时统一写 Trace，避免
     TaskChannel 依赖 Hook。
6. `steer_task()` 的同步结果只表达 `accepted`、`already_finished`、`already_cancelling`、`not_found`
   或 `invalid_content`；`applied` 与 `discarded_by_stop` 是 Hook/Trace 中的后续处置，不扩展
   `TaskOperationStatus` 为异步终态。

### 定向测试

- Context 与 Steer 共用 FIFO，但格式和顺序不同；
- 多条 Steer 不丢失且保持到达顺序；
- ACCEPTED、PREPARING_CONTEXT 和 RUNNING 阶段均可接受；
- CANCELLING 和已结束 Task 明确拒绝；
- Stop 丢弃未应用 Steer；
- 已 drain 的 Steer 保留在 Applied Batch 和安全检查点；
- `close_or_drain` 与 Steer 的并发竞争只有“接受并继续”或“关闭并拒绝”两种结果。

## 任务五：在 ReAct 和 AgentPlugin 中应用 Steer

### 更新文件

- `apps/agent/src/agent_orchestration/capability/react_agent.py`
- `apps/agent/src/agent_orchestration/plugins/agent/plugin.py`
- `apps/agent/test/agent_orchestration/capability/test_react_agent.py`
- `apps/agent/test/agent_orchestration/capability/test_react_agent_stream.py`
- `apps/agent/test/agent_orchestration/plugins/agent/test_plugin.py`
- `apps/agent/test/agent_orchestration/plugins/persistence/test_trace_integration.py`

### 开发内容

1. AgentPlugin 将 `TaskSteerRequestedEvent` 纳入现有统一 Task 操作入口，并路由到
   `TaskChannelRegistry.add_steer()`；应用层直接调用返回 `TaskOperationResult`。首期不将用户 Steer
   加入 AgentPlugin Manifest，Plugin 仍通过 `TaskContextInputEvent` 提供普通 Runtime Context。
2. ReActAgent 继续通过四个公开入口共享的 `_prepare_step()` 和 `_close_or_continue()` 应用输入：
   - Step 1 前到达的 Context 合并到当前 User Message 的 `<user_request>` 之前；
   - Step 1 前到达的 Steer 合并到当前 User Message 的 `<user_request>` 之后；
   - 后续安全点将 Context 和 Steer 作为一条新的 User Message追加；
   - Tool Call 与对应 Tool Result 之间不注入，只有整个 Tool Group 闭合后才注入；
   - 完成竞争中有已接受输入时增加一个 LLM Step。
3. 首个 Step 的合并不解析或重建 Blackboard Prompt：Runtime Context 作为已有 `input_prompt` 前缀，
   Steer 作为后缀；原始图片保持原位置，Steer 图片追加到同一条 User Message。
4. 同步、异步、流式、异步流式入口不得各自实现一套 Steer 逻辑。
5. 扩展现有操作 Trace：
   - 请求接受记录 `operation=steer, status=accepted`；
   - 实际 drain 后记录 `task.steer/applied` 和 `applied_before_step`；
   - Stop 清理未应用内容时记录 `task.steer/discarded_by_stop`；
   - Trace 不改变 Agent 主流程。
6. 保持 Hard Stop 优先：进入 CANCELLING 后不再接受 Steer，且 Stop 不被转换为模型消息。

### 定向测试

- 四个 Agent 入口的 Steer 行为一致；
- 首个 Step 到达的 Runtime Context/Steer 合并进当前 User Message，不额外追加第二条当前 Run User；
- Tool Group 后得到 `assistant(tool_calls) -> tool... -> user(correction)`；普通的无 Tool Call
  Assistant 不得直接跟 Tool Result；
- 同一边界的 Context 在前、Steer 在后；
- Final Assistant 与 Steer 竞争时，Steer 被接受则追加 Step，否则返回已结束；
- Tool Group 执行期间到达的 Steer 不强杀 Tool；
- Stop 与 Steer 竞争符合 TaskChannel 原子结果；
- 完成后的 `AgentResponse.task_messages` 包含已应用 Steer；
- Trace 能区分 accepted、applied 和 discarded_by_stop。

## 任务六：增加 Agent 应用层 Steer 接口

### 更新文件

- `apps/agent/src/application/session_runtime.py`
- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/test/application/test_session_runtime.py`
- `apps/agent/test/application/test_agent_runtime.py`

### 开发内容

1. `SessionRuntime.steer_task(task_id, content, input_images, display_text)`：
   - Runtime 未启动时返回 `not_running`；
   - 在现有 Task Hook Context 中直接调用 AgentPlugin 的统一操作处理；
   - source 固定为应用层用户来源，不发布 EventBus Result Event。
2. `AgentRuntime.steer_task(workspace_path, session_id, task_id, content, resources, display_text)`：
   - 复用 `cancel_task()` 的 Session 定位、mutation lock 和生命周期检查；
   - 不自动恢复已卸载 Session；
   - 不创建新 Task；
   - `accepted` 时只更新活动时间，不写入新的 SessionStore 业务记录。
3. Gateway 增加 `session.steer`，复用 `session.submit` 的 ResourceRef 校验与 Session assets 导入链路。
4. TUI 运行中提交走 `session.steer`，空闲提交继续走 `session.submit`；Steer 被拒绝或调用失败时，
   完整输入保留在现有本地队列，待当前 Task 结束后作为新 Task 提交。
5. 只有安全点真正应用的 Steer 才发布 `user.correction` RuntimeUpdate；TUI 用它展示纠偏并支持
   Session 恢复。

### 定向测试

- 活动 Task 接受 Steer，并保持原 `task_id`、`run_id`；
- 未加载 Session 返回 `not_running`；
- 不存在 Task 返回 `not_found`；
- 已结束 Task 返回 `already_finished`；
- 正在取消返回 `already_cancelling`；
- 空内容返回 `invalid_content`；
- Agent 层 Steer 不创建 Task，也不操作调用方的本地输入队列。
- 文本和图片 Steer 均进入当前 Run；图片在 accepted 前不删除临时文件；已结束 Task 回退为下一条输入。

## 任务七：文档同步与最终验证

### 更新文件

- `apps/agent/docs/arch/plugin-eventbus-blackboard-design.md`
- `apps/agent/docs/arch/agent-run-intervention-design.md`
- `apps/agent/docs/arch/plugin-event-flow-current-state.md`
- `apps/agent/docs/arch/memory-knowledge-plugin-design.md`
- `apps/agent/docs/plan/memory-knowledge-plugin-development-plan.md`
- `docs/todo/agent-core.md`
- 必要时更新其他直接声明 Product Conversation 精简投影的 Agent 文档

### 开发内容

1. 将与本设计冲突的“只保存 User + Final Assistant”改为完整 Agent Run 历史。
2. 保留 Blackboard Region、Memory、Knowledge 的职责边界，不借历史修复改变 Plugin 业务设计。
3. 明确已应用 Runtime Context 会跨 Run 重放，因此后续 Memory 召回去重和 Context Budget 是独立
   后续项，不在本次顺带实现。
4. 更新 `docs/todo/agent-core.md` 的实施顺序：
   - 完整历史与消息闭合；
   - Agent 层 Steer；
   - Tool Result Budget；
   - Request Assembler / Wire Budget；
   - Redirect、HITL 和长会话 Replay。
5. 不把历史方案改写成新 Turn、双历史或数据库事件溯源系统。

### 验证命令

按以下顺序执行，失败时只修复本功能影响范围：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/blackboard/test_message_history.py \
  apps/agent/test/agent_orchestration/plugins/blackboard/test_plugin.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/run_control/test_channel.py \
  apps/agent/test/agent_orchestration/capability/test_react_agent.py \
  apps/agent/test/agent_orchestration/capability/test_react_agent_stream.py \
  apps/agent/test/agent_orchestration/plugins/agent/test_plugin.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/application/test_session_runtime.py \
  apps/agent/test/application/test_agent_runtime.py \
  apps/agent/test/agent_orchestration/plugins/persistence/test_trace_integration.py -q

make test-agent
apps/agent/.venv/bin/python -m compileall -q apps/agent/src
git diff --check
```

## 完成标准

- Blackboard 是唯一的跨 Run 完整消息历史所有者；
- Completed、Cancelled 和带安全检查点的 Failed Run 都以合法 Message 序列提交；
- 下一 Run 可以看到上一 Run 的 Tool Call、Tool Result、中间 Assistant 和已应用 Steer；
- 新 Run 不会写入孤立 User、孤立 Tool Result、未闭合 Tool Call 或空最终 Assistant；
- 用户 Steer 在同一 Task、同一 Agent Run 内生效，不中断 Tool Batch；
- Stop 丢弃未应用 Steer，保留已应用 Steer 和已闭合 Tool Group；
- 四种 Agent 调用入口行为一致；
- Gateway、TUI、Provider、Tool 和数据库 Schema 没有被修改；
- 定向测试、`make test-agent`、compileall 和 `git diff --check` 全部通过；
- 文档不再同时声明两套相反的跨 Run 历史模型。
