# Background Process Plugin Design｜后台进程插件设计

## 文档定位

本文记录 Icarus 当前 `ProcessPlugin` 的实现设计。该 Plugin 面向 `pnpm run dev`、本地预览服务等
需要在一次 Tool Call 返回后继续运行的命令，并将进程生命周期严格限制在所属 Session 内。

本文只描述当前源码架构和已实现契约，不包含实施步骤。实施记录与验证命令单独保存在同目录的
`plan.md`。

相关文档：

- Plugin Runtime：`apps/agent/docs/spec/2026-08-23-plugin-runtime-manifest-lifecycle/arch.md`；
- Tool Execution Guard：`apps/agent/docs/spec/2026-09-20-tool-execution-guard/arch.md`；
- Session Runtime：`apps/agent/docs/spec/2026-08-29-device-agent-runtime-session/arch.md`；
- Gateway：`apps/gateway/docs/spec/2026-08-29-agent-gateway-positioning/arch.md`。

## 1. 核心结论

Icarus 增加一个 Session 级 `ProcessPlugin`，向 Agent 注册唯一的参数化 Tool：

```text
background_process
```

该 Tool 通过 `action` 承载五种操作：

```text
start | list | get | logs | stop
```

第一阶段只实现适合开发服务器的最小进程管理能力，不实现通用作业调度器。核心约束如下：

- `start` 成功表示命令已经交给操作系统、子进程已经创建且 Plugin 已建立进程记录，不表示应用
  ready、端口已监听或健康检查已通过；
- 进程状态只有 `running / completed / failed / stopped`；
- `asyncio.subprocess.Process`、PID、进程组和退出码是运行事实来源，Plugin 不通过周期性 `ps`
  扫描模拟进程状态；
- 每个进程由 Plugin 启动一个受 Runtime 管理的 Supervisor，进程退出后立即更新记录并发布事件，
  不等到下一次 `list` 才修正状态；
- 进程严格归属于创建它的 `ProcessPlugin` 实例，因此也归属于一个 Session；
- Session 关闭时先收束 Plugin 和后台进程，全部清理完成后 Session 才进入 `unloaded`；
- Agent 的所有操作都走现有 `ToolExecutor` 和 Tool Execution Guard；
- TUI、WebUI 等客户端继续只接收统一的 Gateway `runtime.update`，不新增动态 Plugin RPC 或
  `capability.call`；
- `ProcessPlugin` 按当前产品配置作为 required Plugin，不向 `SessionRuntime` 增加
  `start_process()`、`list_processes()` 等领域方法。

## 2. 目标与非目标

### 2.1 目标

- Agent 可以启动一个长期运行的本地命令，并在 Tool Call 返回后继续执行其他步骤；
- Agent 可以列出进程、查询一个进程、分页读取日志和停止进程；
- `pnpm run dev` 失败或自然退出后，状态自动变成 `failed` 或 `completed`；
- 停止作用于完整进程组，避免只结束 shell 而遗留 pnpm、Node、Vite 等子进程；
- 进程的 stdout 和 stderr 持续写入 Session 隔离的有界日志文件；
- `list` 和 `logs` 具有业务分页，最终 Tool Result 继续接受统一单结果预算、Batch 预算、裁剪和
  长文本外置；
- 进程状态通过现有 Event、RuntimeUpdate 和 Gateway WebSocket 链路提供给 TUI/WebUI；
- 正常关闭、启动失败、Tool 取消和超时都不能遗留已创建但未被 Plugin 接管的子进程。

### 2.2 非目标

第一阶段不实现：

- 端口探测、HTTP 健康检查、应用 ready 状态或启动成功日志匹配；
- 系统级守护进程、跨 Session 进程池、开机自启和进程自动重启；
- Session 重载后重新连接旧 PID，或在 AgentRuntime 崩溃后恢复进程所有权；
- Docker、systemd、launchd、Kubernetes 等外部进程管理协议；
- CPU、内存、网络、文件句柄等资源指标采集；
- 任意 PID 查询或终止；
- 信号选择、优先级、用户切换、环境变量覆盖和 shell 类型选择；
- 动态安装、热卸载或热更新 ProcessPlugin；
- 绕过 Agent Tool 直接从 Gateway 启动或停止进程；
- Windows 进程组语义。第一阶段沿用现有 Bash Tool 的 POSIX 进程模型。

## 3. 架构边界

### 3.1 总体结构

