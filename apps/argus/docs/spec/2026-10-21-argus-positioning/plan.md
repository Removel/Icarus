# Argus 第一阶段闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 mock 代替 agent，完成 QQ 收集、观测、判断、递交、显式解决的持续运行闭环，并保留以后替换真实处理端的位置。

**Architecture:** 单 FastAPI 进程，按业务模块分包、模块内部经典 Python 后端分层。QQ 只负责来源协议、接收和事实，common 负责统一存储、判断、叫醒与调度；两张 PostgreSQL 业务表。每次启动生成 boot_id，不恢复历史工作；同 chat 串行、不同 chat 可并行，accepted 后必须获得终态或超时失败。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic、SQLAlchemy 2 async + asyncpg、PostgreSQL 17、PyYAML、typesafe-sdk==0.7.2、websockets、pytest + pytest-asyncio + httpx。

**Spec:** [design.md](design.md)，2026-10-21 修订的目标设计，不是已实现架构。

## Global Constraints

- 单 FastAPI 进程，不拆子容器/子运行时；统一遵循现有 Icarus 应用级生命周期管理。
- 来源之间不互相 import；common 不 import 任何具体来源；只有装配入口了解 QQ 实现。
- 不为目录对称创建空层。QQ 没有专属业务表，因此不创建 QQ 的 models/repositories。
- 不额外建立重复的 entity/contracts/dto 类型体系。ORM models 不直接作为 HTTP 响应或外部调用参数。
- 不使用决策图，不建设回放系统。旧 decision_graph 源码保留，调整其测试对待删业务层的依赖。
- PostgreSQL 为唯一运行时和集成测试数据库。两张业务表 observed_items、wake_requests，外加 schema_version。
- 使用与现有 PG 部署同款 `pgvector/pgvector:pg17` 镜像；Argus 使用独立数据库/账号/表。
- 正式 PG 的建库、账号和部署由用户在本地开发完成后手动执行；不访问或修改 mem0/rela-mem 的真实数据库。
- 连接串只通过 ARGUS_DATABASE_URL 提供；只允许 loopback 绑定；配置与普通测试不泄露真实密钥。
- 只调度本次启动的消息，不恢复上一次遗留任务，不主动拉取历史，不使用旧批次作为判断上文。
- 有请求正在递交或处理时，新消息继续保存，不追加当前请求，也不创建第二个并行请求。
- 普通私聊即为明确寻址，不要求叫名字，也不调用 Jev 判断是否值得回应。
- 第一阶段不写 verified、不执行真实动作、不注册假的成功 Gateway RPC；mock 结果显式 simulated=true。
- 不自动创建分支、提交、推送或删除用户数据。每个任务完成一个可审查检查点；仅在另获授权后提交。

## Review Focus

- 重启前 accepted 与新启动同 chat 并存：旧任务可查询但不活跃，不占锁，不重投；旧消息重投仍全库去重（Task 2、6、7）。
- 模型等待期间消息被修改/撤回：事务重查版本，过期选择不写 ignore/wake，下一轮重算（Task 2、4、6）。
- 超长队首、预算外的 @、敏感词藏在被截断部分：完整检查后摘录，只推进选中 ID，强信号不丢（Task 4、6）。
- 接收超时但实际已接收、重复/跨批次解决结果：同 wake_id 有限重试，匹配完整 ID 集合、原子终态、结果幂等（Task 5、6）。
- 一个群 accepted/阻塞时其他群应可处理：每 chat 互斥、有限并发，不在全局扫描串行等待 Jev/agent（Task 6、7）。

---

## 1. 文件结构与职责

以下路径以 `apps/argus/` 为根，表中每个新 Python 包同时创建空 `__init__.py`；不增加导入副作用。

| 文件 | 职责 |
|---|---|
| `src/main.py`（修改） | 保留模块入口；FastAPI 装配、lifespan、日志和 host/port CLI。 |
| `src/core/config.py` | Argus/QQ/Jev/mock 配置、环境加载和校验。 |
| `src/core/database.py` | PG 生命周期、schema 版本、事务和探测。 |
| `src/common/schemas/observation.py` | ContentPart、观测草稿/读取类型、预算后的 SelectedItem。 |
| `src/common/schemas/judgement.py` | Rule/RuleSet、忽略项、JudgementOutcome。 |
| `src/common/schemas/wake.py` | WakePayload/Receipt/Outcome/View、AgentClient 协议。 |
| `src/common/models/observation.py` | observed_items ORM 映射。 |
| `src/common/models/wake.py` | wake_requests ORM 映射。 |
| `src/common/repositories/observation_repository.py` | 两表持久化及跨表原子状态变更。 |
| `src/common/services/rule_service.py` | YAML 加载/校验、机械规则、语义候选。 |
| `src/common/services/judgement_service.py` | 门槛、预算、事实构造、必要的 Jev 选择。 |
| `src/common/services/wake_service.py` | 递交重试、接收、轮询解决、超时和终态。 |
| `src/common/clients/jev_client.py` | 原样迁移旧 JevClient 的有界调用与 typed 校验。 |
| `src/common/clients/gateway_client.py` | 迁移旧 Gateway 底层传输，不包含 submit/steer 业务、不装配。 |
| `src/common/clients/mock_agent_client.py` | 按 wake_id 幂等接收、延迟解决及测试故障注入。 |
| `src/common/tasks/observation_scheduler.py` | 当前 boot 扫描、去抖、强信号、chat 互斥和并发。 |
| `src/common/api/health.py` | 共用健康逻辑，/health 和 /api/argus/health。 |
| `src/common/api/observations.py` | 观测详情、当前未处理列表。 |
| `src/common/api/wakes.py` | 请求详情、有限分页、历史与 active 区分。 |
| `src/common/api/router.py` | 汇总统一路由。 |
| `src/surfaces/base.py` | 仅 SourceSurface 事实构造接口。 |
| `src/surfaces/qq/schemas/onebot.py` | QQ 平台输入结构，不进入 common。 |
| `src/surfaces/qq/services/parser_service.py` | OneBot 校验和规范化，含撤回。 |
| `src/surfaces/qq/services/fact_service.py` | QQ 事实、名称归一化、强信号。 |
| `src/surfaces/qq/services/observation_service.py` | 解析、入库、通知调度，不等待模型。 |
| `src/surfaces/qq/api/onebot.py` | HTTP 流式限长、验签、账号校验和错误响应。 |
| `db/schema.sql` | 唯一 DDL 来源，两个 ORM 映射必须与之吻合。 |
| `rules/qq.yaml` | 私聊、群 @、群语义规则及默认沉默。 |
| `compose.dev.yaml` | 仅本地开发 PG，同款镜像、独立库/账号和本地端口；不改共享 PG。 |
| `scripts/test.sh`（修改） | 参数透传、隔离 PG 测试实例、可靠清理、应用 compile。 |
| `scripts/start.sh`（核对） | 继续执行 apps.argus.src.main 并透传参数；需要时最小修改。 |
| `settings.json`、`settings.json.example`（修改） | 新配置结构，两者一起迁移，不写密钥。 |
| `requirements.txt`、`requirements-dev.txt`、`pytest.ini` | PG/YAML/异步测试依赖和模式。 |
| `README.md`（修改） | 本地开发、mock 限制、重启丢弃待办语义、未来人工部署。 |

