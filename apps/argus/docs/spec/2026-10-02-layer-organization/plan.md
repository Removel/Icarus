# C：层级职责归位与直接接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入改为 connector → handler → service，取消入站队列；事实放 evidence、判断放 strategy、类对象放 entity，service 使用图基座编排。

**Architecture:** service.receive 只持久化并轻量唤醒，模型与投递仍由后台批次消费者执行。service 内部 BatchScheduler 保留收集、退避、等待重查、恢复；ready 批次队列保留，不另做消息总线。图只承载注入 callable：证据在选中边执行，节点消费证据判断。

**Tech Stack:** Python 3.12、FastAPI、asyncio、SQLAlchemy、现有 Gateway/Jev clients、pytest。

**Spec:** [design.md](design.md)。前置：[A plan](../2026-10-02-decision-graph-substrate/plan.md)、[B plan](../2026-10-02-dao-boundary/plan.md) 全量验收完成。

## 执行记录（2026-10-02）

- A/B/C主体改造已实施；当前状态见[现状架构](../2026-10-01-evidence-and-pacing/arch.md)。
- 最终 `make test-argus`：210 passed；命令包含compileall，`git diff --check`通过。
- 原入站queue/worker及4项旧测试经用户逐文件确认后删除；后台批次队列保留。
- 独立review子代理3次均API400未运行；只完成作者自查，不能视为独立审查通过。
- 未创建分支/worktree、未提交/推送、未连接真实服务或读取密钥/真实库。
- 任务中的协同接口迁移合并实施，部分旧行为用例为原有行为固定测试而非新功能RED；实际RED/GREEN与取舍见本次执行记录，不宣称每个示例逐字执行。

## Global Constraints

- 接入调用链固定 `connector → handler → service`；connector 不直接依赖 service、DAO、策略、evidence、queue。
- handler 直接等待 service 的接收入口，不等待后续图判断和投递；204 必须晚于数据库事务提交。
- 删除 InboundQueue 及仅转手的 QueueWorker；保留 `scheduler.ready` 的批次 ID 队列和受生命周期管理的消费者。
- 不新增队列回执、Future、消息信封、策略注册系统、自动发现、DSL 或无界 create_task。
- evidence/strategy 彼此不 import；都可经注入 clients 使用外部 API；graph 不依赖任何业务层。
- 保留 schema v3、QQ 收集/退避/等待、重复/冲突、投递未知不重发、明确追加拒绝不转 submit。
- QQ/模型/Gateway 测试使用 fake、临时库和可控时间；不读取真实密钥、修改真实 settings、启动真实服务。
- 不创建分支/worktree、不提交/推送；提交须用户另行授权。

## Review Focus

1. HTTP 响应早于收存、或远端读取卡住新消息接收——Task 4 用阻塞 Event 验证，不能靠 sleep 猜。
2. 后台队列满/通知失败后持久化事实丢失或被回滚——Task 4/5 恢复测试。
3. self-only 先走证据边而触发远端读取/模型回环——Task 3 计数守卫测试。
4. session.create 的响应丢失、steer 的明确拒绝与未知回执被混淆——Task 2/6 client 和发送事务回归。
5. 等待期间没有新消息、或发送最后查询后到新消息，旧请求被饿死/错误投递——Task 5/6 原场景回归。

---

## 执行方式与命令

次序 C1 → C2 → C3 → C4 → C5 → C6。每任务保持可运行；一项迁移的定义/调用者/测试同一任务更新，不留下旧路径重导出。

全部命令在仓库根执行：

```bash
apps/argus/.venv/bin/python -m pytest apps/argus/test/handler apps/argus/test/connector apps/argus/test/service -q
make test-argus
git diff --check
```

- [ ] 记录 git status 与 A/B 当前验收结果。
- [ ] Run `make test-argus` 保存 C 基线；不要把用户现有改动清掉。

## 文件结构与固定接口

