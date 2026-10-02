# A：决策图基座整理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把图声明、编译和运行分开，让边可以执行注入的收集动作，并保持现有 QQ 普通判断路径。

**Architecture:** service 声明图并显式编译，runner 只执行 CompiledGraph。节点和边拥有统一执行记录，但不增加 Unit 基类、注册系统或业务依赖。A 只迁移现有 QQ 调用，证据节点归边属于 C。

**Tech Stack:** Python 3.12、dataclasses、asyncio、pytest、原生 assert。

**Spec:** [design.md](design.md)。后续：[B plan](../2026-10-02-dao-boundary/plan.md)、[C plan](../2026-10-02-layer-organization/plan.md)。

## 执行记录（2026-10-02）

- A/B/C主体改造已实施；当前状态见[现状架构](../2026-10-01-evidence-and-pacing/arch.md)。
- 最终 `make test-argus`：210 passed；命令包含compileall，`git diff --check`通过。
- 原入站queue/worker及4项旧测试经用户逐文件确认后删除；后台批次队列保留。
- 独立review子代理3次均API400未运行；只完成作者自查，不能视为独立审查通过。
- 未创建分支/worktree、未提交/推送、未连接真实服务或读取密钥/真实库。
- 任务中的协同接口迁移合并实施，部分旧行为用例为原有行为固定测试而非新功能RED；实际RED/GREEN与取舍见本次执行记录，不宣称每个示例逐字执行。

## Global Constraints

- `decision_graph` 只依赖标准库和包内模块，不 import entity、clients、dao、evidence、strategy 或 service。
- 不引入 reads 输入声明，不分析不透明 condition/binder，不证明每条路径上的业务输入完整性。
- 节点 executes 与选中 collect 共用步数；passive/skipped 边不计步。默认总预算仍为 32 步 / 30 秒。
- CancelledError 向调用方传播；普通边条件异常不再反写源节点失败，这是明确的变化，不冒称全部异常行为不变。
- 不保留两套 runner 接口或旧路径转发；所有源码/测试调用点同步迁移。
- 不改本地 settings、真实库、其他应用；不调用真实 QQ/Gateway/Jev。
- 不创建分支/worktree、不提交/推送。技能模板中的 commit 步骤改为检查任务 diff，除非用户另行授权 Git 操作。
- 本计划尚未执行；完成方框才表示实际完成。

## Review Focus

1. 未选中失败分支不能破坏 selected 汇合；all/any 仍保留原来基于源节点的含义——Task 3 真值表固定。
2. condition 抛错不能丢掉源结果或阻止兄弟边——Task 2 异常隔离测试。
3. passive 边增加记录不能耗尽原来可用的 QQ max_steps——Task 2 步数测试。
4. 重复子图的单元 ID 相同但轮次不同，不能串产物/记录——Task 4 重复与并发测试。
5. 收集动作输出含敏感文本时，状态接口只能给安全摘要——Task 5 trace 测试。

---

## 工作顺序与验证命令

执行次序：A1 → A2 → A3 → A4 → A5，A 全量通过再进入 B。不启动应用来验证这次纯内部改造。

命令均在仓库根 `/home/removel_yi/projects/Icarus` 执行：

```bash
apps/argus/.venv/bin/python -m pytest apps/argus/test/decision_graph -q
make test-argus
git diff --check
```

`make test-argus` 使用 `apps/argus/scripts/test.sh`，同时执行 compileall。定向 pytest 仍加载 test/conftest.py 的临时配置和密钥隔离。

- [ ] 记录 `git status --short`，不要清理用户现有改动。
- [ ] 跑一次 `make test-argus` 保存基线；若有失败，记录具体用例，不修无关失败。

## 文件结构与公共接口

| 文件 | 目标职责 |
|---|---|
| `src/decision_graph/graph.py` | DecisionGraph、RepeatBlock 的冻结声明 |
| `src/decision_graph/edge.py` | Edge、EdgeAction，边 id/超时 |
| `src/decision_graph/node.py` | 保持 NodeBinding、InputBinder 形状 |
| `src/decision_graph/compiler.py` | GraphCompiler、CompiledGraph、结构校验和稳定顺序 |
| `src/decision_graph/state.py` | UnitExecution、ExecutionState、ExecutionBudget、GraphRun |
| `src/decision_graph/runner.py` | 编译产物的顺序执行及边动作 |

