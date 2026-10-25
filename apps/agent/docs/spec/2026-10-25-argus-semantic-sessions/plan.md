# Agent 语义会话与插件消息读取实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development after the user approves this plan and selects execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不新增数据库表、不使用 steer 投递观测正文的前提下，让同语境工作复用 session，由 Argus 插件提醒并由 Agent 主动读取后续消息。

**Architecture:** application 管会话候选、持久接纳、同群执行锁和生命周期；一级 Argus 插件管当前任务的 binding、提示、短引用、待读提醒和报告。复用 sessions.properties、conversation_updates 和现有 Task/Prompt Event；ReActAgent 与通用 Runtime 不理解语义路由或 QQ。

**Tech Stack:** 现有 Python、SQLAlchemy SQLite、Pydantic、asyncio、httpx、Event/Prompt、pytest；不增加依赖。

**Spec:** [跨应用约定](../../../../../spec/2026-10-25-argus-semantic-sessions.md)；独立卫生修复见 [结果质量计划](../2026-10-25-runtime-result-quality/plan.md)。

**实施记录（2026-10-25）：** Tasks1–6本轮本地实现与离线验收完成，当前架构见 [arch](arch.md)。逐批及最终跨组件审阅发现的问题已修复并限定复审通过；最终顺序Agent1288、Argus224、Gateway148通过，mypy276通过，安装包构建/安装导入和发现验证通过。运行中新消息只提醒不走steer，既有表集合不变；真实模型/QQ业务与正式部署尚未执行。以下保留原任务步骤作为实施依据，不把未授权真实服务操作标完成。

## Global Constraints

- 用户已通过 /goal 授权预审、实现和本地隔离测试；不自动 branch/commit/push、改本机 settings、迁移正式库或调用真实模型/MCP。任务完成状态以本轮账本和测试证据为准。
- 不新增数据库表，不把历史执行放入 properties 的无限数组；不跨应用导入 ORM 或互写数据库。
- ReActAgent 无状态，Plugin Runtime 按来源路由，Hooks 不承担通知、认领、记账或控制方向。
- 观测正文由 Agent 自主读取；通知仅一句可用性提醒，不调用 steer/cancel/restart，不中断当前 LLM/工具。
- 同聊天可有多个语义 session，实际执行串行；同群锁与全局名额不因空闲 session 存在而被占用。
- 软过期 300 秒；退出自动续接候选的闲置期限 86400 秒；有运行工作/待读/待同步时不自动退场。
- v1 已接纳记录只对账/安全收尾；v2 接纳使用显式协议，不重算旧指纹，不重跑旧模型。
- 旧的真实停止、工具 tracker、outbox accepted/rejected/unknown 和公开后台隔离继续成立。
- 固定 System 说明稳定；消息、群名、路由依据和提醒只在当前 User Prompt/工具结果。

## Review Focus

- 新消息与任务结束/finalize 竞争：正文不能丢，不能为迟到通知重新建语义 session（Tasks 3/4）。
- session 第二次执行不能覆盖旧 wake 身份或把旧报告发送到新 binding（Tasks 1/4）。
- 通知不等于已读，预算截断/重复提醒不能推进 exposed 或消息游标（Task 4）。
- 运行超过五分钟/24 小时、同步工具未退、报告未知不能被时钟强制删除或释放（Task 5）。
- 旧库恢复及后台 API guard：迁移保留历史，普通 session.steer 不因新增通知通路获得后台访问权（Tasks 1/2/6）。

## 基线：已实现与缺口

已检查 `1e2f541` 下源码和测试：

