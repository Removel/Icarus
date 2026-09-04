# Session Conversation History Development Plan｜Session 会话历史实施计划

> 历史计划：本文记录旧 `conversation.jsonl` 实现。当前实现已由
> `apps/agent/docs/arch/session-store-design.md` 和
> `apps/agent/docs/plan/session-store-development-plan.md` 替代。

## 目标

基于设备级 Runtime 设计，为新产生的 Session 记录稳定的公共会话 journal，使客户端能够恢复用户
消息、助手文本、Tool、错误和终态；同时在每个 Task 终态执行 Plugin State checkpoint，避免下一次
unload 前异常退出导致 Blackboard 丢失最后一轮。

旧 Session 不读取或迁移 `trace.jsonl`。

## 阶段一：公共记录与 Persistence

- 为 RuntimeUpdate 增加可选 Session 内 `sequence` 和 `user.message` 类型；
- 在 Persistence 路径中增加 `conversation.jsonl`；
- 新增 Session 级 append/read store，逐行 flush，校验 schema 和连续 sequence；
- 只允许忽略异常退出留下的最后一条截断 JSON，中间损坏明确失败；
- 增加有序读取和旧 Session 空历史测试。

## 阶段二：AgentRuntime 记录与查询

- AgentRuntime 为新 Task 生成 task_id，并让 SessionRuntime/UserInputPlugin 复用该身份；
- 在 Runtime 接受时记录 `user.message`，随后记录 `task.accepted`；
- 所有会话类型 RuntimeUpdate 先持久化、分配 sequence，再广播；
- 提供 `get_session_history(workspace_path, session_id, after_sequence)`；
- 查询时把已无活动 Runtime 的未终结 Task 持久化为 interrupted；
- 不加载 SessionRuntime、不刷新 last_activity。

## 阶段三：终态 Checkpoint

- 为 Host 增加复用 StateCoordinator 的 checkpoint 操作；
- SessionRuntime 在 EventBus 与 Plugin inbox drain 后执行 checkpoint；
- AgentRuntime 收到 `task.finished` 后先 checkpoint，再持久化和广播终态；
- checkpoint 失败时以安全错误将 Task 收束为 failed。

## 验证

- 正常任务记录完整顺序并可读回；
- 用户原文与 Asset 引用不泄漏暂存绝对路径；
- 进程异常后部分文本和 Tool 保留，未完成 Task 变为 interrupted；
- 每个完成 Task 的 Blackboard State 在不 unload 时已可恢复；
- 旧 Session 返回空展示历史，不读取 Trace；
- 多 Session journal 和 sequence 隔离。

## 实施结果

- 已实现 `conversation.jsonl`、Session 内 sequence、截断尾行恢复和历史完整性校验；
- 已实现 `user.message` 与全部可展示 RuntimeUpdate 的先落盘后广播；
- 已实现异常 Task 的 interrupted 收口和只读历史查询；
- 已实现 Blackboard 终态 checkpoint，完整 Runtime stop 仍保存全部 Plugin State；
- Agent、Gateway、TUI 合并全量测试 488 项通过，包含 9 个 TUI 视觉快照。
