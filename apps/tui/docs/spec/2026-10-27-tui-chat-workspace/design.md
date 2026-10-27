# TUI Chat Workspace Design｜终端聊天工作台重构

## 1. 状态与依据

设计日期：2026-10-27。状态：用户已认可三张目标图和 HTML 交互原型；本文将其落实为实施契约，尚未实现。

- 已认可的可运行原型：[assets/demo.html](assets/demo.html)。它是 HTML 布局/交互参考，不是 Textual 成品，不连接 Gateway。原型中的模型、用量、执行记录均为示例。用户原始三张图仍保留在会话中，未复制进仓库；本文逐条记录图中目标，避免依赖临时附件路径。
- 当前代码基线：`94c7d22`，分支 `feat/tui`。上一轮抽取记录：[2026-10-26-tui-structure](../2026-10-26-tui-structure/arch.md)。
- 本文描述未来设计，不把目标结构写成当前架构。实施完成后再根据代码与测试更新架构文档。
- 实施入口：[plan.md](plan.md)，基础重构：[plan-foundation.md](plan-foundation.md)，目标界面：[plan-chat-ui.md](plan-chat-ui.md)。

### 已确认的产品要求

1. 三张图是目标图，不是松散参考；欢迎、对话、命令建议是同一个聊天页面的不同状态。
2. 欢迎 Hero 位于对话开头，随对话自然上移；底部输入区持续存在。
3. Assistant 文本独立；工具记录折叠、可局部滚动；Thinking 支持运行反馈和展开。
4. 追加/纠偏作为用户消息呈现，不塞在 Agent 执行详情里。
5. `/` 命令候选在 Composer 下方展开，缩小上方对话可用高度，不切换 Screen。
6. 右侧轮次定位保留，New messages 提示轻量、不抢主要视觉。
7. 后续增加其他独立页面；切换页面时当前对话继续后台运行，返回保留草稿和阅读位置。
8. 高内聚、低耦合；客户端按对象分类，例如 `client/gateway/`，不把未来客户端堆入同一文件。
9. TCSS 拆分；保留 `configs/`、`platform/`、`domain/`，不为尚无调用方的功能创建空目录。

### 本设计的工程取舍

最新用户校正优先：AI 消息全文在外层对话展开，不使用消息内部滚动或降级为 progress；每轮 Run 一个固定高度执行记录区（普通 8 行、矮屏 3 行），默认跟随最新且有滑块。当前最新 Thinking 位于执行记录边框下方，旧 Thinking 随新 step 出现归档到该 Run 的记录区，折叠选择与完整文本仍保留。保持现有主题、键位和任务主输入导航轮次；未知元信息明确显示未知。

## 2. 当前实现事实与待解决问题

已检查源代码和相关测试；本文编写阶段未重新运行测试。此前最后一次完整验证记录为 332 passed，不代表未来实现已验证。

| 当前文件 | 实际职责与问题 |
| --- | --- |
| `src/app.py` | 163 行，但有属性/query 代理、业务消息转发与 `_tui_from_screen` 兼容协议；App 的表面接口与真实状态拥有者不一致。 |
| `src/screens/chat.py` | 1,320 行，仍同时持有连接、Worker、队列入口、历史恢复、展示和退出；卸载会清理资源，不满足未来跨页后台运行。 |
| `src/services/update_stream.py` | sequence/scope/完成记录、早到缓冲、投影和 async UI 回调混合；`reconcile` 可从 Worker 直接触发 UI，需统一串行应用。`on_fatal` 参数保存后未使用。 |
| `src/services/dispatch_service.py` | 生产使用 send/handle 两阶段，单测大量使用另一条 dispatch_next；不能为了合并入口而破坏已有时序保护。 |
| `src/services/session_service.py` | 准备连接、重连、Session 操作已抽出，但选择/激活回调和状态变更分布在 Screen；`_config` 未使用。 |
| `src/domain/submission.py`、`widgets/composer.py` | 都存在临时文件删除；资源映射又由 Dispatch/Clipboard 两条路径调用，资源所有权不统一。 |
| `src/widgets/conversation_projection.py` | 有完整无 Widget 展示数据，是可复用核心；目前由 Widget 持有且 live/rebuild 各有语义分支。 |
| `src/widgets/messages.py` | Logo、CJK/Markdown 私有 API 适配、流式生命周期、普通消息和执行卡片混放。 |
| `src/styles.tcss` | 455 行，token、页面、组件和弹窗规则集中；聊天专属响应式规则使用通用 Screen 选择器。 |
| `src/replay.py`、`transcript.py` | 有实际脚本和 golden 测试调用；应归工具目录，不应删除。 |

