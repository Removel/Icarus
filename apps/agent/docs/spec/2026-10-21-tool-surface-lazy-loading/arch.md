# Tool Surface Lazy Loading Architecture｜工具面延迟加载架构

> 状态：Phase 0–4 的实现与文档已完成；验证结果见 [plan.md](plan.md)。可选真实模型 smoke 未执行。
> 第 2 节记录实施前基线；第 3 节起描述当前源码与测试所确认的实现。
> 设计来源为 [eager-scribbling-pony.md](docs/plan/eager-scribbling-pony.md)。

## 1. 文档定位与问题

Icarus 默认会话当前向模型平铺暴露 27 个 Agent 可见 Tool，不含 MCP 动态工具；包含默认不加载的
relationship 工具时为 32 个。每次模型调用都携带全量定义，工具数量继续增长会增加上下文成本并降低
调用准确率。

本设计将默认 Session Agent 的模型面固定为 9 个常驻 Tool，通过 `tool_search` 发现延迟工具、通过
`tool_execute` 调用已加载工具。加载过程只改变 Run 内的执行门控名单，不改变送给模型的
`tool_definitions` 数组。

缓存安全是结构性约束：请求前缀按 `tools → system → messages` 匹配，Run 中变更 `tools` 数组会使
其后的前缀缓存失效。本设计因此不采用 Run 中重建定义数组的方案，也不依赖特定厂商的动态工具声明通道；
它只消除工具面对缓存的破坏，不等同于本期已经启用 prompt caching。

## 2. 实施前架构（Phase 0 基线）

### 2.1 工具注册与归属

- [tool_registry.py](apps/agent/src/icarus/core/tools/tool_registry.py) 将所有插件工具保存在同一个扁平
  `ToolRegistry` 中；`select(None)` 返回全部工具，传入名单时按名单选取并跳过未注册名称。
- [assembly.py](apps/agent/src/icarus/runtime/assembly.py) 的 `PluginAssembly.build_snapshot()` 已产出
  `(tool_name, plugin_id)` 对。工具面归属可以从插件身份派生，不需要扩展 manifest。
- [session_runtime.py](apps/agent/src/agent_app/application/session_runtime.py) 创建并持有全量
  `ToolRegistry`，在 Runtime READY 前完成插件装配与冻结。

### 2.2 Run 内工具快照

- [react_agent.py](apps/agent/src/icarus/core/agents/react_agent.py) 的 `_create_run_state()` 调用
  `ToolExecutor.snapshot(tools)` 一次，并将 `definitions()` 保存到 `_RunState.tool_definitions`。
- `invoke`、`ainvoke`、`stream`、`astream` 的后续模型 Step 都复用这份定义；当前代码没有 Run 中重建
  定义数组的路径。
- [tool_executor.py](apps/agent/src/icarus/core/tools/tool_executor.py) 的 `definitions()` 会注入
  `_execution` 控制参数；Registry 中保存的原始 `ToolDefinition` 不含这层包装。
- [test_react_agent.py](apps/agent/test/icarus/core/agents/test_react_agent.py) 与
  [test_react_agent_stream.py](apps/agent/test/icarus/core/agents/test_react_agent_stream.py) 已覆盖
  `tools` 白名单、空名单和多轮模型调用；[test_tools.py](apps/agent/test/icarus/core/tools/test_tools.py)
  已覆盖 `select` 与原始定义/执行定义的区别。

### 2.3 MCP 先例与结果通道

- [mcp/tools.py](apps/agent/src/agent_app/plugins/mcp/tools.py) 已向模型提供三个固定入口：
  `mcp_tool_list`、`mcp_tool_search`、`mcp_tool_execute`。动态 MCP Tool 由 MCPPlugin 自有 Catalog
  持有，不进入静态 `ToolRegistry`。
- [client_manager.py](apps/agent/src/agent_app/plugins/mcp/client_manager.py) 已在执行前使用
  jsonschema 校验目标工具参数，并将校验错误回灌为可读失败。
