# Plugin Runtime Manifest and Lifecycle Design｜插件运行时声明与生命周期设计

## 文档定位

本文定义并记录 Icarus 当前 Manifest 驱动的 Plugin Runtime 架构。该实现已经替代
`AgentRuntimeService` 中具体 Plugin 的手工构造、Tool 注册和 Event 订阅，并统一 Runtime 的
启动、恢复、收束、快照与停止流程。

SkillPlugin 的工具化重构不属于本文实现范围。它将在本文定义的 Runtime 契约完成后，作为第一
个领域 Plugin 接入并验证这些契约。

## 当前实现状态

- 8 个内置 Plugin 已经提供 `manifest.json` 和 Factory；
- `PluginRuntimeHost` 已负责发现、校验、Capability 注入、Tool 注册和 Event 自动订阅；
- `AgentRuntimeService` 已改为应用入口并委托 Host，不再手写具体 Plugin 拓扑；
- 默认 Tool 已由 `builtin-tools` Plugin 提供；
- Tool 执行身份已在同步、异步和流式入口中扁平透传；
- Blackboard Session History 已接入统一 Session 状态快照和恢复；
- Runtime 退出已接入 `quiesce`、现有 Agent Task 取消、快照和反序停止。

## 目标

- Runtime 启动时从受控目录发现 Plugin Manifest；
- Host 根据 Manifest 统一校验、构造和注册 Plugin；
- Plugin 通过 `PluginRegistration` 声明实际提供的 Capability、Tool 和状态接口；
- Host 根据 Manifest 自动建立现有 EventBus 的来源订阅；
- Agent Run 使用冻结的 Tool 集合，运行期间不发生能力热变更；
- Tool 通过扁平参数获得当前 Task 的必要执行信息；
- Plugin 继续通过现有运行控制 Event 发起“陷入内核”；
- Runtime 退出时复用现有任务取消，统一协调 Plugin 收束和状态快照；
- 按 Workspace 与 Session 保存可恢复状态，但不恢复运行栈。

## 非目标

第一阶段不实现：

- 运行时热安装、热卸载、热更新或替换 Plugin、Tool、Capability 和 Event 拓扑；
- 自动扫描当前 Workspace 或任意递归目录；
- Python entry points 发现；
- Runtime 启动时自动安装 Python 包；
- 多个 Plugin 版本在同一 Runtime 中并存；
- Capability 提供方重绑定；
- 恢复 Agent Run、Model Step、ToolCall、asyncio Task、Queue 或锁；
- 将 Plugin 内部辅助组件注册成子 Plugin；
- 拆分独立的 AgentKernelPlugin；
- SkillPlugin 工具化和 Skill Job 的领域实现。

## 核心原则

### 最小 Host 与“一切业务能力皆 Plugin”

Runtime Host 是 Plugin 的最小承载环境，不是业务 Plugin。它只负责：

- Manifest 发现与解析；
- 依赖图计算和校验；
- Factory 调用；
- Plugin、Capability、Tool、Event 和状态提供者注册；
- 生命周期编排、启动回滚、退出收束和诊断汇总。

实现中，`PluginRuntimeHost` 只保留状态机与生命周期编排；`PluginGraphBuilder` 负责发现、
Factory 校验、Capability/Tool/Event 图和外部模块生命周期；`PluginStateCoordinator` 负责
Workspace/Session 状态恢复与快照。它们都是 Host 内部普通组件，不注册为 Plugin。

持久化、输入、Blackboard、Agent、Skill、Tool 和输出桥接等可替换业务或基础能力均由 Plugin
提供。AgentFactory 是 AgentPlugin 的内部组件，Producer/Evolver AgentFactory 是 SkillPlugin 的内部组件；
它们不作为独立 Plugin 注册。Host 不理解具体领域状态、Event 业务语义或 Tool 行为。

Host 不做成 Plugin，避免出现“由谁发现、构造和启动 Runtime Plugin”的自举循环。

### AgentPlugin 保持执行入口

第一阶段不拆分 AgentKernelPlugin。AgentPlugin 继续作为 Agent 执行入口并承担 Harness 职责，
内部组合以下普通组件：

