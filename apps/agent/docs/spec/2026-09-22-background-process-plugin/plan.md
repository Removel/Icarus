# Background Process Plugin Development Plan｜后台进程插件实施计划

## 目标

基于同目录的 `design.md`，为 Icarus Agent 增加 Session 级 `ProcessPlugin`。Agent 通过唯一的
`background_process` Tool 启动、列出、查询、读取日志和停止长期运行命令；进程状态通过现有
`ProcessUpdatedEvent → RuntimeUpdatePlugin → runtime.update` 链路到达 Gateway 客户端。

第一阶段交付以下固定能力：

```text
background_process(action=start|list|get|logs|stop)
```

`start` 只承诺操作系统进程已创建并被 Plugin 接管，不承诺应用 ready。后台进程一直归属于创建它的
ProcessPlugin 和 Session，并在 Session 正常退出时完成进程组收束。

## 当前状态

- 任务一至七均已实现：Process 路径与配置、状态与 Cursor、Manager/Supervisor、Plugin/Tool、
  RuntimeUpdate、Session required 装配、Tool Guard 和 Gateway 通路均已落地；
- `AgentRuntime.unload_session()` 已将显式 unload 的 Agent Task 准入与 idle cleanup 的完整
  `snapshot.has_work` 判定分开；仅有 Plugin 后台工作时可以进入统一 shutdown，自动清理仍不会卸载；
- Process 状态经 `ProcessUpdatedEvent → process.updated → runtime.update` 发布并持久化；
- 实现没有增加 Process Capability、Gateway RPC、SessionRuntime 领域方法或 ReActAgent 依赖；
- Process 定向测试 `51 passed`、Agent 全量测试 `678 passed`、Gateway 全量测试 `14 passed`；
  Agent/Gateway compileall 与 `git diff --check` 也已通过。

## 实施原则

- ProcessPlugin 是当前默认 required Plugin，但不向 `SessionRuntime` 增加进程领域方法；
- 只注册一个 Tool，所有 action 都经过既有 ToolExecutor 和 Tool Guard；
- `ProcessManager` 以 `asyncio.subprocess.Process`、已知 PGID 和退出码为事实来源，不扫描系统进程表；
- PID 只展示，不作为管理输入；所有操作使用 Session 内生成的 `process_id`；
- 子进程使用 `bash -lc` 和新进程组，停止及 shutdown 对整组 TERM，宽限期后 KILL；
- 进程 Supervisor 必须使用 Plugin Runtime 的后台工作登记，不能创建不受管理的 fire-and-forget Task；
- stdout/stderr 合并写入 Session 下的有界日志，业务分页和 Tool Result 预算分别负责不同层次；
- List/Log Cursor 使用 Plugin 实例级随机密钥签名，保证 opaque Cursor 被修改后稳定失败；
- ProcessPlugin 发布单一 `ProcessUpdatedEvent`，状态通过完整 payload 表达；
- Gateway 不增加 Process 专用 RPC，不增加通用 `capability.call`；
- 不实现健康检查、自动重启、跨 Session 恢复、任意 PID 管理或系统级守护；
- 测试优先使用短生命周期的 Python/Bash 子进程，不依赖本机安装 pnpm。

## 交付顺序

```text
配置与安全路径
  ↓
Process 模型、Cursor 与状态纯逻辑
  ↓
进程创建、日志和 Supervisor
  ↓
Plugin 生命周期与单一 Tool
  ↓
RuntimeUpdate 与 Gateway 通路
  ↓
Session required 装配
  ↓
进程组/关闭/Tool Guard 全链路验证
  ↓
架构文档收口
```

每个任务先增加对应失败测试，再完成最小实现并运行该任务的定向测试。不得为测试便利绕过
Plugin Runtime 或直接修改 ReActAgent。

## 任务一：增加 Process 配置和 Session 日志路径

### 新增文件

- `apps/agent/src/agent_orchestration/plugins/process/__init__.py`
- `apps/agent/src/agent_orchestration/plugins/process/config.py`
- `apps/agent/test/agent_orchestration/plugins/process/__init__.py`
- `apps/agent/test/agent_orchestration/plugins/process/test_config.py`

