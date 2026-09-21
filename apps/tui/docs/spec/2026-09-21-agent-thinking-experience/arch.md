# TUI Run Card, Input Queue, and Command Registry Design｜TUI 运行卡片、输入队列与命令注册设计

## 文档定位

设计日期：2026-09-21。

本文定义 `apps/tui` 如何显示 Agent thinking 与 Tool 预览，如何把所有普通输入统一交给本地队列并在
出队时动态选择 submit/steer，以及如何把现有本地命令迁移到可扩展注册结构。跨应用产品契约见：

- [Agent Thinking Experience](../../../../../spec/2026-09-21-agent-thinking-experience.md)；
- [Agent 事件与持久化设计](../../../../agent/docs/spec/2026-09-21-agent-thinking-experience/arch.md)；
- [Gateway 协议设计](../../../../gateway/docs/spec/2026-09-21-agent-thinking-experience/arch.md)。

实施步骤见 [TUI 实施计划](plan.md)。

本文增量替代 [TUI 持久输入与本地队列设计](../2026-08-19-tui-persistent-input-queue/arch.md) 中
“运行中只等待下一轮”和裸 `exit` / `quit` 退出的部分。原文定义的 Composer、FIFO/LIFO、附件、
`Ctrl+C` 优先级和 Textual 应用边界继续有效。

## 当前实现与问题

### 输出投影

- `ConversationView` 把 Assistant、Tool、Error 和 Task 终态平铺为独立消息。
- `AgentProjector` 不认识 thinking，只显示 Tool 参数和 success/error。
- `UserInputProjector` 把 `user.message` 与 `user.correction` 都投影成顶层用户消息，无法看出哪个内容
  是运行中追加。
- 历史和实时 update 共用 Projector Registry，但没有 thinking 临时块与持久化块的对账状态。

### 输入队列

- idle 输入进入 `ChatState.pending` 后由 `_dispatch_next()` 调用 `session.submit`。
- running 输入在 Composer handler 中绕过队列，直接调用 `_steer_active_task()`。
- steer 失败后才重新入队，导致运行中输入与普通排队输入的撤回、发送顺序和并发行为不一致。
- `ChatState.can_dispatch` 只允许 READY 阶段出队，无法在 RUNNING 阶段发送 steer。

### 命令

- `commands.py` 只返回 `/resume`、`/clear` 的 Literal。
- 裸 `exit` / `quit` 在 App 提交 handler 中单独拦截。
- 新命令需要同时修改 parser 和 App 分支，缺少统一元数据、参数错误和未知命令行为。

## 设计原则

1. TUI 只消费 Gateway 公共协议，不导入 Agent 事件或持久化实现。
2. RuntimeUpdate 先投影为扁平 UiAction，再由 Widget 维护纯展示状态。
3. 所有普通消息共用一条队列；路由是出队时的瞬时决定，不是队列项身份。
4. RPC 接受之前不删除队首，避免网络错误或状态竞争造成输入丢失。
5. Task 运行过程聚合展示，最终回答保持独立，避免中间输出淹没对话。
6. 命令注册层只服务现有具体命令，不为未来命令构造空壳或业务抽象。

## UiAction 扩展

Event Pipeline 增加以下扁平不可变 Action：

```python
@dataclass(frozen=True)
class AppendThinkingDelta:
    task_id: str
    step: int
    text: str


@dataclass(frozen=True)
class CompleteThinking:
    task_id: str
    step: int
    text: str
    partial: bool
    historical: bool = False


@dataclass(frozen=True)
class AppendUserCorrection:
    task_id: str
    text: str
    applied_before_step: int | None


@dataclass(frozen=True)
class UpdateToolCompleted:
    task_id: str
    call_id: str
    tool_name: str
    success: bool
    output_preview: object | None = None
    preview_truncated: bool = False
    full_result_available: bool = False
    preview_error: str | None = None
    error: str | None = None
```

`AgentProjector` 注册并校验 `assistant.thinking_delta`、`assistant.thinking`，同时读取新增 Tool 字段。
`UserInputProjector` 将 `user.correction` 改为 `AppendUserCorrection`；只有 `user.message` 生成顶层
`AppendUserMessage`。

