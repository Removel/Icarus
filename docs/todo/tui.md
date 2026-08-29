# TUI TODO

## 待办

- 完善用于验证和使用 Agent Core 的终端交互能力。
- 完善已经接入的图片输入，并按真实需求扩展其他平台和多模态类型。
- 增加任务控制和会话操作能力。

## 近期优先级

- P0：启动首帧、布局正确性、稳定性和错误恢复，先处理会导致长时间空白、界面失效、
  状态无法恢复或缺少回归保护的问题。
- P1：滚动、长内容、输入、队列和诊断体验。
- P1：继续完善任务控制和异常恢复体验。
- P2：Windows/Linux 图片输入、Session 历史、恢复和切换。

## 基础交互能力

- [x] `TUI-01` 提供全局 `icarus` 启动命令；在任意 Workspace 目录执行时，
  以当前目录启动一次 TUI 对话 Session。
- [x] `TUI-02` 进入 TUI 后展示类似 Hermes、Claude Code 的简洁欢迎页面，
  包含产品标识、当前 Workspace 和基础操作提示。
- [x] `TUI-03` 将已提交的对话输出与当前输入编辑缓冲分开管理；第一阶段先完成
  串行生命周期分离，Agent 运行时输入与流式输出并存的能力归入 `TUI-05`。
- [x] `TUI-04` 输入区支持多行编辑，以及左右键、上下键移动光标；`Enter` 提交，
  `Shift+Enter` 插入换行，并为无法区分该组合键的传统终端提供 `Ctrl+J` 备用换行键。
- [x] `TUI-05` 迁移为 Textual 全屏应用，实现应用内对话滚动、持久底部输入框和 TUI
  本地双端队列：Agent 运行期间输入框保持可编辑；Enter 先 `append`，空闲时从队首
  FIFO 自动提交；待发送消息实时展示；`Ctrl+C` 可以从队尾 LIFO 撤回最新消息并恢复
  完整内容到输入框。
- [x] `TUI-06` 完成运行时任务取消：输入框和本地队列都为空且 Agent 正在运行时，
  `Ctrl+C` 通过 Gateway `session.cancel` 请求取消当前任务，显示
  `Cancelling`，保留终端中的部分输出，并在 `task.finished(status="cancelled")`
  到达后恢复调度下一条消息；取消轮次只提交最近的协议完整 Blackboard 历史。
- [x] `TUI-07` 支持流式 Agent Markdown 输出渲染。
- [x] `TUI-08` 为 Event Projector、UI Action 和 Widget 更新增加应用级错误边界；异常需要
  转换成可理解的界面状态，并补齐 Runtime 启动失败、订阅失败、投影失败、Widget 失败和
  清理失败测试。错误发生时不得静默丢失草稿或待发送队列；如果 Runtime 无法恢复，需要
  阻止继续调度并允许用户安全退出。
  当前实现使用幂等 fatal latch 阻止迟到 Event 恢复状态或触发后续调度；保留当前任务身份、
  草稿和待发送队列，并在退出时逐项清理 Subscription、Worker 和 Runtime，汇总清理错误后
  以非零状态退出。通知展示失败作为非关键错误降级记录，不中断正常任务终态处理。
- [x] `TUI-09` 完善消息提交失败后的恢复操作。保留未提交内容，并提供明确的重试或恢复
  路径，避免界面永久停留在 `FAILED` 且只能依赖用户猜测 `Ctrl+C` 撤回；设计时明确重试
  是否可能重复提交以及如何向用户展示结果。
  当前使用每条 PendingMessage 固定的 `submission_id`；连接或响应失败时保留队首与资源，重连后
  使用相同 ID 重试，AgentRuntime 在同一进程内返回原 task_id 或明确报告内容冲突。

## 待真实终端验证的体验项

以下内容来自当前实现与设计文档的差距分析，尚未全部确认为 Bug。每项先复现或建立性能
基线，再决定具体修改：

- [x] `TUI-10` 区分“自动跟随最新输出”和“用户正在阅读历史”。用户主动上滚后不应被每个
  流式 Event 强制拉回底部。对话区获得焦点时，上下键、`PageUp` / `PageDown` 和滚轮用于
  浏览对话；Composer 获得焦点时，上下键只移动编辑光标，滚轮不控制对话区；
  `PageUp` / `PageDown` 仍可跨焦点翻阅对话，`Ctrl+End` 回到底部并恢复自动跟随。
