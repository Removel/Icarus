# Textual TUI Development Plan｜Textual TUI 开发计划

## 实施状态

本计划已于 2026-08-19 完成核心落地。当前 `icarus` 已切换为 Textual 全屏主流程，旧
`prompt_toolkit + Rich.Live` REPL 已删除。确定性 replay、transcript golden、Pilot 行为测试
和五类完整 shell snapshot 均已建立；视觉审查覆盖欢迎页、流式 Markdown + 草稿、运行中
多条队列、工具失败 + AgentError 和窄终端布局。

视觉审查期间发现并修复了两个实现问题：Composer 单行内容被 `height: auto` 撑到上限，
以及窄屏 class 错加到 App 而不是 Screen。当前 Composer 从一行开始，按逻辑行增长到八行
后内部滚动；窄屏规则由 Screen class 生效。任务级真实取消仍不属于本计划，继续由
`docs/todo/agent-core.md` 跟踪。

## 目标

依据 `apps/tui/docs/arch/tui-persistent-input-queue-design.md`，把第一阶段串行
`prompt_toolkit + Rich` REPL 替换为 Textual 全屏应用：

```text
PersistentComposer
→ TUI local deque
→ AgentRuntimeService.submit()
→ OutputEventSubscription
→ source-aware ProjectorRegistry
→ UiAction
→ Conversation / Status / Notification
```

交付后的 `icarus` 必须支持：

- 一个 Workspace 启动一个新 Session；
- 固定在底部、Agent 运行期间仍可编辑的多行输入框；
- Enter 提交，Shift+Enter 换行，Ctrl+J 作为可靠换行后备；
- 待发送消息由 TUI 本地 `deque` 维护，正常 FIFO、撤回 LIFO；
- Agent Markdown、工具状态、错误和任务终态实时显示；
- 同一个 Runtime 内不同 Plugin 的 Event 按来源投影到不同 UI 区域；
- 上下文相关的四级 `Ctrl+C`；
- 应用内滚动、全屏退出后恢复原终端画面；
- 不依赖真实模型即可完成事件顺序、交互和视觉回归。

本计划初始范围不包含任务级取消、历史 Session 浏览、多模态队列、队列持久化或队列重排。
任务级取消已在后续 TUI-06 中完成：运行中的第三类 `Ctrl+C` 调用
`AgentRuntimeService.cancel_task(task_id)`，等待 cancelled 终态后恢复队列调度。

## 实施边界

- TUI 只调用 `AgentRuntimeService.start / subscribe_events / submit / cancel_task / stop`；
- 不直接导入或访问 PluginManager、EventBus、Blackboard 或具体 Plugin 实例；
- `OutputBridgePlugin` 继续广播原始 `(source_plugin_id, Event)`，不解释 UI；
- `ProjectorRegistry` 和 `UiAction` 归 `apps/tui` 所有；
- Blackboard 继续独占业务对话 History，Conversation 只是当前进程的 UI 投影；
- 首期只注册已有真实来源 `agent` 和 `user-input`，不创建 Skill、Memory 等空 Projector；
- 不保留新旧两条 TUI 主流程；Textual 路径稳定后删除 `input.py`、`repl.py`、
  `renderer.py` 及对应旧测试；
- 不修改 Agent Core 或新增取消协议；
- 不创建分支、commit 或 push。

## 已核对的实现前事实

- `OutputEvent` 当前为 `tuple[str, Event]`，来源身份未丢失；
- Output Bridge 目前显式订阅 `user-input` 和 `agent`；
- 输出订阅是纯实时广播，每个订阅者有独立无界队列，无历史回放；
- `submit()` 在发布 `InputQueuedEvent` 后返回 `InputAccepted(task_id, queue_position)`；
- 同一任务的常规输出顺序为 queued、started、user input、Agent stream、finished；
- `InputFinishedEvent.status` 当前只有 `completed | failed`；
- 当前开发环境尚未安装 Textual 和 snapshot 插件；
- 截至实施计划编写时，Textual `8.2.8` 支持 Python 3.9–3.14，并要求
  Rich `>=14.2.0`；`pytest-textual-snapshot 1.1.0` 是仅测试依赖。

