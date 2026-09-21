# TUI Run Card, Unified Queue, and Command Registry Implementation Plan｜TUI 运行卡片、统一队列与命令注册实施计划

## 目标

基于以下已确认设计，完成 TUI 现有功能迁移与新增输出体验：

- [跨应用规格](../../../../../spec/2026-09-21-agent-thinking-experience.md)；
- [TUI 设计](arch.md)；
- [Gateway 协议设计](../../../../gateway/docs/spec/2026-09-21-agent-thinking-experience/arch.md)。

完成后：

- thinking、Tool 和运行中追加内容聚合在 Task RunCard；
- 最终 Assistant Markdown 独立显示；
- 所有普通输入先进入同一个队列，出队时动态选择 steer 或 submit；
- FIFO 发送、LIFO 撤回、附件和 `Ctrl+C` 对两种路由保持一致；
- `/clear`、`/resume`、`/exit` 通过统一 CommandRegistry 执行；
- `/compact`、`/btw` 只拥有未来可用的注册扩展点，本次不实现、不注册。

## 实施原则

- 先迁移纯状态和命令解析，再接入 App；避免同时改状态机和 Widget 后难以定位回归。
- RuntimeUpdate 先投影为 UiAction，Widget 不直接解析 Gateway payload。
- 队列项不保存 submit/steer intent；每次 reservation 才根据当前 Runtime 状态决定路由。
- 发送成功前不删除队首和临时附件。
- 先保证确定性 Widget 测试，再更新视觉快照。
- 不增加 Gateway RPC、Agent 依赖或本地数据库访问。

## 依赖顺序

```text
ChatState dispatch reservation + CommandRegistry
→ UiAction / Projector
→ RunCard / ThinkingBlock / ToolBlock
→ App unified dispatch and history context
→ Replay / snapshots / full regression
```

纯状态与 Projector 可以在 Gateway 协议模型固定后先实现。App 集成必须等新的 Widget 与 Action 已有定向
测试，避免让 `app.py` 临时承担展示逻辑。

## 阶段一：把 ChatState 改为动态 Dispatch Reservation

### 更新文件

- `apps/tui/src/chat_state.py`
- `apps/tui/test/test_chat_state.py`

### 开发内容

1. 增加：

   ```python
   DispatchMode = Literal["submit", "steer"]

   @dataclass(frozen=True)
   class DispatchReservation:
       message: PendingMessage
       mode: DispatchMode
       task_id: str | None
       attempt_epoch: int
       outcome_unknown: bool = False
   ```

2. 用 `dispatch_reservation: DispatchReservation | None` 取代可写的单独 bool，并提供只读兼容 property
   `dispatch_in_progress`。App 和测试不能直接修改内部 reservation；状态变化通过 ChatState 方法替换这个
   不可变值。
3. `can_dispatch` 改为：
   - pending 非空；
   - 没有 reservation；
   - 队首没有被确定性拒绝标记阻塞；
   - READY 且无 active task，或 RUNNING 且有 active task；
   - STARTING、CANCELLING、SWITCHING、STOPPING、FAILED 均 false。
4. `begin_dispatch(connection_epoch)` 返回固定 reservation：
   - READY → `submit`, task_id=None；
   - RUNNING → `steer`, task_id=当前 active task；
   - 两种模式都把当前连接代次写入 `attempt_epoch`。
5. 把成功处理拆开：
   - `accept_submit(reservation, task_id)`：校验 reservation/队首，popleft，设置 active task 和 RUNNING；
   - `accept_steer(reservation)`：校验 reservation 与队首后 popleft；不得要求响应到达时
     `active_task_id` 仍等于 captured task，因为 `task.finished` 可能先到；若 active task 仍存在则保持
     RUNNING，若已经结束则保持 READY；
   - 两者返回已接受 `PendingMessage`，供 App 删除临时附件。
6. 增加 `release_dispatch(reservation, fatal=False)`：
   - 普通 late steer / 确定性拒绝只释放 reservation，保留队首；
   - 只有 submission conflict、未知协议状态或内部一致性损坏才允许 `fatal=True` 并进入 FAILED；
   - stale reservation 或队首变化直接报编程错误。
