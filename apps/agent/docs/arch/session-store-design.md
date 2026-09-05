# Session Store Design｜Session 数据存储设计

> 状态：已实现。本文是 Session 与公共 Conversation 数据存储的当前实现说明。

## 1. 文档定位

本文定义 `apps/agent` 应用层的 `SessionStore`，用于统一管理本地 Workspace、Session 与公共
Conversation 数据。它取代当前由 `AgentRuntime`、`ConversationStore`、`DataPathResolver` 和
`MetadataStore` 分担的 Session 业务数据读写，使 `AgentRuntime` 只负责编排运行态，不再读取目录、
解析 `conversation.jsonl` 或了解数据库实现。

本文只定义 Agent 应用内部的数据边界。Gateway JSON-RPC 和 TUI 产品行为继续遵守：

- `apps/agent/docs/arch/device-agent-runtime-session-design.md`；
- `apps/agent/docs/arch/session-management-design.md`；
- `spec/session-management.md`。

实施步骤见：

- `apps/agent/docs/plan/session-store-development-plan.md`。

旧 `conversation.jsonl` 和旧 Session 目录不迁移、不兼容读取，也不与新存储双写。首次启用新存储时
必须使用不包含旧 Session 数据的 `ICARUS_DATA_DIR`。系统不自动删除旧数据，也不允许新数据库中的
Session 复用同路径下遗留的 Plugin State 或 Asset。

## 2. 设计结论

本次改造采用以下方案：

- 在 Agent Application 层新增一个具体的 `SessionStore`；
- `SessionStore` 使用 SQLAlchemy 2.x Async ORM 和 `aiosqlite` 管理本地 SQLite；
- 业务代码只依赖 `SessionStore` 和现有 Session/Conversation 领域类型；
- 类名、方法名和公开类型不包含 `SQLite`、`SQLAlchemy`、`ORM` 等技术实现名称；
- 不增加 `SessionStore` Protocol、数据库 Adapter、Repository Factory 或自制 BaseMapper；
- 不手写连接池、连接生命周期、事务框架、ORM、Query Builder 或迁移框架；
- Session 与公共 Conversation 进入数据库；Asset、Plugin State、Runtime Snapshot、Trace 和 Log
  继续使用文件；
- 本地 Agent 是 Session 与 Conversation 的唯一可写事实源，Backend 只能通过 Gateway 访问。

技术框架名称只能出现在依赖声明、`SessionStore` 内部实现和技术设计文档中，不得泄漏到
`AgentRuntime`、Gateway、TUI、公开模型或 RPC 协议。

## 3. 当前问题

当前 Session 业务数据分布如下：

| 数据或操作 | 当前实现 |
|---|---|
| Session 是否存在 | `DataPathResolver.session_exists()` 检查目录 |
| Session 枚举 | `DataPathResolver.list_session_ids()` 扫描目录 |
| Session 摘要 | `ConversationStore.read_summary()` 解码完整 journal |
| 公共 Conversation | `conversation.jsonl` |
| Update sequence | 扫描 journal 加内存 cache |
| Session 元数据 | `workspace.json`、`session.json` |
| 空 Session 删除 | `AgentRuntime` 校验后递归删除目录 |
| Runtime 状态 | `AgentRuntime._entries` |
| Blackboard/Plugin State | `plugin-state/*.json` |
| Asset、Trace、Log | Session 文件目录 |

`AgentRuntime` 因此同时承担 Runtime Manager、Session Registry、Session Repository、Conversation
Repository 和 Update Processor。Session 列表会随 Session 数量和历史长度增加而重复扫描文件，
Session 摘要、sequence 和 Conversation 写入也缺少一个统一事务边界。

## 4. 目标架构

```text
Gateway
   ↓
AgentRuntime                         # 设备级运行编排
   ├── SessionStore                  # Session/Conversation 数据 CRUD
   │   └── SQLAlchemy Async ORM
   │       └── SQLite
   └── SessionRuntime                # 单 Session 执行环境
       └── PluginRuntimeHost
           ├── AgentPlugin
           ├── BlackboardPlugin
           ├── UserInputPlugin
           ├── RuntimeUpdatePlugin
           └── PersistencePlugin
```

依赖方向固定为：

```text
Gateway → AgentRuntime → SessionStore
                       → SessionRuntime → Plugins
```

