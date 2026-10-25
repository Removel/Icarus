# Agent 的 Argus 后台执行设计

> 第二阶段设计；2026-10-22 已接线并完成离线回归及本机真实测试，尚未正式部署。实际现状见arch。
> wire/跨应用语义以 [共同契约](../../../../../spec/2026-10-21-argus-agent-handoff.md) 为准；
> [arch](arch.md) 描述源码基线，[plan](plan.md) 拆分实现和验证任务。

## 2026-10-22 最小接入修订

用户选择后台直接使用配置的mcpServers，不新增白名单、动作scope校验、QQ发送模块或发送记账。
SessionRuntime只补required prompt contributor，不清空MCP；固定说明允许通过已有MCP发送，
最终正文不自动发布。Argus自身的绑定读/逐项report与执行生命周期不变。下文相关“后台MCP为空/
不发QQ”保留为第二阶段历史设计，不再是当前实施要求；当前行为见arch和共同契约顶部修订。

## 1. 目标、分层与存储

复用现有 SessionRuntime 和普通工具/记忆能力；Agent 自主选择读消息、查历史、等待和逐项报告。
不固定模型步骤，不将 run 结束、工具 success 或正文回复当完成，不接 QQ MCP、不发平台消息。

应用内 `agent_app/argus_protocol.py` 持有 Argus HTTP 的纯边界类型和安全错误码；应用宿主客户端与
插件客户端均可依赖它，不反向 import 彼此的 controllers/models/repository。执行控制内部类型
仍归 application/models/scoped_execution.py，不把它们复制给插件，不建立跨应用共享包。

ScopedExecutionController 属于 agent_app/application，拥有持久执行身份、scope/总名额、启动门控、
停止/封口。Argus 是普通一级插件，拥有业务 prompt、四工具和报告协作。ReActAgent 无状态，不认识
Argus；Plugin Runtime 不识别领域 Event；Hook 不启动下游、不决定路由、不承担业务记账。

只给现有 SQLite sessions 加两列：scope_key TEXT、properties JSON。scope_key 非空部分唯一索引
保证同 source/account/chat 跨 workspace/boot 排他；清除 scope_key 才释放。properties 记录
kind=argus_background、owner=argus、wake_id、execution_id、task_id、scope、origin_boot_id、
fingerprint、phase、bound/launching、deadline_at、exit evidence、report_cursor、sync_state、
release_complete。内部 wake_id 表达式唯一索引保证跨 workspace 的 wake 幂等，不新增表。

旧 properties 为 null/空默认 interactive。显式 ADD COLUMN 迁移，不依赖 create_all 补列。
报告 outbox、其接受/拒绝回执和读取游标沿用 conversation_updates；内部类型使用独立 raw 查询，
不得强行反序列化为 RuntimeUpdate，不改变普通历史投影/连续性检查；不显示到 UI、普通订阅。

## 2. 可信会话与 MCP 装配

新增通用不可变 SessionContext(kind,scope,metadata)，不含 secret，不带 QQ 类。由可信应用层创建，
SessionRuntime 注入插件工厂。一次 wake 一个短命隐藏会话；后台 workspace 只来自可信配置。
normal session.create 不能指定后台 kind/scope，get/submit/steer/cancel/unload/delete/history/
subscribe/title/task status/discard_empty 等公开路径一律拒绝后台会话。list 同时过滤 SQL 摘要与
内存 entry；未知会话不能预订阅。内部控制器走可信专用方法，不引入模型可填 trusted=true。

第二阶段后台 effective_config 不继承全局 mcpServers，固定为空；MCP 插件可保留空 manager，
不连接、发现或调用任何配置服务器。interactive 原配置不变。可信 workspace 本身不隔离服务器。
这不是第三阶段动作 profile/policy，也不宣称隔离 Bash/浏览器等普通工具或用户部署中的凭据。

## 3. reserve → bind → launch 与资源上限

控制器通过 AgentRuntime 的公开 scoped_submit/status/cancel/release facade 服务 Gateway。
身份和指纹通过 sessions 同库事务落盘。相同 wake+指纹返回原 execution/session/task，异内容
submission_conflict。先竞争 scope 再检查本宿主的后台名额，默认 max_active_executions=8、1..100；
事务内统计持有非空 scope_key 的后台 sessions，含 stopped/pending_sync。满额 capacity_busy，
不创建新 execution；不影响 interactive。不同 wake 事务竞争不能突破总额。

严格顺序：reserve 身份/占用 → 应用 ArgusHandoffClient.bind 确认 → 持久 bound → 持久 launching
→ `_submit_reserved_task(identity,task_id,prompt,*,execution_id)` 调现有 SessionHost/UserInput 入队。
公共 submit 不开放 task_id。网络 bind 不持 DB 锁；返回后重读取消状态。

