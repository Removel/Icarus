# Agent Orchestration Foundation Development Plan｜Agent 编排基础能力开发计划

## 目标

基于 `agent-orchestration-foundation-design.md`，完成 Agent 编排层第一阶段基础能力：

- 模型角色收敛为 `thinking` 和 `perception`；
- AgentFactory；
- 无状态 ReActAgent；
- `invoke` 和 `ainvoke`；
- 工具注册、检查、执行及首批本地工具；
- Hook 注册、分发、运行上下文及基础观测包装器；
- 同步与异步主链路测试。

第二阶段在以上能力全部完成并验证后，再实现 `stream` 和 `astream`。

## 当前状态

- 第一阶段：已完成；
- 全量测试：`42 passed`；
- 第二阶段：未开始。

## 实施原则

- 复用模型接入层已有的 `Message`、`ImagePart`、`ToolDefinition`、`ToolCall`、`FinishReason`、`Usage` 和 `LLMResponse`；
- 公开函数使用简单、直观、扁平的参数；
- Agent、LLM、ToolRegistry、ToolExecutor 和 Hook 基础设施长期复用；
- Prompt、History、图片、工具范围和 ReAct 中间状态只属于单次调用；
- 基础 Hook 通过包装器自动触发，不在 ReAct、模型厂商实现和具体工具中散落 Hook 代码；
- 不在本阶段实现黑板、EventBus、Agent 业务角色、双线编排、循环检测和资源预算；
- 每完成一层先运行对应单元测试，再进入依赖它的下一层。

## 实施顺序

```text
模型角色收敛
  → 工具体系
  → Hook 基础设施
  → 观测包装器
  → ReActAgent
  → AgentFactory
  → 内置工具与应用组装
  → 集成验证
  → stream / astream
```

## 第一阶段

### 任务一：收敛模型角色

**改动文件**

- `apps/agent/src/model_config/config_model.py`
- `apps/agent/settings.json`
- `apps/agent/test/model_config/test_config_loader.py`
- `apps/agent/test/model_provider/test_llm_factory.py`

**开发内容**

- 从 `ModelSettings` 和 `LLMRole` 删除 `execution`；
- 保留 `thinking` 和 `perception`；
- 更新默认设置和测试数据；
- 保持 `LLMFactory.create_llm()` 的现有参数覆盖规则不变；
- 确认模型配置热加载行为不受影响。

**验证**

- 配置可以正确加载两个模型角色；
- `LLMFactory` 可以分别创建 `thinking` 和 `perception` LLM；
- 现有模型接入层测试全部通过。

### 任务二：建立工具统一类型和执行抽象

**新增文件**

- `apps/agent/src/agent_orchestration/tools/types.py`
- `apps/agent/src/agent_orchestration/tools/base_tool.py`
- `apps/agent/src/agent_orchestration/tools/tool_checker.py`
- `apps/agent/src/agent_orchestration/tools/tool_registry.py`
- `apps/agent/src/agent_orchestration/tools/tool_executor.py`
- `apps/agent/src/agent_orchestration/tools/__init__.py`

**新增测试**

- `apps/agent/test/agent_orchestration/tools/test_tools.py`

**开发内容**

- 定义所有工具成功或失败时统一使用的 `ToolExecutionResult`；
- 定义 `BaseTool`，并直接复用模型接入层的 `ToolDefinition`；
- 实现 ToolChecker，对工具类型、定义、名称和统一执行接口进行形式检查；
- 实现 ToolRegistry，负责注册、按名称查找和输出 ToolDefinition；
- 注册检查失败时记录日志并跳过，不阻塞当前进程；
- 名称重复或定义不合法的工具不进入注册中心；
- 实现 ToolExecutor，将正常返回、主动失败、异常和工具不存在统一转换为 `ToolExecutionResult`；
- 同步与异步执行保持相同结果语义；
- 提供同轮多 ToolCall 的批量执行能力，异步路径并发执行并保持原始 ToolCall 顺序返回结果。

**验证**

- 合规工具能够注册并查询；
- 不合规工具只记录日志且不可查询；
- `ToolRegistry.definitions()` 直接返回已有 `ToolDefinition`；
- 所有执行路径都返回 `ToolExecutionResult`；
- 多 ToolCall 可以并发执行，返回结果顺序稳定；
- 一个工具失败不影响同轮其他工具完成。