- [ ] `TUI-11` 为长 Markdown 流、长会话和高频增量建立性能基线，再优化当前全量重渲染
  等热点，避免在没有测量前进行框架级改写。
  当前暂缓：现有代码存在每个 Delta 更新完整 Markdown 的潜在热点，但尚无真实卡顿证据，
  不提前引入节流、缓冲和边界 flush 状态。出现长回答前快后慢、流式输出期间输入延迟、
  Event 结束后仍持续刷新，或长会话滚动与 resize 明显变慢等可复现场景后再建立基线并优化。
- [ ] `TUI-12` 在真实终端中验证软换行、超长单行、多行粘贴、窄窗口和 resize 下的
  Composer 高度与光标行为；确认问题后修复并增加对应交互和快照测试。
- [ ] `TUI-13` 限制 Tool 参数和错误摘要的默认展示长度，保留查看完整诊断信息的明确入口，
  并避免敏感信息或超长内容淹没对话区域；实施前明确上游数据脱敏与 TUI 展示裁剪各自的
  责任。
- [ ] `TUI-14` 增加可选诊断视图，展示未知 Event、被忽略的 task_id、Runtime 状态和
  必要的调试计数，同时保持普通对话界面简洁；设计时决定诊断信息只属于当前进程还是需要
  持久化。
- [ ] `TUI-19` 在真实 macOS 终端中完成剪贴板图片验收：分别复制系统截图和浏览器图片，
  验证 `Ctrl+V` Marker、连续图片顺序、提交、撤回恢复和退出清理。当前固定系统脚本、
  无图片回退、后台线程、临时文件与提交参数已有自动化测试；此项只保留真实桌面环境验收。

## 后续产品能力

- [x] `TUI-15` 支持多模态输入，并与 Agent Core 的统一输入类型和能力边界保持一致。第一阶段在
  macOS 实现剪贴板图片 `Ctrl+V`，Composer 使用 `[#imageN]` 占位，文字与图片共同排队、撤回并通过
  Gateway ResourceRef 提交；Windows/Linux 后续只扩展统一平台函数，不修改 TUI 主流程。图片读取
  失败作为非致命 TUI 通知，任务确认后清理暂存文件，失败和断线时保留以便幂等重试；
  已覆盖 TUI 功能测试和视觉快照。详细设计见
  `apps/tui/docs/arch/tui-clipboard-image-paste-design.md`，实施步骤见
  `apps/tui/docs/plan/tui-clipboard-image-paste-development-plan.md`。
- [ ] `TUI-16` 支持 Session 历史浏览、恢复和切换；TUI 只展示并发起应用服务操作，不自行
  重建 Blackboard 业务历史。
- [x] `TUI-17` 缩短命令到首帧可见的时间。打开页面时只加载轻量 Textual 壳层，不创建
  Runtime，也不展示虚假的初始化或 Ready 状态；页面挂载后立即在后台连接 Agent Gateway。首次
  有效提交时，若连接与 Session 已 Ready 则正常发送；若尚在初始化则显示
  `Initializing` 并在订阅成功后自动发送本地队首。分别记录首帧和后台 Runtime Ready 的时间。
- [x] `TUI-18` 修复顶部标题区以及底部 Composer / StatusBar 的布局重叠和边框裁切。在宽屏、
  窄屏、多行 Composer 和连续 resize 下使用布局结构保证区域不相交，不依赖魔法间距。

以上编号只用于需求追踪，不代表开发优先级。第一阶段终端框架的状态、交互和验收
设计见 `apps/tui/docs/arch/tui-terminal-framework-design.md`；开发步骤见
`apps/tui/docs/plan/tui-terminal-framework-development-plan.md`。持久输入、队列与过渡期
`Ctrl+C` 设计见 `apps/tui/docs/arch/tui-persistent-input-queue-design.md`，当前 Textual 实施
步骤见 `apps/tui/docs/plan/textual-tui-development-plan.md`。

## 推进方式

- 不依赖 Agent Core 的 P0/P1 项可以持续并行推进。
- `TUI-06` 必须使用 Gateway 的正式任务控制接口，不能直接访问 AgentPlugin、
  UserInputPlugin 或 EventBus，也不能通过重建整个 Runtime 伪装成任务取消。
- 每个确认的 Bug 先补最小复现，再修复并增加对应单元、Pilot、Replay 或 Snapshot 回归。
- 推断出的体验问题先在真实终端复现和测量；未确认前不标记为已知 Bug。
