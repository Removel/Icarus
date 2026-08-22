# Agent Run Intervention Development Plan｜Agent 运行中介入开发计划

## 目标

基于 `apps/agent/docs/arch/agent-run-intervention-design.md`，实现职责明确的 Task 运行中介入：

- Task 接受后即可接收 Plugin 补充信息和用户侧取消请求；
- 补充信息逐条记录，在 ReAct 安全边界按 FIFO 合并注入；
- 取消由代码立即介入，不交给模型判断；
- Agent、Task 和 Tool 终态保持唯一、可观测并可清理；
- 已应用补充信息在成功后进入 Session History；
- Bash 子进程支持 terminate、宽限期和 kill。

## 实施原则

- 当前保持一个 Task 对应一个 Agent Run，但保留独立 `task_id` 和 `run_id`；
- ReActAgent 继续无状态，运行控制对象通过调用参数传入；
- Event 类型表达操作语义，不通过文本或 priority 字段判断取消；
- EventBus 继续只按来源路由，不增加优先级队列；
- Hook 只观测，不负责控制；
- 已经发生的 Tool 副作用不回滚；
- 正式功能完成前只运行受影响模块的定向测试；
- 全部实现完成后再运行 Agent/TUI 全量测试。

## 当前进度

| 模块 | 状态 | 剩余工作 |
|---|---|---|
| TaskChannel 与 Registry | 已完成 | 活动通道 + 1024 条有界已结束 Task 墓碑 |
| ReAct 安全检查点与 Context Batch | 已完成 | 四种入口使用相同检查语义 |
| AgentPlugin / UserInputPlugin 生命周期 | 已完成 | 启动前取消和终态竞争均有回归测试 |
| Service Cancel / Plugin Event | 已完成 | 用户侧只开放 Cancel；Context 只允许 Plugin Event |
| Blackboard 完整消息历史 | 已完成 | 正常提交完整 Tool 轨迹；取消提交安全消息前缀 |
| Tool / Bash 取消 | 已完成 | 异步传播、同步隔离和 Bash 子进程回收已覆盖 |
| TUI Cancelling / Cancelled | 已完成 | Ctrl+C、部分输出和 pending queue 行为已覆盖 |
| 文档与全量验证 | 已完成 | Agent 335、TUI 86、compileall、diff check 通过 |

## 对外接口与返回约定

```python
async def AgentRuntimeService.cancel_task(
    task_id: str,
    reason: str | None = None,
) -> TaskOperationResult: ...
```

Cancel 的两个入口共享同一个操作处理核心；Context 只使用 Plugin Event：

| 调用入口 | 返回方式 | 是否发布结果 Event |
|---|---|---|
| Service Cancel | 直接返回 `TaskOperationResult` | 否 |
| Plugin Context / Cancel Event | 异步发布对应 Result Event | 是 |

结果状态固定为 `accepted`、`not_found`、`not_running`、`already_cancelling`、
`already_finished` 和 `invalid_content`。正常生命周期竞争通过结果状态表达，不抛出控制流异常。

## 生命周期与竞态验收表

| 竞争场景 | 必须保证的结果 |
|---|---|
| Context 与首个 LLM Step | ACCEPTED / PREPARING_CONTEXT 阶段接收的内容在首个 LLM 前注入 |
| Context 与 Tool Batch | 不在 ToolCall 与 ToolResult 之间插入，批次结束后在下一次 LLM 前注入 |
| Context 与 Completed | `close_or_drain` 原子决定继续一个 Step 或关闭接收窗口，不丢失已接受内容 |
| Cancel 与 Context Ready | 已进入 CANCELLING 的 Task 不启动 Agent Run |
| Cancel 与 LLM / Tool | 取消活动 execution task；取消后不再启动新的 LLM 或 Tool |
| Cancel 与正常终态 | 第一个成功关闭 TaskChannel 的终态获胜，只发布一个 Agent 终态和一个 InputFinished |
| 重复 Cancel | 第一次返回 accepted，后续返回 already_cancelling 或 already_finished |
| 迟到 Context | Task 关闭后返回 already_finished，不进入模型或 Session History |
| 同步 Tool 与 Cancel | 无法强杀线程，但迟到结果必须与已取消 Run 隔离 |
| Bash 与 Cancel | terminate，宽限期后 kill，并 wait 回收子进程 |

TaskChannelRegistry 在 Task 结束后移除活动通道，并仅保留有界的已结束 Task 墓碑，使短期迟到操作
能够得到 `already_finished`，同时避免长 Session 内存无限增长。墓碑淘汰后返回 `not_found`。

## 实施顺序

