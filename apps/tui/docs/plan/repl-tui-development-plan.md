# REPL TUI MVP Development Plan｜REPL TUI MVP 开发计划

> 历史计划：本文记录最初标准库 REPL MVP 的实施决策，不再作为当前 TUI 开发依据。
> 第一阶段终端框架设计和实施记录分别见
> `apps/tui/docs/arch/tui-terminal-framework-design.md` 与
> `apps/tui/docs/plan/tui-terminal-framework-development-plan.md`。当前 Textual TUI 设计和
> 计划见 `apps/tui/docs/arch/tui-persistent-input-queue-design.md` 与
> `apps/tui/docs/plan/textual-tui-development-plan.md`。
> 其中旧 `AgentErrorEvent` 映射已由统一 `TaskErrorEvent` 替代。

## 目标

实现一个标准库 REPL，用于验证和使用当前 Agent Core：

```text
Terminal Input
→ AgentRuntimeService
→ UserInputPlugin FIFO
→ BlackboardPlugin
→ AgentPlugin
→ ReActAgent / Tool
→ Event Stream
→ Terminal Output
```

## 实施原则

- `apps/tui` 是独立应用；
- 不引入第三方 TUI 框架；
- TUI 只依赖 AgentRuntimeService；
- Agent Runtime 组装不泄漏到 TUI；
- 每次进程只运行一个 Agent Session；
- REPL 串行输入；
- 当前任务结束后才显示下一次提示；
- 当前不实现取消和历史恢复；
- Trace 使用现有 PersistenceRuntime。

## 任务一：创建 Agent 应用层

**新增文件**

- `apps/agent/src/application/__init__.py`
- `apps/agent/src/application/agent_runtime_service.py`
- `apps/agent/test/application/test_agent_runtime_service.py`

**开发内容**

- 实现 AgentRuntimeService；
- 创建 HookRegistry；
- 创建 PersistenceRuntime；
- 创建 AgentFactory；
- 打开固定 PersistenceSession；
- 创建 PluginManager；
- 注册 UserInputPlugin、BlackboardPlugin、AgentPlugin；
- 增加 OutputBridgePlugin；
- 建立固定订阅关系；
- 提供 `start/submit/next_event/stop`；
- 启动失败时清理已启动资源；
- stop 时按顺序 Drain Plugin、LLM 和 Persistence。

**验证**

- 重复 start/stop 行为明确；
- submit 前未启动时报错；
- Event 顺序正确；
- 停止后无后台任务泄漏；
- Trace Session 正确创建；
- Agent Core 内部对象不暴露给 TUI。

## 任务二：实现 OutputBridgePlugin

**新增文件**

- `apps/agent/src/application/output_bridge.py`
- `apps/agent/test/application/test_output_bridge.py`

**开发内容**

- 实现 BasePlugin；
- 一个统一输出队列；
- 接收 UserInputPlugin 与 AgentPlugin Event；
- `next_event()` 返回下一条 Event；
- 不转换、不丢弃、不重排；
- stop 时 Drain。

**验证**

- Event 原样转发；
- 不同来源顺序稳定；
- Service 可以按 task_id 筛选当前任务；
- 空队列异步等待不占 CPU。

## 任务三：创建 TUI 应用骨架

**新增文件**

- `apps/tui/__init__.py`
- `apps/tui/main.py`
- `apps/tui/renderer.py`
- `apps/tui/test/test_renderer.py`
- `apps/tui/test/test_repl.py`

**开发内容**

- 使用 `asyncio.run(main())`；
- 从当前工作目录创建 AgentRuntimeService；
- 支持可选 `--session-id`；
- 读取 `input("Icarus> ")`；
- `exit/quit` 退出；
- EOF 退出；
- 提交后消费当前 task Event；
- 等待 InputFinishedEvent 后进入下一轮。

**验证**

- 输入/退出；
- 空输入忽略；
- 多轮输入不由 TUI 传递 History；
- task_id 过滤；
- Service 错误显示；
- 不依赖第三方 TUI 包。

## 任务四：实现 Event Renderer

**文件**

- `apps/tui/renderer.py`

**开发内容**

```text
AgentTextDeltaEvent      → 原地打印
AgentToolStartedEvent    → 工具名 + 参数
AgentToolCompletedEvent  → 成功/失败摘要
InputQueuedEvent         → 队列位置
AgentErrorEvent          → 错误
InputFinishedEvent       → 当前任务结束
```

- JSON 参数使用 `ensure_ascii=False`；
- ToolResult 默认不完整展开；
- 流式文本后正确换行；
- 未知 Event 默认忽略或 Debug 输出。

**验证**

- 中文；
- 多个 Delta；
- Tool 成功/失败；
- 大 ToolResult 不被打印；
- 终端输出格式稳定。

## 任务五：History 边界

**开发内容**

- TUI 不维护或拼接 History；
- TUI 每轮只提交当前 Prompt；
- BlackboardPlugin 维护当前 Agent 实例的跨轮消息；
- 恢复业务历史时，通过 AgentRuntimeService 初始化参数一次性注入；
- 不读取本地 trace.jsonl 恢复 History。

**验证**

- 第二轮调用不传 History；
- AgentRuntimeService 的第二轮 Agent 调用自动携带第一轮消息；
- 失败任务不进入 Blackboard History。

## 任务六：生命周期与错误处理

**开发内容**

- 启动配置失败；
- Persistence 配置失败；
- LLM 初始化失败；
- Plugin 启动失败；
- EOF；
- KeyboardInterrupt；
- 正常 stop；
- stop timeout；
- 未捕获异常打印并返回非零退出码。

当前任务执行阶段收到 Ctrl+C 时，不取消任务；提示当前 MVP 不支持取消。

## 任务七：真实模型验证

### 纯文本

```text
Icarus> 只回复 TUI_OK
```

验证流式输出。

### 工具调用

```text
Icarus> 读取项目中的 agent-test.txt
```

验证：

- 工具开始；
- 工具完成；
- 最终回答；
- Trace 落盘。

### 多轮对话

连续两轮，验证 Blackboard 自动为第二轮提供第一轮 History。

### 退出

输入 `quit`，验证 PluginManager、LLM 和 Writer 正常 Drain。

## 任务八：文档与启动说明

**新增或更新**

- `apps/tui/README.md`
- 根目录 `AGENTS.md`（只在需要补充 app 测试命令时更新）

启动命令建议：

```bash
apps/agent/.venv/bin/python -m apps.tui.main
```

要求：

- 已配置 API Key；
- 已配置 `ICARUS_DATA_DIR`；
- 当前目录作为 Workspace。

## 测试命令

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/application -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test apps/tui
git diff --check
```

## 推荐提交拆分

1. AgentRuntimeService + OutputBridgePlugin；
2. REPL 与 Renderer；
3. Service/TUI 单元测试；
4. 真实模型验证；
5. README 与文档。

## 完成标准

- REPL 可以完成真实文本对话；
- REPL 可以展示工具状态；
- 输入与输出流式链路完整；
- Blackboard History 在当前 Agent Runtime 内生效；
- Trace 正常落盘；
- 退出无后台任务泄漏；
- 不引入第三方 TUI 框架；
- TUI 不直接组装 Agent Core；
- 全量测试通过。