- [types.py](apps/agent/src/icarus/core/tools/types.py) 的 `ToolExecutionResult.as_dict()` 不输出
  `metadata`；[tool_executor.py](apps/agent/src/icarus/core/tools/tool_executor.py) 与
  [result_budget.py](apps/agent/src/icarus/core/tools/result_budget.py) 在预算和 finalize 过程中保留
  自定义 metadata key。因此 metadata 可承载 Run 内控制信息而不进入模型消息日志。

### 2.4 当前默认 Session 暴露链路

```text
SessionRuntime(tools)
  → BlackboardContextReadyEvent.tools
  → AgentPlugin._run_agent
  → ReActAgent.astream(tools=...)
  → ToolExecutor.snapshot(tools)
  → _RunState.tool_definitions
  → 每个模型 Step
```

AgentPlugin 当前透传事件中的 `tools`，尚未施加默认 9 元组白名单；底层 `tools=None` 仍表示全部可见。

## 3. 实现目标与不变量

### 3.1 默认 Session 的常驻工具面

```text
read
write
insert
bash
tool_search
tool_execute
mcp_tool_list
mcp_tool_search
mcp_tool_execute
```

- Agent 插件装配处硬编码该 9 元组并传给既有 `tools` 参数，不新增 Agent 入口参数或 config 字段。
- 白名单与可执行全集求交；未注册名称沿用 `ToolRegistry.select` 的 skip-missing 语义，不阻断 Run。
- 默认 Session Agent 的模型面在单个 Run 内保持字节冻结。底层其他调用方不传 `tools` 时仍保持
  全部可见的既有行为，包括 Skill Evolver。
- Catalog 排除上述常驻集合，保证“`tool_search` 搜出来的工具 = 常驻面之外的工具”。

### 3.2 可见性与可执行性分离

- 模型可见性由单次冻结的 `tool_definitions` 决定，Run 中不得重建、增删或重排该数组。
- 延迟工具是否允许经 `tool_execute` 调用，由当前 Run 的 `loaded_tools` 硬门控决定。
- 全量 `ToolRegistry` 继续保存所有可执行工具；旧会话消息和历史 ToolCall 不依赖模型可见数组进行
  Registry 查找。
- `loaded_tools` 只增不减、保序去重，每个 Run 重新初始化为空；跨 Run 不持久化。
- ReActAgent 保持无状态：运行态只存在 `_RunState`，工具实例不保存某个 Run 的加载名单。

### 3.3 依赖方向

- 通用 Catalog、搜索、分发和 Run 状态位于 `icarus.core`；Session 与 Agent 插件装配位于
  `agent_app`。
- core 不依赖 Plugin Runtime、Blackboard、具体业务插件或模型厂商协议。
- 模型厂商差异继续留在 `model_provider`；本设计不在上层增加 OpenAI、Anthropic、Kimi 或其他
  Provider 分支。
- MCP 动态工具继续走三个固定 MCP Tool 的自有通道，与统一工具面 Catalog 分离。

## 4. 组件与所有权

| 组件 | 位置与所有者 | 当前职责 |
|---|---|---|
| `ToolSurfaceCatalog` | 新增于 `icarus.core.tools` | 从 `(tool_name, plugin_id)` 与 Registry 原始定义构建不可变快照；排除常驻 9 元组；按 `plugin_id` 精确返回整组定义 |
| `ToolSearchTool` | 新增于 `icarus.core.tools`，由 SessionRuntime 直接注册 | 在定义描述中携带功能面目录；返回命中工具的完整说明与 Schema；通过 `metadata["tools_loaded"]` 报告本次加载名 |
| `ToolExecuteTool` | 新增于 `icarus.core.tools`，由 SessionRuntime 直接注册 | 校验 `name` / `arguments`、执行硬门控、校验目标 Schema，并以目标工具真实身份走既有 ToolExecutor 链路 |
| `_RunState` | `icarus.core.agents.react_agent` | 持有冻结的 `tool_definitions` 和本 Run 有序、去重的 `loaded_tools` |
| `SessionRuntime` | `agent_app.application` | 在 freeze 前注册双桥；READY 前构建 Catalog、写入目录并 late-bind 全量 Registry |
| `AgentPlugin` | `agent_app.plugins.agent` | 为默认 Session Run 传入常驻 9 元组白名单，发布原始执行流 |
| Base Prompt | `agent_app.plugins.prompt` | 说明先搜索、后分发调用，以及新 Run 必须重新搜索的门控规则 |