```text
TaskChannel 与 Registry
→ UserInput Task 生命周期
→ AgentPlugin Active Run
→ ReAct 安全检查点与 Context Batch
→ 取消终态和历史清理
→ Tool 取消与 Bash 子进程
→ Service Cancel / Plugin Event 入口
→ TUI 取消接入
→ 全量验证
```

## 任务一：运行控制基础类型

**新增文件**

- `apps/agent/src/agent_orchestration/run_control/__init__.py`
- `apps/agent/src/agent_orchestration/run_control/events.py`
- `apps/agent/src/agent_orchestration/run_control/types.py`
- `apps/agent/src/agent_orchestration/run_control/channel.py`
- `apps/agent/src/agent_orchestration/run_control/registry.py`

**开发内容**

- 定义 TaskContextInputEvent 和 TaskCancelRequestedEvent；
- 定义 TaskOperationResult 和必要的异步结果 Event；
- 定义 RuntimeContextRecord、AppliedContextBatch 和状态枚举；
- TaskChannel 使用锁保护 Context FIFO、取消和最终关闭；
- TaskChannelRegistry 管理 `task_id → TaskChannel`；
- 正常竞争返回明确状态，不依赖异常。

**定向测试**

- FIFO 与批量 drain；
- 空内容拒绝；
- 重复取消；
- Context 与 Completed 原子竞争；
- 关闭后拒绝；
- Registry 重复创建与清理。

## 任务二：Task 接受和准备阶段介入

**更新文件**

- `plugins/user_input/plugin.py`
- `plugins/user_input/events.py`
- `plugins/user_input/__init__.py`
- `application/agent_runtime_service.py`

**开发内容**

- UserInputPlugin 接受 Task 时创建 TaskChannel；
- Task 开始处理时进入 PREPARING_CONTEXT；
- Worker 同时等待 Agent 终态与取消请求；
- Run 未启动时取消，直接发布 InputFinishedEvent(cancelled)；
- 每个 Task 只发布一次 InputFinishedEvent；
- Task 结束后关闭并移除通道。

**定向测试**

- ACCEPTED 和 PREPARING_CONTEXT 阶段接收 Context；
- 上下文准备阶段取消不启动 Agent；
- 排队/活动 Task 状态不串扰；
- cancelled 只发布一次。

## 任务三：AgentPlugin 活动 Run 与统一操作入口

**更新文件**

- `plugins/agent/plugin.py`
- `plugins/agent/__init__.py`
- `plugins/__init__.py`

**开发内容**

- 用 `task_id → ActiveAgentRun` 替换匿名 asyncio Task 集合；
- Agent Run 启动时创建业务 run_id；
- Context Ready 到达时先检查 TaskChannel；
- Service Cancel 和 EventBus 操作进入同一处理函数；
- 核心处理函数只返回结果；仅 EventBus 入口发布异步结果 Event；
- `accepts_event()` 仅接收允许的 Event；
- 取消活动 execution_task；
- 只发布一个 Agent Terminal Event；
- 完成后移除活动 Run。

**定向测试**

- Run 注册和移除；
- 未找到、已结束和重复取消；
- 来源无关 Context Event；
- 迟到 Context Ready 不启动 Run；
- Completed 与 Cancel 竞争。

## 任务四：ReAct 运行控制与补充信息

**更新文件**

- `capability/base_agent.py`
- `capability/react_agent.py`
- `capability/types.py`
- `hooks/wrappers/observable_agent.py`

**开发内容**

- 四种 Agent 入口接受可选 Run Control；
- 每次 LLM 前先检查取消，再 drain Context；
- Tool Batch 启动前检查取消；
- Completed 前执行原子关闭或取出补充信息；
- 多条 Context 合并为一条结构化 User Message；
- AgentResponse 返回完整 messages 和当前 Task 消息起点；
- ObservableAgent 使用传入的业务 run_id；
- 独立调用未传控制对象时保持原有行为。

**定向测试**

- 首个 LLM 前注入；
- ToolResult 后、下一 Step 前注入；
- 不在 ToolCall 与 ToolResult 之间插入；
- Completed 竞争触发额外 Step；
- 多条 Context FIFO 合并；
- 四种入口控制语义一致；
- Agent/LLM/Tool Hook 共享业务 run_id。

## 任务五：取消终态与 Session History

**更新文件**

- `capability/types.py`
- `plugins/user_input/events.py`
- `plugins/blackboard/state.py`
- `plugins/blackboard/plugin.py`
- `plugins/skill/plugin.py`

**开发内容**

