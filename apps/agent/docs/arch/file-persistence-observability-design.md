# File Persistence and Observability Design｜文件持久化与监测层设计

## 文档定位

本文描述 Agent Runtime 的本地技术轨迹与运行日志持久化。

当前分支已经完成本文定义的第一阶段文件持久化与监测能力。

本层只服务：

- 故障排查；
- 执行链路审计；
- 模型、工具和插件行为分析；
- Token、耗时和错误分析；
- Agent 技术回放。

本层不作为业务对话的权威数据源，也不参与正常对话恢复。用户消息、风格化后的助手消息和业务会话历史由未来 Backend 数据库保存与恢复。

## 核心边界

```text
Backend Database
└── 业务对话
    ├── 用户输入
    ├── 风格化助手回复
    ├── 会话历史
    └── 产品业务数据

Agent Local Files
└── 技术运行记录
    ├── 完整执行轨迹
    ├── Hook 观测事件
    ├── Tool / LLM / Plugin 细节
    └── 人可读运行日志
```

正常会话恢复流程：

```text
Backend Database
→ History
→ UserInput
→ BlackboardPlugin
→ AgentPlugin
```

禁止：

```text
trace.jsonl
→ 推断业务历史
→ 恢复正常对话
```

## 数据目录

Agent Runtime 必须通过 `.env` 或运行环境显式配置：

```bash
ICARUS_DATA_DIR=/absolute/path/to/icarus-data
```

未配置时程序应明确报错，不静默写入当前工作目录。

测试和开发可以通过构造参数或临时环境变量覆盖。

## Workspace 与 Session

### Workspace

Workspace 只负责日志归档分组。

```python
workspace_key = sha256(
    normalized_workspace_path.encode("utf-8")
).hexdigest()[:16]
```

绝对路径不直接作为目录名。真实路径保存在 `workspace.json`。

### Session

Session 是一组技术执行轨迹的归档身份。

- 后端可提供 `session_id`；
- 未提供时 Agent Runtime 生成 UUID；
- 工作目录已有历史 Session 时，默认仍创建新 Session；
- 只有未来 InputPlugin 显式指定已有 Session 时才选择旧 Session；
- Agent 本地文件不用于恢复 History。

当前 UserInputPlugin 绑定一个长期 PersistenceSession；每次输入只创建新的 Task Scope 和 task_id。

### Task 与 Run

```text
session_id
└── task_id
    └── run_id
        └── event_id
```

- `session_id`：一段技术会话归档；
- `task_id`：一次用户任务链；
- `run_id`：一次具体 Agent 执行；
- `event_id`：一条 Hook 或业务 Event。

## 目录结构

```text
$ICARUS_DATA_DIR/
└── workspaces/
    └── <workspace_key>/
        ├── workspace.json
        ├── runtime.log
        └── sessions/
            └── <session_id>/
                ├── session.json
                ├── trace.jsonl
                ├── runtime.log
                └── assets/
```

### workspace.json

```json
{
  "workspace_key": "workspace-hash",
  "workspace_path": "/absolute/workspace/path",
  "created_at": "UTC timestamp",
  "last_seen_at": "UTC timestamp"
}
```

### session.json

```json
{
  "session_id": "session-uuid",
  "created_at": "UTC timestamp",
  "updated_at": "UTC timestamp",
  "status": "active"
}
```

`session.json` 只保存 Agent 技术 Session 元数据，不保存业务消息历史。

### assets/

当前图片使用 URL，因此 Trace 记录 URL。

未来支持本地文件时：

- 文件复制到 Session `assets/`；
- Trace 记录 Session 相对路径；
- 二进制内容不直接写入 JSONL。

## trace.jsonl

`trace.jsonl` 是完整结构化技术轨迹，一行一个 JSON 对象。

记录范围：

- 用户原始输入 Event；
- Blackboard 完整 Context；
- Agent 开始、完成、失败；
- 每轮聚合后的 LLM 输入与输出；
- Reasoning；
- ToolDefinition；
- ToolCall 和 ToolResult；
- Usage 和 FinishReason；
- Agent Stream 聚合结果；
- EventBus 发布和路由；
- Plugin 消费和生命周期；
- Hook Event；
- 错误、时间和耗时。

