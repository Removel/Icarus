# Plugin Runtime Manifest and Lifecycle Development Plan｜插件运行时声明与生命周期开发计划

## 目标

基于 `apps/agent/docs/arch/plugin-runtime-manifest-lifecycle-design.md`，把当前由
`AgentRuntimeService` 手工完成的 Plugin 构造、注册、订阅、Tool 装配与退出清理迁移到一个
Manifest 驱动的 Runtime Host。完成后，现有 Agent/TUI 行为保持兼容，SkillPlugin 的自动检索
和自动维护行为暂不修改。

## 实施原则

- 先建立通用契约和纯解析逻辑，再迁移现有内置 Plugin；
- Runtime Host 只负责发现、校验、装配和生命周期，不解释领域 Event 与状态；
- Factory 只返回 `PluginRegistration`，不能直接操作 Registry；
- 内置 Plugin 与显式外部目录使用相同 Manifest 模型；
- Tool、Capability 和 Event 图在 READY 前冻结；
- Runtime 退出复用现有 Task 取消，不增加第二套终止机制；
- 保持 ReActAgent 无状态，保持 EventBus 按来源路由；
- 每一步先运行最小测试，全部迁移后再运行 Agent 与 TUI 全量测试。

## 兼容边界

- `AgentRuntimeService` 的 `start`、`submit`、`subscribe_events`、`cancel_task` 和 `stop` 公共行为
  保持不变；
- `BasePlugin.start`、`drain`、`stop` 保留，并新增默认空实现 `quiesce`；
- 现有 Tool 继续实现 `BaseTool`，但同步和异步执行签名增加可忽略的扁平关键字参数；
- 当前 SkillPlugin 继续自动处理 UserInput 与 AgentCompleted Event，待 Runtime 改造稳定后再独立
  工具化；
- 现有手工构造测试可以继续直接使用 PluginManager、ToolRegistry 和具体 Plugin。

## 阶段一：Manifest 与诊断模型

新增 `plugin_runtime/manifest.py`：

- `PluginManifest`；
- `RequiredCapabilityManifest`；
- `ProvidedCapabilityManifest`；
- JSON 字段、ID、版本、entrypoint、状态范围校验；
- Manifest 文件 Hash；
- Python 依赖检查结果。

新增 `plugin_runtime/discovery.py`：

- 通过包资源发现内置 `plugins/*/manifest.json`；
- 扫描 `runtime.plugin_dirs` 直接子目录；
- 路径规范化和去重；
- 内置与外部 Plugin ID 冲突检测；
- 发现阶段不导入 Plugin Python 代码。

新增 `plugin_runtime/diagnostics.py` 保存启用、禁用和失败原因。

定向测试：Manifest 字段、版本、状态范围、路径发现、重复 ID、Python 包缺失与版本不兼容。

## 阶段二：Registration、Capability 与依赖图

新增 `plugin_runtime/registration.py`：

- `ProvidedCapability`；
- `PluginRegistration`；
- `PluginStateProvider` Protocol；
- Capability 使用 `(plugin_id, capability_id)` 作为唯一键。

新增 `plugin_runtime/resolver.py`：

- 根据 `required_capabilities` 建图；
- 检测能力缺失、版本不兼容和循环依赖；
- 计算 Factory、start 和反序 stop 顺序；
- 核心 Plugin 失败时 fail-fast；
- 可选 Plugin 失败时递归禁用依赖方；
- Tool 重名时按核心/可选规则禁用或失败。

定向测试：有向无环图、循环、能力缺失、级联禁用、版本约束与 Tool 冲突。

## 阶段三：Runtime Host 与原子装配

新增 `plugin_runtime/host.py`：

- Runtime 状态 `CREATED` 到 `STOPPED`；
- 读取 Manifest、检查 Python 依赖、导入 Factory；
- 只把已声明 Capability 注入 Factory；
- 校验 Factory 返回内容与 Manifest 一致；
- 在临时构建结果完整后一次性注册 Plugin、Capability、Tool 和状态提供者；
- 根据 Event 声明生成现有 PluginManager 来源订阅；
- 为每个 Plugin 绑定受控 Publisher，拒绝未声明 Event；
- 生成冻结的 Runtime 运行摘要和启动诊断；
- 启动失败时按已完成阶段回滚。

Host 不解释 Event 内容，也不向 Factory 暴露全局 Registry。

定向测试：Factory 多报/少报、Plugin ID 不一致、无效 Tool、未声明 Event 发布、自动订阅、
原子失败与启动回滚。

## 阶段四：Tool 执行身份与 Run 快照

扩展以下接口，扁平透传：

- `BaseTool.invoke/ainvoke`；
- `BaseToolExecutor.execute/aexecute/iter_completed/aiter_completed/execute_many/aexecute_many`；
- `ToolExecutor`；
- `ObservableToolExecutor`；
- ReActAgent 的同步、异步、同步流式和异步流式入口。

参数为：

- `task_id`；
- `run_id`；
- `step`；
- `task_messages`。

每个 Agent Run 在开始时解析一次 Tool 定义和执行对象，形成稳定快照。同一 Tool Batch 使用
相同的当前 Task 消息深拷贝，不包含尚未产生的 ToolResult。基础 Tool 接受并忽略不需要的参数。

定向测试：四种 Agent 入口参数一致、并行批次快照一致、Session History 不进入 task_messages、
Observable 包装器透明转发、直接调用允许身份为空。

## 阶段五：Plugin 收束和状态快照

扩展 `BasePlugin` 与 `PluginManager`：