测试镜像上述目录。共享工厂在 `test/factories.py`，环境/PG 隔离在 `test/conftest.py`，
装配集成测试在 `test/app/`，架构 import 约束在 `test/test_layer_dependencies.py`。

仓库级最小接线：`icarus.toml`、`scripts/tests/test_icarus_control.py`、
`scripts/tests/test_process_runtime.py`。不修改 Gateway 代码/测试/脚本，不扩展控制器 DSN
生成能力，不修改共享 PG Compose。根测试仅验证现有应用生命周期接线，不产生新的根设计文档。

## 2. 固定数据和接口决策

### 2.1 schema 与类型

数据库 DDL 由 Task 1 落地；同名业务 schema 由 Task 2 落地。时间均为 UTC timestamptz，ID 为 UUID。

- `schema_version(version integer PRIMARY KEY, applied_at timestamptz)`；当前版本 1，表中只能有一行。
- `observed_items`：`id, boot_id, source, chat_id, message_id, revision, fingerprint, deleted,
  is_self, strong_signal, sender_id, sender_name, sent_at, observed_at, content jsonb, locator jsonb,
  detail jsonb, status, status_reason, decision_detail jsonb, wake_id nullable FK`。
- `wake_requests`：`id, boot_id, chat_id, status, reason, rule_id nullable, payload jsonb,
  decision_detail jsonb, outcome jsonb nullable, attempt_count, next_attempt_at nullable,
  accepted_at nullable, deadline_at nullable, error_type nullable, created_at, updated_at`。
- 观测状态：`new / delivered / processed / verified / ignored / failed`。
- 请求状态：`waiting / sent / accepted / resolved / no_action / failed`；活跃只含前三种。
- 唯一约束：观测 `(source,chat_id,message_id,revision)`、`(source,chat_id,message_id,fingerprint)`；
  请求部分唯一索引 `(boot_id,chat_id) WHERE status IN ('waiting','sent','accepted')`。
- 索引：观测 `(boot_id,chat_id,observed_at,id)` 的 new 未关联记录、消息最新 revision、wake_id；
  请求 `(boot_id,status,next_attempt_at)`。ORM 不另行自动建表，测试比较实际约束/列映射。

Pydantic 业务 schema 使用 `extra='forbid'`、`frozen=True`；content 为 tuple，拒绝非有限数值。
只保留 ORM 映射和这些公共类型两种模型，不再包一套相同的 entity。

- `ContentPart(kind: Literal['text','image','other'], value: str)`。
- `ObservedItemDraft`：上表观测事实字段，**没有** id/boot_id/revision/fingerprint/status/wake_id；
  observed_at 由接收端服务器产生，不能由平台 event 指定。
- `ObservedItem`：完整观测读取类型，来自 ORM 显式映射。
- `SelectedItem(item: ObservedItem, excerpt: str, truncated: bool)`：保留完整身份，所有下游只使用 excerpt。
- `PendingWindow(first_observed_at:datetime,last_observed_at:datetime,has_strong_signal:bool)`：
  对全体可调度最新消息统计，不能只看预算内前几条。
- `IgnoredSelection(item_ids: tuple[UUID,...], reason: str)`。
- `JudgementOutcome(selected: tuple[SelectedItem,...], ignored: tuple[IgnoredSelection,...],
  decision: Literal['wake','ignore'], reason: str, rule_id: str|None, decision_detail: dict)`。
  selected 和 ignored 无交集；selected 是规则实际判断的预算内消息，预算外 ID 不出现在两者中。
- `Rule(id, description, priority=0, semantic=False, when, decision, reason)`；`RuleSet(source,
  version:int, digest:str, mechanical:tuple[Rule,...], semantic:tuple[Rule,...], default_reason:str)`。
- `WakeItem(item_id: UUID, source:str, chat_id:str, message_id:str, revision:int, locator:dict,
  excerpt:str, truncated:bool)`。
- `WakePayload(wake_id:UUID, boot_id:UUID, chat_id:str, reason:str, rule_id:str|None, items:tuple[WakeItem,...])`。
- `WakeReceipt(accepted:bool, retryable:bool=False, error_type:str|None=None)`。
- `WakeOutcome(wake_id:UUID, item_ids:tuple[UUID,...], status:Literal['resolved','no_action','failed'],
  simulated:bool, reason:str)`；ID 不可重复，必须完整匹配 payload。
- `WakeView`：请求完整字段；HTTP 响应另补 `active:bool = boot_id==current_boot AND status 为活跃状态`。
- `AgentClient(Protocol)`：`async submit(payload:WakePayload)->WakeReceipt`、
  `async get_outcome(wake_id:UUID)->WakeOutcome|None`、`async release(wake_id:UUID)->None`、`async aclose()->None`。

### 2.2 配置默认值

`ArgusConfig` 包含 `host='127.0.0.1', port=8766, log_level='INFO', scan_interval_seconds=1.0,
quiet_seconds=5.0, max_debounce_seconds=30.0, max_items_per_wake=20, max_chars_per_wake=20000,
recent_context_items=10, recent_context_chars=4000, max_concurrent_chats=8,
submit_timeout_seconds=5.0, max_submit_attempts=3, retry_delays_seconds=(1.0,2.0),
outcome_timeout_seconds=60.0, sensitive_keywords=(), default_permission_level=2,
permission_overrides={}, jev, qq, mock`。

- `QQConfig(enabled=False, bot_id=None, bot_names=(), max_body_bytes=1048576)`。
- `JevConfig(enabled=False, base_url='https://openrouter.ai/api', model='typesafe/jev-1.13',
  timeout_seconds=5.0, attempts=3)`；关闭时机械规则照常工作，语义候选静默为 jev_disabled。
- `MockConfig(completion_delay_seconds=0.05)`；第一阶段只装配 mock，不提供误导性的真实 agent 模式。
- `ARGUS_DATABASE_URL` 必填，接受 `postgresql+asyncpg://...`，拒绝 SQLite；脱敏后报错。
- QQ 开启要求 `ARGUS_QQ_REPORT_SECRET` 和合法 bot_id；Jev 开启要求 `ARGUS_JEV_API_KEY`。
- `load_config(*, settings_path:Path|None=None)->ArgusConfig` 保留仓库环境加载，进程环境优先。
- CLI `--host`、`--port` 覆盖配置且再次校验。host 仅 127.0.0.1；本阶段不要求 workspace/session/Gateway 地址。
- 数量/时间为正且有限；permission 为 0..5，0 禁止递交；重试间隔数必须等于 attempts-1。

