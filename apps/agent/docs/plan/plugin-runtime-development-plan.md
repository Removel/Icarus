# Plugin Runtime Development Plan｜插件运行时开发计划

## 目标

在 Agent 编排层中实现统一 Plugin Runtime，使 Agent、Blackboard、Skill、Knowledge、Memory、用户输入、UI 和未来自定义能力都可以作为 Plugin 异步生产和消费 Event。

计划分为三个阶段：

1. Plugin Runtime 核心；
2. AgentPlugin；
3. BlackboardPlugin。

阶段一完成后即可验证多生产者、多消费者和来源路由；阶段二将当前 ReActAgent 接入插件系统；阶段三再实现上下文汇聚。

## 当前状态

- 阶段一 Plugin Runtime 核心：已完成；
- 阶段二 AgentPlugin：已完成；
- 阶段三 BlackboardPlugin：等待 Context 聚合协议 Gate；
- Plugin Runtime 测试：`16 passed`；
- Plugin Runtime + AgentPlugin 测试：`18 passed`；
- 全量测试：`67 passed`；
- 真实模型插件链路：已验证 Blackboard 测试 Producer → AgentPlugin → ReActAgent Stream → WebUI 测试 Plugin，输出 `PLUGIN_RUNTIME_OK`。
- Plugin 消费入口：已支持 `consume(source_plugin_id, event)`，目标 Plugin 可以同时依据来源和 Event 子类处理信息。

## 层级与目录

该架构属于：

```text
Agent Orchestration Layer
└── Runtime Infrastructure
```

建议源码结构：

```text
apps/agent/src/agent_orchestration/
├── capability/
├── events/
├── hooks/
├── tools/
├── plugin_runtime/
│   ├── __init__.py
│   ├── base_plugin.py
│   ├── types.py
│   ├── plugin_registry.py
│   ├── plugin_runtime.py
│   ├── plugin_manager.py
│   ├── event_bus.py
│   └── wrappers/
│       ├── __init__.py
│       ├── observable_event_bus.py
│       └── observable_plugin_runtime.py
└── plugins/
    ├── __init__.py
    ├── agent_plugin.py
    ├── blackboard_plugin.py
    └── events.py
```

测试镜像：

```text
apps/agent/test/agent_orchestration/
├── plugin_runtime/
└── plugins/
```

## 已确认的实现原则

- 所有系统组件统一抽象为 Plugin；
- Plugin 可以同时生产和消费 Event；
- 每个 Plugin 只有一个统一消费入口；
- 一个 Plugin 的统一入口接收所有已订阅来源的 Event；
- Registry 只按照来源 Plugin 维护订阅关系；
- Registry 和 EventBus 不检查 Event 具体类型；
- Plugin 自行判断 Event 是否处理或忽略；
- EventBus 只是异步传输通道；
- 生产者只等待 EventBus 接受 Event；
- 生产者不等待目标 Plugin 消费完成；
- 一个慢 Plugin 不应阻塞生产者或其他 Plugin；
- Plugin 默认按自身统一入口顺序消费；
- Plugin 内部确需并发时自行调度；
- AgentPlugin 正常任务只消费 BlackboardPlugin；
- BlackboardPlugin 汇聚上下文后再生产 Agent Context Event；
- ReActAgent 不依赖 Plugin、Registry 和 EventBus；
- Hook 负责持久化、观测和监督，不替代 EventBus。

## 初版技术策略

### 本地异步运行时

初版限定为单进程 Python 异步运行时：

- `asyncio`；
- 单进程内 Plugin；
- 内存 Event 通道；
- 不实现跨进程和分布式通信；
- 不实现事件持久化恢复。

该限制用于先验证 Plugin 契约和事件流，不限制未来替换底层通道。

### 发布语义

```text
Plugin
→ await bus.publish(plugin_id, event)
→ Event 进入 EventBus ingress
→ publish 返回
→ EventBus 异步路由
→ 目标 Plugin 异步消费
```

`publish()` 成功只表示 EventBus 已接受 Event。

### 统一消费通道

每个 Plugin Runtime 维护一个统一输入通道：

```text
多个来源 Event
→ Plugin Runtime inbox
→ 一个消费循环
→ plugin.consume(source_plugin_id, event)
```

初版使用 `asyncio.Queue` 实现，但队列封装在 PluginRuntime 内部，Plugin 和 EventBus 不直接依赖队列实现细节。

### 队列容量

队列容量通过构造参数配置，不写死业务值：

```python
inbox_maxsize: int = 0
ingress_maxsize: int = 0
```

