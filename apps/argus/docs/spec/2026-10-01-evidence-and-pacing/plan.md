# Argus 证据层与触发节奏实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留消息持久化和发送安全边界的前提下，让 Argus 能基于证据决定回应、追加、等待或不回应，并整理项目内图执行能力。

**Architecture:** 图执行器只管理一次运行；证据节点获取事实，策略节点产出决定，service 负责事务与外部动作。跨时间等待使用同一个持久化批次，不把等待当沉默结束；按会话处理最早未完成批次，重判前先检查消息与工作状态是否变化。

**Tech Stack:** Python、asyncio、dataclasses、FastAPI、SQLAlchemy/SQLite、pytest、现有 TypeSafe SDK；不新增依赖。

**Spec:** [design.md](design.md)。执行前必须同时阅读仓库 `AGENTS.md`；本次已按用户 `/goal` 指令在当前分支实施，未提交。

## Global Constraints

- 只修改 `apps/argus` 的实现、对应测试与文档；不修改 Agent/Gateway 协议。
- 应用代码留在 `apps/argus`；测试镜像源目录。文件、目录、函数 snake_case，类 PascalCase。
- 证据只获取事实；策略不执行 DAO 写操作或 Gateway 提交；service 编排和执行副作用。
- 图执行器不导入业务实体；控制边与必需结果依赖保留不同语义。
- 普通群消息短收集窗口 **1 秒**；连续两次不回应后退避 **15 秒**，指数增长，最多 **300 秒**。
- 近期启动密度窗口 **300 秒**；每次成功新提交增加 **15 秒**间隔，最多 **300 秒**；与空闲退避取较晚期限，不相乘。
- **6 条待处理人类消息**可以插队；机器人自己的消息不计数。单批最多 **20 条**，持续收集上限 **60 秒**不能绕过退避。
- 等待中的工作默认 **5 秒**检查一次。无人类消息、无等待请求时，不调用模型、不主动启动。
- 私聊、@、称呼允许提前判断与重置连续不回应计数，不保证回应或追加。
- `submitted` 只代表请求已被接收，不能冒充 QQ 发言成功。结果不明不得自动重发。
- 不新增旧试验库兼容路径、不自动删库；实现所需新库只用临时目录验证。
- 不修改本机 `apps/agent/settings.json`、`apps/argus/settings.json`，不读取真实凭据、备份聊天或实际数据库。
- 不创建分支、不提交、不推送、不启动真实服务。任务边界是独立验收点，不是自动提交指令。

## Review Focus

1. **active ID 包含排队任务且状态可能滞后**：不能挑第一个 ID 追加；任务状态不可用不能当完成。任务 3、6、7 测试。
2. **等待期间后到取消/纠正**：唤醒最早等待批次；背景不能被冒充为已交付载荷，处理结束后后续消息仍需独立判定。任务 3、6、7 测试。
3. **重启或模型耗时期间消息变化**：清零依据、证据水位和等待状态必须正确恢复，不能把模型未见的新消息记成已见。任务 2、5、7 测试。
4. **图判完到执行之间任务已变**：重新核实，不把追加失败直接改成提交；明确拒绝与传输结果不明分开。任务 6 测试。
5. **密集自身消息/跨账号相同群号**：不自唤醒、不跨会话统计、不让自身消息淹没后面的人类消息。任务 2、4、5 测试。

---

## 0. 顺序、边界与验证约定

依次完成任务 1–8。任务 1 整理图能力时保持旧业务行为；任务 2–4 建立后续需要的接口并保持现有调用可运行；任务 5–6 接通调度和新决策，必须作为连续集成阶段完成后才做整体验收。中间状态不部署。

所有路径相对仓库根。命令在仓库根执行：

```bash
apps/argus/.venv/bin/python -m pytest apps/argus/test/<对应目录或文件> -q
make test-argus
apps/argus/.venv/bin/python -m compileall -q apps/argus/src apps/argus/test
git diff --check
```

每项先写会失败的测试，确认失败是因为缺少目标行为，再做最小实现并跑回归。文中的小代码段给出接口和关键判据，不代替完整错误分支。新增异步测试沿用仓库 `asyncio.run(exercise())` 与原生 `assert`，不引入 pytest-asyncio。

开始时执行 `git status --short`，记录用户已有变更；检查 `.venv` 可用后先跑 `make test-argus` 记录基线。若环境不存在或基线失败，报告真实原因，不改配置或修无关错误。没有通过测试的任务不得称为完成。

### 文件分工总表

| 工作 | 新建/调整的主要文件 |
|---|---|
| 通用图执行 | `src/decision_graph/{node,edge,graph,state,runner}.py` |
| 公共业务类型 | `src/entity/{decision,evidence}.py` |
| 持久化 | `src/dao/{entities,batch_store,observation_store,schema}.py` |
| 取证 | `src/evidence/{icarus_state,recent_activity}.py`、`src/evidence/qq/qq_conversation.py` |
| 文本转换及强信号 | `src/utils/{qq_transcript,qq_signals}.py` |
| 判断 | `src/strategy/qq/` 现有策略 |
| 调度与执行 | `src/service/{batch_scheduler,qq_service,decision_trace}.py` |
| 接线与参数 | `src/{main.py,config/config_model.py,connector/qq_connector.py,clients/icarus_client.py}` |

表内 `src/` 均指 `apps/argus/src/`。为新包添加简单 `__init__.py`，不在里面做注册或重导出旧路径。

## 任务 1：整理图定义与执行状态，不改变业务规则

