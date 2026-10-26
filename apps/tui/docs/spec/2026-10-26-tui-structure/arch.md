# TUI Structure Refactor Design｜TUI 结构调整设计

## 文档定位

设计日期：2026-10-26。本文只调整 `apps/tui` 的源代码组织与职责边界，不改变用户可见行为，不修改 Agent、Gateway、RuntimeUpdate、`UiAction`、Session 或队列语义。实施步骤见 [plan.md](plan.md)。

本文替代 `app.py` 继续增长的做法，但不替代既有功能设计：[Agent Thinking Experience](../2026-09-21-agent-thinking-experience/arch.md)、[Windowed Conversation](../2026-09-23-tui-windowed-conversation/arch.md)、[Session History Restoration](../2026-08-29-tui-session-history-restoration/arch.md) 等文档描述的行为保持不变。

## 当前实现与问题

`apps/tui/src` 约 6,300 行，测试约 8,100 行；`app.py` 单文件 1,879 行，`test_app.py` 单文件 3,216 行。TUI 已有 55 个提交、13 个 SVG 快照和约 200 个测试，行为成熟，但职责集中在 `IcarusTextualApp`：

| 职责 | 当前位置（约） | 说明 |
| --- | --- | --- |
| 协议适配、Textual Message | `app.py:72-224` | `RuntimeClient`、`UpdateSubscription`、`PreparedSession` 和 7 个 Message 定义在 App 模块 |
| 应用壳、布局、响应式 | `app.py:225-340` | `compose`、`on_mount`、`on_resize`、快捷键 |
| Runtime 启动、Session 准备/切换/恢复/重连 | `app.py:341-605`、`721-940` | 直接调用 `RuntimeClient`，包含协议错误分支和空 Session 丢弃 |
| 提交与本地命令路由 | `app.py:605-720` | Composer 提交、命令校验、草稿恢复 |
| 剪贴板图片 | `app.py:957-1037` | 读取、临时文件、`ResourceRef`、失败通知 |
| 队列派发与 steer | `app.py:1038-1214` | reservation、submit/steer、重试、阻断、fatal |
| RuntimeUpdate 消费与对账 | `app.py:1215-1403` | sequence、early updates、task 状态对账、历史回放 |
| `UiAction` 应用、状态栏、队列面板 | `app.py:1404-1602` | 页面渲染与业务判断混合 |
| 中断、取消、EOF、翻页 | `app.py:1613-1710` | 输入策略与 Runtime 操作混合 |
| 关闭与资源释放 | `app.py:1711-1879` | 7 段重复的 worker 取消样板 |

问题不在"文件长"本身，而在于同一个类同时有五种变更理由：协议、会话生命周期、队列策略、输入策略、页面渲染。任何功能都要改 `app.py`，对应测试也只能堆进 `test_app.py`。

现有代码已有三块良好基础，应保留：

- `gateway_client/`：JSON-RPC 传输、Session 握手、错误映射，完全不依赖 Textual；
- `event_pipeline/`：`RuntimeUpdate` → `UiAction` 的纯投影，不依赖 Textual；
- `chat_state.py`：纯队列/阶段状态机，不依赖 Textual。

因此这是一次**行为保持的抽取**，不是重写。

## 目标与非目标

### 目标

1. `App` 只保留进程生命周期、全局按键、主题和 Screen 路由，目标 150–250 行。
2. 主页面由 `ChatScreen` 拥有：布局、焦点、子组件消息路由、页面级动作，目标 250–450 行。
3. Gateway/Session/队列/事件消费/输入/剪贴板逻辑进入普通 Python service，不 import Textual。
4. 纯状态、投影和数据模型保持无框架依赖。
5. 配置收敛为一个不可变对象，由入口构造并向下传递。
6. 测试目录镜像源代码职责；service 可以用普通 pytest 测试。
7. 13 个视觉快照和现有行为保持不变；新增导入方向测试防止结构回退。

### 非目标

