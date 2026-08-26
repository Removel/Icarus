# Agent Stream Event Development Plan｜Agent 流式事件开发计划

> 历史计划：其中 `AgentErrorEvent` 已由 Harness 发布的统一 `TaskErrorEvent` 替代，当前契约见
> `apps/agent/docs/arch/agent-core-capability-completion-design.md`。

## 目标

基于 `agent-stream-event-design.md`，为当前无状态 ReActAgent 增加：

- 通用 Event 基类；
- Agent Stream Event 子类；
- `stream` 和 `astream`；
- 工具有序分批执行；
- 流式路径的聚合 Hook 观测；
- 同步、异步和非流式回归测试。

本计划不实现完整 Plugin、Plugin Registry、EventBus 和 Blackboard。

这些能力将在 Stream 完成后的下一阶段继续设计和实现，当前架构初稿见：

- `apps/agent/docs/arch/plugin-eventbus-blackboard-design.md`

## 当前状态

- Stream Event：已完成；
- `stream`：已完成真实模型验证；
- `astream`：已完成真实模型验证；
- 工具流：已完成真实 ToolCall 验证；
- Hook 聚合：已完成；
- 全量测试：`49 passed`；
- Plugin/EventBus/Blackboard：未开始。

## 实施原则

- Event 是未来插件系统的统一消息基础；
- Agent Stream 输出 Event 子类，不直接输出 LLMStreamChunk；
- ReActAgent 不依赖 EventBus；
- AgentPlugin 将来负责把 Stream Event 发布到 EventBus；
- Stream 是当前调用方的主输出通道；
- Hook 只负责旁路持久化、观测和监督；
- Hook 不逐个记录文字 Delta；
- 同步与异步接口保持相同事件语义；
- 非流式 `invoke/ainvoke` 不发生行为回归；
- 公开函数继续使用简单、扁平参数。

## 实施顺序

```text
Event 基类
→ Agent Stream Event
→ 工具并行能力声明
→ 有序分批执行
→ ReActAgent stream / astream
→ 流式 Hook 聚合
→ 分层测试
→ 真实模型冒烟验证
→ 文档同步
```

## 任务一：定义通用 Event 基类

**新增文件**

- `apps/agent/src/agent_orchestration/events/__init__.py`
- `apps/agent/src/agent_orchestration/events/base_event.py`

**新增测试**

- `apps/agent/test/agent_orchestration/events/test_base_event.py`

**开发内容**

- 定义不可变 Event 基类；
- 包含 `event_id`、`occurred_at` 和 `task_id`；
- 提供简单创建方式，自动生成 Event ID 和时间；
- Event 基类不包含 EventBus 路由逻辑；
- 未来来源插件身份由 EventBus 发布入口补充，不写入纯能力内核的 Event 基类；
- Event 不依赖 Plugin、Registry 或 Blackboard。

**验证**

- 每个 Event 自动获得唯一 ID；
- 时间字段正确；
- task_id 关联同一次用户 Task；
- Event 可以被 dataclass 序列化适配器转换为 JSON。

## 任务二：定义 Agent Stream Event

**更新文件**

- `apps/agent/src/agent_orchestration/capability/types.py`
- `apps/agent/src/agent_orchestration/capability/__init__.py`

**新增测试**

- `apps/agent/test/agent_orchestration/capability/test_agent_stream_types.py`

**开发内容**

- 新增 `AgentTextDeltaEvent`；
- 新增 `AgentToolStartedEvent`；
- 新增 `AgentToolCompletedEvent`；
- 新增 `AgentCompletedEvent`；
- 新增 `AgentErrorEvent`；
- 所有 Agent Stream Event 继承 Event；
- 所有事件包含 Step；
- 工具事件直接复用 ToolCall 和 ToolExecutionResult；
- Completed 直接复用 AgentResponse。

**验证**

- 每种事件字段满足设计文档；
- Event 子类无需额外嵌套 Request 或 Payload 对象；
- 工具参数和执行结果可以直接访问；
- Completed 可以获得完整 AgentResponse。

## 任务三：增加工具并行能力声明

**更新文件**

- `apps/agent/src/agent_orchestration/tools/base_tool.py`
- `apps/agent/src/agent_orchestration/tools/tool_registry.py`
- `apps/agent/src/agent_orchestration/tools/tool_executor.py`
- `apps/agent/src/agent_orchestration/tools/builtin/read_tool.py`
- `apps/agent/src/agent_orchestration/tools/builtin/write_tool.py`
- `apps/agent/src/agent_orchestration/tools/builtin/insert_tool.py`
- `apps/agent/src/agent_orchestration/tools/builtin/bash_tool.py`