**Files**
- Create: `apps/argus/src/decision_graph/node.py`、`edge.py`、`state.py`
- Modify: `apps/argus/src/decision_graph/graph.py`、`runner.py`
- Create: `apps/argus/src/service/decision_trace.py`
- Modify: `apps/argus/src/service/qq_service.py`、`apps/argus/src/dao/batch_store.py`
- Modify: `apps/argus/src/strategy/base_policy.py` 和现有策略中的节点版本声明
- Test: `apps/argus/test/decision_graph/`，新增 `test_state.py`；新增 `apps/argus/test/service/test_decision_trace.py`

**Interfaces**

图仍由 `DecisionGraph(nodes, edges, entry, output)` 定义。`NodeBinding` 保留 `id/evaluate/bind_input/requires/join/timeout_seconds`；**输入映射改为明确的关键字参数**，执行器调用 `await evaluate(**arguments)`，不再让业务多输入通过位置元组传递。普通单输入策略保持 `evaluate(value)`，映射为 `{'value': value}`；多输入策略可以直接定义 `evaluate(*, facts, snapshot)`。

```python
# node.py：结构接口，不要求所有节点继承同一个业务基类。
NodeCallable = Callable[..., Awaitable[object]]
InputBinder = Callable[[object, Mapping[str, object]], Mapping[str, object]]

# state.py：没有 QQ 类型、没有 node version。
@dataclass(frozen=True)
class GraphRun:
    status: Literal['completed', 'failed']
    output: object
    executions: Mapping[str, NodeExecution]
    selected_edges: tuple[tuple[str, str], ...]
    run_id: str
    error_type: str | None = None
```

`NodeExecution` 保留 id/status/output/error_type/duration/iteration。运行期 `ExecutionState` 持有 states/outputs/selected/records；`ExecutionBudget.remaining` 在重复块间共享，每轮结果空间独立。成功出口值允许 `None`，靠出口节点的成功状态区分失败，不靠 `output is None` 猜。

`GraphRunner.run(graph, value, *, max_steps=32, timeout_seconds=30) -> GraphRun`。`RepeatBlock` 保留在 `graph.py`，其输入映射唯一键为 `value`，作为 body 根输入；禁止悄悄接受其他参数。`GraphRun` 不提供业务 `.decision` 或业务 `.trace()`。

`service/decision_trace.py` 提供 `safe_trace(run: GraphRun) -> dict[str, object]`，返回一轮 `{run_id, status, nodes, selected_edges, error_type}` 的安全摘要，只序列化允许的业务字段；`QQService.evaluate_batch` 返回 `GraphRun`，由 service 验证出口为 `RouteDecision`。DAO 改为保存明确的 `decision/status/trace` 参数，不依赖运行器内部对象。任务 1 的保存已采用“trace 列里每项为一轮摘要”的结构，任务 2 再增加等待后的多轮追加。

- [ ] 写通用出口和运行隔离的失败测试：

```python
async def exercise():
    async def increment(*, number):
        return number + 1
    graph = DecisionGraph(
        (NodeBinding('end', increment, lambda root, _: {'number': root}),),
        (), 'end', 'end',
    )
    runner = GraphRunner()
    first = await runner.run(graph, 1)
    second = await runner.run(graph, 9)
    assert first.output == 2 and second.output == 10
    assert first.run_id != second.run_id
    assert first.executions is not second.executions
```

- [ ] 运行 `python -m pytest apps/argus/test/decision_graph/test_state.py -q`（python 使用 §0 的完整路径），确认当前实现不能接受关键字映射/普通整数出口。
- [ ] 将定义按职责拆开；图构造时计算并保存稳定的拓扑顺序及入边/出边索引。保留必需依赖、条件汇合和冲突检测；不增加自动推导依赖或类型反射编译器。
- [ ] 将 `_execute` 的局部状态整理进 `ExecutionState`；只读 MappingProxyType 保护结果映射。它不保证任意输出对象深度不可变，因此业务输出采用 frozen dataclass/tuple，不允许节点修改传入对象。
- [ ] 删除运行器的业务 import、业务失败默认值和 `__self__` 取版本逻辑。图失败返回 `status='failed'` 与安全 error_type；取消继续传播。service 负责把图失败记录成 failed，绝不提交。
- [ ] 更新全部 NodeBinding 输入映射和 import；原 `qq_materials`、关系/动作策略的位置元组改成关键字参数。删除旧路径转发，不保留两套接口。
- [ ] 更新 DAO 保存调用和旧测试的 `.decision/.trace()`；此步骤尚不增加 waiting。新增测试证明图失败仍写 failed，原有代码介入/模型介入路线保持。
- [ ] 跑图测试与 `apps/argus/test/service apps/argus/test/dao`。必须保留：失败必需证据阻塞终点、未选中分支跳过、any 多成功报错、独立分支仍执行、重复预算/超时、停止取消。

**验收**：图测试用整数/字符串出口即可运行；`decision_graph` 不 import `entity`、strategy、evidence 或 service。节点错误日志不包含异常正文中的秘密。

## 任务 2：保存等待和节奏所需事实，明确新库边界

**Files**
- Modify: `apps/argus/src/dao/entities.py`、`observation_store.py`、`batch_store.py`、`schema.py`
- Modify: `apps/argus/src/entity/decision.py`
- Create: `apps/argus/src/entity/evidence.py`、`apps/argus/src/utils/qq_signals.py`
- Modify: `apps/argus/src/connector/qq_connector.py`、`apps/argus/src/strategy/qq/qq_batch_policy.py`
- Remove obsolete migration implementation/entrypoints: `apps/argus/src/migrate.py`、`apps/argus/scripts/migrate.sh` 及 `schema.py` 内旧数据搬运函数
- Test: `apps/argus/test/dao/test_waiting_batches.py`、`test_activity.py`、`test_schema.py`、`apps/argus/test/utils/test_qq_signals.py`；替换旧迁移专项测试 `test_batch_migration.py`