```text
ReActAgent
    │
    │ background_process(action=...)
    ▼
ToolExecutor / Tool Execution Guard
    │
    ▼
┌──────────────────────────────────────────────┐
│ ProcessPlugin                                │
│                                              │
│  BackgroundProcessTool                       │
│          │                                   │
│          ▼                                   │
│  ProcessManager                              │
│    ├─ ProcessRecord                          │
│    ├─ Process Supervisor                     │
│    ├─ process group termination              │
│    └─ bounded combined log                   │
└──────────────────────────────────────────────┘
    │ ProcessUpdatedEvent
    ▼
RuntimeUpdatePlugin
    │ process.updated
    ▼
AgentRuntime → Gateway → runtime.update → TUI / WebUI
```

`ProcessPlugin` 是唯一注册到 Plugin Runtime 的新 Plugin。Tool、Manager、Supervisor、Record 和
日志读写器均为 Plugin 内部普通组件，不注册成嵌套 Plugin。

### 3.2 分层职责

| 组件 | 职责 | 明确不负责 |
|---|---|---|
| `BackgroundProcessTool` | 参数校验、action 分发、形成 `ToolExecutionResult` | 执行进程、裁剪最终 Tool Result |
| `ProcessPlugin` | Plugin 生命周期、事件发布、组合内部组件 | Gateway 协议、应用 ready 判断 |
| `ProcessManager` | 创建记录、串行化状态变更、查询和停止 | 模型可见结果预算 |
| Process Supervisor | 读取输出、等待退出、自动推导终态 | 周期性扫描系统进程表 |
| 进程日志 | 保存有界 stdout/stderr 原始内容并支持分页 | 充当 Session 对话历史 |
| `RuntimeUpdatePlugin` | 将 `ProcessUpdatedEvent` 投影为稳定 `process.updated` | 管理进程 |
| `ToolExecutor` | timeout、Batch 上限、结果预算、裁剪和长结果外置 | 理解 Process action 和日志游标 |
| Gateway | 转发统一 `runtime.update`、提供现有历史读取 | 直接调用 ProcessPlugin |

### 3.3 与 SessionRuntime 的关系

`ProcessPlugin` 加入当前 Session 的 required Plugin 集合。缺失、Manifest 无效、依赖不满足或启动
失败时，Session 启动失败，不以缺少后台进程能力的方式降级运行。

required 只表示当前产品组装要求，不把进程领域接口焊接到应用层：

- `SessionRuntime` 只通过现有 `PluginRuntimeHost` 发现和启动 ProcessPlugin；
- `SessionRuntime` 不持有 `ProcessPlugin` 类型，也不增加进程领域方法；
- ProcessPlugin 不提供 `process_management` Capability；
- Agent 仅通过注册的 `background_process` Tool 使用该能力。

如果未来移除 ProcessPlugin，需要进行一次协调式代码变更：同时从 required Plugin 配置和
`RuntimeUpdatePlugin.consumed_events` 中移除它。当前 Plugin Runtime 不支持在正在运行的 Session
中热拔除 Plugin。

## 4. Plugin Manifest 与装配

内置 Plugin 目录使用：

```text
apps/agent/src/agent_orchestration/plugins/process/
├── __init__.py
├── config.py
├── cursor.py
├── events.py
├── factory.py
├── manifest.json
├── manager.py
├── models.py
├── plugin.py
└── tools.py
```

这些文件按配置、Cursor、状态、进程管理、生命周期和 Tool 职责拆分，但没有形成嵌套 Plugin。
Manifest 为：

```json
{
  "schema_version": 1,
  "plugin_id": "process",
  "plugin_version": "1.0.0",
  "entrypoint": "apps.agent.src.agent_orchestration.plugins.process.factory:create_plugin",
  "python_requires": [],
  "required_capabilities": [
    {"plugin_id": "persistence", "capability_id": "runtime", "version_spec": ">=1,<2"},
    {"plugin_id": "persistence", "capability_id": "session", "version_spec": ">=1,<2"}
  ],
  "provided_capabilities": [],
  "provided_tools": ["background_process"],
  "published_events": [
    "apps.agent.src.agent_orchestration.plugins.process.events.ProcessUpdatedEvent"
  ],
  "consumed_events": [],
  "state_scopes": []
}
```

日志目录由 `persistence/runtime` 的 `DataPathResolver` 解析，Session 身份来自
`persistence/session`。Plugin 不自行拼接不受校验的 Workspace 或 Session 路径。

## 5. Tool 契约

### 5.1 单 Tool 与扁平参数

工具定义：

