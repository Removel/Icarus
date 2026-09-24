# TUI Windowed Conversation and User-Turn Navigation Design｜TUI 对话窗口化与用户轮次导航设计

## 文档定位

设计日期：2026-09-23。本文只修改 `apps/tui` 的展示投影，不修改 Agent、Gateway、RuntimeUpdate、UiAction 协议或本地队列语义。实施步骤见 [plan.md](plan.md)。本文增量细化 [流式 Markdown 与滚动设计](../2026-09-02-tui-streaming-markdown-scroll/arch.md) 和 [Thinking 体验设计](../2026-09-21-agent-thinking-experience/arch.md)。

本次用户明确选择 **Thinking 默认折叠**；这更新了 2026-09-21 跨应用体验规格中“默认展开”的旧产品要求，但不改变 thinking 的传输、持久化、complete/delta 对账和用户手动展开能力。旧文档保留为当时设计记录，最终以本次经验证的代码和测试为准。

## 当前实现与问题

`ConversationView.apply_action()` 直接 mount 每个用户消息、Assistant 候选、RunCard、Tool、Thinking 和终态；历史恢复虽隐藏整个视图，仍逐条构造 Markdown Widget。完成的 Widget 和 `_thinking` / `_tools` 字典一直保留到 `reset()`。这令 Widget 数量、布局树规模随历史长度增长。当前没有 300/1000 轮的基准数据；不能把卡顿仅归因于文本总量，更不能承诺恒定总内存（原始会话文本仍需保留）。

`ThinkingBlock` 的 `display=False` 只能隐藏正文，`append_delta()` 仍写 MarkdownStream，`complete_text()` 仍全量 `update()`；单改 `expanded=False` 不足以消除折叠时的解析成本。当前 `RunCard` 可在同一 task 的完整 Assistant 消息后分段，未完成候选在 Tool/新 step/终态时降级；这些事件顺序不能由简单的“每 task 一个 Widget”还原。

## 目标与边界

1. 以每条顶层 `AppendUserMessage` 建立一个用户输入轮次；`AppendUserCorrection` 留在对应 RunCard，不产生导航点。欢迎内容和用户消息之前的异常输出不计轮次。
2. 保留完整、有序、可重建的 TUI 展示数据，限制已挂载的历史轮次及其 Markdown Widget；正在运行的尾部作为独立保留区，直到安全完成。长到极端的单轮不保证常量内存/布局成本，先测量再考虑分块。
3. 右侧是 **可滚动的用户输入轮次轨道**，不是全历史压缩刻度：常态只显示最多 10 个无编号小圆点，约 30px 中心间距（终端行高约 1.5 行）；普通圆点空心、当前轮实心，轨道宽度约 2 个终端 cell。鼠标进入轨道时在其左侧展开约 20 个终端 cell 宽的紧凑摘要列表；列表首项与最上方可见圆点对齐，每项以圆点开头，再显示按终端 cell 宽度截断的单行用户输入文本，不允许长中文挤到下一行形成孤立圆点；不显示轨道标题、轮次范围、数字或“更早/最新”文字。悬停摘要可看输入，单击圆点或摘要跳转；滚轮只移动轨道的 10 轮窗口，不移动正文；键盘聚焦轨道后可上下/翻页/Home/End/Enter 导航。无 hover 的终端仍可用键盘。导航只索引用户输入，不解析 Gateway 事件。
4. 跳转历史直接重建目标附近窗口而非从当前窗口逐轮遍历；浏览正文靠近上下边缘时按批次更换挂载窗口，并以可见消息标识及相对视口偏移恢复位置。保留 Textual 原生 anchor/release 作为**实时跟随**机制，不为底部跟随引入另一套竞争状态。
5. 浏览历史时新输出更新数据而不强制跳底；在对话区域右上角、紧挨右侧轨道显示英文 `↓ New messages` 入口，点击回到最新并恢复跟随。新消息数按新增用户轮次/可见更新的用户通知语义明确计数，不把每个 token 当一条；状态不能影响队列和运行行为。
6. Thinking 新建与历史恢复均默认折叠，折叠时仅保留摘要及原始文本，不对正文运行 Markdown parser；用户展开时再构造/刷新正文，保留本 Session 内的手动展开选择；流式完整记录替换 delta，迟到 delta 不重复。