现有导入测试能防部分上层依赖，不能证明状态单写、没有磁盘 IO、运行时循环或消息顺序正确。测试通过不等于结构已完成。

## 3. 范围与非目标

本期修改 `apps/tui`：运行所有权、状态、UI、客户端目录、样式和测试。继续只消费 Gateway 公共协议，不直接 import Agent Runtime/Plugin/Blackboard/provider。

不增加真实新业务页面，只用测试 Screen 验证导航；不实现通用 DI、Redux、中间件总线、动态客户端注册表、通用导航框架；不加入 psutil；不改协议、不实现在线版本查询、不伪造模型与上下文值；不恢复已按用户要求删除的 13 个 SVG 快照。

未来若要求真实模型/思考等级/当前上下文占用，另做公共元信息契约。TUI 不直接读取 Agent settings.json 或把累计 tokens 冒充上下文占用。

## 4. 目标目录与职责

```text
apps/tui/src/
├── main.py                         # 参数、配置、具体依赖组装、App.run
├── app.py                          # App、页面导航、应用级 Worker、串行结果应用、最终退出
├── configs/config.py               # TuiConfig，新增可选 app_version
├── client/
│   └── gateway/
│       ├── __init__.py              # 该对象的显式公开接口
│       ├── client.py                # 原 GatewayClient，JSON-RPC/WebSocket
│       ├── ports.py                 # RuntimeClient/ClientFactory/订阅接口
│       ├── models.py                # SubmitAccepted/TaskOperationResult
│       └── subscription.py          # UpdateSubscription 实现
├── domain/
│   ├── chat_state.py                # 复用队列、阶段、reservation 状态机
│   ├── submission.py                # DraftImage/PendingMessage、marker/模型输入规则，无磁盘 IO
│   └── commands.py                  # 命令定义、匹配、校验与提交决策
├── services/
│   ├── chat/
│   │   ├── session.py               # ChatSession：运行入口和资源所有权；含 PreparedSession
│   │   ├── operations.py            # 具体 I/O 操作/结果值与 execute，不改变 Store
│   │   ├── dispatch.py              # 发送/取消的准备及结果规则
│   │   └── updates.py               # scope、sequence、早到和状态对账，无 UI 回调
│   └── attachments.py               # 原 ClipboardService：读取、暂存、资源引用、释放
├── stores/
│   ├── chat.py                      # ChatStore 聚合 ChatState、ConversationProjection、元信息
│   └── conversation.py              # 原 ConversationProjection/DisplayUnit/Turn，纯展示数据
├── viewmodels/chat.py               # 只读 ChatViewModel 和 build_chat_viewmodel
├── screens/
│   ├── chat/
│   │   ├── screen.py                # ChatScreen：布局、焦点、页面交互、可见窗口刷新
│   │   └── messages.py              # 用户意图/状态变更/异步结果的 Textual Message
│   └── session_picker.py            # 既有 ModalScreen；纯选择，不调用网络
├── widgets/chat/
│   ├── welcome.py                   # ASCII Logo 与欢迎信息
│   ├── markdown.py                  # StreamingMarkdown、CJK/私有 API 适配
│   ├── message_cards.py             # 用户、Assistant、纠偏、错误和终态
│   ├── execution_panel.py           # 工具列表、Thinking、disclosure 与局部滚动
│   ├── conversation.py              # 挂载窗口/锚点/live tail，读取唯一投影
│   ├── input_dock.py                # 状态、队列、输入、命令菜单、metadata 的组合
│   ├── composer.py                  # 原编辑内核；无文件删除或网络
│   ├── command_menu.py              # 候选显示和选择
│   ├── queue_panel.py               # FIFO 预览，仅一个滚动容器
│   ├── status.py                    # TaskStatusLine 与 SessionMetadataBar
│   ├── turn_rail.py                 # 轮次目录，不是跨页导航
│   └── text.py                      # Queue/Rail 共有的纯文本截断
├── event_pipeline/                  # 保留动作、注册投影器；不承担 UI 布局
├── platform/clipboard.py            # 操作系统剪贴板读取
├── styles/
│   ├── tokens.tcss
│   ├── base.tcss
│   ├── screens/{chat,session_picker}.tcss
│   └── widgets/{welcome,conversation,input_dock,command_menu}.tcss
└── tools/{replay,transcript}.py
```