| 文件 | 内容 |
|---|---|
| `entity/evidence.py` | 移入 QQBatchFacts；新增 DecisionEvidence（聊天/工作/活动/事实包） |
| `entity/decision.py` | 移入 BatchWindowResult；保留已有决定/批次 |
| `entity/policy_inputs.py` | 真实判断所需不可变输入，不做通用 PolicyInput 大包 |
| `entity/api_result.py` | DeliveryOutcome；client 结果类型，不含 DAO/图状态 |
| `evidence/qq/{qq_signals,qq_transcript,qq_prompt,qq_facts}.py` | QQ 事实提取、文本表达、prompt 和图证据收集 |
| `strategy/{work_state,decision_trace,batch_lifecycle}.py` | 共用工作态/安全复核、判断轨迹、B 的状态映射 |
| `strategy/qq/*.py` | 统一 evaluate(value) 的实际策略 |
| `service/qq_service.py` | 接收、构图、判断/投递编排 |
| `service/batch_scheduler.py` | service 内部计时、合并唤醒提示、数据库补入；保留 ready 队列 |
| `service/batch_runtime.py` | 现有批次执行/周期检查协程的归属，不新建通用 Worker 框架 |
| `handler/qq_handler.py` | parse + 调注入 service 接收入口 |
| `connector/qq_connector.py` | HTTP/签名/account 边界 + 调 handler |
| `main.py` | 装配、生命周期、健康/状态路由 |

跨任务输入名固定为：

```python
@dataclass(frozen=True)
class BatchTimingInput:
    messages: tuple[Observation, ...]
    opened_at: float
    last_human_at: float | None
    now: float
    human_pending_count: int
    has_strong_signal: bool
    activity: RecentActivity

@dataclass(frozen=True)
class InterventionInput:
    facts: QQBatchFacts
    snapshot: TaskSnapshot
    activity: RecentActivity
    rechecking: bool = False

@dataclass(frozen=True)
class RelationInput:
    facts: QQBatchFacts
    snapshot: TaskSnapshot

@dataclass(frozen=True)
class ActionInput:
    relation: TaskRelationResult
    snapshot: TaskSnapshot

@dataclass(frozen=True)
class DeliveryCheckInput:
    decision: RouteDecision
    snapshot: TaskSnapshot
    human_seq: int
    expected_fingerprint: str | None
    current_fingerprint: str
    final_status: dict | None = None
    final_human_seq: int | None = None
    expected_session_signature: tuple | None = None
    final_session_signature: tuple | None = None

@dataclass(frozen=True)
class DecisionEvidence:
    conversation: ConversationEvidence
    snapshot: TaskSnapshot
    activity: RecentActivity
    facts: QQBatchFacts
    rechecking: bool
```

这些类依赖已有 entity，不 import 具体 reader/策略。frozen 只保护字段赋值，不宣称嵌套 dict 深冻结；构造时复制传入状态/JSON，不共享可变工作缓冲。

### Task 1：事实、prompt、trace 归位，策略契约统一

**Files:**
- Move: `apps/argus/src/utils/{qq_signals,qq_transcript}.py` → `apps/argus/src/evidence/qq/` 同名文件
- Move: `apps/argus/src/service/qq_prompt.py` → `apps/argus/src/evidence/qq/qq_prompt.py`
- Move: `apps/argus/src/service/decision_trace.py` → `apps/argus/src/strategy/decision_trace.py`
- Create: `apps/argus/src/{entity/policy_inputs,evidence/qq/qq_facts,strategy/work_state}.py`
- Modify: `apps/argus/src/entity/{evidence,decision}.py`
- Modify: `apps/argus/src/strategy/base_policy.py` 与 `apps/argus/src/strategy/qq/*.py`
- Modify: `apps/argus/src/{handler/qq_handler,connector/qq_connector,service/qq_service,service/batch_scheduler}.py`
- Delete after moving implementation: `apps/argus/src/strategy/qq/qq_batch_facts_policy.py`
- Move tests: `test/service/test_decision_trace.py` → `test/strategy/test_decision_trace.py`
- Modify tests: `apps/argus/test/application/conversation_fakes.py`（所有后续任务复用的 Flow 增加 `database` 字段，make_flow 返回 B 创建的 Database；关闭统一用 flow.database.close，store 仍指 ObservationStore）
- Create tests: `test/evidence/qq/{test_qq_facts,test_qq_prompt,test_qq_signals}.py`、`test/strategy/qq/test_policy_contracts.py`
- Update existing imports/tests: `test/strategy/qq/{test_qq_batch_policy,test_qq_pacing}.py`、`test/service/{test_intervention_state,test_task_relation,test_observation}.py`、`test/decision_graph/test_trace.py`、`test/application/test_qq_delivery_prompt.py`。