preparing/bound 可复用原身份重试。launching 后进程崩溃只对账/标 interrupted，不重新入队；
允许保守的“未执行但中断”窗口，不承诺跨网络 exactly-once。取消与 bind 回执竞争时先明确是否
已绑定：有 binding 走 finalize，无 binding 且确认从未入队才能本地 start_failed 安全收尾。

总执行期限从 reserve 时起默认 15 分钟，可配置，含 preparing/排队/wait。期限到发起取消，不以
墙钟释放。wait 保留 execution 名额和本 scope；不占 Argus 推进 worker。本阶段不新增模型请求
信号量，不声称满额第九群必能运行；status 与 capacity_busy 精确表达上限。

## 4. 真正停止：四层屏障而非一次裸 drain

AgentPlugin.drain() 只等当前已登记 run。UserInput 入队返回不代表 run 已登记，故不能直接在提交
回执之后用空 drain 宣布 stopped。单 wake 单 session 按以下顺序收束：

1. SessionRuntime 的可信内部 `drain_execution()` 先等既有 PluginManager.drain() 完成输入队列、
   prompt/context 路径与插件 inbox；全图 drain 重复到 idle。贡献失败/排队取消须使输入得到明确
   终态，不能留永久 queue.join。不得通过 stop/quiesce 代替正常 drain 而误取消正常执行。
2. 由现有 AgentPlugin.drain() 等 run finally；全图 drain 后再确认本次 task 没有非终态 channel。
   TaskControl 不新增 wait_exited/per-task 退出契约。等待者取消须 shield 底层 drain，不能连带取消 run。
3. 等同会话 ToolExecutionTracker 的 pending 调用真正结束；未知取消保留 cancelling，不释放。
4. 应用 update queue flush 到既有终态落盘、无 persistence_failed，再持久 exit evidence/stopped。
   内部 drain/flush 不持 entry.mutation_lock，避免 update/checkpoint 等待锁造成死锁。

### 通用工具存活跟踪

新增 `icarus/core/tools/execution_tracker.py` 的 ToolExecutionTracker，只有生命周期语义：
`pending_count`、`track(future)`、`async drain()`。每会话一个 tracker，经 AgentFactory →
AgentAssembly → ToolExecutor 注入，同 executor.snapshot 共享，避免每次模型调用丢跟踪。
默认 tracker 也用于普通 executor，但普通取消不自动 drain，不破坏原快速返回语义。

- 同步工具的 asyncio.to_thread 调用单独创建并登记 task，再 shield 等待；取消调用方不会取消
  承载线程的 task，线程真正结束才移除 pending。不能只跟踪一个已被取消的 wrapper。
- 异步工具在启动前登记，超时/取消仍保留原 task；现有 1 秒 grace 后可返回取消未知，但 tracker
  不移除未完成任务，迟到异常被消费且不伪造报告。同步入口 invoke 执行期间也登记实际 future，
  sync/async 对同一工作都不提前确认退出。
- Tracker.drain 本身被取消不取消 pending 工作，后台监控可再次等待；普通取消 API 可立即接受。
  不改变工具语义，不用 Hook 探测或让 core 识别 Argus。工具创建的显式后台进程仍按现有 process
  插件生命周期管理；tracker 证明调用已结束，不声称外部服务副作用回滚或进程都被清除。

## 5. 重启、outbox 结案与释放

单 data_dir 单宿主，不新增 owner 锁。真正单宿主重启可把旧进程内线程/协程死亡作为停止证据，
未完成 execution 标 interrupted；先恢复记账再封口，不重跑模型。不存在内存 entry/普通 task 查
不到不是运行期间的停止证据。外部服务操作或后台进程不因宿主死亡被声称已回滚。

执行停止后按持久 outbox 序号串行结案 → ArgusHandoffClient.finalize(exit_kind,report_cursor)
→ 同一事务清 scope_key 并标 release_complete → 尝试卸载运行资源。资源卸载失败不撤销已释放、
不重跑旧 execution。outbox 未明或 finalize 不可用则 stopped/pending_sync，保留槽与名额，后台退避。
release RPC 只催办幂等收尾；不能强制解锁。按 scope status 只返回当前 owner，按 wake 保留旧身份。

报告内部记录：

| update_type | payload / 含义 |
|---|---|
| argus_report | report_id、规范化 items、fingerprint；durable pending |
| argus_report_synced | report_id、accepted receipt/report_cursor；已接受结案 |
| argus_report_rejected | report_id、确定拒绝 receipt/code；拒绝结案，不算处理 |
| argus_wait_cursor | task_id、last_returned_cursor；最终预算后完整工具结果接受确认才推进 |

同 task+规范化 items 复用未结案 report，模型纠正为不同内容才创建新 ID。本地校验未知/越群/context/
未 exposed 立即拒绝，不先写无效业务 outbox；服务端仍须原子复查角色、版本和策略，不能依赖本地快照。