- AgentFactory 与无状态 ReActAgent；
- TaskChannelRegistry；
- ActiveAgentRun；
- 单次 Agent Run 的 Tool 快照；
- Task 取消和运行中 Context 处理。

这些内部组件不注册为子 Plugin。Runtime Host 不介入模型 Step、ToolCall 或 Agent 终态竞争。
`agent/factory.py` 创建主 AgentFactory，并把 Host 管理的同一个 ToolRegistry 传入；
AgentRuntimeService 不创建、持有或关闭 AgentFactory。SkillPlugin 的 Factory 同样创建并持有独立的
Producer/Evolver AgentFactory。两类 AgentFactory 都由所属 Plugin 在 `stop()` 中关闭。

### 声明与实现分离

Manifest 描述 Plugin 对 Runtime 的静态声明；Python Factory 返回实际实现对象。Host 校验二者
一致后一次性提交注册结果。Factory 和 Plugin 都不能直接修改 Runtime Registry。

## Runtime 状态机

```text
CREATED
  ↓
DISCOVERING
  ↓
RESOLVING
  ↓
VALIDATING
  ↓
STARTING
  ↓
RESTORING
  ↓
READY ⇄ RUNNING
  ↓
QUIESCING
  ↓
SNAPSHOTTING
  ↓
STOPPING
  ↓
STOPPED
```

- `CREATED`：Host 已创建，尚未读取 Manifest；
- `DISCOVERING`：扫描允许的目录并读取 Manifest，不导入 Plugin 代码；
- `RESOLVING`：解析 Python 依赖、Capability 依赖、Tool 所有权和 Event 图；
- `VALIDATING`：导入 Factory，校验 `PluginRegistration` 与 Manifest；
- `STARTING`：按依赖顺序启动 Plugin；
- `RESTORING`：恢复 Workspace 和 Session 状态；
- `READY`：运行图已冻结，可以接收 Task，当前没有活动 Task；
- `RUNNING`：至少有一个活动 Task；
- `QUIESCING`：拒绝新 Task，收束活动 Task 和 Plugin 后台工作；
- `SNAPSHOTTING`：导出并持久化 Plugin 状态及 Runtime 运行摘要；
- `STOPPING`：按依赖反序释放 Plugin、EventBus 和 Host 资源；
- `STOPPED`：实例终止，不允许再次 `start()`。

启动失败进入失败清理流程，但失败清理结束后的实例同样不可重启。应用需要重新创建 Host。

## Plugin 发现

### 允许来源

Runtime 只扫描两类来源：

1. Icarus 安装包内置 Plugin 目录；
2. `runtime.plugin_dirs` 显式配置的外部 Plugin 根目录。

内置源码目录约定为：

```text
apps/agent/src/agent_orchestration/plugins/<plugin-id>/manifest.json
```

安装后必须通过 Python 包资源定位内置目录，不能依赖进程当前工作目录。外部目录约定为：

```text
<configured-plugin-root>/
└── <plugin-id>/
    ├── manifest.json
    └── Python package files
```

Host 只检查显式根目录的直接子目录，不递归搜索任意 `manifest.json`。配置路径规范化后去重。
当前 Workspace、当前目录和 `$ICARUS_DATA_DIR/plugins` 都不会被隐式扫描；若需要加载后者，
必须把它显式写入 `runtime.plugin_dirs`。

外部 Plugin 的 entrypoint 顶级 Python 包名必须等于 `plugin_id` 将连字符替换为下划线后的结果，
且实际导入模块必须位于对应 Plugin 子目录内。Host 对外部导入根使用进程级引用计数，只有最后
一个使用该目录的 Runtime 停止后才移除路径和对应模块。

### 来源优先级与身份冲突

- 内置 Plugin ID 不允许重复；重复表示 Icarus 构建错误；
- 外部 Plugin 不得覆盖同名内置 Plugin；
- 多个外部 Plugin 使用同一 `plugin_id` 时，不按扫描顺序覆盖；
- 如果冲突涉及核心 Plugin，Runtime 启动失败；
- 如果冲突只涉及可选 Plugin，所有冲突项均禁用并写入诊断。

Runtime 启动配置持有 `required_plugin_ids`。核心或可选属性不写入 Manifest，Plugin 不能自行把
自己声明成核心组件。

## Manifest 契约