不逐个写入每个文字 Delta；记录每轮聚合后的完整文本。

### 记录格式

Workspace 和 Session 由文件路径表达，不在每条记录中重复写入。

```json
{
  "schema_version": 2,
  "record_type": "hook_event",
  "event_id": "event-uuid",
  "occurred_at": "UTC timestamp",
  "task_id": "task-uuid",
  "run_id": "run-uuid",
  "name": "tool.execute",
  "phase": "after",
  "context": {},
  "data": {}
}
```

Writer 入队请求携带路由信息：

```python
@dataclass(frozen=True)
class TraceWriteRequest:
    workspace_key: str
    session_id: str
    record: HookEvent
```

`workspace_key` 和 `session_id` 只用于确定文件位置。

## runtime.log

`runtime.log` 是面向人的运行日志。

Workspace 级：

```text
workspaces/<workspace_key>/runtime.log
```

记录 Session 建立前或跨 Session 的异常：

- 配置加载；
- Runtime 启停；
- Plugin 注册失败；
- EventBus 启动失败；
- 持久化 Writer 失败；
- 未捕获异常。

Session 级：

```text
workspaces/<workspace_key>/sessions/<session_id>/runtime.log
```

记录：

- Agent Run 状态；
- Plugin 生命周期；
- Queue 积压告警；
- LLM 和 Tool 错误；
- Event 路由异常；
- Trace 写入状态。

`runtime.log` 不承担完整 Prompt、ToolResult 和模型输出存储，这些属于 `trace.jsonl`。

## SessionIdentity

```python
@dataclass(frozen=True)
class SessionIdentity:
    workspace_path: Path
    workspace_key: str
    session_id: str
```

身份来源：

- Workspace Path：Runtime 启动参数；
- Workspace Key：PathResolver 计算；
- Session ID：后端提供或 Runtime 生成；
- Task ID 不属于 SessionIdentity；进入 Task Scope 后单独写入 HookContext。

## HookContext 传播

当前 HookContext 需要支持嵌套合并。

```text
Session Scope
  workspace_key
  session_id
  task_id

Agent Scope
  run_id
  model_role

LLM / Tool / Plugin Hook
  自动继承全部身份
```

进入 Agent HookContext 时不得覆盖外层 Session 数据。

HookEvent 中：

- `workspace_key` 和 `session_id` 用于 Writer 路由；
- `task_id`、`run_id` 保留在 Trace 记录；
- 其他 Context 进入 `context`。

## Trace 写入链

Hook 不直接执行文件 I/O。

```text
HookEvent
→ FileTraceHook
→ queue.Queue
→ FileTraceWriter Thread
→ trace.jsonl
```

### FileTraceHook

同步和异步 Hook 都只执行快速入队：

```python
class FileTraceHook(BaseHook):
    def handle(self, event: HookEvent) -> None:
        self.writer.offer(event)

    async def ahandle(self, event: HookEvent) -> None:
        self.writer.offer(event)
```

选择标准库 `queue.Queue` 和独立 Writer Thread，避免：

- 同步 Hook 没有 EventLoop；
- Tool ThreadPool 跨线程触发；
- asyncio.Queue 绑定不同 EventLoop。

### FileTraceWriter

Writer Thread 独占文件写入：

```text
读取队列
→ 解析 Workspace/Session 路径
→ 递归脱敏
→ JSON 序列化
→ 追加一行
→ 定期 flush
```

Hook 在请求成功进入队列后返回，不等待磁盘写入或 fsync。

进程关闭时：

```python
writer.stop(drain=True)
```

等待已入队记录落盘并 flush。

写入失败：

- 记录 stderr 或 fallback logger；
- 增加失败计数；
- 不中断 Agent 主流程。

## Logging Handler

普通运行日志使用标准 Python Logging。