**Interfaces / 数据字段**

`RouteDecision.action` 扩展为 `silence|submit|steer|wait`，仍只有 steer 允许 target_task_id。`InterventionResult` 保持 yes/no/undetermined；关系结果改为 `steer|wait`（不再用话题标签隐式提交）。

观察行增加 `is_strong_signal: bool`（默认 False）。它是收件派生元数据，不混入原始事实去重比较：

```python
async def insert(self, obs: Observation, *, is_strong_signal: bool = False) -> bool:
    # 新插入时同事务写入；重复来源消息沿用首次值。
    # 不因配置变化后的重投改变强信号，也不触发 ObservationConflictError。
```

`utils/qq_signals.py` 复用并迁出 `mentions_name`，提供 `is_strong_signal(obs: QQObservation, bot_names: tuple[str, ...]) -> bool`，排除自消息，再判断私聊/mentions_bot/名字命中。connector 在入库前计算，DAO 不读配置。

批次行增加以下明确字段（UTC 时间，允许尚未发生时为空）：

| 字段 | 类型/语义 |
|---|---|
| first_ingest_seq / last_ingest_seq | 本批固定成员的最小/最大顺序号，用于同会话保序 |
| judged_at | 最后一次正常介入判断完成时间，不使用 updated_at 代替 |
| intervention | 最后一次明确 yes/no；无判断、纯自消息或错误为空 |
| judged_through_seq | 该次读取时固定的观察水位；不代表背景已经处理 |
| delivered_at | 成功 submit/steer 回执时间 |
| next_check_at | waiting 下次便宜检查时间 |
| wait_fingerprint | 上次完整判断实际使用的证据摘要哈希 |
| resume_status | claim 前状态 ready/waiting，供 judging 中断恢复 |
| decision_message_seq | 决定时的人类消息水位，执行前核对变化 |
| delivery_attempts | JSON 列表：每次发送的 operation_id、method、目标 task ID、payload 摘要哈希与明确回执分类；不复制完整敏感 payload |

`begin_submission` 保留网络调用前写意图的边界，并在同一事务追加 delivery_attempts；当前 params 保存本次精确请求用于恢复。只有明确未接受的尝试允许生成新尝试 ID；unknown 不允许借此重试。

删除 graph_version 字段及写入点；不引入新人工版本制度。`trace` 保留为 JSON 列，但按每次完整运行 append 一个带 run_id 的安全摘要，不能覆盖以前的 waiting 决定。不追加每次无变化的状态轮询；多次轨迹写入须在事务中完成，避免修改 JSON 列未被 ORM 检测。

新建库 schema VERSION=3。`check_schema` 遇到旧版本明确拒绝并说明“测试库需要显式重建”，不自动迁移、删库或调用已移除脚本；原版本拒绝异常可保留名称。README 在任务 8 同步。旧库拒绝测试仅用于防止误开，不是实现兼容。

DAO 提供以下接口，返回值可继续使用当前 record 字典边界；公开证据使用任务 3 的类型：

```python
# BatchStore
async def record(self, id): ...                 # 保留
async def claim_batch(self, id) -> bool: ...     # ready/waiting -> judging，保存 resume_status
async def save_decision(self, id, *, decision, run_status, trace,
                        judged_at, intervention, judged_through_seq,
                        next_check_at=None, wait_fingerprint=None) -> bool: ...
async def defer(self, id, *, reason, next_check_at, fingerprint,
                trace=None) -> bool: ...        # 尚未交付时 -> waiting，不清除已有证据
async def postpone_check(self, id, *, next_check_at) -> bool: ...
async def mark_receipt(self, id, response, *, delivered_at) -> bool: ...
async def waiting_heads(self, *, limit=100) -> tuple[str, ...]: ...
async def recover_batches(self, *, limit=100, exclude=()) -> tuple[str, ...]: ...
```

waiting 必须加入同 scope 的前序阻塞状态；`recover_batches` 不自行把 waiting 当 ready。`reset_interrupted` 让 judging 恢复 resume_status，submitting 仍变 unknown；恢复不丢等待记录。

- [ ] 写参数化状态测试：wait -> waiting、silence -> finished、图失败 -> failed；waiting 不被普通恢复重复入队，且阻止该会话后续 ready 批次越过它。
- [ ] 写临时库恢复测试（沿用 `test_batch_store.py` 的 make_message/close_batch 方式）：

```python
assert await batches.claim_batch(first.id)
assert await batches.save_decision(
    first.id, decision=RouteDecision('wait', 'busy'), run_status='completed',
    trace={'run_id': 'r1', 'nodes': []}, judged_at=now,
    intervention='yes', judged_through_seq=1,
    next_check_at=now + timedelta(seconds=5), wait_fingerprint='a',
)
assert (await batches.record(first.id))['status'] == 'waiting'
assert second.id not in await batches.recover_batches()
await store.close()
await store.start()
assert (await batches.record(first.id))['status'] == 'waiting'
```

- [ ] 跑新增 DAO 测试确认失败，再增加字段/索引和条件状态迁移。索引分别覆盖 `(platform,account_id,subject,first_ingest_seq)`、交付时间统计和 waiting 检查；不要用随机 ID 决定观察顺序。
- [ ] 增加只读查询：按 scope 读取截至水位的强信号、批次成员顺序、近期成功 submit/steer 和正常判断记录。`submitted` 按 method 区分新提交与追加；窗口边界统一包含下界、排除当前批次。
- [ ] 统计测试覆盖 no、failed、wait、self_only、yes 混合序列：错误/等待/纯自消息不增加连续 no；明确 yes 或更晚强信号结束连续 no；任务判断覆盖水位早于重置水位时不能算作重置后的判断。
- [ ] 强信号重投测试：第一次未命中名字，配置改变后同一来源重投仍返回 False，首次派生标志不变；跨账号/会话不混算。
- [ ] 替换迁移测试为“新库创建为 3、旧库拒绝且字节内容不变、已存在新库可重启”的测试。删除失效入口前搜索所有引用并在任务 8 更新说明；不运行真实迁移或删除命令。
- [ ] 跑 `apps/argus/test/dao apps/argus/test/connector apps/argus/test/entity apps/argus/test/utils`。

