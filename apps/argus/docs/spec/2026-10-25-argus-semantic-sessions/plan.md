# Argus 语义筛选、消息归属与持久待读实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development after user approval. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用现有规则/JEV 和两张业务表，将新消息分配到语义 session；运行中只通知可读，不推正文，支持不同话题排队和同话题跨五分钟续接。

**Architecture:** parser 保留原始内容和必要回复证据；规则先过滤当前不可处理的媒体；routing service 复用 JevClient 选择有限候选。repository 原子保存归属，HandoffService 提供 assigned-work 主动读取；RealWakeService 只推进可信控制。没有新的 inbox/topic/execution 表或插件系统。

**Tech Stack:** 现有 FastAPI、SQLAlchemy/asyncpg、PostgreSQL、typesafe_sdk、httpx/WebSocket、pytest。

**Spec:** [共同约定](../../../../../spec/2026-10-25-argus-semantic-sessions.md)、[Agent 计划](../../../../agent/docs/spec/2026-10-25-argus-semantic-sessions/plan.md)。

**实施记录（2026-10-25）：** Tasks1–5及三应用本地离线验收完成，逐批和最终跨组件问题修正复审通过；最终完整Argus224通过，包含正常/受控延迟确认两条v2场景及旧版两场景。当前实现见 [arch](arch.md)。真实JEV/QQ/MCP与正式迁移部署没有执行，不能用本地确定性provider证明真实业务效果。

## Global Constraints

- 用户已通过 /goal 授权预审、实现和本地隔离测试；正式迁移/真实模型/QQ/现有服务重启仍需另行授权，不自动 branch/commit/push。
- 不新增表。schema_version/wake_requests/observed_items 保留职责，仅改现有 JSON、索引与必要迁移。
- JEV 只判断是否值得看及消息归属，不要求主 Agent 回复；不要把完整 probabilities 给主 Agent。
- 同聊天多语境 session；实际执行串行。active wake 不得阻塞新消息的过滤和语义归属。
- 运行中正文不进 notify；先持久归属，插件只提醒，Agent 主动读。通知不等于 exposed/processed。
- 完整内容安全检测先于截断，所有内容出口和候选语境都受权限/敏感词策略约束。
- 五分钟软过期可续接，24 小时无工作退出续接；Agent 拥有资格计算，Argus 不复制第二个会话 TTL 状态机。
- 不改 OneBot 业务成功语义，不新增 QQ 发送层；旧 boot 授权不因 session 复用而扩大。

## Review Focus

- 纯图、@+图片与有意义图文不能被统一误判为可处理正文或整条丢弃（Task 1）。
- 群内交错话题/短回复与恶意候选文本不能被同群或时间近强行归为一话题（Task 2）。
- active 执行期间仍路由，另一话题不能被 wait 顺手认领（Tasks 3/4）。
- 附加与 finalize 竞争：未读新消息不能被标 failed 或脱离原语义归属（Tasks 3/4）。
- 重试/新 boot/v1 升级不重复认领、篡改原请求或让失败通知丢弃正文（Tasks 3/5）。

## 当前事实

`common/tasks/observation_scheduler.py:29` 遇到 active wake 只 advance 后 return；`common/services/judgement_service.py` 仅选唤醒规则；`surfaces/qq/services/fact_service.py` 没有可处理文本类型事实。QQ parser 把图片引用保存在 content，但 `item_text()` 变成 [image]/[other]。

`db/schema.sql` 的 uq_wake_active 和 uq_wake_real_scope 把同群 waiting 与 running 一起排斥。`HandoffRepository.role()` 把同 boot 未归属新消息视为 incidental work，未按语义 session 限制。`finalize()` 只按冻结 initial 处理，不能直接套用运行中新分配消息。

## 文件分工（相对 apps/argus）