### 更新文件

- `apps/agent/src/agent_orchestration/plugins/persistence/path_resolver.py`
- `apps/agent/test/agent_orchestration/plugins/persistence/test_path_metadata.py`

### 开发内容

1. Process 专用设置继续放在 `runtime.plugin_config.process`，不新增顶层产品配置模型。新增一个 Plugin
   内部不可变配置模型，负责默认值和严格校验，Factory 后续只消费校验后的配置：
   - `max_running_processes=8`；
   - `max_terminal_records=200`；
   - `max_log_bytes=16 * 1024 * 1024`；
   - `terminate_grace_seconds=3.0`；
   - `default_log_page_bytes=32 * 1024`；
   - `max_log_page_bytes=256 * 1024`。
2. `default_page_size` 和 `max_page_size` 不在 JSON 中重复配置，由 SessionRuntime 从现有
   `agent.tool_execution` 注入 ProcessPlugin。
3. 为 `DataPathResolver` 增加 `processes_dir(identity)` 和 `process_log(identity, process_id)`：
   - 固定位于 `session_dir(identity) / "processes"`；
   - 复用已有 Session ID 校验；
   - `ensure_session()` 同时创建该目录，权限保持 `0700`。
4. `process_log()` 复用安全 ID 约束；日志文件名只接受 Plugin 生成并验证过的 `process_id`，文件权限
   在 ProcessManager 中以 exclusive create 和 `0600` 创建；支持时启用 `O_NOFOLLOW`，发生极小概率 ID
   冲突时更换 ID 而不是覆盖旧文件。PathResolver 不接受模型传入的日志路径。

### 定向测试

- 旧配置省略 `plugin_config.process` 时，Process 配置解析仍使用内置默认值；
- Plugin 配置中的未知字段、零、负数、布尔伪装整数、非有限宽限期和默认值大于最大值均被拒绝；
- Processes 目录位于正确 Session 下并为 `0700`；
- 不安全 Session ID 或 process ID 不能逃逸数据目录；
- 原有 Asset、Trace、Session Log 和 Tool Result 路径不回归。

## 任务二：实现 Process 模型、状态转换和 Cursor

### 新增文件

- `apps/agent/src/agent_orchestration/plugins/process/models.py`
- `apps/agent/src/agent_orchestration/plugins/process/cursor.py`
- `apps/agent/test/agent_orchestration/plugins/process/test_models.py`
- `apps/agent/test/agent_orchestration/plugins/process/test_cursor.py`

### 开发内容

1. 定义第一阶段最小类型：
   - `ProcessStatus = running | completed | failed | stopped`；
   - `ProcessRecord`，保存 Process 句柄、PID/PGID、命令、工作目录、时间、退出码、停止请求、日志信息
     和原始 Task ID；
   - 面向 Tool/Event 的不可变 `ProcessSnapshot`，不得暴露 Process 对象和内部锁。
2. 把状态推导集中为纯逻辑：
   - leader 未退出或已知进程组仍存在为 `running`；
   - 未请求停止且完整进程组退出，leader code 0 为 `completed`，非零为 `failed`；
   - 已记录停止请求且完整进程组退出为 `stopped`；
   - 终态不可逆。
3. `process_id` 使用不可预测随机 ID，并通过固定前缀和安全字符校验；不从 PID、命令、Task ID 或数组
   序号推导。
4. 实现版本化 opaque Cursor codec：
   - List Cursor 绑定 Session、`started_at` 和 `process_id` 排序锚点；
   - Log Cursor 绑定 Session、`process_id`、日志代次和字节位置；
   - Factory 为每个 ProcessPlugin 实例生成独立的 256-bit 随机签名密钥，只保存在内存中；
   - Cursor 使用 canonical JSON payload 加 HMAC-SHA256 签名，再做 URL-safe 编码；解码时先用
     `hmac.compare_digest()` 验签，再验证完整结构、版本、范围和绑定关系；
   - Cursor 只用于不可信输入封装，不包含 Secret，不作为授权凭据；
   - Session unload/reload 后旧 Cursor 失效；这与 ProcessRecord 不跨 Plugin 实例恢复的边界一致；
   - 锚点已裁剪、日志代次错误或字节位置超过文件当前范围时返回稳定参数错误。
