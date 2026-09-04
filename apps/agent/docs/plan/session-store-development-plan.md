# Session Store Development Plan｜Session 数据存储实施计划

## 目标

基于 `apps/agent/docs/arch/session-store-design.md`，将当前由 Session 目录和
`conversation.jsonl` 承担的 Session 业务数据统一迁移到 `SessionStore`，并让
`AgentRuntime` 只负责运行态编排。

本次实施完成后：

- `SessionStore` 是 Workspace、Session 和公共 Conversation 的唯一持久化入口；
- SQLAlchemy 负责表映射、CRUD、查询、连接生命周期和事务；
- AgentRuntime 不再扫描目录、解析 JSONL、分配持久化 sequence 或操作数据库类型；
- SessionRuntime 和 Plugin 的执行职责保持不变；
- Gateway/TUI 协议和用户行为保持兼容；
- 旧 Session 数据不迁移、不兼容读取，也不双写。

## 实施原则

- 业务代码只使用 `SessionStore` 和现有领域类型，不出现
  `SQLiteSessionStore`、`SQLAlchemySessionStore`、ORM Entity 或数据库连接类型；
- 使用 SQLAlchemy 2.x Async ORM 和 `aiosqlite`，不重复实现 ORM、连接池、Mapper 基类、
  Query Builder、事务框架或 Migration Framework；
- 第一阶段只提供一个具体 `SessionStore`，不增加 Protocol、Adapter、Repository Factory 或
  PostgreSQL/MySQL 实现；
- Session 与公共 Conversation 进入数据库，Plugin State、Asset、Runtime Snapshot、Trace、Log 和
  Skill 继续使用文件；
- AgentRuntime 负责运行态 Busy 和 mutation lock，SessionStore 负责持久化内容与事务一致性；
- 公共 RuntimeUpdate 只有在数据库事务提交成功后才能发布；
- 每个阶段先补定向测试，再修改实现；不顺带拆分 Session Registry 或 RuntimeUpdate Processor；
- 当前变更只在 `feat/agent` 分支实施；除 Gateway 内部 await 适配外，不修改 TUI 产品逻辑。

## 目标文件结构

```text
apps/agent/src/application/
├── agent_runtime.py
├── session_runtime.py
├── session_entities.py
├── session_store.py
├── runtime_status.py
└── ...

apps/agent/test/application/
├── test_session_store.py
├── test_agent_runtime.py
└── ...

apps/agent/src/agent_orchestration/plugins/persistence/
├── json_state_store.py
├── path_resolver.py
├── plugin.py
├── runtime.py
└── ...
```

删除：

- `apps/agent/src/agent_orchestration/plugins/persistence/conversation_store.py`；
- `apps/agent/test/agent_orchestration/plugins/persistence/test_conversation_store.py`；
- `apps/agent/src/agent_orchestration/plugins/persistence/metadata_store.py`，其 JSON State 职责迁移到
  `json_state_store.py`。

业务层只从 `apps.agent.src.application` 导出 `SessionStore` 和需要公开的领域类型。
`session_entities.py` 的映射类不加入公共导出。

## 阶段一：引入数据框架和表映射

### 更新文件

- `apps/agent/requirements.txt`
- `apps/agent/src/application/session_entities.py`
- `apps/agent/src/application/session_store.py`
- `apps/agent/test/application/test_session_store.py`

### 开发内容

1. 增加运行依赖：

   ```text
   SQLAlchemy[asyncio]>=2,<3
   aiosqlite>=0.20,<1
   ```

2. 在 `session_entities.py` 建立私有 Declarative 映射：
   - `_WorkspaceRow`；
   - `_SessionRow`；
   - `_ConversationUpdateRow`。
3. 建立约束和索引：
   - Workspace 以 `workspace_key` 为主键；
   - Session 以 `workspace_key + session_id` 为联合主键；
   - ConversationUpdate 以 `workspace_key + session_id + sequence` 为联合主键；
   - ConversationUpdate 使用联合外键关联 Session；
   - Session 列表索引覆盖 `workspace_key + deleted_at + last_public_activity_at`。