| 文件 | 修改 |
|---|---|
| `src/surfaces/qq/services/parser_service.py`、`fact_service.py`、`rules/qq.yaml` | 可处理正文/回复关系事实和图片规则 |
| `src/common/services/judgement_service.py` | 保留安全/预算，唤醒含义改为值得看，给路由提供语义输入 |
| Create `src/common/services/session_routing_service.py` | 普通服务，复用 JEV 做有界归属选择 |
| `src/common/schemas/{wake.py,judgement.py,handoff.py}` | v2 目标/候选/通知和有界读取边界 |
| `src/common/repositories/{observation_repository.py,handoff_repository.py}` | 归属、附加、实际执行互斥、可读确认和封口竞态 |
| `src/common/services/{handoff_service.py,real_wake_service.py}` | 高密度数据视图、available/ack、可信通知与故障重试 |
| `src/common/tasks/observation_scheduler.py` | 路由与执行推进分开，同群串行不堵采集 |
| `src/common/clients/{gateway_agent_client.py,gateway_client.py}` | v2 候选/目标/notify，无正文控制信号 |
| `src/common/api/handoff.py`、`src/main.py` | v2 端点与服务装配，单客户端生命周期 |
| `db/schema.sql`、Create `db/migrations/003_semantic_sessions.sql`、`src/core/database.py` | 原表/索引升级，迁移版本 3 |

## Task 1：规则侧过滤图片，保留正文与未来适配信息

**Tests:** `test/surfaces/qq/services/test_parser_service.py`、`test_fact_service.py`、`test/common/services/test_judgement_service.py`、`test_rule_service.py`。

**Interfaces:** QQFactService 增加 `has_readable_text` 已知事实；parser 在 detail 保留可处理文本及可用的 reply_to 证据，content 的 image/type 引用原样保留。`rules/qq.yaml` 增加高于 private/@ 的无可处理正文 ignore 规则；不把单独 @ 号码当作有意义正文。

- [ ] RED：图片-only、@+图片、空白/表情-only 不唤醒；有正文+图片保留文字，@+文字仍可命中；规则 ignore 不删除原图片引用。
- [ ] RED：明确回复 ID 在 parser→规则/路由中不丢；撤回、自发、敏感内容沿用既有过滤，不从媒体占位符恢复正文。
- [ ] Run：`apps/argus/.venv/bin/python -m pytest -q apps/argus/test/surfaces/qq/services apps/argus/test/common/services/test_judgement_service.py apps/argus/test/common/services/test_rule_service.py`。
- [ ] 实现单一可读文本提取，item_text 的旧占位符只供兼容展示，不作为文本资格；不下载图片、不调用 OCR/视觉。
- [ ] GREEN；调整 genuine_question 的描述为“有价值、值得 Agent 看”，不是“必须回答”。主 Agent 可 no_action。

## Task 2：有界 JEV 归属服务

**Tests:** Create `test/common/services/test_session_routing_service.py`；扩展 `test/common/clients/test_gateway_agent_client.py`、test_judgement_service.py。

**Interfaces:**

- async `SessionRoutingService.route(items, *, candidates, recent_context=()) -> tuple[dict, ...]`；每个返回组为 `observation_ids,target_session_id,target_waiting_wake_id,reason`。两个目标最多一个非 null：session 表示续接，waiting_wake 表示合并尚未提交批次，均 null 才表示新语境。身份只能来自输入，候选标签由代码生成，JEV 不输出/创造 UUID；waiting 身份不出 Argus 内部。
- GatewayAgentClient 增加 async `sessions(scope, *, cursor=None, limit=10) -> dict`；单页上限20，本轮路由最多读取两页各10、总20个授权候选。明确 reply_to 只优先使用其中已验证候选，不以本地detail授权跨scope/退场session。已知回复目标不在有界页内时保留pending并持久公平分页水位，后续轮次继续，不静默新建话题或误合并；本轮不新增按session-ID查询端点。
- 候选包含尚待首次执行的批次时，只用现有 wake 身份作为临时候选，尚无 session 不伪造 UUID；合并未提交批次时事务校验其 submission_may_have_run=false。已提交冻结 payload 不变。

- [ ] RED：A/B 交错逐组路由、跨五分钟返回旧 session、短回复依赖前文、明确新话题 target=null、无法确认不混入旧话题；同群不同人能同话题，同人不同问题不自动合并。
- [ ] RED：JEV 恶意/未知标签、输出遗漏/重复 observation、安全过滤后的候选、scope 不符；无 JEV/调用失败保持待处理并安全退避，不能当成“新话题已确定”。
- [ ] Run：`apps/argus/.venv/bin/python -m pytest -q apps/argus/test/common/services/test_session_routing_service.py apps/argus/test/common/clients/test_gateway_agent_client.py apps/argus/test/common/services/test_judgement_service.py`。
- [ ] 实现复用 JevClient 的一次有界分类；代码校验分组完整/互斥、原版本仍有效。不给主 Agent probabilities，不新增向量库/多轮路由 Agent/无限历史输入。
- [ ] GREEN；记录路由 reason 到既有 decision_detail/handoff 作排查证据，不能用模型 confidence 绕过消息权限。