- 不迁移到 Ink 或其他框架。
- 不修改 Agent/Gateway/协议/模型供应商分支。
- 不重新设计视觉、键位或交互语义。
- 不重写 `ConversationView`、`TurnRail`、`Composer` 的内部渲染逻辑。
- 不引入通用 DI 容器、TUI 内部 EventBus 或 presenter 层。
- 不清理 `messages.py` 中对 Textual 私有 API 的依赖；那是独立的兼容性加固任务。

## 目标结构

```text
apps/tui/src/
├── main.py                    # argparse → TuiConfig → IcarusTextualApp(config).run()
├── config.py                  # TuiConfig（不可变）
├── app.py                     # App：生命周期、全局 bindings、push ChatScreen
├── messages.py                # 只在 Screen 边界使用的 Textual Message
├── screens/
│   ├── chat.py                # ChatScreen：compose、焦点、子组件消息、页面动作
│   └── session_picker.py      # 既有 ModalScreen
├── widgets/                   # 既有组件，不改变公开接口
├── services/
│   ├── types.py               # PreparedSession、SessionBusyError、服务结果类型
│   ├── session_service.py     # Runtime 客户端、Session 准备/切换/重连/关闭
│   ├── update_stream.py       # 订阅消费、sequence、early updates、task 对账
│   ├── dispatch_service.py    # reservation → submit/steer → 结果/重试/阻断
│   ├── submission_router.py   # Composer 提交与命令路由（纯逻辑）
│   └── clipboard_service.py   # 图片读取、临时文件、ResourceRef
├── gateway_client/            # 网络层；新增 ports.py
├── chat_state.py              # 纯状态机
├── submission.py              # 纯数据模型与资源辅助函数
├── commands.py                # 命令定义与解析
├── event_pipeline/            # 纯投影
├── styles.tcss
└── test/                      # 镜像 src：services/ screens/ widgets/ ...
```

## 分层契约

| 模块 | 拥有 | 允许依赖 | 禁止依赖 |
| --- | --- | --- | --- |
| `config.py` | 不可变运行配置 | 标准库 | 其他 TUI 模块 |
| `gateway_client/` | JSON-RPC、Session 握手、错误映射 | `packages.gateway_protocol`、`config` | Textual、services、widgets、app |
| `services/` | 会话、派发、事件、输入、剪贴板编排 | gateway、models、config、标准库 | Textual、widgets、screens、app |
| `chat_state.py` / `submission.py` / `commands.py` / `event_pipeline/` | 纯状态、数据、投影 | 标准库、`packages.gateway_protocol` | Textual、gateway、services、widgets、app |
| `widgets/` | 局部渲染与交互 | 纯模型词汇（`UiAction`、`RuntimePhase`） | gateway、services、app |
| `screens/` | 页面布局、焦点、页面编排 | widgets、services、纯模型 | `app`（通过参数/回调获得能力） |
| `app.py` | 生命周期、全局按键、Screen 路由 | 全部下层 | — |
| `main.py` | 入口、参数、config、退出码 | config、app | — |

依赖方向：

```text
main.py ──► app.py ──► screens/ ──► widgets/
                │          │           │
                │          ▼           ▼
                └────► services/ ──► models/
                           │
                           ▼
                    gateway_client/ ──► packages.gateway_protocol
```

### 运行时所有权

- 只有 `Screen` / `Widget` 创建 Textual worker。Service 提供 async 方法，不调用 `run_worker`、`post_message`、`notify` 或 `query_one`。
- 长时间运行的 worker（事件消费、重连、派发、Session 操作、剪贴板）归 `ChatScreen` 所有；`ChatScreen.on_unmount()` 统一取消并等待。
- Service 只管理自己的资源：当前 `RuntimeClient`、`UpdateSubscription`、临时文件。`close()` 返回错误字符串列表，不直接弹出通知。
- 需要回到 UI 线程的结果，由 `ChatScreen` 在 worker 完成后 `post_message`；Textual Message 类集中在 `messages.py`。

### 领域 → 视图协议

继续使用 `event_pipeline.actions.UiAction`，不引入新的 presenter 或双重 ViewModel：