编译产物字段统一如下，后续任务不能另外发明名字：

```python
@dataclass(frozen=True)
class CompiledGraph:
    nodes: Mapping[str, NodeBinding]
    edges: Mapping[str, Edge]  # 值的 id 已解析，不再是 None
    ordered_nodes: tuple[NodeBinding, ...]
    incoming: Mapping[str, tuple[Edge, ...]]
    outgoing: Mapping[str, tuple[Edge, ...]]
    repeat_bodies: Mapping[str, 'CompiledGraph']
    entry: str
    output: str
```

所有 Mapping 由编译器复制并包装只读视图。`repeat_bodies` 按父图节点 ID 索引；原声明不修改。节点的 requires 可引用节点 ID 或边 ID。

### Task 1：声明与编译分离，并迁移所有普通调用

**Files:**
- Create: `apps/argus/src/decision_graph/compiler.py`
- Modify: `apps/argus/src/decision_graph/{graph,edge,state,runner}.py`
- Modify: `apps/argus/src/service/qq_service.py`
- Test: create `apps/argus/test/decision_graph/test_compiler.py`
- Modify tests: `apps/argus/test/decision_graph/{test_runner,test_dependencies,test_limits,test_model_steps,test_state,test_trace}.py`
- Modify direct runner callers: `apps/argus/test/service/test_decision_trace.py`；通过搜索核对其余直接调用点。

**Interfaces:**
- Consumes: 现有 NodeBinding/Edge/RepeatBlock 声明。
- Produces: `GraphCompiler.compile(graph: DecisionGraph) -> CompiledGraph`；`GraphRunner.run(graph: CompiledGraph, value, *, max_steps=32, timeout_seconds=30) -> GraphRun`。
- `DecisionGraph` 构造只保存声明；结构错误由 compile 抛 ValueError。

- [ ] **RED：新增编译产物与失败时点测试。**

```python
import pytest
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge

async def identity(value):
    return value

def test_compile_is_explicit_and_preserves_declaration():
    nodes = (NodeBinding('a', identity, lambda *_: {'value': 1}),
             NodeBinding('b', identity, lambda _, r: {'value': r['a']}))
    declaration = DecisionGraph(nodes, (Edge('a', 'b'),), 'a', 'b')
    compiled = GraphCompiler().compile(declaration)
    assert tuple(n.id for n in compiled.ordered_nodes) == ('a', 'b')
    assert compiled.edges['a->b'].id == 'a->b'
    assert declaration.edges[0].id is None
    with pytest.raises(TypeError):
        compiled.nodes['x'] = nodes[0]

def test_cycle_fails_at_compile_not_declaration():
    node = NodeBinding('a', identity, lambda *_: {'value': 1})
    declaration = DecisionGraph((node,), (Edge('a', 'a'),), 'a', 'a')
    with pytest.raises(ValueError):
        GraphCompiler().compile(declaration)
```

- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/decision_graph/test_compiler.py -q`；预期先因缺少 compiler 失败。
- [ ] **GREEN：移动现有校验和排序，不重写其普通 join 语义。** 冻结声明去掉 `__post_init__` 的拓扑工作；compiler 校验空/重复 ID、悬挂边、重复端点、入口/出口、join、有限正超时、requires、无环与入口可达。

```python
# requires 指向边时，把该边源节点纳入排序约束。
parents = {edge.source for edge in incoming[node.id]}
for required in node.requires:
    parents.add(required if required in nodes else edges[required].source)
# 当前节点要求自己的出边，会形成 self-parent，必须拒绝。
```

- [ ] 边声明加入 `collect: EdgeAction | None`、`id: str | None`、`timeout_seconds=10.0`；此任务尚不执行 collect。
- [ ] runner 对 CompiledGraph 的 ordered_nodes/incoming/outgoing 运行现有普通算法；不是每次 run 重新 compile。
- [ ] QQService 构造改为 `GraphCompiler().compile(self._build_graph())`；测试中的构造期校验改为 compile 校验。
- [ ] 父 compiler 递归生成 repeat_bodies，runner 取编译后的 body；校验嵌套重复的次数/子图，防止声明递归引用无限递归。
- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/decision_graph apps/argus/test/service/test_qq_graph.py -q`；测试预期为 PASS，不改普通路径输出。
- [ ] 检查本任务 diff；校验/接口迁移与测试同属一个任务，不提交半迁移状态。