不新增 `client/ports.py` 或通用 sdk 包；当前唯一对象的接口也放 `client/gateway/`，其他实际客户端以后与 gateway 并列。不增加未实现的目录或空占位文档。`__init__.py` 只导出实际需要的公开类型，避免导入一个工具函数就加载整个 UI。

## 5. 状态所有权与依赖

| 内容 | 唯一拥有者 | 写入方式 |
| --- | --- | --- |
| 队列、phase、active task、reservation | ChatStore.interaction（现 ChatState） | ChatSession 的串行处理调用状态机 |
| 完整对话文本、卡片次序、turn/task/step/call_id 索引 | ChatStore.conversation | UiAction 经 Store 应用一次 |
| 卡片展开选择 | 展示投影 DisplayUnit | Widget 发 DisclosureChanged，由串行处理更新 |
| 连接、订阅、epoch、操作 in-flight 标记 | ChatSession | 应用级串行处理；I/O 只返回结果 |
| sequence、早到缓存、completed steps | ChatSession 内 UpdateProcessor | 仅串行处理；按 Session generation 重置 |
| 持续状态/错误、session 是否有输入、真实可用元信息 | ChatStore | ChatSession 写入 |
| 草稿、光标、选区、撤销、图片 marker | 保留的 Composer Widget | 编辑本地修改；提交/释放发意图 |
| 外层和局部滚动、布局高度、已挂载窗口 | ConversationView/ExecutionPanel | Widget；Screen 被遮挡时保存、不强制跟随 |
| 命令候选选择、菜单 Escape 抑制状态 | InputDock/CommandMenu | Widget；不放全局 Store |
| Spinner 帧、hover、focus | Widget/Textual | 本地 Timer；无事件总线 tick |
| Worker 句柄、最终退出任务 | App | 应用关闭统一 cancel/wait/close |

ViewModel 只从 Store 计算状态文案、按钮可用性和 metadata；不保存队列/全文副本，不操作 Widget、不发请求。UiAction 是变更动作，不等同 ViewModel。不会再加承担同一职责的 Presenter。

Screen/Widget 可读 Store 的只读公开面，不能直接改队列/业务 phase；仅 ChatSession 调用 Store 和状态机写接口。Domain 不 import client/services/Textual，也不 unlink；Platform 不 import UI/services；Services 不 import Textual；ViewModel 不 import UI/client/platform。布局和业务之间通过显式 Message/结果相连，不通过 App 魔法属性代理。

## 6. 唯一串行应用路径

### 6.1 接口约定

以下为目标接口；Operation/Completion 是服务内部有限联合，不是可插件化命令总线。

```python
class ChatSession:
    def __init__(self, store, client_factory, attachments, projectors, commands): ...
    def start(self) -> tuple[Operation, ...]: ...
    def submit(self, submission: PendingMessage) -> tuple[Operation, ...]: ...
    def interrupt(self, has_draft: bool) -> InterruptAction: ...
    def cancel_active(self) -> tuple[Operation, ...]: ...
    def paste(self, *, request_id: str, draft_token: int) -> tuple[Operation, ...]: ...
    def request_resume(self) -> tuple[Operation, ...]: ...  # list operation
    def resume(self, session_id: str | None, *, request_id: str) -> tuple[Operation, ...]: ...
    def clear(self) -> tuple[Operation, ...]: ...
    def receive(self, update: RuntimeUpdateModel, *, scope: OperationScope) -> tuple[Operation, ...]: ...
    def complete(self, result: Completion) -> tuple[Operation, ...]: ...
    def begin_shutdown(self) -> None: ...
    async def close(self) -> tuple[str, ...]: ...

async def execute(operation: Operation) -> Completion: ...
```

`Operation`/`Completion` 放 `services/chat/operations.py`：启动/准备、订阅、派发、取消、重连、状态查询、Session 列表、附件读取、关闭旧客户端/候选、丢弃空 Session。每个异步结果含提交时捕获的 Session generation、连接 epoch、client identity、operation id；派发结果还含原 reservation。每次采用新准备的 Session（即使字符串 ID 相同）都增加 generation；每次开始替换连接前增加 epoch，使旧 reader/状态结果立即失效，重连操作自身使用新 epoch。只接受当前 pending operation id，reader 使用独立且稳定的 subscription token。失败返回 error 字段，`CancelledError` 继续传播。