7. 增加 `mark_dispatch_outcome_unknown(reservation)` 和
   `begin_dispatch_retry(reservation, connection_epoch)`：前者保留并锁定原 reservation；后者只允许严格
   更新的连接代次，原子更新 `attempt_epoch` 并清除 unknown 标记，以同一种 RPC、目标 task_id、payload
   和 submission_id 精确重试。
8. 增加 `block_dispatch(reservation, reason)`：释放 reservation 并记录被拒绝的队首 ID；该项被撤回或
   编辑后重新提交时清除阻塞，后续项不得越过它。
9. `finish_active()` 可以在 steer await 期间把 phase 设为 READY，但不能清除正在进行的 reservation。
10. `interrupt_action()` 在 reservation 锁住唯一队首时返回 `NOTIFY_CANCEL_UNAVAILABLE`；存在其他队尾
   项时仍可 LIFO 撤回未锁定队尾。
11. `can_run_session_command` 继续要求 idle、队列空、无 reservation。

### 定向测试

- STARTING 可入队但不能 reserve。
- READY reserve submit，RUNNING reserve steer 并捕获 task_id。
- 第二次 begin 不产生并发 reservation。
- submit/steer 在 accepted 前都保留队首。
- accepted submit 设置 active task；accepted steer 保持 active task。
- finish 在 steer await 期间先到达不会删除 reservation 或消息。
- late steer release 后下一次在 READY reserve submit。
- RPC 结果不确定时保留完整文本、图片、submission_id 和原路由，状态变化也不重新 reserve。
- 每个连接 epoch 最多重试一次；stale RPC 结果不能覆盖新 epoch reservation，重连先完成、旧 RPC 后失败
  也会立即触发精确重试而不会永久等待下一次重连。
- 被拒绝队首会阻塞后续 FIFO 项；撤回并编辑后解除阻塞，不形成自动重试循环。
- FIFO 连续 steer 和 submit；LIFO 撤回不碰锁定队首。
- Session command 门禁覆盖两种 dispatch。

### 验证命令

```bash
apps/tui/.venv/bin/python -m pytest apps/tui/test/test_chat_state.py -q
```

## 阶段二：迁移三个现有命令到 CommandRegistry

### 更新文件

- `apps/tui/src/commands.py`
- `apps/tui/src/app.py`
- `apps/tui/test/test_commands.py`
- `apps/tui/test/test_app.py`

### 开发内容

1. 在 `commands.py` 增加不可变 `CommandInvocation`、`CommandDefinition` 和 `CommandRegistry`。
2. 注册表负责：
   - 校验并保存唯一 `/name`；
   - 解析去除首尾空白后的命令名与原始 arguments；
   - 大小写不敏感地 resolve；
   - 暴露稳定帮助元数据。
3. 提供 `create_default_command_registry()`，只注册：
   - `/clear` → `clear_session`；
   - `/resume` → `resume_session`；
   - `/exit` → `exit_app`。
4. 三个 Definition 均不接受参数和附件；`/clear`、`/resume` 标记需要 idle Session，`/exit` 只要求
   App 仍接受输入且未进入 stopping。
5. App 初始化时接受可注入 registry，默认使用上述 factory，方便纯测试和未来注册扩展。
6. Composer handler 在普通入队前：
   - 输入以 `/` 开头时调用 registry parse/resolve；
   - 未知命令显示 `Unknown command: /name`；
   - 未知命令、参数/附件非法、idle 门禁失败时都恢复完整 draft（含附件与原 submission ID）并显示
     warning；Composer 发出 Submitted 时已经清空，不能依赖 Widget 自动保留；
   - 合法命令映射到现有 `_clear_session`、`_resume_session` 和 shutdown 路径。
7. 删除 App 对裸 `exit` / `quit` 的特殊判断；它们进入普通队列。
8. 删除旧 `LocalCommand` Literal 和二分支 parser，但保留必要的兼容 re-export 只到同一提交内；最终源码
   和测试全部迁移到 registry。
9. 不添加 `/compact`、`/btw` Definition、handler 或帮助文案。

### 定向测试

- 三个命令唯一注册且可枚举帮助信息。
- 名称大小写不敏感，arguments 保留原文。
- 重复、空名称、缺少 `/` 和空 handler 注册失败。
- `/clear extra`、`/exit now` 给出用法错误且不入队。
- `/unknown`、`/` 给出 Unknown command 且不入队。
- 带附件命令恢复 draft 和附件。
- 未知命令、参数错误、附件错误和 idle 门禁失败均恢复原文本、附件与 submission ID。
- `/clear`、`/resume` 保留当前 idle 门禁，门禁失败不丢 draft。
- `/exit` 走正常 shutdown。
- `exit`、`quit`、`please /clear` 作为普通 PendingMessage。
- `/compact`、`/btw` 当前是 unknown，而不是空操作。