- `application/controllers/scoped_execution.py:60` 每 wake 新建 session；`:164` 封口后立即 unload。
- `application/repositories/session_store.py:545` 用 sessions.properties 的单个 wake_id 同时代表会话和执行；`application/controllers/scoped_execution.py:188` 的报告同步按整个 session 读取，不能直接复用多轮。
- `plugins/argus/plugin.py:20` 构造时固定一个 binding/task，`:87` 拒绝其他 task；`:122` 未 exposed 的引用一律报 delivery_pending。
- `icarus/core/session.py` 是不可变 SessionContext；不能就地改 frozen metadata 作为每轮切换。
- `contracts/prompt.py` 已有 PromptUpdateEvent；PromptPlugin 已在运行边界追加动态上下文。现有 steer 是 user_correction 入队，cancel 才取消执行。
- 本计划不修改上述“steer 不取消执行”的事实，但 Argus 普通观测不得借 steer 注入正文或改变用户优先级。

## 文件分工

以下路径均相对 `apps/agent/`。除明确 Create 外都是已有文件。

| 文件 | 改动职责 |
|---|---|
| `src/agent_app/argus_protocol.py` | v2 纯边界值、候选/通知请求；不依赖 application/plugins |
| `src/agent_app/application/repositories/session_store.py` | 无新表迁移、每 wake 接纳记录、当前执行摘要、候选/恢复查询 |
| `src/agent_app/application/repositories/session_entities.py` | 仅现有字段/必要索引映射，不增加 ORM 表 |
| `src/agent_app/application/models/scoped_execution.py` | 继续复用 ExecutionView，增加候选边界值引用 |
| `src/agent_app/application/controllers/scoped_execution.py` | 同 session 续接、通知、按交接同步报告，不再完成即卸载 |
| `src/agent_app/application/controllers/session_api.py` | 可信 facade 与设施注入，公开 guard 保留 |
| `src/agent_app/application/services/argus_handoff_client.py` | 实际注入插件的 HTTP client：逐 wake/binding 版本分派、available/session_pending、安全错误 |
| `src/agent_app/application/session_runtime.py` | 初始/后续 task 的可信绑定准备；注入通知信号，不按 wake 重建图 |
| `src/agent_app/application/services/session_reaper.py` | 后台续接资格/资源回收，交互 session 原策略不变 |
| `src/agent_app/config/config_model.py`、`settings.json.example`、`src/agent_app/resources/default_settings.json` | 300/86400 秒可信配置，保默认 disabled |
| `src/agent_app/plugins/argus/{factory.py,plugin.py,prompt.py,tools.py,manifest.json}` | per-task binding、信息密集渲染、短引用、通知与主动读取；消费注入 client，不新增同义 client |
| Create `src/agent_app/plugins/argus/references.py` | 普通组件：短引用恢复/映射，不注册插件 |
| 对应 `test/agent_app/application/`、`test/agent_app/plugins/argus/` | 镜像层的功能和恢复测试 |

## Task 1：复用两张现有表，拆开会话与交接记录

**Files:** 修改 session_store.py、必要 session_entities.py；测试 `test/agent_app/application/test_session_store.py`、`test_scoped_execution.py`、`test_handoff_review_regressions.py`。

**Interfaces:**

- 保留 async `reserve_scoped(request, *, identity, execution_id, task_id, max_active_executions=8) -> ExecutionView`；允许 identity 是校验后的既有后台 session。
- 保留 `get_scoped(*, wake_id=None, scope=None)`、`update_scoped(wake_id, changes)`、`complete_scoped_release(wake_id)`，调用者不依赖其旧 JSON 查询细节。
- 增加 async `list_argus_sessions(scope, *, cursor=None, limit=10) -> dict`，返回共同候选页；计时使用注入 clock。
- 内部记录至少包括 `argus_execution`、`argus_reference`、`argus_action`；原 report/cursor 类型继续使用。不要将内部类型加入通用 RuntimeUpdateType。

**存储决策：**