5. 快照序列化保持 JSON 兼容，时间统一输出 UTC ISO 8601，字段与 `design.md` 一致。

### 定向测试

- 四种状态推导和全部合法转换；
- leader 已退出但 PGID 仍存在时保持 `running`；
- 自然退出和停止竞争时只产生一个不可逆终态；
- ProcessSnapshot 不暴露内部 Process/Task/锁；
- ID 唯一、格式安全且不能由 PID 猜出；
- List/Log Cursor 正常往返；
- payload 或签名篡改、跨 Plugin 实例、跨 Session、跨 process、未知版本、负 offset、越界 offset 和
  已裁剪锚点全部拒绝。

## 任务三：实现 ProcessManager、日志和 OS 进程组收束

### 新增文件

- `apps/agent/src/agent_orchestration/plugins/process/manager.py`
- `apps/agent/test/agent_orchestration/plugins/process/test_manager.py`
- `apps/agent/test/agent_orchestration/plugins/process/helpers.py`

### 更新文件

- `apps/agent/src/agent_orchestration/plugins/process/models.py`

### 开发内容

1. `ProcessManager` 在所属 Session Event Loop 上维护记录、异步锁、是否接受新 start，以及运行中和
   终态记录上限。它不注册为 Plugin，也不直接访问 Gateway。
2. 启动命令使用：

   ```python
   await asyncio.create_subprocess_exec(
       "bash", "-lc", command,
       cwd=resolved_workdir,
       stdin=asyncio.subprocess.DEVNULL,
       stdout=asyncio.subprocess.PIPE,
       stderr=asyncio.subprocess.STDOUT,
       start_new_session=True,
   )
   ```

   `workdir` 缺省为 Workspace；显式目录 `resolve(strict=True)` 后必须位于 Workspace 内。命令必须
   非空，UTF-8 长度不超过 32 KiB。后台命令不连接 TUI/Gateway 的 stdin；需要交互式终端或 PTY 的
   程序不在第一阶段支持范围内。
3. 创建日志文件和子进程后，在锁内写入 ProcessRecord；任何后续登记失败都进入 rollback：TERM/KILL
   进程组、等待 leader、关闭日志、移除未提交记录。
4. Manager 接收两个由 ProcessPlugin 注入的回调：
   - `start_background_work(name, operation)`，把每个 Supervisor 纳入 Plugin Runtime；
   - `publish_snapshot(snapshot)`，发布状态事件。
   Manager 不直接持有 PluginRuntime、EventBus 或 AgentRuntime。
5. 使用每进程提交门闩保证事件顺序：
   - Supervisor 可立即开始排空输出；
   - 门闩释放前不得发布任何 Supervisor 更新，包括日志截断和终态，也不得开始终态判定；
   - Manager 先发布 `running`，成功后释放门闩；
   - 快速退出只能形成 `running → completed/failed`。
6. Supervisor 负责：
   - 读取合并 stdout/stderr；
   - 在 16 MiB 上限内写日志并 flush；
   - 超限后继续排空 pipe，只在首次超限时更新 `log_truncated`；
   - 观察 `Process.returncode` 并在最终清理时回收 leader；不能只等待 `Process.wait()`，因为仍存活的
     child 可能继承 stdout pipe；
   - leader 退出后仅用已知 PGID 的 `killpg(pgid, 0)` 检查组内后代，不枚举 `ps`；
   - leader 已退出但进程组仍存在时，通过每条记录的唤醒 Event 加有界指数退避探测，初始 50 ms、
     最大 1 秒；`stop` 或 shutdown 会立即唤醒 Supervisor，不使用高频 busy loop；
   - 进程组完全退出后写入唯一终态并发布更新。
