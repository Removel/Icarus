# Agent Session Management Design｜Agent Session 管理设计

> 当前 Session 数据存储与软删除实现见
> `apps/agent/docs/arch/session-store-design.md`。本文继续定义 Session 列表和清理的产品语义。

## 文档定位

本文定义 `apps/agent` 为 TUI Session 列表、恢复和开始新对话提供的应用层能力，包括 Session 摘要、
空 Session 判定与安全清理。跨应用产品行为见：

`spec/session-management.md`。

本文不定义 TUI 选择器布局和 Gateway JSON-RPC 结构。

## 当前基础

`AgentRuntime` 已经具备：

- 以 `workspace_key + session_id` 标识 Session；
- 从 SessionStore 和活动 Registry 枚举 Session；
- 创建、查询、提交、取消、卸载和读取公共历史；
- 已卸载 Session 在第一次提交时 single-flight 恢复；
- SessionStore 中按 sequence 保存公共会话记录；
- 每个 SessionRuntime 独立恢复 Blackboard 和其他 Plugin State。

上述列表、摘要和清理能力已经实现：

- `list_session_statuses()` 继续面向运行状态；
- `list_session_summaries()` 从 SessionStore 返回用户可识别的对话摘要；
- 空 Session 通过 SessionStore Tombstone 软删除，不从文件目录推断业务存在性。

## 设计原则

- Session 内容和存在性由 Agent 应用层及持久层负责，TUI 不读取磁盘。
- 列表是轻量只读操作，不创建或恢复 SessionRuntime。
- 第一条已接受用户输入是 Session 的稳定识别内容，后续消息不改变它。
- 运行状态可用于安全校验，但不成为 TUI 列表展示要求。
- 删除只支持经权威检查确认的空 Session；非空 Session 不提供删除接口。
- 所有参数保持扁平、显式，不引入通用 Session Request 包装对象。

## Session 摘要模型

在 `application/runtime_status.py` 增加只读领域类型：

```python
@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    first_user_input: str
```

摘要不包含 Workspace、排序时间、Runtime lifecycle、错误详情、Plugin 状态或完整历史。调用已经由
`workspace_path` 限定范围，Gateway 和 TUI 不需要重复接收 Workspace 标识。

`first_user_input` 使用第一条 `user.message` 的展示文本：

1. 读取 `payload.text`；
2. 将换行和连续空白归一为空格；
3. 文本非空时保留最多 256 个 Unicode 字符，超出时追加省略号；
4. 纯图片输入使用固定文本 `[Image]`；
5. 历史兼容数据同时没有文本和资源时使用固定文本 `[Message]`。

服务端限制摘要长度，避免单条超大输入放大列表响应；TUI 仍需根据实际终端宽度二次裁剪。
任何合法解码出的 `user.message` 都表示 Session 已经接受过用户输入，摘要降级不能把它重新判为空。

## 摘要读取

SessionStore 在写入第一条 `user.message` 时，同事务保存 `first_user_input`；每条公共 Update 同事务
更新 `last_public_activity_at` 和 Session cursor。列表查询只读取 Session 行：

`AgentRuntime.list_session_summaries(workspace_path)`：

```text
解析 Workspace Identity
→ 从 SessionStore 查询未软删除 Session
→ 排除没有首条用户输入的空 Session
→ 使用内部 last_public_activity_at 降序、session_id 升序稳定排序
→ 丢弃内部排序字段并构造 SessionSummary
→ 返回不可变 SessionSummary 列表
```

列表查询不创建 `_SessionEntry`，不刷新 `last_task_activity_at`，也不改变 Session lifecycle。

## 空 Session 定义

Session 只有在下列条件全部满足时才可被丢弃：

- 公共 conversation journal 中没有 `user.message`；
- 没有活动 Task；
- UserInput 队列为空；
- EventBus 和 Plugin inbox 没有待处理工作；
- 没有 Plugin 后台工作；
- 不处于 loading 或 unloading；
- 不存在正在进行的其他 Session mutation。

以“没有 `user.message`”而不是“目录为空”作为内容判定。文件目录只承载 Plugin State、Asset、Trace
和日志，不表示业务 Session 存在。只要一条用户输入已被 Runtime 接受并写入 SessionStore，即使后续
Agent 失败或取消，该 Session 也不为空。

## 安全丢弃接口

新增结果类型：

```python
DiscardSessionStatus = Literal[
    "discarded",
    "not_empty",
    "busy",
    "not_found",
]

@dataclass(frozen=True)
class DiscardSessionResult:
    workspace_key: str
    session_id: str
    status: DiscardSessionStatus
```

新增应用方法：