**Interfaces:**
- `BasePolicy[InputT, OutputT].evaluate(value: InputT) -> OutputT`；具体类使用类型参数。
- QQSelfMessagePolicy：ObservationBatch → bool；QQInterventionPolicy：InterventionInput → InterventionResult；QQJevInterventionPolicy：QQBatchFacts → InterventionResult；QQTaskRelationPolicy：RelationInput → TaskRelationResult；QQActionPolicy：ActionInput → RouteDecision；QQBatchPolicy：BatchTimingInput → BatchWindowResult。
- `QQBatchFactsReader(names=()).evaluate(batch: ObservationBatch) -> QQBatchFacts`：原事实组装搬入 evidence，非 BasePolicy。
- `compose_qq_prompt(batch, decision, *, context, has_history, reply_hint=reply_hint) -> str`；reply_hint(subject) 与 prompt 同文件，handler 不再生成它。
- `strategy.work_state` 暂收现有 running_tasks/has_unknown_work/has_queued_work/confirmed_idle；Task 2 收敛 service 的发送复核。

- [ ] **RED：输入契约不是只验证一个自造 ABC 子类，要直接验证实际策略。**

```python
import asyncio
from inspect import signature
from apps.argus.src.entity.policy_inputs import ActionInput
from apps.argus.src.entity.decision import TaskSnapshot, TaskRelationResult
from apps.argus.src.strategy.qq.qq_action_policy import QQActionPolicy
from datetime import UTC, datetime

def test_actual_action_policy_has_uniform_contract():
    policy = QQActionPolicy()
    assert tuple(signature(policy.evaluate).parameters) == ('value',)
    snapshot = TaskSnapshot('/work', None, None, (), 'absent', datetime.now(UTC))
    result = asyncio.run(policy.evaluate(ActionInput(TaskRelationResult('undetermined', 'idle'), snapshot)))
    assert result.action == 'submit'
```