## Task 3：无新表持久归属与等待批次

**Tests:** `test/core/test_handoff_migration.py`、`test/common/repositories/test_observation_repository.py`、test_handoff_repository.py；新增 `test_semantic_assignment.py`。

**Interfaces:**

- async `ObservationRepository.assign_to_session(item_ids, *, session_id, wake_id=None) -> None`；用现有 `observed_items.detail['argus_session_id']` 保存消息的语义归属，保留原 detail 其他键。现有 wake_id 继续代表当前处理批次，不当永久 session ID。
- async `HandoffRepository.attach(wake_id, *, session_id, observation_ids) -> dict`：同 scope/session、当前版本、安全且未被其他批次认领，原子加入未封口批次；不改 wake.payload/fingerprint。相同绑定重复 attach 幂等。
- waiting 批次的 target_session_id 放 wake_requests.handoff；初次新 session 接纳后回填真实关联。正文不复制成 inbox payload。

- [ ] RED：迁移前后表集合完全一致，旧 mock/v1 wake/报告/图片/history 无丢失；迁移并发/重跑幂等，未知版本拒绝。
- [ ] RED：A 运行时 B 可持久 waiting，不能实际启动；scope 状态未知/报告未释放也不启动 B；两个 scheduler 竞争只认领一次。
- [ ] RED：同 session 追加保持旧 frozen payload/指纹；不同 session/权限/旧 revision/撤回拒绝。附加到已 finalize 返回 execution_closed，消息保持原语义归属、未处理状态。
- [ ] Run：`apps/argus/.venv/bin/python -m pytest -q apps/argus/test/core/test_handoff_migration.py apps/argus/test/common/repositories`（PG 仅使用隔离 fixture，无前提记录阻塞，不接正式库）。
- [ ] 写 schema3 迁移：放宽“waiting 也算唯一 active”的索引，实际执行互斥继续由受事务保护的 admission 和 Agent scope 锁保证；v1 可能执行的未知交接仍是屏障。迁移和新建 schema 结果一致。
- [ ] 实现 assign/attach 的 scope→message→wake 一致锁序。禁止仅靠进程内队列认领。GREEN，不执行正式迁移。

## Task 4：主动读取、已读确认和封口不丢待读

**Tests:** test/common/services/test_handoff_service.py、test/common/repositories/test_handoff_repository.py、test/common/api/test_handoff_api.py。

**Interfaces:**

- v2 `available(binding_id) -> {protocol_version:2,pending_count,watermark}`，只计分配给该 session/批次且尚未实际交付的新消息；不输出正文，不授予 exposed。
- 受同一Bearer认证的 `POST /api/argus/internal/sessions/{session_id}/pending` 接收 protocol_version=2/scope，返回 `pending_count,last_related_at`，包括无新binding的waiting。查询只基于现有detail/handoff关联；pending_count仅统计当前boot可调度工作与尚有合法旧binding可完成的工作，不将无新授权的旧boot遗留项算成永久阻塞。Agent在候选/清理/target校验前调用；失败保守保留，不在Gateway复制计数。
- v2 read/history/wait 保留有界页/字符分块，但 wait 的 work 集合只包含语义已分配消息；其他同群历史只有 context。wait 先返回当前binding尚未确认的initial及附加项，不能只查 observed_at>initial_cursor；初始最大时间是采集水位，不等于已读进度。完整初始prompt项由Agent最终ContextReady确认；省略/截断项仍待读。
- v2 `/deliver` 可确认初始及已附加的合法 work 引用，实际 Agent 最终结果完整后由插件调用；确认前不前移消息读取进度。v1 沿用旧端点语义，避免静默变更旧 binding。
- 服务端 item 提供语义展示所需 sender、时间、scope 展示信息、is_self、正文类型；完整 locator/UUID 供内部 client，不强迫模型搬运。