### 验证命令

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/test_commands.py \
  apps/tui/test/test_app.py -q
```

## 阶段三：扩展 UiAction 与 Projector

### 更新文件

- `apps/tui/src/event_pipeline/actions.py`
- `apps/tui/src/event_pipeline/__init__.py`
- `apps/tui/src/event_pipeline/dispatcher.py`
- `apps/tui/src/event_pipeline/projectors/agent.py`
- `apps/tui/src/event_pipeline/projectors/user_input.py`
- `apps/tui/src/app.py`
- `apps/tui/test/event_pipeline/test_dispatcher.py`
- `apps/tui/test/event_pipeline/projectors/test_agent.py`
- `apps/tui/test/event_pipeline/projectors/test_blackboard.py`
- `apps/tui/test/event_pipeline/projectors/test_user_input.py`

### 开发内容

1. 增加并导出：
   - `AppendThinkingDelta`；
   - `CompleteThinking`；
   - `AppendUserCorrection`。
2. 给现有 `AppendAssistantDelta`、`CompleteAssistantMessage` 补上 payload 已有的 `step`，用于区分
   卡片内中间输出与最终候选；扩展 `UpdateToolCompleted`，加入 preview、truncation、
   `full_result_available` 和 preview error 字段；
   字段使用兼容默认值。
3. 把 `UpdateProjector.project()` 扩展为 `project(update, *, historical=False)`，由 Registry、
   `AgentProjector`、`UserInputProjector`、`BlackboardProjector` 及测试 stub 统一接受显式历史上下文；
   Registry 调用任何 projector 时都必须透传，不能只修改 Protocol。
4. App 调用 Registry 时传入现有 `historical` 参数；Projector 不通过 sequence 猜测历史。该签名迁移在
   本阶段完成，阶段六只验证恢复行为，不允许中间提交留下无法运行的 Registry/App 调用组合。
5. AgentProjector：
   - 映射 `assistant.thinking_delta` 和 `assistant.thinking`；
   - 校验 step、text、partial；
   - Tool started arguments 继续稳定 JSON 格式化；
   - Tool completed 读取新增字段，并兼容旧 payload 缺失字段。
6. UserInputProjector：
   - `user.message` 继续映射 `AppendUserMessage`；
   - `user.correction` 单独映射 `AppendUserCorrection`，保留 `applied_before_step`；
   - lifecycle 逻辑保持不变。
7. 默认 Projector Registry 注册两个新增 update type。

### 定向测试

- delta、完整、partial 和 historical thinking 的精确 Action。
- 空文本、非法 step/partial 走当前投影错误边界。
- correction 不再等于顶层 user message。
- Assistant delta/complete action 保留合法 step，非法 step 进入投影错误边界。
- Tool preview 覆盖所有 JSON 类型、截断、完整结果可用性、preview error 和旧 payload fallback。
- `ProjectorRegistry.update_types` 包含两个 thinking type。
- 未知 update 仍忽略并计数。

### 验证命令

```bash
apps/tui/.venv/bin/python -m pytest apps/tui/test/event_pipeline -q
```

## 阶段四：实现 RunCard、ThinkingBlock 与增强 ToolBlock

### 更新文件

- `apps/tui/src/widgets/messages.py`
- `apps/tui/src/widgets/conversation.py`
- `apps/tui/src/widgets/__init__.py`
- `apps/tui/src/styles.tcss`
- `apps/tui/test/widgets/test_conversation.py`

### 开发内容

1. 在 `messages.py` 增加：
   - `RunCard(task_id)`；
   - `ThinkingBlock(step)`；
   - `AssistantProgressBlock(step)`，可复用现有 Markdown 流组件；
   - `UserCorrectionBlock`；
   - 把内部 `ToolMessage` 重命名并重构为可折叠 `ToolBlock`，同步更新仓库内引用，不保留第二个兼容类。
2. 折叠组件使用 Textual 可聚焦控件或明确键盘 action：
   - 摘要包含 `▸` / `▾` 和文字状态；
   - Enter/Space 仅在摘要拥有焦点时切换；Composer 焦点下仍由 PersistentComposer 处理输入；
   - 点击摘要同样切换；
   - 不注册与应用级 `Ctrl+C` 冲突的 Widget binding；
   - 折叠不销毁正文。
3. ThinkingBlock 支持：
   - 流式 append；
   - complete text 对账；
   - partial 状态；
   - `expanded` 切换；
   - finish stream 时停止 Markdown stream。
4. ToolBlock 支持：
   - arguments 格式化 JSON；
   - object/list pretty JSON；
   - string 等宽代码块；
   - scalar 与 null；
   - 常见路径字段摘要；
   - success/failure/interrupted/preview unavailable；
   - 截断和 Agent-side 完整结果提示，不展示本地路径。
5. RunCard 维护自己的 thinking key、Tool call_id 和 correction 顺序；`ConversationView` 的全局 Tool
   索引使用 `(task_id, call_id)`，避免历史中不同 Task 的相同 call_id 互相覆盖。组件公开语义方法而不是
   让 `ConversationView` 操作内部子 Widget。
6. `ConversationView` 改为按 task_id 获取/创建 RunCard，并处理新增 Action：
   - thinking delta/complete；
   - Tool start/complete；
   - correction；
   - FinishTurn 保留 thinking 当前展开状态、收束未完成 Tool。
7. Assistant 文本先按 `(task_id, step)` 在 RunCard 内作为候选流式渲染：同 step Tool 或更大 step 到达时
   固化为中间 `AssistantProgressBlock`；成功 FinishTurn 把最后一个已 complete、未被后续 step/Tool 消费
   的候选原子提升为 RunCard 后的独立 `AssistantMessage`。失败、取消或中断不提升 partial 候选；不能
   通过假设 Tool step 没有文本来规避顺序问题。提升后 RunCard 若没有其他子项则移除空卡片。
8. reset/session switch 清空 RunCard、thinking completed keys、Tool map 和 streaming handle。
9. 更新 TCSS：
   - 单一 RunCard 边界；
   - 弱化 thinking；
   - Tool/Correction 层级；
   - success/failure 不只依赖颜色；
   - 窄终端不横向溢出。

### 定向 Widget 测试

- 实时与历史 thinking 默认展开并支持流式追加。
- 下一 thinking、Tool 或 Task terminal 不自动折叠已有 thinking。
- 完整记录对账替换 delta，不重复创建。
- completed key 之后的迟到 delta 被忽略。
- historical thinking 默认展开，手动可折叠和重新展开。
- 完整记录对账保留用户手动选择的展开状态。
- correction 在正确 RunCard，不产生 UserMessage。
- Tool step 带可见 assistant text 时，中间文本和 Tool 在同一卡片内保持先后顺序。
- 成功 Task 的 final AssistantMessage 从最后候选提升，位于 RunCard 之后、文本不重复且保持独立。
- 只有最终文本、没有中间过程的 Task 不留下空 RunCard。
- failed/cancelled/interrupted 的未完成候选留在 RunCard，不误标为最终回答。
- Tool JSON/text/scalar/path-summary/failure/truncation/full-result 提示显示正确。
- 只有 Tool completion 的恢复历史也能创建 ToolBlock。
- 两个 Task 使用相同 call_id 时分别更新各自 RunCard，不串块。
- 截断预览分别覆盖“完整结果可用”和“完整结果不可用”两种提示，不能暗示不存在的完整结果。
- reset 后旧 task_id/call_id/step 不影响新 Session。
- 流式更新和折叠不改变 Composer focus。
- 中英文混排段落按终端 cell 宽度折行，不在 `18 篇`、`4 千字` 等空格边界留下大段空白。
- 摘要焦点下 Enter/Space 可切换；Composer 中 Enter/Shift+Enter 与全局 Ctrl+C 行为不回归。

### 验证命令

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/widgets/test_conversation.py -q
```