`ChatStore` 持有 `interaction: ChatState`、`conversation: ConversationProjection`、`revision: int`、`last_change: ChatChange | None`。`apply_actions(actions: tuple[UiAction, ...], *, historical: bool = False) -> ChatChange` 是全文投影的唯一入口；`ChatChange` 是冻结值，含 revision、generation、actions、history_reset。状态-only 更新也产生递增 revision，不重新复制全文。

`build_chat_viewmodel(store: ChatStore) -> ChatViewModel` 返回冻结的 `status_text`、`status_kind`、`pending_count`、`can_run_session_command`、`show_phase`、`session_title`、`session_id`、`workspace_path`、`model_label`、`reasoning_label`、`context_label`、`version_label`、`latest_version_label`。

### 6.2 执行顺序

1. App 收用户意图或网络更新，调用 ChatSession 同步决定/推进状态，得到具体 I/O operations。
2. App Worker 仅 `execute(operation)`，不改 Store，不调用 Widget。
3. Worker 向 App 投递 `OperationCompleted(result)`；App 在自己的消息队列内 `complete(result)`。
4. App 先验证 generation/client/reservation，再应用状态、历史和早到缓冲；向可见 ChatScreen 投递 `ChatChanged(revision)`。
5. Screen 读取 Store 时只使用通知作为唤醒信号，不把通知 revision 当已读状态。页面只有一条渲染串行路径和一个 `rendered_revision`；连续增量按捕获的 ChatChange 应用，缺口/返回则同步抓取稳定的有界 `ConversationWindowSnapshot(generation, revision, start, end, copied_units)` 再异步渲染。snapshot 不含 Widget、不是第二份长期事实源，渲染后释放；仅确认实际完成的捕获 revision。Store 在 await 期间前进时再调度一轮；不得把已从快照画出的 delta 再 append 一次。Widget 不再次调用 projection.apply。
6. 订阅 Worker 逐条产生 `RuntimeOutputReceived(update, scope)`；scope 含 generation/epoch/client/subscription token。原始事件、派发结果、重连历史、任务状态结果都进同一个 App 消息队列，不从 Worker 直接投影。

临时进度状态与通知由 Store 的状态字段/ChatChange 显式表达，不向 Service 注入 `query_one` 或任意 UI 回调。错误不能把 cursor 推进到尚未成功应用的记录；迟到 full/delta 对账和现有真实 gap 检测保留。历史允许原有聚合跳号，live sequence 仍连续。所有 completion 必须在等待前捕获 generation，不在返回后补取当前值。

网络关闭可能慢：切换/重连采用先校验并交换拥有权、再异步清理旧资源的操作；不能在 App 串行 handler 等待旧连接关闭。退出则停止接收意图、cancel/wait 所有应用 Worker、await ChatSession.close，最后 App.exit。重复退出只执行一份清理任务；非空 Session 不能被本地推测为空后删除，仍由公共 discard_empty_session 判定。

## 7. 多页面与对话生命周期

App 在运行期间只创建一个 ChatSession 和 ChatStore。ChatScreen 是保留实例；暂时 push 其他 Screen/ModalScreen 时不卸载它、不关闭订阅、不清草稿。ScreenSuspend 不执行会话退出逻辑。

后台更新继续进入 Store；被遮挡页面只标记 dirty，不持续构造新的隐藏 Markdown Widget。ScreenResume 对比 generation/revision，恢复当前窗口与锚点，不播放历史 Spinner。未读输出用轻量标记表达，不自动跳底。Session 切换会重置该 Session 的展示状态，但跨页不是 Session 切换。

本期用测试专用 SecondaryScreen 验证，无生产占位页。页内/临时弹窗使用局部键位；其他页不继承聊天页的 Ctrl+C 任务取消策略。最终退出能力属于 App，聊天页按原有优先级清草稿/撤回队尾/取消/退出。

页面另有 UI-only `activity_token`，每次 suspend/reset/替换渲染时失效。在渲染入口、每个异步挂载前后和延迟 anchor/focus callback 检查 active、token、generation；放弃的渲染只标 dirty，不确认 revision。快速两次切页不能让旧 callback 抢焦点。

`/resume` 的列表请求、弹窗和选择/取消共用 `request_id`，捕获源 client/generation/epoch。`resume(None, request_id=...)` 明确取消并恢复适用的 idle 状态；选择前后身份不符则忽略并关闭候选。采用目标前仍向捕获的源 Session 查询 idle，不能省去现有二次检查。列表在别页活跃时返回只暂存候选，不在别页上弹出选择器；回到聊天页且 token 仍有效才展示。

