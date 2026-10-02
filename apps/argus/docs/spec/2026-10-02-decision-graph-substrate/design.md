# 决策图基座整理设计

> 状态：待审阅设计，尚未实施；不是当前源码架构。本文覆盖 A：图、节点、边、编译、状态与轻量执行。B 见 [DAO 边界](../2026-10-02-dao-boundary/design.md)，C 见 [职责归位](../2026-10-02-layer-organization/design.md)。设计确认后编写实施计划。

## 1. 目标与范围

本次主要目标是整理现有代码结构。基座只提供上层所需的基础能力，不增加 DSL、注册系统、并行调度或业务框架。

已经确认的约定：

- `decision_graph` 只依赖标准库和包内模块，不 import entity、clients、dao、evidence、strategy 或 service。
- service 使用图的声明/编译/执行接口，将业务 callable 注入节点和边；基座不知道业务组件是谁，也不自行创建业务组件。
- 节点承载判断，边可以承载证据收集。边是一等执行单元，有标识、状态、产物、记录，可被节点依赖。
- 产物统一使用字符串单元标识，不额外建立 evidence 命名空间。
- 不引入 reads 输入声明，不分析不透明 condition/binder，不证明每条路径上的业务输入完整性。

A 只机械迁移现有 QQ 图的编译/执行调用，不把现有证据节点搬到边；后者在 C 完成。普通 QQ 分支及结果保持原义；边条件异常从源节点异常中分离，是本次明确的执行边界变化。

## 2. 已核对的现状

- `graph.py` 的 DecisionGraph 构造时校验标识、节点选项、依赖、可达性和无环，并生成索引与拓扑顺序。
- `node.py` 的 NodeBinding 提供 callable、输入绑定、requires、join 和超时。
- `edge.py` 只有 source/target/同步条件，没有执行状态或动作。
- `runner.py` 顺序执行节点，在节点 try 块内求所有出边条件；一个条件抛错会把已执行成功的源节点记为失败。
- `state.py` 的状态、结果映射和 GraphRun 只记录节点及选边，不能报告边动作。

构造期校验已经存在；新增 compiler 是为了把声明和执行计划的职责分开，不声称原来的结构错误只能在运行期发现。

## 3. 文件与接口

| 文件 | 职责 |
|---|---|
| `graph.py` | DecisionGraph、RepeatBlock 声明，不做编译 |
| `node.py` | NodeBinding 与 callable/binder 类型 |
| `edge.py` | Edge、EdgeAction、条件默认值 |
| `compiler.py` | GraphCompiler、CompiledGraph；结构校验与执行顺序 |
| `state.py` | 单次状态、预算、UnitExecution、GraphRun |
| `runner.py` | 顺序执行已编译图及嵌套重复 |

不引入统一 Unit 基类或多级 execution 包；节点和边的记录共享一个简单数据类即可。

```python
DecisionGraph(nodes, edges, entry, output)
GraphCompiler().compile(graph) -> CompiledGraph
await GraphRunner().run(compiled, value, max_steps=32, timeout_seconds=30)
```

DecisionGraph 为冻结声明，编译器不修改它；runner 只接收 CompiledGraph。

节点接口保留 `id/evaluate/bind_input/requires/join/timeout_seconds`。边接口：

```python
@dataclass(frozen=True)
class EdgeAction:
    action: Callable[..., Awaitable[object]]
    bind_input: InputBinder

@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    condition: Callable[[object], bool] = always
    collect: EdgeAction | None = None
    id: str | None = None
    timeout_seconds: float = 10.0
```

默认边标识为 `'<source>-><target>'`；显式 id 必须非空。节点/边标识在同图内统一唯一，继续拒绝同一 source/target 的重复边。超时必须有限且为正。

## 4. 编译规则

编译器承担现有结构校验，加上边标识、动作及边依赖校验：

- 节点/边标识唯一，入口出口存在，source/target 和 requires 引用存在。
- join、超时及重复次数合法，普通图无环，节点从入口结构可达。
- 节点 requires 可以指向节点或边。要求边成功等于依赖该边所在源节点及其出边执行位置；需要自己出边的节点构成环，编译时拒绝。
- 保持稳定拓扑顺序，同样可执行的节点按声明顺序排列。
- 每个源节点后完整执行其出边组，组内按图中的声明顺序，然后执行下一个节点。
- 边不新增 requires。需要跨分支产物的收集动作，在源节点上显式声明前置单元；编译器不从 binder 自动猜测依赖。

CompiledGraph 保存只读节点序、节点/边索引、入出边索引、入口出口和编译后的 RepeatBlock 子图。父图编译时递归编译子图，重复每轮不重新编译，也不改写原声明。

编译器只校验结构，不检测所有业务输入。binder 的 KeyError 可能来自任何内部字典，不能据此声称“没有单元生产过这个字段”；只报告失败单元和异常类型。

## 5. 执行规则

### 5.1 节点与边边界

节点成功先发布 output 与 succeeded 记录，再处理出边；出边异常不反写源节点结果。