**更新测试**

- `apps/agent/test/agent_orchestration/tools/test_tools.py`
- `apps/agent/test/agent_orchestration/tools/builtin/test_builtin_tools.py`

**开发内容**

- BaseTool 增加 `can_run_parallel(arguments)`，默认返回 false；
- ReadTool 返回 true；
- WriteTool 和 InsertTool 使用默认 false；
- Bash ToolDefinition 增加可选 `parallel` 参数；
- BashTool 只在 `parallel=true` 时返回可并行；
- ToolRegistry 或 ToolExecutor 能根据 ToolCall 查询本次调用是否可并行；
- 未注册工具按不可并行处理。

**验证**

- read 始终可并行；
- write 和 insert 不可并行；
- bash 默认不可并行；
- bash 显式 `parallel=true` 时可并行；
- 非布尔 parallel 参数返回参数错误或按既有工具校验规则处理。

## 任务四：实现有序工具分批

**更新文件**

- `apps/agent/src/agent_orchestration/tools/tool_executor.py`
- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_tool_executor.py`

**新增或更新测试**

- `apps/agent/test/agent_orchestration/tools/test_tools.py`
- `apps/agent/test/agent_orchestration/hooks/wrappers/test_observable_wrappers.py`

**开发内容**

- 按 ToolCall 原始顺序扫描；
- 连续可并行调用合并为并发批次；
- 不可并行调用单独形成顺序屏障；
- 批次之间严格串行；
- 批次内并发执行；
- 同步路径使用线程池；
- 异步路径使用 asyncio；
- 每批结果最终按原始 ToolCall 顺序返回；
- 保持同步线程池中的 ContextVar 传播；
- 为流式调用提供“真实完成顺序”的执行能力。

**验证**

- `0 0 1 1 0 1 0 1 1 1 0 1` 正确分批；
- write 后 bash 按原始顺序串行；
- 多 read 并发；
- 批次后续调用不会提前开始；
- 结果按原始顺序返回；
- Hook 仍继承同一 run_id。

## 任务五：扩展 BaseAgent 流式接口

**更新文件**

- `apps/agent/src/agent_orchestration/capability/base_agent.py`

**开发内容**

- 增加同步 `stream`；
- 增加异步 `astream`；
- 参数与 `invoke/ainvoke` 保持一致；
- 返回 `Iterator[Event]` 和 `AsyncIterator[Event]`；
- 不绑定 EventBus 或客户端协议。

**验证**

- 所有 BaseAgent 实现和测试 Stub 同步补齐接口；
- 现有 invoke/ainvoke 调用签名不变。

## 任务六：实现 ReActAgent.stream

**更新文件**

- `apps/agent/src/agent_orchestration/capability/react_agent.py`

**新增测试**

- `apps/agent/test/agent_orchestration/capability/test_react_agent_stream.py`

**开发内容**

- 使用 BaseLLM.stream；
- 实时将 text_delta 转为 AgentTextDeltaEvent；
- reasoning_delta 只在内部聚合，不流出；
- 聚合当前 LLM 轮完整文本；
- 聚合当前轮完整 reasoning、ToolCall、Usage 和 FinishReason；
- 将当前轮 Assistant Message 写入 Messages；
- 有 ToolCall 时按工具批次执行；
- 工具执行前流出 ToolStarted；
- 工具完成时流出 ToolCompleted；
- 结果按原始顺序写回 Messages；
- 无 ToolCall 时构造 AgentResponse 并流出 Completed；
- 异常时流出 Error 后原样抛出异常；
- 调用方停止迭代后不再进入后续步骤。

**验证**

- 纯文本流；
- 工具调用前文字正常流出；
- 一轮 ToolCall；
- 多轮 ToolCall；
- 多 ToolCall 分批；
- ToolStarted 字段完整；
- ToolCompleted 字段完整；
- Completed 的 AgentResponse 完整；
- reasoning 不进入文字事件；
- Error 后抛出原始异常。

## 任务七：实现 ReActAgent.astream

**更新文件**

- `apps/agent/src/agent_orchestration/capability/react_agent.py`

**新增测试**

- `apps/agent/test/agent_orchestration/capability/test_async_react_agent_stream.py`

**开发内容**

- 使用 BaseLLM.astream；
- 与同步流使用相同事件类型和 Step 语义；
- 工具批次异步执行；
- ToolCompleted 按真实完成顺序流出；
- ToolResult 按原始顺序回填；
- 支持异步任务取消；
- 取消后不进入后续 LLM 和工具批次；
- 原始 CancelledError 保持向上传播。

**验证**

- 同步和异步事件序列语义一致；
- 并发工具完成顺序可被实时观察；
- 调用方取消后停止后续流程；
- 非流式异步行为不回归。

## 任务八：实现流式 Hook 聚合

**更新文件**

- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_llm.py`
- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_agent.py`

**更新测试**

- `apps/agent/test/agent_orchestration/hooks/wrappers/test_observable_wrappers.py`

**开发内容**

- ObservableLLM.stream/astream 记录 `llm.stream / before`；
- 包装器透传每个 LLMStreamChunk；
- 包装器内部同时聚合文本、reasoning、ToolCall、Usage 和 FinishReason；
- 当前轮结束后只触发一次 `llm.stream / after`；
- 异常时触发一次 `llm.stream / error`；
- 不为每个 delta 触发 Hook；
- ObservableAgent.stream/astream 建立 HookContext；
- 记录 `agent.stream / before`；
- 正常完成记录 `agent.stream / after`；
- 异常或取消记录 `agent.stream / error`；
- 保证 Hook 不改变 Stream Event。

**验证**

- 多个 text_delta 不产生多个 Hook Handler 调用；
- 每一轮 LLM Stream 只有一对 before/after；
- Agent、LLM、Tool Hook 共享 run_id；
- 每轮 LLM Stream 保持独立 llm_call_id；
- Hook 失败不影响 Stream。

## 任务九：扩展 ObservableAgent 流式代理

**更新文件**

- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_agent.py`