7. `stop(process_id, reason)` 在锁内先判断终态；运行中记录先原子设置 `stop_requested`，再在锁外向
   整组发送 SIGTERM。Supervisor 独立于这次 Tool Call 维护停止截止时间，宽限期后进程组仍存在则发送
   SIGKILL，并再使用一个 `terminate_grace_seconds` 有界确认窗口。`stop` 通过 `asyncio.shield()` 等待
   “进程已终态或本轮终止尝试完成”；调用方被取消或 Tool timeout 不得取消 Supervisor，也不得中断
   已经开始的 TERM → KILL 收束。确认窗口后进程组仍存在时，`stop` 返回 `termination_failed`，记录保持
   `running`，Supervisor 继续低频观察，后续 stop 或 Plugin shutdown 可以重试。
8. OS 错误规则：
   - `ProcessLookupError` 视为目标已退出并继续终态收束；
   - `PermissionError` 不假装成功，返回 `termination_failed` 并保留记录供 shutdown 再次清理；
   - `killpg` 不可用的平台明确报告 unsupported，不悄悄只杀 leader。
9. Supervisor 内部异常或被 Runtime 取消时必须先执行 shielded 最终清理：TERM/KILL 已知进程组、回收
   leader、flush/关闭日志并收束记录；所有等待都有上限，清理失败必须显式上报，不能让 Runtime 仅记录
   background failure 后静默留下失管进程。
10. 记录排序固定为 `started_at DESC, process_id ASC`。终态超过上限时删除最旧终态内存记录；运行中
   记录不裁剪，日志文件不在此时删除。
11. `read_logs` 通过 Log Cursor 分页读取 bytes，默认 32 KiB、最大 256 KiB；在 UTF-8 安全边界结束，
    非法原始字节使用替换字符。`next_cursor` 即使暂时读到 EOF 也返回，允许之后读取新增内容。

### 定向测试

- 长期命令的 start 快速返回，Process 对象、PID、PGID、日志路径和 `running` 快照正确；
- `exit 0` 自动完成、`exit 7` 自动失败，状态不依赖 list/get 触发；
- 极快退出的事件顺序固定为 running 后 terminal；
- 极快输出触发日志截断时，首次 Event 仍是 running，后续更新不发生倒序；
- stdout/stderr 在同一日志中可读，日志分页不会切断有效 UTF-8；
- 子进程 stdin 为 DEVNULL，不读取或占用宿主 TUI 输入；
- 当前 EOF Cursor 能继续读取随后追加的内容；
- 达到日志硬上限后 `log_truncated=true`、文件不再增长，但进程不会因 pipe 阻塞；
- running 数量上限拒绝新进程，终态释放运行配额；
- 终态内存裁剪保持稳定顺序且不删除运行中记录；
- stop 终止整组、TERM 无效时升级 KILL、重复 stop 幂等；
- KILL 后仍无法确认退出时在有界时间内返回 `termination_failed`，不误报 stopped，也不无限等待；
- stop Tool 在 TERM 后被取消或超时时，Supervisor 仍完成 KILL、回收和终态更新；
- leader 先退出而子进程仍在同组时记录保持 running，Session stop 后无残留；
- leader 退出后的组存活探测采用有界退避，stop 请求能立即唤醒，不产生高频轮询；
- Supervisor 内部异常或 Runtime 取消时仍清理进程组和日志句柄；
- spawn、日志、Supervisor 登记和首次 Event 发布失败均回滚，不留下子进程或半记录。

## 任务四：实现 ProcessPlugin 生命周期、Event 和单一 Tool

### 新增文件

- `apps/agent/src/agent_orchestration/plugins/process/events.py`
- `apps/agent/src/agent_orchestration/plugins/process/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/process/tools.py`
- `apps/agent/src/agent_orchestration/plugins/process/factory.py`
- `apps/agent/src/agent_orchestration/plugins/process/manifest.json`
- `apps/agent/test/agent_orchestration/plugins/process/test_plugin.py`
- `apps/agent/test/agent_orchestration/plugins/process/test_tools.py`
- `apps/agent/test/agent_orchestration/plugins/process/test_factory.py`