`0` 表示初版默认不限制。容量限制、溢出和丢弃策略在真实运行数据出现后单独设计。

### 消费失败

初版语义：

- 单个 Event 消费失败被 Runtime 捕获；
- 记录错误和失败计数；
- Hook 上报；
- Worker 继续消费后续 Event；
- 不自动重试；
- 不重新发布失败 Event；
- 不实现死信队列。

重试和死信在后续阶段设计，避免初版出现重复业务副作用。

### 生命周期

初版 Plugin 状态：

```text
CREATED
→ STARTING
→ RUNNING
→ STOPPING
→ STOPPED

任意运行失败
→ FAILED
```

初版只支持：

- 注册；
- 启动；
- 停止；
- 注销已停止 Plugin。

不实现自动重启、热升级和动态代码重载。

## 阶段一：Plugin Runtime 核心

### 任务一：定义 Plugin 通信类型

**新增文件**

- `apps/agent/src/agent_orchestration/plugin_runtime/types.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugin_runtime/test_types.py`

**开发内容**

- `PluginId` 类型别名；
- `PluginStatus`；
- `PublishedEvent`；
- `Subscription`；
- `PluginRuntimeSnapshot`；
- `PluginConsumeResult` 或等价的内部消费结果。

`PublishedEvent` 至少包含：

```python
@dataclass(frozen=True)
class PublishedEvent:
    source_plugin_id: str
    event: Event
```

Event 自身不写入来源 Plugin，保持当前纯能力内核边界。

**验证**

- 类型不可变；
- Event 与来源身份分离；
- Snapshot 不暴露 Queue、Task 等活对象；
- 数据可以转换为 Hook 观测快照。

### 任务二：定义 BasePlugin

**新增文件**

- `apps/agent/src/agent_orchestration/plugin_runtime/base_plugin.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugin_runtime/test_base_plugin.py`

**开发内容**

定义最小异步 Plugin 接口：

```python
class BasePlugin(ABC):
    plugin_id: str

    async def start(self) -> None:
        ...

    async def consume(
        self,
        source_plugin_id: str,
        event: Event,
    ) -> None:
        ...

    async def stop(self) -> None:
        ...
```

提供 EventBus 发布绑定能力，但不把 Registry 和路由暴露给 Plugin：

```python
await self.publish(event)
```

`publish()` 由 Plugin Runtime 或 Manager 在注册时绑定到 EventBus。

**验证**

- Plugin 只能以自身 `plugin_id` 发布；
- 未绑定 EventBus 时发布产生明确异常；
- 一个消费入口可以接收不同 Event 子类；
- 一个消费入口可以识别 Event 来源 Plugin；
- Plugin 可以在 `consume()` 内再次发布 Event。

### 任务三：实现 PluginRegistry

**新增文件**

- `apps/agent/src/agent_orchestration/plugin_runtime/plugin_registry.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugin_runtime/test_plugin_registry.py`

**开发内容**

- 注册 Plugin；
- 注销 Plugin；
- 防止重复 `plugin_id`；
- 按来源 Plugin 建立订阅；
- 取消订阅；
- 查询某来源的全部目标 Plugin；
- 移除 Plugin 时清理相关订阅；
- 保持订阅者注册顺序；
- 返回副本，禁止调用方修改内部关系。

公开语义：

```python
registry.register(plugin)
registry.unregister(plugin_id)
registry.subscribe(
    subscriber_plugin_id=...,
    source_plugin_id=...,
)
registry.unsubscribe(subscription_id)
registry.get_subscribers(source_plugin_id)
```

Registry 不解析 Event。

**验证**

- 两个来源可以被同一 Plugin 订阅；
- 一个来源可以拥有多个 Subscriber；
- 重复订阅行为明确；
- 注销后路由关系正确清理；
- 未注册来源可以先建立订阅还是必须先注册，需要测试固定为“双方都已注册后才能订阅”；
- Event 类型不参与 Registry API。

### 任务四：实现 PluginRuntime

**新增文件**

- `apps/agent/src/agent_orchestration/plugin_runtime/plugin_runtime.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugin_runtime/test_plugin_runtime.py`

**开发内容**

每个 PluginRuntime 管理：

- Plugin 实例；
- 一个统一 inbox；
- 一个消费 Worker；
- 生命周期状态；
- `processed_count`；
- `failed_count`；
- `last_event_at`；
- `last_error`。

主要接口：

```python
await runtime.start()
await runtime.enqueue(published_event)
await runtime.stop()
runtime.snapshot()
```