---

### Task 1: PG、配置与隔离测试基线

**Files:**
- Create: `apps/argus/src/core/{__init__.py,config.py,database.py}`、`apps/argus/db/schema.sql`、`apps/argus/compose.dev.yaml`、`apps/argus/pytest.ini`。
- Modify: `apps/argus/requirements.txt`、`requirements-dev.txt`、`scripts/test.sh`、`test/conftest.py`。
- Test: `apps/argus/test/core/test_config.py`、`apps/argus/test/core/test_database.py`、`apps/argus/test/test_scripts.py`。

**Interfaces:**
- Consumes: 现有 `packages.runtime_environment.load_icarus_environment`。
- Produces: §2.2 配置类型/loader；`SCHEMA_VERSION=1`、`SchemaVersionError`、
  `Database(dsn:str, *, schema:str='public')`，`async start()->None`、`async close()->None`、
  `session()->AsyncContextManager[AsyncSession]`、`begin()->AsyncContextManager[AsyncSession]`、
  `async is_available()->bool`。dsn 不出现在 repr/log 中。
- Fixtures: 隔离配置、屏蔽真实密钥/环境加载；`database` 为每测试唯一 schema 的 PG。

- [ ] **Step 1: 写失败测试。** `test_config_uses_explicit_fixture_not_real_settings`、
  `test_sqlite_or_remote_bind_is_rejected`、`test_retry_budget_is_validated`；
  `test_start_creates_exactly_two_business_tables` 断言 observed_items/wake_requests/schema_version；
  `test_unknown_version_closes_partial_engine` 断言版本 99 拒绝且连接释放；
  `test_test_script_forwards_args_and_cleans_owned_container` 用假的 docker/python 验证透传与退出清理。

  ```python
  async def test_start_creates_exactly_two_business_tables(database):
      async with database.session() as session:
          names = set((await session.execute(text(
              "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
          ))).scalars())
      assert names == {"observed_items", "wake_requests", "schema_version"}
  ```

- [ ] **Step 2: 聚焦执行确认 RED。** `apps/argus/scripts/test.sh apps/argus/test/core apps/argus/test/test_scripts.py -q`。
  首次旧脚本不透传，先以 app 私有 Python 执行该 pytest 路径确认缺模块失败；不将脚本问题混作业务 RED。
- [ ] **Step 3: 实现配置、DDL、Database。** 空库探测通过 SQLAlchemy inspect/to_regclass，
  不能先 SELECT 不存在的 schema_version 再在失败事务继续建表。DDL 事务中执行，版本错误释放连接。
  显式 schema 使用服务端 connect_args 的 search_path，不能把 libpq options 直接塞 asyncpg URL；
  fixture schema 名只允许内部生成 `test_<hex>`。探测 SELECT 1 超时 0.25 秒返回 False。
- [ ] **Step 4: 实现测试/开发 PG。** 依赖添加 asyncpg、PyYAML、pytest-asyncio；暂保留 aiosqlite
  直到旧测试退出。pytest 设置 asyncio_mode=auto。test.sh 无测试 DSN 时启动自己的临时
  `pgvector/pgvector:pg17`（独立 argus_test 库/账号、127.0.0.1 随机端口），最多等待 30 秒，
  trap 只删除本脚本创建的容器；已有 ARGUS_TEST_DATABASE_URL 时只建/删唯一 test schema，
  明确禁止把生产 DSN 当测试 DSN。直接 pytest 缺测试 DSN 使 DB fixture 报配置错误，不 skip 成假绿。
  compose.dev.yaml 使用独立项目 `argus-dev`、argus 库/账号、默认 127.0.0.1:55433、独立卷，
  密码来自 `ARGUS_DEV_POSTGRES_PASSWORD`，不写默认生产密码、不自动 down -v。
- [ ] **Step 5: 同命令确认 PASS。** conftest 迁移时保留旧测试所需的 config_loader 隔离至 Task 8，
  新 fixture 同时隔离新 loader，测试不读取真实 settings/.env。记录结果，不自动提交。

### Task 2: 观测与请求类型、ORM、原子 repository

**Files:**
- Create: `apps/argus/src/common/{__init__.py,schemas/__init__.py,models/__init__.py,repositories/__init__.py}`。
- Create: `apps/argus/src/common/schemas/{observation.py,judgement.py,wake.py}`、`apps/argus/src/common/models/{observation.py,wake.py}`、`apps/argus/src/common/repositories/observation_repository.py`。
- Create: `apps/argus/test/factories.py`；Test: `apps/argus/test/common/repositories/test_observation_repository.py`、`apps/argus/test/common/models/test_schema.py`。

**Interfaces:**
- Consumes: Task 1 Database；Produces: §2.1 全部类型和 `ObservationRepository(database:Database, *, boot_id:UUID)`。
- 读取：`async get_item(item_id:UUID)->ObservedItem`、`async latest_open_items(chat_id:str, *, limit:int)->tuple[ObservedItem,...]`、
  `async recent_context(chat_id:str, *, before:tuple[datetime,UUID], limit:int)->tuple[ObservedItem,...]`、
  `async chats_to_scan()->tuple[str,...]`、`async pending_window(chat_id:str)->PendingWindow|None`、
  `async active_wake(chat_id:str)->WakeView|None`、`async get_wake(wake_id:UUID)->WakeView`、
  `async list_wakes(*,status:str|None=None,chat_id:str|None=None,limit:int=50,offset:int=0)->tuple[WakeView,...]`、
  `async wake_items(wake_id:UUID)->tuple[ObservedItem,...]`。
- 观测事务：`async upsert_revision(draft:ObservedItemDraft)->tuple[ObservedItem,bool]`、
  `async commit_judgement(chat_id:str,outcome:JudgementOutcome)->WakeView|None`；失效版本抛 `StaleSelection`。
- 请求事务：`async begin_attempt(wake_id:UUID, *, now:datetime)->WakeView`、
  `async schedule_retry(wake_id:UUID, *, error_type:str,next_attempt_at:datetime)->None`、
  `async mark_accepted(wake_id:UUID, *, now:datetime,deadline_at:datetime)->None`、
  `async record_poll_error(wake_id:UUID, *, error_type:str)->None`、
  `async finish_wake(wake_id:UUID, *, status:Literal['resolved','no_action','failed'],outcome:WakeOutcome|None,error_type:str|None=None)->None`。
- 所有调度读取/变更限定 current boot；ID 详情读取可跨 boot 但不产生变更。NotFound、StaleSelection、
  ActiveWakeExists 明确作为业务异常，原子写入回滚不能留空 wake。