## 实施顺序

```text
依赖与纯状态
→ UiAction / Projector / Replay / Transcript
→ Composer / Conversation / Queue / Status Widgets
→ Textual App 生命周期与本地调度
→ Pilot / Golden / Snapshot / Shell replay
→ Wheel / PTY / 可选真实模型验收
```

每个阶段先运行最窄测试，再进入下一阶段。Snapshot 基线只能在语义测试通过并人工查看
实际 SVG/PNG 后建立。

## 阶段一：依赖、打包和纯状态模型

### 任务一：切换 Textual 依赖与样式资源

**更新文件**

- `pyproject.toml`
- `apps/agent/requirements.txt`
- `apps/tui/test/test_cli.py`

**开发内容**

- 运行依赖增加 `textual>=8.2.8,<9`；
- Rich 范围调整为 `rich>=14.2,<15`，满足 Textual 约束；
- 移除 TUI 对 `prompt-toolkit` 的直接运行依赖；
- `pytest-textual-snapshot>=1.1,<2` 只进入当前开发/测试依赖入口，不进入最终用户运行时
  dependencies；
- 为 `apps.tui.src` 声明 `styles.tcss` package data；
- 保持 console script 为 `icarus = "apps.tui.src.main:main"`；
- 更新打包测试，明确 wheel 必须包含 `styles.tcss`，且不包含测试、文档和被删除的旧 TUI
  模块。

**验证**

```bash
apps/agent/.venv/bin/python -m pip install -r apps/agent/requirements.txt
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_cli.py -q
git diff --check
```

### 任务二：实现无 Textual 依赖的 `ChatState`

**新增文件**

- `apps/tui/src/chat_state.py`
- `apps/tui/test/test_chat_state.py`

**开发内容**

- 定义明确的 Runtime phase：`starting`、`ready`、`running`、`stopping`、`failed`；
- 保存 `pending: deque[str]`、`active_task_id`、`dispatch_in_progress`；
- 提供窄而显式的状态转换：enqueue、begin dispatch、accept dispatch、fail dispatch、
  finish active、pop tail；
- `accept dispatch` 只有拿到 `InputAccepted` 后才 `popleft()`；
- 用 `dispatch_in_progress` 阻止重复 submit；
- 定义纯值 `InterruptAction`，按“清草稿、撤回队尾、提示不支持取消、退出”返回唯一动作；
- 草稿是否存在按原始字符串判断，空白草稿也属于可清空草稿；提交是否有效才使用
  `text.strip()`；
- 状态类不导入 Textual、Agent Event、Widget 或 Service，不保存业务 History。

**测试用例**

- Starting 时可 enqueue，但不可 dispatch；
- ready 且 idle 时只能开始一次 dispatch；
- submit 接受前队首仍在，接受后才移除；
- 正常消费始终 `popleft()`，撤回始终 `pop()`；
- active 结束且队列非空时回到可继续调度状态；
- task ID 不匹配或重复终态不清除当前任务；
- submit 失败保留完整队首并停止自动忙重试；
- 四级 `Ctrl+C` 优先级逐项互斥；
- 多行、缩进、中文和 Unicode 在 enqueue/pop 中完全不变。