`SessionStore` 不依赖 `AgentRuntime`、`SessionRuntime`、Plugin Runtime、Gateway 或 TUI。
`SessionRuntime` 和 Plugin 也不直接调用 `SessionStore`。

## 5. 职责边界

### 5.1 SessionStore

`SessionStore` 负责：

- 初始化和关闭本地 Session 数据库；
- 创建或更新 Workspace 记录；
- 创建、查询和枚举 Session；
- 保存 Session 摘要、最近活动时间和 Conversation cursor；
- 为需要长期恢复的完整 `RuntimeUpdate` 分配单 Session 连续 sequence；
- 追加并读取完整或增量 Conversation；
- 判断 Session 是否已有用户内容；
- 对空 Session 执行持久化层二次校验和软删除；
- 使用成熟框架处理 ORM 映射、查询生成、连接获取与释放、事务提交与回滚。

`SessionStore` 不负责：

- 判断 SessionRuntime 是否 Busy；
- 创建、加载、卸载或停止 SessionRuntime；
- 管理 Task、Queue、EventBus 或 Plugin 后台工作；
- 组织 Blackboard 模型上下文；
- 保存 Plugin State、Asset、Trace 或 Log；
- 发布 RuntimeUpdate；
- 处理 Gateway 权限、连接或序列化。

### 5.2 AgentRuntime

`AgentRuntime` 继续负责：

- 设备级启动和停止；
- 内存 Session Registry 和 `_SessionEntry`；
- 每个 Session 的 `mutation_lock`；
- SessionRuntime single-flight 加载和卸载；
- Task 提交、取消、状态和 submission 幂等；
- Task、Queue、Plugin inbox 和后台工作的 Busy 判断；
- 空闲 SessionRuntime 自动卸载；
- 调用 `SessionStore` 完成业务数据操作；
- 只在 Conversation 事务成功后发布带 sequence 的公共 RuntimeUpdate。

改造后 `AgentRuntime` 不再知道：

- 数据库文件名称和表结构；
- SQLAlchemy 类型或 ORM 映射对象；
- `conversation.jsonl`；
- Session 目录是否存在；
- sequence 如何分配；
- Session 摘要如何存储和排序；
- 软删除字段如何更新。

### 5.3 SessionRuntime 与 Plugin

`SessionRuntime` 继续负责单 Session 的执行环境、Plugin 组装、提交、取消、Checkpoint、资源导入和
停止。Blackboard、UserInput、Agent、Skill 等 Plugin 继续保有各自的 Session 内职责。

`PersistencePlugin` 缩小为文件型运行状态与观测组件，继续处理：

- Blackboard/Plugin State；
- Runtime Snapshot；
- Asset；
- Trace；
- Log；
- Hook Context。

它不再通过 `workspace.json`、`session.json` 或目录存在性创建和更新业务 Session。

## 6. 模块布局与命名

首期采用简单的后端数据模块，不预先拆分 DAO、Mapper、Adapter 和 Repository 多层结构：

```text
apps/agent/src/application/
├── agent_runtime.py
├── session_runtime.py
├── session_entities.py
├── session_store.py
└── ...
```

职责如下：

- `session_entities.py`：数据库表映射、约束和索引；
- `session_store.py`：Engine、Session Factory、Schema 初始化、CRUD、事务和领域类型转换；
- `agent_runtime.py`：只调用 `SessionStore` 业务方法。

业务代码只导入和使用 `SessionStore`。数据库映射类保持模块内部，不从
`apps.agent.src.application` 公共导出。映射类使用 `_WorkspaceRow`、`_SessionRow` 和
`_ConversationUpdateRow` 等内部名称，避免映射对象被误当成公共领域模型。

禁止出现以下业务类型：

```text
SQLiteSessionStore
SQLAlchemySessionStore
OrmSessionRepository
DatabaseSessionManager
```

本项目使用的 `Session` 业务概念与 SQLAlchemy 的 ORM Session 不同。实现内部的 ORM 会话变量使用
`db` 等局部名称，避免在业务接口中引入第二种 `Session` 语义。

## 7. 数据模型

### 7.1 Workspace

每个 Workspace 一条记录：

| 字段 | 含义 |
|---|---|
| `workspace_key` | 稳定主键 |
| `workspace_path` | 当前规范化绝对路径 |
| `created_at` | 首次创建时间 |
| `last_seen_at` | 最近由 Session 操作确认的时间 |