| 情况 | 边状态 | 行为 |
|---|---|---|
| 源节点 skipped | skipped | 不求条件，不收集 |
| 源节点 failed/blocked | blocked | 不求条件，不收集 |
| 条件为假 | skipped | 不收集 |
| 条件为真，无 collect | succeeded | 记录选中，产物为 None |
| 条件为真，collect 成功 | succeeded | 记录选中及产物 |
| 条件/绑定/collect 异常 | failed | 记录错误，目标阻断；继续其他兄弟边 |

条件异常不等于条件为假。只有条件返回真才进入 selected_edges；随后 collect 失败仍保留“这条边已被选中”。目标收到失败边时 blocked，包括无法完成条件求值的失败边。

collect 的 binder 接收当前输入与只读成功产物映射：能看到源节点及已成功的前序单元/兄弟边，不能看到未来兄弟边。不发布失败动作的半成品，不保证任意对象深度冻结；业务组件使用不可变输出。

### 5.2 保持现有 join 含义

A 不趁整理重新定义 all/any/selected。现有规则以入边的源节点作为控制父节点：

- all/any：控制父节点是全部入边源节点。
- selected：控制父节点只包含已选中入边的源节点。
- 入口激活，其他节点至少有一条入边选中才激活。

按以下顺序判定：

1. requires 或控制父节点 failed/blocked，或有入边 failed → blocked。
2. 未激活 → skipped。
3. 任一 requires 单元非 succeeded → blocked。
4. all/selected 要求所有控制父节点成功，否则 skipped。
5. any 要求恰好一个控制父节点成功，不是“至少一个”；否则 failed。
6. 满足以上规则才执行节点。

因此：有一条成功分支、另一条 skipped 分支时，all 跳过、any/selected 可成功；另一条 failed 分支时，all/any 阻断，selected 可忽略未选中的失败分支。新增规则是实际入边动作失败直接阻断其目标，不允许缺失已请求的产物继续执行。

requires 声明的边必须实际 succeeded；条件未选中的边不能仅因为其源节点成功就满足 requires。没有读取数据的 passive 边不改变现有 QQ 图步数及分支结果。

### 5.3 预算、超时与取消

- 节点执行（含 RepeatBlock 包装节点）沿用一次一计步。
- 已选中的 collect 尝试在绑定/执行前消耗一步；所有节点与边动作共享预算。
- passive 边和未选中的边不消耗动作步数，不让旧 QQ 图因新增边记录耗尽原预算。
- 边动作使用边超时；整图超时包含节点、边及嵌套子图。条件和 binder 为同步且必须非阻塞，不能声称 asyncio 能抢占同步阻塞代码。
- 边预算/超时错误记录在边，不写为源节点错误。普通失败沿既有 blocked/图出口失败传播。
- CancelledError 向调用方传播，不变成普通失败结果。
- 每轮 RepeatBlock 状态独立，沿用记录前缀/iteration，共享父预算与截止时间；最大轮数耗尽明确失败。

## 6. 状态和报告

ExecutionState.states/outputs 统一按字符串单元标识记录节点与边；GraphRun.executions 值改为 UnitExecution：

```python
@dataclass(frozen=True)
class UnitExecution:
    id: str
    kind: Literal['node', 'edge']
    status: Literal['succeeded', 'failed', 'skipped', 'blocked']
    output: object = None
    error_type: str | None = None
    duration: float = 0.0
    iteration: int = 0
```

GraphRun 保留 completed/failed、output、selected_edges、run_id、error_type。记录映射只读，单次运行互不污染。

基座不安全序列化业务对象。A 阶段上层 safe_trace 做最小适配：原 nodes 列表只序列化 kind=node，另增 edges 安全摘要（id/status/duration/iteration/error_type），不输出 collect 原始产物，保留原分页/顶层字段。C 再将业务 formatter 文件移到 strategy，不把业务白名单搬进 graph。

## 7. 迁移边界

本次有多项内部接口变化，不是只有一个类型替换：

- DecisionGraph 构造不再校验，调用方显式 compile；order/incoming/outgoing 在编译产物上。
- runner.run 接收 CompiledGraph。
- RepeatBlock 子图在父编译计划中持有编译产物。
- NodeExecution 改为 UnitExecution，新增边记录和动作执行。

现有源码和测试一并迁移，不留旧路径转发或两套 runner 接口。service 的 QQ 图在 A 中保持原节点/边声明，collect=None；C 再归位证据节点。

## 8. 验收

先最小图测试，再 `make test-argus`、编译与 `git diff --check`；临时配置/库，不接真实服务。

覆盖：编译错误/边标识冲突/边 requires 环；稳定顺序；收集产物和兄弟边可见性；条件异常不破坏源/兄弟；join 真值表与 requires 跳过/失败；边超时/计步/取消；重复子图编译与预算；并发运行状态隔离；安全 trace 不输出边原始产物；旧 QQ 图普通路径判断结果不变。