- `UpdateStream` 把 `RuntimeUpdate` 投影成 `tuple[UiAction, ...]`；
- `ChatScreen` 统一调用 `ConversationView.apply_action()`、状态栏和队列刷新；
- `UiAction` 是跨层唯一的数据词汇，Widget 只依赖它和 `RuntimePhase` 等纯类型。

### 交互结果

Service 的一次性结果使用返回值，不通过回调拼装 UI：

- `DispatchService.dispatch_next() -> DispatchOutcome`
- `SessionService.start()/resume()/clear()/reconnect() -> SessionResult`
- `SubmissionRouter.route() -> SubmissionPlan`
- `UpdateStream.project()` 返回 actions，`consume()` 通过 `on_actions` 回调持续投递

只有两类显式回调：

- `SessionService` 接收 `select_session`（SessionPicker）和 `activate`（Screen 执行实际切换）；
- `UpdateStream` 接收 `on_actions`、`on_fatal`、`on_user_input`。

## 服务接口

以下签名是后续任务和测试的契约；实现细节由任务负责。

```python
@dataclass(frozen=True)
class TuiConfig:
    workspace_path: Path
    gateway_url: str = "ws://127.0.0.1:8765/rpc"
    session_id: str | None = None
    resource_root: Path | None = None


class SessionService:
    def __init__(
        self,
        runtime_factory: ClientFactory,
        config: TuiConfig,
        *,
        on_status: Callable[[str], None],
    ) -> None: ...

    @property
    def client(self) -> RuntimeClient | None: ...
    @property
    def subscription(self) -> UpdateSubscription | None: ...
    @property
    def epoch(self) -> int: ...
    @property
    def session_id(self) -> str | None: ...

    async def start(self, initial_session_id: str | None) -> SessionResult: ...
    async def resume(
        self,
        select_session: SessionSelector,
        activate: SessionActivator,
    ) -> SessionResult: ...
    async def clear(self, activate: SessionActivator) -> SessionResult: ...
    async def reconnect(self, last_sequence: int) -> SessionResult: ...
    async def close(self) -> list[str]: ...


class UpdateStream:
    def __init__(
        self,
        projectors: ProjectorRegistry,
        chat_state: ChatState,
        *,
        session_scope: Callable[[], tuple[str | None, str | None]],
        on_actions: Callable[[tuple[UiAction, ...]], Awaitable[None]],
        on_fatal: Callable[[str, BaseException], None],
        on_user_input: Callable[[], None],
    ) -> None: ...

    def reset(self, last_sequence: int = 0) -> None: ...
    async def project(
        self, update: RuntimeUpdateModel, *, historical: bool = False
    ) -> tuple[UiAction, ...]: ...
    def buffer(self, update: RuntimeUpdateModel) -> None: ...
    def take_early(self) -> tuple[RuntimeUpdateModel, ...]: ...
    async def reconcile(
        self, client: RuntimeClient, task_id: str
    ) -> tuple[UiAction, ...]: ...


class DispatchService:
    def __init__(
        self,
        chat_state: ChatState,
        client: Callable[[], RuntimeClient | None],
        *,
        resource_root: Path | None,
        on_status: Callable[[str], None],
        on_notify: Callable[..., None],
    ) -> None: ...

    async def dispatch_next(self, *, epoch: int) -> DispatchOutcome: ...
    async def cancel_active(self, *, epoch: int) -> DispatchOutcome: ...


class SubmissionRouter:
    def __init__(self, commands: CommandRegistry) -> None: ...

    def route(
        self,
        submission: PendingMessage,
        *,
        phase: RuntimePhase,
        can_run_session_command: bool,
    ) -> SubmissionPlan: ...


class ClipboardService:
    def __init__(self, resource_root: Path | None) -> None: ...

    def paste_image(self) -> DraftImage | None: ...
    def resources(self, submission: PendingMessage) -> tuple[ResourceRefModel, ...]: ...
    def discard(self, submission: PendingMessage) -> None: ...
    def cleanup(self) -> list[str]: ...
```