同一个 `workspace_key` 再次出现时更新 `workspace_path` 和 `last_seen_at`，不创建重复 Workspace。

### 7.2 Session

每个 `workspace_key + session_id` 一条记录：

| 字段 | 含义 |
|---|---|
| `workspace_key` | Workspace 外键和联合主键 |
| `session_id` | Session 联合主键 |
| `created_at` | 创建时间 |
| `updated_at` | 最近业务数据更新时间 |
| `first_user_input` | 第一条已接受用户输入的稳定摘要 |
| `last_public_activity_at` | 最近一条公共 Conversation 时间 |
| `last_sequence` | 当前 Conversation cursor，初始为 0 |
| `deleted_at` | 软删除时间；未删除为 null |
| `delete_reason` | 软删除原因；未删除为 null |

首期 `delete_reason` 使用 `empty_cleanup`。字段保留字符串形式，后续只有出现真实产品操作时才扩展
`user_deleted` 等值。

Session 列表使用组合索引支持：

```text
workspace_key + deleted_at + last_public_activity_at
```

### 7.3 ConversationUpdate

每条需要长期恢复的完整 `RuntimeUpdate` 一条记录。`assistant.text_delta` 只用于实时显示，不进入
Conversation 历史；每个模型 Step 在流结束后以一条 `assistant.message` 保存完整文本：

| 字段 | 含义 |
|---|---|
| `workspace_key` | Session 联合外键和联合主键 |
| `session_id` | Session 联合外键和联合主键 |
| `sequence` | Session 内从 1 开始连续递增的联合主键 |
| `task_id` | 可选 Task 身份 |
| `update_type` | `RuntimeUpdate.type` |
| `payload` | JSON payload |
| `occurred_at` | Update 原始发生时间 |

`workspace_key + session_id + sequence` 唯一。ConversationUpdate 不单独软删除；Session 的
`deleted_at` 是整个聚合的 Tombstone。未来如需遮蔽单条消息，应单独设计 Redaction 记录，不能在本次
改造中修改追加历史语义。

数据库关系为：

```text
Workspace 1 ── N Session 1 ── N ConversationUpdate
```

数据库不保存 Runtime lifecycle、Busy 状态、活动 Task、Queue 长度或 Plugin 后台工作，这些仍以
`AgentRuntime` 和 `SessionRuntime` 内存快照为权威来源。

## 8. SessionStore 业务接口

`SessionStore` 是具体类，不为当前唯一实现增加 Protocol。接口保持面向业务，不暴露 ORM Entity、
Engine、数据库连接或通用 SQL 执行入口。

```python
class SessionStore:
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def create_session(
        self,
        identity: SessionIdentity,
    ) -> None: ...

    async def get_session(
        self,
        identity: SessionIdentity,
        *,
        include_deleted: bool = False,
    ) -> SessionRecord | None: ...

    async def session_exists(
        self,
        identity: SessionIdentity,
        *,
        include_deleted: bool = False,
    ) -> bool: ...

    async def list_session_ids(
        self,
        workspace_key: str,
    ) -> tuple[str, ...]: ...

    async def list_session_summaries(
        self,
        workspace_key: str,
    ) -> tuple[SessionSummary, ...]: ...

    async def append_update(
        self,
        identity: SessionIdentity,
        update: RuntimeUpdate,
    ) -> RuntimeUpdate: ...

    async def read_updates(
        self,
        identity: SessionIdentity,
        *,
        after_sequence: int = 0,
    ) -> tuple[tuple[RuntimeUpdate, ...], int]: ...

    async def soft_delete_empty_session(
        self,
        identity: SessionIdentity,
        *,
        reason: str,
    ) -> Literal["discarded", "not_empty", "not_found"]: ...
```

`SessionRecord` 是应用内部只读领域记录，用于表达持久化 Session；它不是 ORM Entity，也不暴露
框架类型。`get_session()` 用于取得包含软删除状态和持久化 cursor 的完整记录；普通存在性判断继续
使用 `session_exists()`，避免调用方依赖 ORM 映射。

不提供以下通用方法：

```text
execute_sql
save(entity)
update(table, values)
delete_where(...)
query_builder(...)
```

这些能力由内部 ORM 提供；对外接口只表达 Icarus 的 Session 业务操作。

## 9. 数据库生命周期

默认数据库位置为：

```text
ICARUS_DATA_DIR/icarus.db
```