4. `payload` 使用 SQLAlchemy JSON 类型；所有时间写入前统一为 UTC，读取后恢复为带时区
   `datetime`，不得向领域层返回 SQLite 产生的 naive datetime。
5. 在 `SessionStore` 中创建 Async Engine 和 `async_sessionmaker`。业务方法通过 ORM Session 上下文
   获取和释放连接，不自行管理 Cursor、连接池或 commit/rollback。
6. `start()` 使用 ORM metadata 创建 Schema，并完成 SQLite 所需的外键、busy timeout 等连接初始化；
   `close()` dispose Engine，关闭后拒绝后续调用。
7. 配置 SQLite 外键、WAL 和 busy timeout；连接、连接池、事务提交和回滚继续交给 SQLAlchemy。
   首期不额外实现全局数据库写锁，依赖 AgentRuntime 的每 Session `mutation_lock`、数据库事务和唯一
   约束；只有并发测试证明跨 Session 写入仍有真实争用时再增加最小限流。
8. 数据库首次创建前检查目标 `ICARUS_DATA_DIR`。如果没有新数据库但已存在旧 Session 数据目录，
   启动明确失败并提示使用新的空数据目录；不读取、迁移或自动删除旧文件。
9. 不引入 Alembic。数据进入需要跨版本保留的稳定阶段后再接入标准迁移工具。

### 定向测试

- 首次 `start()` 创建表和索引；
- 重复 `start()` 或重复建表幂等；
- `close()` 释放资源，关闭后调用明确失败；
- 外键约束生效；
- JSON 和带时区 UTC 时间往返一致；
- 多个 Session 并发写入不会产生重复 Workspace、重复 sequence 或未处理的锁争用；
- 无数据库但存在旧 Session 数据目录时安全失败，空数据目录可以正常初始化。

## 阶段二：实现 Session CRUD 与软删除

### 更新文件

- `apps/agent/src/application/session_store.py`
- `apps/agent/src/application/runtime_status.py`
- `apps/agent/test/application/test_session_store.py`

### 开发内容

1. 增加应用内部只读 `SessionRecord`，只使用 Python 领域类型，不继承或暴露 ORM 映射类。
2. 实现 `create_session(identity)`：
   - 同一事务创建或刷新 Workspace；
   - 检查包括软删除记录在内的 Session 主键；
   - 插入初始 `last_sequence=0` 的 Session；
   - 活动或已软删除 ID 都不能隐式复用。
3. 实现 `get_session()` 和 `session_exists()`：
   - 默认排除软删除 Session；
   - 只有维护和诊断调用可以显式 `include_deleted=True`。
4. 实现 `list_session_ids()`：
   - 只返回指定 Workspace 下未软删除 Session；
   - 结果按 Session ID 稳定排序；
   - 不读取文件目录，不加载 SessionRuntime。
5. 实现 `list_session_summaries()`：
   - 排除软删除和没有 `first_user_input` 的空 Session；
   - 按 `last_public_activity_at` 倒序、`session_id` 升序稳定排序；
   - 继续返回现有 `SessionSummary`。
6. 实现 `soft_delete_empty_session()`：
   - 事务内再次查询未删除 Session；
   - 检查持久化层不存在 `user.message`；
   - 更新 `deleted_at`、`delete_reason` 和 `updated_at`；
   - 返回 `discarded`、`not_empty` 或 `not_found`；
   - 不删除 Conversation 和文件目录。
7. Store 不判断 Runtime Busy，也不接收 Runtime Snapshot；运行态检查继续由 AgentRuntime 完成。

### 定向测试

- Workspace 首次创建和路径刷新；
- Session 创建、重复创建和软删除 ID 冲突；
- 普通查询与 `include_deleted` 查询；
- 多 Workspace 隔离；
- Session ID 和摘要排序；
- 空 Session 不进入摘要列表；
- 空 Session 软删除、非空拒绝、重复删除和不存在；
- 软删除不删除 Conversation 或 Session 文件。