### 阶段一检查点

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_chat_state.py apps/tui/test/test_cli.py -q
apps/agent/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test
git diff --check
```

完成条件：不启动 Textual 和 Agent Runtime 即可证明本地队列、调度握手和 Ctrl+C 决策。

## 阶段二：来源投影、确定性回放和语义 Golden

### 任务三：定义 TUI 自有 `UiAction`

**新增文件**

- `apps/tui/src/event_pipeline/__init__.py`
- `apps/tui/src/event_pipeline/actions.py`

**开发内容**

- 使用 frozen dataclass 定义首期真实动作：
  - `AppendAssistantDelta`；
  - `AppendToolStarted`；
  - `UpdateToolCompleted`；
  - `AppendError`；
  - `SetRuntimeStatus`；
  - `ShowNotification`；
  - `FinishTurn`；
- Action 只携带渲染所需的扁平数据，例如 task ID、call ID、工具名、参数摘要、成功状态
  和错误文本；
- 不携带 Textual Widget、Agent Response、完整 ToolResult 或内部 Plugin 对象；
- 用显式 union 表示 `UiAction`，让路由遗漏可以在类型检查和测试中暴露。

### 任务四：实现来源感知的 Projector Registry

**新增文件**

- `apps/tui/src/event_pipeline/dispatcher.py`
- `apps/tui/src/event_pipeline/projectors/__init__.py`
- `apps/tui/src/event_pipeline/projectors/agent.py`
- `apps/tui/src/event_pipeline/projectors/user_input.py`
- `apps/tui/test/event_pipeline/test_dispatcher.py`
- `apps/tui/test/event_pipeline/projectors/test_agent.py`
- `apps/tui/test/event_pipeline/projectors/test_user_input.py`

**开发内容**

- 定义 `EventProjector` Protocol：输入公开 Event，返回 `tuple[UiAction, ...]`；
- `ProjectorRegistry` 按 `source_plugin_id` 显式注册 Projector；
- Dispatcher 接收 `(source_plugin_id, event, active_task_id)`，先验证 task_id，再交给对应
  Projector；
- 未注册来源、已注册来源的未知 Event、空 task_id 和非当前任务 Event 默认返回空 tuple，
  只增加诊断计数或 debug 日志；
- `AgentProjector` 映射文本、工具开始/完成和 AgentError；
- `AgentCompletedEvent` 默认不重复投影完整回答；
- `UserInputProjector` 映射 accepted、started 和 finished；`UserInputEvent` 不重复显示用户消息；
- ToolResult 只投影成功/失败和失败摘要，不暴露完整 output；
- Projector 不操作队列、Service 或 Widget。

**关键测试**

- 同一种 Event 从错误来源到达时不被错误 Projector 处理；
- 未注册 Plugin 来源不会自动显示 `repr(event)`；
- 新增未知 Event 不会意外暴露内部数据；
- unrelated Task Event 不产生 UiAction；
- 连续 Delta、工具边界、工具失败、AgentError 和 FinishTurn 的动作顺序准确；
- 相同输入事件序列多次投影结果完全一致；
- reasoning 不在公开 Agent Event 中，因此投影层不会自行生成或显示 reasoning。

### 任务五：实现版本化 JSONL codec 与 Replay Service

**新增文件**

- `apps/tui/src/replay.py`
- `apps/tui/scripts/replay_events.py`
- `apps/tui/test/fixtures/synthetic_tui_events.jsonl`
- `apps/tui/test/test_replay.py`

**开发内容**

- 使用固定 envelope：`schema_version`、`source_plugin_id`、`event_type`、
  `task_id`、`payload`；
- codec 只支持首期公开可见 Event 白名单，并解码为现有 Agent Event dataclass；
- 严格拒绝未知 schema、缺字段、类型错误和不支持的 event type；
- Tool Call 参数使用稳定 JSON，fixture 不保存 secret、绝对临时目录或未展示的完整结果；
- `ReplaySubscription` 保持与生产订阅相同的 `next_event()/close()` 形状；
- `ReplayRuntimeService` 保持 TUI 所需的 `start/subscribe_events/submit/stop` 形状；
- Replay Service 按 task 分组：每次 submit 返回 fixture 中下一任务的 task ID，再按固定顺序
  发布该任务事件；
- 测试模式立即发布，真实 shell 模式按 `--speed` 控制间隔；
- 默认脚本输出 transcript；`--tui-real` 启动完整 Textual shell，但不注册 Plugin、不调用模型、
  不写 Session；
- replay 只作为 TUI 开发适配器，不成为第二个生产 Runtime。

**Fixture 至少覆盖**

- completed 任务的 accepted、started、分段 Markdown 和 finished；
- 文本 → tool started → tool completed → 文本；
- 工具失败和 AgentError；
- unrelated Task Event；
- 至少三个 task，供 FIFO 自动调度和 LIFO 撤回测试；
- Markdown 列表、代码块、中文、宽字符与多行内容。

### 任务六：实现 Transcript Recorder 与 Golden

**新增文件**

- `apps/tui/src/transcript.py`
- `apps/tui/test/golden/synthetic_tui_transcript.txt`
- `apps/tui/test/test_timeline_transcript_golden.py`

**开发内容**

- Transcript Recorder 消费和 Textual View 相同的 UiAction；
- 合并连续 assistant delta，在工具、错误和终态边界固化段落；
- 工具参数使用排序稳定、`ensure_ascii=False` 的紧凑 JSON；
- 输出无 ANSI、无动态时间、无随机 event ID、无临时绝对路径；
- Golden 只验证语义顺序，不复制 CSS 或终端布局；
- 更新 Golden 必须显式设置项目约定的更新开关，并人工检查 diff。

### 阶段二检查点

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/event_pipeline apps/tui/test/test_replay.py -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_timeline_transcript_golden.py -q
apps/agent/.venv/bin/python apps/tui/scripts/replay_events.py \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
git diff --check
```