`AgentRuntime.start()` 在接受调用前创建并启动 `SessionStore`；`AgentRuntime.stop()` 在所有
SessionRuntime、Update Queue 和订阅收束后关闭 `SessionStore`。关闭后的 Store 不可重新启动。

`SessionStore` 内部创建一个 Async Engine 和一个 Session Factory。每个业务方法通过框架上下文获取和
释放 ORM 会话；业务代码不管理连接、Cursor、连接池、提交或回滚。

数据库首次启动使用 ORM metadata 创建 Schema。本期不保留旧数据，因此不引入旧 JSONL 导入器。
启动时如果目标数据根目录已经存在旧 Session 目录但没有新数据库，系统明确失败并提示更换为空的
`ICARUS_DATA_DIR`；不扫描旧历史、不尝试判断其是否可复用，也不自动清理用户文件。
当数据库进入需要跨版本保留用户数据的稳定阶段后，使用 Alembic 管理 Schema 演进，不自研 Migration
Framework。

SQLite 外键约束、等待超时等连接级设置由 `SessionStore` 内部完成。若底层驱动需要少量方言初始化
语句，它们只能存在于数据库初始化代码，不能成为业务 CRUD SQL。

## 10. 核心事务

### 10.1 创建 Session

```text
AgentRuntime 取得对应 mutation_lock
→ SessionStore 创建或刷新 Workspace
→ 检查同一 Session 主键是否存在，包括已软删除记录
→ 不存在则插入 Session，last_sequence=0
→ 提交事务
→ AgentRuntime 加载 SessionRuntime
```

已软删除 Session 的 ID 不允许隐式复用。重复创建活动 Session 返回现有
`SessionAlreadyExistsError`。

SessionRuntime 启动失败时保留 Session 记录，AgentRuntime 将运行状态记为 failed，后续允许显式重试
恢复。数据库事务不跨越 SessionRuntime、Plugin State 和文件创建过程。

### 10.2 追加公共 Update

```text
RuntimeUpdatePlugin 生成无 sequence 的 RuntimeUpdate
→ AgentRuntime 按当前 Update Loop 和 mutation_lock 处理运行态
→ assistant.text_delta 仅实时发布，不写 SessionStore
→ assistant.message、user.message、Tool 摘要和 Task 状态进入持久化流程
→ SessionStore 开启事务
→ 查询未软删除 Session
→ last_sequence + 1
→ 插入 ConversationUpdate
→ 更新 last_sequence、updated_at、last_public_activity_at
→ 第一条 user.message 同事务写入 first_user_input
→ 提交事务
→ 返回带 sequence 的 RuntimeUpdate
→ AgentRuntime 发布给订阅者
```

只有事务成功后才能发布带 sequence 的持久化 Update。实时 `assistant.text_delta` 不带 sequence，
完成时的 `assistant.message` 是恢复对话的事实记录。Sequence 由 Session 行和 Conversation 唯一约束
共同保护，不使用 journal 扫描或进程内 sequence cache。取消或异常前已流出的部分文本也收束成一条
`assistant.message`，避免恢复时丢失用户已经看到的内容。

第一条用户输入的摘要规则沿用现有设计：归一化空白，最多 256 个 Unicode 字符；纯图片使用
`[Image]`，无法识别内容时使用 `[Message]`。后续用户消息不修改 `first_user_input`。

### 10.3 读取历史

```text
查询未软删除 Session
→ 查询 sequence > after_sequence 的 ConversationUpdate
→ 按 sequence 升序
→ 转换为 RuntimeUpdate
→ 将旧版本连续 assistant.text_delta 按 task_id + step 聚合为 assistant.message
→ cursor 返回 Session.last_sequence
```

`after_sequence` 必须大于等于 0。不存在或已软删除 Session 返回 `SessionNotFoundError`。旧历史聚合后
保留最后一个 delta 的 sequence，因此逻辑历史允许 sequence 跳号；数据库物理记录仍严格连续。Gateway
在该结果上分页，TUI 循环读取到 `history_cursor`，完整恢复不依赖单条 WebSocket 消息大小。

### 10.4 Session 列表

```text
按 workspace_key 查询
→ 排除 deleted_at 非空的 Session
→ 排除 first_user_input 为空的 Session
→ last_public_activity_at 降序、session_id 升序稳定排序
→ 转换为 SessionSummary
```

列表不创建 `_SessionEntry`，不加载 SessionRuntime，也不刷新 Runtime 空闲时间。