- [ ] **Step 1: 写失败测试。** `test_concurrent_same_message_inserts_once` 用 asyncio.gather 并发 20 次，
  断言同 ID、仅一次 inserted；`test_edit_appends_and_replay_does_not_revert` 验证 A/B/重投 A；
  `test_recall_before_message_does_not_resurrect` 验证墓碑；`test_new_boot_ignores_old_work_but_deduplicates_globally`；
  `test_commit_rejects_revision_changed_during_judgement`；`test_finish_is_atomic_idempotent_and_chat_local`。

  ```python
  async def test_concurrent_same_message_inserts_once(repository, draft_factory):
      value = draft_factory(message_id="789", text="hello")
      results = await asyncio.gather(*(repository.upsert_revision(value) for _ in range(20)))
      assert len({item.id for item, _ in results}) == 1
      assert sum(inserted for _, inserted in results) == 1
      assert {item.revision for item, _ in results} == {1}
  ```

- [ ] **Step 2: RED。** `apps/argus/scripts/test.sh apps/argus/test/common/repositories apps/argus/test/common/models -q`。
- [ ] **Step 3: 实现 ORM 和 revision。** fingerprint 为规范化 content+deleted 的 SHA256，
  在同消息的 `pg_advisory_xact_lock(hashtextextended(canonical_identity,0))` 事务中检查全历史去重、
  检查墓碑和分配 revision；不把 sender_name 变化当内容编辑。并发 hash 碰撞只额外串行，不影响正确性。
  遇已撤回后的非撤回上报返回墓碑和 False。保留原始发生时间与接收时间区分。
- [ ] **Step 4: 实现判断提交与状态变更。** commit 在同一事务按稳定顺序取得所有消息身份锁，
  校验 selected/ignored 全属本 boot/chat、无交集、是最新 revision 且 new 未关联；任一失效整体回滚。
  ignored 写原因，decision=ignore 的 selected 写 ignored，decision=wake 的 selected 原子关联一个
  waiting 请求，payload 固化短摘录。空 selected 不创建 wake。部分唯一冲突转 ActiveWakeExists。
  accepted 才将关联记录置 delivered；resolved/no_action 置 processed，failed 置 failed；保留 wake_id。
  finish 只接受 active→terminal，同结果重复为 no-op，冲突终态拒绝；verified 从不写入。
- [ ] **Step 5: 实现读取与 PASS。** latest_open 先取每消息全库最新 revision，再过滤 boot/new/
  deleted/wake；recent_context 同 boot/chat、稳定时间边界、最新且非撤回；pending_window 对所有
  可调度最新消息计算 min/max，不对前 20 条误算安静窗口。chats_to_scan 同时包含当前待处理消息和
  当前活跃 wake；旧状态不占当前唯一索引。工厂暴露 draft_factory、item_factory、payload_factory、
  outcome_factory，后续测试复用。重跑 Step 2，记录结果，不自动提交。

### Task 3: QQ 来源协议、解析和事实

**Files:**
- Create: `apps/argus/src/surfaces/{__init__.py,base.py}`、`apps/argus/src/surfaces/qq/{__init__.py,schemas/__init__.py,services/__init__.py}`。
- Create: `apps/argus/src/surfaces/qq/schemas/onebot.py`、`apps/argus/src/surfaces/qq/services/{parser_service.py,fact_service.py}`。
- Test: `apps/argus/test/surfaces/qq/services/test_parser_service.py`、`apps/argus/test/surfaces/qq/services/test_fact_service.py`。

**Interfaces:**
- Consumes: Task 2 types；Produces: `QQParser(bot_id:str,bot_names:Sequence[str]=())`，
  `parse(raw:Mapping[str,object])->ObservedItemDraft|None`；`QQFactService.build_facts(items:Sequence[SelectedItem], *,permission_level:int)->dict[str,object]`。
- `SourceSurface(Protocol)` 只有 `source:str`、`known_facts:frozenset[str]` 和上述 build_facts；
  QQFactService 实现它，无额外 surface 装配包装层。
- facts：source/chat_id/scope_kind/item_count/mentions_bot/is_self_only/text/sender_ids/sender_names/
  permission_level/newest_sent_at/oldest_observed_at；common 只消费已声明 facts。

- [ ] **Step 1: 写/迁移失败测试。** 将旧 `test/handler/test_qq_handler.py` 解析参数化用例迁入新路径，
  补 `test_recall_creates_deleted_draft`、`test_private_self_message_uses_target_id`、
  `test_private_is_strong_without_bot_name`、`test_fullwidth_and_casefold_alias_matches`；
  断言 text facts 只使用 SelectedItem.excerpt，不重新读取长原文。

  ```python
  def test_private_is_strong_without_bot_name():
      draft = QQParser(bot_id="123").parse({
          "self_id": 123, "post_type": "message", "message_type": "private",
          "message_id": -789, "user_id": 456, "sender": {"nickname": "peer"},
          "time": 1700000000, "message": "帮我整理笔记",
      })
      assert draft.chat_id == "qq:123:private:456"
      assert draft.strong_signal is True
      assert draft.is_self is False
      assert draft.message_id == "-789"
  ```

- [ ] **Step 2: RED。** `apps/argus/scripts/test.sh apps/argus/test/surfaces/qq/services -q`。
- [ ] **Step 3: 实现。** 迁移旧 ID/时间/内容校验（负数非零 message_id、无 CQ 的纯文本、数组、
  图片顺序、未知段显式 other、bool/NaN/越界时间拒绝）；self_id 必须匹配配置。
  支持 message/message_sent 和 group_recall/friend_recall；其他已知事件 None。
  chat_id=`qq:{bot_id}:{kind}:{external_id}`；自发私聊取 target_id；group 取 group_id。
  locator 保存 account_id/chat_kind/external_id/message_id/time；detail 只存 QQ 必要附加事实。
  self/strong_signal 上提为通用观测字段；强信号为非自发私聊或 @/NFKC+casefold 名称匹配。
- [ ] **Step 4: 同命令 PASS。** 不改旧 parser，待 Task 8 清理；记录迁移能力清单，不自动提交。

### Task 4: 规则、Jev 和预算一致的判断

**Files:**
- Create: `apps/argus/src/common/services/{__init__.py,rule_service.py,judgement_service.py}`、`apps/argus/src/common/clients/{__init__.py,jev_client.py}`、`apps/argus/rules/qq.yaml`。
- Test: `apps/argus/test/common/services/{test_rule_service.py,test_judgement_service.py}`、`apps/argus/test/common/clients/{test_jev_client.py,test_jev_typed.py}`。

