# Session Management｜Session 管理功能规格

## 文档定位

本文定义 Icarus 在当前 Workspace 内创建、列出、选择、恢复和开始新 Session 的统一产品行为。
该能力同时需要 Agent、Gateway 和 TUI 配合，无法拆成任一单独应用内部的完整需求，因此放在仓库
根目录 `spec/`。各应用的内部结构和实施步骤由对应 `apps/<app>/docs/` 文档承接。

相关设计：

- `apps/agent/docs/arch/session-management-design.md`；
- `apps/gateway/docs/arch/session-management-rpc-design.md`；
- `apps/tui/docs/arch/tui-session-management-design.md`。

实施计划：

- `apps/agent/docs/plan/session-management-development-plan.md`；
- `apps/gateway/docs/plan/session-management-rpc-development-plan.md`；
- `apps/tui/docs/plan/tui-session-management-development-plan.md`。

## 背景

Icarus 已经支持多 Session 隔离、指定 Session ID 启动、公共会话历史恢复、SessionRuntime 按需恢复
和空闲卸载。但用户目前只能自行记住 Session ID，并通过命令行参数恢复，TUI 内没有历史 Session
列表、选择和开始新对话的入口。

本功能把现有基础能力收敛成可发现的 TUI 交互，同时保持以下架构边界：

- TUI 负责用户命令、选择和界面投影；
- Gateway 负责协议校验、路由和更新订阅；
- AgentRuntime 负责 Session 事实、持久化、运行状态和加载；
- SessionRuntime 负责单个 Session 的 Plugin、Blackboard 和任务执行；
- ReActAgent 继续无状态，不参与 Session 管理。

## 目标

- 用户直接运行 `icarus` 时，继续在当前 Workspace 自动创建并进入一个新 Session。
- 用户通过 `icarus --session-id <id>` 时，直接打开指定 Session；不存在时保持现有兼容行为并创建。
- 用户在空闲态输入 `/resume` 时，可以查看当前 Workspace 的非空 Session 并选择恢复。
- 用户在空闲态输入 `/clear` 时，可以保留当前有效对话并开始一个新 Session。
- Session 选择与命令行指定 Session 使用同一套历史恢复和实时订阅语义。
- 空 Session 不作为长期对话保留，也不出现在 `/resume` 列表。

## 非目标

- 本期不实现 `/compact` 或主动上下文压缩。
- 不实现 Session 搜索、筛选、重命名、收藏和删除非空 Session。
- 不展示 SessionRuntime 的 `ready`、`running`、`unloaded` 等内部状态。
- 不允许在当前 Session 忙碌时切换，也不支持切换后在后台观察原 Session 的任务。
- 不实现长历史分页、跨 Workspace Session 列表或跨设备同步。
- 不改变自动 Compact、Blackboard 上下文和 ReActAgent 生命周期。

## 用户交互

### 默认启动

```text
icarus
→ 以当前目录作为 Workspace
→ 生成新 Session ID
→ 创建并进入新 Session
→ 显示空白对话界面
```

这个行为不因 `/resume` 的增加而改变。用户不需要先经过 Session 选择页。

### 指定 Session 启动

```text
icarus --session-id <id>
→ 查询指定 Session
→ 已存在则恢复历史并进入
→ 不存在则按当前兼容行为创建同名 Session
```

### 本地命令

TUI 在普通消息进入 Pending Queue 前识别去除首尾空白并转为 ASCII 小写后的精确命令：

- `/resume`；
- `/clear`。

命令不发送给 Agent，不写入公共会话历史，也不进入 TUI Pending Queue。带图片附件的本地命令拒绝
执行并保留草稿，避免把附件静默丢弃。除上述精确命令外，其他输入继续沿用普通消息行为。

### 空闲态门禁

`/resume` 和 `/clear` 只能在下列条件同时满足时执行：

- 当前 TUI 已完成 Session 激活并处于 Ready；
- 当前没有活动 Task；
- 没有进行中的提交握手；
- TUI Pending Queue 为空；
- 没有正在进行的历史恢复或 Session 切换；
- Gateway 连接可用。

不满足时，TUI 显示命令当前不可用，不排队、不延迟执行，也不发送给 Agent。AgentRuntime 仍需在
`session.get` 和空 Session 清理入口提供权威状态；TUI 在命令执行前查询当前 Session，并在目标激活前
查询目标 Session，不能把本地检查视为并发安全保证。首期不支持多个客户端对同一 Session 并发提交，
也不引入跨客户端 Lease。

### `/resume`

`/resume` 打开一个模态 Session 选择器，只展示当前 Workspace 下至少有一条已接受用户输入的
Session。列表按最近公共会话活动时间倒序排列。

每项只展示：

- 第一条用户输入的单行摘要；
- 缩写后的 Session ID；
- 当前 Session 的简单标记。

不展示 Runtime 状态和时间。文本根据终端宽度截断，完整 Session ID 只作为内部选择值使用。第一期
不提供搜索和筛选。

选择操作：

- `Up` / `Down` 移动；
- `Enter` 加载所选 Session；
- `Escape` 关闭并保留当前 Session。

选择任一 Session，包括当前 Session，都复用与命令行指定 Session 相同的激活路径，重新读取并显示
该 Session 的持久化内容。目标 Session 在激活检查时存在活动 Task、队列或后台工作时，切换失败，
当前 Session 和界面保持不变。

### `/clear`

`/clear` 表示开始新对话，不表示删除当前历史：

```text
当前 Session 非空
→ 创建一个新 Session
→ 激活并显示空白 Conversation
→ 原 Session 保留，可通过 /resume 恢复
```