**验收**：等待可恢复、发送未知仍阻塞；更新展示状态不改变判断/交付时间；两次判断轨迹均可查。

## 任务 3：建立证据包，提供真实且有界的工作上下文

**Files**
- Create: `apps/argus/src/evidence/__init__.py`、`icarus_state.py`、`recent_activity.py`、`qq/__init__.py`、`qq/qq_conversation.py`
- Create: `apps/argus/src/utils/qq_transcript.py`
- Modify: `apps/argus/src/entity/evidence.py`、`entity/decision.py`、`dao/observation_store.py`、`dao/batch_store.py`、`clients/icarus_client.py`
- Modify: `apps/argus/src/service/qq_service.py` 和使用 transcript 的策略
- Remove: `apps/argus/src/service/qq_materials.py`（更新全部调用之后）
- Test: `apps/argus/test/evidence/test_icarus_state.py`、`test_recent_activity.py`、`qq/test_qq_conversation.py`、`apps/argus/test/utils/test_qq_transcript.py`、`apps/argus/test/clients/test_icarus_client.py`

**Interfaces**

复用并扩展 `TaskSnapshot`，不另造同义的会话类型。新增 `TaskEvidence`、`ConversationEvidence` 和 `RecentActivity` 放 `entity/evidence.py`：

```python
@dataclass(frozen=True)
class TaskEvidence:
    task_id: str
    lifecycle: str | None
    status_known: bool

@dataclass(frozen=True)
class RecentActivity:
    last_judged_at: datetime | None
    consecutive_no_action: int
    last_submit_at: datetime | None
    submissions_in_window: int
    last_delivery_at: datetime | None
    reset_seq: int

@dataclass(frozen=True)
class ConversationEvidence:
    current: tuple[Observation, ...]
    preceding: tuple[Observation, ...]
    following: tuple[Observation, ...]
    through_seq: int
    human_through_seq: int
    truncated: bool
```

`TaskSnapshot` 保留现有 workspace/session/lifecycle/checked_at，增加 `tasks: tuple[TaskEvidence,...]`、`queued_task_count`、`pending_event_count`、`pending_plugin_event_count`、`background_work_count`、`history_records`（只保留允许的字段）、`history_cursor`、`history_complete`、`stable`。active_task_ids 可继续保留为原始证据；不设置“第一个就是目标”的属性。

读取接口：

```python
async def IcarusStateReader.evaluate(self, *, batch: ObservationBatch) -> TaskSnapshot: ...
async def QQConversationReader.evaluate(self, *, batch: ObservationBatch) -> ConversationEvidence: ...
async def RecentActivityReader.evaluate(self, *, batch: ObservationBatch,
                                       through_seq: int, now: datetime) -> RecentActivity: ...
```

只读查询按 batch 身份绑定 scope；供未关闭候选查询的 DAO 方法使用显式 platform/account_id/subject/through_seq/now/window_seconds 参数，不伪造 ObservationBatch。

- [ ] 先写状态矩阵失败测试：`active_task_ids=['queued','running']`，task 查询分别返回 accepted/running，结果保持两种状态且不按数组顺序选目标；queued_task_count>0 时不宣称可直接追加。
- [ ] 使用假网关验证以下边界：

```python
# 假网关依次返回相同身份的两个 session 快照。
# task.get_status 抛 GatewayBusinessError('task_status_unavailable', ...)
evidence = await reader.evaluate(batch=batch)
assert evidence.tasks[0].status_known is False
assert evidence.tasks[0].lifecycle is None
# 禁止将 unavailable 伪造成 completed/空闲。
```

- [ ] 实现“两次会话快照夹住读取”的核实：session.get -> 对明确 ID 查 task 状态/有界历史 -> session.get。比较身份、lifecycle、active ID 集合和工作计数；变化则 stable=False。网络故障抛出；session_not_found 可以返回 absent，workspace/session 不符必须报错。
- [ ] `task.get_status` lifecycle 只使用现有 accepted/running/completed/failed/cancelled/interrupted；queue_position 不作为实时排队名次，run_id 为空不表示未运行。
- [ ] 改进 `IcarusClient.session_get_history`，保留原分页能力，同时返回 `history_cursor` 和 `has_more`。读取近期历史以已知 cursor 的有界尾窗为主（最多 200 条），按真实 sequence 前进，不用条数推算。缺少目标任务原请求时，优先从本库已交付参数按 task ID 查找；不得按文本相似度匹配。找不到则标明不完整，不能虚构任务内容。
- [ ] 历史只允许 user.message/user.correction、task.accepted/started/finished、assistant.message、tool.started/completed 的必要字段；排除 thinking、完整工具 arguments 和结果文件。每条可读文本最多 4000 字符，总历史最多 20000 字符，截断标志随证据保留。tool.completed 只表示该工具完成，不表示 QQ 已发送；assistant.message 不必然是最终回复。
- [ ] 会话记录读取在一次本地只读事务中固定水位：本批成员 + 前 10 条 + 后续最多 20 条（多于 20 时首 10/末 10），明确截断。后续消息即使属于后续 ready 批次也可作背景；不改变归属。水位是已查询范围，不是“这些消息已处理”的标记。
- [ ] 将 transcript 迁入 utils，给当前/前序/后到背景分别渲染标签；`[图片]` 保持占位并明确当前判断未读取图片内容，不能称其一定为表情包。
- [ ] 将 `QQBatchFactsPolicy` 的纯事实提取与证据 reader 对齐；不要在证据中给出 yes/no。近期活动按任务 2 的查询生成，不读全局配置、不写表。
- [ ] 跑 evidence、utils、clients 与现有 service 测试。新增“历史读取导致远端恢复，第二次快照变化”“历史截断没有目标任务”“同群不同账号隔离”测试。