### 完整结构

```json
{
  "schema_version": 1,
  "plugin_id": "skill",
  "plugin_version": "1.0.0",
  "entrypoint": "apps.agent.src.agent_orchestration.plugins.skill.factory:create_plugin",

  "python_requires": [
    "PyYAML>=6,<7"
  ],

  "required_capabilities": [
    {
      "plugin_id": "persistence",
      "capability_id": "runtime",
      "version_spec": ">=1.0,<2.0"
    },
    {
      "plugin_id": "persistence",
      "capability_id": "session",
      "version_spec": ">=1.0,<2.0"
    },
    {
      "plugin_id": "persistence",
      "capability_id": "state_store",
      "version_spec": ">=1.0,<2.0"
    },
    {
      "plugin_id": "persistence",
      "capability_id": "redactor",
      "version_spec": ">=1.0,<2.0"
    },
    {
      "plugin_id": "blackboard",
      "capability_id": "conversation",
      "version_spec": ">=1.0,<2.0"
    }
  ],

  "provided_capabilities": [
    {
      "capability_id": "skill_management",
      "version": "1.0.0"
    }
  ],

  "provided_tools": [
    "skills_list",
    "skill_search",
    "skill_produce",
    "skill_evolve",
    "skill_job_status"
  ],

  "published_events": [
    "apps.agent.src.agent_orchestration.run_control.events.TaskContextInputEvent"
  ],

  "consumed_events": [
    "apps.agent.src.agent_orchestration.run_control.events.TaskContextInputResultEvent"
  ],

  "state_scopes": [
    "workspace",
    "session"
  ],
  "workspace_state_version": 1,
  "session_state_version": 1
}
```

所有数组字段允许为空数组，但字段本身必须存在，避免缺省值形成隐式行为。未使用某个状态范围
时，对应状态版本字段必须省略；声明状态范围时，对应版本字段必须是正整数。
声明任一状态范围的 Plugin 还必须显式依赖 `persistence/state_store`，避免状态接口存在但 Runtime
没有统一落盘能力。

### 字段语义

| 字段 | 语义 |
|---|---|
| `schema_version` | Manifest 格式版本；第一阶段只接受整数 `1` |
| `plugin_id` | Plugin 稳定身份，必须与目录名和 Factory 返回实例一致 |
| `plugin_version` | Plugin 实现版本 |
| `entrypoint` | `<python-module>:<factory-function>` |
| `python_requires` | 导入 Plugin 前检查的 PEP 508 包依赖 |
| `required_capabilities` | 需要直接调用的下游能力 |
| `provided_capabilities` | 本 Plugin 对外提供的直接调用能力 |
| `provided_tools` | 本 Plugin 向 Agent 提供的 Tool 名称 |
| `published_events` | Plugin 允许发布的 Event 类完整路径 |
| `consumed_events` | Plugin 声明消费的 Event 类完整路径 |
| `state_scopes` | Plugin 支持的持久状态范围，只允许 `workspace`、`session` |
| `workspace_state_version` | Workspace 状态格式版本 |
| `session_state_version` | Session 状态格式版本 |

### 版本规则

- `schema_version` 与 Host 支持版本精确匹配，不做猜测式兼容；
- `plugin_version` 和 Capability `version` 必须是有效的 PEP 440 版本，推荐使用
  `MAJOR.MINOR.PATCH`；
- `version_spec` 使用 PEP 440 Specifier，例如 `>=1.0,<2.0`；
- 预发布版本只有在 `version_spec` 明确允许时才参与匹配；
- `python_requires` 使用 PEP 508 Requirement；
- Python 依赖缺失或版本不匹配时，Host 记录错误并跳过对应 Plugin，不自动安装依赖；
- 如果被跳过的是核心 Plugin，Runtime 启动失败。

## Capability 模型

### 唯一身份

Capability 使用二元身份：

```text
plugin_id + capability_id
```

消费者必须同时声明提供方 Plugin 和能力 ID，第一阶段不支持配置重绑定：

```json
{
  "plugin_id": "persistence",
  "capability_id": "state_store",
  "version_spec": ">=1.0,<2.0"
}
```

