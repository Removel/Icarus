# File Persistence and Observability Development Plan｜文件持久化与监测层开发计划

> 历史计划：本文记录文件观测层的初始实现。Workspace/Session 业务元数据和公共 Conversation
> 已迁移到 Agent Application 的 SessionStore；文件层当前只负责 Plugin State、Runtime Snapshot、
> Asset、Trace 和 Log。

## 目标

实现 Agent Runtime 的本地技术轨迹和运行日志持久化：

- 按 Workspace/Session 分目录；
- Hook 快速入队；
- Writer Thread 异步写 `trace.jsonl`；
- Python Logging 写 `runtime.log`；
- 完整记录并递归脱敏；
- 正常关闭时 Drain；
- 不影响 Agent 主流程；
- 不承担业务对话恢复。

## 当前状态

- 第一阶段文件持久化与监测：已完成；
- Plugin 独立目录迁移：已完成；
- UserInputPlugin 使用长期 Session + 每轮 Task Scope：已完成；
- 持久化专项测试：`12 passed`；
- 插件分组测试：`21 passed`；
- 全量测试：`90 passed`；
- 真实模型落盘验证：`60` 条 Trace，覆盖 Agent、LLM、EventBus 和 Plugin；
- Writer Failure：`0`；
- SQLite、业务消息库和本地对话恢复：未实现，符合范围。

## 实施原则

- 必须配置 `ICARUS_DATA_DIR`；
- Workspace/Session 由目录表达，不在每条 Trace 中重复；
- Trace 保留 `task_id`、`run_id` 和 `event_id`；
- 后端数据库是业务对话的权威来源；
- Agent 本地文件只保存技术轨迹和监测日志；
- Hook 只等待内存队列接受，不等待磁盘；
- Writer Thread 独占文件写入；
- 完整内容落盘前递归脱敏；
- 不实现 SQLite、业务消息库和本地对话恢复；
- 不逐个记录每个文字 Delta；
- 文件初版目前永久保留。

## 目录与文件

```text
apps/agent/src/agent_orchestration/plugins/persistence/
├── __init__.py
├── session_identity.py
├── path_resolver.py
├── metadata_store.py
├── redactor.py
├── trace_record.py
├── trace_writer.py
├── trace_hook.py
└── log_handler.py
```

测试：

```text
apps/agent/test/agent_orchestration/plugins/persistence/
```

## 实施顺序

```text
配置
→ Identity
→ PathResolver
→ Metadata
→ HookContext 合并
→ Redactor
→ TraceRecord
→ Writer Thread
→ FileTraceHook
→ Logging Handler
→ Agent / Plugin Runtime 组装
→ 集成与真实验证
→ Plugin 目录迁移
```

## 任务一：增加数据目录配置

**更新文件**

- `apps/agent/.example.env`
- `apps/agent/src/model_config/config_model.py`
- `apps/agent/src/model_config/config_loader.py`
- `apps/agent/test/model_config/test_config_loader.py`
- `.gitignore`

**开发内容**

- 增加 `ICARUS_DATA_DIR`；
- 使用绝对路径；
- 未配置时明确抛出配置错误；
- 测试可通过构造参数或环境变量覆盖；
- 增加项目本地开发数据目录忽略规则，例如 `.icarus-data/`。

**验证**

- 正确读取绝对路径；
- 相对路径被拒绝或明确规范化，初版建议拒绝；
- 未配置时失败；
- `.env` 内容不进入日志；
- 原模型配置测试不回归。

## 任务二：实现 SessionIdentity

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/persistence/session_identity.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/persistence/test_session_identity.py`

**开发内容**

```python
@dataclass(frozen=True)
class SessionIdentity:
    workspace_path: Path
    workspace_key: str
    session_id: str