双桥均不是独立 Plugin，不需要 manifest；`ToolSearchTool` 与 `ToolExecuteTool` 由 SessionRuntime
单一持有。MCPPlugin 不拥有统一 Catalog，也不承担延迟工具的门控。

## 5. 功能面与发现契约

### 5.1 Catalog 构建

`ToolSurfaceCatalog.build(pairs, registry)` 消费 `build_snapshot().tools` 和 Registry 原始定义：

1. 按 `(tool_name, plugin_id)` 派生功能面，不修改 manifest，不增加 usage/examples 字段。
2. 排除常驻 9 元组；MCP 动态工具本来就不在该快照中。
3. 保存不可变的 `(name, plugin_id, definition)` 快照；定义不含 `_execution` 包装。
4. `search(scope)` 只按 `plugin_id` 精确查组，命中后返回组内全部定义，不做关键词评分或组内过滤。

每个功能面当前约 2–6 个工具，整组召回是本期固定粒度，不引入 Hermes 式词干匹配或评分层。

### 5.2 功能面目录

SessionRuntime 在 READY 前把目录写入 `tool_search` 的定义描述。Runtime Host 先生成
`graph_snapshot`，再调用 `before_ready`；SessionRuntime 在该钩子中消费快照的 `(tool_name, plugin_id)`，
构建并绑定 Catalog 与全量 Registry，随后才冻结 Registry。条目形态固定为：

```text
scope_id(N): tool1, tool2, ...
```

其中 `scope_id` 是 `plugin_id`，`N` 是该面延迟工具数量。工具名就是目录描述，core 不维护额外的
功能面说明。

### 5.3 搜索结果

- 命中时，以内联文本直通工具名称、description 和完整 input schema；渲染层不改写 description。
- 插件作者的 description 必须说明何时使用、注意事项和示例调用，这是模型正确走双桥流程的主要引导。
- 命中结果的 `ToolExecutionResult.metadata["tools_loaded"]` 携带本次加载名，由 Agent 循环并入
  `loaded_tools` 并去重。
- 未命中或空 query 时返回全部功能面目录，不写入新增加载名；模型需使用正确功能面名称重试。
- 不做模糊匹配、拼写纠正、关键词/token 评分或模型辅助选面。
- `tool_search.can_run_parallel=True`，sync/async 行为一致。

`tools_loaded` 只供 Agent 循环消费。它随既有 finalize/metadata 链路保留，但不通过 `as_dict()` 进入
模型消息，不产生额外消息 token；结果 Hook 的 `_preserve_result` 也不得改写该控制信息。

## 6. Run 数据流与执行门控

### 6.1 单次 Run

```text
创建 _RunState
  ├─ ToolExecutor.snapshot(tools=常驻9元组)
  ├─ tool_definitions 单次生成并冻结
  └─ loaded_tools = []

模型调用 tool_search(scope)
  → ToolSurfaceCatalog 精确查组
  → 完整工具说明写入 Tool Result
  → finalize 后扫描 metadata["tools_loaded"]
  → 与可执行全集求交，保序去重并入 loaded_tools
  → 下一模型 Step 复用原 tool_definitions

模型调用 tool_execute(name, arguments)
  → 检查 name 是否在当前 Run 的 loaded_tools 中
  → 从全量 Registry 获取目标原始定义
  → jsonschema 校验 arguments
  → 以目标工具身份执行既有 ToolExecutor 链路
```

四条 Agent 路径都在每组工具 finalize 后扫描加载 metadata：`invoke` / `ainvoke` 使用工具组返回值，
`stream` / `astream` 使用各自 finalize 结果。任何路径都不得因此重建 `tool_definitions`。

### 6.2 `tool_execute` 公共契约

```text
name: str
arguments: object
```

- `arguments` 直接接收 JSON object，不要求模型生成并转义一整段 JSON 字符串。
- 未加载工具拒绝执行，错误文本直接提示下一步先调用 `tool_search('<scope>')`，让每次流程错误成为
  可自愈的引导。