- [ ] RED：available 只计待读，十次查询不产生 delivered；普通 history 看见 B 不使其成为 A 的 work；其他 session 的条目不可 report。
- [ ] RED：HTTP 返回后 renderer 截断/客户端超时、未 deliver，available/读取水位不漏项；显式确认后才减少待读。长正文 next_text_offset 与消息游标独立。
- [ ] RED：finalize 与 attach 竞争：成功附加且未读的后续消息不算 initial unresolved；保留 session 归属并重新排到同 session 后续批次。已读但未作判断的后续消息也不自动 no_action。原 initial 未结案仍保留原 unresolved 语义，accepted 结果不回滚。
- [ ] RED：候选摘要/read/history/wait 的敏感尾部和元数据检测在截断前；跨 boot 不扩大旧 binding work 授权，新批次可关联旧语境但用新授权。旧boot A2 在原批次封口后跨重启只保留历史/context，不自动启动/接管或被新binding报告；新boot有效相关触发可读取有限旧context但不得批量结清旧项，session_pending 不把其算成永久可执行待办。
- [ ] Run：`apps/argus/.venv/bin/python -m pytest -q apps/argus/test/common/services/test_handoff_service.py apps/argus/test/common/repositories/test_handoff_repository.py apps/argus/test/common/api/test_handoff_api.py`。
- [ ] 实现 v2 available/deliver 与按归属的 work 查询；finalize 对未处理附加项解除旧 wake 认领但保留 detail session 归属。对未知结果保守重读，不以通知标记替代送达确认。GREEN。

## Task 5：推进调度、提醒重试与三应用验收

**Files:** scheduler、real_wake_service、gateway clients、main；测试 test_observation_scheduler.py、test_real_wake_service.py、test/app/test_real_handoff.py、共同 fixture `test/common/fixtures/argus_v2.json`。

**Interfaces:**

- scheduler 每轮分别做已有交接 advance、有限新消息路由、空闲 scope 启动 waiting；不再因 active 就无条件 return。
- 同运行 session：先 attach，再 `GatewayAgentClient.notify(wake_id, session_id)`；已停止/closed 则建立后续批次 target=原 session。notify 无正文，重复和丢回执可用原身份重试。
- 同群不同语境：不 notify 当前任务，保持 waiting；Agent capacity/scope_busy 不换身份、不重跑 JEV。

- [ ] RED：A 工具执行时到 A2/B，A2 仅通知可读，B 保持另一语境排队；A 结束后未读 A2 仍在原 session。notify 超时/重放不重复 attach。A 空闲有 waiting A2、B 长时占用时，session pending 查询仍保护 A 的续接，不只依赖运行中 notify。
- [ ] RED：旧 boot/服务重启、submit/notify/attach/finalize 回执未知分别对账；不能因 status 错误将正文标 ignored/processed；跨应用 v2 fixture 规范化一致。
- [ ] Run：`apps/argus/.venv/bin/python -m pytest -q apps/argus/test/common/tasks/test_observation_scheduler.py apps/argus/test/common/services/test_real_wake_service.py apps/argus/test/common/clients/test_gateway_agent_client.py apps/argus/test/app/test_real_handoff.py`。
- [ ] 真实 Gateway/AgentRuntime/Argus 使用本地模型 fixture、隔离 PG/SQLite，无 QQ/MCP 网络。只补生产接线，不能用直接访问另一应用 ORM 代替协议。
- [ ] 完成后 `make test-argus`，再 `apps/argus/.venv/bin/python -m compileall -q apps/argus/src apps/argus/test`、`git diff --check`；三应用集成结果和缺前提项如实记录。

## 回滚与自审

回滚先停止新 v2 路由，不丢既有明细/归属/报告，继续推进未知远端执行的安全停止和记账。不得把待输入静默清为 no_action、降级 mock 或清空数据库。旧可读权限继续独立复核。

自审检查：新图片规则、JEV 归属、同群串行/多批次、仅提醒、最终结果确认、结束竞态、scope/boot/权限与表集合不变均有指定用例；300/86400 秒的候选资格由 Agent 计划拥有，本计划不复制另一份计时器。