**开发内容**

- 透明代理 BaseAgent.stream；
- 透明代理 BaseAgent.astream；
- 保持 Event 顺序和字段不变；
- 在整个生成器生命周期内保持 HookContext；
- 生成器正常结束、异常和取消时正确恢复 ContextVar。

**验证**

- 调用方获得的 Event 与原始 Agent Event 相同；
- 生成器结束后 HookContext 不泄漏；
- 同时运行多个 astream 时 run_id 互不污染。

## 任务十：客户端适配示例和序列化检查

**更新文档**

- `apps/agent/docs/arch/agent-stream-event-design.md`
- `apps/agent/docs/arch/agent-orchestration-foundation-design.md`
- `apps/agent/docs/plan/agent-orchestration-foundation-development-plan.md`

**开发内容**

- 增加 TUI 消费示例；
- 增加 Web SSE 伪代码；
- 增加 TTS 缓冲示例；
- 明确 AgentPlugin/EventBus 为未来范围；
- 确认 Event 可以被外层序列化为 JSON；
- 不在本次增加 Web 框架依赖。

## 任务十一：分层验证

**测试命令**

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/events -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/tools -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/capability -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/hooks -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration -q
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
```

**静态检查**

```bash
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test
git diff --check
```

**验证重点**

- Event 类层次正确；
- Stream 和 Hook 不混用职责；
- 不逐 Delta 触发 Hook；
- 同步/异步事件语义一致；
- 工具批次顺序正确；
- 非流式 42 条现有测试不回归；
- ReActAgent 不依赖 EventBus。

## 任务十二：真实模型冒烟验证

使用当前配置的真实 `thinking` 模型验证：

### 纯文字流

- 模型分多段返回文字；
- TUI 能逐段打印；
- 最后产生 Completed；
- Hook 只记录一轮聚合结果。

### 工具流

- 模型先输出“让我调用工具”一类文字；
- 产生 ToolStarted；
- 本地工具完成后产生 ToolCompleted；
- 第二轮 LLM 继续输出；
- 最终产生 Completed。

### 异步流

- `perception.astream` 或 `thinking.astream` 可以真实运行；
- 客户端能够逐 Event 消费；
- 取消行为按设计传播。

真实验证不输出 API Key，不把临时验证文件混入代码提交。

## 完成标准

- 设计文档验收项全部满足；
- 全量测试通过；
- 真实同步与异步 Stream 可运行；
- Stream 可以被 TUI 直接消费；
- AgentTextDeltaEvent 可供 TTS 缓冲；
- 工具事件包含名称、参数和结果；
- Hook 只记录聚合后的流式生命周期；
- 非流式接口无回归；
- 未提前实现 Plugin、EventBus 和 Blackboard。

## 推荐提交拆分

1. Event 基类和 Agent Stream Event；
2. 工具并行声明和有序分批；
3. ReActAgent stream；
4. ReActAgent astream；
5. 流式 Hook 包装；
6. 流式测试和真实验证；
7. 文档同步。