- sessions.properties 保存稳定 kind/owner/scope、有限语境摘要、`last_semantic_activity_at`、可空 `current_wake_id`。长期归属不使用可释放的 scope_key。
- conversation_updates 的 `argus_execution` 每 wake 一条接纳记录：wake_id/fingerprint/request/ExecutionView/binding/期限及启动门控。状态更新定位该行，在事务内合并 payload；接纳行 sequence/occurred_at 不变，最新状态时间写 payload，不能改动公开历史序号。
- report 等实际发生的记录仍 append；每条带原 wake_id/binding_id。不为每一次 status 轮询追加快照，不把历史交接 JSON 复制进 properties。
- SQLite 对内部接纳记录的 payload.wake_id 建部分唯一表达式索引，替代旧 sessions 顶层 wake_id 唯一索引；scope_key 部分唯一索引保留。按 wake 查询使用接纳记录，按 scope 查当前 owner。
- 旧记录原子转换：为已有后台 session 建等价内部接纳行；保留现有 UUID/指纹/阶段/停止证据；旧报告能从旧单 binding 补归属。旧顶层执行键转换后不再作为第二个权威状态源。公开历史游标可能增加，但旧记录不删不重排。

- [ ] RED：新增 `test_semantic_reuse_preserves_prior_wake_identity_and_reports`：两个 wake 使用同 session，旧 wake 可查，旧报告 binding 不变；同 wake 异指纹拒绝。
- [ ] RED：新增 `test_argus_upgrade_preserves_table_set_and_legacy_outbox`：迁移前后 `sqlite_master` 用户表集合一致，四表不增，旧普通历史/后台执行/待同步报告保留；迁移失败回滚，重跑不重复接纳行。
- [ ] RED：新增 `test_two_sessions_one_scope_only_one_execution_owner`：同群不同 session 并发 reserve 仅一个成功；空闲历史 session 不计执行名额。
- [ ] Run：`apps/agent/.venv/bin/python -m pytest -q apps/agent/test/agent_app/application/test_session_store.py apps/agent/test/agent_app/application/test_scoped_execution.py apps/agent/test/agent_app/application/test_handoff_review_regressions.py`，先记录行为 FAIL，不把导入错误当 RED。
- [ ] 实现上述事务/索引/迁移；内部行 schema 严格校验，缺失恢复信息明确不可用，不用默认值伪造释放。
- [ ] 同命令 GREEN；验证旧 `read_updates()` 仍过滤内部 payload 且 sequence 连续性成立。不自动提交。

## Task 2：可信 session 续接与 v2 facade

**Files:** argus_protocol.py、models/scoped_execution.py、controllers/scoped_execution.py、session_api.py、session_runtime.py、plugins/argus/factory.py；新增 fixture `test/agent_app/application/fixtures/argus_v2.json`。

**Interfaces:**

- async `AgentRuntime.scoped_sessions(scope, *, cursor=None, limit=10) -> dict`。
- async `AgentRuntime.scoped_notify(*, wake_id: str, session_id: str) -> dict`，只返回可用性接受结果，无消息正文。
- v2 WakeRequest 增加 `target_session_id: str | None`；已有 scoped_submit/status/cancel/release 签名保持。
- 应用在既有 argus facilities 注入只读 async `load_binding(task_id: str) -> dict`；查询本 session 的持久交接记录，返回对应 wake/binding。插件消费 UserInputEvent 时加载并校验，不新建 TaskBoundEvent，不让 user-input 插件理解 Argus。
- 稳定 SessionContext 只反映后台身份/scope；factory 不再强制初次构造 metadata 里永远有唯一 binding。后台图装配时仍注册 manifest 声明的稳定四工具，不能等 UserInputEvent 后再注册；load_binding 校验只控制各 task 执行权限/required prompt，不引入动态工具注册。renderer 从本 task 上下文取身份，不从冻结 metadata 取上一轮 wake。
- 修改实际 `application/services/argus_handoff_client.py`，按持久 wake/binding 的 protocol_version 编码请求并验证响应，增加 available/session_pending；v1 解析器/原指纹保留，v2 target 在首次提交前冻结并纳入指纹，重试不取当前目标。远端 HandoffError 在 facade 映射安全 unavailable/目标业务拒绝，不能穿透成未知内部错误或 pending=0。