### 开发内容

1. 定义不可变 `ProcessUpdatedEvent`，携带完整 ProcessSnapshot 字段。继承的 `task_id` 固定为
   `None`，启动来源单独放在 `origin_task_id`。
2. ProcessPlugin 组合 Manager，并把 Manager 的快照回调映射为 `publish(ProcessUpdatedEvent(...))`；
   `consume()` 不解释其他领域 Event。
3. 生命周期严格实现：
   - `start()` 记录当前 Session Event Loop，启用 Manager；
   - `quiesce()` 幂等拒绝新 start，并以 `session_shutdown` 请求停止全部运行中进程，但不等待；
   - `drain()` 等待尚未登记完成的 start 事务，以及所有 Supervisor、日志 flush 和终态事件发布；
   - `stop()` 幂等 KILL 残留进程组，在有界确认窗口内回收 leader、关闭日志并释放句柄；仍无法清理的
     进程组必须返回 Plugin stop 错误，不能无限阻塞或伪造终态。
4. Tool 名固定为 `background_process`，Schema 使用扁平参数：
   - `action` 必填且枚举五种值；
   - `command/workdir` 只用于 start；
   - `process_id` 只用于 get/logs/stop；
   - `cursor/page_size` 只用于 list；
   - `cursor/limit_bytes` 只用于 logs；
   - 未知字段、缺失字段和 action 不匹配字段均返回 `invalid_arguments`。
5. action 路由返回稳定 `ToolExecutionResult`：
   - start/get/stop 返回同一 ProcessSnapshot；
   - list 返回 `processes/page_size/has_more/next_cursor`；
   - logs 返回 `process_id/content/has_more/next_cursor/log_path/log_truncated`；
   - 预期错误使用 `code: message`，不向 Agent 暴露 traceback。
6. 五种 action 均 `can_run_parallel=False`。`ainvoke()` 直接调用 Session Loop 上的 Manager；
   `invoke()` 通过 `start()` 时记录的 owner loop 和 `run_coroutine_threadsafe` 复用同一 Manager，并使用
   ToolExecutor 注入的 `timeout_seconds` 作为同步等待上限。owner loop 尚未启动、已经停止/关闭、提交时
   发生生命周期竞争或从 owner loop 线程同步重入时，返回稳定 `tool_unavailable`，不得新建 Event Loop；
   同步等待超时必须取消提交的 Future，start 事务仍按取消规则完成 rollback。生产 Session 链路继续使用
   原生 `ainvoke()`。
7. Factory：
   - 校验 `persistence/runtime` 和 `persistence/session`；
   - 通过 PathResolver 获取 processes 目录；
   - 校验 Plugin 配置并构造 Manager/Plugin；
   - 注册且只注册 `background_process`；
   - 不提供 Capability 或 StateProvider。
8. Manifest 声明 Persistence 两项依赖、一个 Tool、一个 Published Event、无 Consumed Event 和无
   State Scope。

### 定向测试

- Factory 注册内容与 Manifest 完全一致；
- Manifest 只声明一个 Tool 且无 Capability/StateProvider；
- 五种 action 的必填、可选、互斥和未知字段校验；
- start 的 `success=true` 只依赖进程接管，不依赖应用 ready 或后续 exit code；
- list 使用默认 50、最大 200 和 opaque Cursor；get/stop 使用 process_id 而非 PID；
- logs 的业务分页结果随后仍可由 ToolExecutor 处理；
- 同步/异步入口在可用调用环境中返回相同结构，owner-loop 同步重入明确失败而不死锁；
- owner loop 未启动、停止、关闭及提交竞争均快速返回 `tool_unavailable`，不悬挂调用线程；
- quiesce 后拒绝 start，但 list/get/logs/stop 仍可用于收尾；
- `quiesce → drain → stop`、重复 stop 和部分启动失败均安全。

## 任务五：接入 RuntimeUpdate 和 Session History

### 更新文件

