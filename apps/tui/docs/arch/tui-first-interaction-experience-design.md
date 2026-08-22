# TUI First Interaction Experience Design｜TUI 首次交互体验设计

## 文档定位

本文定义 Icarus Textual TUI 第一轮体验修复，包括：

- 命令启动后快速进入可编辑页面，并在 TUI 挂载后并发初始化 Agent Runtime；
- 初始页面不展示初始化状态；只有消息提交时 Runtime 尚未 Ready，才展示等待反馈；
- 修复顶部 Header 和底部 Composer / StatusBar 的布局重叠；
- 支持在 Agent 流式输出期间浏览历史，而不会被新输出持续拉回底部。

本文是 `apps/tui/docs/arch/tui-persistent-input-queue-design.md` 的增量设计。原文中的队列、
事件投影、History 所有权和 `Ctrl+C` 边界继续有效；本文细化其中的启动时序与状态展示，
并替代“每次输出都自动滚底”和互相独立 dock 的布局选择。

实施计划见 `apps/tui/docs/plan/tui-first-interaction-experience-plan.md`。

## 问题与根因

### 命令启动后长时间没有反馈

当前 `main.py` 在 Textual 启动前导入并构造 `AgentRuntimeService`。该导入链会继续加载
AgentFactory、SkillPlugin、NumPy、OpenAI、Anthropic 等模块。`app.py` 和 Event Projector
还通过 Agent 聚合包导入类型，使得即使移动 Service 构造，TUI 壳层本身仍会提前加载
Agent 依赖。

因此当前顺序是：

```text
icarus
→ 导入大部分 Agent Runtime
→ 构造 AgentRuntimeService
→ Textual app.run()
→ 用户第一次看到页面
```

问题不是页面没有显示 Loading，而是页面根本还没有运行。

### Header、Composer 与 StatusBar 重叠

`app-title` 与 `workspace-label` 都独立 `dock: top`；`composer-shell` 与 `status-bar` 都独立
`dock: bottom`。同方向的兄弟 Widget 竞争同一屏幕边缘，导致区域重叠。Composer 的 dock
宽度与外部 margin 组合还会让右边框超出屏幕。

### 流式输出期间无法稳定上滚

`ConversationView` 在每个 Delta、Tool、Error 和 Finish Action 后都排队执行
`scroll_end()`。用户即使成功上滚，下一条输出也会覆盖阅读位置。Composer 默认保持焦点，
其 `TextArea` 又会消费方向键和翻页键，因此还需要明确按键路由。

## 方案比较

### 启动方式

| 方案 | 优点 | 问题 | 结论 |
| --- | --- | --- | --- |
| 首帧前同步初始化 | 实现直接 | 重型导入阻塞 Textual，用户仍会面对长时间空白 | 不采用 |
| 页面挂载后并发初始化，初始状态静默 | 页面先出现，Runtime 同时准备；多数情况下首条消息可直接发送 | 需要区分内部初始化状态和用户可见等待状态 | 采用 |
| 首次有效提交才初始化 | 空闲页面没有初始化成本 | 必然把全部初始化耗时推迟到首次交互 | 不采用 |
| TUI 与 Runtime 拆成两个进程 | 隔离和可取消性最强 | 需要 IPC、跨进程事件和退出协议，当前范围过重 | 不采用 |

### 布局方式

| 方案 | 问题 | 结论 |
| --- | --- | --- |
| 给 Composer 增加固定 bottom margin | 与 StatusBar 高度耦合，不能解决顶部重叠和右边框裁切 | 不采用 |
| 新增复杂 Header / Footer 布局组件 | 能工作，但当前组件顺序已经足够表达结构 | 暂不采用 |
| 所有兄弟区域进入同一垂直流 | 结构与视觉顺序一致，无魔法间距，改动最小 | 采用 |

### 滚动跟随方式

| 方案 | 问题 | 结论 |
| --- | --- | --- |
| 每次输出强制 `scroll_end()` | 用户无法阅读历史 | 不采用 |
| 自行维护滚动位置与未读计数 | 功能完整，但当前没有未读提示需求，状态和 resize 处理偏重 | 暂不采用 |
| 使用 Textual 原生 anchor / release-anchor | 内容增长时跟随，用户滚动时自动脱离，到底部后恢复 | 采用 |

## 首帧后并发初始化与按需反馈

### 用户可见行为

打开 TUI 时：

- 欢迎页、Conversation、Composer 和操作提示立即可见；
- Composer 立即获得焦点并可编辑；
- 不显示 `Initializing`、`Starting` 或虚假的 `Ready`；
- 首帧路径不执行 Agent Runtime 的重型导入、构造或 Session 创建；
- TUI 挂载后立即创建唯一 Bootstrap Worker，在不阻塞 Textual 事件循环的前提下并发准备
  Runtime。