```text
logging
→ WorkspaceSessionContextFilter
→ WorkspaceSessionFileHandler
→ runtime.log
```

Handler 根据当前 SessionIdentity 将日志路由到 Workspace 或 Session 文件。

不把普通 Logging 再包装成 HookEvent。

## 完整记录与脱敏

完整 Trace 仍必须递归脱敏。

至少过滤字段：

- `api_key`
- `authorization`
- `token`
- `cookie`
- `password`
- `secret`
- `credential`

要求：

- 字段名大小写不敏感；
- 嵌套 dict/list 递归处理；
- `.env` 内容禁止写入；
- 二进制不内联；
- 文件权限尽量使用 `0600`；
- 目录权限尽量使用 `0700`。

用户主动在 Prompt 或 Tool 输出中输入的秘密无法完全识别。文档应明确完整 Trace 可能包含敏感业务内容。

## 文件生命周期

初版：

- 永久保留；
- 不自动清理；
- 不轮转；
- 不压缩。

但需要监测：

- 单条记录序列化大小；
- 当前 Trace 文件大小；
- 写入失败次数；
- 超过可配置告警阈值时输出 Warning。

未来再增加清理、轮转和配额。

## Plugin 目录规范

具体 Plugin 采用独立目录、显式注册。

```text
apps/agent/src/agent_orchestration/plugins/
├── agent/
├── blackboard/
├── persistence/
├── contracts/
├── style/
├── memory/
├── skill/
└── knowledge/
```

持久化目录：

```text
plugins/persistence/
├── __init__.py
├── session_identity.py
├── path_resolver.py
├── metadata_store.py
├── redactor.py
├── trace_hook.py
├── trace_writer.py
└── log_handler.py
```

初版不实现自动发现、Manifest 和动态加载。应用启动时显式注册组件。

`FileTraceHook` 是 Hook Handler，不需要作为 BasePlugin 注册。持久化包后续如需消费业务 Event，再新增 PersistencePlugin。

## 与后端的边界

后端负责业务对话：

- 用户输入；
- 风格化助手回复；
- History；
- 会话标题和业务状态；
- 用户、权限和产品数据。

Agent 负责技术日志：

- 完整执行轨迹；
- Runtime 监测；
- Tool、LLM 和 Plugin 明细。

后端不直接读取 Agent 本地文件。正常对话恢复只从后端数据库获得 History，再传给 Agent。

## 本期实现范围

### 实现

- `ICARUS_DATA_DIR` 配置；
- SessionIdentity；
- Workspace/Session PathResolver；
- workspace/session 元数据；
- 可合并 HookContext；
- Redactor；
- FileTraceWriter Thread；
- FileTraceHook；
- Workspace/Session Logging Handler；
- 写入大小和失败监测；
- 单元、集成和真实轨迹验证。

以上能力当前已经实现。真实模型验证产生了完整 Agent、LLM、EventBus 和 Plugin Trace，并在测试后清理临时数据目录。

### 不实现

- SQLite；
- 业务消息库；
- `messages.jsonl`；
- 本地会话恢复；
- TUI 特殊存储；
- PersistencePlugin 业务 Event 存储；
- 自动清理、轮转、压缩；
- 自动 Plugin 发现和 Manifest；
- 后端数据库与后端 API。

## 验收标准

- 未配置 `ICARUS_DATA_DIR` 时明确失败；
- 同一 Workspace 的不同 Session 写入不同目录；
- 不同 Workspace 不冲突；
- Hook 触发只等待队列接受；
- 同步、异步和 Tool Thread Hook 都可安全入队；
- Writer 单线程顺序写入 JSONL；
- 关闭 Drain 后队列为空且文件完整；
- Trace 包含 Agent、LLM、Tool 和 Plugin Runtime 聚合轨迹；
- Workspace/Session 不重复写入每条 JSON；
- task_id、run_id 和 event_id 可用于关联；
- 递归脱敏生效；
- runtime.log 正确路由；
- 写入失败不影响 Agent 主流程；
- 正常对话恢复不读取本地 Trace；
- 全量现有测试不回归。