历史标记不写入 Gateway payload。App 已知道 `_project_runtime_update(..., historical=True)`，它在调用
Projector 时显式传入历史上下文，由 Projector 填充 Action，供 Widget 保留来源语义；实时与历史 thinking
都默认展开。不要通过 `sequence is not None` 推断历史，因为刚发生的实时完整记录同样有 sequence。

## Conversation 组件结构

`ConversationView` 继续管理顶层对话顺序，但每个 Task 增加一个 `RunCard`：

```text
ConversationView
├── UserMessage(task A)
├── RunCard(task A)
│   ├── ThinkingBlock(step 1)
│   ├── AssistantProgressBlock(step 1, when a Tool step emits text)
│   ├── ToolBlock(call A)
│   ├── UserCorrectionBlock
│   ├── ThinkingBlock(step 2)
│   └── ToolBlock(call B)
├── AssistantMessage(task A final)
└── TurnStatusMessage(task A, only non-success terminal states)
```

`ConversationView` 维护：

```text
run_cards: dict[task_id, RunCard]
assistant_candidates: dict[(task_id, step), AssistantProgressBlock]
completed_thinking: set[(task_id, step)]
tools: dict[(task_id, call_id), ToolBlock]
restoring_history: bool
```

RunCard 是 TUI 展示组件，不是新的 Runtime 实体，不持久化自身状态，也不改变 Task/Run/Step 定义。

## Assistant 候选与最终提升

`assistant.text_delta` 和 `assistant.message` 都已有 step。Projector 保留该 step，ConversationView 将
当前 step 的可见文本先流式投影为 RunCard 内的 Assistant 候选，而不是立即创建卡片后的顶层消息：

- 同 step 后续出现 `tool.started`，该候选成为 `AssistantProgressBlock` 并留在 RunCard；
- 更大 step 的 thinking/text 开始时，上一候选也确定为中间过程；
- `task.finished(status=completed)` 到达时，把最后一个未被后续 step/Tool 消费、且已经收到
  `assistant.message` 的候选原子提升为 RunCard 后的独立 `AssistantMessage`；
- failed/cancelled/interrupted 不提升未完成候选，已产生内容作为 partial progress 留在 RunCard；
- 历史恢复按相同规则重建，不能依赖“Tool step 通常没有文本”的模型习惯。

提升后如果 RunCard 没有 thinking、Tool、correction 或中间 Assistant，移除空卡片，使不产生中间过程的
普通问答保持现有简洁布局。

提升只改变 Widget 所属层级，不复制文本、不增加 RuntimeUpdate。这样既保持实时可见，也保证 Tool 不会
因为插入到已挂载 RunCard 内而在视觉上跑到早先的顶层 Assistant 文本之前。

## ThinkingBlock 行为

每个 `(task_id, step)` 对应一个 ThinkingBlock：

- 实时 delta 或历史完整记录创建区块时都默认展开。
- 后续 delta 追加到同一 Markdown/Text 内容。thinking 使用克制的弱化样式，不伪装成最终 Assistant
  回答。
- 新 thinking step、Tool 开始和 Task 终态都不自动改变已有 thinking 的展开状态。
- `assistant.thinking` 到达时：
  - 若临时块存在，使用完整 `text` 对账替换，并记录 `partial`；
  - 若不存在，创建完整块；
  - 标记该 key 已完成，忽略之后迟到的同 key delta。
- 完整记录对账保持当前展开状态；历史恢复创建的完整块也默认展开。
- `partial=true` 使用轻量 `partial` / `interrupted` 状态文案，不把内容标成错误。
- 用户可通过键盘和鼠标切换展开状态；折叠只改变显示，不删除正文。

折叠摘要本身可聚焦；Enter/Space 只在摘要拥有焦点时切换。Composer 拥有焦点时继续由
`PersistentComposer` 处理 Enter/Shift+Enter，应用级 `Ctrl+C` 优先级不变，避免新增展开快捷键与输入、
撤回或取消冲突。

恢复历史时，sequence 顺序决定 RunCard 子项顺序。实时 delta 没有 sequence，只能更新当前 step；对应
完整记录到达后成为该区块的持久化事实。

## ToolBlock 行为

Tool started 创建默认折叠的 ToolBlock，摘要行至少包含：

```text
◆ tool_name   running / completed / failed / interrupted
```

展开区域包含：

