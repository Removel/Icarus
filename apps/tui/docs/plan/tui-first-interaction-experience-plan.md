# TUI First Interaction Experience Implementation Plan｜TUI 首次交互体验实施计划

## 目标与依据

依据 `apps/tui/docs/arch/tui-first-interaction-experience-design.md`，完成三项当前已复现问题：

1. `icarus` 命令先快速进入可编辑页面，TUI 挂载后并发初始化 Runtime；消息提交时仅在
   Runtime 尚未 Ready 的情况下显示 `Initializing` 并等待；
2. 修复 Header、Composer 和 StatusBar 的确定性布局重叠；
3. 支持流式输出期间稳定浏览历史，并按已确认焦点规则处理按键和滚轮。

本计划不修改 `apps/agent/src/`，不优化 Runtime 内部真实初始化耗时，不引入子进程、IPC、
任务取消或新的 Agent Event。

## 实施原则

- 每项先补最小失败测试，再修改实现；
- 首帧与 Runtime Ready 分开验证；
- 保留订阅先于首次 submit 的顺序；
- 初始化或 submit 失败前不移除本地队首；
- 复用现有 ChatState、deque、Textual Worker 和 AgentRuntimeService；
- 不新增通用 Lifecycle Manager、TUI Service wrapper 或跨应用公共包；
- Snapshot 通过后必须直接查看原始 SVG / PNG，不能只看 pytest 结果。

## 任务一：建立轻量 CLI 导入回归基线

**更新文件**

- `apps/tui/test/test_cli.py`

**测试内容**

- fresh subprocess 只导入 `apps.tui.src.main` 后，`sys.modules` 中不存在：
  - `apps.agent.src.application.agent_runtime_service`；
  - `apps.agent.src.agent_orchestration.agent_factory`；
  - `openai`、`anthropic`、`numpy`、`fastembed`、`onnxruntime`；
- `--help` 不导入或构造 Runtime；
- `run_app()` 捕获当前 Workspace 和 `--session-id`，但在 `app.run()` 和首帧前不调用 Runtime
  factory；
- App Stub 模拟挂载并启动 Bootstrap Worker 时，参数才用于构造 Service。

**基线测量**

- 记录修改前 fresh-process 的 `import apps.tui.src.main` 时间与模块集合；
- 性能数值不作为易抖动的单元测试阈值，模块边界作为稳定 CI 断言；
- 最终用真实 PTY 重复测量 `command → first visible frame`。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_cli.py -q
```

## 任务二：切断首帧前的 Agent 导入链

**更新文件**

- `apps/tui/src/main.py`
- `apps/tui/src/app.py`
- `apps/tui/src/event_pipeline/dispatcher.py`
- `apps/tui/test/test_cli.py`

**开发内容**

- `main.py` 不再在模块顶层导入或构造 `AgentRuntimeService`；
- 定义一个异步 Runtime factory：通过 `asyncio.to_thread()` 在线程内局部导入
  `AgentRuntimeService` 类型以及默认 Projector 依赖的具体 Agent 模块，回到 Textual 事件循环后
  构造真实 Service 和 Projector Registry；线程阶段只完成模块导入，不创建持有 asyncio 状态的
  Runtime 对象；
- `IcarusTextualApp` 接收 factory，而不是已构造的生产 Service；
- `app.py` 的 RuntimeService、RuntimeSubscription 和 SubmitResult 使用最小 TUI Protocol；
- 运行时 Event 类型按 `object` 通过 Subscription 与 Projector Registry，Agent 类型只留在
  具体 Projector；
- `dispatcher.py` 移除通用层对 Agent `Event` 的运行时 import；
- `create_default_projector_registry()` 保持具体 Projector 的局部 import，并延迟到 Runtime
  初始化过程；
- App 模块导入不得触发 Agent package 聚合 `__init__.py`；
- 不修改 Agent 聚合包或 Provider Factory，本阶段只建立 TUI 边界。

**维护性约束**

- Factory 只创建现有 Service，不包装或复制其业务接口；
- 不为未来 WebUI / GUI 提前抽取公共 Bootstrap 包；
- 测试和 Replay 也通过同一个 factory seam 注入 Stub Service。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_cli.py apps/tui/test/event_pipeline -q
apps/agent/.venv/bin/python -c 'import apps.tui.src.main'
```