### Task 2：边动作成为独立执行单元

**Files:**
- Modify: `apps/argus/src/decision_graph/{edge,state,runner}.py`
- Create test: `apps/argus/test/decision_graph/test_edges.py`

**Interfaces:**
- Consumes: Task 1 的 CompiledGraph；`EdgeAction(action, bind_input)`。
- Produces: `UnitExecution(id, kind, status, output=None, error_type=None, duration=0.0, iteration=0)`；GraphRun.executions 混装节点/边。
- bind_input 签名保持 `(value, successful_outputs) -> keyword_arguments`；映射只读。

- [ ] **RED：边产物、异常隔离、步数分别写成以下测试。**

```python
import asyncio
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge, EdgeAction
from apps.argus.src.decision_graph.runner import GraphRunner

async def identity(value):
    return value

def test_collect_output_is_available_to_target():
    graph = DecisionGraph((
        NodeBinding('a', identity, lambda *_: {'value': 2}),
        NodeBinding('b', identity, lambda _, r: {'value': r['facts']}, requires=('facts',)),
    ), (Edge('a', 'b', collect=EdgeAction(identity, lambda _, r: {'value': r['a'] + 1}),
             id='facts'),), 'a', 'b')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None, max_steps=3))
    assert run.output == 3
    assert run.executions['facts'].kind == 'edge'
    assert run.executions['facts'].status == 'succeeded'

def test_condition_failure_keeps_source_and_sibling():
    def broken(value):
        raise ValueError('do not log this text')
    graph = DecisionGraph((
        NodeBinding('a', identity, lambda *_: {'value': 1}),
        NodeBinding('bad', identity, lambda *_: {'value': 0}),
        NodeBinding('ok', identity, lambda *_: {'value': 9}),
    ), (Edge('a', 'bad', broken), Edge('a', 'ok')), 'a', 'ok')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None))
    assert run.output == 9
    assert run.executions['a'].status == 'succeeded'
    assert run.executions['a->bad'].status == 'failed'
    assert run.executions['bad'].status == 'blocked'
    assert run.executions['a->ok'].status == 'succeeded'

def test_passive_edges_do_not_consume_steps():
    graph = DecisionGraph((
        NodeBinding('a', identity, lambda *_: {'value': 1}),
        NodeBinding('b', identity, lambda *_: {'value': 2}),
    ), (Edge('a', 'b'),), 'a', 'b')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None, max_steps=2))
    assert run.status == 'completed' and run.output == 2
```

- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/decision_graph/test_edges.py -q`；预期 collect 无记录/未执行导致失败。
- [ ] **GREEN：源节点成功先写 output/record，单独执行每条出边。** false→skipped；源 skipped→skipped；源 failed/blocked→blocked；条件/绑定/action 异常→边 failed；选中且无动作→succeeded(None)。兄弟边继续，错误日志只写类型/单元 ID，不写异常正文。

```python
state.outputs[node.id] = output
state.states[node.id] = 'succeeded'
# 之后按声明序处理每条边，使用独立 try/except Exception。
# collect 开始前扣预算，asyncio.timeout(edge.timeout_seconds) 包住动作。
# 真条件先记 selected；collect 失败也保留 selected。
```

- [ ] 加入兄弟产物可见性测试：第一条 collect 返回 1，第二条 binder 读取第一条产物返回 2；反向读取未来单元必须记录 KeyError，不能凭此宣称缺生产者。
- [ ] 加入 skipped/blocked 边记录断言，以及 collect 返回 None 的成功断言；可依赖无动作边，它成功但值为空。
- [ ] Run test_edges 和原 test_state；检查运行间映射互不污染、不可写。
- [ ] 检查本任务 diff，确认没有业务 import。

### Task 3：固定 join 与 requires 的传播语义

**Files:**
- Modify: `apps/argus/src/decision_graph/runner.py`
- Modify: `apps/argus/test/decision_graph/test_dependencies.py`
- Create: `apps/argus/test/decision_graph/test_joins.py`
- Modify: `apps/argus/test/decision_graph/test_compiler.py`

**Interfaces:**
- Consumes: 节点/边的 states、selected、outputs。
- Produces: 设计 5.2 定义的 all/any/selected 真值表；边 requires 能约束编译顺序和运行成功性。

- [ ] **RED：参数化真值表（另一个父节点 skipped/failed/succeeded）。**

```python
import asyncio
import pytest
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge
from apps.argus.src.decision_graph.runner import GraphRunner