## 展示数据与 Widget 生命周期

TUI 展示投影按接收顺序存储语义单元：用户消息；按 task_id + segment 顺序排列的 RunCard（thinking、tool、correction、progress）；完整 Assistant 消息；错误和终态。单元只存展示所需文本、参数、状态和展开选择；不存持久化模型副本，也不引用被卸载的 Widget。活动任务查找键沿用 `(task_id, step)` 和 `(task_id, call_id)`；历史上同一 task 的多个 RunCard 段仍按顺序保存。`append` / `complete` 改动对应语义单元；仅挂载范围内的单元同步到 Textual Widget。卸载前结束流式 handle、保存展开状态、清除 Widget 索引；重新挂载从完整文本创建 Widget，不重放 delta 或重新投影业务事件。

`_project_runtime_update` 的 sequence、reconnect、early-update 和 session 门禁保持在 App 层。历史恢复仅更新语义投影，结束后一次挂载末尾窗口。会话切换/清空先取消旧流与旧视图资源，再清除数据、轮次索引、滚动测量和导航位置；旧 task_id 不得串入新 Session。完整 Assistant 优先于后续 Tool，不得退化成 progress；未完成候选仍按现有逻辑降级。Tool completed 没有 started 时仍能恢复 Tool；两任务重复 call_id 不混淆。

正文窗口先以 16–24 个用户轮次作为测试基线，而非硬编码性能承诺；窗口含上下 overscan，批次装卸。不能用 `display=False` 来伪装卸载；不能同时持有全量历史 Widget 的强引用。被卸载历史的占位高度必须经过测量或提供显式跳转语义；终端宽度、Thinking 展开改变高度时重新测量，不能累计估高导致错误的滚动坐标。尚未挂载的目标在完成布局后定位并高亮；已挂载目标仅调用 `scroll_to_widget`。滚动时不得在一次 refresh 中往返装卸同一批次。

轨道数据从轮次索引读取原始 `UserMessage` 文本，摘要只保留单行、限制终端 cell 宽度，图片输入以原有标记表示；不得为摘要构造 Markdown Widget。常态最多 10 个圆点，终端高度不足时减少数量而不压缩到无法命中；轨道和摘要均不绘制历史总量、轮次编号或上下文字提示。小终端宽度不足时轨道隐藏而原有对话/Composer 仍可用；不占用既有 Conversation 原生滚动条或窃取 Composer 快捷键/焦点。鼠标只处理轨道区域的滚轮与点击；跳转才改变正文窗口。

## 验证契约

- 0、1、20、300、1000 用户轮次下，对话已挂载 Widget/轨道条目数量受窗口和活跃尾部上界约束；语义数据仍完整。大量历史恢复不逐轮 mount Markdown。
- 用户输入与 correction 区分；多段 RunCard、完整/未完成 Assistant、Tool 缺失 start、失败/中断、历史/重连、session reset 的文本与顺序不变。
- 历史阅读中持续 delta 不跳底，显式跳转/回到底部恢复正确锚点；导航滚轮不滚正文，点击大跨度轮次直接可见；Composer 继续可编辑。
- Thinking 默认折叠时无正文 Markdown 更新；展开/折叠、complete/late delta、卸载再挂载保留文字和选择。旧默认展开断言与快照按新设计调整。
- 以有界 Widget 数量、恢复与跳转耗时、连续输出输入响应及 300/1000 轮对比数据验证改善；没有基准数据前不写固定毫秒/内存目标。

测试放在 `apps/tui/test/widgets/` 和 `apps/tui/test/`，先聚焦测试，再 `make test-tui`、`compileall`、`git diff --check`。不修改 Agent/Gateway、系统提示、插件或事件总线；不因本功能修无关基线失败。