附件读取通过具体 ReadClipboard/ClipboardReadCompleted 结果进入 App，同样携带 request_id、Session generation 和 Composer `draft_token`。clear/discard/提交转移/Session 替换使 draft_token 失效，普通文本编辑不无故失效；未交付/过期图片由 AttachmentsService 释放。`ClipboardReady` 仅交付原 retained Composer，页面隐藏时保存待交付结果但不抢焦点，恢复时再次验证 token；替换 Session 或退出时回收。

## 8. 目标界面与尺寸

### 8.1 页面结构

`ChatScreen` = `ConversationArea(ConversationView + TurnRail + new-output)` + `InputDock(TaskStatusLine + QueuePanel + Composer + CommandMenu + SessionMetadataBar)`。Hero 是 ConversationView 的前导内容，不是 dock，也不是单独 Screen。

- 原有暖黑/粉色 token 延续；原型外层浏览器说明、状态切换按钮、模拟执行按钮不进入产品。
- Hero 在最早窗口可重建，其他历史窗口不挂载；不产生 turn 或 task。
- Conversation 主区 `1fr`，InputDock `auto`；展开菜单只挤占可用高度，不 destroy/recompose Composer。
- 宽屏 120×40、普通 100×32、窄屏 70×24 与 58×24、短屏 100×12 为验收尺寸。
- 保留现有阈值：width<=70 为 narrow，width<=88 为 compact-logo，height<=12 为 short。类只加在 ChatScreen，不污染后续页面。
- Composer 普通最多 8 行，short 最多 4；命令普通最多 5 个可见候选、short 最多 2；执行记录列表固定 8 行，short 固定 3 行；AI 消息全文显示，不设正文最大高度或内层滚动。用户超长文本仍可独立浏览。均为逻辑行，不使用原型像素数。
- 保证上述验收尺寸下外层对话至少 3 行、Composer 至少 1 行、任务状态和 metadata 各 1 行。高度不足时先压缩队列摘要/菜单/Composer 上限，不挤掉输入；小于验收尺寸不承诺完整信息，但不能出现未捕获异常。
- narrow 隐藏次级 workspace/思考说明，metadata 按优先级截断（模型、会话、context、workspace）；short 使用紧凑 Hero。完整值可通过可聚焦内容/后续详情页获取，不横向撑破布局。

### 8.2 消息、执行区域与纠偏

每个 task/Run 只保留一个执行记录区，持续收集该 Run 的工具与旧 Thinking；不再因 Assistant 完成或用户纠偏新建记录区。Assistant 的各 step 文本按外层消息顺序完整呈现，未完成文本不降级塞进工具区。

工具详情默认折叠，展示工具名、参数摘要、运行/完成/失败标签，展开只显示已有安全 preview。`full_result_available` 不是自动下载权限，不新增完整结果端点。列表追随最新，用户上滚后暂停局部跟随。

纠偏为顶层 UserCorrection 消息样式，标注追加已应用，仍保留原 task/step，不创建新导航 turn。纠偏不关闭或分割该 Run 的执行记录区；live 与窗口重建一致。

Thinking 摘要默认折叠、按需解析正文；每 step 保留文本。该 Run 最大 step 的 Thinking 单独显示于记录框下方，出现新 step 时把旧 Thinking 移入固定记录区，不丢弃正文或改变 task/step；旧 step 的迟到 complete 只更新归档内容，不替换最新 Thinking。Spinner 用 Textual LoadingIndicator 或等宽字符帧；只在实际活跃 thinking 时运行，complete/task finished/隐藏/卸载后停止 Timer。历史重建按投影中的真实 completed/status 渲染，不因为 historical=True 就把活跃任务假装完成。减少动效配置关闭旋转但保留运行文案。

外层和各局部区有各自滚动 owner，事件只由最内层可消费者处理；到达边缘后向外层传递。外层脱离跟随时 token 更新不跳底；工具局部脱离不隐式改变外层锚点；PageUp/PageDown 保持现有外层浏览语义，Ctrl+End 回到底部。

### 8.3 命令建议

仍只有 `/resume`、`/clear`、`/exit` 三个已实现命令。CommandDefinition 新增 `usage`（默认由 name 派生），匹配为命令名忽略大小写的子序列匹配，按注册顺序稳定返回；原型没有的命令不凭空创建。

