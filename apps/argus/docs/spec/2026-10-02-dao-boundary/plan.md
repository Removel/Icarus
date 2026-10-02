# B：DAO 边界整理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 表记录和业务对象归 entity，DAO 收发表记录、执行事务，证据组装和业务状态选择归上层。

**Architecture:** 一个 Database 提供公开会话/事务入口，两个 store 共用它而不互访私有属性。业务对象用同层 to_row/from_row 映射；evidence 读取相同事务中的行并构造业务证据；service 调 strategy 选择状态，再在事务内保存。B 迁移全部调用点，但暂不取消入站队列或改 QQ 图的证据位置，这些在 C 完成。

**Tech Stack:** Python 3.12、SQLAlchemy async、SQLite/aiosqlite、dataclasses、pytest。

**Spec:** [design.md](design.md)。按 [A plan](../2026-10-02-decision-graph-substrate/plan.md) → B → [C plan](../2026-10-02-layer-organization/plan.md) 执行。

## 执行记录（2026-10-02）

- A/B/C主体改造已实施；当前状态见[现状架构](../2026-10-01-evidence-and-pacing/arch.md)。
- 最终 `make test-argus`：210 passed；命令包含compileall，`git diff --check`通过。
- 原入站queue/worker及4项旧测试经用户逐文件确认后删除；后台批次队列保留。
- 独立review子代理3次均API400未运行；只完成作者自查，不能视为独立审查通过。
- 未创建分支/worktree、未提交/推送、未连接真实服务或读取密钥/真实库。
- 任务中的协同接口迁移合并实施，部分旧行为用例为原有行为固定测试而非新功能RED；实际RED/GREEN与取舍见本次执行记录，不宣称每个示例逐字执行。

## Global Constraints

- `entity` 定义业务对象和基于表的记录对象；两类对象分文件，不让业务对象继承 ORM。
- `entity` 不依赖 DAO；DAO 不导入业务实体、证据或策略。所有 ORM 定义归 entity/records.py。
- 三张表、列/索引/约束、schema v3、事实存储格式不变，不执行数据库迁移。
- 前缀成员绑定、条件更新、提交意图、明确拒绝和 accepted 回执各自保留原子事务。
- 窗口/截断/活动计数规则不变；不能把一个读事务拆成多个不一致快照。
- 不建立 CRUD 基类、repository、Unit of Work 框架或两套兼容接口。
- 所有新测试使用临时数据库/隔离配置；不读取密钥、不连接真实服务、不动用户 settings。
- 不创建分支/worktree、不提交/推送；每任务结束检查 diff，Git 提交仅在用户另行授权后进行。

## Review Focus

1. 重投的新本地 ID/received_at 和 bot_names 变化不得修改首次强信号或原记录——Task 2 去重回归。
2. 行脱离 session、JSON 后续被修改时映射仍稳定——Task 1/2 往返及拷贝测试。
3. 读上下文期间新消息到达，水位/前序/后到必须仍来自同一事务——Task 3 快照回归。
4. 明确 steer 拒绝落库后立刻中断，重启不能成为 submission_unknown——Task 4 原子写测试。
5. 批次成员争抢失败不能留下半成品批次/绑定——Task 2 成员原子性测试。

---

## 文件结构与接口契约

| 文件 | 工作 |
|---|---|
| `entity/records.py` | Base、ObservationRow、SessionLinkRow、DecisionBatchRow；原 ORM 定义原样迁移 |
| `entity/qq_observation.py` | QQObservation.to_row/from_row，移入原 codec 格式校验 |
| `entity/session_link.py` | SessionLink 及 to_row/from_row |
| `entity/decision.py` | ObservationBatch.from_row/to_row，输入显式成员和接收序号 |
| `dao/database.py` | engine/sessionmaker、SQLite 配置、公开 session/begin、运行及健康状态 |
| `dao/{observation_store,batch_store}.py` | 原表查询/写入，返回行，不构造业务证据 |
| `evidence/{recent_activity,icarus_state}.py`、`evidence/qq/qq_conversation.py` | 行解释和证据构造 |
| `strategy/batch_lifecycle.py` | 状态集合/决策状态映射/重启恢复映射的纯函数 |
| `service/batch_state.py` | 加载业务批次及原子批次状态保存的实际调用函数，不是新 repository 层 |
| `service/observation_status.py` | 组合行/trace 的安全分页视图 |
| `utils/datetime_utils.py` | 现有 utc 函数改名为 utc，行为不变 |