如果当前 Session 本身为空，`/clear` 不重复创建，保持当前 Session 并提示已经处于新对话。

## Session 摘要

Agent 应用层提供面向产品的轻量 Session 摘要，只向客户端暴露：

```text
session_id
first_user_input
```

`first_user_input` 来源于公共会话 journal 中第一条 `user.message`，优先使用用户当时看到并提交的
`payload.text`，而不是内部 Prompt、System Prompt 或 Blackboard Message。纯图片输入使用固定摘要。
Agent 层在内部使用最后一条公共记录时间完成排序，该时间不进入 Session 摘要协议，也不进入 TUI。

没有 `user.message` 的 Session 为空，不进入摘要列表。枚举和摘要读取不加载 SessionRuntime，也不刷新
Session 空闲时间。

## Session 激活

TUI 对启动、`/resume` 和 `/clear` 使用同一套 Session 激活语义：

```text
准备绑定目标 Session 的 Gateway Client
→ 查询或按入口规则创建 Session
→ 订阅目标 Session 的实时 RuntimeUpdate
→ 读取目标完整公共历史与 history_cursor
→ 按入口策略校验目标可激活（`/resume` 要求目标空闲）
→ 重建 TUI Conversation 和 Session 级状态
→ 启动实时事件消费
```

恢复历史只读取公共 conversation journal，不从 Trace 推断产品历史。浏览或恢复已卸载 Session 不主动
加载 SessionRuntime；用户提交下一条消息时，由 AgentRuntime 按现有机制自动恢复 Plugin、Blackboard
和执行环境。

切换使用“先准备目标，后替换当前”的提交方式。目标连接、订阅或历史读取失败时关闭候选资源，当前
Session、Conversation、订阅和游标保持不变。

## 空 Session 生命周期

Session 是否为空由 AgentRuntime 根据公共会话历史判断，TUI 不读取或删除本地目录。

空 Session 在下列时机请求清理：

- 从空 Session 成功切换到另一个 Session 后；
- TUI 在空 Session 中正常退出时。

AgentRuntime 只在确认 Session 没有用户输入、没有活动 Task、没有排队工作且没有 Plugin 后台工作时
执行清理。非空、忙碌或状态无法确认时拒绝清理。异常退出遗留的空目录不作为用户会话展示；后续可由
独立维护机制回收，本期不把扫描删除副作用放进 `session.list`。

## 跨应用职责

| 应用 | 本期职责 | 明确不负责 |
|---|---|---|
| Agent | Session 摘要、非空判定、排序、权威 Busy 检查、安全丢弃空 Session | TUI 布局、命令解析、网络协议 |
| Gateway | 暴露摘要列表和空 Session 清理 RPC，转换稳定错误 | 读取持久化目录、解释 Blackboard、实现切换状态机 |
| TUI | `/resume`、`/clear`、空闲门禁、选择器、候选 Client、界面重建 | 判断磁盘 Session 是否为空、直接管理 SessionRuntime |

Gateway 不新增 `session.switch`。切换是客户端从一个 Session 投影切换到另一个 Session 投影，不是
服务端持久化业务状态。

## 错误处理

- Session 列表失败：关闭选择器并提示错误，当前 Session 不变。
- 目标 Session 不存在：`/resume` 不自动创建，提示 Session 已不存在。
- 目标 Session 忙：拒绝切换，当前 Session 不变。
- 目标历史损坏或恢复失败：关闭候选资源，当前 Session 和界面不变。
- 目标准备成功后的 TUI Widget 提交失败：进入现有 Fatal Error 边界，不能继续派发消息。
- 空 Session 清理失败：已经成功的切换或退出不回滚；记录诊断并在仍可展示时提示。
- Gateway 断线：沿用现有重连和 `history_cursor` 对账逻辑。

## 兼容性

- 保留 `icarus` 默认新建 Session 的行为。
- 保留 `icarus --session-id <id>` 的入口和当前“查询，不存在则创建”兼容语义。
- 保留现有 `session.get_history`、`session.subscribe`、`session.submit` 和 RuntimeUpdate 类型。
- `session.list` 从尚未被 TUI 使用的运行状态列表收敛为产品 Session 摘要列表；单 Session 运行状态
  继续由 `session.get` 提供。
- 已有 Session 不迁移，不修改其 conversation journal 和 Blackboard State 格式。
- TUI 仍然只依赖共享 Gateway 协议，不导入 Agent 实现。

## 验收标准

- 直接运行 `icarus` 进入新的空 Session。
- 指定 `--session-id` 可以恢复已有对话并继续执行。
- `/resume` 只在空闲态可用，列表只包含当前 Workspace 的非空 Session。
- 列表以第一条用户输入识别 Session，Session ID 只缩写显示，不显示 Runtime 状态。
- 选择 Session 后恢复用户消息、助手文本、Tool、错误和终态，且历史与实时流无遗漏、无重复。
- 选择当前 Session 也通过统一激活路径重新恢复内容。
- 目标忙、历史损坏或网络失败时当前界面保持不变。
- `/clear` 从非空 Session 开始新 Session，原 Session 可通过 `/resume` 找回。
- `/clear` 在空 Session 中不重复创建。
- 空 Session 不出现在 `/resume` 列表，并在正常切走或退出后由 AgentRuntime 安全清理。
- `/resume`、`/clear` 不出现在 Agent 输入、Blackboard 上下文或公共会话历史中。
- Agent、Gateway、TUI 相关测试和 `git diff --check` 通过。