- [ ] RED：新增 `test_scoped_submit_reuses_idle_session_without_changing_prior_execution`：当前 task 关闭后，新 task 使用同 session 历史、不同 execution/binding。
- [ ] RED：新增 `test_target_session_scope_owner_and_retirement_are_validated`：跨群/工作区、interactive、已退出续接或未知 session 拒绝；不得静默改投另一 session。
- [ ] RED：扩展 test_session_runtime.py，验证多 task 绑定按顺序安装，System 稳定，旧 immutable SessionContext 不被变更；普通 submit/steer/get 仍拒绝后台。
- [ ] Run：`apps/agent/.venv/bin/python -m pytest -q apps/agent/test/agent_app/application/test_scoped_execution.py apps/agent/test/agent_app/application/test_session_runtime.py apps/agent/test/agent_app/plugins/argus/test_factory.py`。
- [ ] 实现候选和目标校验，复用现有设施注入 load_binding；插件在任务开始时按 task 加载，并验证 session/scope/wake/binding 一致。缺失绑定使 required prompt 明确失败，不退回上一轮 binding；不修改 user-input/TaskControl 契约、不通过插件导入 application 仓库。
- [ ] 仅新任务使用 submit_reserved；运行中的普通新消息走 Task 3，不调用 submit 或 steer。GREEN 同命令并核对 v1 对账 fixture。

## Task 3：插件可用性提醒，不投递正文

**Files:** controllers/scoped_execution.py、services/argus_handoff_client.py、session_api.py/session_runtime.py；plugins/argus/plugin.py、manifest.json、contracts/prompt.py 的既有类型复用；测试新增 `test/agent_app/plugins/argus/test_notifications.py`。

**Interfaces:**

- 应用通过明确注入的每 session `asyncio.Event` 唤醒 Argus 插件普通后台组件；signal 只表达“重查”，无正文/命令，既不持久也不承担消息所有权。
- 插件向 Argus `available` 端点查询当前 binding 的 `{pending_count, watermark}`；无正文、不产生 exposed。端点由 Argus 计划定义。
- client 增加 async `session_pending(session_id: str, scope) -> dict`，调用受认证 `POST /api/argus/internal/sessions/{session_id}/pending`，body 带 protocol_version=2/scope，返回 pending_count/last_related_at；覆盖尚无 binding 的 waiting。候选、清理和 target 校验使用此持久事实，超时则保守不退场；notify 仅唤醒，不另造计数真值。
- 插件使用既有 `PromptUpdateEvent` 发布一个 runtime section，固定语义为“当前语境有新的待读消息，可在合适时通过 argus_wait 读取”。实际提醒由 PromptPlugin 在现有安全步骤边界送达。

- [ ] RED：`test_notification_never_steers_cancels_or_contains_observation_text`：冻结 LLM/工具 fixture，通知不调用 cancel/steer，不中断执行；后续输入只有固定提醒，无正文/UUID。
- [ ] RED：`test_notifications_coalesce_until_agent_reads`：同一 pending watermark/未读周期最多一个提醒；十次 notify 不变十段上下文，不反复唤醒模型。Agent 读取后仍有待办可在新周期提醒。
- [ ] RED：`test_lost_signal_and_finished_task_preserve_pending_messages`：丢通知、未加载 runtime、任务已 close_or_drain，正文仍在 Argus；状态恢复后能发现，不因 remind 失败标 delivered。新增 `test_idle_notification_waiter_does_not_block_execution_drain`，通知监听仍开启时已完成执行可以通过原停止屏障。
- [ ] Run：`apps/agent/.venv/bin/python -m pytest -q apps/agent/test/agent_app/plugins/argus/test_notifications.py apps/agent/test/agent_app/plugins/prompt/test_plugin.py apps/agent/test/icarus/core/run_control/test_channel.py`。
- [ ] 实现 available 重查与合并提醒；等待前登记/重查消除 signal 清除竞态；错误退避不制造大量日志或提示。监听只在当前 task 开始到完成/关闭之间注册；on_input 启动受跟踪监听后立即返回，不阻塞本插件 inbox。manifest 消费现有 input.finished；匹配 InputFinishedEvent handler 取消并 await watcher/查询/退避，stop 仅作兜底；然后通过原 execution drain，不能隐藏真实在途工作绕过屏障。真实 task.control/input.submit/context.ready 来源 ID 从现有 graph 注入，工具确认/输入/结束/初始视图确认均校验来源与task，不硬编码 agent。持久待办由 Argus 保存，迟到信号在完成对账/下一 task 重查。
- [ ] manifest 声明 prompt.update；不修改 ReActAgent 为 Argus 增加分支，不更改 steer 为 inbox。GREEN 并测试提醒本身不改变读取游标/报告水位。

