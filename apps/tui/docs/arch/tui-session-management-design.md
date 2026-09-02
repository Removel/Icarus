# TUI Session Management Design｜TUI Session 管理设计

## 文档定位

本文定义 `apps/tui` 如何通过本地命令列出、选择、恢复和开始新 Session。跨应用产品行为见：

`spec/session-management.md`。

Agent 的摘要与空 Session 生命周期见：

`apps/agent/docs/arch/session-management-design.md`。

Gateway 的 RPC 契约见：

`apps/gateway/docs/arch/session-management-rpc-design.md`。

## 当前实现

当前 TUI 是单 Session 控制器：

- `GatewayClient` 在构造时绑定一个固定 `session_id`；
- `IcarusTextualApp` 持有一个 Client、一个实时 Subscription 和一个 Event Worker；
- `ChatState`、`_last_sequence`、`_early_updates` 和 `ConversationView` 都对应当前 Session；
- 启动时执行 `session.get/create → subscribe → get_history → restore → consume updates`；
- 重连时按 `_last_sequence` 补历史并对账活动 Task。

本期继续保持“一次只激活一个 Session”，不把 TUI 改造成多 Session 后台管理器。

## 方案选择

### 采用：候选 GatewayClient 准备后原子替换

切换 Session 时新建绑定目标 Session 的候选 Client：

```text
当前 Client 和 Conversation 保持可用
→ Candidate Client 连接目标 Session
→ 订阅并读取历史
→ 校验目标可激活
→ 一次性替换当前 TUI 投影
→ 关闭旧 Client
```

该方案复用命令行指定 Session 的加载路径，且目标准备失败时不需要恢复旧连接、旧订阅和旧界面。
准备阶段会短暂存在两个本机 WebSocket，持续时间仅覆盖查询和历史读取。

### 不采用：现有 Client 原地改 session_id

原地取消旧订阅、修改身份再订阅目标，虽然少一个短连接，但目标历史损坏或网络失败时需要重建旧
订阅、游标和事件 Worker，回滚复杂且容易出现半切换状态。

### 不采用：单连接管理多个活动 Session

该方案适合后台观察多个 Session，但与本期“只在空闲态切换、一次只展示一个 Session”的需求不符，
会提前引入多 Session UI 投影和任务通知管理。

## 本地命令路由

新增独立、纯函数式命令解析模块，例如 `src/commands.py`：

```python
LocalCommand = Literal["resume", "clear"]

def parse_local_command(text: str) -> LocalCommand | None:
    ...
```

解析规则：

- 对输入执行 `strip()` 后精确匹配 `/resume` 或 `/clear`；
- 本地命令不区分大小写；
- `/resume anything`、`please /resume` 等不匹配，仍是普通 Agent 输入；
- 命令附带图片时拒绝执行，并通过 `restore_draft()` 恢复完整草稿和附件；
- 命令在 `ChatState.enqueue()` 之前拦截，因此不进入队列或历史。

本期不注册 `/compact`，也不为未知斜杠命令增加统一错误；未知命令保持普通文本语义。

## 空闲态门禁

在 `ChatState` 增加纯状态属性：

```python
@property
def can_run_session_command(self) -> bool:
    return (
        self.phase == RuntimePhase.READY
        and self.active_task_id is None
        and not self.dispatch_in_progress
        and not self.pending
    )
```

App 再检查：

- `service`、`subscription` 和 Gateway 连接可用；
- `_session_operation_worker` 不存在或已结束；
- 当前不在历史恢复和 Fatal 状态。

本地门禁通过后，App 调用当前 Client 的 `session.get`，用 Agent 返回的 lifecycle 和全部工作计数确认
当前 Session 也处于空闲。`/resume` 的 Picker 可能停留任意时长，因此用户确认选择后、准备 Candidate
前再检查一次当前 Session。`/clear` 在创建新 Session 前执行同一检查。

命令不可执行时，通过现有 Notification 显示 `Session commands are only available while idle.`，然后把
焦点交回 Composer。命令不进入等待队列。

Session 操作期间增加明确的 `SWITCHING` phase 或等价的独立操作标志，使普通提交和其他 Session 命令
都暂时不可用。该状态仅属于 TUI，不映射为 Agent Task。

## GatewayClient 调整

当前 Client 在 `start()` 中隐式执行“查询，不存在则创建”。增加扁平构造参数：

```python
GatewayClient(
    url=...,
    workspace_path=...,
    session_id=...,
    create_if_missing=True,
)
```

`start()` 继续执行：

```text
connect
→ session.get
→ 如果 session_not_found 且 create_if_missing，则 session.create
→ 否则上抛
→ session.subscribe
```

新增客户端方法：

```python
async def list_sessions(self) -> tuple[SessionSummaryModel, ...]
async def get_session_status(self) -> dict[str, object]
async def discard_empty_session(
    self, session_id: str
) -> DiscardEmptySessionResultModel
```

