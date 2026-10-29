# TUI Chat Workspace Architecture｜当前聊天工作台架构

## 状态与范围

2026-10-27，根据本轮源代码和相关测试记录。目标契约见 [design.md](design.md)，执行入口见 [plan.md](plan.md)，已认可浏览器原型见 [assets/demo.html](assets/demo.html)。本文描述实际代码，不将 HTML 原型的模拟数据冒充运行元信息。最终验证结果记录在 [validation.md](validation.md)。

## 运行链路

`main.py` 解析 CLI、构造 TuiConfig 与依赖。默认 client factory 延迟创建 `client/gateway/GatewayClient`，`--preview` 则注入离线 ReplayRuntimeService，无 Gateway、模型或业务 Session 调用。

`IcarusTextualApp` 拥有 ChatStore、ChatSession、保留的 ChatScreen、一个订阅 Worker 和具体 I/O Workers。用户提交/粘贴/中断与网络更新/completion 通过显式 Textual Message 投递 App。ChatSession 同步决定状态迁移与 Operation；execute 只执行 I/O 并返回 Completion。App 在同一消息处理路径校验并应用结果，再通知页面刷新，不等待 Markdown 绘制完成才处理下一条协议更新。

OperationScope 包含 generation、epoch、client identity、operation_id；派发保留原 reservation。Session 替换递增 generation，重连递增 epoch；订阅拥有独立 reader token。旧状态响应不能通过字符串 Session/task 相同而作用于新连接。未知派发结果在更新连接后以原 submission_id/目标重试。

## 当前模块

- `client/gateway/`：公共 JSON-RPC/WebSocket 适配、RuntimeClient/订阅 Protocol、结果值对象、async Queue 订阅实现。没有通用 SDK 或未来对象占位。
- `domain/`：ChatState 状态机、PendingMessage/DraftImage、命令定义与纯提交决策。不做文件删除或网络请求。
- `services/chat/`：Session 拥有权与采用、具体 IO Operation/Completion、纯派发结果分类、公共更新顺序/去重/投影、idle 状态规则。
- `services/attachments.py`：系统剪贴板读取、临时图片、ResourceRef 和释放；取消 to_thread await 后仍排空真实读取再清理。
- `stores/`：ChatStore 聚合现有 ChatState 与无 Widget 的 ConversationProjection。revision 标识整体变更，conversation_revision 只标识展示内容变更。额外保留最近 512 个 ChatChange（包含纯状态修订），供页面按顺序补齐积压动作；Session reset 清空旧变更，超过缓存范围则回退窗口快照。capture_window 复制有界窗口元数据与 DisplayUnit，供异步视图重建。
- `viewmodels/chat.py`：纯函数推导任务状态、可用性、队列数与真实可得 metadata；未知值保留未知。
- `screens/chat/`：页面拼接、焦点、rendered_revision/generation、activity_token、视图错误反馈；不拥有连接生命周期。没有旧 App `__getattr__/__setattr__`、query 代理或 `_tui_from_screen`。
- `widgets/chat/`：ConversationView 读取外部投影并管理挂载窗口/live tail/锚点；InputDock 组装任务状态、队列、Composer、CommandMenu、元信息。Markdown/CJK 私有 API 集中在 markdown.py，欢迎 Logo、消息卡片和执行区各自独立。
- `platform/clipboard.py`：Windows 文本/macOS 图片系统适配，其他能力仍保持明确限制。
- `event_pipeline/`：按公共 type 转换 UiAction。UiAction 是变化动作，不是 ViewModel 或可路由 Plugin Event。
- `tools/`：离线 replay、语义 transcript、真实 UI 预览数据；不是另一套 UI 实现。

## 页面与展示

欢迎、聊天、slash suggestions 是同一个 ChatScreen 的状态。Hero 为正文前导，首窗口可重建且不计导航 turn；对话容器不预留顶部空行，短会话恢复后 Hero 从终端首行开始，长会话仍按正文正常滚动。Hero 只展示 Logo（窄/矮窗口为紧凑标题）和与提示同色的灰色版本信息，不再重复 Workspace 或罗列快捷键。InputDock 是持久对象；Composer 上方一行左侧保留任务状态（最长占半行），右侧 ComposerTip 每 8 秒轮播一条单行操作提示，不改变输入焦点、光标或草稿。菜单在 Composer 下方展开，改变对话可用高度而不重建输入控件。底部元信息用 `model`、`context`、`session`、`uid`、`workspace` 英文键，键色更暗、值较亮并加粗，删除重复的思考等级；终端单元格字号仍由终端统一控制，不模拟局部缩放。消息角色通过标题行 `❯ You` / `✦ Icarus` 与正文缩进区分，不使用贯穿全文的纯色竖条；追加消息同样使用 `❯` 标识，用户/追加正文保留低对比底色，执行记录边框独立保留。