## 阶段五：把 App 的 Submit 与 Steer 合并为统一 Dispatch

### 更新文件

- `apps/tui/src/app.py`
- `apps/tui/src/gateway_client/client.py`
- `apps/tui/src/replay.py`
- `apps/tui/test/test_app.py`
- `apps/tui/test/test_app_snapshots.py`
- `apps/tui/test/gateway_client/test_client.py`
- `apps/tui/test/test_replay.py`

### 开发内容

1. Composer handler 对普通输入只执行 enqueue、刷新 QueuePanel、调度；删除 RUNNING 直接
   `_steer_active_task()` 分支。
2. 把 `_dispatch_next()` 改为消费 `DispatchReservation`：
   - submit reservation 调用现有 `service.submit()`；
   - steer reservation 调用现有 `service.steer_task(captured_task_id, ...)`；
   - 提取 `_send_reservation(reservation)` 供初次发送和重连精确重试共用；重试不得再次调用
     `begin_dispatch()` 或重新判断 submit/steer。
3. submit accepted：
   - 调用 `accept_submit()`；
   - 删除已接受项的 owned temp image；
   - flush early updates；
   - reconcile task status。
4. steer accepted：
   - 调用 `accept_steer()`；
   - 删除已接受项的 owned temp image；
   - active task 保持不变；
   - 立即 schedule 下一队首。