- 仅草稿为单行、去前导空白后以 `/` 开头、光标位于该行末尾且还在命令名 token 中时自动展开。以已有命令后接参数时不对 Enter 做候选劫持。
- 上下键选择；Tab 补全选中名称；Enter 执行当前候选（只要仍在名称补全模式）；Shift+Enter/Ctrl+J 换行后关闭菜单并保留原编辑行为。
- Escape 隐藏直到草稿再次编辑；关闭菜单时方向键恢复 Composer 光标导航。无候选时 Enter 走现有未知命令校验，原草稿不丢。
- 真实执行前仍校验 idle/附件/参数；忙时拒绝 resume/clear、恢复完整草稿，/exit 按原清理路径。鼠标选择不造成双提交。
- 命令列表、菜单提示、metadata 位于 Composer 下方，元信息始终最底部。

## 9. 样式、平台与数据真实性

App.CSS_PATH 显式按 tokens、base、screen、widgets 顺序加载本期所有 TCSS。相对路径相对 App 定义文件，不相对 cwd；测试子类使用同一已解析样式清单。TCSS 文件不自动局部隔离，聊天规则使用 ChatScreen/组件类型/class 限定。布局由父控件样式控制，内部外观由组件样式控制；不做全局深层覆盖，不用 @import 模仿浏览器 CSS。

Platform 继续提供真实系统剪贴板；AttachmentsService 负责 IO 与 cleanup，Composer 只保存 marker/草稿。async to_thread 取消不保证底层线程停止：关闭要等待已启动的附件写入结束/回收未交付文件，再清理目录，避免退出后重新创建临时目录。Windows/Linux 图片仍显示未支持或回退文本，不伪装已实现。

psutil 只适用于进程/系统监控，不负责剪贴板、终端、模型状态；本期没有监控调用方，不增加依赖。

模型、reasoning、context 目前缺少完整公开来源：ViewModel 显示 `未知`/`—`；不使用 demo-model/24k。当前版本通过 `TuiConfig.app_version: str | None = None` 注入，未提供显示 `未知`；最新版本固定 `未检查`，不联网。Workspace/session 来自现有输入/公共客户端。后续真实 metadata/更新检查另立协议设计，不跨层读取 Agent 配置。

## 10. 文件精简清单

- 保留：状态机、client 实现、公共投影器、流式/CJK 适配、窗口化、rail、编辑内核和 replay/golden。
- 合并：SubmissionRouter 的纯提交决策归 domain/commands.py；PreparedSession/SessionBusyError 归 session.py；附件资源操作归 attachments.py；文本 preview 归 widgets/chat/text.py。
- 拆分：客户端对象目录/订阅实现、messages Widget 大文件、状态与 metadata、TCSS。
- 删除：App 兼容代理和旧 constructor kwargs、转发标记、旧根 messages.py、重复默认 client factory、无效 config/on_fatal 参数、只在单测使用的完整 dispatch_next、旧迁移路径和空导出。删除前移植测试；不删除行为测试来让重构变绿。
- 重定位：ConversationProjection→stores/conversation.py；replay/transcript→tools/；页面 Message→screens/chat/messages.py。
- skills 文档同步本期路径；MassGen skill 只借用离线/视觉验证方法，不执行它的项目命令；项目 skill 中已过时的快照数、单文件 TCSS 和“App 不得创建 Worker”规则在收尾时更新。

## 11. 验收

基础重构阶段先不改视觉和纠偏顺序；目标 UI 阶段有意更新相关语义断言和 transcript golden，不盲目更新输出。既有 13 张 SVG 已删除，不作为门禁；不声称 Pilot 等于视觉审查。

门禁：最小测试→相关目录→`make test-tui`→compile/diff；覆盖 sequence gap/重复、early/live 顺序、相同 submission_id 重试、旧结果隔离、关闭与临时文件、后台页面续跑/返回锚点、默认折叠、CJK 和菜单 Enter 优先级。视觉以离线 replay + 实际截图人工检查上述五档尺寸为准；HTML 原型仅作为参考。没有凭测量前承诺固定耗时或跨平台全覆盖。

完成标准不是行数阈值：Screen 不理解协议错误码和 retry，Service 不碰 Textual，Store 无 Widget，ViewModel 无副作用，客户端对象独立；改业务不需要改布局、换页面不销毁会话；每个异步结果经过同一校验入口。