后台初始化独立于用户输入：

```text
Textual mount
→ 启动唯一 Bootstrap Worker
→ 工作线程导入 AgentRuntimeService 与默认 Projector 模块
→ 主事件循环构造 AgentRuntimeService 与 Projector Registry
→ await service.start()
→ 创建 OutputEventSubscription
→ 启动 Event Worker
→ Runtime Ready，等待用户输入
```

首次提交非空且不是 `exit` / `quit` 的内容时，根据 Runtime 状态分流：

```text
READY
  → 原文进入 TUI pending deque
  → 按正常流程立即 submit 队首

STARTING
  → 原文进入 TUI pending deque
  → QueuePanel 显示待发送消息
  → 状态显示 Initializing runtime
  → Runtime Ready 后自动 submit 队首

FAILED
  → 原文保留在 TUI pending deque
  → 展示初始化失败，不发起 submit
```

初始化期间的后续提交继续进入本地队列，不能重复创建 Runtime。`Initializing` 不是全局常驻
启动状态，而是“已有用户消息正在等待 Runtime”的反馈。订阅必须先于第一次 `submit()` 建立，
继续保留当前对早到 `InputQueuedEvent` 的顺序保护。

### 轻量导入边界

首帧前允许加载 Python 标准库、Textual 和 TUI 自有模块，但不得加载 Agent Runtime、
Skill、模型 Provider SDK 或 Embedding 实现。

为此：

- `main.py` 通过异步 Runtime factory 在线程内局部导入 `AgentRuntimeService` 类型，再回到
  Textual 所在事件循环构造 Service；
- `app.py` 使用 TUI 自有 Protocol 描述 `start / subscribe_events / submit / stop`；
- 只用于类型标注的 Agent 类型不在运行时导入；
- `ProjectorRegistry` 的通用入口接收 `object`，具体 Agent Event 类型只在创建默认 Projector
  时加载；
- 默认 Projector Registry 随挂载后的 Runtime 初始化创建，不在 App 构造或首帧路径创建；
- 不修改 Agent Core 来迁就 TUI 首帧。

同步重型 import 不能直接在 Textual 事件循环执行，否则页面会在显示后冻结。模块导入在
工作线程完成；Service 构造、`service.start()`、订阅、Event Worker 和 UI 状态转换在
Textual 所在的 asyncio 生命周期执行。当前实测 Service 构造约 0.02 秒，没有必要为了这点
耗时跨线程创建持有 asyncio 组件的对象。默认 Projector 的重型类型导入也遵循同一边界。

### 状态与所有权

TUI Runtime phase 保持为真实 Runtime 生命周期：

```text
starting      页面已可编辑，Runtime 正在后台构造或启动
ready         Runtime 与输出订阅可用，当前没有活动任务
running       Runtime 已接受一个活动任务
failed        初始化、订阅或提交失败
stopping      TUI 正在退出和清理
```

Runtime 在页面挂载后进入 `starting`，但内部 phase 不直接等于用户文案。StatusBar 仅在
`starting` 且 `pending` 非空时显示 `Initializing`；没有消息等待时只显示正常操作提示。这样既不
伪装成 `ready`，也不会让尚未采取操作的用户看到无意义的启动日志。

Runtime factory 只负责创建现有 `AgentRuntimeService`，不是第二个 Service 层或全局生命周期
管理器。IcarusTextualApp 仍然拥有单个 Runtime、单个订阅和单个 Event Worker。

### 失败与退出

- Factory 失败：`service` 保持为空；如果已有待发送消息则保留队首并显示初始化错误；
- `service.start()` 失败：保留队首，调用 Runtime 既有失败清理，不创建订阅；
- 订阅失败：保留队首，停止已经创建的 Service；
- 初始化失败后不自动循环重试，避免配置错误导致忙循环；
- 用户仍可通过 `Ctrl+C` 从队尾恢复完整待发送内容并安全退出；
- 本阶段不新增独立 Retry 按钮或命令，显式恢复流程由后续提交恢复设计处理；
- 初始化刚开始、尚未构造 Service 时退出，不得盲目调用 `service.stop()`；
- 初始化途中退出时，停止接收输入，等待正在进行的导入/构造/启动到达可清理边界，再确保 Service
  最多停止一次；
- RuntimeStarted 与退出竞争时，迟到的订阅必须立即关闭，不能启动新的调度。

工作线程中的 Python import 不能被可靠强制终止，因此本阶段不伪造“立即取消初始化”。TUI
挂载后启动的初始化即使尚无用户消息也可以完成并进入 idle ready；退出时必须等待其到达可清理
边界，避免泄漏一个半构造 Runtime。