这类依赖表示直接 Python 调用，也决定启动和停止顺序。只通过 Event 通信的 Plugin 不建立
Capability 依赖。第一阶段不提供仅用于排序、但没有实际接口的裸 Plugin 依赖。

### Plugin 内部组件

Plugin 内部专用能力不是公共 Capability。例如 SkillPlugin 的 Catalog、JobManager、Producer、
Evolver 和 Repository 都是 Plugin 内部组件，不注册为子 Plugin。只有出现真实跨 Plugin 调用者时，
才把某项能力提升为公共 Capability。

## Factory 与原子注册

### Factory 输入

Factory 使用扁平、明确的参数，不接收可任意查询的全局 Registry：

```python
def create_plugin(
    plugin_id: str,
    workspace_path: Path,
    session_id: str,
    config: Mapping[str, object],
    required_capabilities: Mapping[tuple[str, str], object],
    logger: logging.Logger,
) -> PluginRegistration:
    ...
```

`required_capabilities` 只包含 Manifest 已声明且版本匹配的能力。Workspace、Session 身份和
Logger 属于每个 Plugin 的基础构造参数，不需要再次声明成 Capability。

### Factory 输出

```python
@dataclass(frozen=True)
class ProvidedCapability:
    capability_id: str
    version: str
    value: object


@dataclass(frozen=True)
class PluginRegistration:
    plugin: BasePlugin
    capabilities: tuple[ProvidedCapability, ...] = ()
    tools: tuple[BaseTool, ...] = ()
    state_provider: PluginStateProvider | None = None
```

Factory 只构造并返回对象，不直接修改 PluginRegistry、ToolRegistry、EventBus 或状态存储。
Host 校验整个 `PluginRegistration` 后一次性注册；任何检查失败都丢弃整份结果，不留下半注册
状态。

校验至少包括：

- `plugin.plugin_id` 与 Manifest 一致；
- Capability ID、版本和数量与 Manifest 一致；
- Tool 名称与 Manifest 一致，并通过现有 ToolChecker；
- `state_provider` 与 `state_scopes` 一致；
- Event 类可以导入且继承当前 `Event`；
- Factory 没有多报或少报任何声明能力。

## Tool 注册与执行

### Plugin 提供 Tool

Plugin Tool 是 Plugin 内部的普通组件，不是子 Plugin。Factory 可以让 Tool 持有所属 Plugin
暴露的领域接口：

```python
PluginRegistration(
    plugin=skill_plugin,
    capabilities=(
        ProvidedCapability(
            capability_id="skill_management",
            version="1.0.0",
            value=skill_plugin,
        ),
    ),
    tools=(
        SkillsListTool(skill_plugin),
        SkillSearchTool(skill_plugin),
        SkillProduceTool(skill_plugin),
        SkillEvolveTool(skill_plugin),
        SkillJobStatusTool(skill_plugin),
    ),
    state_provider=skill_plugin,
)
```

Tool 名称在一个 Runtime 中全局唯一。重复 Tool 的处理不依赖扫描顺序：

- 冲突涉及两个核心 Plugin：Runtime 启动失败；
- 冲突涉及核心与可选 Plugin：禁用可选 Plugin；
- 冲突只涉及可选 Plugin：禁用所有冲突 Plugin；
- 禁用结果继续向 Capability 和 Event 依赖方传播。

### Tool 冻结

Host 完成校验后冻结 Runtime Tool Registry。每个 Agent Run 开始时，再根据允许的 Tool 名称
解析出本次 Run 的稳定 Tool 定义和执行对象快照。

这里的 Runtime 指当前单 Session 的 Plugin Runtime Host。目标多 Session 架构下，Manifest 或文件
变化不影响已加载的 SessionRuntime；Plugin 变更只在下一次 SessionRuntime 启动时生效。

### Tool 执行参数

不新增 ToolExecutionContext 包装类。必要身份沿现有调用链扁平透传：

```python
def invoke(
    arguments: dict[str, Any],
    *,
    task_id: str | None = None,
    run_id: str | None = None,
    step: int | None = None,
    task_messages: tuple[Message, ...] = (),
) -> ToolExecutionResult:
    ...


async def ainvoke(
    arguments: dict[str, Any],
    *,
    task_id: str | None = None,
    run_id: str | None = None,
    step: int | None = None,
    task_messages: tuple[Message, ...] = (),
) -> ToolExecutionResult:
    ...
```