```python
async def discard_empty_session(
    self,
    workspace_path: str | Path,
    session_id: str,
) -> DiscardSessionResult
```

执行顺序：

```text
解析并校验 SessionIdentity
→ 如果 SessionStore 和 Registry 都不存在，返回 not_found
→ 获取或创建对应 _SessionEntry，后续与 submit/create 复用同一 mutation_lock
→ 锁内遇到 loading / unloading 或 snapshot.has_work 时返回 busy
→ SessionStore 事务内复检公共历史；存在 user.message 时返回 not_empty
→ 如果 runtime 已加载，发起正常 unload，释放锁等待完成后重新从头检查
→ runtime 已卸载后，在同一锁内再次检查持久化历史仍为空
→ 设置仅应用层可见的 discarding 标记，阻止已取得 Entry 的 create/submit 继续使用
→ 在锁内写入 Session Tombstone，不删除 Conversation 和文件目录
→ 在 entries_lock 下移除同一个 Registry Entry
→ 返回 discarded
```

仅靠 `mutation_lock` 不足以保护删除，因为其他协程可能已经取得同一个 Entry 并正在等待锁。
`discarding` 标记必须由 create、submit 和其他 Session mutation 识别；删除失败时在锁内清除标记并保留
Entry，删除成功后等待中的操作统一收到 `SessionNotFoundError`，而不能复活已移除的孤立 Entry。

如果 SessionRuntime 已加载但空闲，先复用正常停止流程，确保 Plugin State、日志线程和文件句柄已关闭。
软删除失败不得伪造 `discarded`，应保留可诊断错误和 Registry Entry。

## 目标 Session Busy 判定

TUI 通过 `/resume` 恢复目标 Session 前会读取 `SessionStatus`。AgentRuntime 继续以现有 Session 快照
作为权威来源。首期认为以下任一项存在即为 Busy：

- `active_task_ids` 非空；
- `queued_task_count > 0`；
- `pending_event_count > 0`；
- `pending_plugin_event_count > 0`；
- `background_work_count > 0`；
- lifecycle 为 `loading` 或 `unloading`。

现有 `_status()` 只会在 `snapshot.has_work` 时返回 `running`，因此 `running` 已被上述工作计数覆盖。
`unloaded`、无工作的 `ready` 以及无工作的 `failed` 可以读取持久化历史；后续提交仍由现有惰性加载
路径处理。该状态只用于操作校验，不进入 Session Picker 展示。

目标状态可能在查询后变化。首期依赖订阅缓冲和激活前的二次 `session.get` 降低竞态，不增加 Session
租约或 UI 所有权抽象。若准备期间收到目标 Session 的新 Task Update，TUI 放弃切换。

## 空 Session 清理时机

本期由 TUI 在以下成功路径显式请求：

- 从空 Session 成功切换到其他 Session 后；
- 空 Session 中正常退出时。

`session.list` 只过滤空 Session，不执行删除副作用。TUI 异常退出留下的空 Session 不会展示；未来如
需要批量回收，应设计独立维护流程，而不是把清理隐式放进查询。

## 错误处理

- 单个 Conversation 记录损坏：保留 `ConversationHistoryCorruptError`，历史调用返回失败，不伪造摘要。
- Session 在枚举后软删除：后续查询按不存在处理；其他读取错误正常上抛。
- Busy：返回结构化 `busy`，不自动取消 Task 或后台 Job。
- 非空：返回 `not_empty`，不删除任何文件。
- 停止 Runtime 失败：保留 Session 数据并报告失败。
- Store 软删除失败：不移除 Registry Entry，允许后续重试。

## 测试范围

- 第一条文本用户输入被归一化并截断；
- 图片-only 输入得到稳定摘要；
- 后续用户消息不改变首条摘要；
- 摘要按最后公共活动时间倒序稳定排序；
- 空 Session 被列表排除；
- 列表不创建 SessionRuntime、不刷新空闲时间；
- 空且未加载、空且已加载的 Session 均可安全丢弃；
- 非空、活动 Task、排队、Plugin 后台工作、loading/unloading 状态拒绝丢弃；
- 软删除失败不留下错误的 Registry 状态；
- Session ID 路径逃逸被现有安全校验拒绝；
- 多 Session 并发摘要和丢弃不串 Workspace 或 Session。

## 完成标准

- AgentRuntime 可以返回当前 Workspace 的非空 Session 摘要；
- 摘要查询完全不加载执行 Runtime；
- 空 Session 只能通过受控接口在无工作时软删除；
- 原有创建、恢复、提交、卸载和历史读取行为不变；
- 不向 ReActAgent、Blackboard 或 Plugin Runtime 引入 Session UI 语义。