**Interfaces:**
- Consumes: Rule/RuleSet、SelectedItem、JudgementOutcome、SourceSurface、迁移 JevClient。
- Produces: `load_rule_set(path:Path, *,known_facts:set[str])->RuleSet`、
  `evaluate_mechanical(rules:RuleSet,facts:Mapping[str,object])->Rule|None`、
  `candidate_semantic_rules(rules:RuleSet,facts:Mapping[str,object])->tuple[Rule,...]`、`RuleValidationError`。
- `JudgementService(rule_set:RuleSet,surface:SourceSurface,jev:JevClient|None, *,sensitive_keywords:Sequence[str],
  max_items:int=20,max_chars:int=20000,recent_chars:int=4000)`；
  `async judge(items:Sequence[ObservedItem], *,permission_level:int,recent_context:Sequence[ObservedItem]=())->JudgementOutcome`。

- [ ] **Step 1: 迁移底层 Jev 测试，写规则/预算失败测试。** 明确测试高优先级、文件顺序、未知事实/
  操作符/重复 ID/保留 ID none、私聊无 Jev、群 @ 无 Jev、语义合法选项、失败默认沉默。
  `test_long_first_item_uses_excerpt_and_does_not_mark_tail`：单条 25000 字符在 20000 预算内选中、
  truncated=True、下一条不在 selected/ignored；`test_sensitive_tail_is_checked_before_excerpt`；
  `test_sensitive_recent_is_removed_and_context_has_own_budget`；`test_self_and_sensitive_items_do_not_drop_valid_mention`。

  ```python
  async def test_long_first_item_uses_excerpt_and_does_not_mark_tail(judgement, item_factory):
      head = item_factory(text="a" * 25000)
      tail = item_factory(text="@Icarus 看看报错", strong_signal=True)
      result = await judgement.judge([head, tail], permission_level=2)
      assert [value.item.id for value in result.selected] == [head.id]
      assert len(result.selected[0].excerpt) == 20000
      assert result.selected[0].truncated is True
      assert all(tail.id not in value.item_ids for value in result.ignored)
  ```

- [ ] **Step 2: RED。** `apps/argus/scripts/test.sh apps/argus/test/common/services/test_rule_service.py apps/argus/test/common/services/test_judgement_service.py apps/argus/test/common/clients/test_jev_client.py apps/argus/test/common/clients/test_jev_typed.py -q`。
- [ ] **Step 3: 实现规则。** yaml.safe_load、extra forbid，version 正整数、source 匹配、默认仅 ignore；
  操作符 all/any/not、eq/ne/in/contains_any/contains_all/gte/lte/exists，简写 fact:value 为 eq。
  mechanical 按优先级降序/原文件顺序，semantic 不混入机械评估；缺失事实只由 exists 判断，
  其他比较缺失返回 False，非法比较类型在载入/评估时明确拒绝而不当 truthy。
  默认规则 private_message(priority=200,scope_kind=private)、mention_bot(priority=100)、
  genuine_question(semantic,group,wake)、casual_chat(semantic,group,ignore)，none 为保留选项。
- [ ] **Step 4: 实现唯一预算选择。** 扫描传入前 max_items 条，逐条将自消息/敏感消息放 ignored；
  合法项按原顺序，第一项超限时摘录至 max_chars 并停止，后续完整文本放不下就留待下轮。
  当没有 selected 可单独提交 ignored；禁止因忽略项而操作未扫描的尾部。
  删除/非new/已关联输入不选中，事务仍会重查版本。全文先过滤，NFKC+casefold 关键词匹配。
  上文先排除撤回/自消息/敏感，按 recent_chars 独立摘录；保留规范化 SelectedItem，facts/模型/
  WakePayload 同一份 excerpt。每条截断必须标记，图片只保留短占位，不下载附件。
- [ ] **Step 5: 迁移 JevClient 并实现调用。** 客户端原样保留 timeout/单层重试/typed 校验/aclose。
  判断内部构造 Choice、固定问题 intent，候选 criteria 加 none；state 只放 scope_kind、
  items[{text,sent_at}]、recent[{text}]，不放显示名/chat_id/locator/账号 ID。
  保存规则 version/digest、选择/confidence/probabilities/error_type，不存请求密钥或原文副本。
  Jev 关闭/失败/none 按明确原因 ignore；CancelledError 继续传播，不吞停机信号。
- [ ] **Step 6: 同命令 PASS。** 确认预算外 @ 未进入 outcome、后续仍可调度，不自动提交。

### Task 5: mock 接收/解决、稳定身份递交与终态

**Files:**
- Create: `apps/argus/src/common/clients/{mock_agent_client.py,gateway_client.py}`、`apps/argus/src/common/services/wake_service.py`。
- Test: `apps/argus/test/common/clients/{test_mock_agent_client.py,test_gateway_client.py}`、`apps/argus/test/common/services/test_wake_service.py`。

**Interfaces:**
- Consumes: AgentClient、WakePayload/Receipt/Outcome/View、repository 请求事务。
- Produces: `MockAgentClient(*,completion_delay_seconds:float=0.05,clock:Callable[[],datetime]=utc_now)`
  实现 AgentClient；测试通过注入同接口的 controlled_agent（asyncio.Event）覆盖任意时序，不增加生产故障配置。
- `WakeService(repository:ObservationRepository,agent:AgentClient, *,submit_timeout_seconds:float,
  max_submit_attempts:int,retry_delays_seconds:Sequence[float],outcome_timeout_seconds:float,
  clock:Callable[[],datetime])`；`async advance(wake_id:UUID)->None` 每次只推进一个可执行步骤。
- gateway_client 只迁移 IcarusClient 的 connect/close/call、读循环、错误类型；本阶段没有真实 wake adapter。

- [ ] **Step 1: 写失败测试。** `test_acceptance_is_not_completion`、`test_mock_resolves_after_delay`、
  `test_timeout_after_acceptance_retries_same_id_once_only`（fake 首次接收后丢响应）、
  `test_three_attempts_fail_and_release_chat`、`test_accepted_deadline_fails_even_when_poll_errors`、
  `test_duplicate_outcome_is_noop`、`test_foreign_or_partial_ids_fail_without_touching_other_items`、
  `test_simulated_resolution_never_marks_verified`。迁移旧 IcarusClient transport 测试，删除 session 业务断言。

  ```python
  async def test_mock_resolves_after_delay(clock, payload_factory):
      payload = payload_factory()
      agent = MockAgentClient(completion_delay_seconds=0.05, clock=clock.now)
      assert (await agent.submit(payload)).accepted is True
      assert await agent.get_outcome(payload.wake_id) is None
      clock.advance(0.05)
      outcome = await agent.get_outcome(payload.wake_id)
      assert outcome.status == "resolved" and outcome.simulated is True
      assert outcome.item_ids == tuple(item.item_id for item in payload.items)
      assert await agent.get_outcome(payload.wake_id) == outcome
  ```