消费循环：

```text
inbox.get
→ plugin.consume(source_plugin_id, event)
→ 成功或失败记账
→ inbox.task_done
→ 继续下一条
```

默认串行消费。

**验证**

- 多来源 Event 进入同一 inbox；
- Plugin 按入队顺序消费；
- 消费失败不终止 Worker；
- 失败后下一条 Event 仍可处理；
- start/stop 幂等语义明确；
- stop 后拒绝新 Event；
- Snapshot 与运行状态一致。

### 任务五：实现 EventBus

**新增文件**

- `apps/agent/src/agent_orchestration/plugin_runtime/event_bus.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugin_runtime/test_event_bus.py`

**开发内容**

EventBus 管理：

- ingress；
- Router Worker；
- PluginRegistry；
- Plugin Runtime 查询能力。

主要接口：

```python
await bus.start()
await bus.publish(source_plugin_id, event)
await bus.stop()
```

发布流程：

```text
publish
→ 校验来源 Plugin 已注册并运行
→ PublishedEvent 放入 ingress
→ publish 返回
```

路由流程：

```text
Router Worker 获取 PublishedEvent
→ Registry 查询 source_plugin_id 的 Subscribers
→ 向每个目标 Runtime.enqueue
→ 不调用 Plugin.consume
→ 不等待 Plugin 处理完成
```

EventBus 不解析 Event 类型。

**验证**

- publish 在 Subscriber 完成前返回；
- 慢 Subscriber 不阻塞 Producer；
- 同一 Event 可以扇出到多个 Subscriber；
- 无 Subscriber 的 Event 可以正常接受和丢弃；
- EventBus 不根据 Event 子类筛选；
- 未注册来源发布失败；
- stop 后发布失败；
- 路由顺序保持 ingress 顺序。

### 任务六：实现 PluginManager

**新增文件**

- `apps/agent/src/agent_orchestration/plugin_runtime/plugin_manager.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugin_runtime/test_plugin_manager.py`

**开发内容**

PluginManager 集中组装：

- PluginRegistry；
- PluginRuntime；
- EventBus；
- Plugin 发布能力绑定；
- 所有 Runtime 的生命周期。

主要接口：

```python
await manager.register(plugin)
await manager.unregister(plugin_id)
manager.subscribe(subscriber_id, source_id)
manager.unsubscribe(subscription_id)
await manager.start()
await manager.stop()
manager.get_runtime_snapshot(plugin_id)
```

生命周期顺序：

```text
start:
Registry 已准备
→ Runtime start
→ EventBus start

stop:
拒绝新 publish
→ EventBus 停止接收
→ 处理或放弃 ingress 中剩余 Event
→ Runtime stop
```

初版停止策略使用明确的“Drain 已接受事件”：

- EventBus 停止接受新 Event；
- 等待 ingress 路由完成；
- 等待 Plugin inbox 消费完成；
- 再停止 Worker。

为防止无限等待，`stop(timeout=...)` 提供超时；超时后取消 Worker并记录未完成数量。

**验证**

- 一次调用可以启动完整系统；
- Plugin 发布能力在注册后可用；
- 停止顺序不丢失已接受 Event；
- 停止超时可以收口；
- 注销运行中的 Plugin 被拒绝；
- Manager 状态与 Runtime 状态一致。

### 任务七：实现 Runtime Hook 观测

**新增文件**

- `apps/agent/src/agent_orchestration/plugin_runtime/wrappers/__init__.py`
- `apps/agent/src/agent_orchestration/plugin_runtime/wrappers/observable_event_bus.py`
- `apps/agent/src/agent_orchestration/plugin_runtime/wrappers/observable_plugin_runtime.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugin_runtime/wrappers/test_runtime_observability.py`

**开发内容**

基础观测边界：

```text
event.publish / before / after / error
event.route / before / after / error
plugin.consume / before / after / error
plugin.lifecycle / before / after / error
```

约束：

- Hook 不改变 Event；
- Hook 不控制订阅关系；
- Hook 不替代 EventBus；
- Hook 失败不终止 Runtime；
- 不为 AgentTextDeltaEvent 自动执行重持久化；
- 高频 Event 的持久化采样由具体 Hook 决定。

**验证**

- publish、route、consume 可通过 correlation_id 关联；
- Event ID 保持不变；
- Hook 失败不影响投递；
- 慢 Hook 不应在初版造成不可控阻塞；如果沿用同步 Hook，则明确由 Handler 自行快速入队。