参数由 ReActAgent 明确提供，并经 ToolExecutor、ObservableToolExecutor 原样传递。

- `task_id`：当前业务 Task；
- `run_id`：当前 Agent Run；
- `step`：产生该 ToolCall 的模型 Step；
- `task_messages`：从当前 Task 用户消息开始，到本批 ToolCall Assistant Message 为止的深拷贝
  不可变快照，不包含整个 Session 历史，也不包含尚未产生的 ToolResult。

同一并行 Tool 批次获得相同的 `task_messages` 快照。没有 RunControl 的直接 Agent 调用允许前三
个身份参数为 `None`。现有基础 Tool 可以忽略这些参数；需要关联任务的 Plugin Tool 按需使用。

不得通过 Hook ContextVar、全局变量或 Runtime Registry 隐式读取 Tool 的业务执行身份。Hook
保持只观测、不控制主流程。同步、异步和流式入口保持相同行为。

## Event 声明与自动订阅

### 构图

Manifest 直接声明当前 Python Event 类的完整导入路径。Host 在启动时：

1. 导入并校验所有 Event 类继承 `Event`；
2. 将每个 `published_events` 与相同类型的 `consumed_events` 精确匹配；
3. 为每一对来源 Plugin 和消费者 Plugin 调用现有
   `PluginManager.subscribe(subscriber_plugin_id, source_plugin_id)`；
4. 冻结订阅图。

一个 Event 可以有多个发布者和消费者。EventBus 继续只按来源 Plugin 路由，不解析 Event 类型；
消费者的 `accepts_event()` 继续执行运行时精确过滤。

### 校验和故障

- Plugin 发布未在 Manifest 中声明的 Event 时，绑定给该 Plugin 的受控 Publisher 拒绝发布并
  记录错误；
- `consumed_events` 没有任何有效发布者时，核心 Plugin 使启动失败，可选 Plugin 被禁用；
- `published_events` 没有消费者时允许启动，但写入启动诊断；
- Event 类路径无效或类型不继承 `Event` 时，该 Manifest 无效；
- 运行期间不改变 Event 声明和订阅关系。

## Plugin 发起“陷入内核”

不新增领域专用 Kernel 通道。任何 Manifest 获准的 Plugin 都可以发布现有 Event：

```python
await self.publish(
    TaskContextInputEvent(
        task_id=task_id,
        content=content,
    )
)
```

AgentPlugin 在 Manifest 中声明消费 `TaskContextInputEvent`，Host 自动建立来源订阅。完整路径为：

```text
Source Plugin
→ EventBus
→ AgentPlugin.consume
→ AgentPlugin.handle_task_operation
→ TaskChannel.add_context
→ ReActAgent 下一安全检查点
```

AgentPlugin 发布 `TaskContextInputResultEvent`。需要知道投递结果的来源 Plugin 声明消费该 Event，
并通过 `request_event_id` 关联请求。结果只描述投递状态，不改变来源 Plugin 自己的业务终态。

Plugin 不得直接访问 ReActAgent、修改 Blackboard、操作 TaskChannel，或通过 Hook 改变执行。

## 状态快照与恢复

### 可选状态接口

Plugin 负责解释和序列化自己的状态，PersistencePlugin 负责统一落盘，Host 只调度和汇总：

```python
class PluginStateProvider(Protocol):
    async def restore_workspace_state(
        self,
        state: Mapping[str, object],
        *,
        state_version: int,
    ) -> None:
        ...

    async def restore_session_state(
        self,
        state: Mapping[str, object],
        *,
        state_version: int,
    ) -> None:
        ...

    async def snapshot_workspace_state(
        self,
    ) -> Mapping[str, object] | None:
        ...

    async def snapshot_session_state(
        self,
    ) -> Mapping[str, object] | None:
        ...
```

状态必须是 JSON 可序列化映射。Plugin 可以把 `state_provider` 指向自己，也可以返回一个内部
普通组件。无状态 Plugin 返回 `None`，且 Manifest 的 `state_scopes` 必须为空。

### 保存内容

Runtime 保存：

