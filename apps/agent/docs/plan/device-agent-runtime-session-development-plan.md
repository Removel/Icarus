# Device Agent Runtime and Session Runtime Development Plan｜设备级 Runtime 与多 Session 开发计划

## 目标

基于 `apps/agent/docs/arch/device-agent-runtime-session-design.md`，把当前固定单 Session 的
`AgentRuntimeService` 迁移为：

```text
AgentRuntime
├── SessionRuntime A
├── SessionRuntime B
└── SessionRuntime C
```

本计划同时完成：

- 每个 Session 独立 PluginRuntimeHost、Plugin、Queue、Blackboard 和 Persistence；
- 同 Session single-flight resume 与修改操作串行；
- 无锁只读 Session 状态投影；
- PluginRuntime 对脱离主 Task 的后台工作统一跟踪；
- 6 小时空闲、2 小时扫描的自动卸载；
- Plugin 状态兼容与多 Session 日志隔离；
- 内部 Event 到公共 RuntimeUpdate 的投影；
- 设备级有界 Update 广播；
- Session 内存幂等提交、近期 Task 状态和受控本地资源导入。

本计划不实现 FastAPI、WebSocket、JSON-RPC、Backend 产品业务或 TUI 网络迁移。Gateway 与 TUI
分别按各自应用计划实施。

## 实施原则

- ReActAgent 保持无状态，AgentRuntime 不进入 Agent 执行循环；
- SessionRuntime 不感知 Gateway、WebSocket、Backend 或 UI；
- AgentRuntime 是新架构唯一公开入口；旧 AgentRuntimeService 只在 TUI 迁移完成前作为现状入口保留，
  Gateway 和新代码不得依赖，最终删除；
- 接口使用扁平 `workspace_path + session_id`，SessionIdentity 只作为内部 Registry/Persistence 键；
- 不新增 TaskManagerPlugin、BackgroundWorkTracker、共享 Persistence 层或第二套 EventBus；
- 每次 create/resume 读取一次最新 ConfigModel，单个 SessionRuntime 生命周期内冻结；
- 修改操作按 Session 串行，只读状态查询不等待 Session mutation lock；
- Hook 继续只观测，不参与生命周期、空闲或状态兼容判断；
- 每个阶段先补定向测试，再修改实现；不同时改 Agent、Gateway 和 TUI 三条主链。

## 目标文件结构

```text
apps/agent/src/application/
├── agent_runtime.py
├── session_runtime.py
├── runtime_update_stream.py
├── runtime_status.py
└── resource_ref.py

apps/agent/src/runtime_update.py

apps/agent/src/agent_orchestration/plugins/runtime_update/
├── __init__.py
├── factory.py
├── manifest.json
└── plugin.py
```

最终删除：

- `apps/agent/src/application/agent_runtime_service.py`；
- `apps/agent/src/application/output_bridge.py`；
- `apps/agent/src/agent_orchestration/plugins/output_bridge/`；
- 旧的 `(source_plugin_id, Event)` 应用输出类型和公开导出。

删除动作必须等 Gateway 和 TUI 已切换到新入口后执行，不保留长期兼容门面。

## 阶段一：PluginRuntime 后台工作生命周期

### 更新文件

- `apps/agent/src/agent_orchestration/plugin_runtime/base_plugin.py`
- `apps/agent/src/agent_orchestration/plugin_runtime/plugin_runtime.py`
- `apps/agent/src/agent_orchestration/plugin_runtime/plugin_manager.py`
- `apps/agent/src/agent_orchestration/plugin_runtime/types.py`
- `apps/agent/src/agent_orchestration/plugin_runtime/wrappers/observable_plugin_runtime.py`
- `apps/agent/src/agent_orchestration/plugins/skill/job_manager.py`
- `apps/agent/src/agent_orchestration/plugins/skill/plugin.py`
- 对应 PluginRuntime、Skill Job 测试

### 开发内容

1. 在 PluginRuntime 内直接保存活动后台 Task，不新增管理 Plugin 或 Tracker 对象。
2. 向所属 BasePlugin 绑定受控 `start_background_work(name, operation)` 入口；参数使用创建协程的
   callable，避免 quiesce 后拒绝时产生未 await 的协程。
3. PluginRuntime 为每项工作分配 work_id，记录 name、started_at、当前活动数、最近变化时间和安全
   错误摘要；完成、失败和取消时自动移除活动记录。