1. Arguments：格式化 JSON；
2. Output：根据值类型选择展示；
3. Error：失败时独立错误区；
4. Truncation：存在省略时的固定提示。

展示规则：

| 数据形态 | 展示 |
| --- | --- |
| dict / list | 稳定缩进 JSON code block |
| string | 等宽文本 code block，保留换行 |
| number / bool | 简洁标量 |
| null | 不显示空 Output 区 |
| 包含常见 `path` / `file` 字段的对象 | 摘要行突出路径，完整对象仍可展开 |
| `success=false` | 摘要标失败，Error 独立显示 |
| `preview_error` | 显示“Preview unavailable”，不把 Tool 改判为失败 |

截断与完整结果提示必须同时读取两个字段，不能把“预览被截断”误写成“必然存在完整结果”：

| `preview_truncated` | `full_result_available` | 提示 |
| --- | --- | --- |
| true | true | `Output preview truncated. Full result remains on the Agent side.` |
| true | false | `Output preview truncated. Full result is unavailable.` |
| false | true | `Full result remains on the Agent side.` |
| false | false | 不显示结果可用性提示 |

所有分支都不展示本地路径或提供读取动作。旧历史没有 preview 字段时继续只显示 arguments 和
success/error。

## 运行中追加展示

`user.correction` 表示 Agent 实际在安全边界接受并应用的追加输入，而不是用户刚按下 Enter 的瞬间。
它在对应 Task 的 RunCard 中显示：

```text
Added by you before step 3
补充检查失败路径。
```

- 使用 `applied_before_step` 提供位置/标签信息。
- 不创建新的顶层 UserMessage，不让用户误以为已经开始下一轮。
- 尚在本地队列、尚未被 steer 接受的内容仍只出现在 QueuePanel。
- steer RPC 被接受但尚未产生 `user.correction` 时不提前伪造卡片记录；以公共 RuntimeUpdate 为事实。
- 历史恢复按 sequence 重建相同记录。

## 统一输入队列状态

`PendingMessage` 保持 TUI 所有的文本、图片和 `submission_id`。本期不增加 `intent`、`route` 或
`intended_task_id` 字段。
同一个 PendingMessage 无论当前尝试 submit、steer 或从 late steer 降级为 submit，都复用原
`submission_id`。

`ChatState` 另维护 `blocked_submission_id: str | None`。确定性内容拒绝只暂停当前队首：当该 ID 仍在
队首时 `can_dispatch=false`；`Ctrl+C` 最终撤回该项或用户恢复并重新提交编辑后的 draft 后清除阻塞。新入队
内容不能越过被阻塞的队首，保持 FIFO。

`ChatState` 增加发送模式：

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

App 维护单调递增的 `connection_epoch`，每次初始连接或重连成功后递增。Reservation 的
`attempt_epoch` 捕获本次 RPC 使用的连接代次；网络异常发生前 `outcome_unknown=false`，RPC 已发出但未
收到权威响应时置为 true。这些字段属于正在发送状态，不进入 `PendingMessage`，也不随队列项改变业务
intent。

`begin_dispatch()` 在调用时读取当前 phase 和 active task：

| 当前状态 | 队列非空时的结果 |
| --- | --- |
| READY，且无 active task | reserve `submit` |
| RUNNING，且有 active task | reserve `steer(task_id)` |
| CANCELLING / SWITCHING / STARTING / STOPPING / FAILED | 暂不出队 |
| 已有 dispatch in progress | 暂不出队 |

reservation 固定本次 RPC 目标，防止 await 期间状态改变后误把同一次请求换成另一种 RPC。队列项本身
仍无模式；失败并释放 reservation 后，下次出队重新判断。

## 发送状态机

### 入队

Composer 提交非命令内容时统一执行：

```text
validate non-empty submission
→ ChatState.enqueue(PendingMessage)
→ refresh QueuePanel
→ schedule dispatch
```

RUNNING 分支不再直接调用 `_steer_active_task()`。这样所有输入立即显示在 QueuePanel，并共享 FIFO、
LIFO 撤回和附件生命周期。

### Submit reservation

```text
reserve queue head as submit
→ session.submit
→ accepted: popleft, delete owned temp images, set active_task_id
→ flush early updates and reconcile task status
```