**验收**：模型可区分本批内容和后到背景；状态未知不会变成无任务；自身可见发言与提交回执不混淆；证据读取无 Argus 业务写动作。

## 任务 4：把时间规则做成可单独测试的策略

**Files**
- Modify: `apps/argus/src/strategy/qq/qq_batch_policy.py`、`apps/argus/src/config/config_model.py`
- Test: `apps/argus/test/strategy/qq/test_qq_batch_policy.py`、新增 `test_qq_pacing.py`、`apps/argus/test/config/test_config.py`

**Interfaces**

沿用 `QQBatchPolicy` 与 `BatchWindowResult(action, deadline, reason)`；`evaluate` 改为明确关键字参数，不需要一个塞满所有服务的大 context：

```python
async def evaluate(self, *, messages, opened_at, last_human_at, now,
                   human_pending_count, has_strong_signal,
                   activity: RecentActivity) -> BatchWindowResult: ...
```

参数在构造时显式注入。`ArgusConfig` 默认：batch_quiet_seconds=1、batch_max_messages=20、batch_max_seconds=60，新增 no_action_backoff_base_seconds=15、no_action_backoff_start_count=2、no_action_backoff_cap_seconds=300、submission_window_seconds=300、submission_spacing_seconds=15、pacing_bypass_messages=6、waiting_check_seconds=5。浮点字段拒绝 NaN/Infinity/负数，start_count 和 bypass 为正整数。只改配置模型与临时配置测试，不改用户 settings。

算法伪代码使用 UTC 秒，实现时可用 datetime/timedelta：

```python
if human_pending_count == 0:
    return BatchWindowResult('wait', None, 'self_only')
if has_strong_signal or human_pending_count >= bypass_messages:
    return BatchWindowResult('close', None, 'direct_or_pressure')

collect_at = min(last_human_at + quiet_seconds, opened_at + max_seconds)
if len(messages) >= max_messages:
    collect_at = now  # 仅结束收集等待，不擅自绕过退避。
idle_until = activity.last_judged_at
if activity.consecutive_no_action >= start_count:
    delay = min(cap_seconds, base_seconds * 2 ** bounded_exponent)
    idle_until = activity.last_judged_at + delay
submit_until = activity.last_submit_at
if activity.last_submit_at is not None:
    submit_until += min(cap_seconds, spacing_seconds * activity.submissions_in_window)
deadline = max(collect_at, idle_until or collect_at, submit_until or collect_at)
return BatchWindowResult('close' if now >= deadline else 'wait', deadline, 'pacing')
```

指数先依据 cap/base 截断再计算，避免历史计数巨大时构造巨整数。未达到退避起点时 last_judged_at 本身不是新的延迟；成功 submit 计数包含窗口下界。提交次数退出窗口可以缩短 deadline，但普通新消息不能使退避期限向后滑动。

- [ ] 写固定时钟表驱动测试：no 次数 0/1/2/3/6/100000 分别产生 0/0/15/30/240/300 秒空闲退避；一次新提交是 15 秒，steer 不增加次数。
- [ ] 写“60 秒收集截止不能绕过 300 秒退避”的断言；6 条人类消息可绕过，6 条自身消息不行。
- [ ] 写 `test_message_arrival_does_not_slide_backoff`：判断完成 t=100、退避至115，t=110/112 两次新消息仍不改变115；短收集窗口只影响 collect_at。
- [ ] 运行新测试确认失败，按上述判据实现；保留现有单批大小限制，不删除消息或重新归属已关闭批次。
- [ ] 添加无历史只等1秒、私聊立即判、名字命中提前判但不改变回应结论、配置非法数值拒绝测试。
- [ ] 跑 strategy/config 测试。

**验收**：算法是纯计算、可注入时钟、每个 deadline 有可解释原因；不依赖数据库或网络。

## 任务 5：接入调度，持久化待处理批次且不空转

**Files**
- Modify: `apps/argus/src/service/batch_scheduler.py`、`apps/argus/src/main.py`、`apps/argus/src/dao/{observation_store,batch_store}.py`
- Test: `apps/argus/test/service/test_batch_scheduler.py`、`test_batch_order.py`、新增 `test_waiting_scheduler.py`，`apps/argus/test/application/test_batch_health.py`

**Interfaces**

保留 offer/check_due/replenish/done，增加一个注入的异步函数：

```python
# QQService 实现，任务 6 接入；没有 waiting 时不会调用。
async def waiting_changed(batch_id: str, *, now: datetime) -> bool: ...
```

调度器对 waiting heads 到期或新的人类消息到达时调用，True 才将原 ID 入 ready 队列，False 仅更新 next_check_at。所有入队继续受 scheduled 集合和有界队列限制，done 必须 finally 执行。waiting 不改名为 ready 后再丢失原状态，claim 支持原状态即可。

