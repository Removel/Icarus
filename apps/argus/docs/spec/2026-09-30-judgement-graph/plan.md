# Argus 决策图与消息批次实施计划

> 执行记录：已落地批次/图路径并通过 106 项 Argus 自动测试。实施中用户追加要求“迁移后删除兼容代码”，因此旧 observation 处理方法、旧 Jev decide 入口和 QQStrategy 已移除；历史裁决/错误先转存 batch 再删除旧列。当前实现与限制以 [arch.md](arch.md) 为准；真实库尚未迁移，未重启服务。下文保留原任务拆分，不能把旧过渡步骤视为当前接口。

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:executing-plans`（当前会话逐项实现），或在用户选择后使用 `superpowers:subagent-driven-development`。按复选框记录实际完成步骤。此文待评审，不是开始实现、迁移真实数据库或重启服务的授权。

**Goal:** 以一批消息作为注意力判断单位，由 QQService 手工组装类型化决策图，代码优先、Jev 处理不确定判断，最终安全执行 silence/submit/steer。

**Architecture:** 保留 Observation→queue→worker 正常路径和 DB 收存确认，将成批计时与网络判断分离。BasePolicy 只规定异步判断接口；Graph 定义边/输入/依赖；GraphRunner 管执行状态与失败传播；JevClient 保留 SDK 多类型答案。批次保存固定成员、决定和提交意图，不以 observation 状态或“判断水位”猜测是否应当重放。

**Tech Stack:** Python ≥3.11、现有 FastAPI、SQLAlchemy[asyncio] 2.x、aiosqlite、TypeSafe SDK 0.7.2、websockets、pytest；不新增 LangGraph 或图 DSL 依赖。

**Spec:** 同目录 [design.md](design.md)，是本计划唯一的功能设计依据；该稿含供用户评审的首版默认规则。

**Prerequisite:** 先执行 [模块命名计划](../2026-09-30-module-naming/plan.md)，验证重命名后功能不变。下面所有路径按 snake_case 写，尚不存在的新路径不是遗漏。命名与功能分开验收、分开提交（仅用户要求提交时）。

## Global Constraints

- 不改 Agent/Gateway、SnowLuma、`packages/`；不实现记忆/知识 SDK；不创建真实远程项目或调用真实模型/QQ。
- `BasePolicy` 在 `strategy/base_policy.py`；类名 PascalCase，文件/包 snake_case；图组装必须在 service，不放 main。
- 判断结果与运行状态分离；代码 `undetermined` 不是故障。中间 yes 不结束图；最终路由仅 silence/submit/steer。
- 图内允许只读材料，不允许 Gateway submit/steer/外部写入；外部动作由 service 在最终决定后执行。
- 不设最短调用间隔；默认群静默 5 秒、20 条/60 秒先到先关闭，私聊人类消息即时。批次上限不等于每分钟最多一次调用。
- 已收存消息不因 300 秒年龄自动 silence；将来源时间交给策略。当前语义改动应由测试钉住，不保留旧 expired if。
- Jev 共最多三次尝试、每次 2 秒（SDK retry=0）；图默认总预算 30 秒/32 次执行；故障默认阻断依赖路径并 warn，不伪装为正常 silence。
- queue 保持有界，worker 消费 Observation；service 收件方法不等待模型运行。批次执行与收件解耦且同 subject 保序。
- 数据库升级需显式维护流程和 SQLite backup API；不得直接改用户运行库。schema 版本存 PRAGMA user_version，首版三张业务表，不增加水位表。
- 外部结果 unknown 不自动重发；steer 返回 accepted 才算已交付；未知不转 submit。
- 保留用户本机 settings、已有 Agent 设置和备份、未提交设计修订；不读取密钥。不创建分支/commit/amend/push，除非另外授权。
- pytest 功能测试、原生 assert、临时目录/数据库/可控时钟/假网络。禁止用实际 QQ/模型连接验证单元测试。

## Review Focus

1. 一批关闭/判断/提交过程中崩溃：成员、batch_id、操作 ID 必须稳定且不重复发送（Task 4、7）。
2. 条件未选中的分支被错误当作失败，或中间介入 yes 提前短路：必须能走到动作节点（Task 2）。
3. 有限队列满、后续新消息不停、恢复乱序：不能导致定时器失效、收件阻塞或已确认消息漏批（Task 5、8）。
4. Jev 多问题包含 Noul/Score，SDK 输出缺失/类型错：不能一律 `.choice`、不能叠九次重试、不能变正常否（Task 3）。
5. task 在判断与 steer 之间结束，或旧库已有 unknown：只能对明确拒绝有限重路由，不能盲目 submit（Task 4、7）。

---

## 文件地图

以下除特别说明外都在 `apps/argus/`：

| 文件 | 职责 |
|---|---|
| `src/entity/decision.py` | ObservationBatch、领域输入/输出、RouteDecision；不含 ORM/SDK 客户端 |
| `src/strategy/base_policy.py` | BasePolicy 泛型基类，仅 evaluate |
| `src/decision_graph/graph.py` | NodeBinding、Edge、DecisionGraph、RepeatBlock、只读结果视图 |
| `src/decision_graph/runner.py` | GraphRunner、NodeExecution/GraphRun、验证与日志 |
| `src/clients/jev_client.py` | 新 typed ask，保留单 Choice decide 的兼容入口 |
| `src/dao/schema.py` | SQLite schema 版本检查、备份和升级 |
| `src/dao/entities.py` | 增 ingest_seq/batch_id 与 DecisionBatchRow；保留旧数据列 |
| `src/dao/batch_store.py` | 固定成员/状态转换/操作意图，复用 ObservationStore 的 engine/session 生命周期 |
| `src/dao/observation_store.py` | 收存序分配、未归属读取、批次历史读取及旧 API 过渡 |
| `src/service/batch_scheduler.py` | 按 subject 的缓冲、deadline、图执行排队，不做业务判断 |
| `src/service/qq_materials.py` | 对 Gateway 状态/历史的只读节点 |
| `src/service/qq_service.py` | build_graph、receive、check_due、process_batch、执行最终路由 |
| `src/strategy/qq/*.py` | 一文件一具体判断策略，见 Task 6 |
| `src/clients/icarus_client.py` | 新增状态查询、history、steer 窄方法，不改服务端 |
| `src/main.py`、`queue.py`、`worker.py` | 接线与生命周期；入站队列不替换为数据库消费 |
| `src/config/config_model.py` | 分组与图预算配置校验，保留已有设置 |
| `scripts/migrate.sh`、`src/migrate.py` | 明确的离线维护入口（非正常启动隐式升级） |
| `test/...` | 镜像上述模块；不增全局外部服务测试依赖 |

### Task 1: BasePolicy 与公共类型

**Files:** 新建 `src/entity/decision.py`、`src/strategy/base_policy.py`、`test/strategy/test_base_policy.py`、`test/entity/test_decision.py`；新建包的 `__init__.py`。

**Interfaces:**

```python
InputT = TypeVar('InputT')
OutputT = TypeVar('OutputT')

class BasePolicy(ABC, Generic[InputT, OutputT]):
    name: str
    version: str

    @abstractmethod
    async def evaluate(self, value: InputT) -> OutputT:
        raise NotImplementedError

@dataclass(frozen=True)
class RouteDecision:
    action: Literal['silence', 'submit', 'steer']
    reason: str
    target_task_id: str | None = None
```

`ObservationBatch` 字段为 id、platform、account_id、subject、messages: tuple[Observation,...]、opened_at/closed_at；`InterventionResult` 为 verdict: yes/no/undetermined、reason；`TaskRelationResult` 为 supplement/new_topic/undetermined、reason。`TaskSnapshot` 包含 workspace_path/key/session_id、active_task_ids、lifecycle、checked_at，不假设有任务意味着本批次为补充。不可变类型用 dataclass，运行态不是这些类型的字段。

- [ ] **RED：**测试基类不能直接实例化、带 bool 输出的策略和带 InterventionResult 输出的策略均可调用。测试错误 route：steer 缺 target_task_id 不允许，非 steer 携带 target_task_id 拒绝（在 `__post_init__` 校验）。示例：

```python
class HumanPresent(BasePolicy[tuple[bool, ...], bool]):
    name, version = 'human-present', '1'
    async def evaluate(self, value):
        return any(not is_self for is_self in value)

def test_code_policy_returns_typed_result():
    assert asyncio.run(HumanPresent().evaluate((True, False))) is True

def test_steer_requires_target():
    with pytest.raises(ValueError):
        RouteDecision('steer', 'supplement')
```

运行 `apps/argus/.venv/bin/python -m pytest apps/argus/test/strategy/test_base_policy.py apps/argus/test/entity/test_decision.py -q`，预期缺实现而失败。
- [ ] **GREEN：**按接口实现；batch 构造拒绝空 messages、跨账号/subject 混入；批次内容不复制 ORM 行。clock/client/DB 不放在实体里。
- [ ] 运行最小测试与 Argus 隔离套件，预期通过。隔离启动器沿用命名计划，使旧 `test_main.py` 不读取真实启用的 QQ 配置。

### Task 2: 图定义、输入映射、条件边与执行器

**Files:** 新建 `src/decision_graph/{graph,runner}.py`、`test/decision_graph/{test_graph,test_runner,test_repeat}.py`。

**Interfaces:** `NodeBinding(id, evaluate, bind_input, requires=(), join='all', timeout_seconds=...)`；`Edge(source, target, condition)`；`DecisionGraph(nodes, edges, entry, output)`；`GraphRunner.run(graph, input, *, max_steps=32, timeout_seconds=30) -> GraphRun`。`GraphRun` 包含 status=completed/failed、decision、只读 node_executions。每条 NodeExecution 包含 id、iteration、status、output、error_type、duration；失败的 output 不可取。输入构造器 `bind_input(root, results)` 显式读取前驱值。

- [ ] **RED：**只用内存节点测试，不接 QQ、网络或 DB。首例：代码判断输出 yes，后继 route 给 submit，断言 route 确实被调用；这能发现中间 yes 错当终态。示意构图：

```python
async def yes(_):
    return InterventionResult('yes', 'explicit-address')
async def route(value):
    assert value.verdict == 'yes'
    return RouteDecision('submit', 'no-active-task')

graph = DecisionGraph(
    nodes=(
        NodeBinding('check', yes, lambda root, results: root),
        NodeBinding('route', route, lambda root, results: results['check'], requires=('check',)),
    ),
    edges=(Edge('check', 'route', lambda value: value.verdict == 'yes'),),
    entry='check', output='route',
)
run = asyncio.run(GraphRunner().run(graph, None))
assert run.status == 'completed' and run.decision.action == 'submit'
```

- [ ] 再写失败 case：重复 ID/悬空边/普通回边在编译阶段拒绝；条件 no 路径未选中是 skipped；all 依赖失败→blocked；any 分支恰好一个成功→可汇合，冲突两个成功→failed；一个分支出错不能取消与它无依赖的只读节点，但最终动作依赖缺失则失败 silence。用 `pytest caplog` 断言 warn 有 run/node/error_type、不含输入正文。运行 `... -m pytest apps/argus/test/decision_graph -q` 看红灯。
- [ ] **GREEN：**runner 分开维护 activated/readiness/executions；用节点 ID 的独立输出，不任意 merge 全局 dict。执行前检查图连通与普通环；顺序执行就绪节点、通过条件函数激活后继，未选中状态传播为 skipped；每步 await 超时，整体 `asyncio.timeout` 包围。节点异常记录 failed；`CancelledError` 原样传播。没有最终输出或输出不合法生成 status=failed、RouteDecision(silence,'graph_failed')。
- [ ] **RED→GREEN：**`RepeatBlock(body_graph, next_input, until, max_iterations)` 作为单显式执行单元支持受限循环。body 每轮新结果命名空间，下一轮只吃 next_input；测试 counter 0→2 两轮退出、前轮答案不会混入新轮、达到上限失败、body 节点每次均计入总预算。不要用全局 visited 去阻止合法重算。
- [ ] 全部 runner 测试、Argus 隔离套件通过。图不是 utils，无 batch store/Gateway 导入。

### Task 3: JevClient 多类型、多问题契约

**Files:** 修改 `src/clients/jev_client.py`；扩展 `test/clients/test_jev_client.py`。

**Interfaces:** 新增 `async ask(state: JSONContent, questions: Mapping[str, Question]) -> SystemOneResponse`，复用 TypeSafe SDK 公共 `Noul/Choice/Score/Answer/SystemOneResponse`；`JevCallError` 只包含安全失败类型/attempts。原 `decide(...) -> Judgement` 保留作旧 service 过渡入口，不叠重试。

- [ ] **RED：**用 SDK 类型构造响应（不构造自己仿制的 answer model），注入一次 transport answer：

```python
response = SystemOneResponse(
    model='fixture', usage=Usage(input_tokens=1, output_tokens=1),
    answers={
        'relevant': NoulAnswer(noul=0.9),
        'relation': ChoiceAnswer(choice='new_topic', confidence=0.8,
                                 probabilities={'new_topic': 0.8, 'supplement': 0.2}),
        'urgency': ScoreAnswer(score=1.5, confidence=0.7,
                              legend={0: 'low', 1: 'medium', 2: 'high'},
                              probabilities={0: 0.1, 1: 0.3, 2: 0.6}),
    },
)
```

断言 ask 保留 noul/score/概率与 usage；一批多问题实际只调用一次；不要读取每个 answer.choice。
- [ ] 故障测试：缺请求键、问题类型和返回类型不符、未知 Choice 标签重试共三次；三次仍失败抛 JevCallError。业务 fallback 由策略层决定；兼容 decide 将错误映射为调用者给的 fallback。超时取消不会在后台留下同步 to_thread 调用（使用现有 AsyncTypeSafeClient）。
- [ ] **GREEN：**一个私有重试函数供 ask 与兼容入口共同使用，SDK retry=0；测试 sleep 注入。Noul 判定阈值交策略配置，Score 排序交调用方，不在 client 内决定 wake。
- [ ] 运行 `... -m pytest apps/argus/test/clients -q` 与隔离全套。保留旧测试注入能力；若注入接口需变更，统一修改假对象且明确区分生产返回和测试替身。

### Task 4: 批次表、固定成员与离线 schema 升级

**Files:** 修改 `src/dao/{entities,observation_store}.py`；新建 `src/dao/{batch_store,schema}.py`、`src/migrate.py`、`scripts/migrate.sh`、`test/dao/{test_batch_store,test_batch_migration}.py`。

**Interfaces:** `BatchStore` 复用 ObservationStore 的 session factory，应用只开一个 engine；`close_batch(scope, member_ids, close_reason) -> ObservationBatch`；`claim_batch(id) -> bool`；`save_decision(id, graph_run)`；`begin_submission(id, method, params, operation_id)`；`mark_receipt(id, response)`；`recover_batches()`。所有状态转换带预期状态条件。`ObservationStore.unassigned(scope, limit)` 按 ingest_seq 升序读；新接收序须在 DB 写事务中产生，不能用时钟推断。

- [ ] **RED：**临时 DB 放三条观察，关闭前两条；断言它们仅有一个 batch_id，第三条仍未归属；再次用同样成员关闭不能造第二批。模拟事务第二步失败，断言既无孤立 batch 也无部分归属。观察事实和 JSON detail 不变。测试形状：

```python
batch = await batches.close_batch(scope, (first.id, second.id), 'quiet')
assert [m.id for m in batch.messages] == [first.id, second.id]
assert [m.id for m in await observations.unassigned(scope, 20)] == [third.id]
```

上述 first/second/third 由现有 QQHandler 合成事件 fixture 生成，时间相同但 DB 接收序不同；scope=(platform,account_id,subject)。
- [ ] **GREEN：**增加 DecisionBatchRow 和 observations.ingest_seq/batch_id。明确成员绑定是权威，不新建水位表。事务内校验同 scope、未归属、收存前缀、有界条数，插 batch+条件 update 成员并核实行数。路径记录 JSON 保留版本/结果摘要，不把密钥或全量聊天正文复制进去。
- [ ] **RED：**迁移测试用手写的旧 DDL 建临时旧库（来自当前 entities，包含 observations 和 session_links），各插一条 pending/processing/submitting/submitted/finished/failed/submission_unknown。给带提交意图者保存原 submission_id，记录 before JSON。运行 schema 升级后检查一对一兼容批次及状态映射，所有已发送历史不重新 ready，重跑升级不变。测试 `submitting`/带 submission_id 的 processing 无 task_id → unknown；没有提交意图的 processing → ready。session_links 和原有 task_id 保留。
- [ ] **GREEN：**`schema.migrate(database_path)` 使用 SQLite backup API，再 `BEGIN IMMEDIATE` 事务升级；PRAGMA user_version 标记完成，失败回滚并保留备份。ingest_seq 根据旧 received_at/id 稳定回填后建唯一索引。新增数据库直接创建新版；正常服务遇旧版报需离线 migrate，不能自动 drop/create。维护命令要求 Argus 已停止，检查相同 data dir 的托管/已知外部进程，无法确认独占就拒绝执行。测试中只用临时库，不触碰真实 `.icarus`。
- [ ] **RED→GREEN：**关闭/重开 DAO 后批次 ID、成员、已存决定/参数不变；恢复 ready/judging 复用原批次；decided 保留决定；submitting 变 unknown；submitted/finished/failed/unknown 不进待执行队列。执行 DAO 套件和隔离全套。

### Task 5: 成批策略与不阻塞收件的调度

**Files:** 新建 `src/strategy/qq/qq_batch_policy.py`、`src/service/batch_scheduler.py`；扩展 `src/config/config_model.py`、`test/strategy/qq/test_qq_batch_policy.py`、`test/service/test_batch_scheduler.py`；不现在重写 main。

**Interfaces:** `QQBatchPolicy.evaluate(BatchWindowInput) -> BatchWindowResult`（wait/close、deadline、reason）；输入包含候选消息、opened_at、last_human_at、now 和静默/条数/最长时间参数。`BatchScheduler.offer(obs)` 只做有界缓冲/调用规则；`check_due(now)` 检查截止；关闭调用 BatchStore 并把 batch_id 放有界 ready-batch 队列。计时和队列机制归 scheduler，判断归 Policy。

- [ ] **RED：**可控时钟不真实 sleep；默认配置下测试人类群消息 t=0/2 → t=6 不关、t=7 静默关闭；第 20 条立即关；持续消息小于 20 条但开批到 t=60 关闭；私聊和人类 at/名字命中 t=0 立即关；名字仅触发成批，不自动介入。自消息 t=6 不把上述人类 deadline 从 7 延到 11；纯自消息批次不触发即时通道。

```python
result = await policy.evaluate(BatchWindowInput(
    messages=messages, opened_at=0.0, last_human_at=2.0,
    now=6.0, quiet_seconds=5.0, max_messages=20, max_seconds=60.0,
))
assert result.action == 'wait' and result.deadline == 7.0
```

- [ ] **GREEN：**三种触发取先到者，选定最早至多 20 条；时钟用注入 monotonic 值，数据库保存 wall time 用于重启计算剩余/已到截止。不得新增最短冷却；按 platform/account/subject 独立实例/状态，不混账号。
- [ ] **RED→GREEN：**blocked fake judge 不影响 scheduler 接收下一消息；两个 subject 的 deadline 独立；关闭时并发新消息属于下一批；入站 queue 乱序时按 DB 未归属前缀恢复候选，不让先到的高序消息跳过低序消息。ready 队列满时批次已 ready 落库，补入循环后续再投，不阻塞 HTTP或撤销归属。
- [ ] 测试恢复：未关闭候选从 DB 重建；旧来源时间不丢弃，超过 300 秒的消息也入批；已固定批次不会重切。运行策略/scheduler 最小测试与隔离全套。

### Task 6: QQ 策略节点与 service 内图编排

**Files:** 新建 `strategy/qq/{qq_self_message_policy,qq_batch_facts_policy,qq_intervention_policy,qq_jev_intervention_policy,qq_task_relation_policy,qq_action_policy}.py`、`service/qq_materials.py`；修改 `service/qq_service.py`；测试 `test/strategy/qq/`、`test/service/test_qq_graph.py`。

**Interfaces:** QQService 的 `_build_graph()` 返回 Task 2 的 DecisionGraph；`evaluate_batch(batch) -> GraphRun` 只运行判断，不提交。readonly reader 只暴露 session_get/task_get_status/get_history。`QQBatchFacts` 包含 type/发言人、结构化提及、称呼信号、转录、当前批次与历史分界；不传主 ORM row。

- [ ] **RED：**构造 fake Jev 计数 + fake reader。纯自消息→silence且零调用；人类私聊文本→yes→无任务→submit 且零调用；群 at + 文本→代码 yes；正文仅提到 icarus 而无 at→undetermined→恰好一次 Jev；群没 at 也不能直接 silence（保留主动介入机会）。大于 300 秒但明确请求的批次不被代码年龄拦截。
- [ ] **GREEN：**将老 QQService 中 self/expired/state 等判断迁入节点：废除 expired if；service 内显式装配节点及条件边。是否有活动任务由 reader 查实，网络错进入失败阻断路径不是空任务。客户端/只读 reader 通过构造注入，不在 BasePolicy 读取全局设置。
- [ ] **RED→GREEN：**关系判断有可靠结构化 task 引用才代码 supplement；无可靠引用且有活动任务调用 Jev supplement/new_topic；没有活动任务不问关系。测试 new_topic 即使同群活动也走 submit，supplement 则 steer 携正确 task_id；task reader 失败后 action 节点 blocked、GraphRun failed/silence 并 warn。混合自消息/人类批次不误过滤全部。
- [ ] **多问题契约验证：**用 Noul、Choice、Score 组成一次 ask 后由三个投影节点取结果，证明一次请求可被多后继使用；再写后一个请求 state 使用前一个答案的用例，证明两次请求按依赖先后发生。这个用例用合成图，不往生产 QQ 图添加没有调用方的评分节点。
- [ ] 执行图/策略/service 测试及隔离全套，旧单消息兼容路径可暂存但 main 不能同时驱动旧路径和新图而重复提交。

### Task 7: 最终动作、已有 Gateway 协议与提交竞态

**Files:** 修改 `src/clients/icarus_client.py`、`src/service/qq_service.py`、`src/dao/batch_store.py`；测试 `test/clients/test_icarus_client.py`、`test/service/test_batch_dispatch.py`。

**Interfaces:** 新增 `task_get_status(workspace_path,session_id,task_id)`、`session_steer(workspace_path,session_id,task_id,prompt,submission_id)`、分页 `session_get_history`；保持现有 `session_get/create/submit`。`QQService.apply_decision(batch, decision)` 写操作意图后调用 RPC，不让图调用外部写方法。

- [ ] **RED：**用本机临时 WS 假服务器验证请求字面结构，不向运行中的 Gateway 提交。测试调用：

```python
await client.session_steer('/work', 's', 't', '补充内容', 'b:steer:t')
assert request['method'] == 'session.steer'
assert request['params'] == {
    'workspace_path': '/work', 'session_id': 's', 'task_id': 't',
    'prompt': '补充内容', 'submission_id': 'b:steer:t',
}
```

fake response status 分别 accepted/not_running/already_finished/未知；不能只要 task_id 就认成功。参考真实协议 `apps/gateway/src/protocol/methods.py` 与 `apps/agent/src/application/agent_runtime.py` 只读，不更改它们。
- [ ] **GREEN：**图返回后再次核实目标状态；正常 submit 复用稳定会话创建意图。steer accepted 记批次 submitted/action=steer；明确 not_running/already_finished 最多重路由一次、保存拒绝证据再改 submit，操作 ID 区分方法和 task。未知不重路由，写 unknown。禁止直接修改运行任务或借 Hook 注入。
- [ ] **RED→GREEN：**模拟 DB reserve 后进程断开、发送后响应丢失、收到 accepted 但 receipt 写失败；重启都不能再发该 uncertain 操作。模拟用户改 workspace、原 session 消失、同 subject 新批到达，目标校验与锁必须防跨 workspace/重复建会话。先出现 blocked 网络时未跨发送边界的决定可待恢复，不重做内容裁决；保存准确阶段。
- [ ] 测试持续订阅不作为必须依赖；以现有状态/history RPC 提供人工排查线索，history 无 submission_id 时不能作肯定或否定提交证明。执行 client/service/DAO 套件与隔离全套。

### Task 8: 生命周期接线、恢复、文档与回归

**Files:** 修改 `src/{main,worker,queue}.py`、`src/config/config_model.py`、README、当前功能 `arch.md`；新增 `test/application/test_batch_flow.py`、`test/application/test_batch_recovery.py`。新增配置默认值写 model；更新配置示例时保留本机 qq enabled/bot_id/路径，不把它们重置或提交。

- [ ] **RED：**合成 HTTP 验签入站→observations→Observation 队列→收件 worker→成批→graph→假 Gateway。发送三条群消息不触发逐条 Jev；推进时钟至静默后一次图判断并发送包含三位/三条发言的转录。追加私聊即时路径；在模型 await 被阻塞时第二条 Observation 仍被正常收存并进入下一候选。
- [ ] **GREEN：**main 只组装依赖和生命周期，不组装业务图（调用 QQService 内 `_build_graph`）。区分 Observation 入站队列与 ready-batch 执行队列，两者均有界；恢复循环只负责补入，worker 不变成 DB 消费者。启动确保 schema 兼容、DB 与执行任务就绪再接受请求；禁用 QQ 不消费其 pending；停机先停止接收/计时、记录阶段并等待或取消判断/执行、再释放 client/DB。
- [ ] **RED→GREEN：**通过真实临时数据库重开模拟故障窗口：候选尚未固定、close 事务后未入 batch 队列、judging、decided、submitting、receipt 后分别恢复；断言 observation 无遗失、成员不漂移、已知成功不重放、unknown 不重发。run cancelled 不被记成正常 silence。health 反映 DB/收件/批次执行任务，不仅 HTTP 端口。
- [ ] **最终回归：**最小测试 → Argus 隔离全套 → 根控制测试 → `make test-agent`、`make test-gateway`、`make test-tui` → `compileall`、`git diff --check`。不要用启用了真实 QQ 的普通全套测试入口直接连接生产。既有 TUI 快照失败按具体名称报告，不修无关问题。
- [ ] **文档交付：**读取本次真实实现与测试后新增本功能 `arch.md`，不把设计里未落实项写成现状；README 说明新 schema 维护步骤、群批次不是限频、私聊即时、未知提交需人工确认、一次 ask 的成本记录，以及“不做自动过期丢弃”。本次只新增/更新所需说明，不重写无关产品定位。

## 自查与验收清单

- [ ] BasePolicy 泛型返回、NodeExecution 状态、RouteDecision 三者无混淆；名字/版本属于策略，不硬编码进 client。
- [ ] 图首轮小批可运行，any/all 条件和 RepeatBlock 每轮输出隔离有测试；代码 yes 不提前短路。
- [ ] 命名统一计划已执行并单独回归，本计划没有再出现 PascalCase 模块路径。
- [ ] 自消息留档供上下文，不延长人类静默；已入队历史正常处理；没有新增群最短间隔。
- [ ] 旧库 migration 的全部状态经过临时 DB 验证；执行者没有清理真实历史或代替用户迁移/重启。
- [ ] Jev Noul/Choice/Score 被原样保留且最多三次请求；无 SDK 内外叠加重试；依赖失败与正常否分开记录。
- [ ] 批次成员、关闭、判断、提交各事务边界有故障注入测试；任务结束竞态不会把未知 steer 变成第二次 submit。
- [ ] 真实模型/QQ/Gateway 写操作冒烟未授权则明确未执行，不能将 fake 结果写成真实链路已验证。

## 执行与交付顺序

1. 用户审阅模块命名计划与本功能修订设计/计划。
2. 独立实施命名整理，验证功能未变。
3. 顺序执行本计划 Task 1–8；每一项有测试证据，设计偏差记执行台账而非暗改语义。
4. 完成一次独立评审后修重要问题；未经新授权不提交、不推送、不升级真实库、不启动 Argus。

推荐沿用原生逐项执行；任务接口相互依赖，避免同时改 service 与 DAO 的不同约定。若用户选择子代理执行，仍使用同一类型/数据库契约，并限制同一文件的并行写入。