## 阶段三：实现公共 Conversation 事务

### 更新文件

- `apps/agent/src/application/session_store.py`
- `apps/agent/test/application/test_session_store.py`

### 开发内容

1. 实现 `append_update(identity, update)`：
   - 校验 Update 与 SessionIdentity 完全一致；
   - 校验 payload 可以由 JSON 类型安全保存；
   - 查询未软删除 Session；
   - 在同一事务中计算 `last_sequence + 1`、插入 ConversationUpdate，并更新 Session cursor 与时间；
   - 第一条 `user.message` 同事务生成并写入 `first_user_input`；
   - 返回带 sequence 的新 `RuntimeUpdate`。
2. 摘要规则复用现有行为：
   - 归一化换行和连续空白；
   - 最多 256 个 Unicode 字符；
   - 纯图片输入使用 `[Image]`；
   - 无文本和资源时使用 `[Message]`；
   - 后续用户消息不改变摘要。
3. 实现 `read_updates(identity, after_sequence=0)`：
   - `after_sequence` 必须大于等于 0；
   - 未删除 Session 才可读取；
   - 按 sequence 升序转换为 `RuntimeUpdate`；
   - cursor 返回 Session 当前 `last_sequence`；
   - 空历史返回空记录和 cursor 0。
4. 保留 `ConversationHistoryCorruptError` 的应用语义，并将其所有权从旧 Persistence ConversationStore
   迁移到 SessionStore；无效 type、payload、时间或 sequence 明确失败，不返回伪造空历史。
5. 不提供通用 `save(entity)`、`execute_sql()` 或 Query Builder；所有方法只表达 Session 业务操作。

### 定向测试

- sequence 从 1 连续递增；
- 同一 Session 顺序稳定，不同 Session 相互隔离；
- 文本、图片和降级摘要正确；
- Update payload、task_id、时间和 type 往返一致；
- `after_sequence` 和 cursor 正确；
- 不存在或软删除 Session 拒绝写入和读取；
- Update 插入、cursor 和摘要任一步失败时整体回滚；
- 非法 payload 或损坏数据库记录明确失败。

## 阶段四：AgentRuntime 接入 SessionStore

### 更新文件

- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/src/application/__init__.py`
- `apps/agent/test/application/test_agent_runtime.py`

### 开发内容

1. `AgentRuntime` 构造时允许注入 `SessionStore`；生产环境未注入时，在 `start()` 根据
   `ICARUS_DATA_DIR` 创建具体 `SessionStore`。业务字段和方法名不包含数据库或 ORM 框架名称。
2. `start()` 必须先启动 Store，再把 Runtime 标记为可接受调用；Store 启动失败时清理已创建资源并保持
   Runtime stopped。
3. `create_session()`：
   - 在 `_SessionEntry.mutation_lock` 内调用 Store 创建；
   - 创建成功后才发起 SessionRuntime 加载；
   - Runtime 启动失败保留 Session 记录并标记内存 lifecycle failed，允许后续恢复；
   - Store 创建冲突继续映射为现有 `SessionAlreadyExistsError`。
4. `submit()`：
   - 已卸载 Session 通过 Store 判断是否存在；
   - 用户消息改用 `await store.append_update()`；
   - 只有事务成功才发布用户消息并返回 accepted；
   - 写入失败时拒绝后续提交，取消本次已接受 Task，并将 Session 锁存为持久化失败，不能让未记录的
     Agent 执行继续产生外部可见结果。
5. `_update_loop()`：
   - 所有非 `session.lifecycle` Update 使用 Store 追加；
   - Checkpoint 错误 Update 和最终失败 Update 保持现有顺序；
   - 每条 Update 提交成功后才发布；
   - Store 写入失败时不发布该 Update，在释放当前 mutation lock 后锁存 Session 失败并收束对应
     SessionRuntime，避免停止流程重新取得同一锁时死锁；
   - 不尝试向已失败的同一 Store 再写一条描述 Store 失败的 Conversation Update。
6. `get_session_status()`、`list_session_statuses()`、`list_session_summaries()` 改为 async：
   - Registry 中的运行状态仍来自内存；
   - 未加载 Session 的存在性和 ID 列表来自 Store；
   - 列表查询不创建 `_SessionEntry`、不加载 Runtime、不刷新空闲时间。
7. `get_session_history()` 改用 Store 读取和追加异常退出的 `interrupted` 终态，保持现有恢复语义。
8. `discard_empty_session()`：
   - 保留 `mutation_lock`、`discarding`、Busy 判断和先卸载后复检；
   - 最终持久化操作改为 Store 软删除；
   - 成功后移除同一个 Registry Entry，不删除文件；
   - Store 失败时清除 `discarding`，保留 Entry 并上抛。
9. `stop()` 在全部 SessionRuntime、Update Queue 和订阅收束后关闭 Store；Store 关闭错误进入现有关闭
   错误汇总。
10. 删除 `_resolver()`、`_history_store()` 和 `_conversation_store`；资源导入只通过保留的文件路径组件
    解析 Incoming Resource。

### 定向测试

- Store 生命周期早于 Runtime accepting，晚于所有 Update 收束；
- 创建、恢复、提交、取消、卸载和状态查询不回归；
- Runtime 启动失败保留可恢复 Session；
- 同 Session 并发 create/resume/submit/delete 保持串行；
- 不同 Session 可以并发运行；
- Store 写入失败不发布 Update、不继续接收提交并收束 Runtime；
- 异常退出任务在首次历史读取时只补一个 interrupted 终态；
- 列表和历史查询不加载 Runtime；
- 空 Session 软删除与并发 submit 竞争保持 fail closed；
- idle unload 只释放 Runtime，不修改 Store Session 记录。

## 阶段五：收敛 Persistence 文件职责

### 更新文件

- `apps/agent/src/agent_orchestration/plugins/persistence/__init__.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/runtime.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/path_resolver.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/json_state_store.py`
- `apps/agent/test/agent_orchestration/plugins/persistence/`

### 开发内容

1. 删除 `ConversationStore` 及其公共导出，公共 Conversation 只由 SessionStore 保存。
2. 将 `MetadataStore` 的 JSON 读写职责迁移为内部 `JsonStateStore`：
   - 只保存和读取 Plugin State 与 Runtime Snapshot；
   - 不创建 `workspace.json`、`session.json`；
   - 不维护 active/closed 业务状态。
3. `PersistencePlugin.start()`、`stop()`、`PersistenceRuntime.open_session()`、`session_scope()` 和
   `PersistenceSession.task_scope()` 移除 Session 元数据初始化和状态写入，只保留 Context 与文件资源
   生命周期。
4. `DataPathResolver` 删除业务数据方法：
   - `workspace_metadata()`；
   - `session_metadata()`；
   - `conversation_file()`；
   - `session_exists()`；
   - `list_session_ids()`；
   - `discard_session()`。
5. 保留并验证：
   - Workspace/Session 文件目录；
   - Asset 和 Incoming Resource；
   - Plugin State；
   - Runtime Snapshot；
   - Trace 和 Log。
6. 不重命名 PersistencePlugin 的 `session` Capability；该命名清理不属于本次数据改造。

### 定向测试

- Plugin State 和 Runtime Snapshot 保存、恢复不回归；
- 无 State/Asset/Trace/Log 的新 Session 不要求提前创建文件目录；
- 首次写入对应文件时安全创建目录和权限；
- 图片导入、解析和缺失错误不回归；
- Trace 和 Session 日志仍按完整 SessionIdentity 隔离；
- Persistence 启停不再创建业务元数据 JSON。

## 阶段六：Gateway 内部异步适配

### 更新文件

- `apps/gateway/src/protocol/methods.py`
- `apps/gateway/test/test_methods.py`
- Gateway 测试 Runtime Stub

### 开发内容

1. 对改为异步的 AgentRuntime 查询补充 `await`：
   - `get_session_status()`；
   - `list_session_statuses()` 的内部调用；
   - `list_session_summaries()`。
2. 保持以下 RPC 名称、参数、响应和业务错误不变：
   - `session.create`；
   - `session.list`；
   - `session.get`；
   - `session.get_history`；
   - `session.discard_empty`；
   - `session.submit`、`cancel`、`unload`。
3. `ConversationHistoryCorruptError` 的 import 改为 Agent Application 所有，不把 ORM 或数据库异常
   暴露到 Gateway。
4. Store 内部故障继续转换为安全的 Gateway internal error；日志保留完整本地异常，不向客户端暴露
   数据库路径、SQL 或连接信息。

### 定向测试

- Session create/list/get/history/discard 方法正确 await Runtime；
- Wire model 和 JSON 响应不变；
- session not found、exists、history corrupt 和 internal error 映射不回归；
- WebSocket 订阅和 RuntimeUpdate 推送不受存储实现影响。

## 阶段七：文档同步与全量验收

### 更新文件

- `apps/agent/README.md`
- `README.md`
- `apps/agent/docs/arch/session-store-design.md`
- `apps/agent/docs/arch/session-management-design.md`
- `apps/agent/docs/arch/device-agent-runtime-session-design.md`
- `apps/agent/docs/plan/session-store-development-plan.md`
- `docs/todo/agent-core.md`
- `docs/todo/development-roadmap.md`

### 开发内容

1. 实施完成后移除 `session-store-design.md` 顶部“尚未实现”标记，并将当前事实更新为 SessionStore。
2. 将旧文档中的 Session 目录存在性、`conversation.jsonl`、物理删除和同步查询描述改为新实现。
3. README 将 `ICARUS_DATA_DIR/icarus.db` 记录为 Session/Conversation 事实源，并继续说明文件数据边界。
4. 明确旧数据不迁移、不兼容、不双写；使用者需要使用新的空数据目录。
5. 记录 Backend 仍通过 Gateway 访问本地 Agent，不直接读取数据库。
6. 路线图将“对话元数据、历史和索引持久化”更新为已完成，但不提前勾选搜索、重命名、非空删除、
   Memory 或 Backend 同步。

### 验证顺序

先执行 Store 定向测试：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/application/test_session_store.py -q
```