### 任务八：Runtime 集成验证

**新增测试**

- `apps/agent/test/agent_orchestration/plugin_runtime/test_runtime_integration.py`

**场景**

1. Producer A 和 Producer B 发布不同 Event；
2. Subscriber 同时订阅 A 和 B；
3. Subscriber 从同一个 `consume(source_plugin_id, event)` 入口接收两者；
4. Subscriber 能够看到每个 Event 的来源 Plugin；
5. Subscriber 自行识别不同 Event 子类；
6. Subscriber 消费后再次发布新 Event；
7. 下游 Plugin 正常接收；
8. 慢 Subscriber 不阻塞 Producer；
9. 失败 Subscriber 不影响其他 Subscriber；
10. Shutdown Drain 已接受 Event。

**阶段一完成标准**

- Plugin Runtime 核心不依赖 AgentPlugin 和 BlackboardPlugin；
- Registry 只按来源路由；
- EventBus 不检查 Event 类型；
- 每个 Plugin 一个统一消费入口；
- Producer 只等待 EventBus 接受；
- 全量现有 Agent 测试不回归。

## 阶段二：AgentPlugin

### 前置 Gate

实现前确认 Blackboard 向 Agent 提供的最小上下文 Event 字段。

初版建议使用：

```python
@dataclass(frozen=True, kw_only=True)
class AgentContextReadyEvent(Event):
    model_role: LLMRole
    system_prompt: str
    history_messages: list[Message]
    input_prompt: str
    input_images: list[ImagePart]
    tools: list[str] | None
```

如果 Blackboard 需要额外插件上下文，应在 system prompt 或 Message 中完成整合，不把任意 `dict` 直接泄漏给 ReActAgent。

### 任务九：定义 AgentPlugin Event

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/events.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/test_plugin_events.py`

**开发内容**

- `AgentContextReadyEvent`；
- 必要的任务或上下文关联字段；
- 直接复用当前 Agent Stream Event 作为 AgentPlugin 输出；
- 不重复定义 TextDelta、ToolStarted、ToolCompleted、Completed、Error。

### 任务十：实现 AgentPlugin

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/agent_plugin.py`
- `apps/agent/src/agent_orchestration/plugins/__init__.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/test_agent_plugin.py`

**开发内容**

- 继承 BasePlugin；
- 只处理 BlackboardPlugin 生产的 AgentContextReadyEvent；
- 忽略其他 Event；
- 通过 AgentFactory 获取对应 ReActAgent；
- 调用 `astream()`；
- 每得到一个 Agent Stream Event，立即发布到 EventBus；
- 只等待 EventBus 接受；
- 不等待 WebUI、TTS、Memory 等消费者；
- Agent Stream 异常时发布 AgentErrorEvent 后保持失败可观测；
- stop 时取消当前 Agent Stream 任务。

**验证**

- AgentPlugin 不直接依赖 Skill、Knowledge、Memory；
- TextDelta 按 Stream 到达顺序发布；
- 工具事件完整发布；
- Completed 正常发布；
- AgentError 正常发布；
- 慢下游不阻塞 AgentPlugin 读取后续 Stream；
- AgentPlugin 不修改 ReActAgent Event。

### 任务十一：AgentPlugin 真实模型验证

**场景**

- 使用当前真实 `thinking` 模型；
- Blackboard 测试 Producer 发布 AgentContextReadyEvent；
- AgentPlugin 消费后运行 ReActAgent；
- WebUI 测试 Plugin 接收文字和工具 Event；
- Producer publish 返回不等待 Agent 完成；
- AgentPlugin Event 最终完整到达 WebUI Plugin。

**阶段二完成标准**

- 当前 Agent Stream 能通过 Plugin Runtime 流转；
- AgentPlugin 只消费 Blackboard 来源；
- ReActAgent 代码不新增 Plugin/EventBus 依赖；
- 真实文字流和工具流可通过 EventBus 消费。

## 阶段三：BlackboardPlugin

### 前置 Gate

在实现 Blackboard 前，需要单独确认：

- Context 来源插件清单；
- 本次任务需要等待哪些来源；
- 必选与可选 Context；
- 空结果语义；
- 失败降级；
- 等待超时；
- 多任务并发时的 task_id/correlation_id；
- History 的所有权；
- Agent 结果如何回写 Blackboard；
- Context Ready Event 的最终字段。

这些问题未确认前，不直接实现通用 Blackboard 聚合器。

### 任务十二：定义 Context Event 契约

**计划新增**