5. steer 返回状态按语义分类：
   - `already_finished` / `not_found` / `not_running`：release，保留队首，对账 captured task；READY 后
     重新 reserve 为 submit；
   - `already_cancelling`：release，保留队首并等待终态；
   - `invalid_content` / `expired`：release，保留队首并暂停自动调度，显示可编辑错误；
   - 未知 status：按协议错误进入可见 fatal 边界，不能猜测为 accepted 或自动 submit。
6. App 维护单调递增的 `connection_epoch`，初始连接及每次重连成功后递增。Gateway 返回的结构化业务
   错误是权威结果，按第 5、8 条分类；只有 transport 断开等无法确定服务端结果的异常才调用
   `mark_dispatch_outcome_unknown()`，保留原 reservation、队首和附件。连接恢复后
   先用相同 mode、task_id、submission_id 和 payload 重试原请求；得到权威结果后才 accept 或 release，
   不能因 Task 已结束而提前改走 submit。若重连已先完成、旧 RPC 后抛错，检测 epoch 已推进后立即安排
   重试；每个 epoch 最多一次，过期尝试的晚到结果不得修改当前 reservation。
   RPC transport failure 与 `RuntimeSubscriptionFailed` 共用幂等 reconnect 调度，最多一个 reconnect worker；
   前者不能被动等待后者必然到达，后者也不能和请求失败并发调用 `service.reconnect()`。
7. GatewayClient 与 App `RuntimeClient` 的 `steer_task()` 增加必传 `submission_id`，并在请求中发送。
   GatewayClient 增加可识别的 transport error；`reconnect()` / 旧 reader 退出时结束属于旧连接的全部
   pending RPC future，再连接和订阅新 socket，不能留下悬挂 await，也不能误伤新连接请求。正常
   `close()` / App stopping 取消不启动自动重试。
   同步更新 `ReplayRuntimeService`、App/Snapshot 测试替身及其断言；所有实现都显式接收该参数，不能
   依赖宽泛 `**kwargs` 掩盖接口漂移。
8. submit/steer 的 `invalid_resource` / `resource_unavailable` 与 steer 的 `invalid_content` /
   `expired` 使用同一可编辑拒绝语义：调用 `block_dispatch()`，保留队首和附件、暂停自动调度且不把
   整个 Runtime 标成 FAILED。`submission_conflict`、未知 status 或无法分类的协议错误视为客户端状态
   损坏，进入可见 fatal 边界；按 `GatewayClientError.code` 与 transport exception 明确分类，不依赖错误
   文案匹配。
9. `_schedule_dispatch()` 只依赖新的 `can_dispatch`，因此 RUNNING 时也可调度；outcome unknown 时
   `can_dispatch=false`。
10. FinishTurn 在任何 reservation 状态下先更新 active task；若存在 outcome-unknown reservation，不调度
    下一项。
11. Ctrl+C 根据 reservation 锁定规则恢复未发送队尾；发送中唯一队首只提示，不伪装成撤回成功。
12. `_delete_submission_images()` 只在 submit/steer accepted 后调用；shutdown 清理继续覆盖剩余临时附件。

### 竞争场景测试