`SessionResult` 包含 `kind`（ready/reconnected/switching/failed/fatal）、`prepared: PreparedSession | None`、`error`、`message`。`DispatchOutcome` 包含 `accepted_task_id`、`refresh_queue`、`schedule_next`、`retry: DispatchReservation | None`、`reconnect_cause: BaseException | None`、`fatal_stage/error`。`SubmissionPlan` 是 `Enqueue`、`RunCommand`、`Reject`、`Unavailable` 的有限联合。

## 控制流

### 启动

```text
main.py → TuiConfig
App.on_mount() → push ChatScreen
ChatScreen.on_mount() → session_service.start()
  → PreparedSession(client, subscription, history)
  → ChatScreen applies historical UiActions
  → starts UpdateStream consumer worker
  → marks ready, focuses Composer
```

### 提交与派发

```text
Composer.Submitted
  → SubmissionRouter.route()
    → Reject/Unavailable: restore draft + status/notify
    → RunCommand: ChatScreen executes local command
    → Enqueue: ChatState.enqueue()
  → ChatScreen schedules dispatch worker
  → DispatchService.dispatch_next()
    → submit / steer / retry / blocked / fatal
  → ChatScreen applies DispatchOutcome:
      accepted → UpdateStream.take_early(), reconcile task, refresh queue
      transport unknown → session_service.reconnect()
      fatal → ChatScreen fatal state
```

### 更新与重连

```text
SessionService.subscription
  → UpdateStream.project()
    → sequence/scope/early-update gates
    → ProjectorRegistry
    → tuple[UiAction, ...]
  → ChatScreen applies actions
```

`UpdateStream.consume()` 的订阅异常交由 `ChatScreen` 触发 `SessionService.reconnect()`；重连成功后，`ChatScreen` 用返回的 history records 逐条 `project(historical=True)`，再替换 subscription/consumer。旧 client/订阅的错误不得串入新 epoch。

### Session 切换

```text
ChatScreen._run_command("/resume" | "/clear")
  → SessionService.resume(select_session, activate) / clear(activate)
    → prepare candidate client + history
    → activate(prepared) 由 ChatScreen 实现：
        shutdown old consumer, apply history, swap subscription/epoch, start consumer
    → SessionService 关闭旧 client，必要时 discard 旧的空 Session
```

### 退出

```text
ChatScreen.request_shutdown()
  → stop accepting input, cancel all workers
  → session_service.close() + clipboard_service.cleanup()
  → app.exit(return_code)
```

`App.on_unmount()` 只作为兜底：若 Screen 未完成清理，关闭已构造的 services，不重复用户可见流程。

## 迁移映射

| 当前 `app.py` | 目标 |
| --- | --- |
| `RuntimeClient` / `ClientFactory` | `gateway_client/ports.py` |
| `PreparedSession` / `SessionBusyError` | `services/types.py` |
| `compose` / `on_mount` / `on_resize` / BINDINGS | `screens/chat.py`（App 只保留 CSS_PATH 与 push） |
| `_start_runtime` / `_prepare_session` / `_apply_prepared_session` / `_resume_session` / `_clear_session` / `_reconnect_runtime` | `services/session_service.py` + ChatScreen 的 `activate` |
| `_consume_runtime_events` / `_project_runtime_update` / `_flush_early_updates` / `_reconcile_task_status` | `services/update_stream.py` |
| `_dispatch_next` / `_send_reservation` / `on_dispatch_finished` / steer 与错误分支 | `services/dispatch_service.py` |
| `on_persistent_composer_submitted` / 命令校验 | `services/submission_router.py` + ChatScreen 执行 |
| 图片粘贴与资源生命周期 | `services/clipboard_service.py` |
| `_refresh_queue` / `_refresh_status` / `_apply_action` / `on_button_pressed` | `screens/chat.py` |
| `action_context_interrupt` / `action_eof` / page actions | `screens/chat.py` + dispatch/session services |
| `_release_runtime_resources` | 各 service `close()` + ChatScreen 的 worker 取消 |