- 新增默认空实现 `quiesce()`；
- 按依赖顺序 start、反序 stop；
- `quiesce` 后拒绝新的应用 Task，但仍允许收束 Event；
- `drain` 将 Plugin 已接受工作收敛到稳定状态；
- 一个 Plugin 清理失败不阻断其他 Plugin 清理；
- 汇总所有快照和清理错误。

PersistencePlugin 提供统一状态存储能力：

- Workspace 和 Session 状态分开存放；
- 保存 Plugin ID、Plugin 版本、Manifest Hash 和状态版本；
- Host 调用 State Provider，PersistencePlugin 负责 JSON 原子落盘；
- 恢复失败按核心/可选规则处理；
- 不恢复 Agent Run、Step、ToolCall 或 asyncio 对象。

定向测试：保存与恢复、版本不兼容、可选 Plugin 恢复失败、反序停止、清理错误聚合。

## 阶段六：内置 Plugin Manifest 与 Factory

为当前运行组件增加 Manifest 和 Factory：

- persistence；
- builtin-tools；
- agent；
- user-input；
- skill；
- blackboard；
- output-bridge。

内部组件继续留在所属 Plugin：

- SkillPlugin 内部持有 Embedding、Scanner、Ranker、Repository 和维护 Agent；
- AgentPlugin 内部持有 AgentFactory、TaskChannelRegistry 和 ActiveAgentRun；
- Tool 不注册为子 Plugin。

AgentFactory 的默认构造也由所属 Plugin Factory 完成：主 AgentFactory 由 AgentPlugin Factory
创建并接入 Host 的 ToolRegistry，维护 AgentFactory 由 SkillPlugin Factory 创建。
AgentRuntimeService 不创建或持有二者。

将 `AgentRuntimeService` 改为：

- 创建 Runtime Host；
- 传入 Workspace、Session、配置和核心 Plugin ID；
- 从 Host 获取 UserInput、Agent 控制与 OutputBridge 能力；
- 保留现有应用层方法，并把生命周期委托给 Host；
- 删除具体 Plugin 的手工构造和 `subscribe` 连线。

定向测试：内置 Manifest 全部可解析、拓扑与当前行为一致、RuntimeService API 兼容、
Skill 自动检索与维护保持现状、TUI 事件序列不变。

## 阶段七：退出主链路

实现统一退出：

```text
停止接受新 Task
→ Plugin quiesce
→ 通过 AgentPlugin 现有入口取消活动 Task
→ 等待唯一 Agent 终态和 Event 路由
→ Plugin drain
→ 导出 Workspace / Session 状态
→ PersistencePlugin 落盘
→ Plugin 反序 stop
→ EventBus stop
→ STOPPED
```

超时只作为清理兜底，不增加 Agent 业务终态。Plugin 后台任务由 Plugin 自己标记为稳定完成、
失败或 `interrupted`。

定向测试：运行中 stop、准备阶段 stop、后台任务收束、快照错误和重复 stop。

## 实施验证顺序

每阶段执行对应最小目录：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugin_runtime -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/tools \
  apps/agent/test/agent_orchestration/capability -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/application/test_agent_runtime_service.py -q
```

完成迁移后执行：

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src apps/agent/test apps/tui/src apps/tui/test
git diff --check
```

## 完成标准

- `AgentRuntimeService` 不再硬编码具体 Plugin 构造和 Event 订阅；
- 内置与显式目录 Plugin 使用同一 Manifest 解析和校验流程；
- 核心 Plugin 失败阻止 READY，可选 Plugin 失败被完整隔离；
- Capability、Tool 和 Event 拓扑可从冻结 Runtime 摘要中检查；
- Plugin Tool 可以获得完整、显式、不可变的当前 Task 调用信息；
- Plugin 可以通过 Manifest 声明和现有 Event 完成“陷入内核”；
- Runtime 退出复用现有 Agent Task 取消，并完成状态快照与反序清理；
- 新 Runtime 可以恢复 Workspace 和 Session 持久状态，但不恢复运行栈；
- 当前 Agent 与 TUI 行为保持兼容；
- 定向、全量、编译和 diff 检查全部通过。

## 实施结果

- 已实现 Manifest 模型、内置/显式目录发现、Python 依赖检查和静态依赖解析；
- 已实现 `PluginRegistration`、Capability 注入、Plugin Tool 注册和 Event 自动订阅；
- 已将 Host 拆分为生命周期协调器、`PluginGraphBuilder` 和 `PluginStateCoordinator`，避免把
  图构建、状态持久化和运行状态机混在一个模块；
- 已实现 Runtime Tool Registry 冻结及每次 Agent 调用的 ToolExecutor 快照；
- 已实现 `task_id`、`run_id`、`step`、`task_messages` 在四种 ReAct 入口中的扁平透传；
- 已实现 Plugin `quiesce`、退出时活动 Task 取消、状态快照/恢复和反序清理；
- 已将主 AgentFactory 和维护 AgentFactory 的构造、持有与关闭完整下沉至各自 Plugin；
- 已迁移 7 个内置 Plugin Manifest/Factory，AgentRuntimeService 不再手工注册或订阅；
- 已验证外部显式目录 Plugin Factory 加载和可选 Plugin 隔离；
- 已构建 wheel，并确认 7 个内置 Manifest 均进入安装产物；
- Agent 全量测试 368 项通过；
- TUI 全量测试 88 项通过，包含 8 个视觉快照；
- `compileall` 与 `git diff --check` 通过。