HTTP 超时/503/认证配置错误不是 definitive rejected，继续保留并告警；确定的 item_not_reportable/
content_restricted/result_conflict 回执落 argus_report_rejected，可继续下一条。report_id_conflict
重放本地原 body 对账，不覆盖/换 ID 抹除旧报告；execution_closed 对账已有 report/finalize 回执，
未核清不解锁。只将 accepted receipt 的序号用于 report_cursor，初始 0，按顺序不跳过未知报告。
任意拒绝不得变 handled；未报告 initial 最终 unresolved；incidental 未报告仍 new，无 wake 归属。

## 6. 普通 Argus 插件与四工具

目录 `agent_app/plugins/argus/`，manifest 声明 events/capability/state/tools；不注册子插件。
无后台 context 时专用工具为空、动态贡献空完成；后台缺 binding/required 贡献失败不启动模型。

固定 `ARGUS_WORK_GUIDANCE` 通过 `static_prompt_sections()` 返回一份
`PromptSection('argus-work','session',...)`，初始化后 System 不变。动态 PromptContributionEvent
仅带 scope/wake、理由、引用/摘录、initial_cursor、有记录进度，外部原文是不可信内容。
插件预算组装后 /deliver，确认实际贡献 ID 才发布。受限引用只有安全标识，不进 exposed work；
全部初始内容被限制则失败收束，不等 required timeout 漂移。后续结果不反复附固定指导。

工具 schema：

- argus_read(observation_id,text_offset=0,max_chars=20000)：稳定版本规范文本分块，max_chars 1..20000，
  返回 next_text_offset 或 null；不允许把消息页 cursor 当正文偏移。每块复查安全策略/版本提示。
- argus_history(cursor?,limit=20)：初始水位之前向旧翻，1..50 项；同 scope、可跨 boot，有限输出。
- argus_wait(cursor?,timeout_seconds=30,limit=20)：从 initial 或持久成功游标向新翻，单次 0..30 秒；
  没消息正常空页。仅安全过滤行时服务端可推进水位；成功接受结果后才持久，HTTP 失败不推进。
- argus_report(items)：1..50 项、reason≤1000；模型不填 report_id/session/task/wake/binding/scope。

read/history/wait 采用服务端 role/origin，不按工具名猜认领。origin_boot 资格遵守根契约：旧
binding 在 Argus 重启后仍可处理其原 boot 合法工作，新 boot 消息只 context。版本/撤回/敏感策略
变化可拒绝原work，按确定拒绝终态结案，不让旧报告永久堵群。
插件 backend 只暂存 pending_delivery[task_id,tool_call_id] 的原始结构化结果，不在 HTTP 成功
时提交 exposed/游标。ToolExecutor 通过现有_supported_execution_arguments按签名注入只读
`tool_call_id=ToolCall.id`，Argus工具声明此执行参数但不放模型schema，普通工具不支持则不传；
不能由模型伪造关联ID。复用现有通用 AgentToolCompletedEvent（只接受来源 AgentPlugin）；该事件
的 result 已经通过 ToolExecutor.finalize_group 并进入 run.messages。Argus 插件按task/tool_call_id
比对最终 output 与暂存的完整规范 JSON：完全一致才把本页ID加入exposed，并持久 wait cursor；
预算截断/文件预览/预算失败/取消没有完整事件则整页保守不确认，可用更小limit重取，不能从仅
残留metadata或结果文件指针推断已送达。下一轮工具与异步确认事件竞速时，report返回可重试
`delivery_pending`，不写业务outbox；wait使用上一个已确认游标，允许重复不漏项。初始prompt
/deliver的确认路径不变。不新增Hook或ReAct领域分支，也不以普通ToolExecutionResult.success作确认。
规范JSON比较只忽略JSON对象键顺序，不忽略数组顺序/字段/文本；最终结果必须保持完整结构且
无截断标记，仅元数据相同不够。确认事件重复按tool_call_id幂等，不能用旧确认倒退游标；
Argus专用工具can_run_parallel固定false。同一工具组多个wait若同游标暂存，确认按水位取max，
宁可重复不能跳过未确认页。

sync invoke 与 async ainvoke 共用后端和现有 async 桥接，不另起 loop。工具结果明确业务
accepted/rejected/unknown，ToolExecutionResult.success 不充当逐消息处理报告。

## 7. 交付、打包与验证

固定 JSON 契约 fixture 与 Argus/Gateway 一致，应用各自持有边界类型；新 manifest 纳入 wheel
验证白名单。不要以源目录存在为启用，也不自动修改本机配置。

离线验证覆盖迁移、持久幂等/总额竞争、公开 session guard 双路径、input 未启动时不提前 stopped、
同步线程/async 未退不释放、tracker.snapshot 与取消等待者、update 落盘屏障、后台不继承已有 QQ
MCP、部分报告/永久拒绝/重启 outbox、跨 boot 工作资格、正文续读/敏感拒绝、不同 scope 并行与满额。
真实模型/MCP、正式数据库和部署另获授权。不沿用基线测试数当验收，不新增 speculative 抽象。