```

- Workspace Path 规范化；
- Workspace Key 使用 SHA-256 前缀；
- 后端可传 `session_id`；
- 未传时生成 UUID；
- 默认不复用 Workspace 下旧 Session；
- task_id 不属于 SessionIdentity，在一次用户任务的 Task Scope 中补充。

**验证**

- 相同规范化路径得到相同 key；
- 不同路径 key 不冲突；
- 默认生成不同 Session ID；
- 显式 Session ID 原样保留；
- Identity 不包含业务用户信息。

## 任务三：实现 DataPathResolver

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/persistence/path_resolver.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/persistence/test_path_resolver.py`

**开发内容**

- 解析 Workspace 目录；
- 解析 Session 目录；
- `workspace.json` 路径；
- Workspace `runtime.log` 路径；
- `session.json` 路径；
- `trace.jsonl` 路径；
- Session `runtime.log` 路径；
- `assets/` 路径；
- 创建目录时使用安全权限；
- 防止 `session_id` 路径穿越。

**验证**

- 所有路径位于 `ICARUS_DATA_DIR`；
- Session ID 中的 `/`、`..` 等非法字符被拒绝；
- Workspace/Session 目录权限符合平台能力；
- 不同 Workspace/Session 不串目录。

## 任务四：实现元数据文件

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/persistence/metadata_store.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/persistence/test_metadata_store.py`

**开发内容**

- 初始化/更新 `workspace.json`；
- 初始化/更新 `session.json`；
- 使用临时文件 + replace 原子写入；
- UTC 时间；
- 更新 `last_seen_at`、`updated_at` 和状态；
- 不保存业务消息；
- 不保存 Secret。

**验证**

- 首次创建；
- 重复初始化更新 last_seen；
- 原子写入失败不破坏旧文件；
- JSON 内容符合设计；
- 权限正确。

## 任务五：增强 HookContext

**更新文件**

- `apps/agent/src/agent_orchestration/hooks/hook_context.py`
- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_agent.py`
- `apps/agent/test/agent_orchestration/hooks/test_hooks.py`
- `apps/agent/test/agent_orchestration/hooks/wrappers/test_observable_wrappers.py`

**开发内容**

- 嵌套 HookContext 合并外层数据；
- Session Scope 提供：
  - `workspace_key`
  - `session_id`
  - `task_id`
- Agent Scope 增加：
  - `run_id`
  - `model_role`
- 内层同名字段显式覆盖；
- 退出内层后恢复外层；
- 并发任务 ContextVar 隔离；
- Tool Thread 中继续传播 Context。

**验证**

- Session 数据不会被 ObservableAgent 覆盖；
- LLM/Tool Hook 自动继承完整身份；
- 并发 Session 不串；
- 嵌套退出恢复正确；
- 现有 Hook 测试不回归。

## 任务六：实现 Redactor

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/persistence/redactor.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/persistence/test_redactor.py`

**开发内容**

- 递归处理 dict/list/tuple；
- 字段名大小写不敏感；
- 脱敏：
  - `api_key`
  - `authorization`
  - `token`
  - `cookie`
  - `password`
  - `secret`
  - `credential`
- 保留结构；
- Path、Enum、dataclass 等先使用现有 Hook snapshot；
- 二进制转为元信息，不内联；
- 支持自定义附加敏感字段；
- 使用固定替换值，例如 `[REDACTED]`。

**验证**

- 多层嵌套；
- Header；
- Tool 参数；
- Plugin Context；
- 大小写；
- 非敏感字段不变；
- 原对象不被修改。

## 任务七：定义 TraceRecord 与序列化

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/persistence/trace_record.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/persistence/test_trace_record.py`

**开发内容**

- HookEvent → TraceRecord；
- 记录：
  - `schema_version`
  - `record_type`
  - `event_id`
  - `occurred_at`
  - `task_id`
  - `run_id`
  - `name`
  - `phase`
  - `context`
  - `data`
- 移除仅用于路由的 `workspace_key/session_id`；
- JSON 单行序列化；
- 确保 UTF-8；
- 记录序列化字节大小。

**验证**

- 一行一个 JSON；
- 记录可重新解析；
- Workspace/Session 不重复进入 JSON；
- task/run/event ID 保留；
- 完整 LLM/Tool 快照可以序列化；
- 脱敏在序列化前生效。