```text
background_process(
    action,
    command?,
    workdir?,
    process_id?,
    cursor?,
    page_size?,
    limit_bytes?
)
```

参数保持扁平。Tool Schema 声明全部可用字段，具体 action 的必需字段和互斥字段由 Tool 做二次
校验。框架仍会自动注入并剥离 `_execution`，业务 Tool 不把它当作领域参数。

| action | 必需参数 | 可选参数 | 语义 |
|---|---|---|---|
| `start` | `command` | `workdir` | 创建进程、记录和 Supervisor 后立即返回 |
| `list` | 无 | `cursor`、`page_size` | 分页返回本 Session 当前保留的进程记录 |
| `get` | `process_id` | 无 | 返回指定进程的最新完整记录 |
| `logs` | `process_id` | `cursor`、`limit_bytes` | 从日志游标继续读取一个有界分片 |
| `stop` | `process_id` | 无 | TERM 进程组，宽限期后 KILL，并返回最终记录 |

不属于目标 action 的字段视为参数错误，未知字段同样拒绝。`process_id` 是 Plugin 生成的不可预测
标识；PID 只用于展示和诊断，不能作为 `get/logs/stop` 的输入。

### 5.2 `start` 语义

`start` 使用 `bash -lc <command>`，工作目录缺省为当前 Workspace。成功边界固定为：

```text
校验参数与资源上限
→ 创建有界日志文件
→ create_subprocess_exec(..., start_new_session=True)
→ 保存 Process 对象、PID 和进程组信息
→ 注册受 Plugin Runtime 管理的 Supervisor，并立即开始排空 stdout pipe
→ 发布 status=running 的 ProcessUpdatedEvent
→ 释放 Supervisor 提交门闩
→ 返回 success=true
```

这里不等待应用 ready。即使 shell 随后立刻以 `127` 或其他非零码退出，`start` 调用仍然表示
“进程创建成功”；Supervisor 会立即把记录更新为 `failed`。如果退出发生得极快，`start` 返回的记录
可以已经是终态，但 `ToolExecutionResult.success` 仍为 `true`。

以下情况才是 `start` 失败：参数非法、Plugin 已进入 quiescing、并发数已满、工作目录非法、日志文件
无法创建或操作系统未能创建子进程。首次 `running` Event 成功发布并释放提交门闩前属于启动事务；
这段时间发生取消、超时、Supervisor 注册失败或首次 Event 发布失败时，即使子进程已经产生，也必须
先终止进程组并把记录收束为终态。提交门闩释放后，进程所有权稳定转移给 ProcessPlugin；后续 Agent
Task 结束或取消不影响该进程。极端情况下调用方可能在门闩释放后、收到 Tool Result 前断开，但仍可
通过已经发布的 `process.updated` 获得 `process_id`。

Supervisor 在提交门闩释放前可以排空 stdout/stderr，避免首次 Event 发布受阻时子进程因 pipe 填满而
阻塞，但不得发布日志截断或终态。即使命令立即退出，EventBus 中也必须先出现 `status=running`，再
出现 `completed/failed`，不能因调度竞态发生倒序。

### 5.3 返回结构

`start/get/stop` 返回同一记录投影：

```json
{
  "process_id": "proc_f8b4...",
  "pid": 42017,
  "command": "pnpm run dev",
  "workdir": "/workspace/apps/web",
  "status": "running",
  "started_at": "2026-09-22T10:00:00Z",
  "ended_at": null,
  "exit_code": null,
  "stop_reason": null,
  "log_path": ".../sessions/<session-id>/processes/proc_f8b4....log",
  "log_truncated": false
}
```

`list` 返回：

```json
{
  "processes": [],
  "page_size": 50,
  "has_more": false,
  "next_cursor": null
}
```

记录按 `started_at` 降序、`process_id` 升序形成确定顺序。列表是动态数据，使用包含排序锚点的
opaque Cursor，不把 PID、数组下标或可由调用方篡改的裸偏移作为 Cursor。默认 `page_size=50`，
最大 `200`。新记录插入列表头部时，已有 Cursor 继续从锚点之后读取，避免重复返回已看过的记录。
如果 Cursor 版本、Session 绑定或排序锚点无效，包括锚点已被终态记录裁剪，则明确返回
`invalid_arguments`，不静默回到第一页。

`logs` 返回：

```json
{
  "process_id": "proc_f8b4...",
  "content": "...",
  "has_more": false,
  "next_cursor": "opaque-log-cursor",
  "log_path": ".../proc_f8b4....log",
  "log_truncated": false
}
```