RPC 异常时释放 reservation、保留队首并进入现有可见失败状态；用户内容和附件不能删除。
其中只有收到权威业务错误后才释放：`invalid_resource` / `resource_unavailable` 进入可编辑队首阻塞，
`submission_conflict` 或未知协议错误进入 fatal 边界。连接中断等无法判断服务端是否已接受的异常必须
保留原 reservation 并标为 `outcome_unknown`，重连后以同 ID 精确重试原 submit。

### Steer reservation

```text
reserve queue head as steer(active_task_id)
→ session.steer(submission_id=message.submission_id)
├── accepted: popleft, delete owned temp images
├── already_finished / not_found / not_running:
│   release reservation, keep head
│   reconcile captured task_id
│   schedule again after state becomes READY
├── already_cancelling:
│   release reservation, keep head
│   wait for task.finished, then dispatch as submit
├── invalid_content / expired:
│   release reservation, keep head, pause automatic dispatch
│   show deterministic error; Ctrl+C can restore the message for editing
└── transport / response outcome unknown:
    keep the original reservation and mark outcome_unknown
    reconnect, then retry the same RPC with the same target and submission_id
    never reroute or start a tight retry loop before the original result is known
```

接受 steer 后 active task 不变，并可以立即调度下一个队首继续 steer；FIFO 保持不变。是否存在服务端
对连续 steer 的安全限制由现有 `session.steer` 结果表达，TUI 不并发发送多个 steer。

当 Task 在 steer await 期间结束时，`FinishTurn` 可以先把 ChatState 切到 READY。返回
`already_finished` 后保留队首；下一次 reservation 将自然选择 submit。若 steer 实际返回 accepted，说明
服务端已经接纳当前 Task，即使终态 update 紧邻到达，该队首也应移除，不能重复 submit。
AgentRuntime 以 `submission_id` 对已接受的 steer 做有界进程内幂等，因此连接中断造成结果不确定时可以
在重连后安全重试。此时即使 Task 已结束，也必须先重试原 steer：返回 cached accepted 就消费队首；返回
`already_finished` 等明确拒绝后才释放 reservation 并重新动态路由。TUI 不为重试生成新 ID。

### 调度触发

以下时机调用同一个幂等 `_schedule_dispatch()`：

- 普通输入入队；
- Runtime 启动或重连完成；
- 一次 submit/steer reservation 完成或释放；
- `FinishTurn`；
- late steer 状态对账完成。

`dispatch_scheduled` 与 `dispatch_in_progress` 继续保证最多一个实际发送协程。
`outcome_unknown` reservation 只能由重连成功触发原请求重试，普通入队、FinishTurn 和状态刷新不得绕过
它发送队列中的下一项。重试只允许使用严格大于 `attempt_epoch` 的当前连接代次，并在发出前原子更新
reservation 的 `attempt_epoch`、清除 `outcome_unknown`。如果重连已经成功，旧连接上的 RPC 才随后抛错，
App 检测到 `connection_epoch > attempt_epoch` 后必须立即安排同一 reservation 的精确重试，不能继续等待
一个不会再次发生的 reconnect 通知。这样每个连接代次至多发起一次尝试，也不会因事件先后顺序永久卡住
队首。

初次发送与重试共用 `_send_reservation(reservation)`；普通 `_dispatch_next()` 只负责创建新 reservation，
重连路径通过 `begin_dispatch_retry()` 取得更新 epoch 后直接调用同一 sender，不能再次调用
`begin_dispatch()`。RPC transport failure 和订阅 failure 进入同一个幂等 reconnect 调度，任何时刻最多一个
reconnect worker；否则请求失败若没有及时触发订阅关闭会永久卡住，而两个失败信号同时到达又会并发重连。

`GatewayClient.reconnect()` 在关闭旧 socket/reader 时，必须先让旧连接仍 pending 的 RPC future 以明确的
transport error 完成，再建立新连接；不能只 cancel reader 后留下永远等待的 future。业务
`GatewayClientError.code` 仍表示权威拒绝，不触发 reconnect。客户端 close/shutdown 引起的取消沿用退出
流程，不在停止阶段启动重试。

## `Ctrl+C` 与队列编辑

继续使用既有优先级：