## 任务八：实现 FileTraceWriter Thread

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/persistence/trace_writer.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/persistence/test_trace_writer.py`

**开发内容**

- 标准库 `queue.Queue`；
- 独立 Writer Thread；
- `start()`；
- `offer(request)`；
- `stop(drain=True)`；
- 单 Writer 顺序追加；
- 每个 Session 单独文件；
- UTF-8 JSONL；
- 批量或间隔 flush；
- 正常关闭 flush；
- 文件权限；
- 写入失败计数；
- 单条/文件大小统计；
- 超阈值 Warning；
- Writer 错误不抛回 Agent 主流程。

**关键语义**

```text
offer 成功
→ Hook 返回
→ 后台写盘
```

**验证**

- 主线程和 Tool Thread 同时 offer；
- 多 Session 正确路由；
- 同一 Session 顺序稳定；
- Drain 完整写入；
- 不 Drain 可快速停止；
- 写入失败不阻塞调用方；
- Writer Thread 正确退出；
- 重复 start/stop 行为明确。

## 任务九：实现 FileTraceHook

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/persistence/trace_hook.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/persistence/test_trace_hook.py`

**开发内容**

- 实现 BaseHook；
- 同步/异步入口都快速入队；
- 从 HookContext 获取 Workspace/Session 路由；
- 缺少 SessionIdentity 时：
  - 不写 Session Trace；
  - 记录 Warning；
  - 不影响主流程；
- 记录 Hook 入队失败计数；
- 不逐 Delta 生成额外 Hook。

**验证**

- 同步 Hook；
- 异步 Hook；
- Agent/LLM/Tool/Plugin Runtime Hook；
- 缺少 Identity；
- Writer 停止后的 offer；
- Hook 失败不影响 Agent。

## 任务十：实现 Workspace/Session Logging

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/persistence/log_handler.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/persistence/test_log_handler.py`

**开发内容**

- ContextFilter 读取当前 SessionIdentity；
- Session 存在时写 Session `runtime.log`；
- Session 不存在时写 Workspace `runtime.log`；
- 包含：
  - 时间
  - level
  - logger
  - task_id
  - run_id
  - plugin_id（如有）
  - message
- 禁止日志递归；
- 写入失败 fallback stderr；
- 不把完整 Trace 重复写入 runtime.log；
- 不自动轮转。

**验证**

- Workspace 日志；
- Session 日志；
- 并发 Session 路由；
- 异常堆栈；
- 文件权限；
- 敏感字段由调用方避免或 Handler 进行基本过滤。

## 任务十一：组装 Persistence Runtime

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/persistence/runtime.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/__init__.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/persistence/test_persistence_runtime.py`

**开发内容**

统一组装：

- DataPathResolver；
- MetadataStore；
- Redactor；
- FileTraceWriter；
- FileTraceHook；
- Logging Handler；
- Session Scope Context Manager。

概念接口：

```python
runtime = PersistenceRuntime.from_env()
runtime.start()

with runtime.session_scope(
    workspace_path=...,
    session_id=...,
    task_id=...,
):
    ...

runtime.stop(drain=True)
```

Session Scope：

- 初始化 Workspace/Session 元数据；
- 设置可合并 HookContext；
- 配置 Logging 路由；
- 退出后恢复外层 Context。

**验证**

- 环境配置；
- Session 创建；
- 新 Session 默认 UUID；
- 显式 Session ID；
- 多 Session 连续/并发；
- 关闭 Drain；
- 重复启动/停止。

## 任务十二：Agent 与 Plugin Runtime 集成

**更新文件**