## 任务三：实现后台 starting 与按需 Initializing 反馈

**更新文件**

- `apps/tui/src/chat_state.py`
- `apps/tui/src/widgets/status_bar.py`
- `apps/tui/src/app.py`
- `apps/tui/test/test_chat_state.py`
- `apps/tui/test/test_app.py`

**先补状态测试**

- 初始 Runtime phase 是 `STARTING`，但 pending 为空时不显示生命周期标签；
- 挂载只允许创建一个 Bootstrap Worker 和一个 Runtime；
- STARTING 期间提交消息只 enqueue，不创建第二个 Runtime；
- STARTING 成功后进入 READY，再按现有条件 dispatch；
- Factory、start 或 subscribe 失败进入 FAILED，pending 队首保持原样；
- 尚未构造 Service 的 STARTING 可以直接进入 STOPPING，清理逻辑不得假定 Service 存在。

**开发内容**

- `on_mount()` 设置 narrow class、Composer focus 和初始界面后，立即创建唯一 Bootstrap Worker；
- StatusBar 在 STARTING 且 pending 为空时只显示操作提示，不显示 `Starting` 或
  `Initializing`；
- 首次非空、非退出命令提交后先 enqueue 原始文本并刷新 QueuePanel，再按状态处理：
  - READY：按正常流程立即调度队首，不显示 `Initializing`；
  - STARTING：显示 `Initializing runtime`，等待既有 Bootstrap Worker，不重复启动初始化；
  - FAILED：保留队首并显示初始化失败，不调用 submit；
- 调用异步 factory，确保 Runtime 与默认 Projector 的重型 import 都在工作线程完成，Service 和
  Projector Registry 的实例构造仍位于 Textual event loop；
- 回到 async 生命周期后执行 `await service.start()`；
- 使用已经完成模块预加载的默认 Projector Registry，并创建唯一 Output Subscription；
- 启动 Event Worker 后 mark ready，再调用 `_schedule_dispatch()`；
- 多条初始化期间输入只追加 deque，成功后保持 FIFO；
- 保留 `submit()` 成功前不 `popleft()` 的现有握手。

**App 集成测试**

- mount 后 Composer 已聚焦、唯一 factory/start 已开始、但无初始化状态文案；
- factory/start 阻塞时，首条提交立即显示 pending 与 `Initializing`；
- Runtime 在首条提交前已 Ready 时，消息立即进入正常提交且不闪现 `Initializing`；
- 阻塞 factory 或 start 时，界面仍响应，第二和第三条继续排队；
- 顺序严格为 mount → factory → start → subscribe → event worker → submit；
- 空白输入、`exit` 和 `quit` 不进入待发送队列，也不改变已经开始的后台初始化；
- `InputQueuedEvent` 在 submit 返回前到达仍不被误丢弃；
- 初始化成功后只提交一个队首，后续仍按当前任务终态逐条调度。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/tui/test/test_chat_state.py \
  apps/tui/test/test_app.py -q
```

## 任务四：收口初始化失败与退出清理

**更新文件**

- `apps/tui/src/app.py`
- `apps/tui/test/test_app.py`

**开发内容**

- Service 字段允许在 STARTING 的早期阶段或 factory 失败时为空；
- Factory 失败不调用 `stop()`；
- start 失败依赖 AgentRuntimeService 既有失败清理，并保证 App 不创建订阅；
- subscribe 失败时停止已启动 Service；
- 初始化失败不自动重试，不移除 pending 队首；
- shutdown 顺序统一为：
  1. 禁止新输入与调度；
  2. 等待或收口 Bootstrap Worker；
  3. 关闭已经存在的订阅；
  4. 停止 Event Worker；
  5. 对已经构造的 Service 最多调用一次 stop；
  6. 退出 Textual；
- RuntimeStarted 在 shutdown 后迟到时立即关闭 Subscription，不重新调度；
- 用户在初始化期间撤回全部消息时，不强制取消不可中断的 Python import；初始化完成后可以
  保持 READY idle。

**测试内容**

- factory 抛错、start 抛错、subscribe 抛错各自保留完整队列；
- Service 尚未构造时退出不调用 `stop()`，但必须收口已经启动的 Bootstrap Worker；
- 阻塞 factory、阻塞 start 和已 Ready 三种状态退出均最终完成清理；
- shutdown 与 RuntimeStarted 竞争不泄漏订阅，不重复 stop；
- 清理失败产生非零退出码，并保留可诊断错误。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_app.py -q
```

