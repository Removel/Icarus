# TUI Session History Restoration Design｜TUI Session 历史恢复设计

## 文档定位

本文定义 TUI 打开已有 Session 时如何恢复并展示持久化会话内容。它只负责客户端读取、顺序交接和
界面投影，不改变 Blackboard 的模型上下文职责，也不从内部 Trace 重建产品历史。

Agent 应用层的持久化记录见：

`apps/agent/docs/arch/device-agent-runtime-session-design.md`。

Gateway 查询与订阅契约见：

`apps/gateway/docs/arch/agent-gateway-positioning-design.md`。

## 目标

使用 `icarus --session-id <id>` 打开已有 Session 时，在允许发送新消息前恢复并展示：

- 用户消息和稳定图片引用；
- 助手已生成的文本，包括异常退出前的部分文本；
- Tool 开始、成功、失败和未完成状态；
- Task 错误；
- Task 的 completed、failed、cancelled 或 interrupted 终态。

历史展示与实时输出必须连续、有序且不重复。

这里的“恢复到 Session 退出时的样子”指恢复 Conversation 中的可见语义内容和组件状态：消息顺序、
助手文本分段、Tool 卡片及其最终状态、错误和 Task 终态。Composer 草稿、尚未被 Runtime 接受的本地
Pending Queue、滚动位置、焦点、窗口尺寸和临时通知属于单个 TUI 进程，不进入 Session 历史。

## 非目标

- 不迁移当前已经存在的旧 Session Trace；
- 不从 `trace.jsonl`、日志或内部 Plugin Event 猜测产品历史；
- 不把 TUI Conversation 变成持久化事实源；
- 不持久化 Composer 草稿、本地 Pending Queue、滚动位置、焦点或窗口布局；
- 第一阶段不增加历史分页、搜索、编辑或删除；
- 不因为查看历史而强制创建或常驻 SessionRuntime。

## 数据来源

TUI 只消费 Gateway 返回的公共会话记录。记录与实时 RuntimeUpdate 使用相同的公共类型和 payload，
并带有 Session 内单调递增的 `sequence`。

```text
Agent Session conversation.jsonl
→ AgentRuntime.get_session_history
→ Gateway session.get_history
→ GatewayClient
→ TUI 历史投影
→ ConversationView
```

没有 `conversation.jsonl` 的旧 Session 返回空历史。TUI 不回退到读取本地文件或 Trace；该 Session
之后产生的新任务从新 journal 的第一条记录开始展示。

## 启动与无缝交接

TUI 启动已有 Session 时按以下顺序执行：

```text
连接 Gateway
→ session.get；不存在时 session.create
→ session.subscribe，开始缓冲实时 runtime.update
→ session.get_history(after_sequence=0)，取得 records 与 history_cursor
→ 隐藏 Conversation，在 Textual batch_update 中按 sequence 构建全部历史 Widget
→ 完成后一次显示 Conversation 并定位到底部
→ 丢弃实时缓冲中 sequence <= history_cursor 的重复记录
→ 按 sequence 回放其余实时记录
→ 标记 TUI Ready 并调度本地待发送消息
```

历史加载期间 Composer 可以继续编辑，本地提交可以进入 TUI Pending Queue，但不能向 Runtime 自动
派发。只有历史渲染和实时交接完成后才进入 Ready，避免新消息插到旧历史中间。
历史 `assistant.text_delta` 虽然仍复用公共 Projector 合并内容，但批处理期间不触发可见重绘，因此用户
不会看到旧回复逐字重新流式输出。

断线重连使用同一交接方式，但以客户端最后已应用的 sequence 作为 `after_sequence`，只补齐缺失
记录，再接续实时流。Session 生命周期 Update 不参与 sequence 去重。

## 展示规则

历史渲染复用现有 Widget 和 RuntimeUpdate Projector 的语义，不复制一套内部 Event 转换：

| 公共记录 | TUI 展示 |
|---|---|
| `user.message` | `UserMessage`，显示用户原文和附件 marker |
| `assistant.text_delta` | 按原顺序合并到当前 `AssistantMessage` |
| `tool.started` | `ToolMessage` running |
| `tool.completed` | 更新对应 Tool 为 completed 或 failed |
| `task.error` | `ErrorMessage` |
| `task.usage` / `context.compacted` | 复用现有轻量状态或通知投影 |
| `task.finished` | completed 关闭当前轮次；failed、cancelled 或 interrupted 显示终态 |

历史投影不使用当前 `active_task_id` 过滤，因为它需要还原多个已结束 Task；实时投影继续只接受当前
活动 Task。两条路径复用同一组公共记录模型和 UiAction，但由调用模式明确是否执行 active-task
过滤。

提交被 Runtime 接受后，发起提交的 TUI 不再直接调用 `append_user_message()`；它与其他客户端一样，
等待持久化后广播的 `user.message` 再渲染。提交响应到达前收到的 `user.message` 和
`task.accepted` 继续进入现有 early-update buffer，响应确认 `task_id` 后按 sequence 回放，从而避免
本地乐观显示与公共记录重复或内容不一致。Runtime 尚未接受时，消息只显示在现有 Pending Queue。

如果一个 Task 以 `interrupted` 结束：

- 已有用户消息、助手部分文本、已完成 Tool 和错误照常保留；
- 已开始但没有 `tool.completed` 的 Tool 显示为 interrupted；
- 关闭未完成的 Assistant 流式段；
- 追加明确的 `Task interrupted` 终态，不伪造 completed、failed 或 cancelled。

## 图片与敏感信息

历史只包含 Session Asset 的稳定引用和媒体类型。第一阶段 TUI 可以继续显示原消息中的附件 marker，
不要求立即增加历史图片预览。历史响应不包含 Base64、原暂存路径、任意绝对路径、System Prompt、
完整模型请求或 Hook Trace。

## 错误处理

- history RPC 失败：TUI 不进入 Ready，保留草稿和 Pending Queue，并显示可重试错误；
- journal 最后一行因异常退出而截断：Agent 只允许忽略最后一条不完整记录，并记录诊断；中间记录
  损坏则返回明确的历史损坏错误；
- sequence 缺口或重复：停止历史交接并重试查询，不猜测顺序；
- Session 没有新格式历史：按空历史正常启动，不读取旧 Trace；
- 历史渲染单条失败：终止恢复并显示错误，不能静默跳过导致用户误解会话。

## 测试范围

- 已完成多轮对话按 sequence 恢复用户、助手、Tool、错误和终态；
- 部分助手文本和未完成 Tool 在异常退出后显示 interrupted；
- subscribe 与 history 查询之间产生实时 Update 时不遗漏、不重复、不乱序；
- 历史恢复完成前不派发本地 Pending Message；
- 无 journal 的旧 Session 显示空历史且不读取 Trace；
- 图片历史不泄漏 Base64 或绝对路径；
- 重连只补齐最后 sequence 之后的记录；
- 大量文本 delta 可以恢复为正确的 Assistant 分段。

## 完成标准

- 指定新格式 Session 启动 TUI 后，可以看到此前完整的用户、助手、Tool、错误和终态；
- 历史内容与 Agent 应用层 journal 一致，TUI 不维护第二份持久化事实；
- 异常退出的最后一轮保留已发生内容并显示 interrupted；
- 历史与实时流交接无遗漏、无重复；
- 查看历史不加载 SessionRuntime，也不刷新 6 小时空闲时间；
- 旧 Session 不迁移、不报错，并从升级后的新任务开始记录。