- 应用启动组装入口（如尚不存在则新增最小 runtime bootstrap）
- AgentFactory/PluginManager 的组装测试

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/persistence/test_trace_integration.py`

**开发内容**

- 注册 `FileTraceHook` 到 HookRegistry；
- ObservableAgent/LLM/Tool 使用 Session Scope；
- ObservableEventBus/PluginRuntime 使用同一 HookRegistry；
- task_id 从 Blackboard/AgentPlugin 进入 Session Scope；
- trace 包含 Agent、LLM、Tool、EventBus、Plugin 消费；
- runtime.log 包含生命周期和错误。

**验证**

- 完整技术链路产生 trace.jsonl；
- 一个 HookEvent 只写一次；
- 不逐 TextDelta 重复写入；
- Workspace/Session 路由正确；
- 失败场景仍可落盘；
- 日志写入失败不阻塞 Agent。

## 任务十三：真实模型验证

使用当前真实模型和临时 `ICARUS_DATA_DIR`：

```text
模拟 UserInput/Context
→ BlackboardPlugin
→ AgentPlugin
→ ReActAgent
→ Tool
→ EventBus
→ Hook
→ trace.jsonl/runtime.log
```

检查：

- 真实 Prompt；
- Blackboard Context；
- LLM 聚合输入输出；
- Reasoning；
- ToolCall/ToolResult；
- Usage；
- EventBus 和 Plugin 生命周期；
- task/run/event ID；
- 脱敏；
- Writer Drain。

验证结束后清理临时目录，不把日志提交到 Git。

## 任务十四：Plugin 目录迁移

在持久化功能稳定后，再迁移当前平铺插件目录：

```text
plugins/
├── agent/
├── blackboard/
├── persistence/
└── contracts/
```

计划：

- `agent_plugin.py` → `agent/plugin.py`
- `blackboard_context_converter.py` → `agent/context_converter.py`
- `blackboard_plugin.py` → `blackboard/plugin.py`
- `blackboard_state.py` → `blackboard/state.py`
- 公共 Event → `contracts/events.py`
- 更新 import 和测试镜像路径；
- 保持公共导出兼容或一次性更新所有调用方；
- 不改变业务行为。

当前迁移已经完成，源码与测试均采用镜像目录。

**验证**

- 全量 import；
- 测试目录镜像；
- PluginManager 显式注册；
- 不引入自动发现和 Manifest；
- 全量测试不回归。

## 分层验证

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/persistence -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/hooks -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test -q

apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src \
  apps/agent/test

git diff --check
```

## 风险与控制

### Hook 阻塞 Agent

- Hook 只 offer；
- queue.Queue；
- Writer Thread；
- 不等待磁盘。

### 跨线程与跨 EventLoop

- 不使用 asyncio.Queue；
- Writer 只在独立 Thread；
- Hook 同步/异步入口共享 thread-safe offer。

### Session 串日志

- SessionIdentity；
- ContextVar 合并；
- Writer 请求显式携带 Workspace/Session；
- 并发 Session 测试。

### 日志泄密

- 统一 Redactor；
- 安全文件权限；
- `.env` 禁止写入；
- 全量 Trace 风险文档化。

### 磁盘无限增长

- 初版不删除；
- 记录文件大小；
- 告警阈值；
- 后续增加轮转和清理。

### 对话恢复误用 Trace

- 不实现 messages.jsonl；
- 不提供从 Trace 生成 History 的 API；
- 文档明确 Backend DB 是唯一业务历史来源。

### 写入失败影响主流程

- Writer 捕获错误；
- failure_count；
- fallback logging；
- 不向 HookDispatcher 抛出。

## 推荐提交拆分

1. 数据目录配置与 Identity；
2. PathResolver 与 Metadata；
3. HookContext 合并；
4. Redactor 与 TraceRecord；
5. TraceWriter Thread；
6. FileTraceHook；
7. Logging Handler；
8. PersistenceRuntime 组装；
9. Agent/Plugin 集成测试；
10. 真实模型验证；
11. Plugin 目录迁移；
12. 文档同步。

## 完成标准

- 文件结构符合设计；
- Agent 主流程不等待磁盘；
- Trace 内容完整且脱敏；
- Workspace/Session 路由正确；
- HookContext 身份贯穿；
- runtime.log 人可读；
- 正常关闭 Drain；
- 文件永久保留；
- 不实现业务对话恢复；
- 不引入 SQLite；
- 现有全量测试不回归。