async def identity(value):
    return value

@pytest.mark.parametrize('other,join,status', [
    ('skipped', 'all', 'skipped'), ('skipped', 'any', 'succeeded'),
    ('skipped', 'selected', 'succeeded'), ('failed', 'all', 'blocked'),
    ('failed', 'any', 'blocked'), ('failed', 'selected', 'succeeded'),
    ('succeeded', 'all', 'succeeded'), ('succeeded', 'any', 'failed'),
    ('succeeded', 'selected', 'succeeded'),
])
def test_join_parent_statuses(other, join, status):
    async def right(value):
        if other == 'failed':
            raise ValueError('right failed')
        return value
    graph = DecisionGraph((
        NodeBinding('root', identity, lambda *_: {'value': True}),
        NodeBinding('left', identity, lambda *_: {'value': 1}),
        NodeBinding('right', right, lambda *_: {'value': 2}),
        NodeBinding('end', identity, lambda *_: {'value': 3}, join=join),
    ), (Edge('root', 'left'), Edge('root', 'right', lambda _: other != 'skipped'),
        Edge('left', 'end'), Edge('right', 'end')), 'root', 'end')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None))
    assert run.executions['end'].status == status
```

- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/decision_graph/test_joins.py apps/argus/test/decision_graph/test_dependencies.py -q`。旧真值表可能已经 PASS，这属于保持行为的 characterization，不伪称 RED；新增“要求边 ID/失败边阻断”的测试在新能力未实现时才应失败，记录实际结果。
- [ ] **GREEN：**控制父节点仍按设计取源节点；requires/控制父失败或实际入边 failed 优先 blocked，再判激活，再 requires 完成，再 join。不要把 any 改为至少一个；不要把 all/any 的成功数量改为边数量。
- [ ] 新增 end 要求一条条件为假的边：即使该边源节点 succeeded，end 也必须 blocked；失败边同样 blocked。
- [ ] 新增 compile 拒绝节点要求自己的出边、未知边 ID、跨分支 requires 形成环的测试。
- [ ] Run 全部 test/decision_graph；检查普通分支与图失败输出保持原断言。
- [ ] 检查本任务 diff。

### Task 4：边超时、取消、重复与共享预算

**Files:**
- Modify: `apps/argus/src/decision_graph/{compiler,runner}.py`
- Modify: `apps/argus/test/decision_graph/test_limits.py`
- Modify: `apps/argus/test/decision_graph/{test_runner,test_state}.py`

**Interfaces:**
- Consumes: 编译后的 repeat_bodies、单一 ExecutionBudget。
- Produces: 节点/collect 同一预算、每轮独立状态和带前缀记录；取消不吞。

- [ ] **RED：超时和取消用 Event，不用长 sleep。**

```python
def test_edge_timeout_is_not_source_failure():
    async def wait(value):
        await asyncio.Event().wait()
    graph = DecisionGraph((
        NodeBinding('a', identity, lambda *_: {'value': 1}),
        NodeBinding('b', identity, lambda *_: {'value': 2}),
    ), (Edge('a', 'b', collect=EdgeAction(wait, lambda *_: {'value': None}),
             timeout_seconds=0.01),), 'a', 'b')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None))
    assert run.executions['a'].status == 'succeeded'
    assert run.executions['a->b'].error_type == 'TimeoutError'
    assert run.executions['b'].status == 'blocked'
```

将 imports/identity 明确加入 test_limits：compiler、EdgeAction 与原 helper 共用。