- [ ] **Step 2: RED。** `apps/argus/scripts/test.sh apps/argus/test/common/clients/test_mock_agent_client.py apps/argus/test/common/clients/test_gateway_client.py apps/argus/test/common/services/test_wake_service.py -q`。
- [ ] **Step 3: 实现 mock。** submit 按 wake_id 缓存规范化 payload；相同重投返回同一接收事实，
  同 ID 不同 payload 拒绝。get_outcome 到时间返回完整 ID resolved、simulated=True；此前 None。
  不为每次 submit 创建新任务，不读真实模型或 Gateway。aclose 释放内存等待，不跨启动恢复。
- [ ] **Step 4: 实现 WakeService。** waiting 到 next_attempt_at 才 begin_attempt→sent→有界 submit；
  接收确认写 accepted、accepted_at、固定 deadline；transport/timeout 未知按同 ID 和固化 payload 重试，
  1/2 秒后重投，第三次失败终止；明确拒绝不重试。accepted 查询 outcome 使用有界调用，
  None/临时查询错到固定 deadline 即 failed；错误不能刷新 deadline。每轮也检查当前 boot 的
  sent 超时，保留同一 ID 调整至 retry/failed，以免 DB 回执写入失败永久卡住。不扫描旧 boot。
  校验结果 ID、重复、终态、simulated 标识；默认 mock 模式拒绝 simulated=False。无效结果终止
  当前请求，绝不更新不相关记录。DB 暂时写入失败由下轮重试同一步，不能伪造完成。
- [ ] **Step 5: 迁移传输并 PASS。** gateway_client 不 import 旧 DeliveryOutcome；测试验证原有
  request-id 关联、断线错误、超时/重连/关闭。没有真实网络连接或 Gateway 代码改动。不自动提交。

### Task 6: 当前启动调度、群内串行与跨 chat 并发

**Files:**
- Create: `apps/argus/src/common/tasks/{__init__.py,observation_scheduler.py}`。
- Test: `apps/argus/test/common/tasks/test_observation_scheduler.py`。

**Interfaces:**
- Consumes: repo.boot_id、pending_window/chats_to_scan/commit_judgement、JudgementService、WakeService。
- Produces: `ObservationScheduler(repository:ObservationRepository,judgement:JudgementService,wakes:WakeService,
  *,permission_for:Callable[[str],int],quiet_seconds:float,max_debounce_seconds:float,
  scan_interval_seconds:float,max_concurrent_chats:int,max_items:int,recent_items:int,clock:Callable[[],datetime])`；
  `notify(chat_id:str, *,immediate:bool=False)->None`、`async scan_once()->None`、
  `async process_chat(chat_id:str)->None`、`async run()->None`、`async aclose()->None`。
- 测试 `scan_once` 只创建/回收有界 worker；以 controlled_agent/Event 等待可观测状态，不用任意 sleep。

- [ ] **Step 1: 写失败测试。** `test_quiet_five_seconds_and_max_thirty_seconds`；
  `test_private_and_mention_skip_quiet_window`；`test_new_mention_during_accepted_waits_then_wakes_again`；
  `test_idle_chat_finishes_while_other_chat_waits`；`test_newest_beyond_twenty_resets_quiet_window`；
  `test_old_boot_accepted_never_blocks_current_chat`；`test_stale_selected_revision_is_rejudged_not_delivered`；
  `test_disabled_source_creates_no_workers`（装配控制，Task 7 追加整体验证）。

  ```python
  async def test_new_mention_during_accepted_waits_then_wakes_again(flow):
      first = await flow.receive(chat="group-a", text="@Icarus 总结文章")
      await flow.until_accepted(first)
      second = await flow.receive(chat="group-a", text="@Icarus 看看报错")
      await flow.scan_until_idle()
      assert len(flow.agent.submissions) == 1
      flow.agent.resolve(first)
      await flow.until_accepted(second)
      assert len(flow.agent.submissions) == 2
      assert flow.agent.submissions[0].wake_id != flow.agent.submissions[1].wake_id
  ```

  本任务在同测试文件建立 flow fixture：包装实际 repo/judge/scheduler 和 controlled_agent；
  receive 固定 chat 映射并生成合法 draft；until_accepted 用事件/有界轮询推进 scan_once，
  scan_until_idle 等本轮 worker 完成，不等待 accepted 自动解决；resolve 显式开放 mock 结果。

- [ ] **Step 2: RED。** `apps/argus/scripts/test.sh apps/argus/test/common/tasks -q`。
- [ ] **Step 3: 实现扫描与有限任务。** 每 chat 仅一个 worker/lock，最多 8 个正在运行，不无限创建
  等信号量的 task；扫描本 boot 待办/活跃请求并按稳定游标公平轮转，不让最早几个群饿死尾部。
  全局扫描不 await 每个模型调用；worker 的异常逐 chat 记录，其他 chat 继续。完成 task 回收。
  notify 使用去重 chat 集合+Event 而不是无界重复队列，强信号可从持久字段重建。
- [ ] **Step 4: 实现单 chat 流程。** 有活跃请求只调用 advance，不再判断新消息；无活跃请求时
  pending_window 对全体未关联最新记录算去抖，强信号按待处理窗口存在性触发；取前 max_items，
  recent 以稳定 (observed_at,id) 边界查本 boot 上文，judge 后 commit。StaleSelection 仅下轮重算。
  请求终态后通知该 chat 下一轮检查；强信号不能在预算未消费或 accepted 时被清除。
  闲聊即使在等待期间积累也重新判断，不自动递交。所有重试保留已有 wake，不再次调 Jev。
- [ ] **Step 5: 生命周期/停机与 PASS。** run 仅启动正常扫描，不恢复旧工作；
  aclose 取消且 await 全部本进程 worker，CancelledError 向上传递；内存集合回收不无限保留 chat。
  单次 PG 故障保持循环可取消，不用无间隔热重试。重跑 Step 2，不自动提交。

### Task 7: OneBot HTTP、统一读取和应用装配

**Files:**
- Create: `apps/argus/src/surfaces/qq/api/{__init__.py,onebot.py}`、`apps/argus/src/surfaces/qq/services/observation_service.py`。
- Create: `apps/argus/src/common/api/{__init__.py,router.py,health.py,observations.py,wakes.py}`。
- Modify: `apps/argus/src/main.py`。
- Test: `apps/argus/test/surfaces/qq/api/test_onebot.py`、`apps/argus/test/surfaces/qq/services/test_observation_service.py`、`apps/argus/test/common/api/{test_health.py,test_observations.py,test_wakes.py}`、`apps/argus/test/app/{test_main.py,test_flow.py}`。