后续任务共享以下名称：

```python
Database(data_dir: Path)
await database.start()
await database.close()
database.is_running: bool
await database.is_available() -> bool
async with database.session() as session: ...
async with database.begin(immediate=True) as session: ...

ObservationStore(database: Database)
BatchStore(database: Database)
QQObservation.to_row(*, is_strong_signal=False) -> ObservationRow
QQObservation.from_row(row: ObservationRow) -> QQObservation
SessionLink.to_row(*, id: str, created_at: datetime) -> SessionLinkRow
SessionLink.from_row(row: SessionLinkRow) -> SessionLink
ObservationBatch.from_row(row: DecisionBatchRow, members: tuple[ObservationRow, ...]) -> ObservationBatch
ObservationBatch.to_row(*, first_ingest_seq: int, last_ingest_seq: int,
                        close_reason: str, status: str) -> DecisionBatchRow
```

store 方法中可选的 `session=None` 必须在传入时复用，不自行提交/关闭该 session；未传则使用 Database 建立自己的读取或写事务。不向图 runner 传 ORM session。

B 在迁移调用侧时统一注入以下组件，C 沿用它们，不再把 store 当数据库：

```python
QQService(database, observations, batches, judge, gateway, *, workspace_path,
          bot_names=(), reply_hint=QQHandler.reply_hint, max_steps=32,
          graph_timeout_seconds=30, clock, waiting_check_seconds=5,
          submission_window_seconds=300)
QQConversationReader(database, observations, batches)
RecentActivityReader(database, observations, batches, *, window_seconds=300)
IcarusStateReader(observations, batches, client, workspace_path, *, clock)
BatchScheduler(database, observations, batches, *, activity_reader,
    quiet_seconds=1, max_messages=20, max_seconds=60, bot_names=(), capacity=100,
    clock, waiting_changed=None, base_seconds=15, start_count=2, cap_seconds=300,
    spacing_seconds=15, bypass_messages=6, submission_window_seconds=300)
```

clock 的默认值沿用各组件原 UTC 当前时间/秒值，测试显式注入；不增加生产参数大包。Task 2 同步更新 main、test/application/conversation_fakes.Flow（database 字段）、make_flow 及其他 fake setup；store 仍指 ObservationStore。

## 命令与基线

所有命令在仓库根执行：

```bash
apps/argus/.venv/bin/python -m pytest apps/argus/test/dao apps/argus/test/entity apps/argus/test/evidence -q
make test-argus
git diff --check
```

- [ ] 核对 A 的已验证状态和当前 git status；保存现有改动，不能 reset。
- [ ] 执行 `make test-argus` 记录 B 的基线；失败按具体测试记录，不能顺手修别的应用。

### Task 1：表记录迁入 entity，抽出 Database

**Files:**
- Move: `apps/argus/src/dao/entities.py` → `apps/argus/src/entity/records.py`
- Create: `apps/argus/src/dao/database.py`
- Create in this task: `apps/argus/src/service/batch_state.py`（load_batch/close_batch，见 Task 2 的固定签名）
- Create: `apps/argus/src/utils/datetime_utils.py`
- Modify: `apps/argus/src/dao/{observation_store,batch_store,qq_observation_codec,schema}.py`
- Modify call sites: `apps/argus/src/{service/qq_service,service/batch_scheduler,evidence/icarus_state,evidence/qq/qq_conversation,evidence/recent_activity}.py`
- Modify: `apps/argus/src/main.py`
- Create tests: `apps/argus/test/entity/test_records.py`、`apps/argus/test/dao/test_database.py`
- Modify tests: `apps/argus/test/dao/test_schema.py`、`apps/argus/test/application/test_batch_health.py`、所有构造 store 的测试/fixture。