- Plugin 自己声明的 Workspace 和 Session 状态；
- Blackboard Session History；
- Plugin 后台 Job 的最终状态或 `interrupted`；
- 本次启用与禁用 Plugin、版本、Manifest Hash 和诊断；
- Capability 绑定、Tool 所有权和 Event 订阅拓扑摘要。

Runtime 不保存：

- 活动 Agent Run；
- LLM 流式连接；
- 当前 Model Step；
- 执行到一半的 ToolCall；
- asyncio Task、Queue、锁或 Python 对象引用。

原则是“恢复持久状态，不恢复运行栈”。下一次 SessionRuntime 启动时重新构造所有 Plugin 和运行图，
再按依赖顺序恢复 Workspace 状态和 Session 状态。

`state_version` 是持久状态格式的兼容契约。`plugin_version` 和 Manifest Hash 继续随快照保存，用于
记录状态来源和诊断，但二者发生变化本身不阻止恢复。只要 `state_version` 与当前 Manifest 声明
相同，Host 就把状态交给当前 StateProvider 恢复；Plugin 改变状态格式时必须提升对应版本。

`state_version` 不匹配或 StateProvider 实际恢复失败时，核心 Plugin 失败会终止 SessionRuntime 启动；
可选 Plugin 会被停止并禁用、保留原快照，并由 Host 按 Capability 和 Event 依赖继续级联处理。级联
触及核心 Plugin 时同样终止启动。第一阶段不做状态迁移，也不自动丢弃或覆盖不兼容快照。

## Plugin 生命周期

### 接口

```python
class BasePlugin:
    async def start(self) -> None:
        ...

    async def quiesce(self) -> None:
        ...

    async def drain(self) -> None:
        ...

    async def stop(self) -> None:
        ...
```

- `start()`：打开本 Plugin 需要的资源；业务 Event 在 Runtime READY 前不会进入；
- `quiesce()`：停止产生新的领域工作，但继续消费完成收束所需的 Event；
- `drain()`：让已接受工作进入完成、取消或 `interrupted` 等稳定状态；
- `stop()`：释放 Worker、连接、文件和其他资源，必须幂等。

### 启动顺序

```text
发现并解析 Manifest
→ 校验 Python 依赖
→ 解析 Capability 和 Event 图
→ 按 Capability 依赖顺序调用 Factory
→ 校验并原子注册 PluginRegistration
→ 按依赖顺序 start Plugin
→ 恢复 Workspace 状态
→ 恢复 Session 状态
→ 冻结 Plugin、Capability、Tool 和 Event 图
→ 启动业务 Event 接收
→ READY
```

Plugin 在 `start()` 和状态恢复阶段不得发布业务 Event。

### 正常运行

`READY` 后 Runtime 使用冻结运行图。现有任务所有权保持不变：

- UserInputPlugin 拥有输入 FIFO 和 InputFinished；
- BlackboardPlugin 拥有 Session History；
- AgentPlugin 拥有 Agent Run、Task 控制和唯一 Agent 终态；
- TaskChannel 拥有取消、运行中 Context 和历史安全检查点；
- 各领域 Plugin 拥有自己的后台 Job 与领域状态；
- OutputBridgePlugin 向应用层提供 Event 订阅。

Host 不解释具体 Event，不改变 Agent Step，也不持有领域状态。

### 退出顺序

```text
收到结束命令
→ Runtime 进入 QUIESCING，拒绝新 Task
→ 所有 Plugin quiesce
→ 通过 AgentPlugin 已有确定性取消入口结束活动 Agent Task
→ 等待 Agent 唯一终态和必要 Event 路由完成
→ 各 Plugin drain，将后台工作收束为稳定状态
→ Runtime 进入 SNAPSHOTTING
→ Plugin 导出 Workspace / Session 状态
→ PersistencePlugin 统一落盘
→ Plugin 按 Capability 依赖反序 stop
→ 停止 EventBus
→ Runtime 进入 STOPPED
```

Runtime 停止不创建第二套 Agent 终止协议。活动 Agent Task 继续使用现有
TaskCancelRequestedEvent、TaskChannel 和 AgentCancelledEvent 语义。已经发生的 Tool 外部副作用
不回滚。