## 测试策略

目标测试目录：

```text
apps/tui/test/
├── test_app.py                 # 启动/退出/Screen 路由的集成测试
├── test_config.py
├── test_layering.py            # AST import 方向检查
├── services/
│   ├── test_session_service.py
│   ├── test_update_stream.py
│   ├── test_dispatch_service.py
│   ├── test_submission_router.py
│   └── test_clipboard_service.py
├── screens/
│   ├── test_chat_screen.py
│   └── test_session_picker.py
├── widgets/                    # 既有
├── gateway_client/             # 既有
├── event_pipeline/             # 既有
└── __snapshots__/              # 13 个既有 SVG
```

- Service 测试使用 fake `RuntimeClient` / `UpdateSubscription`，不启动 Gateway、不调用模型。
- Screen/Widget 测试使用 `App.run_test()` / Pilot；视觉变化仍由 13 个快照保护。
- `test_layering.py` 用 AST 检查：`services/**` 不含 `textual`，`gateway_client/**` 不含 `services`/`widgets`，`widgets/**` 不含 `gateway_client`/`services`/`app`，纯模型模块不含 `textual`。
- 每个阶段先跑对应测试文件，再跑 `make test-tui`、`compileall`、`git diff --check`。
- 不更新快照来消除回归；快照变化必须经过渲染审查。

## 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| Worker 所有权变化导致退出时挂起 | ChatScreen 统一持有 worker 并在 `on_unmount` 取消；service 不创建 worker |
| 从 worker 直接改 Widget 引发时序问题 | 保持 `post_message` 模式，service 返回结果，Screen 投递 Message |
| 队列/steer 状态机在迁移中漂移 | 先写 status/transport 的表格测试，再移动代码；`ChatState` 不改语义 |
| Session 切换时旧 task 更新串入新 Session | epoch/scope 检查保留在 UpdateStream；切换测试覆盖旧订阅失败与迟到更新 |
| `app.py` 抽完后 `ChatScreen` 又变成 god object | Screen 只保留布局、消息路由和结果应用；协议/队列/会话逻辑必须留在 service |
| 测试大规模搬迁产生噪声 | 每阶段只移动该阶段覆盖的测试，保留集成测试直到最后 |
| 私有 Textual API 的兼容风险 | 本设计不触碰；在后续独立任务中收敛适配层 |

## 备选方案

- **迁移 Ink**：成本 6–10 周，且会失去当前鼠标、IME/CJK 与快照资产；本次结构问题可以在 Textual 内以约一周的抽取解决。
- **引入 controller/presenter 分层**：更接近 Clean Architecture，但不符合 Textual 的 App/Screen/Widget 惯例，且会为单屏应用增加无收益的间接层。
- **一次性重写**：行为风险高，无法用现有快照逐阶段验证；采用行为保持的分阶段抽取。

## 验收标准

1. `app.py` ≤ 250 行，`screens/chat.py` ≤ 450 行，单个 service ≤ 400 行。
2. `services/**`、`gateway_client/**`、纯模型模块不 import Textual；`widgets/**` 不 import gateway/service/app；由 `test_layering.py` 强制。
3. `app.py` 不再直接调用 `RuntimeClient.submit/steer/cancel/get_session_status`，不再包含 steer/gateway 错误码分支。
4. 既有约 200 个测试与 13 个 SVG 快照行为不变；新增 service 单元测试覆盖队列、重连、早到事件、Session 切换。
5. `make test-tui`、`compileall`、`git diff --check` 通过；不存在与本次改动无关的基线失败。
6. README 与本文档反映最终目录和职责。

## 验证命令

```bash
apps/tui/.venv/bin/python -m pytest apps/tui/test/services -q
apps/tui/.venv/bin/python -m pytest apps/tui/test/test_app_snapshots.py -q
make test-tui
apps/tui/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test packages
git diff --check
```

确定性 replay 用于验证真实 Textual shell：

```bash
apps/tui/.venv/bin/python apps/tui/scripts/replay_events.py \
  --tui-real --speed 8 \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
```