**Interfaces:**
- Consumes: 原 ORM 列/约束和 ObservationStore.start/close/_configure_connection 的逻辑。
- Produces: 上述 Database/public session 接口；ObservationStore/BatchStore 都直接依赖同一个 database，不再依赖另一个 store。

- [ ] **RED：Database 生命周期/只读记录定义测试。**

```python
import asyncio
from apps.argus.src.dao.database import Database
from apps.argus.src.entity.records import Base

def test_database_lifecycle_and_schema(tmp_path):
    async def exercise():
        database = Database(tmp_path)
        assert not database.is_running
        await database.start()
        assert database.is_running and await database.is_available()
        async with database.session() as session:
            from sqlalchemy import text
            assert await session.scalar(text('PRAGMA user_version')) == 3
        await database.close()
        assert not database.is_running and not await database.is_available()
    asyncio.run(exercise())
    assert set(Base.metadata.tables) == {'observations', 'decision_batches', 'session_links'}
```

- [ ] Run test_database/test_records，预期缺少新模块失败。
- [ ] **GREEN：**ORM 去掉私有前缀，表名/类型/默认/索引不变；Database 接收原 WAL、busy_timeout、权限和 schema 拒绝逻辑。公开 begin(immediate=True) 执行 BEGIN IMMEDIATE；begin() 也明确执行 BEGIN，不能仅依赖 aiosqlite 对 SELECT 的隐式事务，否则 Task 3 多次读取不一定共用快照。session() 用于不要求组合快照的单次读取；context 退出正确 commit/rollback/close。
- [ ] 两个 store 移除 engine 所有权；原构造 `BatchStore(store)` 全部改为 `BatchStore(database)`，不做重导出/包装旧路径。BatchStore.get 在本任务就改返回 DecisionBatchRow，成员查询归 ObservationStore.member_rows；load_batch/close_batch 的真实编排实现移入 service.batch_state。暂用原 codec 转换成员直到 Task 2，不把另一个 store 注回 BatchStore。两个 store 的交叉查询通过 Database/session 和记录类型完成，不能只更名后继续穿透原私有成员。
- [ ] main 只启动/关闭 Database，health 调公开 is_running/is_available；测试注入从 store 改 database/组件引用并保留 test_batch_health 的断库覆盖。
- [ ] 原 codec 暂保留真实实现但改 import entity.records；Task 2 完成映射迁移后删除，不增加新兼容壳。
- [ ] 批量构造调用点可用测试辅助函数集中创建 `database, observations, batches`，但别新建生产装配框架。
- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/dao/test_database.py apps/argus/test/dao/test_schema.py apps/argus/test/application/test_batch_health.py -q`；再跑 Argus 全量，检查无 schema 差异。
- [ ] 检查本任务 diff，无提交。

### Task 2：业务映射与行 CRUD 边界

**Files:**
- Modify: `apps/argus/src/entity/{qq_observation,decision}.py`
- Create: `apps/argus/src/entity/session_link.py`
- Delete after moving implementation: `apps/argus/src/dao/qq_observation_codec.py`
- Modify: `apps/argus/src/dao/{observation_store,batch_store}.py`
- Modify: `apps/argus/src/service/batch_state.py`（Task 1 已创建，本任务转换到 entity 的完整映射接口）
- Modify call sites: `apps/argus/src/{main,connector/qq_connector,service/qq_service,service/batch_scheduler,evidence/icarus_state,evidence/qq/qq_conversation,evidence/recent_activity}.py`
- Move tests: `test/dao/test_qq_observation_codec.py` → `test/entity/test_qq_observation_mapping.py`
- Modify tests: `test/dao/{test_observation_store,test_batch_store,test_session_links,test_recovery}.py` 及所有使用业务对象 CRUD 的测试/fixture。

**Interfaces:**
- Produces: `ObservationStore.insert(row: ObservationRow, *, session=None) -> bool`、`get(id, *, session=None) -> ObservationRow`、`recent(platform, account_id, subject, limit=20, *, session=None) -> tuple[ObservationRow, ...]`。
- `current_link(platform, account_id, subject, *, session=None) -> SessionLinkRow | None`；`save_link(row: SessionLinkRow, *, session=None) -> None`（原活动关联结束/新关联保存仍同事务）。
- `BatchStore.get(id, *, session=None) -> DecisionBatchRow`；删除与之重复的业务 `record()` 接口并迁移调用点；`transition(id, expected: tuple[str, ...], *, session=None, **values) -> bool`。
- `BatchStore.create(row: DecisionBatchRow, member_ids: tuple[str, ...], *, session=None) -> None`：事务内校验待绑定前缀与 first/last seq；不生成业务 batch/close_reason/状态。
- `ObservationStore.member_rows(batch_id, *, session=None) -> tuple[ObservationRow, ...]`。
- `service.batch_state.load_batch(observations, batches, id) -> ObservationBatch`：用两张表行经 from_row 构造，不让 DAO 返回业务对象。
- `service.batch_state.close_batch(database, observations, batches, scope, member_ids, close_reason, *, now) -> ObservationBatch`：公开 immediate 事务内读取候选前缀，使用调用侧生成的 batch ID/时间构造业务批次与 row，调用 BatchStore.create，再返回业务批次；QQService/scheduler 的批次创建统一调用它，不重复生成成员状态。

- [ ] **RED：独立映射和脱离 session 回读。**

```python
def test_business_mapping_is_same_layer_and_copies_json():
    from apps.argus.src.handler.qq_handler import QQHandler
    from apps.argus.src.entity.qq_observation import QQObservation
    from apps.argus.src.entity.records import ObservationRow
    obs = QQHandler('10000').parse(dict(post_type='message', self_id=10000,
        time=1700000000, message_id=1, message_type='group', group_id=42,
        user_id=7, message='hello'))
    row = obs.to_row(is_strong_signal=True)
    assert isinstance(row, ObservationRow)
    assert QQObservation.from_row(row) == obs
    restored = QQObservation.from_row(row)
    row.content[0]['value'] = 'changed'
    assert restored.content[0].value == 'hello'