- [ ] 给所有实际策略检查同一 evaluate 参数名；行为断言沿用原私聊/自消息/群不确定/任务关系/退避测试，不只验反射。
- [ ] Run test_policy_contracts，预期签名或输入模块缺失失败。
- [ ] **GREEN：**定义明确输入/输出类，搬真实实现和导入；binder 调 `policy.evaluate(value)`。QQBatchPolicy 继承 BasePolicy；facts reader 不继承。
- [ ] reply_hint 从 handler 移至 evidence/qq/qq_prompt，原 JSON 示例和中文 prompt 内容保持；删除 QQService 的 handler import，调用端同步修改。
- [ ] 工作状态谓词不再住 qq_action_policy；策略互不 import evidence。facts 中显式信号语义与首次强信号区分保持，不能擅自把名称命中等同 explicit_address。
- [ ] prompt 回归包含：已交付前序排除、JSON 引用、8000 字符上文截断、steer 补充提示、不得声称已发 QQ；轨迹继续满足 A 的节点/边安全摘要。
- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/strategy apps/argus/test/evidence apps/argus/test/service/test_observation.py apps/argus/test/application/test_qq_delivery_prompt.py -q`；Run 全量。
- [ ] 核对旧事实 policy/prompt/trace 路径引用消失，真正辅助函数允许留 utils，不再按是否 QQ 专用一刀切。

### Task 2：client 简单操作与发送前判断分层

**Files:**
- Create: `apps/argus/src/entity/api_result.py`
- Modify: `apps/argus/src/clients/icarus_client.py`
- Modify: `apps/argus/src/strategy/work_state.py`
- Modify: `apps/argus/src/evidence/icarus_state.py`
- Modify: `apps/argus/src/service/qq_service.py`
- Test: `apps/argus/test/clients/test_icarus_client.py`
- Create: `apps/argus/test/strategy/test_work_state.py`
- Update fakes: `apps/argus/test/application/conversation_fakes.py`、`apps/argus/test/service/{test_dispatch,test_batch_dispatch,test_gateway,test_intervention_state,test_waiting_flow}.py`

**Interfaces:**
- `DeliveryOutcome(kind: Literal['accepted','rejected','unknown','failed'], receipt: dict | None = None, task_id: str | None = None, error_type: str | None = None)`。
- `IcarusClient.ensure_session(workspace_path, session_id) -> dict`：稳定 ID 的 get/create/重新 get，不生成新 ID，不写 DAO。
- `session_submit` / `session_steer` 原 RPC 参数不变，返回 DeliveryOutcome；call 仍是协议请求，JevClient 不变。
- `DeliveryCheckPolicy(BasePolicy[DeliveryCheckInput, RouteDecision]).evaluate(value)`：安全返回原 decision，不安全返回 wait/reason；实际执行与状态保存仍 service。
- `work_signature(snapshot) -> dict` / `fingerprint(snapshot, human_seq) -> str` 搬至 evidence/icarus_state（事实指纹）。`session_signature` 保持在 evidence/icarus_state 用于事实快照；DeliveryCheckPolicy 不 import 该 reader，而只比较调用侧输入的签名和计数字段。service 绑定数据，不自行作是否发送的判断。
- `DeliveryCheckInput` 已在文件结构节声明 expected_session_signature/final_session_signature；service 调已有 session_signature 构造这些值，再传给 DeliveryCheckPolicy。策略检查缺签名/身份异常不能假设安全；absent 情形仍按 final_status 的 lifecycle/active/counts 规则判断。

- [ ] **RED：client 明确追加拒绝只发一次。**

```python
import asyncio
from apps.argus.src.clients.icarus_client import IcarusClient

def test_steer_rejection_is_typed_and_not_retried():
    async def exercise():
        client = IcarusClient('ws://unused/rpc')
        calls = []
        async def call(method, params):
            calls.append((method, params))
            return {'status': 'already_finished', 'task_id': 't'}
        client.call = call
        result = await client.session_steer('/work', 's', 't', 'more', 'b:steer:t')
        assert result.kind == 'rejected' and result.task_id == 't'
        assert len(calls) == 1 and calls[0][0] == 'session.steer'
    asyncio.run(exercise())