`discard_empty_session` 接受显式目标 ID，使新 Client 可以在成功切换后请求清理旧空 Session。

## Client Factory

将当前无参数 `RuntimeFactory` 调整为显式 Client Factory：

```python
ClientFactory = Callable[
    [str | None, bool],
    Awaitable[RuntimeClient],
]
```

参数含义：

- `session_id`：目标 Session；`None` 表示生成新 ID；
- `create_if_missing`：是否允许不存在时创建。

调用关系：

| 场景 | session_id | create_if_missing |
|---|---|---:|
| `icarus` | `None` | `True` |
| `icarus --session-id xxx` | `xxx` | `True` |
| `/resume` | 选择的完整 ID | `False` |
| `/clear` | `None` | `True` |

不增加宽泛 Client Options 对象。

## Session Picker

增加一个独立 `ModalScreen`，只负责展示和返回选择结果：

```text
Resume session

> 修复登录页面的错误                    a83f…91c2
  分析 Agent Runtime 架构               dev-…n-01
  为 TUI 增加 Session 切换              7c12…ed40

Up/Down Select   Enter Resume   Esc Cancel
```

属性与行为：

- 输入为不可变 `SessionSummaryModel` 列表和当前完整 Session ID；
- 主内容是第一条用户输入；
- Session ID 由 TUI 做前后缩写，不改变真实值；
- 当前 Session 使用简洁标记，不显示 Runtime 状态；
- 根据组件宽度裁剪预览；
- `Up` / `Down` 循环或边界停止应使用 Textual ListView 的原生行为；
- `Enter` dismiss 完整 Session ID；
- `Escape` dismiss `None`；
- 空列表显示“没有可恢复的对话”，只允许关闭；
- 搜索、筛选和删除不进入本期。

Picker 不访问 Gateway，不拥有 Client，也不修改 ChatState。

## 统一 Session 准备

提取 App 内部准备方法，保持参数扁平：

```python
async def _prepare_session(
    self,
    session_id: str | None,
    create_if_missing: bool,
) -> tuple[RuntimeClient, UpdateSubscription, SessionHistoryModel]:
    ...
```

执行顺序：

```text
Client Factory
→ client.start()
→ 获得已经开始缓冲的 subscription
→ client.get_session_history(after_sequence=0)
→ client.get_session_status() 二次确认目标无工作
→ 返回候选 Client、Subscription 和 History
```

启动路径、`/resume` 和 `/clear` 共用该方法。区别仅由传入参数决定。

对于 `/resume`（即 `create_if_missing=False`），最终状态检查出现活动 Task、排队、待处理 Event、
待处理 Plugin Event、后台工作或 loading/unloading 时，关闭候选 Client 并报告 Busy。现有 `running`
状态由工作计数推导，因此也会被拒绝。当前和目标检查都是时点检查；首期不增加 Session Lease，也
不支持多个客户端对同一 Session 并发提交。命令行 `--session-id` 使用同一准备和历史恢复代码，但保持
现有启动兼容行为，不额外增加 Busy 拒绝。

## Session 激活提交

准备成功后执行 `_activate_session(...)`：

1. 将 TUI 置为 Switching，阻止输入和 Session 命令。
2. 保存旧 Client、Subscription、Event Worker、Session ID 和是否为空。
3. 停止旧 Event Worker，但暂不关闭旧 Client。
4. 将候选 Client、Subscription 和目标 SessionIdentity 设置为当前投影。
5. 重置当前 Session 级 TUI 状态：
   - 新建空 `ChatState`；
   - `_last_sequence = 0`；
   - 清空 `_early_updates`；
   - 清空 Session 级错误和状态文案；
   - 根据目标历史重新计算 `_session_has_user_input`。
6. 调用 `ConversationView.reset()` 清除旧消息、Assistant 段和 Tool 索引，并恢复 Welcome。
7. 使用现有 history restore 投影目标记录，更新 `history_cursor`。
8. 启动候选实时 Event Worker，进入 Ready。
9. 关闭旧 Client。
10. 如果旧 Session 为空，通过新 Client 请求 `session.discard_empty`。

选择当前 Session 也执行完整准备与激活，不走特殊 no-op，从而与 `--session-id` 启动保持同一恢复
语义。

候选历史与实时缓冲仍沿用现有 sequence 交接规则：历史投影至 `history_cursor` 后，实时 Event Worker
忽略或对账不大于该 cursor 的记录，再继续处理新记录。

## ConversationView 重置

增加明确方法：

```python
async def reset(self) -> None:
    ...
```

它负责：

- 结束并丢弃当前 Assistant 引用；
- 清空 Tool 索引；
- 移除现有子 Widget；
- 重置 history restore 和自动跟随状态；
- 重新挂载当前 Workspace 的 WelcomeMessage。

App 不直接操作 ConversationView 私有字段。