### 任务三：实现首批本地工具

**新增文件**

- `apps/agent/src/agent_orchestration/tools/builtin/read_tool.py`
- `apps/agent/src/agent_orchestration/tools/builtin/write_tool.py`
- `apps/agent/src/agent_orchestration/tools/builtin/insert_tool.py`
- `apps/agent/src/agent_orchestration/tools/builtin/bash_tool.py`
- `apps/agent/src/agent_orchestration/tools/builtin/__init__.py`

**新增测试**

- `apps/agent/test/agent_orchestration/tools/builtin/test_builtin_tools.py`

**开发内容**

- 实现 `read`、`write`、`insert` 和 `bash`；
- 每个工具提供简单明确的 ToolDefinition；
- 所有输入和输出遵守统一工具接口；
- 文件与命令错误返回统一失败结果；
- 提供一个默认注册函数，在应用组装时将首批工具注册到 ToolRegistry；
- 本阶段不实现资源锁、冲突分析和工具调用安全策略框架。

**验证**

- 文件工具覆盖正常执行和常见失败路径；
- Bash 工具返回退出状态、标准输出和标准错误；
- 首批工具可以通过默认注册函数进入 ToolRegistry；
- 任一工具注册失败不影响其他工具可用。

### 任务四：建立 Hook 核心框架

**新增文件**

- `apps/agent/src/agent_orchestration/hooks/hook_event.py`
- `apps/agent/src/agent_orchestration/hooks/base_hook.py`
- `apps/agent/src/agent_orchestration/hooks/hook_context.py`
- `apps/agent/src/agent_orchestration/hooks/hook_registry.py`
- `apps/agent/src/agent_orchestration/hooks/hook_dispatcher.py`
- `apps/agent/src/agent_orchestration/hooks/__init__.py`

**新增测试**

- `apps/agent/test/agent_orchestration/hooks/test_hooks.py`

**开发内容**

- 定义统一 HookEvent；
- 实现 BaseHook 的同步与异步处理入口；
- 实现 HookRegistry，支持同一个事件名称注册多个 Handler；
- 实现 HookDispatcher 的 `trigger` 和 `atrigger`；
- HookDispatcher 负责查询、构造事件、执行 Handler 和隔离异常；
- Handler 返回值全部忽略；
- 单个 Handler 失败只记录日志，不影响其他 Handler 和主流程；
- 使用 HookContext 和 `ContextVar` 传播当前 `run_id` 等关联信息；
- Hook 名称保持可扩展，不定义封闭枚举；
- 未注册 Hook 的触发为空操作。

**验证**

- 同一事件的多个 Handler 都能执行；
- 同步和异步 Handler 都能通过对应入口触发；
- Handler 异常不会向主流程传播；
- 并发 Agent Run 的 HookContext 互不污染；
- 自定义事件名称无需修改 Hook 框架。

### 任务五：实现基础观测包装器

**新增文件**

- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_llm.py`
- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_tool_executor.py`
- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_agent.py`
- `apps/agent/src/agent_orchestration/hooks/wrappers/__init__.py`

**新增测试**

- `apps/agent/test/agent_orchestration/hooks/wrappers/test_observable_wrappers.py`

**开发内容**

- ObservableAgent 观测一次 Agent 调用的开始、完成和异常；
- ObservableLLM 观测 ReAct 内部每一轮 LLM 调用；
- ObservableToolExecutor 观测每一个 ToolCall 的执行；
- 三类包装器统一通过 HookDispatcher 分发事件；
- ObservableAgent 在调用入口建立 HookContext，并在结束时恢复上下文；
- LLM 和 Tool 事件自动继承当前 Agent Run 的 `run_id`；
- 包装器保持原对象的同步和异步调用语义；
- 第一阶段只要求完整支持非流式调用；已有 LLM 流式接口先保持透明代理，流式观测在第二阶段完善；
- 包装器不得修改输入、返回值和原始异常。

**验证**

- 不修改 ReAct、BaseLLM 厂商实现和具体工具即可产生基础事件；
- 一次 Agent Run 内的 Agent、LLM 和 Tool 事件具有相同 `run_id`；
- `before / after / error` 顺序稳定；
- Hook 失败不改变被包装组件的结果和异常；
- 未注册 Hook 时包装器行为与原组件一致。

### 任务六：实现无状态 ReActAgent

**新增文件**

- `apps/agent/src/agent_orchestration/capability/base_agent.py`
- `apps/agent/src/agent_orchestration/capability/react_agent.py`
- `apps/agent/src/agent_orchestration/capability/types.py`
- `apps/agent/src/agent_orchestration/capability/__init__.py`

**新增测试**

- `apps/agent/test/agent_orchestration/capability/test_react_agent.py`

**开发内容**

- 定义 Agent 的统一同步和异步调用抽象；
- 实现扁平调用参数：system prompt、history messages、input prompt、input images 和 tools；
- Agent 内部将 Prompt 和图片组装为模型接入层已有的 Message；
- `tools=None` 使用全部已注册工具；
- `tools=[]` 禁止本次工具调用；
- 指定名称列表时只向 LLM 提供成功解析的工具定义；
- 执行完整 ReAct 循环：
  - 调用 LLM；
  - 保存 Assistant ToolCall 消息；
  - 执行本轮全部 ToolCall；
  - 将全部 ToolExecutionResult 转成 tool Message；
  - 再次调用 LLM；
  - 直到模型不再返回 ToolCall；
- 同轮 ToolCall 并发执行，全部完成后再进入下一轮；
- 工具结果按原始 ToolCall 顺序写回上下文；
- 工具不存在或执行失败时，将失败结果写回 LLM，由模型决定后续行为；
- 复用模型接入层的 FinishReason，不定义重复结束原因；
- 不设置 `max_steps`，不实现启发式死循环检测；
- 调用结束后不在 Agent 实例中保存任何消息或步骤状态。

**验证**

- 纯文本和图文输入都能正确组装；
- 无工具对话可以直接结束；
- 一轮和多轮 ToolCall 都可以完成；
- 同一 Agent 实例连续调用时不存在消息泄漏；
- 工具失败后 LLM 仍能继续下一轮；
- `invoke` 和 `ainvoke` 的 ReAct 语义一致；
- 最终返回保留模型接入层的结束原因和用量信息。

### 任务七：实现 AgentFactory 和应用组装

**新增文件**

- `apps/agent/src/agent_orchestration/agent_factory.py`
- `apps/agent/src/agent_orchestration/__init__.py`

**新增测试**

- `apps/agent/test/agent_orchestration/test_agent_factory.py`

**开发内容**

- AgentFactory 成为上层获取 Agent 的唯一入口；
- 根据 `model_role` 获取 `thinking` 或 `perception` LLM；
- 按模型角色缓存并复用无状态 Agent；
- 共享 ToolRegistry、ToolExecutor、HookRegistry 和 HookDispatcher；
- 按顺序组装 ObservableLLM、ObservableToolExecutor、ReActAgent 和 ObservableAgent；
- 上层只依赖统一 Agent 抽象，不感知包装器；
- AgentFactory 不定义 Agent 业务角色；
- 提供清理入口，统一释放长期 BaseLLM 客户端资源；
- 支持调用方注入自定义 ToolRegistry 和 HookRegistry；
- 未注入时使用应用默认工具注册结果和空 HookRegistry。

**验证**

- `thinking` 和 `perception` 可以获取对应 Agent；
- 相同模型角色重复获取时复用同一无状态实例；
- 两个模型角色的配置和实例互不混淆；
- 通过 AgentFactory 获取的 Agent 自动产生 Agent、LLM 和 Tool Hook；
- 自定义工具和 Hook 可以通过注册中心接入；
- 关闭 Factory 时所有长期 LLM 资源被正确释放。

### 任务八：第一阶段集成验证与文档同步

**更新文件**

- `apps/agent/docs/arch/agent-orchestration-foundation-design.md`
- `apps/agent/docs/arch/model-provider-layer-design.md`

**开发内容**

- 根据实际实现修正文档中的类名和依赖方向；
- 更新模型接入层文档中已经过时的类名或角色描述；
- 增加一条完整的同步 ReAct 集成用例；
- 增加一条完整的异步多 ToolCall 集成用例；
- 增加 Hook 轨迹关联集成用例；
- 检查公开接口是否保持扁平，避免新增不必要的 Request 包装层；
- 检查 ReAct 和具体工具实现中是否混入基础 Hook 触发代码。

**第一阶段测试命令**

```bash
apps/agent/.venv/bin/pytest apps/agent/test/model_config -q
apps/agent/.venv/bin/pytest apps/agent/test/model_provider -q
apps/agent/.venv/bin/pytest apps/agent/test/agent_orchestration/tools -q
apps/agent/.venv/bin/pytest apps/agent/test/agent_orchestration/hooks -q
apps/agent/.venv/bin/pytest apps/agent/test/agent_orchestration/capability -q
apps/agent/.venv/bin/pytest apps/agent/test/agent_orchestration -q
apps/agent/.venv/bin/pytest apps/agent/test -q
```

**第一阶段完成标准**

- 架构文档中的第一阶段验收项全部通过；
- 全量测试通过；
- Agent 核心路径不依赖未来编排组件；
- Agent、LLM 和 Tool 的基础观测无需修改核心实现；
- 未引入黑板、EventBus、业务角色或循环控制等未确定设计。

## 第二阶段：Agent 流式能力

第二阶段必须在第一阶段完成后开始。

### 任务九：定义 Agent 流式事件

**计划新增或更新**

- `apps/agent/src/agent_orchestration/capability/types.py`
- `apps/agent/test/agent_orchestration/capability/test_agent_stream_types.py`

**开发内容**

- 定义 Agent 级流式事件；
- 区分文本增量、推理增量、工具开始、工具完成和最终完成；
- 保持事件与 Hook 的职责分离：
  - Stream 面向当前调用方；
  - Hook 面向旁路持久化和观测。

### 任务十：实现 `stream` 和 `astream`

**计划更新**

- `apps/agent/src/agent_orchestration/capability/base_agent.py`
- `apps/agent/src/agent_orchestration/capability/react_agent.py`
- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_agent.py`
- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_llm.py`

**计划新增测试**

- `apps/agent/test/agent_orchestration/capability/test_react_agent_stream.py`
- `apps/agent/test/agent_orchestration/capability/test_async_react_agent_stream.py`

**开发内容**

- 一个 Agent Stream 内串联多轮 LLM Stream；
- LLM 返回完整 ToolCall 后结束当前模型流；
- 执行工具并继续下一轮 LLM Stream；
- 同步与异步流保持相同事件语义；
- 正确处理消费者取消、工具异常和模型异常；
- 完善流式路径的基础 Hook 观测。

**第二阶段完成标准**

- `stream` 和 `astream` 可以完成多轮 ReAct；
- 工具执行期间不会丢失上下文；
- Stream 事件顺序明确且可测试；
- Hook 和 Stream 互不替代、互不修改；
- 非流式接口行为不发生回归。

## 主要风险与控制

### 同步和异步行为分叉

控制方式：同步与异步路径复用消息组装、工具选择、结果序列化和终止判断等纯逻辑，并使用同一组行为测试。

### Hook 包装器造成接口语义变化

控制方式：所有包装器都进行透明代理测试，确保输入、返回值和原始异常不被修改。

### Agent 状态泄漏

控制方式：所有 ReAct 消息和步骤状态只使用调用内局部变量，并增加同一实例连续调用测试。

### 工具失败影响进程稳定性

控制方式：注册失败只记录日志并跳过；执行失败统一转换为 ToolExecutionResult；一个工具失败不取消同轮其他工具。

### 过早侵入核心编排设计

控制方式：本计划只实现稳定基础边界。Agent 业务角色、黑板、EventBus、双线调度、预算和循环控制全部留在未来核心编排层。

## 推荐提交拆分

实现时建议按以下顺序形成独立提交，便于审查和回滚：

1. 收敛模型角色；
2. 工具框架和内置工具；
3. Hook 核心框架；
4. 基础观测包装器；
5. ReActAgent；
6. AgentFactory 和集成测试；
7. 文档同步；
8. 第二阶段流式能力。