- [ ] 先扩展 DAO 候选读取能力，返回最早前缀、有限范围内的人类积压计数及强信号标志；查询全 scope 的存在性不能只看最前20条。若前缀全是自消息但后面有人类消息，将前缀以 self_only 批次正常结束，再处理下一前缀，不调模型、不改变节奏统计。无人类消息时纯自消息前缀也可用同一零模型路径收口，不创建持续模型任务。
- [ ] 先写 scheduler 测试：前30条自消息、后1条人类消息，不会永久堵塞；同一 head 未完成时后续批次不能越过；队列满仍可补入。
- [ ] 写等待检查抑制测试（用明确计数的注入函数）：

```python
checks = []
async def unchanged(batch_id, *, now):
    checks.append(batch_id)
    return False
# 临时库先保存一个 next_check_at=t+5 的 waiting head。
await scheduler.check_due(now=t.timestamp() + 4)
assert checks == []
await scheduler.check_due(now=t.timestamp() + 5)
assert checks == [batch.id]
assert scheduler.ready.empty()
```

- [ ] 运行失败测试，再接入 recent activity 和纯节奏策略。只在 head 可以形成/处理时计算，不能为 blocked 后继反复请求 Gateway。
- [ ] 新消息到达时唤醒 waiting head：用持久化的人类水位比较，不让仅自身回报重复触发；强信号在当前 head 阻塞时不越过它提交，而是加快 head 重判。
- [ ] `main.py` 显式注入参数与 waiting_changed，保留 worker 只收件/成批、单执行消费者；不得在全局 scheduler 锁内等待模型或 Gateway 网络。
- [ ] 无变化检查返回 False 时将期限推进 now+5；不能把证据 fingerprint 或已判断水位更新为模型尚未见过的内容。轮询自身不追加图轨迹。
- [ ] 跑 scheduler/order/application health 测试。新增停止/取消、队列满恢复、重启恢复 waiting、两会话相互不混状态测试。

**验收**：时间过去不会凭空启动；等待被保留；没有新证据时不反复调用模型；收件不被模型阻塞。

## 任务 6：接通证据、判断、等待和发送前复核

**Files**
- Modify: `apps/argus/src/service/qq_service.py`、`decision_trace.py`
- Modify: `apps/argus/src/strategy/qq/{qq_batch_facts_policy,qq_intervention_policy,qq_jev_intervention_policy,qq_task_relation_policy,qq_action_policy}.py`
- Modify: `apps/argus/src/entity/decision.py`、`apps/argus/src/dao/batch_store.py`
- Test: `apps/argus/test/service/{test_qq_graph,test_task_relation,test_batch_dispatch,test_dispatch,test_batch_errors,test_intervention_state}.py`；新增 `test_waiting_flow.py`

**Interfaces 与接线**

`evaluate_batch(batch)` 返回通用 GraphRun；service 使用 `run.output` 校验 RouteDecision。材料依赖用显式 kwargs 连接：

```python
NodeBinding(
    'relation', relation.evaluate,
    lambda _, r: {'facts': r['facts'], 'snapshot': r['work'],
                   'conversation': r['conversation']},
    requires=('facts', 'work', 'conversation'),
)
```

路径为：human -> conversation/facts/activity/work -> code ->（必要时 model）-> intervene；no -> silence；yes -> action/关系判断 -> end。各输入来源用具名绑定函数代替长 lambda 嵌套。graph 顺序执行，但不应为了“取证齐全”让纯自消息去请求 Gateway/Jev。

`QQInterventionPolicy.evaluate(*, facts, snapshot, activity)`：无真人为 no；明确私聊/@ 且无近期工作和交付可以代码 yes；有正在处理、排队、等待重判或近期交付时为 undetermined，让模型看完整上下文。名字命中只改时机，不直接 yes。

模型回应问题仍返回 Choice yes/no；任务正在忙且唯一目标可追加时，关系问题改成 Choice steer/wait，要求基于**本批是否适合补入当前工作**，不以新话题直接新建任务。忙碌但排队/身份不稳定/材料不足时可以代码 wait，不编造关系答案。

动作策略的优先级：

```python
if intervention.verdict == 'no':
    return RouteDecision('silence', 'not_required')
if not snapshot.stable or has_unknown_work(snapshot):
    return RouteDecision('wait', 'work_state_unresolved')
if snapshot.queued_task_count > 0 or has_accepted_task(snapshot):
    return RouteDecision('wait', 'queued_work')
if exactly_one_running_task(snapshot):
    return RouteDecision('steer', 'supplement', task_id) if relation.verdict == 'steer' else RouteDecision('wait', 'busy')
if confirmed_idle(snapshot):
    return RouteDecision('submit', 'response_needed')
return RouteDecision('wait', 'work_state_unresolved')
```

这些判据作为 `qq_action_policy.py` 内有类型的普通 helper，不放进图引擎。confirmed_idle 需要稳定快照、无非终态任务、排队及 pending/background 计数为0、lifecycle 为 ready/unloaded/absent；running 与计数不一致时保守等待。terminal task 状态可结合历史，但不可用状态不能伪造终态。

证据 fingerprint：规范化 JSON 后 SHA256，包含 workspace/session、按ID排序的任务状态、工作计数、lifecycle、history cursor、人类消息水位及完整性标志；不包含 checked_at、last_activity_at 或当前时间。`waiting_changed` 先便宜查会话/task/local人类水位；必要时读有界历史 cursor，变化才完整重判。读取历史可能恢复远端状态，需按任务3再次核对。