## `/resume` 流程

```text
Composer 提交 /resume
→ 本地命令解析
→ 空闲门禁
→ session.get 确认当前 Session 空闲
→ 当前 Client.list_sessions()
→ 打开 Session Picker
→ 用户取消：返回当前会话
→ 用户选择：再次确认当前 Session 空闲
→ _prepare_session(id, False)
→ 准备成功：_activate_session(...)
→ 准备失败：关闭 Candidate，恢复 Ready，当前界面不变
```

列表本身不展示空 Session，因此新启动后尚未输入的当前 Session 不会出现在列表中。

## `/clear` 流程

App 使用 `_session_has_user_input` 表示当前公共历史中是否已经出现 `user.message`。它是独立于
“用户是否操作过 UI”的 Session 级事实，只能由历史恢复或实时 `user.message` 更新，不能在消息仍处于
本地 Pending Queue 或提交握手时提前设为真。

```text
Composer 提交 /clear
→ 本地命令解析
→ 空闲门禁
→ session.get 确认当前 Session 空闲
→ 当前 Session 为空：保持当前并提示
→ 当前 Session 非空：_prepare_session(None, True)
→ _activate_session(...)
→ 原 Session 保留
```

如果新 Session 已创建但在激活提交前的候选准备阶段失败，先请求丢弃该空 Session，再关闭候选，避免
失败路径遗留空目录。激活提交阶段失败按下文 Fatal Error 边界处理，不再假装可以安全回滚。

## 正常退出与空 Session

退出清理顺序增加：

```text
停止 Event Worker 并关闭本地 Subscription，避免再投影退出期 Update
→ 当前 Client 仍连接时请求 discard_empty(current_session_id)
→ 关闭 Client
→ 清理临时图片
```

`not_empty` 是正常结果。`busy` 或清理请求失败不阻止用户退出，也不直接访问磁盘；记录日志并继续
现有清理流程。

## 失败与回滚

### 候选准备失败

如果 Candidate 由 `/clear` 新建，先通过仍可用的 Candidate 或当前 Client 请求清理该空 Session，再
关闭 Candidate。随后恢复 Ready，并通过 Notification 显示错误。旧 Client、Conversation、ChatState、
cursor 和 Event Worker 从未改变。

### 目标忙

视为可恢复操作失败，不进入 Fatal 状态。关闭 Picker/Candidate，提示目标当前不可恢复。

### 激活提交阶段失败

一旦开始移除旧 Widget，简单回滚已不可靠。使用现有 App Fatal Error 边界停止后续提交，保留错误
诊断，不尝试拼接两份 Conversation。实现应先在内存中校验完整历史模型，再进入提交阶段，把该窗口
压缩到 Widget 重建本身。

### 迟到旧事件

停止旧 Event Worker 后，App 仍应在 `_project_runtime_update()` 入口验证
`workspace_key + session_id` 与当前 Client 一致。身份不匹配的迟到事件丢弃并记录诊断，不进入当前
Conversation。

## 测试范围

### 纯状态与命令

- 精确解析 `/resume`、`/clear` 和大小写；
- 未知斜杠文本保持普通消息；
- 有图片的命令恢复完整草稿；
- Ready/Running/Cancelling/Failed/Switching、Pending 和 submitting 的门禁；
- Session ID 缩写和第一条输入裁剪。

### GatewayClient

- `create_if_missing=True` 保持现有自动创建；
- `False` 时不存在直接失败；
- Session 列表模型；
- 显式清理任意旧 Session ID；
- 候选订阅和历史缓冲无遗漏。

### App 功能

- `/resume` 不进入 Pending Queue 或 Agent 输入；
- Picker 列表、空态、选择和取消；
- 选择历史 Session 恢复完整 Conversation；
- 选择当前 Session 也重新加载内容；
- 目标 Busy、Not Found、历史损坏和连接失败保留当前界面；
- 切换后旧 Session 事件不污染目标；
- `/clear` 从非空会话创建新 Session；
- 空会话 `/clear` 不重复创建；
- 切走和退出清理空 Session；
- Candidate 在激活提交前失败时清理新建空 Session；
- `--session-id` 和无参数启动行为回归。

### 视觉与真实终端

- Session Picker 宽屏、窄屏、长首条输入和空列表快照；
- 键盘选择、Escape、焦点恢复；
- 切换后 Conversation 自动跟随和 Composer 焦点正确；
- 真实 Gateway 下完成新建 A、对话、Clear 到 B、Resume A、重启后再次 Resume A。

## 完成标准

- 用户可以只通过 TUI 管理当前 Workspace 的基本 Session 生命周期；
- Session 恢复与命令行启动复用同一准备和历史投影路径；
- 所有 Session 命令只在空闲态执行；
- 失败不会留下半切换界面；
- 空 Session 不显示、不长期积累；
- TUI 不依赖 Agent 内部实现或本地持久化目录。