### 10.5 空 Session 软删除

```text
AgentRuntime 取得 mutation_lock
→ 检查 loading、unloading、Task、Queue、Event 和 Plugin 后台工作
→ 如 Runtime 已加载，先按正常流程卸载
→ SessionStore 开启事务
→ 再次查询未软删除 Session
→ 持久化层确认不存在 user.message
→ 更新 deleted_at、delete_reason、updated_at
→ 提交事务
→ AgentRuntime 移除同一 Registry Entry
```

AgentRuntime 负责运行态 `busy`；SessionStore 负责持久化层 `not_empty`。两层检查不能互相替代。

软删除后：

- 普通列表、查询、恢复和提交均视为不存在；
- Session ID 不允许重新创建；
- Conversation 和 Session 文件目录继续保留；
- 首期不开放恢复、非空 Session 删除或物理清理；
- 将来永久删除需要单独定义 purge，并同时处理数据库记录和文件资源。

## 11. 错误处理

- Store 未启动或已关闭：明确拒绝调用；
- 创建冲突：转换为 `SessionAlreadyExistsError`；
- 不存在或已软删除：转换为 `SessionNotFoundError`；
- Conversation payload 不能 JSON 序列化：在写入前失败；
- 数据库事务失败：框架回滚，不能发布对应 RuntimeUpdate；
- 历史解码失败：请求明确失败，不伪造空历史；
- 软删除失败：不移除 Registry Entry，不返回 `discarded`；
- Store 启动失败：AgentRuntime 不进入可接受调用状态；
- Store 关闭失败：与其他 AgentRuntime 收束错误一起报告。

公共写入失败时，AgentRuntime 必须锁存该 Session 的持久化失败状态，停止接受新的提交并尝试正常
收束 SessionRuntime。由于同一数据存储已经失败，不再尝试向 Conversation 写入一条描述自身失败的
错误 Update；错误通过 Session 状态和应用日志暴露。

## 12. 其他模块适配

### 12.1 AgentRuntime

删除以下直接数据操作：

```text
DataPathResolver.session_exists
DataPathResolver.list_session_ids
DataPathResolver.discard_session
ConversationStore.append
ConversationStore.read
ConversationStore.read_summary
ConversationStore sequence cache
```

替换为 `SessionStore` 的异步业务方法。需要查询持久化数据的 `get_session_status()`、
`list_session_statuses()`、`list_session_summaries()` 和 `get_session_history()` 统一调整为异步。

### 12.2 PersistencePlugin

停止通过 `MetadataStore.initialize()` 和 `update_session_status()` 创建或更新 Workspace/Session 业务
元数据。删除 `MetadataStore.initialize()` 和 `update_session_status()`；保留 JSON State 和 Runtime
Snapshot 写入能力，并将该内部组件收敛为只处理文件状态的 `JsonStateStore`。

PersistencePlugin 当前提供的 `session` Capability 实际值是 `SessionIdentity`。本次改造不为命名清理
扩大范围；后续可独立评估重命名为 `session_identity`。

### 12.3 DataPathResolver

继续提供 Asset、Plugin State、Trace、Log、Incoming Resource 和 Skill 相关路径。目录存在性不再表示
业务 Session 存在，目录枚举也不再作为 Session 列表。

### 12.4 Gateway

公开 JSON-RPC 方法、参数和响应保持不变。内部 Handler 对异步 AgentRuntime 查询增加 `await`。

### 12.5 TUI

`/resume`、`/clear`、历史恢复、实时订阅、取消和断线对账行为保持不变。TUI 不知道数据库或 ORM 的
存在，只运行现有功能和 Snapshot 回归。

### 12.6 Backend

Backend 只能通过 Gateway 查询和订阅本地 Agent 数据，不能直接打开 `icarus.db`。如未来保存服务端
只读投影，仍以本地 Agent 的 `workspace_key + session_id + sequence` 为权威顺序，不形成第二个可写
Conversation 事实源。

## 13. 文件数据边界

改造后本地数据布局为：

```text
ICARUS_DATA_DIR/
├── icarus.db
├── incoming/
├── skills/
└── workspaces/
    └── <workspace_key>/
        └── sessions/
            └── <session_id>/
                ├── assets/
                ├── plugin-state/
                ├── runtime-snapshot.json
                ├── trace.jsonl
                └── runtime.log
```