完成条件：同一个 JSONL fixture 能稳定得到同一组 UiAction 和同一份 canonical transcript。

## 阶段三：Textual Widgets 与样式

### 任务七：实现持久 Composer

**新增文件**

- `apps/tui/src/widgets/__init__.py`
- `apps/tui/src/widgets/composer.py`
- `apps/tui/test/widgets/test_composer.py`

**开发内容**

- `PersistentComposer` 继承 Textual `TextArea`，整个 App 生命周期只挂载一次；
- 发布单一 `Submitted(text)` Message，不直接操作队列或 Service；
- Enter 对非空白草稿提交并清空；
- Shift+Enter 插入换行，Ctrl+J 提供跨终端后备；
- 保留 TextArea 原生左右、上下、选择、粘贴和多行编辑；
- `restore_draft(text)` 恢复完整文本并把光标放到末尾；
- Agent 输出、QueuePanel 更新和状态通知不得改变 Composer 文本、光标或焦点；
- 通过 Pilot 先验证 Textual 8.2 的实际 key 名，再固定 binding；不能把普通 Enter 同时当作
  提交和换行。

**测试用例**

- Enter 只发布一次完整多行内容；
- Shift+Enter 和 Ctrl+J 各插入一个换行且不提交；
- 空白输入不提交、不误清理；
- 左右上下键、粘贴、Unicode 和缩进保持；
- restore 后文本和 cursor 均正确；
- 流式 UI 刷新时草稿与焦点不变。

### 任务八：实现 Conversation 与消息组件

**新增文件**

- `apps/tui/src/widgets/messages.py`
- `apps/tui/src/widgets/conversation.py`
- `apps/tui/test/widgets/test_conversation.py`

**开发内容**

- 建立 Welcome、User、Assistant Markdown、Tool、Error 和 Turn Status 组件；
- `ConversationView` 负责 Conversation 类 UiAction，不识别 Agent Event；
- Assistant 使用 Textual `Markdown` / `MarkdownStream` 处理增量内容；
- 工具开始前结束当前 Markdown 段，工具后文本创建新 Assistant 段；
- Tool Widget 以 call ID 更新，完成事件缺少 start 时也能降级创建可读状态；
- 工具成功不展开 output，失败显示简短 error；
- FinishTurn 固化当前流，failed 追加明确终态；
- 默认自动滚到底部，第一版不实现用户上滚后的“新消息”模式；
- Markdown 更新错误转为可读 Error Widget，不使 Composer 失效。

### 任务九：实现 QueuePanel、StatusBar 与 TCSS

**新增文件**

- `apps/tui/src/widgets/queue_panel.py`
- `apps/tui/src/widgets/status_bar.py`
- `apps/tui/src/styles.tcss`
- `apps/tui/test/widgets/test_queue_panel.py`

**开发内容**