- 新增 AgentCancelledEvent；
- InputFinishedEvent.status 增加 cancelled；
- Run 已启动时 AgentPlugin 发布 AgentCancelledEvent；
- Run 未启动时 UserInputPlugin 直接收口 cancelled；
- Blackboard 和 Skill 清理取消 Task；
- ReActAgent 在 LLM 前、完整 Tool Batch 后和最终回答后保存协议完整检查点；
- AgentCancelledEvent 携带最近的安全消息前缀；
- 正常 Task 提交当前 Task 完整消息链，包括 ToolCall 和 ToolResult；
- 取消 Task 提交最近安全消息前缀；Run 启动前取消不提交；
- 保留双终态乱序与幂等。

**定向测试**

- 准备阶段取消；
- LLM 执行中取消；
- Tool 前取消；
- 首次 LLM 中取消保留已交给模型的 User / Plugin Context；
- Tool Batch 中取消丢弃不完整 ToolCall Batch；
- 后续 LLM 中取消保留此前完整 ToolCall / ToolResult；
- 成功任务完整 Tool 轨迹和 Context Batch 写入下一轮 History；
- 唯一 Agent/Task 终态。

## 任务六：Tool 取消与 Bash 子进程

**更新文件**

- `tools/base_tool.py`
- `tools/tool_executor.py`
- `tools/builtin/bash_tool.py`
- 相关 Tool 测试。

**开发内容**

- 每个 Tool Batch 启动前检查取消；
- 原生异步 Tool 传播取消；
- 同步线程 Tool 取消后隔离迟到结果；
- BashTool 使用 asyncio 子进程；
- 取消 Bash 时 terminate，宽限期后 kill，并 wait 回收；
- 保持同步 invoke 的现有结果格式。

**定向测试**

- 尚未启动 Tool 被阻止；
- 异步 Tool 收到取消；
- 同步 Tool 迟到结果不进入下一 Step；
- Bash 正常完成、timeout、terminate 和 kill；
- 并行批次取消后无子任务泄漏。

## 任务七：Service Cancel、Plugin Context 和 TUI 接入

**更新文件**

- `application/agent_runtime_service.py`
- `application/__init__.py`
- Runtime 组装和输出测试；
- `apps/tui/src/app.py`、状态和 Projector；
- 对应 TUI 测试。

**开发内容**

- Service 只暴露 cancel_task；
- Context 只允许已订阅 Plugin 发布 TaskContextInputEvent；
- Runtime 组装允许的操作 Event 来源；
- 异步 Event 来源发布操作结果 Event；
- TUI Ctrl+C 调用 cancel_task；
- 显示 Cancelling 和 Cancelled；
- 收到 cancelled 后再调度下一条。

**定向测试**

- Service Cancel 与 Plugin Cancel Event 使用同一结果；
- Runtime 输出包含取消终态；
- TUI 保留部分输出和 pending queue；
- Ctrl+C 不停止整个 Runtime；
- 下一条任务只在 cancelled 终态后开始。

## 任务八：最终验证与文档同步

先按以下顺序运行定向验证，失败时只修复本功能影响范围：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/run_control \
  apps/agent/test/agent_orchestration/capability/test_react_agent.py \
  apps/agent/test/agent_orchestration/hooks/wrappers/test_observable_wrappers.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/agent/test_plugin.py \
  apps/agent/test/agent_orchestration/plugins/user_input/test_plugin.py \
  apps/agent/test/agent_orchestration/plugins/blackboard/test_plugin.py \
  apps/agent/test/application/test_agent_runtime_service.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/tools/builtin/test_builtin_tools.py \
  apps/agent/test/agent_orchestration/tools/test_tools.py -q

apps/agent/.venv/bin/python -m pytest apps/tui/test -q
```

完成所有功能和定向测试后执行：

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test apps/tui/src apps/tui/test
git diff --check
```

同步：

- `agent-run-intervention-design.md`；
- `plugin-event-flow-current-state.md`；
- `docs/todo/agent-core.md`；
- `docs/todo/tui.md`。

## 完成标准

- 已授权 Plugin 的 Context 和用户侧 Cancel 均可进入当前 Task；
- 上下文准备和 Agent Run 阶段都可取消；
- 补充信息至少影响后续一次 LLM Step；
- 取消后不启动新的 LLM 或 Tool；
- Bash 子进程可以确认终止；
- 同步 Tool 迟到结果不会进入 Agent；
- 成功、失败和取消终态唯一；
- Session History 与模型实际使用的 Context 一致；
- 定向和全量验证全部通过。

## 实施结果

- Agent 全量测试：335 passed；
- TUI 全量测试：86 passed，包含 7 个 Snapshot；
- `compileall`：通过；
- `git diff --check`：通过；
- 未执行真实模型冒烟测试：本轮功能由确定性 Stub、Plugin 集成和 TUI Pilot/Snapshot 覆盖，
  不需要凭证即可验证控制与历史语义。