日志是只追加数据，Cursor 绑定 `process_id`、日志代次和安全字节位置。首次无 Cursor 时从已保留日志
开头读取；`next_cursor` 始终指向本次读取后的安全位置，即使当前 `has_more=false`，调用方以后仍可用
它读取新增内容。`limit_bytes` 使用内部默认值和硬上限，返回边界不得切断 UTF-8 字符。

### 5.4 并发语义

第一阶段所有 action 的 `can_run_parallel()` 均返回 `false`。ProcessManager 使用异步锁保护记录和状态
转换，避免 `start/list/stop` 与 Supervisor 退出回调形成竞态。后续只有在证明只读并发确有收益且不会
破坏日志 Cursor、终态读取和 Tool Call 顺序时，才开放并行查询。

### 5.5 同步与异步入口

`ainvoke()` 是当前 SessionRuntime 生产链路的原生入口。`invoke()` 与 `ainvoke()` 必须复用同一套
action 校验、ProcessManager 和结果投影；同步入口通过线程安全桥接把操作提交到 ProcessPlugin 所属的
Session Event Loop，不创建第二套 ProcessManager，也不围绕已绑定 Event Loop 的 Process 对象调用
`asyncio.run()`。两条入口对同一 action 返回相同字段和错误语义，并分别纳入测试。

## 6. 状态模型

### 6.1 ProcessRecord

Plugin 在内存中维护最小记录：

| 字段 | 用途 |
|---|---|
| `process_id` | Session 内稳定业务标识 |
| `process` | `asyncio.subprocess.Process` 句柄，运行事实来源 |
| `pid` / `process_group_id` | 诊断和整组信号 |
| `command` / `workdir` | 查询与展示 |
| `started_at` / `ended_at` | 排序和生命周期展示 |
| `stop_requested` / `stop_reason` | 区分主动停止和自然退出 |
| `exit_code` | 操作系统返回码 |
| `log_path` / `log_truncated` | 日志读取与容量状态 |
| `origin_task_id` | 诊断启动来源，不作为进程生命周期父对象 |

ProcessRecord 不保存模拟的心跳或健康值。Plugin 同时跟踪 `asyncio.subprocess.Process` 和创建时的
进程组：leader 尚未退出，或 leader 已退出但已知进程组仍然存在，都属于 `running`。只有 leader 已被
回收且进程组确认不存在后才进入终态。PID 可能被操作系统复用，因此不能在丢失 Process 对象后仅凭
PID 宣称旧进程仍受 Plugin 管理。

### 6.2 状态推导

| 条件 | 状态 |
|---|---|
| leader 尚未退出，或已知进程组仍存在 | `running` |
| 未请求停止，leader 已回收、进程组不存在且 `exit_code == 0` | `completed` |
| 未请求停止，leader 已回收、进程组不存在且 `exit_code != 0` | `failed` |
| 已记录 `stop_requested`，且进程组已确认不存在 | `stopped` |

状态转换只有：

```text
running ── exit 0 ───────────────▶ completed
running ── exit non-zero ────────▶ failed
running ── stop/session closing ─▶ stopped
```

终态不可逆。若 Supervisor 已先观察到自然退出，随后到达的 `stop` 只返回已有终态，不把
`completed/failed` 重写为 `stopped`。重复 `stop` 是幂等查询。

### 6.3 自动更新

每次成功启动都注册一个 Supervisor 作为 `BasePlugin.start_background_work()` 的后台工作。子进程配置
`stdout=PIPE`、`stderr=STDOUT`；提交门闩释放后，Supervisor 持续读取合并输出并写入日志，同时观察
leader 的 `returncode` 和已知进程组。不能只等待 `Process.wait()`：leader 退出后，仍存活的 child 可能
继承 stdout pipe，使 asyncio 延后完成该等待。Supervisor 因此以有界退避观察 `returncode`，并针对
Plugin 已知 PGID 使用 `killpg(pgid, 0)` 检查组是否仍存在，但不枚举或扫描系统进程表。完整进程组
退出一旦被观察到，立即：

1. 保存 `exit_code` 和 `ended_at`；
2. 按固定规则推导终态；
3. 完成日志 flush 和关闭；
4. 发布完整的 `ProcessUpdatedEvent`；
5. 结束对应后台工作。

因此 `list/get` 只读取已经维护好的快照，不在查询时执行 `ps` 扫描。Supervisor 存活期间计入现有
`background_work_count`，Session 因而保持 `running`，不会被 idle cleanup 当成空闲 Session 卸载。