- QueuePanel 只接收 pending 的只读快照，不调用 append/pop/popleft；
- 队列为空时隐藏；非空时按 FIFO 顺序编号，队首即下一条任务；
- 预览压缩换行并按显示宽度截断，但不修改原始消息；
- 长队列设置最大高度并内部滚动；
- StatusBar 显示 Starting、Ready、Running、Queued n、Failed 和临时提示；
- 顶部显示 Icarus、绝对 Workspace 和 Session 标识；
- Composer 固定底部并限制增长高度，Conversation 占剩余空间；
- 窄终端下仍保证 Composer 和状态栏可见；
- 静态颜色、边框、尺寸只放在 `styles.tcss`。

### 阶段三检查点

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/widgets -q
apps/agent/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test
git diff --check
```

完成条件：Widgets 可在无 Agent Runtime 条件下验证输入、Markdown 段、工具更新、队列布局
和焦点保持。

## 阶段四：Textual App、Runtime 生命周期和本地调度

### 任务十：组合 App 与 Runtime Event Worker

**新增文件**

- `apps/tui/src/app.py`
- `apps/tui/test/test_app.py`

**开发内容**

- `IcarusTextualApp` 组合 Header、Conversation、QueuePanel、Composer 和 StatusBar；
- mount 后立即聚焦 Composer，再由 Worker 启动 Runtime；
- `await service.start()` 成功后先创建唯一长生命周期 Subscription，再允许 submit；
- 输出 Worker 只调用 `next_event()` 并投递 `RuntimeOutputReceived` Textual Message；
- Worker 不解析、过滤或直接更新 Widget；
- App Message Loop 验证 active task_id，调用 ProjectorRegistry，并按 UiAction target
  更新 Conversation、Status 或 notification；
- Runtime 启动、订阅关闭和 Worker 异常都转换为可读 App 状态；
- 统一且幂等的 shutdown：禁止新调度 → close subscription → stop service → exit；
- 正常退出返回 0，启动或未处理 fatal error 返回非零。

**提交握手竞态约束**

`service.submit()` 会在返回 task ID 前发布 queued Event。为防止最早 Event 被误判 unrelated：

1. submit 在当前 App Message handler 内直接 await；它只做 Runtime 入队握手，不等待模型；
2. 输出 Worker 可以并行读取 Event，但只把它 post 到 Textual Message Queue；
3. submit 返回后，handler 先写 `active_task_id`、挂载 User Message 和刷新本地队列；
4. handler 返回后，Textual 才处理已经排队的 RuntimeOutputReceived；
5. 此时 queued / started Event 使用已确认 task ID 投影。

不得在独立 submit Worker 中先返回 UI Message Loop，除非同时实现并测试等价的握手缓冲。

### 任务十一：接入本地队列与自动调度

**更新文件**

- `apps/tui/src/app.py`
- `apps/tui/test/test_app.py`

**开发内容**

- Composer Submitted 后保留原始内容到 `ChatState.pending` 并立即清空输入框；
- Starting 期间只排队；Runtime ready 后自动尝试队首；
- ready、无 active、无 dispatch-in-progress 时才调用 submit；
- submit 成功后才移除队首、加入 User Message、设置 task ID；
- 运行中 Enter 继续 enqueue 并实时刷新 QueuePanel；
- 只有匹配 active task 的 `FinishTurn` 能清 active 并触发下一条；
- 一次 finished 最多调度一个队首；
- submit 失败保留队首、显示 fatal/paused 状态且不忙循环；
- Runtime 的 `InputQueuedEvent` 只更新状态，不重复加入本地 QueuePanel。

**关键测试**

- 订阅建立在第一次 submit 之前；
- Starting 时第二、第三条可编辑并排队；
- FIFO 自动提交且 QueuePanel 数量同步减少；
- submit 返回前到达的 queued / started Event 不丢失；
- dispatch 期间重复触发不会产生第二个并发 submit；
- unrelated task 和重复 finished 不改变活动任务；
- Agent 输出期间 Composer 文本、光标和焦点保持；
- submit 异常时 pending 原文仍可撤回。

### 任务十二：实现四级 `Ctrl+C` 与显式退出

**更新文件**

- `apps/tui/src/app.py`
- `apps/tui/src/widgets/composer.py`
- `apps/tui/test/test_app.py`

**开发内容**

- App 使用高优先级 Ctrl+C binding，覆盖 TextArea 默认复制语义，严格执行产品优先级；
- 草稿非空：只清空草稿；
- 草稿为空且 pending 非空：`pop()` 队尾，恢复完整消息和末尾光标；
- 草稿与 pending 为空且 active task 存在：通知 Runtime 尚不支持取消，任务继续；
- 草稿、pending 为空且无 active task：正常退出；
- Runtime starting/failed 且无草稿、队列和 active task 时允许退出；
- 空 Composer 的 Ctrl+D、精确 `exit` / `quit` 命令显式退出整个程序；
- 非空 Composer 的 Ctrl+D 保留 TextArea 正常编辑行为；
- 显式退出可以停止整个 Runtime 并放弃进程内 pending，这不伪装成单任务取消。

### 任务十三：迁移 CLI 并删除第一阶段主流程

**更新文件**

- `apps/tui/src/main.py`
- `apps/tui/test/test_cli.py`

**删除文件**

- `apps/tui/src/input.py`
- `apps/tui/src/repl.py`
- `apps/tui/src/renderer.py`
- `apps/tui/test/test_input.py`
- `apps/tui/test/test_repl.py`
- `apps/tui/test/test_renderer.py`

**开发内容**

- CLI 继续从 `Path.cwd().resolve()` 捕获 Workspace；
- 继续支持可选 `--session-id`；
- 创建真实 `AgentRuntimeService` 并注入 `IcarusTextualApp`；
- 使用 Textual 自己的 `run()` 生命周期，不再外包一层串行 `asyncio.run(run_repl())`；
- 读取 App return code，保持帮助为 0、正常退出为 0、初始化/fatal error 为非零；
- `--help` 不初始化 Agent Runtime；
- 不留下兼容旧内部模块的 forwarding shim。

### 阶段四检查点

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_app.py apps/tui/test/test_cli.py -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test
git diff --check
```