| 优先级 | 状态 | 动作 |
| --- | --- | --- |
| 1 | Composer 有草稿/附件 | 清空当前草稿 |
| 2 | 本地队列非空，且队尾未被当前 reservation 锁定 | pop 队尾并恢复完整草稿 |
| 3 | 队列为空且 Task 运行中 | 请求取消当前 Task |
| 4 | 无草稿、队列和活动 Task | 退出应用 |

如果队列仅剩正在发送的队首，RPC 握手完成前不能撤回该项，避免客户端已发送但本地又恢复为草稿。
状态栏显示“Message is being sent”。多项队列中仍可撤回未锁定的最后一项。
确定性拒绝已经释放 reservation，因此可以按 LIFO 规则撤回；若它前面没有其他队尾，撤回该阻塞项会
同时清除 `blocked_submission_id`。编辑后重新提交仍复用原 submission ID，但会重新进入正常调度。

新对话与追加没有不同的编辑或撤回逻辑。用户原本期望追加的消息如果尚未发送而 Task 已结束，仍可
从队尾撤回；如果继续保留，则自动作为下一轮 submit。

## CommandRegistry

### 最小结构

`commands.py` 从 Literal parser 改为轻量注册表：

```python
@dataclass(frozen=True)
class CommandInvocation:
    name: str
    arguments: str


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    summary: str
    handler_name: str
    accepts_arguments: bool = False
    allow_attachments: bool = False


class CommandRegistry:
    def register(definition: CommandDefinition) -> None: ...
    def parse(text: str) -> CommandInvocation | None: ...
    def resolve(name: str) -> CommandDefinition | None: ...
```

注册表保持纯解析和元数据，不持有 Textual App、GatewayClient 或 Worker。App 根据 `handler_name` 调用
自身异步 handler，确保具体 UI 生命周期仍由 App 管理。重复名称、非 `/` 名称和空 handler 在注册时
拒绝。

### 本期注册项

| 命令 | 参数 | 附件 | Handler | 门禁 |
| --- | --- | --- | --- | --- |
| `/clear` | 不接受 | 不接受 | 现有 clear Session 流程 | idle Session command |
| `/resume` | 不接受 | 不接受 | 现有 Session picker 流程 | idle Session command |
| `/exit` | 不接受 | 不接受 | 现有正常 shutdown 流程 | 应用仍接受输入且尚未 stopping |

命令解析：

- 去除首尾空白，命令名 ASCII case-insensitive。
- 首个空白前是名称，剩余原文是 arguments。
- 已知命令收到不支持的参数时显示用法错误，不进入普通队列。
- 未知 `/xxx` 显示 `Unknown command: /xxx`，不发送给 Agent。
- 仅有 `/` 也属于未知命令。
- 不以 `/` 开头的 `exit`、`quit` 和其他文本全部作为普通输入。
- 未知命令、参数错误、附件错误或 idle 门禁失败时，恢复 Composer 提交前的完整文本、附件和
  `submission_id`；因为 `Submitted` 事件发出时 Composer 已清空，所有“识别为本地命令但未执行”的路径
  都必须显式恢复原 submission。

本期不注册 `/compact`、`/btw`，不创建 placeholder handler，不为未来命令增加权限、远程命令或多级
子命令系统。未来命令只在语义明确时新增一条 Definition 和对应 handler。

## 历史恢复与重复抑制

Session 激活时 `ConversationView.begin_history_restore()`：

- 按 sequence 依次投影 `user.message`、thinking、Tool、correction、Assistant 和终态。
- 每个 task_id 懒创建 RunCard。
- 所有 historical ThinkingBlock 默认展开。
- ToolBlock 默认折叠。
- 中间 Assistant candidate 留在 RunCard；成功 Task 的最后 candidate 在终态时提升为独立最终消息。
- 恢复结束后清除当前流式句柄并滚动到历史末尾。

重连增量历史与实时流可能重叠：

- sequence 继续由 App 的 `_last_sequence` 去重。
- thinking delta 使用 `(task_id, step)` 更新临时块。
- 完整 thinking 到达后把 key 加入 completed set；之后同 key delta 忽略。
- 完整 Tool completion 以 `(task_id, call_id)` 更新既有区块；缺少 started 时创建可恢复的 ToolBlock，
  不同 Task 使用相同 call_id 时不能互相覆盖。
- Session reset/switch 清空 run card、completed thinking、Tool 和临时 assistant 映射。

## 样式与可访问性