```

- [ ] Run test_qq_observation_mapping，预期缺少 to_row/from_row 失败。
- [ ] **GREEN：**迁移 codec 真实映射与 UnsupportedObservationError 到 entity/qq_observation；from_row 使用 utc 对已有 naive/aware 时间归一化，不把 UTC 以外时间硬 replace；所读取 JSON 转成独立业务值。
- [ ] DAO insert 只比较表字段事实：platform/account/source_id、subject、sender_id/name、occurred_at、content、subtype/detail_version/detail；排除 id/received_at/ingest_seq/batch_id/is_strong_signal。仍由 DB 唯一约束和 BEGIN IMMEDIATE 固定接收顺序。
- [ ] 加入重投测试：相同来源生成新本地 ID/接收时间/强信号参数，insert=False；原 id、seq、首次强信号不变；修改内容则 ObservationConflictError，原记录不变。
- [ ] BatchStore.create 接收调用方已构造的 row；在一事务内 SELECT 预期前缀→验证 ID/seq→insert batch→绑定 members。冲突时整事务回滚。service/scheduler 负责创建 ObservationBatch 和 row，不让 store 生成业务对象。
- [ ] `load_batch` 及调用侧转换在本任务同步完成；临时仍存在的 DAO 证据组装先改成使用已有字段逻辑，Task 3/4 将它们迁出，不保留旧业务 CRUD 接口。
- [ ] 将原 store record 字典访问改为 row 属性；JSON 更新采用复制赋值，不靠原地 append 触发 SQLAlchemy 脏检测。
- [ ] 从映射删除的路径搜索全部引用，确认不留下旧导入。
- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/entity apps/argus/test/dao -q`；Run `make test-argus`。
- [ ] 检查固定前缀争抢/回滚和重启恢复断言保持真实覆盖，无提交。

