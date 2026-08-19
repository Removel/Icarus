# TUI TODO

## 待办

- 完善用于验证和使用 Agent Core 的终端交互能力。
- 增加多模态输入支持。
- 增加任务控制和会话操作能力。

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
- [ ] `TUI-06` 完成运行时任务取消：输入框和本地队列都为空且 Agent 正在运行时，
  `Ctrl+C` 通过 `AgentRuntimeService.cancel(task_id)` 停止当前任务，保留终端中的部分输出
  但不提交不完整 Blackboard 历史。该项依赖 Agent Core 的任务级取消契约；契约完成前
  TUI 只明确提示暂不支持取消，不伪造成功。
- [x] `TUI-07` 支持流式 Agent Markdown 输出渲染。

以上编号只用于需求追踪，不代表开发优先级。第一阶段终端框架的状态、交互和验收
设计见 `apps/tui/docs/arch/tui-terminal-framework-design.md`；开发步骤见
`apps/tui/docs/plan/tui-terminal-framework-development-plan.md`。持久输入、队列与过渡期
`Ctrl+C` 设计见 `apps/tui/docs/arch/tui-persistent-input-queue-design.md`，当前 Textual 实施
步骤见 `apps/tui/docs/plan/textual-tui-development-plan.md`。