## 7. Event、RuntimeUpdate 与 Gateway

### 7.1 单一状态事件

只定义一个领域事件 `ProcessUpdatedEvent`，不拆分 `started/exited/failed` 事件。事件携带完整当前
快照，其中 `status` 显式表示 `running/completed/failed/stopped`：

```text
ProcessUpdatedEvent
  process_id
  pid
  command
  workdir
  status
  started_at
  ended_at
  exit_code
  stop_reason
  log_truncated
  origin_task_id
```

Event 的继承字段 `task_id` 固定为 `None`。后台进程可能长于创建它的 Agent Task，不能让后续退出
更新错误地改变原 Task 的状态或活动时间；原始 Task 关系只放在 `origin_task_id` 中。

发布时机包括：

- 进程完成登记，发布 `status=running`；
- 自然退出，发布 `status=completed` 或 `status=failed`；
- Tool 或 Session 请求停止并确认退出，发布 `status=stopped`；
- 日志首次达到上限时，可发布一次状态不变但 `log_truncated=true` 的更新。

### 7.2 RuntimeUpdate 投影

`RuntimeUpdatePlugin` 按当前其他 Plugin 的方式，在 Manifest 中静态消费
`ProcessUpdatedEvent`，并投影为：

```json
{
  "type": "process.updated",
  "task_id": null,
  "payload": {
    "process_id": "proc_f8b4...",
    "pid": 42017,
    "command": "pnpm run dev",
    "workdir": "/workspace/apps/web",
    "status": "running",
    "started_at": "2026-09-22T10:00:00Z",
    "ended_at": null,
    "exit_code": null,
    "stop_reason": null,
    "log_truncated": false
  }
}
```

投影前使用 RuntimeUpdatePlugin 已持有的 Redactor 处理 `command`、`workdir` 和 `stop_reason`。原始
日志内容和本地日志路径不进入 RuntimeUpdate，避免高频、大文本或本地路径通过网络广播。

应用层 `RuntimeUpdateType` 增加 `process.updated`。Gateway Wire Model 的 `type` 已是字符串，Gateway
继续统一转发 `runtime.update`，不新增 `process.*` RPC、动态 Plugin RPC 或通用 Capability RPC。

### 7.3 快照与增量的边界

- `background_process(action=list|get)` 是 Agent 读取当前 ProcessPlugin 内存快照的真相来源；
- `process.updated` 是 TUI/WebUI 的增量状态来源；
- 同一 AgentRuntime 内网络重连时，客户端可使用现有 `session.get_history` 补齐已持久化更新，再按
  `process_id` 取最后一条；
- Session 正常 unload 时会先得到各进程的 `stopped`，再得到 Session unload 生命周期；
- AgentRuntime 非正常崩溃不保证产生终态更新，也不保证操作系统中不存在逃逸进程。重启后旧记录只
  能视为历史证据，不能凭旧 PID 恢复管理关系或宣称仍在运行。

第一阶段接受最后一项限制，不为异常崩溃引入跨进程守护服务或通用 Gateway 查询协议。

## 8. 日志设计

### 8.1 保存位置和内容

每个进程使用一个 Session 隔离日志文件：

```text
<ICARUS_DATA_DIR>/workspaces/<workspace_key>/sessions/<session_id>/processes/
└── <process_id>.log
```

目录权限使用 `0700`，日志文件使用 `0600`。stdout 和 stderr 在创建子进程时合并到同一输出目标，
尽量保留操作系统观察到的输出顺序，并避免两个无时间戳流在读取阶段伪造顺序。

进程结束或 Plugin 停止后保留日志文件用于诊断；本功能不新增独立清理周期，日志最终跟随既有 Session
数据清理策略。

### 8.2 容量控制

单进程日志设置固定硬上限，默认 `16 MiB`。达到上限后：

- 保留已写入内容；
- 停止继续写入并持续排空子进程 pipe，避免子进程因 pipe 填满而阻塞；
- 将 `log_truncated` 置为 `true`；
- 不因日志过多自动杀死仍正常工作的开发服务器。

Plugin 同时限制每个 Session 的运行中进程数量，默认最多 `8` 个。终态记录只在内存保留最近
`200` 个，运行中的记录永不因该上限被裁掉。三个上限由受控 Plugin 配置提供，必须是正整数，并
保持保守的默认值。

### 8.3 与 Tool Result 外置的关系

进程日志文件是 ProcessPlugin 的业务数据，不替代 Tool Execution Guard：