**Interfaces:**
- Consumes: all preceding tasks。
- Produces: `QQObservationService(repository,parser, *,notify:Callable[...,None])`，
  `async receive(raw:Mapping[str,object])->tuple[ObservedItem,bool]|None`；
  `create_qq_router(config:QQConfig,service:QQObservationService, *,secret:str)->APIRouter`。
- Produces: `create_app(config:ArgusConfig|None=None, *,database:Database|None=None,jev:JevClient|None=None,
  agent:AgentClient|None=None,qq_secret:str|None=None)->FastAPI`；`main(argv:list[str]|None=None)->None`。
- `create_router(repository:ObservationRepository, *,database:Database,scheduler:ObservationScheduler|None)->APIRouter`。
- 路由：`POST /api/argus/qq/onebot/events`；`GET /health` 和 `GET /api/argus/health`；
  `GET /api/argus/observed-items/{item_id}`；`GET /api/argus/wake-requests/{wake_id}`；
  `GET /api/argus/wake-requests?status=&chat_id=&limit=50&offset=0`；
  `GET /api/argus/chats/{chat_id}/unhandled?limit=50`。
  列表 limit 1..100、offset>=0；wake 详情返回 `{wake,items}`，wake 含 active；列表以 (created_at,id) 稳定排序。

- [ ] **Step 1: 写失败测试。** 迁移旧验签、原始 body HMAC/compare_digest、账号不符、超长请求，
  坏请求断言零插入。新添 `test_api_does_not_wait_for_blocked_judge`、
  `test_health_aliases_and_dead_scheduler`、`test_history_is_readable_but_not_active`、
  `test_invalid_status_and_pagination_are_422`、`test_startup_failure_closes_owned_resources`。
  test_flow 完整执行“文章请求 accepted→闲聊+新 @ 保存→另一个群完成→文章解决→闲聊不单独叫醒、
  新 @ 进入下一次请求→resolved”；断言模拟标识、没有 verified、没有平台发送。

  ```python
  async def test_health_aliases_and_dead_scheduler(app_client, app):
      for path in ("/health", "/api/argus/health"):
          response = await app_client.get(path)
          assert response.status_code == 200
          assert response.json() == {"status": "ready"}
      app.state.scheduler_task.cancel()
      await asyncio.gather(app.state.scheduler_task, return_exceptions=True)
      assert (await app_client.get("/health")).status_code == 503
  ```

  app/app_client fixtures 使用显式 lifespan 和隔离 PG、QQ enabled、Jev disabled、注入 mock；
  不读取实际 settings，不依赖 ASGITransport 自动启动 lifespan。

- [ ] **Step 2: RED。** `apps/argus/scripts/test.sh apps/argus/test/surfaces/qq/api apps/argus/test/surfaces/qq/services/test_observation_service.py apps/argus/test/common/api apps/argus/test/app -q`。
- [ ] **Step 3: 实现接收。** 只有成功新增且 strong_signal 才 notify(immediate=True)；
  普通消息 notify 不加速去抖。流式读取过程中检查上限，HMAC-SHA1 严格 sha1=40hex，账号 header
  和 body self_id 均匹配；401 签名、403 header 账号、413 大小、422 结构、204 成功、503 停机。
  数据库不可用→503，不把内部 DSN/错误原文带回；非聊天事件确认后 204 不落库。
- [ ] **Step 4: 装配。** lifespan 内生成 boot_id、构造 repo、加载规则、连接 DB，再启动调度；
  QQ 关闭不挂入口、不扫描、不创建 Jev/mock 处理端；DB 和只读接口仍可用。Jev 关闭使用 None，
  QQ 开启默认 mock；不连接 Gateway。ready 前拒收，先接受停止再取消/等待 scheduler 与 worker，
  关闭自有 Jev/mock/DB；注入依赖由调用方拥有，生命周期约定通过测试明确，不重复关闭。
  所有启动中失败也清理已成功创建资源。health 检查 ready+DB+该启用的 scheduler，关闭 QQ 无
  scheduler 属正常；DB 探测异常/0.25秒超时、任务已退出→503。
- [ ] **Step 5: 实现读接口并 PASS。** UUID/时间由 schema 序列化，未知 UUID 404，参数错误422；
  unhandled 仅当前 boot 的最新未关联 new，历史 item/wake 可读不推进状态。保留 main 的模块入口，
  CLI 接收 host/port 并重新 config 校验。新增纯命令行测试通过注入 fake uvicorn 验证参数解析，
  不真正监听端口。原 test/test_main.py 及旧装配型测试将随旧链路退出，
  本阶段只跑新路径，不将过时旧测试错误修成新系统兼容层。重跑 Step 2，不自动提交。

### Task 8: lifecycle 接线、迁移清理和全量验收

**Files:**
- Modify: `apps/argus/settings.json`、`settings.json.example`、`README.md`、`requirements.txt`、`test/conftest.py`；核对 `scripts/start.sh`。
- Modify: `icarus.toml`、`scripts/tests/test_icarus_control.py`、`scripts/tests/test_process_runtime.py`。
- Modify: `apps/argus/test/decision_graph/{test_dependencies.py,test_runner.py,test_limits.py,test_model_steps.py,test_trace.py}`。
- Create: `apps/argus/test/test_layer_dependencies.py`。
- Delete after migration: `apps/argus/src/{clients,config,connector,dao,entity,evidence,handler,service,strategy,utils}/`；不删新 main、common、core、surfaces、decision_graph。
- Delete after migration: 旧 `apps/argus/test/{clients,application,config,connector,dao,entity,evidence,handler,service,strategy}/`、`test/test_main.py`、`test/persistence_fakes.py`。

**Interfaces:**
- Consumes: 新模块入口、配置、health、隔离本地 PG；Produces: `icarus start/stop/status argus` 继续使用现有控制面。
- 新 `icarus.toml` Argus start_command 与 Gateway 同形传 `--host {host} --port {port}`，
  process_markers 仍 apps.argus.src.main，probe=/health 且 expect_body.status=ready。
  mock 模式移除过时 Gateway requires；数据库是显式 ARGUS_DATABASE_URL，不伪造 provider DSN、
  不自动启动共享 PG。默认栈仍含 Argus，缺 DSN 明确报缺配置，README 说明新增前置条件。

- [ ] **Step 1: 写/迁移失败测试。** 根 control 测试断言 Argus marker/probe/host-port 参数与入口一致、
  无 Gateway 硬依赖；process_runtime 用本地 fixture app 验证透传端口并可识别/停止，不启动真实 QQ/PG。
  架构 AST 测试断言 common/core 无 concrete surface import、sources 不交叉、ORM 不流到路由、
  新代码不 import decision_graph/旧模块。迁移五个保留图测试：本地通用结果替代 RouteDecision，
  Jev 改新 import；业务 safe_trace 测试改为新判断详情不含原文的测试，不保留整个旧 strategy。

  ```python
  def test_argus_registry_keeps_existing_process_contract():
      import tomllib
      config = tomllib.loads((ROOT / "icarus.toml").read_text())["components"]["argus"]
      assert "apps.argus.src.main" in config["process_markers"]
      assert config["probe"]["path"] == "/health"
      assert config["probe"]["expect_body"] == {"status": "ready"}
      assert config["start_command"][-4:] == ["--host", "{host}", "--port", "{port}"]
      assert "gateway" not in config.get("requires", {})
  ```