- `apps/agent/src/runtime_update.py`
- `apps/agent/src/agent_orchestration/plugins/runtime_update/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/runtime_update/manifest.json`
- `apps/agent/test/agent_orchestration/plugins/runtime_update/test_plugin.py`
- `apps/agent/test/application/test_session_store.py`
- `apps/agent/test/application/test_agent_runtime.py`

### 开发内容

1. 在 `RuntimeUpdateType` 增加 `process.updated`，保持 Gateway Wire Model 的字符串类型不变。
2. RuntimeUpdatePlugin 导入并识别 `ProcessUpdatedEvent`，投影完整当前状态：
   - `process_id/pid/command/workdir/status`；
   - `started_at/ended_at/exit_code/stop_reason/log_truncated`；
   - 不投影日志正文、`log_path` 或 Process 对象；
   - `RuntimeUpdate.task_id=None`。
3. 对 `command`、`workdir` 和 `stop_reason` 使用现有 Redactor；对时间使用 JSON 兼容 ISO 8601。
4. 在 RuntimeUpdatePlugin Manifest 的 `consumed_events` 中静态加入 ProcessUpdatedEvent。ProcessPlugin
   当前为 required，因此接受这项与现有 Plugin 相同的静态关联。
5. 验证 AgentRuntime 对 `process.updated` 的处理：
   - 不进入 Task 状态转换；
   - 作为非 delta 更新持久化并分配连续 sequence；
   - Persistence 失败仍触发当前 fail-closed Session unload；
   - running 和 terminal 事件顺序在历史中保持一致。
6. 不把 `process.updated` 当作持续活动心跳。进程是否阻止 idle unload 由 Supervisor 的
   `background_work_count` 决定，Event 本身只在关键状态变化时发布。

### 定向测试

- 四种状态均投影为同一 `process.updated` 类型；
- 首次更新显式包含 `status=running`，快速退出的历史顺序仍为 running 后 terminal；
- 命令、路径和停止原因正确脱敏；日志正文和日志路径不进入 payload；
- `task_id=None` 不创建或修改 Agent TaskStatus；
- SessionStore 可写入、读取和分页恢复 `process.updated`；
- 旧的 RuntimeUpdate 类型、Assistant delta 处理和 Session History 连续性不回归。

## 任务六：装配 required Plugin 并验证 Session 生命周期

### 更新文件

- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/src/application/session_runtime.py`
- `apps/agent/src/model_config/config_model.py`
- `apps/agent/settings.json`
- `apps/agent/test/application/test_agent_runtime.py`
- `apps/agent/test/application/test_session_runtime.py`
- `apps/agent/test/model_config/test_config_loader.py`

### 开发内容

1. 在 `RuntimeSettings.required_plugin_ids` 和默认 `settings.json` 中加入 `process`，并由
   `SessionRuntime` 像现有 `runtime-update/mcp/memory/knowledge` 一样执行 `required.add("process")`。
   即使调用方传入自定义 required 列表，当前产品 Session 也不能意外缺少 ProcessPlugin；未来移除时
   需要同步删除默认配置、强制 required 和 RuntimeUpdate Event 消费。
2. SessionRuntime 向 Process Factory 注入：
   - 现有 `runtime.plugin_config.process`；
   - Tool Execution Settings 的 `default_page_size/max_page_size`；
   - 不直接注入 Gateway publisher，不保存 ProcessPlugin 引用。
3. 默认配置将 ProcessPlugin 作为 required：
   - Plugin 缺失、Manifest 失配、Factory 失败或依赖缺失时 Session 启动失败；
   - 已有 Plugin 的启动顺序继续由 Capability 图决定；
   - ToolRegistry freeze 前完成 `background_process` 注册。
4. 验证运行中 Supervisor 计入 Runtime Snapshot 的 `background_work_count`：
   - Agent Task 完成后只要进程仍在，SessionStatus 保持 `running`；
   - idle cleanup 不卸载该 Session；
   - 进程自然结束且没有其他工作后，SessionStatus 回到 `ready`。
5. 调整 `AgentRuntime.unload_session()` 的显式 unload 准入条件：
   - 运行中或排队中的 Agent Task 仍返回 `busy`，不隐式取消 Task；
   - 没有工作，或剩余工作仅为 Plugin `background_work_count` 和待 drain Event 时，允许进入
     `_begin_unload_locked()`，由通用 `SessionRuntime.stop()` 执行 Plugin lifecycle；
   - 不按 Plugin ID 或 Process 类型分支，不向 SessionRuntime 增加进程领域方法；
   - idle cleanup 继续使用完整 `snapshot.has_work`，后台进程运行时不得自动卸载 Session。
6. 验证完整关闭顺序：

   ```text
   session.lifecycle=unloading
   → ProcessPlugin.quiesce
   → ProcessPlugin.drain
   → process.updated(status=stopped)
   → ProcessPlugin.stop
   → session.lifecycle=unloaded
   ```

7. Session 重新加载时创建新的 ProcessPlugin 实例，不加载旧 ProcessRecord、不连接历史 PID。旧日志和
   `process.updated` 只作为磁盘与会话历史证据保留。

### 定向测试

- 默认配置和默认 `settings.json` 均包含 required `process`，自定义 required 列表也不能绕过；
- 默认 Session 图包含 required `process` 和唯一 `background_process` Tool；
- 缺少 ProcessPlugin 时 required Session 明确启动失败；
- SessionRuntime 没有新增 `process_xxx` 或 ProcessPlugin 类型依赖；
- 后台进程运行期间 snapshot.has_work 为真且 idle cleanup 不卸载；
- 自然退出后 background work 归零，Session 可恢复 ready；
- 显式 `session.unload` 在仅有 Process Supervisor 时进入 shutdown 并返回 `unloaded`，但存在运行中或
  排队 Agent Task 时仍返回 `busy`；
- unload 先产生 stopped 更新、后产生 unloaded 更新；
- start failure cleanup 和重复 Session stop 不遗留进程。

## 任务七：验证 Tool Guard、Gateway 和端到端场景

### 新增文件

- `apps/agent/test/agent_orchestration/plugins/process/test_integration.py`

### 更新文件

- `apps/agent/test/agent_orchestration/tools/test_tools.py` 或现有 ToolExecutor 对应测试文件
- `apps/gateway/test/test_app.py`
- `apps/gateway/test/test_methods.py`

### 开发内容

1. 通过真实 ToolExecutor 调用 `background_process`，不直接调用 Manager，以确认：
   - `_execution` 从业务参数剥离；
   - Tool timeout 只限制当前 action；
   - 已提交后台进程不受 start Tool Call 返回后的 Task 结束影响；
   - 单结果和 Batch 预算照常应用；
   - 单 Batch 超过 8 个调用时沿用统一拒绝行为。
2. 构造大日志页触发 Tool Result 裁剪和 `ToolResultStore` 外置：
   - Process 日志文件仍是持续追加的业务日志；
   - `tool-results/<task_id>/` 保存该次 logs Tool Result 快照；
   - 返回路径存在、Session 隔离正确、Preview 不超过 Token 预算。
3. Gateway 使用现有 `runtime.update` 发送 `process.updated`：
   - 仅订阅对应 workspace/session 的连接收到；
   - Wire payload 保持 JSON 兼容；
   - 不新增 RPC method 或 ProcessPlugin import。
4. Gateway 现有 `session.unload` 不增加参数或新方法；验证仅有后台进程的 Session 可进入正常关闭，
   有活动 Agent Task 时仍返回 `busy`。
5. 使用短小的实际子进程覆盖两条验收链路：
   - `start → running → logs → non-zero exit → failed → list/get`；
   - `start parent+child → Session unload → TERM/KILL → stopped → unloaded → 无残留 PGID`。
6. 所有创建真实子进程的测试使用 `try/finally` 和最终 KILL 安全网；测试失败也不能把进程留在开发机。
   对信号与进程组行为使用 POSIX 条件标记，非 POSIX 平台明确 skip。

### 定向测试

- ToolExecutor timeout、输出预算、Batch 限制和文件外置均覆盖 Process Tool；
- Gateway 订阅过滤和 `runtime.update` 方法名不变；
- Gateway `session.unload` 保持现有协议并触发后台进程收束；
- `pnpm` 不作为测试依赖，但等价的父子长期进程场景通过；
- 测试结束后 PID/PGID 均不存在；
- 原有 Bash Tool 的进程组清理测试继续通过。

## 任务八：回归验证与文档收口

### 更新文件

- `apps/agent/docs/spec/2026-09-22-background-process-plugin/design.md`
- `apps/agent/docs/spec/2026-09-22-background-process-plugin/plan.md`
- `apps/agent/docs/spec/2026-08-29-device-agent-runtime-session/arch.md`
- `apps/gateway/docs/spec/2026-08-29-agent-gateway-positioning/arch.md`

### 开发内容

1. 对照实现和测试逐项复核 `design.md`，把目标表述更新为实际架构；不把开发过程或未落地想法写入
   架构说明。
2. 在本计划“当前状态”中记录完成情况；若实现中改变文件拆分，只更新真实文件清单，不改变已确认的
   对外契约。
3. 确认仓库没有引入：
   - 多个 Process Tool；
   - Process Capability 或 Gateway RPC；
   - SessionRuntime 的进程领域方法；
   - `ps` 全局扫描、自动重启或跨 Session 恢复；
   - 未登记的后台 asyncio Task。
4. 检查框架日志、错误和 RuntimeUpdate 不镜像未脱敏的命令输出或 Secret，所有本地路径由
   PathResolver 创建，所有 Tool Result 仍通过 ToolExecutor。子进程原始输出属于受 `0600` 保护、按需由
   `logs` 返回的业务日志，不宣称其内容天然无 Secret。

### 验证命令

按从小到大的顺序执行：

```bash
python -m pytest apps/agent/test/agent_orchestration/plugins/process -q
python -m pytest apps/agent/test/agent_orchestration/plugins/runtime_update -q
python -m pytest apps/agent/test/agent_orchestration/plugins/persistence/test_path_metadata.py -q
python -m pytest apps/agent/test/application/test_session_runtime.py -q
python -m pytest apps/agent/test/application/test_session_store.py -q
python -m pytest apps/agent/test/application/test_agent_runtime.py -q
python -m pytest apps/gateway/test/test_app.py -q
python -m pytest apps/gateway/test/test_methods.py -q
make test-agent
make test-gateway
python -m compileall -q apps/agent/src apps/gateway/src packages/gateway_protocol
git diff --check
```

如果定向测试暴露仓库已有的无关失败，只记录并隔离，不在本功能中顺手修复。

## 建议提交拆分

只有用户明确要求提交时，才按以下逻辑边界创建提交：

1. `feat(agent): add session background process manager and tool`
   - 配置、路径、Process 模型、Manager、Plugin、Tool、Manifest 及对应测试；
2. `feat(agent): publish background process runtime updates`
   - RuntimeUpdate 类型、投影、Session History、Gateway 回归测试；
3. `docs(agent): document background process plugin`
   - `design.md`、`plan.md` 及必要架构文档收口。

实现和对应测试必须留在同一逻辑提交中。本轮未创建提交；如用户后续明确要求提交，再按以上边界拆分。

## 完成标准

- Agent 只看到一个 `background_process` Tool，并可完成五种 action；
- `start` 在进程被接管后快速返回，不等待服务 ready，也不受后台进程寿命影响；
- 进程自然退出后无需 list 触发即可得到准确终态；
- list/logs 分页、Tool Result 预算、Batch 限制和长文本外置同时生效；
- 正常 Session unload 后不存在该 ProcessPlugin 遗留的进程组；
- 显式 unload 可关闭仅有后台进程工作的 Session，同时不改变活动 Agent Task 的 busy 保护；
- TUI/WebUI 可通过现有 `runtime.update` 观察完整 `process.updated` 状态；
- 没有新增 Process Gateway RPC、Capability、SessionRuntime 领域方法或 ReActAgent 依赖；
- Process 定向测试、Agent 测试、Gateway 测试、compileall 和 diff check 全部通过。