- `logs` 先按自己的 Cursor 和 `limit_bytes` 读取一个业务页；
- 形成的 `ToolExecutionResult` 仍由 ToolExecutor 执行 Token 计量；
- 如果这一页仍超过单结果或 Batch 预算，ToolExecutor 按统一规则生成 Preview，并把预算处理前的
  Tool Result 写入 `tool-results/<task_id>/`；
- ProcessPlugin 不重复实现模型上下文裁剪、Head/Tail Preview 或 Batch water-filling。

因此业务日志路径与 Tool Result 外置路径可能同时出现，两者用途不同：前者是持续追加的进程输出，
后者是某次 Tool Call 的完整结果快照。

## 9. 生命周期与关闭顺序

### 9.1 所有权

```text
AgentRuntime
└── SessionRuntime
    └── PluginRuntimeHost
        └── ProcessPlugin
            └── process groups + supervisors + log handles
```

后台进程的生命期不得超过 ProcessPlugin；ProcessPlugin 的生命期不得超过 SessionRuntime。Tool Call
结束或发起它的 Agent Task 结束，不会自动停止已经成功登记的后台进程。

### 9.2 统一关闭协议

ProcessPlugin 复用 `start → quiesce → drain → stop`，不建立第二套 shutdown API：

1. `quiesce()` 原子地拒绝新的 `start`，仍允许 `list/get/logs/stop` 完成清理；随后对所有 running
   进程组记录 `stop_requested` 和 `session_shutdown` 并唤醒 Supervisor，但不等待终止完成。
2. `drain()` 等待尚在 spawn 的启动事务完成提交或回滚，并等待进程 Supervisor 执行 SIGTERM、在
   宽限期后升级 SIGKILL、更新退出状态、flush 日志和发布事件。Supervisor 独立于发起 stop 的 Tool
   Call；该调用被取消或 timeout 后，已经开始的 TERM → KILL 收束仍继续完成。
3. `stop()` 作为幂等最终安全网，对任何残留进程组发送 `SIGKILL`、等待回收子进程、关闭日志句柄并
   释放内存引用。
4. Plugin Runtime 完成反序停止后，`SessionRuntime.stop()` 才把 Session 标记为 closed；
   `AgentRuntime` 最后发布 `session.lifecycle=unloaded`。

EventBus 在 Plugin drain 完成后才停止，因此 shutdown 产生的 `process.updated(status=stopped)` 可以沿
现有链路发送和持久化。`stop()` 即使在部分启动失败、重复调用或前序 drain 超时后也必须安全。

### 9.3 进程组规则

子进程使用 `start_new_session=True`，Plugin 保存进程组标识并对整个组发送信号。Supervisor 观察到
leader 已退出后仍要检查进程组；若仍有后代存活，则进行收束后再把记录设为终态。主动 daemonize、
double-fork 或脱离原进程组不属于支持范围。

## 10. Tool Execution Guard 适配

`background_process` 是普通 Plugin Tool，必须通过已有 ToolRegistry 和 ToolExecutor 注册、冻结和执行。
没有任何 action 可以跳过 Guard。

| Guard 能力 | ProcessPlugin 行为 |
|---|---|
| `_execution.timeout_seconds` | 限制一次 start/list/get/logs/stop Tool Call；不限制已登记后台进程寿命 |
| 单结果预算 | 作用于 Process Tool 的结构化返回和日志页 |
| Batch 预算 | 与同一步其他 Tool Result 一起计算 |
| Batch 调用数 | 继续受每组最多 8 次调用限制 |
| 长结果外置 | 由 ToolExecutor 写入 Session `tool-results` 并返回路径 |
| 分页 | `list` 和 `logs` 在 Plugin 内实现，最终结果仍经过预算 |
| 取消 | Tool 尚未完成进程登记时清理已创建进程；登记并成功返回后由 Plugin 生命周期管理 |

`start` 的 Tool timeout 与进程寿命完全分离。例如默认 Tool timeout 为 120 秒，但成功启动的
`pnpm run dev` 可以持续运行到显式 `stop` 或 Session 关闭。

## 11. 精简安全控制

该能力本质上允许 Agent 以 Icarus 进程的操作系统权限执行 shell 命令。第一阶段不把 Tool Execution
Guard 描述成操作系统沙箱，也不声称能识别命令业务风险。最小控制为：