完成条件：Replay Service 驱动的 `run_test()` 能证明生命周期、竞态、FIFO/LIFO、Ctrl+C 和
清理语义，不需要真实模型。

## 阶段五：视觉回归、完整 Shell 和发布验收

### 任务十四：建立 App Snapshot

**新增文件**

- `apps/tui/test/test_app_snapshots.py`
- `apps/tui/test/__snapshots__/` 下由插件生成的基线

**开发内容**

- 用 `pytest-textual-snapshot` 和固定终端尺寸覆盖：
  - 初始欢迎页；
  - 流式 Markdown 且 Composer 有未提交草稿；
  - Agent running 且有两条以上本地队列；
  - 工具执行、工具失败和 AgentError；
  - 长 Markdown、长队列；
  - 窄终端；
- 禁用动态时钟、随机 ID、光标闪烁等会导致 snapshot 抖动的因素；
- Snapshot 更新前先通过 Projector、Golden、Widget 和 App 行为测试；
- 生成 report，先关闭 difference overlay 查看 raw current/historical，再查看差异；
- 必须直接查看 SVG；若当前查看器不支持，使用浏览器引擎渲染 PNG 后查看；
- 未人工看图时不得把视觉状态标记为通过。

**验证命令**

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_app_snapshots.py -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_app_snapshots.py \
  --snapshot-report /private/tmp/icarus-tui-snapshot-report.html -q
```

只有人工确认是预期变化时，才运行：

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_app_snapshots.py \
  --snapshot-update -q
```

### 任务十五：无模型完整 Shell Replay 与真实 PTY

**更新文件**

- `apps/tui/scripts/replay_events.py`
- `apps/tui/test/test_replay.py`

**验证内容**