## 任务五：修复垂直布局结构

**更新文件**

- `apps/tui/src/styles.tcss`
- `apps/tui/test/test_app.py`
- `apps/tui/test/test_app_snapshots.py`

**开发内容**

- 移除 `app-title` 和 `workspace-label` 的独立 top dock；
- 移除 `composer-shell` 和 `status-bar` 的独立 bottom dock；
- 保持 Compose 顺序作为唯一垂直结构；
- Conversation 是唯一 `height: 1fr` 的区域，并保留最小可用高度；
- QueuePanel 与 Composer 使用 auto + max-height，超出后内部滚动；
- 窄屏继续隐藏 Workspace，保留现有水平 margin 规则；
- 根据 58×12 实测增加通用短窗口约束，不添加针对截图的绝对坐标。

**几何回归**

在 100×30、58×24 和 58×12 下，分别覆盖一行和八行草稿：

- `title.region.bottom <= workspace.region.y`（Workspace 可见时）；
- `composer.region.bottom <= status.region.y`；
- `composer.region.right <= screen.region.right`；
- StatusBar 始终可见；
- resize `100×30 → 58×12 → 100×30` 后重复断言。

**Snapshot**

- 更新初始静默 STARTING 页面；
- 增加八行 Composer + Queue + Status 的宽屏与窄屏状态；
- 只更新已确认的布局差异。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/tui/test/test_app.py \
  apps/tui/test/test_app_snapshots.py -q
```

## 任务六：实现可脱离的自动跟随与焦点路由

**更新文件**

- `apps/tui/src/widgets/conversation.py`
- `apps/tui/src/app.py`
- `apps/tui/test/widgets/test_conversation.py`
- `apps/tui/test/test_app.py`

**先补 Conversation 测试**

- 初始 anchored，持续 Delta 时保持在底部；
- Conversation 上滚后，新 Delta 不改变阅读位置；
- Tool start/completed、Error 和 Finish 同样不强制滚底；
- 连续多次输出与 Markdown 高度变化时保持 detached；
- 滚到底部或调用 resume-follow 后，后续输出继续跟随；
- resize 后 FOLLOWING / DETACHED 语义不反转。

**开发内容**

- `ConversationView.on_mount()` 启用 Textual anchor；
- 删除每个 Action 后调用的 `_scroll_to_latest()` 与延迟 callback；
- 使用 Textual 自身的用户滚动 release-anchor 和到底部恢复逻辑；
- App 增加 priority binding：
  - `PageUp` → Conversation page up；
  - `PageDown` → Conversation page down；
  - `Ctrl+End` → Conversation end + resume anchor；
- 不增加全局 wheel handler；后续实现由
  `apps/tui/docs/arch/tui-streaming-markdown-scroll-design.md` 修正为按指针所在区域路由，
  Conversation 不再要求自己持有键盘焦点；
- Conversation 聚焦时方向键和 Home / End 使用 VerticalScroll 原生行为；
- Composer 聚焦时方向键和 Home / End 继续编辑文本。

**App 集成测试**

- Composer 有多行草稿、Selection 和 Cursor 时，PageUp / PageDown 只滚 Conversation；
- 操作前后 Composer focus、text、selection、cursor 完全不变；
- Conversation 聚焦后 Up / Down 可滚动，流式 Delta 不抢回底部；
- Composer 聚焦时 Up / Down 只移动编辑光标；
- `Ctrl+End` 恢复跟随，下一 Delta 到达后仍位于新底部；
- 指针位于 Conversation 时滚轮滚动 Conversation，即使 Composer 保持焦点；指针位于
  Composer 时不改变 Conversation。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/tui/test/widgets/test_conversation.py \
  apps/tui/test/test_app.py -q
```