- 所有调用必须经过 ToolExecutor，不能暴露绕过 Guard 的 Gateway 启停入口；
- `command` 必须是非空字符串，UTF-8 编码后不得超过固定的 `32 KiB`；
- `workdir` 缺省为 Workspace，显式路径必须 resolve 后位于当前 Workspace 内且是已存在目录；
- 不接受调用方提供 PID、进程组 ID、日志路径、用户、环境变量、shell 或任意 signal；
- 只能用 Plugin 生成且属于当前 Session 的 `process_id` 查询和停止进程；
- 限制运行中进程数、单进程日志大小、列表页和日志页大小；
- 进程组 TERM/KILL、Tool 取消清理和 Plugin 幂等 stop 共同防止正常路径遗留子进程；
- RuntimeUpdate 中的命令和路径经过 Redactor，日志正文不广播；
- 进程继承 Icarus 的宿主环境和权限，不额外提升权限。

当前若存在更高层 Tool 审批或调用策略，`background_process` 与其他 Plugin Tool 一样自然受其约束；
本功能不新增独立审批协议或重复实现 Tool Guard。

## 12. 错误处理

Tool 使用稳定错误类别形成失败结果，不把内部 traceback、系统环境变量或未脱敏日志返回给模型：

| 错误类别 | 场景 | 结果 |
|---|---|---|
| `invalid_arguments` | action 字段错误、字段缺失或路径越界 | `success=false`，不产生副作用 |
| `invalid_cursor` | Cursor 无效、绑定不匹配、偏移越界或锚点已裁剪 | `success=false`，不产生副作用 |
| `process_limit_reached` | 已达到 Session 并发上限 | `success=false` |
| `process_not_found` | process_id 不属于当前 Plugin | `success=false` |
| `spawn_failed` | 日志或子进程创建失败 | 清理部分资源后 `success=false` |
| `tool_unavailable` | Plugin 未启动、非 owner loop 调用或 shutdown 期间请求 start | `success=false` |
| `log_unavailable` | 日志缺失、不可读或 Cursor 不匹配 | `success=false`，进程状态不改变 |
| `termination_failed` | TERM/KILL 后仍无法确认进程组退出 | `success=false` 并由 Plugin stop 再次清理 |

`stop` 与自然退出竞争时，以持锁后观察到的 OS 状态决定：已经终态则幂等返回；仍在运行则先记录
`stop_requested` 再发信号，从而不会把已完成进程错误标为主动停止。

终态事件发布失败不能撤销已经发生的 OS 事实。Plugin 保留更新后的 ProcessRecord 并记录错误；Session
关闭路径仍继续终止进程。首次 `running` Event 发布失败属于尚未提交的启动事务，按 `start` 失败执行
进程组清理。RuntimeUpdate 持久化失败沿用当前 AgentRuntime 的 fail-closed 行为，不在 ProcessPlugin
内另建补偿队列。

## 13. 配置默认值

ProcessPlugin 第一阶段只保留少量资源配置：

| 配置 | 默认值 | 说明 |
|---|---:|---|
| `max_running_processes` | 8 | 单 Session 同时 running 的进程组上限 |
| `max_terminal_records` | 200 | 内存中保留的最近终态记录数 |
| `max_log_bytes` | 16 MiB | 单进程合并日志硬上限 |
| `terminate_grace_seconds` | 3 秒 | TERM 到 KILL 的宽限期 |
| `default_page_size` | 50 | `list` 默认页大小，复用 Tool Execution Settings |
| `max_page_size` | 200 | `list` 最大页大小，复用 Tool Execution Settings |
| `default_log_page_bytes` | 32 KiB | `logs` 默认读取量 |
| `max_log_page_bytes` | 256 KiB | `logs` 业务分页硬上限，仍受 Token Budget 二次约束 |

这些设置通过受控 `runtime.plugin_config.process` 注入并在 Factory 中校验。不允许 Agent 在 Tool 参数
里临时提高并发数、日志总量或终止宽限期。

## 14. 测试与验收

### 14.1 模型与状态测试

- `running → completed/failed/stopped` 的全部合法转换；
- 终态不可逆、重复 stop 幂等、自然退出与 stop 竞争；
- PID 仅展示，错误或其他 Session 的 process_id 不能操作；
- 终态记录裁剪不删除 running 记录。

### 14.2 Tool 测试

- 单一 `background_process` Tool 的五个 action 和 action-specific 参数校验；
- `start` 返回不等待长期进程退出，快速失败进程仍自动更新；
- `list` 默认 50、最大 200、稳定排序、动态 opaque Cursor、空页和非法 Cursor；
- `logs` 首次读取、继续读取、新增内容、UTF-8 边界、空日志、终态日志和容量截断；
- 所有 action 默认不可并行。