### Task 3：聊天与活动证据迁出 DAO，保持查询快照

**Files:**
- Modify: `apps/argus/src/dao/{observation_store,batch_store}.py`
- Modify: `apps/argus/src/evidence/{recent_activity,icarus_state}.py`
- Modify: `apps/argus/src/evidence/qq/qq_conversation.py`
- Modify: `apps/argus/src/service/{batch_scheduler,qq_service}.py`
- Test: `apps/argus/test/evidence/qq/test_qq_conversation.py`
- Test: `apps/argus/test/evidence/{test_recent_activity,test_icarus_state}.py`
- Move business statistics assertion from `apps/argus/test/dao/test_waiting_batches.py` into test/evidence/test_recent_activity.py。

**Interfaces:**
- `ObservationStore.preceding_rows(scope, first_seq, *, limit, session=None) -> tuple[ObservationRow, ...]`：倒序查询，返回倒序，reader 负责恢复聊天顺序。
- `following_rows(scope, last_seq, through_seq, *, limit, descending=False, session=None) -> tuple[ObservationRow, ...]`、`count_following(scope, last_seq, through_seq, *, session=None) -> int`。
- 保留 `message_watermark(platform, account_id, subject, *, human_only=False, session=None) -> int`、`latest_strong_signal(platform, account_id, subject, *, through_seq=None, session=None) -> int`。
- `unassigned(scope, limit=20, *, session=None) -> tuple[ObservationRow, ...]`、`pending_summary(scope, *, session=None) -> tuple[int, bool]`、`unassigned_scopes(*, session=None) -> tuple[tuple[str, str, str], ...]`：明确只返回行/计数。
- `delivery_context_rows(scope, first_seq, *, limit=10, session=None) -> tuple[tuple[ObservationRow, str | None], ...]`：按前序降序先取最多10条，并附对应 batch status；调用侧反序再排除 submitted，不能先 SQL 排除后取更远10条而改变上下文。
- `QQConversationReader.delivery_context(batch) -> tuple[Observation, ...]` 使用上述查询构造投递上文；service 不直接拿行当聊天实体。
- `BatchStore.judged_rows(scope, *, through_seq, exclude=None, statuses, interventions, session=None) -> tuple[DecisionBatchRow, ...]`：按 judged_at/first_seq 降序，过滤条件由 reader 提供。
- `BatchStore.delivered_rows(scope, *, through_seq, since, until, exclude=None, statuses, session=None) -> tuple[DecisionBatchRow, ...]`：按 delivered_at 降序。
- `BatchStore.task_delivery_rows(scope, *, task_id, limit=100, session=None) -> tuple[DecisionBatchRow, ...]`：先按原范围查最近100条 submitted 的 session.submit，再按精确 receipt.task_id 取匹配记录，保持原查询边界；由 IcarusStateReader 提取原 params.prompt。
- reader 的现有 `evaluate(batch=...)` 签名暂保持，为 A 机械 QQ 图兼容；RecentActivityReader 增加 `read_scope(scope, *, through_seq, now, exclude=None) -> RecentActivity` 供 scheduler 复用。

- [ ] **RED：新增直接使用新行查询的 reader 测试，保留 following/member 分离断言。**