```

- [ ] 增加 transport→unknown、submit 无 task_id→unknown、steer 非 accepted/rejected→unknown、原 `-32602` 等明确业务拒绝→failed；原其他 code→unknown。不是请求失败就都算未发送。
- [ ] Run test_icarus_client，预期原 dict 不满足新结果类型。
- [ ] **GREEN：**回执验证/错误分类迁进 client；不能 auto-retry submit/steer、生成新 submission_id 或把 unknown 变失败。
- [ ] ensure_session 测例覆盖 not_found→create、session_exists→get、create response lost→按同一 ID get、返回错误 session_id→ValueError；已有 link 的 workspace_key 校验仍由当前身份事实核实，不能只检查 session_id。
- [ ] DeliveryCheckPolicy 按原分支顺序判断：持久化指纹变化→evidence_changed；submit 非确认空闲→work_state_unresolved；steer 非唯一 running/有排队/unknown/目标不符→target_changed；final status 或人类水位变化→evidence_changed。absent→新建 ready/unloaded 的允许范围保持原判断，不要求新会话 ID 等于 absent 的 None。
- [ ] 对 queued/unknown、相同 running 目标、final 查询后新消息参数化测试；service 不再内联这些工作态 if，调用策略并执行其结果。
- [ ] service 保留 ID 生成、save_link 和 B 原子 begin_submission/mark_receipt/mark_rejected；根据 outcome 持久化，绝不在记录意图前发 RPC。
- [ ] fakes 同步返回 DeliveryOutcome；client 真实校验使用 stubbed call 的专门测试，不能只靠 fake 跳过校验。
- [ ] Run client/work_state 测试和 `test/application/test_review_boundaries.py`、`test/service/test_dispatch.py`；再 Run 全量。

### Task 3：QQ 证据挂边，判断留节点

**Files:**
- Modify: `apps/argus/src/evidence/qq/qq_facts.py`
- Modify: `apps/argus/src/entity/evidence.py`
- Modify: `apps/argus/src/service/qq_service.py`
- Create: `apps/argus/test/evidence/qq/test_qq_decision_evidence.py`
- Modify: `apps/argus/test/service/{test_qq_graph,test_intervention_state,test_observation,test_task_relation}.py`
- Modify: `apps/argus/test/application/{test_decision_status,test_pacing_conversation_flow}.py`

**Interfaces:**
- `QQDecisionEvidenceReader(conversation_reader, work_reader, activity_reader, facts_reader, batches, *, clock).evaluate(*, batch) -> DecisionEvidence`。
- 读取顺序 conversation→work→activity（through_seq 来自 conversation）→facts；rechecking 来自已存 trace 和现有上下文事实。
- 业务证据边显式 ID `decision_evidence`，被 code/model/relation/action 的 binder 读取；输出节点仍 RouteDecision。

- [ ] **RED：新增确定的 evidence 边守卫用例，复用已迁移的真实数据库 Flow。**

```python
import asyncio
from apps.argus.test.application.conversation_fakes import make_flow, receive
from apps.argus.src.entity.decision import ObservationBatch
from apps.argus.src.decision_graph.compiler import GraphCompiler