旧 `workspace.json`、`session.json` 和 `conversation.jsonl` 不再创建，也不作为 fallback。数据库中的
Session 可以在尚未产生文件资源时没有对应 Session 目录；只有 Asset、State、Trace 或 Log 首次写入时
才需要创建目录。

## 14. 依赖与框架使用原则

新增运行依赖：

```text
SQLAlchemy[asyncio]>=2,<3
aiosqlite>=0.20,<1
```

使用成熟框架提供的：

- Declarative 表映射；
- ORM CRUD 和查询表达式；
- Async Engine 和 ORM Session 生命周期；
- 连接获取、复用与释放；
- 事务提交和回滚；
- 类型转换、约束和索引；
- metadata Schema 创建；
- 后续与 Alembic 的标准迁移路径。

Icarus 只实现自己的业务语义，不重复实现 ORM、连接池、事务框架、Mapper 基类、Query Builder 或
Migration Framework。业务 CRUD 不使用手写 SQL；仅允许数据库连接初始化中存在底层方言要求的配置。

## 15. 测试范围

### 15.1 SessionStore

- 首次启动创建 Schema，重复启动幂等；
- Workspace 创建和路径刷新；
- Session 创建、重复创建和已软删除 ID 冲突；
- Session 普通查询与 `include_deleted` 查询；
- Session ID 和摘要稳定排序；
- 空 Session 不进入产品摘要列表；
- Conversation sequence 从 1 连续递增；
- Update payload、时间和身份往返一致；
- `after_sequence` 增量读取和 cursor；
- 实时 delta 不持久化，完整 Assistant 消息持久化；
- 旧 delta 历史按 Task Step 无损聚合；
- 第一条文本、纯图片和降级消息摘要；
- 追加 Update 与摘要更新同事务回滚；
- 空 Session 软删除、非空拒绝、重复删除和不存在；
- 已软删除 Session 不可追加或普通读取；
- Store start/close 和关闭后拒绝调用。

测试使用临时 SQLite 数据库和真实 `SessionStore`，不为单一实现创建通用 Fake Repository。

### 15.2 AgentRuntime

- 创建 Session 先写 Store，再加载 Runtime；
- Runtime 启动失败保留可重试的 Session 记录；
- 并发创建、resume、submit、unload 和软删除保持现有互斥语义；
- Conversation 提交成功后才向订阅者发布；
- Store 写入失败不发布并锁存 Session 失败；
- 列表和历史查询不加载 SessionRuntime、不刷新空闲时间；
- 空 Session 删除仍同时检查运行态和持久化内容；
- AgentRuntime 停止顺序正确关闭所有 Runtime、Update Queue 和 Store。

### 15.3 跨应用回归

- Gateway 全量测试；
- TUI `/resume`、`/clear`、历史恢复和异常恢复测试；
- TUI Snapshot；
- Agent、Gateway、TUI 全量门禁和 `git diff --check`。

## 16. 首期明确不做

- 不迁移或兼容旧 JSONL 和旧 Session 目录；
- 不双写文件和数据库；
- 不增加 PostgreSQL/MySQL 实现；
- 不增加数据库 Adapter、Protocol、Repository Factory 或 BaseMapper；
- 不让 Backend 直接访问本地数据库；
- 不把 Plugin State、Asset、Trace、Log 或 Skill 放入数据库；
- 不增加 Session 搜索、重命名、归档和全文检索；
- 不开放非空 Session 删除、恢复已删除 Session 或物理清理；
- 不增加 Conversation 分页 RPC 或单条消息删除；
- 不在本次改造中拆分 Session Registry 或 RuntimeUpdate Processor。

## 17. 完成标准

- `SessionStore` 成为 Session 与公共 Conversation 的唯一持久化事实源；
- `AgentRuntime` 不再扫描目录、读取 JSONL、分配持久化 sequence 或操作数据库实现类型；
- 业务代码和公开接口中不存在数据库或 ORM 框架命名；
- ORM 映射、CRUD、事务和连接生命周期由成熟框架处理；
- SessionRuntime 和 Plugin 的运行职责保持不变；
- Gateway/TUI 协议和产品行为保持兼容；
- 软删除同时满足 AgentRuntime 运行态校验和 SessionStore 持久化校验；
- 旧数据不被读取、迁移或双写；
- 相关定向测试、Agent 全量测试、Gateway 全量测试、TUI 全量测试和 diff 检查通过。