- 默认 replay 输出与 golden 一致；
- `--tui-real` 使用完整 Icarus shell 和相同 fixture；
- 自动输入第一、第二、第三条以观察运行中编辑和 QueuePanel；
- 人工确认 Ctrl+C 撤回队尾、完成后自动 FIFO、应用内滚动和空闲退出；
- 真实 PTY 验证 alternate screen 进入/恢复、resize、中文宽字符和退出码 0；
- 测试使用临时 `ICARUS_DATA_DIR`，不污染用户真实 Session；
- live Agent 只在凭据可用且确定性测试全部通过后执行一次最小 smoke。

**命令**

```bash
apps/agent/.venv/bin/python apps/tui/scripts/replay_events.py \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
apps/agent/.venv/bin/python apps/tui/scripts/replay_events.py \
  --tui-real --speed 8 apps/tui/test/fixtures/synthetic_tui_events.jsonl
```

### 任务十六：更新文档并做应用与打包回归

**更新文件**

- `apps/tui/README.md`
- `README.md`
- `apps/tui/docs/arch/tui-terminal-framework-design.md`
- `docs/todo/tui.md`

**开发内容**

- 把第一阶段串行说明标为历史，不再宣称保留原生 scrollback；
- 记录 Textual 全屏、应用内滚动、持久 Composer、队列和快捷键；
- 如实记录第三类 Ctrl+C 尚不支持真实取消；
- 保留 `docs/todo/agent-core.md` 中 task-scoped cancel TODO；
- 说明默认新 Session 和未来历史加载边界；
- 更新安装命令与依赖，不暴露开发 replay 为生产入口。

**最终回归顺序**

```bash
# TUI
apps/agent/.venv/bin/python -m pytest apps/tui/test -q

# Agent 应用层与全量回归
apps/agent/.venv/bin/python -m pytest apps/agent/test/application -q
apps/agent/.venv/bin/python -m pytest apps/agent/test -q

# 编译与格式
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src apps/agent/test apps/tui/src apps/tui/test
git diff --check

# Wheel 内容和仓库外入口
apps/agent/.venv/bin/python -m build --wheel
```

Wheel 验收必须确认：

- 包含 `apps/tui/src/styles.tcss`；
- 不包含 `apps/tui/test`、文档、snapshot report 或旧 input/repl/renderer；
- 临时虚拟环境安装后，在非仓库目录执行 `icarus --help` 成功；
- Replay shell 和真实 `icarus` 都能正常进入、退出并恢复终端。

## 故障处理原则

- Runtime start 失败：UI 保留错误状态，禁止调度，允许退出；
- submit 失败：队首不丢失，不自动忙重试；
- Subscription 意外关闭：不伪造当前任务完成，进入 fatal 状态并统一清理；
- Projector 或 Widget 更新失败：显示可读错误，Composer 与退出路径继续可用；
- unknown source/Event：忽略并诊断，不自动显示内部结构；
- unrelated Task Event：不改变 active task，不触发下一条；
- Worker 被取消：不能绕过 subscription 和 Service 清理；
- Snapshot 不一致：先区分语义变化、视觉变化和环境抖动，不能直接覆盖基线；
- 测试出现无关既有失败：单独报告，不在本次迁移中顺手重构。

## 完成标准

- 全局 `icarus` 进入 Textual 全屏欢迎页；
- Composer 始终固定可见，Agent 输出期间可编辑并提交；
- 本地队列显示准确，正常 FIFO、撤回 LIFO；
- submit/Event 的早期事件竞态有确定性测试且不丢输出；
- Agent Markdown、工具、错误和终态正确分段；
- `agent` 与 `user-input` 通过来源 Projector 显式接入；
- 未知 Plugin/Event 不意外泄漏到 UI；
- 四级 Ctrl+C、Ctrl+D、exit/quit 与清理语义通过 Pilot；
- Transcript golden 和 Snapshot 回归稳定；
- 已直接查看初始、流式、排队、工具错误和窄终端截图；
- 无模型完整 shell replay 和真实 PTY 通过；
- TUI 测试、Agent 全量测试、compileall、diff check 和 wheel 验收通过；
- 不存在新旧两套 TUI 主流程；
- 没有伪造任务取消，也没有改变 Blackboard History 所有权。