### 14.3 生命周期与操作系统测试

- Session A 与 Session B 的进程和日志完全隔离；
- `pnpm/node` 类父子进程在 stop 和 Session unload 后整组退出；
- TERM 正常退出、忽略 TERM 后 KILL、leader 先退出但组内仍有子进程；
- start 期间取消、日志创建失败、子进程创建失败和 Plugin 启动失败不遗留进程；
- `quiesce → drain → stop` 顺序正确，stop 可重复执行；
- Supervisor 计入 `background_work_count`，运行中 Session 不被 idle cleanup；
- Session unload 的最后一个 process 终态更新早于 `session.lifecycle=unloaded`。

### 14.4 Event、Gateway 与 Guard 测试

- ProcessPlugin Manifest 发布 Event，RuntimeUpdatePlugin Manifest 静态消费；
- start、自然退出、失败、stop 和日志截断投影为 `process.updated`；
- `status=running` 明确出现在首次更新中，退出更新携带准确 `exit_code`；
- 命令与路径经过 Redactor，日志正文和 `log_path` 不进入 RuntimeUpdate；
- Gateway 不新增 RPC，现有 `runtime.update` 和 `session.get_history` 可承载 `process.updated`；
- `background_process` 经过 `_execution` timeout、单结果预算、Batch 调用数和 Batch Token 预算；
- 超大日志页触发统一 Tool Result Preview 与 Session 文件外置，返回路径有效；
- Tool timeout 只结束当前调用，不结束已经成功登记的后台进程。

### 14.5 验收场景

```text
Agent 调用 background_process(action=start, command="pnpm run dev")
→ Tool 快速返回 process_id、pid、status=running
→ TUI/WebUI 收到 process.updated(status=running)
→ Agent 继续执行其他 Tool
→ Agent 用 logs Cursor 查看启动输出
→ dev server 非零退出
→ Supervisor 自动记录 exit_code
→ list/get 自动显示 status=failed
→ TUI/WebUI 收到 process.updated(status=failed)
```

另一个必测场景：

```text
dev server 保持运行
→ Session 开始 unload
→ ProcessPlugin.quiesce 拒绝新 start，并 TERM 全部进程组
→ drain 等待退出、日志和 stopped 更新
→ stop KILL 任何残留
→ Session 最后进入 unloaded
→ 系统中不存在由该 Session 正常路径遗留的进程组
```

## 15. 风险与取舍

| 风险或取舍 | 影响 | 处理 |
|---|---|---|
| `start` 不代表应用 ready | Agent 可能过早访问服务 | 返回语义和描述明确；Agent 通过 logs 或现有网络工具判断 |
| 任意 shell 命令权限较高 | 可执行宿主用户允许的副作用 | 复用 Tool Guard、限制工作目录和管理入口；不声称是沙箱 |
| RuntimeUpdatePlugin 静态消费 Process Event | ProcessPlugin 不能单独热移除 | 当前 ProcessPlugin 为 required；未来移除时协调修改 Manifest |
| 日志达到上限后丢弃后续内容 | 长期服务的最新错误可能不可见 | 返回 `log_truncated`；用保守上限控制磁盘，不自动杀服务 |
| AgentRuntime 非正常退出 | 无法保证终态事件和进程清理 | 首期只保证正常 lifecycle；跨进程守护和恢复不在范围 |
| POSIX 进程组不覆盖 double-fork | 自行 daemonize 的后代可能脱离管理 | 明确不支持 daemonize；面向前台 dev server 命令 |
| 状态更新进入 Session History | 历史记录增加 | 每个进程只在关键状态变化时发事件，不发送心跳或日志块 |

## 16. 采用与不采用的方案

采用当前其他 Plugin 的既有模式：

```text
Plugin Tool + EventBus + RuntimeUpdatePlugin + Gateway runtime.update
```

不采用以下方案：

- 多个独立 `process_start/process_list/process_stop` Tool：增加模型工具面且没有必要；
- `SessionRuntime.process_xxx`：把具体 Plugin 领域能力硬编码进应用核心；
- `process_management` 或 `runtime_update_publisher` Capability：当前没有必要的 Plugin 间调用方；
- `capability.call` 或动态 Plugin Gateway RPC：偏离现有显式 Gateway 协议且扩大首期范围；
- 每次 list 扫描 `ps`：无法可靠判断 Session 所有权，并受 PID 复用影响；
- 独立 daemon：破坏 Session 所有权，增加跨进程恢复和权限边界。