- RUNNING 输入立即进入 QueuePanel，然后才发 steer。
- 两条 running 输入严格串行 steer，保持 FIFO。
- steer accepted 后公共 `user.correction` 到达并进入 RunCard。
- steer await 期间先收到 FinishTurn，再返回 accepted：消息只被消费一次，不 submit。
- steer await 期间先收到 FinishTurn，再返回 already_finished：保留队首并最终 submit。
- Task 在消息尚未 reserve 前完成：首次 dispatch 直接 submit。
- steer RPC 响应丢失后 Task 先结束：重连仍先以同 ID 重试原 steer；cached accepted 只消费一次，
  明确 rejected 才降级 submit。
- submit RPC 响应丢失后重连以同 ID 重试原 submit，不创建第二个 Task。
- 重连完成早于旧 RPC 失败时仍立即重试；旧 epoch 的晚到异常/响应不覆盖新尝试结果。
- RPC 与订阅同时报告断线时只启动一个 reconnect；仅 RPC 报告 transport failure 时也会主动恢复连接。
- reconnect 主动取消旧 reader 时，旧 pending RPC 及时以 transport error 完成且从 pending map 清理；
  新连接请求不被旧 reader 的晚到清理误伤。
- `already_cancelling` 等待终态；`invalid_content` / `expired` 暂停自动重试且可 Ctrl+C 撤回。
- submit/steer 的 invalid/unavailable resource 同样阻塞队首、允许撤回编辑且不把 Runtime 标成 FAILED。
- 确定性拒绝的队首阻塞后续 FIFO 项；撤回并编辑该项后可恢复调度。
- 未知 steer status 和 submission conflict 不进入自动重试循环。
- accepted steer 删除 owned temp image；rejected steer 直到新 submit accepted 才删除。
- submit/steer 都不会并发执行第二个 reservation。
- Ctrl+C 可撤回未锁定队尾，不能撤回发送中的唯一队首。
- reconnect/Session switch 不带走旧 Session 的 pending queue；Session command 门禁保证切换前队列为空。

### 验证命令

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/test_chat_state.py \
  apps/tui/test/test_app.py \
  apps/tui/test/widgets/test_queue_panel.py -q
```

## 阶段六：接入历史上下文与 Thinking 对账

### 更新文件

- `apps/tui/src/app.py`
- `apps/tui/src/event_pipeline/dispatcher.py`
- `apps/tui/src/widgets/conversation.py`
- `apps/tui/test/test_app.py`
- `apps/tui/test/widgets/test_conversation.py`

### 开发内容

1. `_project_runtime_update(update, historical=...)` 显式把 historical 传给 Registry。
2. 把 `_completed_thinking_steps` 所有权放在 ConversationView；App 不保留第二份集合，因为该状态只
   影响展示去重。
3. 对账规则：
   - delta 更新临时 block；
   - complete 替换同 key 文本并标记 completed；
   - completed 后迟到 delta 忽略；
   - sequence 仍只由 App 更新 `_last_sequence`。
4. Reconnect：先恢复 `after_sequence` 之后的完整记录，再重新挂实时 worker；不请求 delta 补发。
5. History restore：所有 thinking 默认展开；实时和历史 complete 都保留当前 block 展开状态。
6. Session activation/reset 清空对账集合和 RunCard map。
7. 保留 Assistant `_completed_assistant_steps` 的现有去重行为，不把 thinking 与 Assistant key 混用。
8. 历史恢复使用与实时相同的 Assistant candidate 固化/提升状态机；不能在 historical 分支另写一套
   “所有 assistant.message 都是最终回答”的规则。

### 定向测试

- 断线前收到 delta，重连后完整 thinking 替换临时 block。
- 历史 complete 先到、迟到实时 delta 后到时不重复。
- 多 task 相同 step 不冲突。
- Tool step 含文本、无 Tool 的中间 continuation step、最终成功 step 三种 Assistant candidate 均正确归类。
- 历史恢复与实时路径对同一事件序列产生相同的 RunCard/最终消息结构。
- Session switch 后相同 task/step 可在新 Session 正确创建。
- sequence gap 规则对持久化 thinking 生效，对 delta 不生效。
- history cursor 只随有 sequence update 推进。

### 验证命令

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/test_app.py \
  apps/tui/test/widgets/test_conversation.py -q
```

## 阶段七：更新 Replay、Golden 与视觉快照

### 更新文件