## Task 4：高密度消息、持久短引用、逐 binding 报告

**Files:** plugins/argus/{plugin.py,prompt.py,tools.py,references.py}、application/services/argus_handoff_client.py、application/controllers/scoped_execution.py、repositories/session_store.py；测试现有 plugin/tools/integration 和新增 `test_references.py`。

**Interfaces:**

- 模型 `argus_read(ref: str, text_offset: int=0, max_chars: int=20000)`；history/wait 保留有限页语义，游标由插件维护，报告 `items[{ref,status,reason?}]`。
- references 普通组件暴露 `resolve(ref: str) -> dict`、async `assign(observation_id: str, *, wake_id: str, binding_id: str) -> str`；scope/身份全来自可信绑定。map 持久到 argus_reference 内部记录，不能每 task 重置编号。
- 错误区分 unknown_reference / not_exposed / not_reportable；delivery_pending 仅限确实有 pending_delivery、尚未最终确认的情况。
- report 先按绑定分组，再使用各自报告锁/指纹/outbox；已有事件完成确认仍决定是否真正送达。client 原始页先生成模型视图；pending_delivery 同时保留原始身份和预算前的模型视图，完成事件与该视图比较，不能继续把精简输出与原始 wire JSON 比较而永久拒绝确认。
- 初始贡献保留已取得的高密度消息，不退回纯索引/每条强制read。v2 不在 on_input 提前 deliver；插件消费可信 BlackboardContextReadyEvent，确认本 task 的完整模型视图确实在最终 input_prompt 后才 ack 对应完整项。被省略/正文截断项不 ack，available/wait 仍能取回；不能把整个 initial 最大时间水位当作已读。初始确认晚于模型启动时，确有确认中的 report 返回可重试 delivery_pending，不写错误 outbox。

- [ ] RED：`test_prompt_preserves_sender_place_time_content_reason_and_work`：有身份/场景/正文和关注原因，无码表/locator 清单；恶意文本仍为不可信数据；必要平台目标不被删。
- [ ] RED：`test_references_survive_next_task_restart_and_never_rebind`：两次 task、卸载重载、同 observation 重取引用稳定；新 observation 单调分配。旧 ref 若当前资格仅 context，不可重新报告；历史可读不等于旧 binding 重开。
- [ ] RED：`test_final_budget_confirmation_controls_read_ack_and_cursor`：结果截断/事件晚到/取消时不确认已读、不跳页；通知不 grant；正文用更小页可重读。初始A/B中B被prompt预算省略时，最终视图只ack A，B仍available且wait能返回；完整初始A不要求再read一次才报告。
- [ ] RED：`test_two_bindings_flush_only_their_own_outbox`：W1 accepted/rejected/unknown 与 W2 独立；重启重送原 report ID，不发新业务动作。
- [ ] Run：`apps/agent/.venv/bin/python -m pytest -q apps/agent/test/agent_app/plugins/argus apps/agent/test/agent_app/application/test_handoff_review_regressions.py`。
- [ ] 实现 per-task binding/短引用与文本渲染，初始任务 prompt 含核心语义，后续通知不含正文；不按未知 ID 拼 UUID。服务端 final 后历史读取用新的合法 context 权限复查。
- [ ] `_flush_reports` 按 wake/binding 过滤；未明确判断的项不自动填 no_action。同路径 GREEN。

## Task 5：相关活动计时、恢复、动作证据