- RunCard 使用单一克制边界，不为每个子项创建重型嵌套边框。
- Thinking 使用弱化标签和正文颜色，展开状态不只依赖颜色，要有 `▸` / `▾` 或等价文本提示。
- Assistant 与 Thinking 中包含 CJK 字符的 Markdown 普通段落按终端 cell 宽度折行，避免 Rich 将
  连续中文视为一个英文单词后在 `18 篇`、`4 千字` 等中英文边界提前换行；纯英文段落仍按单词换行，
  代码块、表格和 Tool 详情沿用各自布局。
- Tool 状态同时使用文字 `running/completed/failed/interrupted`。
- User correction 使用 `Added by you`，与顶层 `You` 明确区分。
- 展开控件支持键盘 focus/activate；鼠标只是附加能力。
- 流式更新和完整记录对账不能抢走 Composer 焦点。
- Conversation 的已有滚动跟随策略继续生效；用户主动离开底部时不强制跳回。

## 错误处理

- thinking payload 非法：Projector 抛出可诊断错误并进入现有投影失败边界，不静默渲染错误数据。
- Tool preview 缺失：按旧协议展示，不报错。
- Agent 已保证 `output_preview` 是 JSON value；TUI 格式化发生意外失败时不调用任意对象 `repr`，改用
  固定安全占位并显示 preview unavailable。
- RunCard Widget 更新失败：进入 ConversationView 现有 fatal 边界，避免继续产生错误投影。
- submit/steer 的 `invalid_resource` / `resource_unavailable` 与 steer 的 `invalid_content` / `expired`
  都属于可编辑的确定性拒绝：释放 reservation、保留并阻塞队首及附件，等待用户用 `Ctrl+C` 撤回修改；
  后续队列项不得越过。`submission_conflict` 或未知协议状态表示客户端状态损坏，进入可见 fatal 边界。
- submit/steer 网络结果不确定：保留原 reservation，状态栏说明正在对账，重连后精确重试原 RPC。
- 未知斜杠命令：只显示 warning，不污染 Conversation 或 QueuePanel。
- `/exit` cleanup 失败：沿用现有 shutdown 记录和返回码策略，不把命令发送给 Agent。

## 测试范围

### 纯状态与命令

- READY 出队得到 submit reservation，RUNNING 出队得到带 captured task_id 的 steer reservation。
- Task 在 await 期间结束，late steer 保留队首并下一次改走 submit。
- accepted steer 移除队首但不改变 active task。
- RPC 错误保留消息、附件和原 reservation；重连后不改变其路由。
- 多条消息 FIFO 发送，`Ctrl+C` LIFO 撤回，发送中队首不会被撤回。
- 三个命令注册、大小写、空白、参数、附件和未知命令行为。
- 裸 `exit` / `quit` 解析为普通消息。

### Projector

- thinking delta / complete 生成正确 Action。
- historical 上下文不从 sequence 错误推断。
- `user.correction` 不再生成 `AppendUserMessage`。
- Tool preview 的对象、数组、文本、null、截断、完整结果可用性和错误字段正确映射。
- 未知 update 保持忽略和计数。

### Widget 与 App

- 实时与历史 thinking 都默认展开；下一 thinking、Tool 和 Task 终态不自动折叠。
- 用户可以手动折叠或重新展开，完整记录对账保留当前展开状态。
- 完整 thinking 替换实时临时内容，无重复块。
- correction 位于对应 RunCard，最终 Assistant 独立。
- Tool 格式化、失败、截断提示和旧历史 fallback。
- RUNNING 提交先显示在 QueuePanel，再由调度器 steer。
- Task 结束前后竞争不会丢消息或重复提交。
- Session 切换清空全部卡片映射并正确恢复目标历史。

## 完成标准

- TUI 可实时显示、折叠并历史恢复 thinking。
- thinking、Tool 和 correction 归入 Task RunCard，最终 Assistant 回答保持独立。
- Tool 展开内容可读，失败和截断不含歧义。
- 所有普通输入严格经过同一队列，出队时动态选择 steer/submit。
- Ctrl+C、附件和发送失败在两种路由下行为一致。
- `/clear`、`/resume`、`/exit` 通过 CommandRegistry 执行；未来命令拥有注册扩展点但未被提前实现。
- TUI 小测试、`make test-tui` 和 `git diff --check` 通过。