纠偏投影为同一 turn 的顶层 correction，保留 task/step，不生成新任务/轮次。执行记录按 Assistant 消息分组：CompleteAssistantMessage 封闭当前 segment，后续工具/Thinking 创建新 segment；纠偏本身不切段。快照中的 run_card.completed 表示该分组已封闭，重建时不将其注册为当前可追加分组。所有 Assistant 文本（包括中途输出、未完成文本）保留在外层，不降级为 progress 或设内层滚动。

工具条目默认折叠，首次工具条目到达才显示执行记录边框；无工具时 Thinking 独立显示，不预留空框。ToolRecords 随内容高度增长，最多 8 行（short 3 行，不含标题/边框），超过上限显示滚动条，默认跟随最新；用户上滚暂停局部跟随。每组最大 step 的 Thinking 放在记录边框下方；旧 Thinking 在新 step 到来时归档，有工具时位于该组记录区，否则保留在无边框的 Thinking 历史区；首个工具到达时迁入记录区。迟到完成仍按原 task/step 或 call_id 更新，不挪到新组。Thinking 折叠时不解析正文，运行动画由本地 Timer 管理，完成/隐藏/卸载/减少动效暂停。AI 正文没有 LocalScroll 或高度上限；仅用户超长文本保留 LocalScroll。QueuePanel 只有一个滚动拥有者，外层正文仍有历史窗口化和右侧轮次栏。

跨页保留同一个聊天页面/Composer；网络在 App 中继续处理，被遮挡时不持续挂载新的 Markdown。同 generation 且缓存完整时，返回页面或连续更新积压均按修订顺序补齐动作，不销毁现有消息、展开项和滚动容器。首次渲染、generation 替换、未完成渲染或缓存过期才重建窗口；重建使用 App.batch_update 抑制中间帧绘制，不将对话区设为 hidden，避免黑帧和中断后残留隐藏状态。多轮补齐用 task_id 定位各 turn，不把积压消息都绑定到投影的最后一轮。历史导航重建与 Screen 绘制共享 ConversationView 的 render_lock；导航替换递增 window_generation，页面发现窗口代次变化便使用快照校准，不将已被导航快照消费的 delta 再次追加。绘制开始即标 dirty，成功确认修订后才清除，协程取消后的下一次刷新因此重建而非重复应用部分动作。其他真实业务页面尚未新增，仅测试 SecondaryScreen 验证这种生命周期。

## 样式

App 明确加载 8 个文件：tokens、base、chat screen、session picker、welcome、conversation、input dock、command menu。Textual 的每份 stylesheet 变量解析不能仅靠前一个文件的定义，App.get_css_variables 从 tokens.tcss 提供共享变量；相对路径不依赖启动 cwd。聊天响应式类限制在 ChatScreen，避免污染未来页面。

## 当前限制与验收边界

- 元信息模型和当前上下文占用缺公共来源，显示未知/—；最新版本未检查。版本可由配置注入，不在线查询。
- psutil 未加入；它不处理剪贴板和终端协议。
- `icarus tui` 根启动器检查 Gateway 依赖就绪，未为 preview 修改仓库基础设施。离线真实界面运行 `./apps/tui/scripts/start.sh --preview`；正式会话先启动 Gateway 后 `icarus tui`。
- 真实预览通过 tmux 启动并键入输入/Enter，以及真实 App capture 脚本输出 SVG；SVG 是临时审查证据，不恢复删除的 13 张基线。字体转换差异和原生终端行为必须区别报告。
- Native Windows/macOS IME/剪贴板没有在 Linux/WSL 环境伪装成已实测；完整系统兼容仍需对应平台验收。

## 验证入口

```bash
make test-tui
apps/tui/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test packages
apps/tui/.venv/bin/python apps/tui/scripts/replay_events.py apps/tui/test/fixtures/synthetic_tui_events.jsonl
apps/tui/.venv/bin/python apps/tui/scripts/capture_preview.py --out /tmp/icarus-tui-preview --width 120 --height 40
./apps/tui/scripts/start.sh --preview
git diff --check
```