**Files:** controllers/scoped_execution.py、services/session_reaper.py、repositories/session_store.py、config/config_model.py、settings.json.example、resources/default_settings.json；plugins/argus/plugin.py；测试 test_scoped_execution.py/test_session_runtime.py，新增 `test_semantic_session_lifecycle.py`。

**Interfaces:**

- 可信 ArgusSettings 增加 `session_idle_seconds=300`、`session_retention_seconds=86400`，正数且 retention > idle；不写当前 settings.json。
- async `cleanup_argus_sessions()` 与现有交互 cleanup 分开，复用 clock 和卸载设施。
- 有关活动从持久接受的相关消息/实际工作完成更新；后台同步、重复通知不续时。待输入/待报告独立保护。
- argus_action 记录包含当前 session、来源工具调用、目标、发生时间、业务确认状态和有限 receipt。确定成功必须有下游明确业务回执；无规范化回执仅记未确认，不猜测 retcode。

- [ ] RED：用假 clock 验证 299/300 秒、301 秒重新激活、86399/86400 秒退出候选，时间回拨保守处理；历史仍可排查、不调用物理 delete。
- [ ] RED：运行超过 24 小时/工具 tracker 非空/未读/报告 unknown 不回收；仅 status/report retry 不延长语义闲置；资源卸载后仍可在资格期内续接。A 空闲但 Argus 有 waiting A2、B 占用跨过 A 原24小时期限时，session_pending 保护 A；查询 unavailable 不当作 pending=0。
- [ ] RED：恢复旧已停止 execution 只补同步；恢复未确认正文仍可读。结构化业务失败或只有 MCP success 不产生成功动作；重复工具完成事件只记一次 receipt。
- [ ] Run：`apps/agent/.venv/bin/python -m pytest -q apps/agent/test/agent_app/application/test_semantic_session_lifecycle.py apps/agent/test/agent_app/application/test_scoped_execution.py apps/agent/test/agent_app/plugins/argus`。
- [ ] 完成 execution finalize/release 后不立即删除 session；按资格处理 runtime 内存清理。允许安全卸载但不得早于停止屏障。action 由插件消费业务回执、普通记录设施落盘，不借 Hook 日志实现。
- [ ] GREEN；如果 snowluma 没有明确结构化业务回执，标记该成功确认项“下游依赖未满足”，不伪造通过，不为此建设 QQ 专用发送模块。

## Task 6：应用和三方验收

**Files:** 新增 `test/agent_app/application/test_semantic_session_integration.py`；跨应用测试归 Gateway/Argus 计划；更新 README 和实现后对应 arch。

- [ ] 使用真实插件图、SQLite、本地确定性模型与 HTTP fixture，验证“运行→消息到达→仅提醒→主动读取→报告→原 session 下一轮”；同时覆盖另一话题排队、结束竞态、重启恢复和普通 steer/cancel。
- [ ] `apps/agent/.venv/bin/python -m pytest -q apps/agent/test/agent_app/application/test_semantic_session_integration.py`。
- [ ] 聚焦通过后 `make test-agent`、`make typecheck-agent`、`make check-package-agent`；再 `apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test`、`git diff --check`。
- [ ] 记录实际命令/退出码、原有失败身份及未执行项；不得把文档核验或 stub facade 测试称为端到端已实现。仅更新真实实现架构，不自动提交。

## 接口联调、回滚与自审

联调依赖：共同 v2 fixture → Task 1/2 → Gateway sessions/wake/notify → Argus 路由/附加 → Task 3/4 → Task 5/6。独立结果质量可先行。旧 schema 数据先离线迁移测试，不执行正式迁移。

回滚停止新 v2 接纳，旧 execution 的报告/停止路径继续；保留内部接纳/引用/动作记录及索引，不清 sessions/conversation_updates，不恢复每 wake 覆盖 session 的旧行为去解释新数据。

自审：无新表、无旧 UUID 覆盖、无隐式 steer、无运行中正文推送、无时间强删、无 Hook 路由、无回收冒充完成；每项均有上述行为测试。用户已授权逐批实现，实际进度和验收以开发账本/测试证据记录。