4. 扩展 PluginRuntimeSnapshot，增加后台工作数量、活动工作摘要和最近后台变化时间。
5. PluginRuntime quiesce 后拒绝新后台工作；PluginManager 改为调用 Runtime 的 quiesce，而不是绕过
   Runtime 直接调用 Plugin。
6. Plugin 自己决定 drain 时取消还是等待业务 Job；PluginRuntime 在 Plugin.drain 后确认所有已登记
   工作已经结束。
7. 将 SkillJobManager 的裸 `asyncio.create_task()` 迁移到受控入口。Skill Job 的业务状态、通知、
   commit 不可中断语义和持久化仍由 Skill Plugin/JobManager 负责。
8. 后台工作完成后是否发布领域 Event 仍由 Plugin 决定；EventBus 不解释后台工作。
9. 受控入口只管理“调用返回后继续执行的有限领域工作”。已有 Agent Task 由 TaskChannelRegistry 和
   AgentPlugin 管理，EventBus/Plugin inbox worker 属于 Runtime 基础循环，二者都不重复登记为后台
   工作；长期被动服务循环也不能因为自身常驻而永久阻止 Session 空闲。

### 定向测试

- 后台工作登记、完成、失败、取消后活动数量正确；
- 相同 Plugin 的多项工作相互独立，不同 Plugin 状态隔离；
- quiesce 后拒绝新工作且不产生 coroutine warning；
- drain 等待已登记工作，超时继续走 Host 现有错误汇总；
- 后台异常被观测但不自动禁用 Plugin；
- Skill 生成阶段取消、commit 阶段等待和通知行为不回归。

## 阶段二：Plugin 状态兼容与 Persistence 隔离

### 更新文件

- `apps/agent/src/agent_orchestration/plugin_runtime/state_coordinator.py`
- `apps/agent/src/agent_orchestration/plugin_runtime/host.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/log_handler.py`
- `apps/agent/src/agent_orchestration/plugins/skill/job_manager.py`
- `apps/agent/src/agent_orchestration/plugins/skill/manifest.json`
- 对应状态恢复、Host、Persistence 和 Skill 测试

### 开发内容

1. 恢复时只用 state_version 判断状态格式兼容；plugin_version 和 manifest_hash 保留在快照与诊断，
   不再因为单独变化而拒绝恢复。
2. state_version 不匹配或 StateProvider 恢复抛错时：
   - 核心 Plugin 使 Host 启动失败并回滚；
   - 非核心 Plugin 被停止、禁用并保留旧快照；
   - 继续复用现有 Capability 和 Event 依赖级联；
   - 级联触及核心 Plugin 时启动失败。
3. 不实现状态迁移器，不自动覆盖不兼容状态。
4. 每个 SessionRuntime 继续创建独立 PersistenceRuntime、Trace Writer、Logger Handler 和
   HookRegistry。
5. WorkspaceSessionFileHandler 只处理 HookContext 中完整 SessionIdentity 与自身一致的日志；其他
   Session 和缺少完整身份的日志跳过，避免同一进程多个 Handler 重复写入。
6. Skill 的完整 Job/通知运行状态迁移到 Session State，并保持 v1 的兼容增量：新 Session 快照在
   现有 `job_ids`、`notifications` 之外增加对应完整 `jobs`；恢复旧 v1 Session 时，如果缺少 `jobs`，
   允许从旧 Workspace v1 状态读取后按当前 Session 的 `job_ids` 过滤。
7. Skill 的 `snapshot_workspace_state()` 不再返回完整 Job 集合，避免多个 SessionRuntime 覆盖同一
   Workspace 文件；旧 Workspace 文件只作为兼容读取来源，不自动改写或删除。该固定兼容路径不扩展
   为通用 Plugin 状态迁移框架。

### 定向测试

- plugin_version 或 manifest_hash 变化、state_version 相同时可以恢复；
- state_version 不匹配时核心失败、可选禁用、依赖正确级联；
- 可选恢复失败不覆盖原快照；
- 两个 SessionRuntime 的日志和 Trace 不重复、不串写；
- 停止一个 Session 不影响另一个 Session 的 Writer；
- 两个 Session 的 Skill Job/通知状态互不覆盖。

## 阶段三：RuntimeUpdate 公共契约与 Plugin 投影