- [ ] 另测取消：collect 设置 entered Event 后等待；取消 run task，`pytest.raises(asyncio.CancelledError)` 包住 await；不得返回 failed/silence。
- [ ] Run test_limits；预期超时/预算/取消不完整时失败。
- [ ] **GREEN：**嵌套子图使用父 budget；每轮创建新 ExecutionState；collect 先扣步数再绑定；节点包装超时和整图截止时间都覆盖子图；CancelledError 不由 Exception 处理。
- [ ] 用含两个节点（inc→end）和一个 collect 的重复 body 测 2 轮：需要 wrapper 1 + body nodes 4 + collects 2 = 7 步；7 完成，6 失败，轮次产物按输入递增，不跨轮取旧边值。不能写“一个节点带边动作”的 body，那会成为自环或悬挂边。
- [ ] 同时运行同一个 CompiledGraph 两次（输入 1/2），用 asyncio.gather，断言输出、run_id 和 records 独立。
- [ ] 在编译任务上断言子图已存于 repeat_bodies，runner 每轮不调用 compiler。
- [ ] Run 图测试全量，检查敏感异常正文不入日志。
- [ ] 检查本任务 diff。

### Task 5：业务调用与安全 trace 最小适配，完成 A 验收

**Files:**
- Modify: `apps/argus/src/service/{qq_service,decision_trace}.py`
- Modify: `apps/argus/test/decision_graph/test_trace.py`
- Modify: `apps/argus/test/service/{test_qq_graph,test_decision_trace}.py`
- Modify: `apps/argus/test/application/test_decision_status.py`
- Update after implementation: `apps/argus/docs/spec/2026-09-30-judgement-graph/arch.md`、`apps/argus/docs/spec/2026-10-01-evidence-and-pacing/arch.md`

**Interfaces:**
- Consumes: GraphRun.executions 的 kind=node/edge。
- Produces: 原 safe_trace 顶层字段保留；nodes 只含节点；新增 edges 仅含 id/status/duration/iteration/error_type。

- [ ] **RED：构造 collect 产物为敏感字符串的图，用现有 safe_trace 检查。**

```python
import asyncio
import json
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge, EdgeAction
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.service.decision_trace import safe_trace

def test_trace_excludes_edge_raw_output():
    async def identity(value):
        return value
    declaration = DecisionGraph((
        NodeBinding('a', identity, lambda *_: {'value': True}),
        NodeBinding('b', identity, lambda *_: {'value': True}),
    ), (Edge('a', 'b', collect=EdgeAction(identity,
              lambda *_: {'value': 'sensitive-chat'})),), 'a', 'b')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(declaration), None))
    safe = safe_trace(run)
    assert all(item['id'] in {'a', 'b'} for item in safe['nodes'])
    assert safe['edges'][0]['id'] == 'a->b'
    assert 'result' not in safe['edges'][0]
    assert 'sensitive-chat' not in json.dumps(safe)
```

collect 返回敏感字符串，节点仅返回 bool；测试防止边原始结果在 nodes 或 edges 泄漏。

- [ ] Run test_decision_trace/test_trace，预期旧 formatter 把边当节点或缺 edges 而失败。
- [ ] **GREEN：**按 kind 分组；业务 safe_result/reason 白名单留原文件，A 不搬到 graph。保留状态页的 next_after_run/has_more 和旧运行分页语义。
- [ ] 现有 QQ 图依旧保留 conversation/work/activity/facts 节点，所有边 collect=None；私聊、自消息、模型不确定路径断言原调用次数/结果。
- [ ] 搜索所有 GraphRunner/DecisionGraph/NodeExecution 引用，确认没有遗留直接 run 未编译声明或旧执行类型；不批量改历史 design/plan。
- [ ] Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/decision_graph apps/argus/test/service/test_qq_graph.py apps/argus/test/service/test_decision_trace.py apps/argus/test/application/test_decision_status.py -q`。
- [ ] Run `make test-argus`；记录实际测试结果，不把失败替换成修改预期来消掉。
- [ ] 更新对应 arch 的现状描述（只有实现后才写“已实现”）；Run `git diff --check` 并检查 compileall 结果。
- [ ] A 验收通过才标记任务完成并进入 B；未授权不提交。