- [ ] 先更新 `test_task_relation.py`：原“有任务+new_topic -> submit”的断言替换为“适合追加 -> steer；不适合 -> waiting”；加 accepted+running+queued 的矩阵。运行确认旧逻辑失败。
- [ ] 新增“没任务但近期已经交付，模型 no -> 不提交”和“正常新追问模型 yes -> 沿用会话再次提交”，不能用全局次数限制代替语义判断。
- [ ] 将 Jev state 分成 current_pending、preceding、following_background、current_work、recent_delivery，明确背景非执行请求，历史文本是数据不是指令。传实际取得的请求和进度，不传不可获取的 token 流；历史不足时标记，不自动请求写工具补齐。
- [ ] 实现等待返回和保存：next_check_at=now+5，fingerprint 使用本次模型看到的材料；no 正常结束，wait 不能以 finished 保存；纯 self_only 不计 no。
- [ ] `process_batch` 先以会话锁核实最早 head，再 claim；waiting 无变化只推迟便宜检查，不运行图。图失败仍 failed，原因可查，不作为普通 no 或成功等待。
- [ ] 执行副作用前重新核实任务和本地人类水位。新的人类消息到达或任务变化，将已定动作转 waiting，下一轮重新判断；自消息不导致永久反复重判。service 每次只做一次实际 submit/steer 尝试，不在一个循环里自动改动作。
- [ ] 删除旧 `target_finished/steer_rejected -> submit` 自动转换。明确 `not_running/already_finished` 表示未追加，转 waiting；网络断开/超时/不可信回执转 unknown 并阻塞。若再判同一 payload 同一方法/目标，复用 operation ID；明确拒绝后改了 payload 的同方法重试用新 attempt 后缀（由已保存尝试记录生成），不重复使用幂等键传不同参数。
- [ ] 发送意图和准确 payload 在网络调用前持久化；出现决定期间进程崩溃、接受后响应丢失、旧 decided 恢复时都测试：已交付不重放，submitting 重启变 unknown，decided 执行前重验。
- [ ] 请求只带当前固定成员和必要的前情说明，不把 following_background 混进执行载荷。只有证据证实 QQ 发言时才说“已回复”；否则说“此前已交付/已完成任务”，任务完成不保证QQ发送。
- [ ] 跑全部 service 测试以及 `test_batch_flow.py`，保证提交/追加失败不会破坏原 HTTP 持久收件语义。

**验收**：有新请求且当前忙可以等待；当前空闲不会等待一个永不存在的任务；任务结束不能导致旧请求无条件重发。

## 任务 7：用完整时间线验证等待、恢复和正确回应

**Files**
- Create: `apps/argus/test/application/test_pacing_conversation_flow.py`
- Create: `apps/argus/test/application/test_waiting_recovery_flow.py`
- Create: `apps/argus/test/application/conversation_fakes.py`
- Modify: 现有 `apps/argus/test/application/test_batch_flow.py`、`test_qq_flow.py`

**测试工具接口**

`conversation_fakes.py` 提供 FakeClock(now/advance)、FakeGateway（按现有 RPC 字段记录 sessions/tasks/history/submissions/steers）、FakeJudge（按问题名返回 typed ChoiceAnswer 并计数）。不实现第二套业务算法。`make_flow(tmp_path)` 返回测试专用 `Flow` dataclass，其字段为 `store,batches,scheduler,service,gateway,judge,clock`；`receive(flow, *, text, sender_id='7', private=False, at=False)` 经真实 QQHandler/DAO/scheduler 收件，`drain(flow)` 只执行当前 ready 队列直至为空，不推进假时间。FakeGateway 提供 `set_running(task_id, *, request)` 与 `finish(task_id)` 同时更新符合现有合约的 session/task/history，FakeJudge 提供 `set_choices(**choices)` 和 `call_count`。每个测试 finally 关闭临时 store，避免连接泄漏。

- [ ] 先写会失败的“等待后无人再发消息”完整流程：

```python
async def exercise():
    flow = await make_flow(tmp_path)
    flow.gateway.set_running('task-a', request='正在处理的原请求')
    flow.judge.set_choices(intervene='yes', relation='wait')
    obs = await receive(flow, text='另外请帮我看看这个问题', private=True)
    await drain(flow)
    state = await flow.store.status_details(obs.id)
    assert state['status'] == 'waiting'
    assert flow.gateway.submissions == []
    calls = flow.judge.call_count
    flow.clock.advance(5)
    await flow.scheduler.check_due()
    await drain(flow)
    assert flow.judge.call_count == calls  # 无变化不重复问模型
    flow.gateway.finish('task-a')
    flow.clock.advance(5)
    await flow.scheduler.check_due()
    await drain(flow)
    assert len(flow.gateway.submissions) == 1
```

- [ ] 补齐“任务已回答了等待中的问题，模型 no -> finished 且零新提交”的对照，不能只测任务结束必提交。
- [ ] 写等待后取消/纠正：后到“不要做了”唤醒 head，模型据此结束旧请求；取消句之后仍按新批次判定，不被假装已交付。新请求如果是另一个真正问题，不因上一批沉默而丢失。
- [ ] 写停止恢复：waiting 保存后关闭/重开临时库，固定成员/next_check/fingerprint保留；judging中断恢复为原状态；成功提交后重启不会重放。
- [ ] 写模型阻塞期间到新消息：用 asyncio.Event 控制假模型；返回时不能把新水位写成旧模型看过；执行前复核转 wait 后再次处理。强信号重置也覆盖“旧判断晚返回”的顺序。
- [ ] 写活跃流量边界：连续人类消息可触发6条旁路；机器人自消息不会旁路；已结束对话后新追问允许第二次提交，不断言固定时间段最多一次。
- [ ] 写多账户相同 group_id、队列满、等待head阻塞后继、其他客户端新增排队任务的场景。
- [ ] 跑 application 测试，再跑 `make test-argus`。修本轮回归，不修无关应用失败；完整输出记录在实施结果中。

**验收**：确定性测试覆盖设计 §9 示例的合法处理分支；这些测试证明流程正确，不宣称假模型测试证明真实判断质量。

## 任务 8：开放安全诊断并更新当前文档