- 参数校验复用 MCP 已验证的 jsonschema 校验行为，同时保持 core 不反向依赖 MCPPlugin。
- Schema 校验失败返回模型可读的结构化字段级错误，包含出错字段、期望约束和实际值/类型。
- 执行采用 unwrap 语义：ReActAgent 只向 `tool_execute` 传入当前 Run 的 `loaded_tools`、原始
  `ToolCall.id` 和全量 `BaseToolExecutor`。分发工具完成门控与 Schema 校验后，以目标名称和原调用 ID
  进入全量执行器；目标工具因此沿用既有执行、finalize、metadata、工具组预算和 Hook 链路。
- 外层快照执行器对 `tool_execute` 保持观测透明，不把分发入口再次记录为业务 Tool；Hook 只观察 unwrap
  后的目标调用。工具实例不保存某个 Run 的名单或模型角色对应的执行器。
- `tool_execute.can_run_parallel=True`，sync/async 行为一致。

### 6.3 跨 Run 与提示词

消息历史仍由 Blackboard 跨 Run 保留，旧 `tool_search` 结果也仍在后续上下文中，但 `loaded_tools`
不继承。模型在新 Run 使用历史中出现过的延迟工具前，必须重新搜索该功能面。Base Prompt 只增加这条
流程所需的静态引导，不把动态 Plugin 上下文写入稳定 System Prompt。

## 7. 兼容性、验证与回滚边界

当前实现的 observable contracts：

1. 四条 ReAct 路径中，每个模型 Step 收到同一份冻结定义；搜索加载后仍不发生定义重建。
2. Catalog 查组、常驻排除和不可变性可由纯功能测试验证。
3. 未加载时硬门拒绝，加载后经 `tool_execute` 放行；非法 object 参数和 Schema 错误均可读回灌。
4. `loaded_tools` 保序去重，新增名显式与可执行全集求交；跨 Run 重置。
5. `tools=None`、空名单、显式白名单及 Skill Evolver 的既有行为不变。
6. manifest、持久化消息、MCP 三个固定 Tool 和 MCP 动态 Catalog 无需迁移。
7. Registry 双桥注册、目录注入与 late-bind 均在 READY 前完成，无运行期装配竞态。

具体测试文件、命令和真实模型 smoke 观察项见 [plan.md](plan.md)。回滚不得破坏既有会话数据、用户
配置或其他未相关改动。

## 8. 明确不做

- 不新建 `search_scoring.py`，不做关键词/token 评分、模糊匹配、拼写纠正或组内过滤。
- 不引入模型或独立判定服务选择功能面。
- 不改 manifest schema 或任何 `manifest.json`，不增加 usage/examples 字段。
- 不改 MCP 三个固定 Tool，不把 MCP 动态工具并入统一 Catalog。
- 不在 `config_model` 增加可见性字段，不给 Skill Evolver 的生成 Run 增加门控或专门 canary。
- 不增加 browse/list 第二工具，不按调用计数自动收缩加载面。
- 不增加 `calls` 数组批量分发；并行仍由循环层既有工具批处理负责。
- 本期不接入 Anthropic `tool_addition`、Kimi 注入式声明等 vendor 原生通道。
- 不缓存跨 Run 加载名单；先观察重复搜索频率，再决定是否存在独立需求。
- 本期不收 contributor guidance；其点名隐藏工具时的模型行为列为 smoke 观察项。

## 9. 参考资料

- [R1：Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md)：
  前缀匹配、`tools → system → messages` 顺序与缓存经济账。
- [R2：Anthropic Mid-conversation tool changes](https://platform.claude.com/docs/en/agents-and-tools/tool-use/mid-conversation-tool-changes.md)：
  `defer_loading` / `tool_addition` 原生扩展，本期不采用。
- [R3：Kimi 动态加载工具](https://platform.kimi.com/docs/guide/use-dynamic-tool-loading)：
  追加式声明与前缀缓存约束，本期不采用其厂商通道。
- [R4：Hermes Agent Tool Search / Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-search)：
  固定入口、unwrap、listing 与 progressive disclosure 先例；本设计按功能面直选压缩为双桥。