```python
import asyncio
from apps.argus.src.dao.database import Database
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.dao.batch_store import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.src.entity.decision import ObservationBatch
from apps.argus.src.evidence.qq.qq_conversation import QQConversationReader

def test_reader_uses_fixed_members_and_following_rows(tmp_path):
    async def exercise():
        database = Database(tmp_path)
        await database.start()
        observations, batches = ObservationStore(database), BatchStore(database)
        try:
            messages = tuple(QQHandler('10000').parse(dict(post_type='message',
                self_id=10000, time=1, message_id=n, message_type='group',
                group_id=42, user_id=7, message=f'part{n}')) for n in (1, 2, 3))
            for message in messages:
                await observations.insert(message.to_row())
            first = await observations.get(messages[0].id)
            batch = ObservationBatch('b', 'qq', '10000', messages[0].subject.key,
                (messages[0],), messages[0].received_at, messages[0].received_at)
            await batches.create(batch.to_row(first_ingest_seq=first.ingest_seq,
                last_ingest_seq=first.ingest_seq, close_reason='quiet', status='ready'),
                (messages[0].id,))
            assert await observations.count_following(('qq','10000','qq:group:42'),
                first.ingest_seq, 3) == 2
            evidence = await QQConversationReader(database, observations, batches).evaluate(batch=batch)
            assert evidence.current == (messages[0],)
            assert evidence.following == messages[1:]
            assert evidence.through_seq == evidence.human_through_seq == 3
        finally:
            await database.close()
    asyncio.run(exercise())
```

新 count_following 接口在本任务新增，先缺失而失败；原 fixed members/reload 断言继续放既有批次测试，不能仅把旧测试改到仍会通过的等价调用当成 RED。
- [ ] 再构造 11 条前序/21 条后到：前序只保留最近10，后到首10/末10，第11条后到不冒充成员，truncated=True；不足上限时无截断。
- [ ] Run test_qq_conversation/test_recent_activity，预期迁走旧入口而新 reader 未实现时失败。
- [ ] **GREEN：在 reader 内复用一个公开读事务。**

```python
async with database.begin() as session:
    record = await batches.get(batch.id, session=session)
    through = await observations.message_watermark(*scope, session=session)
    human = await observations.message_watermark(*scope, human_only=True, session=session)
    before = await observations.preceding_rows(scope, record.first_ingest_seq,
                                               limit=11, session=session)
    count = await observations.count_following(scope, record.last_ingest_seq,
                                               through, session=session)
    # count>20：显式取首10/末10；否则 limit=20。转换/截断属于 reader。
```