Plugin 后台任务不属于 Agent Task，由所属 Plugin 在 `quiesce()`、`drain()` 和 `stop()` 中收束。
Runtime 只调用生命周期接口，不解释 Job 业务状态。

### 超时与清理错误

- Runtime 可以设置统一退出超时，但不创造新的业务终态；
- Agent Task 仍通过现有取消路径结束；
- Plugin `drain()` 超时后继续调用该 Plugin 的 `stop()`；
- Plugin 必须在 `stop()` 中把未完成后台 Job 收束为 `interrupted`；
- 一个可选 Plugin 的失败不能阻止其他 Plugin 快照和清理；
- 核心 Plugin 失败使退出结果带失败明细，但 Host 仍尽力完成剩余清理；
- Host 汇总所有失败，不以最后一个错误覆盖前面的错误。

## 启动失败与可选 Plugin 隔离

核心 Plugin 失败时，Runtime 不进入 READY。可选 Plugin 失败时，Host 禁用整个注册单元：

- Plugin 实例；
- Capability；
- Tool；
- Event 订阅；
- State Provider。

依赖被禁用 Capability 的可选 Plugin 递归禁用；任何核心 Plugin 最终缺少依赖都使启动失败。

启动回滚只处理已经完成相应阶段的对象：

```text
已启动 Plugin：按依赖反序 stop
已打开状态存储：关闭
已构造但未注册对象：直接丢弃
已注册但未启动对象：从临时构建结果中丢弃
未进入 READY：不接受 Task，不写正常运行快照
```

## 冻结 Runtime 快照

进入 READY 前，Host 形成不可变运行摘要：

- Workspace ID 和 Session ID；
- 启用 Plugin 的 ID、版本、来源和 Manifest Hash；
- 禁用 Plugin 及原因；
- Capability 提供者与消费者；
- Tool 名称与所属 Plugin；
- Event 发布者、消费者和来源订阅；
- Plugin 启动顺序与反序停止顺序；
- 各 Plugin 状态范围和状态版本。

该摘要既是当前 Runtime 的能力事实，也是退出快照和启动诊断的索引。它不包含可执行 Python
对象或运行栈。

## 第一阶段验收

### 发现与校验

- 能从内置目录和配置显式目录发现 Plugin；
- 不扫描当前 Workspace 或其他隐式目录；
- 能在导入 Plugin 代码前识别 Python 依赖缺失；
- 能检测无效 Manifest、重复 Plugin、重复 Tool、能力缺失、版本不兼容和循环依赖；
- 核心 Plugin 失败时不能进入 READY；
- 可选 Plugin 失败时，其完整注册单元被移除并留下诊断。

### 注册与运行

- Factory 不直接修改 Registry，失败后不留下半注册状态；
- Manifest 与 PluginRegistration 的 Plugin、Capability、Tool 和状态声明严格一致；
- Manifest 生成的 Event 来源订阅与当前手工拓扑行为一致；
- Plugin 发布未声明 Event 时被拒绝；
- Agent Run 使用稳定 Tool 快照；
- Tool 同步、异步和流式执行均收到一致的 `task_id`、`run_id`、`step` 和 `task_messages`；
- Plugin 可以通过现有 TaskContextInputEvent 发起运行中介入并获得 Result Event。

### 退出与恢复

- 退出后不再接受新 Task；
- 活动 Agent Task 使用现有取消链路进入唯一 cancelled 终态；
- Plugin 后台工作在快照前进入可保存状态；
- Workspace 与 Session 状态分别保存并可在新 Runtime 中恢复；
- 不恢复 Agent Run、Model Step 或 ToolCall；
- Plugin 按依赖反序停止；
- 单个可选 Plugin 清理失败不阻断其他资源释放；
- 最终退出结果包含所有快照和清理失败明细。

## 后续接入

Runtime 契约实现并验证后，SkillPlugin 作为第一个领域接入方：

- 停止自动检索和自动维护；
- 通过 PluginRegistration 提供 Skill Capability 和五个 Tool；
- 使用 Tool 的扁平执行参数创建后台 Job；
- 使用现有运行中介入 Event 通知仍活跃的 Agent；
- 使用 Workspace / Session 状态接口保存 Job 和领域状态。

该接入属于后续独立设计与实施，不反向修改本文的通用 Runtime 契约。