- UserInput Event；
- Skill Context Event；
- Knowledge Context Event；
- Memory Context Event；
- Context Source Completed/Failed 状态；
- AgentContextReadyEvent 最终版本。

所有 Context Event 使用 correlation_id 关联同一次任务。

### 任务十三：实现 Blackboard 状态

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/blackboard_state.py`

**开发内容**

- 按任务维护上下文；
- 记录已到达来源；
- 记录空结果、失败和完成状态；
- 生成不可变 Context Snapshot；
- 完成任务后清理或归档状态。

### 任务十四：实现 BlackboardPlugin

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/blackboard_plugin.py`

**开发内容**

- 订阅 UserInput、Skill、Knowledge、Memory 和 AgentPlugin；
- 消费 Context Event；
- 聚合本次任务上下文；
- 满足就绪条件后生产一次 AgentContextReadyEvent；
- 消费 AgentCompletedEvent 和 AgentErrorEvent；
- 更新当前任务状态；
- 不直接调用 ReActAgent。

### 任务十五：Blackboard 集成验证

**场景**

- UserInput 先到；
- Skill、Knowledge、Memory 异步返回；
- Blackboard 等待必需来源；
- 空 Context 正确计为完成；
- 所有要求满足后只发布一次 AgentContextReadyEvent；
- AgentPlugin 消费并执行；
- Agent 结果回到 Blackboard；
- 多任务 correlation_id 互不污染。

## 分层测试命令

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugin_runtime -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test -q
```

静态检查：

```bash
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src \
  apps/agent/test

git diff --check
```

## 风险与控制

### Plugin 消费阻塞

风险：Plugin 的 `consume()` 执行时间过长。

控制：

- 每个 Plugin 独立 Runtime；
- Producer 不等待 Consumer；
- 不同 Plugin 相互隔离；
- 单个 Plugin 内默认串行；
- Plugin 自己决定是否内部并发。

### 无界队列增长

风险：初版默认无界队列可能导致内存增长。

控制：

- 所有 Queue 支持 maxsize 参数；
- Snapshot 暴露 queue_size；
- Hook 上报积压；
- 真实负载出现后再确定溢出策略；
- 不在初版静默丢 Event。

### 事件循环

风险：Plugin A 和 B 互相生产 Event，形成无限循环。

控制：

- Event 保留 correlation_id；
- Hook 可以观测事件链；
- 初版不做启发式循环阻断；
- 未来编排策略控制 Event TTL 或 hop_count；
- 不在 EventBus 内猜测业务循环。

### 重复副作用

风险：自动重试造成重复写入、重复 TTS 或重复动作。

控制：

- 初版不自动重试；
- 消费失败只记录；
- 后续需要重试时引入幂等键和明确策略。

### Shutdown 丢事件

风险：进程停止时已接受 Event 尚未消费。

控制：

- Manager stop 使用 Drain；
- 支持 timeout；
- 超时后记录未完成事件数；
- 不声称未处理 Event 已成功。

### Runtime 与业务耦合

风险：EventBus 开始识别 Agent、Blackboard 或 Event 类型。

控制：

- EventBus API 只使用 source_plugin_id 和 Event；
- Registry 只返回 subscriber IDs；
- Event 类型判断只存在于 Plugin.consume；
- 使用架构守卫测试禁止 Runtime 导入具体 Plugin。

## 推荐提交拆分

1. Plugin 通信类型与 BasePlugin；
2. PluginRegistry；
3. PluginRuntime；
4. EventBus；
5. PluginManager 与 Shutdown；
6. Runtime Hook；
7. Runtime 集成测试；
8. AgentPlugin；
9. AgentPlugin 真实验证；
10. Blackboard Event 契约；
11. BlackboardPlugin；
12. 文档同步。

## 总体验收标准

- 多个 Producer 可以异步发布；
- 一个 Plugin 从统一入口消费多个来源；
- Registry 只按来源路由；
- EventBus 不解析 Event 类型；
- Producer 只等待 Bus 接受；
- 慢 Plugin 不阻塞 Producer 和其他 Plugin；
- 消费失败不终止整个 Runtime；
- Shutdown 能处理已接受 Event；
- AgentPlugin 只消费 BlackboardPlugin；
- Agent Stream Event 原样发布；
- BlackboardPlugin 汇聚 Context 后再提供给 AgentPlugin；
- Hook 可以观测发布、路由、消费和生命周期；
- ReActAgent 不依赖 Plugin Runtime；
- 现有 49 条 Agent 测试不回归。