再执行 Agent 相关目录：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/application/test_agent_runtime.py \
  apps/agent/test/agent_orchestration/plugins/persistence -q
```

然后执行应用级门禁：

```bash
make test-agent
make test-gateway
make test-tui
```

最后执行仓库总门禁：

```bash
make test
git diff --check
```

如果本地有可用模型凭据，再执行一次小型真实模型恢复冒烟：

```text
创建 Session
→ 提交一轮文本任务
→ 正常退出 Agent/Gateway
→ 重启
→ session.list 可见
→ session.get_history 恢复
→ 同一 Session 继续一轮对话
```

冒烟测试不得输出 API Key、完整私有 Prompt 或本地敏感路径。

## 风险与处理

### 1. Store 写入发生在 Task 接受之后

当前 `UserInputPlugin.submit()` 会先接受 Task，再由 AgentRuntime 写入用户公共消息。Store 写入失败时
Task 可能已经进入队列，因此实施必须补充确定性取消和 Session 持久化失败锁存，不能只向调用方返回
异常后让任务继续执行。

### 2. 数据库与文件不共享事务

Session 数据库与 Plugin State、Asset、Trace、Log 无法形成单一事务。本期通过明确所有权处理：
Session 存在性以数据库为准；文件按需创建；软删除只写 Tombstone，不物理删除文件。

### 3. SQLite 是单写者

当前设备级 AgentRuntime 是唯一写入者。同一 Session 的写操作由现有 `mutation_lock` 串行，跨 Session
写入依赖 ORM 事务、数据库约束、WAL 和 busy timeout；首期不预设全局写锁。并发测试证明存在真实
争用时再增加最小限流。不支持多个 AgentRuntime 进程同时写同一 `ICARUS_DATA_DIR`；如未来需要
多进程写入，必须重新设计部署和所有权，不能只增加连接数。

### 4. 旧文件与新数据库不兼容

本期不迁移旧数据。启用新 Store 时必须使用新的空 `ICARUS_DATA_DIR`，避免显式复用旧 Session ID 时
错误加载遗留 Plugin State 或 Asset。系统不自动删除旧目录。

### 5. Framework 类型泄漏

代码评审必须检查 AgentRuntime、Gateway、TUI 和公共类型没有导入 SQLAlchemy Entity、Engine、
AsyncSession 或数据库方言类型。数据库框架只存在于 `session_entities.py` 和 `session_store.py` 内部。

## 首期明确不做

- 不迁移、扫描或兼容旧 JSONL；
- 不双写 `conversation.jsonl`；
- 不实现 PostgreSQL/MySQL；
- 不增加数据库 Adapter、Protocol、Repository Factory 或 BaseMapper；
- 不让 Backend 直接访问本地数据库；
- 不把 Plugin State、Asset、Trace、Log 或 Skill 放入数据库；
- 不增加 Session 搜索、重命名、归档和全文检索；
- 不开放非空 Session 删除、恢复软删除 Session 或物理清理；
- 不增加 Conversation 分页 RPC 或单条消息删除；
- 不重命名 PersistencePlugin Capability；
- 不拆分 AgentRuntime 的 Session Registry 或 RuntimeUpdate Processor。

## 完成标准

- `SessionStore` 成为 Session 和公共 Conversation 的唯一持久化入口；
- AgentRuntime 不再读取 Session 目录或 `conversation.jsonl`；
- Session CRUD、Conversation sequence、摘要和软删除由 ORM 事务保证一致；
- 业务代码和公共接口不包含数据库或 ORM 框架命名；
- PersistencePlugin 只负责文件状态、Asset、Trace、Log 和 Hook；
- Store 写入失败不会发布未持久化 Update，也不会留下继续执行的失管 Task；
- Gateway/TUI 协议和产品行为保持不变；
- 旧数据不被迁移、兼容读取或双写；
- Agent、Gateway、TUI 全量测试和仓库总门禁通过；
- 文档与最终代码保持一致。

## 实施结果

- 已新增 `SessionStore` 和私有表映射，使用 SQLAlchemy Async ORM、`aiosqlite` 和本地
  `ICARUS_DATA_DIR/icarus.db`；
- 已实现 Workspace/Session CRUD、Session 摘要、公共 Conversation、连续 sequence、增量读取和
  Session Tombstone 软删除；
- 同一 Session 的并发 append 使用数据库原子更新返回 sequence；10 条并发写入稳定得到 1–10；
- AgentRuntime 已移除目录枚举、JSONL 解析和 sequence cache，并在 Store 事务成功后才发布 Update；
- Conversation 落库失败时取消活动 Task、锁存 Session、发布 failed lifecycle 并卸载 SessionRuntime；
- PersistencePlugin 已删除 ConversationStore 和业务 MetadataStore，只保留 JsonStateStore、Asset、
  Runtime Snapshot、Trace、Log 和 Hook；
- Gateway 已适配异步状态与摘要查询，RPC 名称、参数和响应保持不变；
- AgentRuntime 重启测试已验证同一数据库可以枚举、恢复公共历史、补齐 interrupted 终态并继续提交；
- 旧 JSONL 不迁移、不兼容、不双写；检测到旧 Session 目录且没有新数据库时启动安全失败；
- 最终门禁：Agent `365 passed`、Gateway `11 passed`、TUI `161 passed`、TUI Snapshot `12 passed`，
  `git diff --check` 通过。

## 建议提交拆分

只有用户后续明确要求提交时才执行：

1. `feat(agent): add session store persistence`
   - 依赖、ORM 映射、SessionStore 和定向测试；
2. `refactor(agent): route session data through store`
   - AgentRuntime、Persistence 文件职责和对应测试；
3. `refactor(gateway): await session store backed runtime queries`
   - Gateway 内部异步适配和测试；
4. `docs(agent): document session store architecture`
   - 架构、计划、README 和路线图同步。