- `apps/tui/test/fixtures/synthetic_tui_events.jsonl`
- `apps/tui/test/golden/synthetic_tui_transcript.txt`
- `apps/tui/test/test_replay.py`
- `apps/tui/test/test_timeline_transcript_golden.py`
- `apps/tui/test/test_app_snapshots.py`
- `apps/tui/test/__snapshots__/test_app_snapshots/*.svg` 中受影响的已跟踪快照

### 开发内容

1. 把 fixture 扩展为：
   - thinking delta + complete；
   - Tool success preview；
   - Tool failure error；
   - user correction；
   - final Assistant；
   - unrelated task update。
2. Replay 文本输出应表达 thinking/Tool/correction 的层级，不依赖 Textual 颜色。
3. 增加或更新快照场景：
   - 所有 thinking 默认展开，Tool 开始后保持展开；
   - Tool JSON/text preview 和截断提示；
   - correction 位于 RunCard；
   - 历史恢复全部 thinking 展开；
   - 窄终端运行卡片；
   - 运行中队列与 Composer 同时可见。
4. 只在断言确认结构正确后更新快照；先人工查看 SVG diff，不能直接接受大面积无关布局变化。
5. `.playwright-mcp/` 和 `.superpowers/brainstorm/` 不属于 TUI 测试资产，不加入提交。

### 验证命令

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/test_replay.py \
  apps/tui/test/test_timeline_transcript_golden.py \
  apps/tui/test/test_app_snapshots.py -q
```

## 阶段八：文档和全量验证

### 更新文件

- `README.md`
- `apps/tui/README.md`
- `apps/tui/docs/spec/2026-08-19-tui-persistent-input-queue/arch.md`
- `docs/todo/tui.md`
- 本功能 `arch.md` 与 `plan.md`，仅在实现发现事实偏差时修正

### 文档内容

- 列出 `/clear`、`/resume`、`/exit`。
- 明确裸 `exit` / `quit` 是普通消息。
- 描述运行中输入会排队，并由当前状态决定追加或下一轮。
- 简述 RunCard、thinking 折叠和 Tool preview。
- 在旧持久队列设计顶部标明由本设计替代的等待下一轮、直接 steer 和裸 exit/quit 行为，保留仍有效的
  Composer、FIFO/LIFO、附件与 Ctrl+C 约束。
- 在根 README 的当前能力与交互说明中同步 thinking、RunCard、统一队列和 `/exit`。
- 在 `docs/todo/tui.md` 只把已经落地且验证通过的本功能标为完成，并链接本 spec。
- 不宣传未实现的 `/compact`、`/btw`。

### 验证命令

```bash
make test-tui
git diff --check
```

跨应用完成后：

```bash
make test
```

### 手工闭环

```text
启动 TUI
→ 发起一个会产生 thinking 和 Tool 的任务
→ thinking 流式显示
→ Tool 开始后上一 thinking 保持展开，仍可手动折叠
→ 运行中连续输入两条补充消息
→ QueuePanel 先显示，再依次 steer
→ Task 在剩余消息发送前结束时，剩余消息自动成为新 submit
→ /resume 恢复历史，thinking 默认展开且 Tool preview 可展开
→ /clear 创建新 Session
→ 输入裸 exit，确认作为普通消息
→ /exit 正常退出
```

## 建议提交边界

实际获得提交授权后，TUI 部分建议拆成两个逻辑提交：

1. `refactor(tui): unify queued submit and steer dispatch`
   - ChatState reservation、App 调度、CommandRegistry 和测试。
2. `feat(tui): render thinking run cards and tool previews`
   - Actions、Projectors、Widgets、样式、Replay 和 snapshots。

如果实现顺序使 CommandRegistry 与队列 diff 容易独立审核，也可以单独提交
`refactor(tui): register local commands`；不得为凑提交数拆开实现与测试。

## 完成标准

- thinking 实时、完整、partial、历史和重连路径都有确定性测试。
- RunCard 聚合 thinking、Tool、correction，最终 Assistant 保持独立。
- Tool preview 对 JSON、文本、错误、截断和旧历史均可读。
- 所有普通输入经过同一队列，状态竞争不丢失、不重复。
- FIFO、LIFO、附件和 Ctrl+C 在 submit/steer 两条路径上一致。
- 只有 `/clear`、`/resume`、`/exit` 注册；裸 exit/quit 成为普通消息。
- TUI Widget、App、GatewayClient、Replay、Golden、Snapshot 和全量测试通过。
- `make test-tui` 与 `git diff --check` 通过。