- [ ] **Step 2: RED。** `apps/agent/.venv/bin/python -m pytest scripts/tests/test_icarus_control.py scripts/tests/test_process_runtime.py -q`；
  `apps/argus/scripts/test.sh apps/argus/test/test_layer_dependencies.py apps/argus/test/decision_graph -q`。
- [ ] **Step 3: 接线和配置。** main/shell 维持原启动模块、不强行改 __main__；更新 TOML 的注释和
  可选依赖。实际 settings 和 example 同步新结构，检查内容再改，不覆盖秘密/本地有效设置。
  README 写清 compose.dev 的显式手动启动、独立 DSN、测试随机端口、普通私聊直接唤醒、
  群忙时积累、重启不恢复、mock 解决不是实际动作、正式 PG 用户手动部署；不提供生产清库步骤。
- [ ] **Step 4: 清理。** 全仓检查待删模块调用者，先迁移 Jev/Gateway/QQ 协议和隔离测试再删除；
  移除 aiosqlite；从 conftest 移除旧 loader patch，保留新配置和密钥隔离。
  旧数据文件不动；不创建旧路径兼容壳；图源码不改，图测试去业务依赖。
- [ ] **Step 5: 全量验收。** 先运行 Step 2 的聚焦路径，再 `make test-argus`；
  `apps/agent/.venv/bin/python -m pytest scripts/tests -q`；
  `apps/argus/.venv/bin/python -m compileall -q apps/argus/src apps/argus/test packages`；`git diff --check`。
  预期全部 PASS、无 collection error、无通过 skip 隐藏 DB 问题、无真实模型/Gateway/QQ 调用。
  如果出现无关现存失败按身份对照基线报告，不顺手修复。没有修改 Gateway/Agent 产品代码，
  不把它们的全套测试列成虚假的已运行项。不自动提交。

---

## 3. 最终验收清单

- [ ] 签名正确的上报立即204；错误请求零落库，模型/处理端阻塞不拖住上报。
- [ ] 并发重复只一条，编辑追加 revision，旧内容重投不倒退，撤回不复活。
- [ ] 私聊与 @ 尽快判断、普通群聊安静5秒/最长30秒；群忙时继续采集，其他 chat 不阻塞。
- [ ] 预算外内容保持 new，超长消息有短摘录和完整原文；任何实际 Jev 输入已过滤且有界。
- [ ] waiting→sent→accepted→resolved/no_action/failed，终态释放 chat；重复回执不重复推进。
- [ ] 传输未知用同 wake_id 重试，有最大次数/期限，失败不永久占用，不无限重新叫醒。
- [ ] mock 结果 simulated=true，观测 processed 而非 verified，不声称真实外部任务已完成。
- [ ] 重启不恢复、不处理旧待办；旧 accepted 不阻塞新消息；全库幂等不因重启失效。
- [ ] QQ 关闭时来源完全暂停；两条 health 共用逻辑；入口/探针和应用级管理一致。
- [ ] 本地/测试 PG 隔离、无生产 DB 访问、无真实密钥/模型调用，应用全套和相关根测试通过。

## 4. 实施顺序与后续接线

依赖顺序：Task 1→2；Task 3 依赖2；Task 4依赖2/3；Task 5依赖2；Task 6依赖4/5；
Task 7依赖全部；Task 8完成迁移和最终验收。每任务依次 RED→实现→GREEN，不先搭一堆空层。

后续真实 Gateway/agent adapter 另开设计，替换 AgentClient 的实现，不自动继承旧永久 accepted
stub 或 session_reuse/preferred_session_id 约定。Gateway RPC、会话/结果日志的实现文档归各自
应用；只有不可拆分的共同要求才进入根跨应用 spec。未经授权不改共享 PG、不建真实账号或部署。

## 5. 实施记录（2026-10-21）

第一阶段代码已按功能提交到 feat/argus：存储与配置、QQ采集、规则判断、叫醒调度、应用切换及
旧链路清理分别提交，未推送。具体当前架构见 [arch.md](arch.md)。
原逐步清单保留为实施要求，不把尚未执行的每一个建议用例勾成已完成；实际验证以运行记录为准。

- 已落地经典后端分层、双表PG、boot隔离、规则/Jev、mock递交解决、串行/并发调度和读接口。
- 已迁移Jev typed/重试、Gateway传输与QQ关键协议测试，用户单独批准后清理旧链路；保留图源码。
- 相比本计划接口的改进：路由注册一次，通过当前app.state获取本次boot依赖，避免重复lifespan
  保留旧repository闭包；create_router不再接受scheduler参数，健康检查读取当前app.state。
- 复用既有进程管理机制，补Argus风格host/port命名参数的真实spawn/status/stop用例，
  加Argus注册与应用CLI透传测试，不复制运行管理器。
- 数据库验证使用本次独立PG17容器；test.sh自动建/清临时容器路径已实际执行。
- 实际验证：`make test-argus` 为141项通过；新增Argus注册和进程管理测试共30项通过；
  compileall、依赖pip check、git diff --check通过。真实loopback TCP /health冒烟通过，未调用真实模型。
- 独立审查后的修复均有复现测试：终态release释放mock完整payload；Jev不外送显示名并校验有限概率；
  首次begin_attempt重查版本（未知递交重试仍保留原包）；at目标进入规范化内容和版本指纹；
  关闭使用AsyncExitStack保证继续释放；配置错误hide_input_in_errors隐藏误填秘密。
- 根suite最终360通过/7失败，失败与HEAD测试、HEAD拓扑加本机local override一致；无local override的HEAD
  control用例20/20通过。未改动这些无关环境覆盖或测试期望。
- 未访问正式数据库、未执行真实QQ/模型/agent调用、未创建分支或推送；初次实现未提交，随后经
  用户授权按功能拆分提交，文档与代码分开。最终审查修复与测试结果在本节及执行ledger中记录。

## 6. 原文档自检（编写计划时）

- design 的分层、无 graph/回放、mock 闭环、重启语义、群串行/跨群并发、私聊、预算、安全、
  本地 PG、生命周期、历史查询均映射到上述任务。
- 接口名称只在各任务 Interfaces 定义，状态、默认值以 §2 为准；任务内不引入额外业务表。
- 编写计划时，上述命令尚未执行；现有实施与验证结果以第5节和执行ledger为准。