- [ ] 固定水位测试：使用第二个连接在第一次 SELECT 后插入后到消息；同一 reader 的 through/成员查询保持一致，不拆新 session。注入/query spy 只放测试，不在生产加 hook。
- [ ] activity reader 保留原 no/yes/失败排除、强信号 reset、submit 与 steer 区分、统计窗口的边界 `since <= delivered_at <= now`；不随意加历史截断导致连续计数变小。
- [ ] IcarusStateReader 不再函数内 `BatchStore(store)`；注入 observations/batches/database，精确任务 ID 查行并提取请求，不按相似文本猜。
- [ ] 删除 DAO.conversation/recent_activity/task_request 的业务输出入口，scheduler 调 ActivityReader，QQService 调 reader。仍保留业务从 DAO 移出的精确过滤行为。
- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/evidence apps/argus/test/service/test_batch_context.py apps/argus/test/application/test_waiting_recovery_flow.py -q`；Run Argus 全量。
- [ ] 检查本任务 diff，DAO 不再 import entity.evidence。

### Task 4：业务状态映射上移，原子保存仍在事务内

**Files:**
- Create: `apps/argus/src/strategy/batch_lifecycle.py`
- Modify: `apps/argus/src/service/batch_state.py`
- Modify: `apps/argus/src/service/{qq_service,batch_scheduler}.py`
- Modify: `apps/argus/src/main.py`
- Modify: `apps/argus/src/dao/batch_store.py`
- Create: `apps/argus/test/strategy/test_batch_lifecycle.py`
- Modify tests: `apps/argus/test/dao/{test_recovery,test_waiting_batches}.py`
- Test regressions: `apps/argus/test/application/test_review_boundaries.py`

**Interfaces:**
- `strategy.batch_lifecycle.status_for_decision(action: str, run_status: str) -> str`：failed/finished/waiting/decided 原映射。
- `recovery_status(status: str, resume_status: str | None) -> str`：judging 恢复 waiting/ready；submitting→submission_unknown；其他不改。
- `OPEN_STATES` 位于 strategy，不再 DAO 硬编码。DAO head/recovery 接口固定为：
  - `is_head(id, *, open_states, session=None) -> bool`。
  - `recover_batches(*, limit=100, exclude=(), now, open_states, ready_states, session=None) -> tuple[str, ...]`。
  - `waiting_heads(*, limit=100, open_states, waiting_status='waiting', session=None) -> tuple[str, ...]`。
  - `rows_in_states(states: tuple[str, ...], *, session=None) -> tuple[DecisionBatchRow, ...]`：用于 startup 恢复，不在 DAO 选择恢复目标。
- DAO 仅 get/transition/create 及查询；原 claim/save_decision/defer/begin_submission/mark_receipt/mark_rejected/reset_interrupted 的业务选择迁至 service.batch_state 函数，用 Database.begin(immediate=True)+get/transition 保持事务。
- service.batch_state 的函数均首传 `database, batches`，其余签名固定如下，不另建 Store 包装类：

```python
async def claim_batch(database, batches, id) -> bool: ...
async def save_decision(database, batches, id, *, decision, run_status, trace,
    judged_at=None, intervention=None, judged_through_seq=None, next_check_at=None,
    wait_fingerprint=None, decision_message_seq=None) -> bool: ...
async def defer(database, batches, id, *, reason, next_check_at,
    fingerprint, trace=None) -> bool: ...
async def begin_submission(database, batches, id, method, params, operation_id,
    *, workspace_key=None) -> bool: ...
async def mark_receipt(database, batches, id, response, *, delivered_at=None) -> bool: ...
async def mark_rejected(database, batches, id, response, *, next_check_at) -> bool: ...
async def reset_interrupted(database, batches) -> None: ...
```

- [ ] **RED：状态映射测试。**

```python
from apps.argus.src.strategy.batch_lifecycle import status_for_decision, recovery_status

def test_decision_and_recovery_states():
    assert status_for_decision('silence', 'completed') == 'finished'
    assert status_for_decision('wait', 'completed') == 'waiting'
    assert status_for_decision('submit', 'completed') == 'decided'
    assert status_for_decision('silence', 'failed') == 'failed'
    assert recovery_status('judging', 'waiting') == 'waiting'
    assert recovery_status('judging', 'ready') == 'ready'
    assert recovery_status('submitting', None) == 'submission_unknown'
```

- [ ] Run test_batch_lifecycle，预期新接口缺失失败。
- [ ] **GREEN：固定映射归 strategy，service 控制写事务，DAO transition 收 expected 与明确 values。**

```python
async with database.begin(immediate=True) as session:
    row = await batches.get(batch_id, session=session)
    if row.status != 'submitting':
        return False
    attempts = list(row.delivery_attempts or [])
    if attempts:
        attempts[-1] = dict(attempts[-1], outcome='rejected')
    return await batches.transition(batch_id, ('submitting',), session=session,
        status='waiting', receipt=response,
        route=dict(action='wait', reason='steer_rejected', target_task_id=None),
        next_check_at=next_check_at, delivery_attempts=attempts)