**Files**
- Modify: `apps/argus/src/service/decision_trace.py`、`apps/argus/src/dao/observation_store.py`、`apps/argus/src/main.py`
- Test: `apps/argus/test/service/test_decision_trace.py`、新增 `apps/argus/test/application/test_decision_status.py`
- Modify: `apps/argus/README.md`
- Modify after implementation: `apps/argus/docs/spec/2026-09-30-judgement-graph/arch.md`、`apps/argus/docs/spec/2026-09-30-module-naming/design.md`
- Create after implementation: `apps/argus/docs/spec/2026-10-01-evidence-and-pacing/arch.md`

**Interfaces**

保留本机 `GET /observations/{id}/status`，新增 `next_check_at`、`wait_reason` 与分页 `runs`。参数 `after_run=0, limit=20`（1–100），响应 `next_after_run/has_more`，不改变原 batch/session/task/route/status 字段语义；尚未归批仍 pending。只返回经 safe_trace 生成的数据，不返回 params 或原始证据。

安全摘要白名单：run_id、时间、图状态、节点id/status/duration/iteration/error_type、选中的边、route action/固定reason/target、证据条数和截断/状态是否完整。Jev返回文本不直接进 reason；仅使用固定原因码。不从任意 dataclass 自动 asdict 入日志。

- [ ] 写安全测试：给图输出/证据注入字符串 `DO_NOT_EXPOSE_SECRET`，生成状态响应后断言：

```python
assert 'DO_NOT_EXPOSE_SECRET' not in json.dumps(response)
assert 'params' not in response
assert response['status'] == 'waiting'
assert response['runs'][0]['status'] == 'completed'
assert response['has_more'] is True  # 两轮记录，limit=1
```

- [ ] 运行失败测试，实现摘要序列化、DAO分页读取和端点参数校验。waiting可见原因/期限，unknown可见阻塞原因，图失败和正常沉默保持不同状态。
- [ ] README 更新新参数、无主动找话、waiting语义、查看方式、新库要求；删除已失效旧迁移命令，不加入自动删除实际库命令。补充接入层修复：验签及账号核实后，已知非聊天事件返回空204、不落库不触发判断；格式错误聊天仍422，认证失败仍401/403。用connector参数化测试覆盖心跳、生命周期、通知、请求、无效事件与签名。
- [ ] 实现与测试完成后再更新旧 arch 的当前描述，并新写本特性的 arch；注明真QQ/付费模型冒烟是否实际执行，不复用旧文档的过期测试结论。命名说明增加 evidence，不把过去所有历史文档重写。
- [ ] 收尾运行最小新增测试、`make test-argus`、compileall、`git diff --check`；`git status --short`确认两个用户settings未改。新建未跟踪文件单独执行 `git diff --no-index --check /dev/null <文件>` 检查空白。
- [ ] 输出实施结果：完成任务、通过/失败测试、未执行外部验证、schema变化以及需要用户单独批准的部署操作。不得自动启动、清库或提交。

---

## 设计覆盖与复核清单

| 设计要求 | 实施任务 |
|---|---|
| 证据/策略/service边界、文本工具迁移 | 1、3 |
| 图接口、控制边和依赖、执行状态、取消与重复 | 1 |
| 真实工作状态、历史限额、事实与回复区分 | 3、6 |
| 1秒收集、15秒退避、密度退让、6条旁路 | 4、5 |
| wait与silence分开、head保序、无人再说话也处理等待 | 2、5、6、7 |
| 持久化时间、强信号清零、重启恢复 | 2、5、7 |
| 执行前竞态、明确拒绝与unknown、幂等 | 6、7 |
| 安全轨迹、状态入口、评价不依赖提交频率 | 7、8 |
| 测试阶段不兼容旧库、不动真实配置数据 | 0、2、8 |

实施过程中如果发现现有 Gateway 无法提供设计必需的证据，先停在对应任务报告缺口，不通过跨层读取 Agent 内部状态绕过协议。不新增另一套会话/任务管理器。

## 执行前复核结论

已根据现有代码核对图接口、收件/成批生命周期、状态表、测试入口及 Gateway 状态/历史字段。第二轮补清了等待检查频率、状态无变化时不重复调用模型、排队状态优先级、首次成功提交的间隔、强信号持久化与读取水位边界；未改变用户确定的功能方向。

实施记录：任务 1–8 的功能已经接通并完成隔离验证，当前未提交；详细结果见 [arch.md](arch.md)。原步骤保留用于复核，未将未逐项保存证据的操作机械全部勾选。

- [x] 任务 1：通用图出口、关键字输入、独立执行状态、分支与预算回归。
- [x] 任务 2：schema v3、等待与恢复、固定顺序、强信号、独立时间与发送尝试。
- [x] 任务 3：证据包、有界聊天与工作历史、准确任务身份、精确已提交载荷。
- [x] 任务 4：收集/退避/启动密度分离，参数校验与边界测试。
- [x] 任务 5：等待唤醒、有界补入、self-only零模型路径、完成head后补入。
- [x] 任务 6：上下文介入、追加或等待、最终状态和水位复核、未知不重发。
- [x] 任务 7：等待后无人发消息、已解决、取消、重启、正常追问和判断中来新消息的场景。
- [x] 任务 8：分页安全诊断、README、当前架构和命名文档。

审查修复：四个重要问题均经回归测试 RED→GREEN；另外补齐明确无效参数的失败分类与本库精确请求优先规则。最终 `make test-argus` 为153通过，编译与diff检查通过。真实QQ/模型测试、部署、提交均未执行。

执行调整：证据reader移除旧入口随任务6接线完成，避免增加短期适配层；任务5/6的接口连续集成。未创建worktree/分支，保留未提交修改及执行记录，以遵守仓库约定。