def test_self_only_skips_decision_evidence_edge(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            obs = await receive(flow, text='bot context', sender_id='10000')
            batch = ObservationBatch('synthetic', 'qq', '10000', obs.subject.key,
                (obs,), obs.received_at, obs.received_at)
            collect_calls = []
            original = flow.service.decision_reader.evaluate
            async def collect(*, batch):
                collect_calls.append(batch.id)
                return await original(batch=batch)
            flow.service.decision_reader.evaluate = collect
            flow.service.graph = GraphCompiler().compile(flow.service._build_graph())
            run = await flow.service.evaluate_batch(batch)
            assert run.output.action == 'silence'
            assert collect_calls == [] and flow.judge.call_count == 0
            assert run.executions['decision_evidence'].status == 'skipped'
        finally:
            await flow.database.close()
    asyncio.run(exercise())
```

测试明确重新编译既有 `_build_graph()` 以绑定替换的 callable，不为测试新增生产 registry/API。human case 在 `test_qq_decision_evidence.py` 注入有序假 reader 并断言 conversation/work/activity/facts 的调用次序及 through_seq 传递。

- [ ] Run test_qq_graph/new decision evidence 测试，预期仍是旧证据节点或没有边 ID 而失败。
- [ ] **GREEN：service 绑定一个有实际内容的证据边，不插四个假判断节点。**

```python
Edge('human', 'code', bool,
    collect=EdgeAction(decision_reader.evaluate, lambda batch, _: {'batch': batch}),
    id='decision_evidence')
NodeBinding('code', code_policy.evaluate,
    lambda _, r: {'value': InterventionInput(
        r['decision_evidence'].facts, r['decision_evidence'].snapshot,
        r['decision_evidence'].activity, r['decision_evidence'].rechecking)},
    requires=('decision_evidence',))
```

- [ ] 删除纯证据 conversation/work/activity/facts 节点，把原 `_facts/_activity/_code` 中读事实/组上下文的实现搬 reader；code 使用实际策略，无 service 辅助节点读取 DB。
- [ ] 保持 code→可选model→intervene→silent或relation/action→end 的分支；汇合/出口 identity 保持通用路由用途，不假装它们是新增判断策略。
- [ ] process_batch 从 `run.executions['decision_evidence'].output` 取 conversation/snapshot/trace 元数据，不再按旧 work/conversation 节点 ID 找数据；失败边无产物时沿原 graph_failed 路径保存。
- [ ] 证据包获取不改变固定成员/后到背景、双快照、历史限额、名字/显式信号和指纹；prompt 仍在发送前由 evidence 生成，不强制塞进图。
- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/evidence/qq apps/argus/test/service/test_qq_graph.py apps/argus/test/service/test_task_relation.py apps/argus/test/application/test_decision_status.py -q`；Run 全量。
- [ ] 审核新图节点/边清单，base 仍无业务 import。

### Task 4：直接接入，取消入站队列

**Files:**
- Modify: `apps/argus/src/service/qq_service.py`
- Modify: `apps/argus/src/handler/qq_handler.py`
- Modify: `apps/argus/src/connector/qq_connector.py`
- Modify: `apps/argus/src/service/batch_scheduler.py`
- Modify: `apps/argus/src/main.py`
- Delete: `apps/argus/src/{queue,worker}.py`
- Delete only obsolete unit tests after replacement coverage: `apps/argus/test/{test_queue,test_worker}.py`
- Create: `apps/argus/test/service/test_receive.py`
- Modify: `apps/argus/test/handler/test_qq_handler.py`、`apps/argus/test/connector/test_qq_connector.py`、`apps/argus/test/test_main.py`、`apps/argus/test/config/test_config.py`
- Modify application setup: `apps/argus/test/application/{conversation_fakes,test_qq_flow,test_batch_flow}.py`

**Interfaces:**
- `QQService.receive(observation: QQObservation) -> bool`：调用 to_row→ObservationStore.insert；返回 inserted；事务提交后仅本地通知，不执行 graph/Gateway。
- `QQHandler(bot_id, *, receive=None)` 保留纯 parse 用于测试/映射；`await handler.handle(raw) -> QQObservation | None`：ignored→None，成功解析后 await 注入 receive，返回业务 observation；没有注入 receive 时 handle 明确 RuntimeError，不默默丢弃。parse 不依赖 service/HTTP。
- `QQConnector(config: QQConfig, secret: str, handler: QQHandler)`：只接注入 handler，事件入口 await handler.handle，不直接接 DAO/queue/策略/服务。
- `BatchScheduler.notify(scope: tuple[str,str,str], *, human: bool) -> None`：合并唤醒提示；非异步、不查库、不读远端、不携带业务消息队列。

- [ ] **RED：service.receive 没有远端调用，且必须等事务。**

```python
import asyncio
from apps.argus.test.application.conversation_fakes import make_flow
from apps.argus.src.handler.qq_handler import QQHandler

def test_receive_waits_for_commit_not_for_graph(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        original_insert = flow.store.insert
        async def blocked_insert(row, **kwargs):
            entered.set()
            await release.wait()
            return await original_insert(row, **kwargs)
        flow.store.insert = blocked_insert
        obs = QQHandler('10000').parse(dict(post_type='message', self_id=10000,
            time=1, message_id=1, message_type='private', user_id=7, message='hello'))
        task = asyncio.create_task(flow.service.receive(obs))
        try:
            await entered.wait()
            assert not task.done()
            release.set()
            assert await task
            assert flow.judge.call_count == 0 and flow.gateway.submissions == []
        finally:
            release.set()
            await flow.database.close()
    asyncio.run(exercise())
```

本任务给 B 已迁移的 Flow 增加 database 引用；store 保持表示 ObservationStore，不混成 Database。

- [ ] Run test_receive，预期 service 缺 receive 或调用了完整图而失败。
- [ ] **GREEN：**receive 在 B 的插入事务提交后 notify；重复不修改首次强信号，冲突向 handler/connector 回传；通知失败只记录安全错误并保留数据库恢复，不转成“未收到”。
- [ ] handler.handle 的实际测试注入 fake receive，检查调用一次、业务对象字段一致、ignored/invalid 不调用 receive；connector 校验后只调 handle，204/401/403/413/422/409 保持。
- [ ] 用 TestClient 证明 204 后可从另一个 DB session 查到记录；相同来源重复204不二次形成工作，冲突409不改原事实。
- [ ] 将 application.conversation_fakes.receive 从手动 store.insert+scheduler.offer 改为调用 service.receive（仍用 fake clock 替换 received_at），然后测试显式 `await scheduler.check_due()`/drain，避免 helper 绕过真实接收入口。
- [ ] main 注入 `QQHandler(config.qq.bot_id, receive=service.receive)` 并交 QQConnector；去掉 app.state.queue/worker、worker 健康断言/生命周期；QQ disabled 时无上报路由且不创建空转 worker。
- [ ] 删除 src/queue.py/src/worker.py 和对应纯包装测试；停机/错误/恢复的有效断言移到 receive/runtime，不删掉行为覆盖。
- [ ] 对 queue_capacity 字段：保留作为 ready 批次队列容量，不引入第二容量；worker_count 现有 `Literal[1]` 配置键暂保持可读取以免改用户 settings，但不再用它创建/检查入站 worker。此项不是恢复队列或新增兼容模式。
- [ ] 更新健康/配置说明和 test_config，以说明上述保留的 worker_count 键不再控制入站 worker；不能为这个无运行时作用的键恢复队列或创建假消费者。
- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/handler apps/argus/test/connector apps/argus/test/service/test_receive.py apps/argus/test/test_main.py -q`；Run 全量。

### Task 5：后台计时/执行归 service，维持等待和恢复

**Files:**
- Create: `apps/argus/src/service/batch_runtime.py`
- Modify: `apps/argus/src/service/batch_scheduler.py`
- Modify: `apps/argus/src/main.py`
- Create: `apps/argus/test/service/test_batch_runtime.py`
- Modify: `apps/argus/test/service/{test_batch_scheduler,test_waiting_scheduler,test_batch_order}.py`
- Modify: `apps/argus/test/application/{test_batch_health,test_waiting_recovery_flow,test_pacing_conversation_flow}.py`

**Interfaces:**
- `BatchScheduler.wakeup: asyncio.Event`，`notify` 只置位及合并 human scope；提示集合是可丢失的加速提示，数据库是事实源，不存完整消息。
- `check_due(now=None)` 仍查询未绑定 scope/等待批次，调证据与策略，关批并 replenish；新 human scope 可提前检查对应 waiting，非新消息仍按原期限检查。
- `BatchRuntime(scheduler, service, *, recovery_interval_seconds)`：`run_batches()`、`run_checks()` 两个长运行协程；main 创建/取消 task，健康检查监督 task 状态。

- [ ] **RED：阻塞模型时还能收存另一条消息。** 使用 FakeJudge.ask 内 entered/release Event；run_batches 正在 graph 时调用 service.receive 第二条，断言第二条完成且已落库，模型仍 blocked。
- [ ] 新增 runtime cancellation 测试，取消两协程后无悬挂 task；不捕获 CancelledError 为正常业务失败。
- [ ] Run test_batch_runtime/test_waiting_scheduler，预期通知/生命周期尚不完整失败。
- [ ] **GREEN：把 main 的两个协程实际实现移入 BatchRuntime。**

```python
async def run_batches(self):
    while True:
        batch_id = await self.scheduler.ready.get()
        try:
            await self.service.process_batch(batch_id)
        finally:
            self.scheduler.done(batch_id)
        await self.scheduler.replenish()

async def run_checks(self):
    while True:
        self.scheduler.wakeup.clear()
        await self.scheduler.check_due()
        try:
            async with asyncio.timeout(self.recovery_interval_seconds):
                await self.scheduler.wakeup.wait()
        except TimeoutError:
            pass
```

clear 必须放在开始检查前，检查期间来的通知留到 wait；human scope 提示在 check_due 中原子取出，新增提示不被清掉。计时使用既有可控时钟，不用墙钟覆盖策略 deadline。

- [ ] 不在 receive 等 `_check_scope/_check_waiting`；后台扫描无通知时仍恢复持久化记录；ready 满则延后补入，不增长无界执行 task。
- [ ] `scheduler.offer` 不再是接入必经，迁移调用方到 notify/check_due 后删除；保留等待 force human 的语义和 no-change 不调模型。
- [ ] health 去掉空入站 worker 条件，监督数据库、批次/检查任务和现有 Gateway 连接；QQ disabled 仍可以健康，不要求不存在的后台业务任务运行。
- [ ] 私聊/@/称呼/人类压力仍唤醒后台判断，不保证必回应；自身消息前缀排空后及时 replenish，人类批次不被饿死。
- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/service/test_batch_runtime.py apps/argus/test/service/test_waiting_scheduler.py apps/argus/test/application/test_waiting_recovery_flow.py apps/argus/test/application/test_batch_health.py -q`；Run 全量。

### Task 6：发送安全、层级依赖与最终文档验收

**Files:**
- Modify tests: `apps/argus/test/application/{test_layer_dependencies,test_review_boundaries,test_decision_status,test_qq_delivery_prompt}.py`
- Modify tests: `apps/argus/test/service/{test_dispatch,test_batch_dispatch}.py`
- Modify: `apps/argus/src/**` 中本轮迁移涉及的绝对应用内 import（仅应用内，不改第三方）。
- Update after implementation: `apps/argus/README.md`、`docs/spec/2026-10-01-evidence-and-pacing/arch.md`、`docs/spec/2026-09-30-judgement-graph/arch.md`、`docs/spec/2026-09-29-qq-observation-flow/arch.md`、`docs/spec/2026-09-30-module-naming/design.md`。

**Interfaces:**
- 最终层级依赖与 design 3.3 一致，旧 queue/worker/facts policy/DAO codec 路径不存在。
- 观察状态端点依旧按运行分页，trace 不输出收集产物全文。

- [ ] **RED：AST 层级依赖检查。** 在 B 的依赖测试增加 connector→service/dao/evidence/strategy/queue、service→handler/connector、evidence↔strategy、graph→任意业务 import 的拒绝；同样解析相对 import，函数体内 import 也检查。
- [ ] 代码断言：`Path('apps/argus/src/queue.py').exists()` 与 worker.py 必须 False；只检查源码真实路径，不把 __pycache__ 当旧实现。

```python
from pathlib import Path

def test_forwarding_ingress_queue_is_removed():
    source = Path(__file__).resolve().parents[2] / 'src'
    assert not (source / 'queue.py').exists()
    assert not (source / 'worker.py').exists()
```

- [ ] Run test_layer_dependencies，预期仍有未归位 import 时失败。
- [ ] **GREEN：**清理本轮实际引用，不能为了通过检查额外加调用转发模块；按现有命名/参数风格保持代码明确。
- [ ] 逐个保留并运行原 test_review_boundaries：最后查询发现 queued work、新消息/取消阻止旧投递、无历史取消不快判 yes、pre-send transport 的恢复期限、拒绝原子记录、统计窗过期仍看历史、invalid RPC parameters failed。
- [ ] test_dispatch 保留 session create response lost、workspace 身份不符、submit 响应丢失 unknown 不重发；test_batch_dispatch 保留 steer rejected→waiting 不自动 submit。
- [ ] 状态/trace 分页和 prompt 回归仍用真实数据库与假外部服务；不能只 stub process_batch 来宣称行为保持。
- [ ] Run 最小目录：handler/connector/clients/evidence/strategy/service/application；再 `make test-argus`。
- [ ] 实现通过后更新 README/current arch：直接接入、service 内部 scheduler/ready、证据边、判断节点、entity.records/DAO 边界；旧 arch 用指向当前架构的说明替换过期实现描述，不改写历史计划为事实。
- [ ] Run `git diff --check`，核对应用脚本 compileall；记录实际通过/失败和未做真实外部 smoke，不泛称“模型效果已验证”。
- [ ] 全部 task 验收后才标记 C 完成；不创建提交或推送，交给用户审阅 diff。