## 布局设计

Widget 的 Compose 顺序就是唯一的垂直结构：

```text
App title        fixed 1 row
Workspace        fixed 1 row; narrow mode may hide
Conversation     1fr; minimum usable viewport
QueuePanel       auto; capped; empty时隐藏
Composer shell   auto; capped; overflow内部处理
StatusBar        fixed 1 row
```

`app-title`、`workspace-label`、`composer-shell` 和 `status-bar` 不再独立 dock。Conversation
是唯一消费剩余空间的 `1fr` 区域。宽屏、窄屏、Composer 一行至八行以及窗口 resize 时，各
区域必须满足：

```text
title.bottom <= workspace.y
composer.bottom <= status.y
composer.right <= screen.right
```

短窗口优先让 QueuePanel 和 Composer 使用内部滚动，不通过覆盖 StatusBar 获得空间。实现时
根据 Textual 实际布局验证支持的最小高度，不添加针对单张截图的固定坐标。

## 滚动与焦点设计

Conversation 默认使用 Textual anchor 跟随最新输出：

```text
FOLLOWING
  新内容或 Markdown 重排 → 保持底部
  用户向上滚动          → Textual release anchor

DETACHED
  新内容到达            → 保持当前阅读位置
  滚动到底部 / Ctrl+End → 恢复 anchor
```

移除每个 UiAction 后无条件执行的 `_scroll_to_latest()`，不额外复制一套滚动状态机。

按键与指针规则：

| 当前交互对象 | `↑ / ↓` | `PageUp / PageDown` | 滚轮 | `Home / End` |
| --- | --- | --- | --- | --- |
| Conversation | 按行滚动对话 | 按页滚动对话 | 滚动对话 | 对话首尾 |
| Composer | 移动编辑光标 | 跨焦点滚动对话 | 不改变 Conversation | 编辑行首尾 |

`Ctrl+End` 是全局恢复跟随动作：滚动到 Conversation 底部并恢复 anchor，同时保持 Composer
的焦点、文本、Selection 和 Cursor。Conversation 只有在自己获得焦点时才响应滚轮；
Composer 获得焦点时，TUI 不把滚轮转发给 Conversation。实现保持 Widget 局部处理，不增加
App 全局滚轮路由。

本阶段不增加“有 N 条新内容”提示；这是独立的后续增强，不是解决无法上滚的必要条件。

## 维护性与范围判断

该设计属于聚焦式结构修复：

- 比只移动一行 import 更完整，因为同时切断 TUI 壳层的 Agent 类型导入；
- 比新增进程和 IPC 更轻，不改变 AgentRuntimeService 的公开契约；
- 布局使用正常 Flow，不引入 magic margin；
- 滚动使用 Textual 原生能力，不维护重复状态；
- 后台初始化复用现有本地 deque、订阅握手和 Runtime phase；
- 所有变化留在 `apps/tui`，不反向修改 Agent Kernel、Plugin Runtime 或 Blackboard。

本阶段明确不实现：

- 加速 Agent Runtime 自身的真实初始化工作；
- TUI / Runtime 子进程与 IPC；
- Runtime 初始化进度百分比；
- 初始化失败自动重试或新的恢复面板；
- 任务级取消、Session 恢复和历史重建；
- 未读消息计数；
- Agent Event 或 History 语义修改。

## 验收标准

- 执行 `icarus` 后先出现可编辑 TUI，不再等待 Agent 重型依赖导入；
- 首帧可见前不执行 Agent Runtime 重型导入、Service 构造或 Session 创建；
- TUI 挂载后只创建一个 Bootstrap Worker，并在后台初始化 Runtime；
- 初始 StatusBar 不显示 `Starting`、`Initializing` 或虚假的 `Ready`；
- Runtime 已 Ready 时，首次有效提交不闪现 `Initializing`，直接进入正常提交；
- Runtime 尚在 Starting 时，首次有效提交立即显示 `Initializing`，Ready 后自动提交队首；
- 初始化期间提交多条消息时保持 FIFO，Ready 后只提交队首；
- Factory、start 或 subscribe 失败时队首不丢失，程序仍可安全退出；
- 订阅建立在首次 `submit()` 之前；
- 顶部与底部组件在宽窄窗口、多行 Composer 和 resize 后不重叠、不越界；
- 用户上滚后，新 Delta、Tool、Error 和 Finish 不把 Conversation 拉回底部；
- `PageUp / PageDown` 和 `Ctrl+End` 不破坏 Composer 草稿、光标或焦点；
- TUI 仍只通过 `AgentRuntimeService` 的公开接口控制 Agent；
- 不修改 Agent Core、Event schema 或 Blackboard History 所有权。