### 新增文件

- `apps/agent/src/runtime_update.py`
- `apps/agent/src/agent_orchestration/plugins/runtime_update/`
- `apps/agent/test/agent_orchestration/plugins/runtime_update/test_plugin.py`

### 更新文件

- 内置 Plugin Manifest/Factory 测试和包数据测试
- `apps/agent/settings.json`，迁移期增加 runtime-update 并暂留 output-bridge；TUI 切换后删除
  output-bridge。编辑时必须保留用户的模型配置改动

### 开发内容

1. 定义扁平不可变 RuntimeUpdate：workspace_key、session_id、task_id、type、只读 JSON payload 和
   occurred_at。该模块是 RuntimeUpdatePlugin 与 application 共同依赖的 Agent 公共契约，避免编排层
   反向导入 application；application 包只做公开 re-export。
2. 新增 runtime-update Plugin，并从现有 output-bridge 中迁移 Event 消费和投影相关实现。迁移期间
   旧 AgentRuntimeService/TUI 仍可临时使用 output-bridge，SessionRuntime 和 AgentRuntime 从第一天
   只使用 runtime-update；TUI 切换后立即删除旧 Plugin，最终不存在双输出链。
3. RuntimeUpdatePlugin 继承内部 Event occurred_at，投影 task accepted/started/finished/error/usage、
   assistant text delta、tool started/completed 和 context compacted。
4. Tool arguments 保持 JSON 对象；tool_execution_failed 不重复投影成 task.error。
5. 成功且 AgentCompletedEvent.response.usage 存在时发布累计 task.usage；失败和取消不使用
   last_usage 冒充累计值。
6. RuntimeUpdatePlugin 只投影并调用注入的 publish_update callback，不维护客户端订阅队列。

### 定向测试

- 每种内部 Event 的公共字段、时间戳、脱敏边界和 ignored Event；
- task.usage 在 task.finished 之前，缺失 Usage 时不伪造；
- Tool 失败不重复产生 task.error；
- payload 可由标准 JSON 序列化；
- Plugin 不再向应用层输出 source_plugin_id 或 Python Event。

## 阶段四：提取 SessionRuntime

### 新增文件

- `apps/agent/src/application/session_runtime.py`
- `apps/agent/test/application/test_session_runtime.py`

### 更新文件

- `apps/agent/src/application/__init__.py`
- 原 `test_agent_runtime_service.py` 中可迁移的单 Session 测试

### 开发内容

1. 将 AgentRuntimeService 的单 Session 组装逻辑迁入 SessionRuntime：HookRegistry、
   PersistenceRuntime、ToolRegistry、PluginManager、RuntimeUpdatePlugin 和 PluginRuntimeHost。
2. SessionRuntime 构造时接收已经确定的 SessionIdentity、ConfigModel 快照、系统 Prompt、Tool
   allowlist、初始消息和 publish_update callback。
3. 保留 `start`、`submit`、`cancel_task` 和幂等 `stop(reason, timeout)`；不提供网络协议方法。
4. 暴露只读运行快照，聚合 TaskChannel、UserInput Queue、EventBus、Plugin inbox 和后台工作动态。
   为 UserInputPlugin 增加只读 pending/active 指标，不从其私有字段外部反射取值。
5. 所有退出原因复用同一个 stop 实现：manual_unload、idle_timeout、runtime_shutdown、start_failed。
   reason 只用于日志和诊断，不分叉清理步骤。
6. 创建或恢复失败清理半初始化组件，但不删除已经产生的本地 Session 目录或诊断文件。

### 定向测试

- 迁移后的单 Session 事件顺序、Task FIFO、取消、图片、快照和清理行为与当前实现一致；
- start 失败、取消 start、重复 stop 和 stop 超时保持现有语义；
- SessionRuntime 不导入 Gateway、Backend 或 TUI；
- ConfigModel 在一个 SessionRuntime 生命周期内保持同一快照。

## 阶段五：Session Registry、状态投影与本地发现

### 新增文件

- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/src/application/runtime_status.py`
- `apps/agent/test/application/test_agent_runtime.py`

### 更新文件

- `apps/agent/src/agent_orchestration/plugins/persistence/metadata_store.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/path_resolver.py`
- 对应 Persistence 测试

### 开发内容

1. AgentRuntime 使用完整 SessionIdentity 作为内部键；公开方法接收扁平 workspace_path 和
   session_id，并在内部统一规范化。
2. 每个 Registry 条目保存独立 mutation_lock、SessionRuntime 引用、共享 resume_task、不可变
   SessionStatus、近期提交和近期 Task 状态。设备级短锁只保护条目字典，不包裹 Plugin 启停或 I/O。
3. 生命周期使用 loading、ready、running、unloading、unloaded、failed。running 根据现有动态投影，
   不维护与计数重复的第二份事实。
4. 状态变更时整体替换不可变 SessionStatus；get/list 不取得 mutation_lock，不刷新空闲时间，允许
   返回某一时刻一致但稍旧的快照。
5. 扩展 PathResolver/MetadataStore 的只读存在性和 Session 枚举能力。session.json 的 active/closed
   不作为加载或可恢复事实；本地 Session 目录存在即可尝试恢复。
6. list 合并本地 Session 和内存 Registry：未加载本地 Session 显示 unloaded，加载中或失败条目使用
   内存投影覆盖。

### 定向测试

- 相同 session_id 在不同 Workspace 不冲突；
- 状态查询不等待正在执行的 mutation；
- list 能同时返回 loaded、unloaded、loading 和 failed；
- 异常退出留下的 active/closed 元数据不影响恢复；
- 本地目录存在但状态不完整时允许尝试恢复。

## 阶段六：Create、single-flight Resume 与路由

### 更新文件

- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/src/application/session_runtime.py`
- `apps/agent/test/application/test_agent_runtime.py`

### 开发内容

1. AgentRuntime.start 只启动设备级服务和清理循环，不预加载全部 Session。
2. create_session：
   - 未传 ID 时生成 UUID；
   - 本地同名 Session 已存在则返回冲突；
   - 调用一次 config_loader；
   - 创建并启动空 SessionRuntime；
   - Ready 后安装到 Registry 并返回 session_id。
3. create 失败时清理半初始化实例、不进入 Registry、不删除本地目录；之后相同 ID 的 submit 可以
   尝试恢复，再次 create 仍视为已存在。
4. submit：本地不存在则失败；已加载直接提交；未加载或 failed 时在 mutation_lock 内登记唯一
   resume_task 后释放锁并等待；正在 resume 的调用方在锁内取得同一个 Task 后同样释放锁等待，得到
   同一次成功或错误。恢复 I/O 不长时间占用 mutation_lock。
5. resume 每次重新调用 config_loader，创建当前版本 Plugin Graph，恢复持久状态，不恢复旧对象、
   Queue、协程或调用栈。
6. 恢复完成后在 mutation_lock 内安装 Runtime；submit 等待恢复后重新取得 mutation_lock，在锁内复检
   Runtime 并完成 Task 入队与活动时间更新，随后释放。cancel、unload 和 Registry 替换同样按该锁
   串行；状态查询不加锁。
7. 等待共享 resume_task 时使用 shield，单个调用者取消不能取消所有等待者共享的恢复；恢复 Task
   完成后短暂加锁清理引用，失败后的新请求可以重新尝试。
8. cancel_task 只作用于已加载 Runtime，不因取消请求触发 resume。
9. unload 在锁内复检，Task、Queue、Event 或后台工作存在时返回 busy；空闲时切换 unloading，调用
   统一 stop 并将条目投影为 unloaded。
10. AgentRuntime.stop 先拒绝新修改，停止清理循环，再并发取得各 Session 锁并调用统一 stop，最后
   关闭设备级 Update 订阅。

### 定向测试

- create 立即得到 Ready Runtime，重复 ID 冲突；
- submit 不隐式创建不存在 Session；
- 同 Session 并发 resume 只构造一次，等待者共享成功或失败；
- 单个 resume 等待者取消不取消共享恢复；
- 不同 Session 可以并发恢复和提交；
- submit 与 unload、自动 unload 与 resume、stop 与 create 的竞态没有重复 Runtime 或已关闭提交；
- cancel 已卸载 Session 不触发恢复；
- create/resume 分别读取最新配置，已加载 Session 不热更新。

## 阶段七：提交幂等与 Task 状态

### 更新文件

- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/src/application/runtime_status.py`
- `apps/agent/src/application/session_runtime.py`
- `apps/agent/test/application/test_agent_runtime.py`

### 开发内容

1. submit 必须携带调用方生成的 submission_id。去重键为完整 SessionIdentity + submission_id。
2. 对提交请求生成稳定指纹，覆盖 prompt 和 ResourceRef 列表。submit 在解析或读取 ResourceRef 之前
   先查询近期提交记录，使首次成功后暂存文件已经删除的重试仍可直接返回原结果：
   - 相同 ID、相同内容返回原 InputAccepted；
   - 相同 ID、不同内容返回明确冲突；
   - 新 ID 正常进入 Session Runtime Queue。
3. 每个 Session 只保留有界近期提交记录，默认容量在实现中显式配置；进程重启后不保证去重。
4. AgentRuntime 根据 submit 返回和 RuntimeUpdate 维护当前 Task 与有界近期终态投影。
5. get_task_status 第一阶段只返回内存中的排队、运行和近期终态；已卸载且记录淘汰时返回不可用，
   不从 trace.jsonl 推断业务状态。

### 定向测试

- 相同提交只创建一个 task_id；
- 内容冲突不会进入 Queue；
- 不同 Session 的相同 submission_id 不冲突；
- 有界淘汰不增长内存；
- 排队、运行、完成、失败和取消投影正确；
- 进程重建后不错误承诺旧 submission_id 幂等。

## 阶段八：空闲时间与自动卸载

### 更新文件

- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/src/application/session_runtime.py`
- `apps/agent/src/application/runtime_status.py`
- `apps/agent/test/application/test_agent_runtime.py`

### 开发内容

1. AgentRuntime 默认 idle_timeout=6h、cleanup_interval=2h；测试可注入时间源和短间隔。
2. 每台设备只运行一个清理 Task，不为每个 Session 创建 Timer。
3. SessionRuntime Ready 时初始化 ready_at；最近活动时间取 ready_at、Task 接受/终态、
   PluginRuntime.last_event_at 和 last_background_work_at 的最大值。
4. 每轮扫描先只比较时间；候选 Session 取得 mutation_lock 后复检：无活动 Task、无 Runtime Queue、
   无 EventBus/Plugin inbox 积压、无 Plugin 后台工作，才调用统一 stop。
5. 连接、Update 订阅、状态查询、Session 列表、心跳和健康检查不刷新时间，也不阻止卸载。
6. 实际卸载允许发生在连续空闲 6～8 小时之间。

### 定向测试

- 未满阈值不卸载，满阈值且空闲时卸载；
- Task、Event、Plugin inbox 或后台工作任一存在时跳过；
- 扫描复检与并发 submit 竞争时不会关闭已接受 Task；
- 订阅和查询不延长 Session 寿命；
- 自动卸载后下一次 submit 自动 single-flight resume；
- AgentRuntime.stop 取消并等待清理 Task。

## 阶段九：设备级 RuntimeUpdate 广播

### 新增文件

- `apps/agent/src/application/runtime_update_stream.py`
- `apps/agent/test/application/test_runtime_update_stream.py`

### 更新文件

- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/src/application/session_runtime.py`

### 开发内容

1. AgentRuntime 为 loading/ready/running/unloading/unloaded/failed 发布 session.lifecycle，并消费各
   SessionRuntime 的公共 Update 以维护 Task/Session 状态和设备级广播。
2. 复用当前多订阅广播代码到 AgentRuntime 的 RuntimeUpdateStream。每个订阅使用独立有界队列，
   默认容量 4096 并允许构造参数覆盖；
   溢出关闭慢订阅并抛出明确 overflow，不阻塞其他订阅或 SessionRuntime。
3. 保持单 Session FIFO 和 AgentRuntime 实际发布顺序；不增加全局 sequence、持久化或补发。

### 定向测试

- Session 生命周期 Update 来自 AgentRuntime，不经 EventBus；
- 多 Session 更新进入同一设备流且身份正确；
- 多订阅独立、有序，慢订阅 overflow 不影响快订阅；
- Update Stream 关闭会唤醒全部等待者。

## 阶段十：受控本地资源与图片导入

### 新增文件

- `apps/agent/src/application/resource_ref.py`
- `apps/agent/test/application/test_resource_ref.py`

### 更新文件

- `apps/agent/src/application/agent_runtime.py`
- `apps/agent/src/application/session_runtime.py`
- `apps/agent/src/agent_orchestration/plugins/user_input/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/runtime.py`
- 对应图片和应用测试

### 开发内容

1. 定义扁平 ResourceRef，第一阶段包含 resource_id 和可选 media_type；resource_id 只允许安全字符。
   受控暂存根目录从现有 `ICARUS_DATA_DIR` 下固定的 `incoming/` 目录派生，写入方与 Runtime 使用同一
   环境配置，根目录本身不通过 RPC 传输；不接受绝对路径、`..` 或 URI。
2. AgentRuntime submit 在 mutation_lock 内解析 ResourceRef，并在返回 task_id 前导入目标 Session
   assets。继续复用现有签名检测、内容 Hash、原子复制和安全权限。
3. media_type 只作为声明，真实格式以文件签名校验为准；不匹配时拒绝提交。
4. UserInput Queue 最终只保存稳定 ImagePart，不保存暂存路径。
5. 导入失败时整个 submit 不进入 Queue，也不记录 submission_id 成功结果。
6. Runtime 接受后由调用方清理暂存文件；Agent 不依赖暂存文件继续存在。

### 定向测试

- 合法 ResourceRef 导入后返回 task_id，删除暂存文件不影响执行；
- 绝对路径、路径穿越、缺失文件、伪造扩展名和媒体类型不匹配被拒绝；
- 相同内容按 Hash 复用 Session Asset；
- 失败提交不产生 Task、幂等成功记录或残缺 Blackboard 输入；
- URL ImagePart 的原有路径不回归。

## 阶段十一：移除旧 Service 与最终验证

### 删除或迁移

- Gateway 和 TUI 已使用新接口后删除 AgentRuntimeService；
- 将仍有价值的旧 Service 测试迁移到 SessionRuntime 或 AgentRuntime；
- 删除旧 OutputEvent、OutputEventSubscription 和 output-bridge Plugin；
- 更新 `application/__init__.py`、required_plugin_ids、包数据与文档引用。

### 验证顺序

每阶段先运行最小测试：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugin_runtime -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/application/test_session_runtime.py \
  apps/agent/test/application/test_agent_runtime.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/runtime_update \
  apps/agent/test/application/test_runtime_update_stream.py -q
```

Agent 层完成后：

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test
git diff --check
```

旧 Service 删除必须在 Gateway 和 TUI 迁移测试通过后执行，并补跑：

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
```

## 完成标准

- AgentRuntime 是 Agent 应用层唯一公开入口；
- 一个 SessionIdentity 最多一个活动 SessionRuntime，并发恢复严格 single-flight；
- 修改串行、状态查询无锁，不同 Session 并行；
- 所有脱离调用长期运行的 Plugin 工作受 PluginRuntime 跟踪；
- 手动卸载、自动卸载、Runtime 退出和失败清理复用一个 SessionRuntime.stop；
- 6 小时空闲、2 小时扫描在竞态下不会关闭已接受 Task；
- Plugin 状态升级和可选 Plugin 降级符合已确认规则；
- 多 Session 日志、Trace、Skill State 不重复、不串写、不覆盖；
- Gateway 只看到 RuntimeUpdate，不看到内部 Event；
- 返回 task_id 前资源已经进入 Session assets；
- Agent 全量测试、编译和 diff 检查通过。

## 实施结果

- 已实现 PluginRuntime 受控后台工作、状态快照和 Skill Job 迁移；
- 已实现 state_version 兼容、核心/可选 Plugin 恢复边界、Session 日志隔离和 Skill Session State；
- 已实现 RuntimeUpdatePlugin、SessionRuntime、设备级 AgentRuntime、strict single-flight resume、
  mutation lock、无锁状态投影、submission_id 幂等、Task 状态和 6h/2h 空闲卸载；
- 已实现 ResourceRef 安全解析，并在返回 task_id 前导入 Session Asset；
- 已删除 AgentRuntimeService、OutputBridgePlugin 和旧应用 Event 输出链；
- 已修复 Agent 流式生成器跨 `yield` 持有 Hook Context，导致关闭发生在不同 Context 时重置
  `ContextVar` token 失败的问题；Hook Context 现在只包围单次拉取、Hook 调用和关闭动作；
- Agent、Gateway、TUI 合并全量测试 476 项通过，包含 9 个 TUI 视觉快照；compileall、
  `git diff --check`、wheel 包内容检查和真实本机 GatewayClient/WebSocket 模型冒烟通过。