```

service 中固定状态序列的条件校验是持久化编排；回应/动作含义取自策略结果，不搬进 DAO。

- [ ] begin_submission 保存准确 method/params/operation_id、workspace/session 身份、hash/attempt；accepted 保存 receipt/delivered_at；更新 trace 使用新列表；不在事务提交前发 RPC。
- [ ] recovery 枚举待恢复行并依据 strategy 生成变更，按预期旧 status 条件更新；不重放 submitted/unknown。
- [ ] 原拒绝中断回归保留：mark_rejected 提交后模拟取消，再 reset_interrupted，仍 waiting、attempt=rejected，无重复 submit。
- [ ] 保存 graph failed 时 judged_at/intervention 与原行为一致，失败不能计成正常 silence。
- [ ] 删除 DAO 的业务状态 helpers/import dataclasses.asdict 和业务 action→status if；搜索确认 OPEN_STATES 无 DAO 定义。
- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/strategy/test_batch_lifecycle.py apps/argus/test/dao/test_recovery.py apps/argus/test/application/test_review_boundaries.py -q`；Run 全量。
- [ ] 检查事务边界 diff，不能用多次自动提交代替一条原子保存。

### Task 5：状态视图、边界检查与 B 完成验收

**Files:**
- Create: `apps/argus/src/service/observation_status.py`
- Modify: `apps/argus/src/{main,dao/observation_store}.py`
- Modify: `apps/argus/test/application/{test_decision_status,test_batch_health}.py`
- Create: `apps/argus/test/service/test_observation_status.py`
- Create: `apps/argus/test/application/test_layer_dependencies.py`
- Update after implementation: `apps/argus/docs/spec/2026-10-01-evidence-and-pacing/arch.md`

**Interfaces:**
- `ObservationStatusService(observations, batches).get(id, *, after_run=0, limit=20) -> dict`。
- DAO get 返回纯行；不存在 observation 仍 KeyError→HTTP404；无 batch 返回 pending 空 runs；分页计数不变。

- [ ] **RED：新增 formatter 单测，真实分页/HTTP 断言沿用既有集成测试。**

```python
import asyncio
from types import SimpleNamespace
from apps.argus.src.service.observation_status import ObservationStatusService

def test_status_service_paginates_only_safe_trace():
    class Observations:
        async def get(self, id):
            return SimpleNamespace(id=id, batch_id='b')
    class Batches:
        async def get(self, id):
            return SimpleNamespace(status='waiting', session_id='s', operation_id=None,
                receipt=None, error_type=None, route={'action':'wait', 'reason':'busy'},
                next_check_at=None, trace=[{'run_id':'r1'}, {'run_id':'r2'}])
    page = asyncio.run(ObservationStatusService(Observations(), Batches()).get('o', after_run=0, limit=1))
    assert page['observation_id'] == 'o'
    assert page['runs'] == [{'run_id':'r1'}]
    assert page['next_after_run'] == 1 and page['has_more']
    assert 'content' not in page
```

新增 `test/service/test_observation_status.py` 存放该单测；既有 test_decision_status 用真实库/假网关保持 after_run<0、limit=0/101 的 HTTP422 与观察未绑定 pending/不存在404 断言，不以 formatter fake 代替集成覆盖。

- [ ] Run test_decision_status，先确认旧 DAO 返回视图依赖被移走。
- [ ] **GREEN：**service 组合 row.route/receipt/trace/times，不输出 ORM 或聊天全文；main 路由调用 status service，删 DAO.status_details。
- [ ] 依赖测试通过 AST 校验 DAO 不导入 entity.decision/evidence/qq_observation、service/strategy；entity 不导入 dao；内部相对导入按所在包解析，不能只匹配绝对 import。
- [ ] 核对无 `observations._require_sessions`、`._codec`、`from .batch_store import utc`；无 dao.entities/codec 路径残留。允许 DAO 持久化异常类型/表记录依赖，不用该测试禁止合法第三方 import。
- [ ] Run DAO/entity/evidence 最小目录与相关 application 回归；Run `make test-argus`。
- [ ] 更新 arch，记录 B 阶段证据 reader 暂仍是 QQ 图节点；不能提前声称取消入站队列或 C 已实现。
- [ ] Run `git diff --check`，检查 compileall，记录真实结果。B 验收通过才进入 C，不提交。