## 任务七：适配 Replay、Snapshot 与完整验证

**更新文件**

- `apps/tui/src/replay.py`（仅在 factory seam 需要时）
- `apps/tui/scripts/replay_events.py`
- `apps/tui/test/test_replay.py`（仅在接口变化需要时）
- `apps/tui/test/test_app_snapshots.py`
- `apps/tui/test/__snapshots__/test_app_snapshots/*.svg`
- `apps/tui/README.md`
- `apps/tui/docs/arch/tui-persistent-input-queue-design.md`
- `docs/todo/tui.md`

**开发内容**

- Replay 与 Snapshot Service 通过返回既有 Stub 的 async factory 使用同一个后台 factory
  seam；
- replay 不成为第二个生产 Runtime 入口；
- 初始 snapshot 使用可控的阻塞 factory/start，验证后台初始化进行中但页面保持静默；
- 其他 snapshot 等待 Replay Runtime Ready 或在 STARTING 时提交消息，以分别覆盖正常提交和
  `Initializing`；
- 更新 README 的首帧后台初始化、按需等待反馈和滚动按键说明；
- 在旧 Textual 设计顶部标注本文替代的启动、布局和滚动部分；
- 对应 TODO 只有在实现与全部验证完成后才勾选。

**完整自动化验证**

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test
git diff --check
```

**无模型 Shell 验证**

```bash
apps/agent/.venv/bin/python apps/tui/scripts/replay_events.py \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl

apps/agent/.venv/bin/python apps/tui/scripts/replay_events.py \
  --tui-real \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
```

**性能与真实终端验收**

- 至少 20 次 fresh process 记录 `command → first visible frame`，目标 p50 ≤ 500ms、
  p95 ≤ 1s；环境安全扫描导致异常值时单独记录，不隐藏；
- 首帧出现时确认 Agent、Provider、Skill 重依赖尚未加载；随后确认后台初始化已经开始并最终
  Ready；
- factory / start 人为阻塞 10 秒且尚未提交消息时，页面保持安静且 Composer 可用；此时提交
  首条消息才显示 `Initializing`，队列仍可继续接收输入；
- Runtime 已 Ready 后提交首条消息时不显示 `Initializing`；消息等待初始化成功后则自动提交，
  不需要用户再次按 Enter；
- 用长流式 fixture 验证滚轮、PageUp / PageDown、方向键和 Ctrl+End；
- 验证退出后恢复启动 Icarus 前的终端画面。

**强制视觉审查**

1. 生成 snapshot report；
2. 先关闭 difference overlay 查看原始 current SVG；
3. 检查初始静默 STARTING、消息等待时的 Initializing、八行 Composer、窄屏、流式 detached
   五类状态；
4. 再检查 historical 与差异；
5. 未直接查看图片不得声明视觉验证通过。

## 提交拆分建议

如果用户后续明确要求提交，按逻辑拆分：

1. `fix(tui): initialize runtime after first frame`
2. `fix(tui): prevent shell layout overlap`
3. `fix(tui): preserve conversation scroll position`
4. `docs(tui): document first interaction experience`

实现与对应测试放在同一个 commit；当前不创建分支、不 commit、不 push。

## 完成标准

- 首帧前没有 Agent Runtime 重依赖；
- Runtime 在首帧后随 TUI 挂载并发创建，首帧路径不加载 Agent 重依赖；
- 无消息等待时不显示初始化状态；消息撞上 STARTING 时反馈真实、队列不丢失且只创建一个
  Runtime；
- 布局在覆盖尺寸与 resize 后不重叠、不裁切；
- 流式输出期间可以稳定阅读历史并恢复跟随；
- TUI 回归、compileall 和 diff check 全部通过；本阶段未修改 `apps/agent/src/`，不要求运行
  Agent 全量测试；
- 完整 replay 已运行，Snapshot 原图已人工审查；
- 设计、README、TODO 与最终实现一致。
