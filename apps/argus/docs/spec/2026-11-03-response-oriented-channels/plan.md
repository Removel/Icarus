# 回应驱动 Channel 与规则语义修正实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让Argus按“是否需要Agent回应/处理、应归属哪个Channel”进行规则判断，支持短对话与跨Session业务状态，并保持确认与更新原子化。

**Architecture:** 协议Plugin持久规范化Observation，规则源提供业务criterion/context，JEV与perception仅做同契约语义适配。固定WorkflowRules handler负责有界深判、Channel事务及既有Event交付；实际Session归属由Agent拥有，Argus只冻结session_key和业务快照。

**Tech Stack:** Python / pytest / pytest-asyncio / SQLAlchemy async PostgreSQL / typesafe-sdk / 既有BaseLLM、Event、Session协议。

**Spec:** [plan-design.md](plan-design.md)。依赖：[Agent计划](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan.md)、[Gateway计划](../../../../gateway/docs/spec/2026-11-03-response-oriented-channels/plan.md)。

**Status:** 用户已明确授权“写完之后就可以开始开发”；已按不新增state的修订开始实施。当前源代码修改尚未提交，运行服务与真实消息验收不在自动执行范围；详见[执行记录](execution.md)。基线43312c4。

## Global Constraints

- Channel是回应/处理业务上下文，不等于来源或Session；群聊不要求@，也不默认每条都回应。
- 规则业务语义不得写死在model_provider；供应商协议不得泄漏到规则、Runtime或ReActAgent。
- 正式Agent继承普通Session能力，规则与perception分类保持只读；Argus不发QQ消息。
- 保持规则每次最多3个能力调用、semantic最多1次、能力最长5秒；较慢perception在隔离规则与DB锁之外执行。
- JEV原生confidence门槛0.7、普通match门槛0.5；perception单修订最多1次、20秒、2048 tokens、自动重试0。
- discovery criterion≤4000字符，text≤16000字符，context≤16384 UTF-8 JSON字节，history≤8条且不晚于当前事件；候选≤20个、每条≤4096字节。不得靠丢候选或静默截断关键消息伪造确定判断。
- 不新增业务state字段；沿用goal/progress、规则和Session历史，省略已有提交字段保持不变。
- 不删旧数据、不自动重评ignored/exhausted；真实DB迁移、模型调用、平台发送及服务重启分别确认。
- 测试依次聚焦→应用整套→编译/diff，保留所有实际失败。未获新授权不创建分支/worktree、提交或推送。

## Review Focus

1. 已鉴权心跳/notice是正常无操作而非格式错误；非法签名仍拒绝，不调用模型。
2. 自己发出的私聊必须使用真实对端target_id；人工self消息与Agent回复回声不能混淆。
3. 高置信度但错误业务criterion不会被无意义反复兜底；JEV/perception必须获得同一规则任务和完整有界上下文。
4. 后续内容归入已有active Channel后不能因恒False bootstrap长期失声，也不能绕过其规则重复投递。
5. next_session或外部发送完成后响应丢失：旧binding、版本和session_key必须稳定，不能在commit重试时生成多个Session或重放动作。

---

## 文件职责与依赖顺序

- `plugins/protocols/qq_protocol/plugin.py` / `application.py`：事件接收/规范化/有限错误码。
- `plugins/workflow_rules/discovery.py` / `plugin.py`：规则criterion、可信上下文、深判触发和后续规则。
- `model_provider/semantic.py` / `discovery_fallback.py`：问题编码和模型结果验证，不持有业务政策。
- `contracts/commit.py` / `infrastructure/store.py`：next_session、冻结session_key及已有业务字段的原子提交；不为业务state修改Channel模型或快照。
- `plugins/icarus_bridge/client.py` / `plugin.py`：共享DTO薄适配和保守重放。
- 不新增Argus业务列或state迁移脚本；Session关联持久化由Agent计划拥有。

顺序：本计划Task1/2可先做；Agent Task1/2与Gateway共享DTO就绪后完成本计划Task3/4；最后跨应用确定性验收。每个任务都以失败测试开头，不自动提交。

### Task 1: QQ正常事件应答与消息规范化

**Files:** Modify `apps/argus/src/argus_app/plugins/protocols/qq_protocol/plugin.py`、`apps/argus/src/argus_app/application.py`；Modify `apps/argus/test/argus_app/plugins/protocols/qq_protocol/test_plugin.py`、`apps/argus/test/argus_app/test_host_integration.py`。

**Interfaces:** 保留 `QQProtocol.receive(payload: dict)` 产生message Observation；增加普通helper `normalize_message(payload: dict) -> tuple[str, str, dict, dict]`，依次为source_key/message_id/content/detail。HTTP处理器在鉴权后对支持忽略的正常事件返回 `{accepted:true,ignored:true,reason:"unsupported_event_type"}`，不调用receive。明确消息错误使用 `DomainError` 的有限码并由HTTP映射422；认证失败401不变。

- [ ] **Step 1: 写失败测试。** `test_signed_heartbeat_is_acknowledged_without_ingest`：HTTP200、Observation/模型调用均0；notice/request同样断言。`test_bad_signature_is_rejected_before_event_dispatch`：401。现有“非message均拒绝”测试拆成Plugin规范化测试与HTTP事件策略测试。
- [ ] **Step 2: 运行RED。** `bash apps/argus/scripts/test.sh apps/argus/test/argus_app/plugins/protocols/qq_protocol/test_plugin.py apps/argus/test/argus_app/test_host_integration.py -q`。Expected：新增HTTP应答/自己消息规范化断言失败，不是环境错误。
- [ ] **Step 3: 实现规范化。** 保存text、message_type、sender_id、有界segments；detail保存self_id/is_self/peer_id/reply_to/schema_version。self私聊target_id决定对端；缺可靠对端返回 `qq_peer_missing`。未知post_type、缺message_id、非法account返回具体有限错误，签名仍覆盖原body。不要从CQ正文解析出新的技术授权。
- [ ] **Step 4: 补充边界。** `test_self_private_uses_target_peer`、`test_self_private_without_peer_is_rejected`、`test_group_identity_is_not_private_sender`、`test_segments_preserve_reply_and_mentions`、`test_unsupported_event_never_consumes_semantic_budget`；账号/群/私聊隔离及重复消息修订行为保持。
- [ ] **Step 5: GREEN。** 重跑Step2命令全部通过；代码diff不得包含平台发送、下载媒体或群管理动作。

### Task 2: 规则驱动的回应语义与perception深判

**Files:** Modify `apps/argus/src/argus_app/plugins/workflow_rules/discovery.py`、`plugins/workflow_rules/plugin.py`、`model_provider/semantic.py`、`model_provider/discovery_fallback.py`、`contracts/models.py`、`plugins/rule_capabilities/observation_query/plugin.py`（只复用/验证现有source_bounds与discovery_observation_id的历史边界，不增加公共position字段）；Modify对应 `test_semantic_discovery.py`、`model_provider/test_discovery_fallback.py`、`plugins/workflow_rules/test_discovery.py`、`test_discovery_fallback.py`。

**Interfaces:** discovery参数为 `{mode:"discovery",text,criterion,context,candidates}`；可信context含 `conversation`、`recent_history`。broker从规范化Observation生成conversation并复核history边界，覆盖脚本伪造的技术字段；RuleEngine继续剥离event.detail，不把凭据暴露给脚本。默认规则从公开channel上下文读取conversation/history，明确构建criterion。`DiscoveryDecision.review_required: bool=False`仅允许action=defer；持久最终路由四种不变。

`DiscoveryFallback.__call__(self, text: str, candidates: list[dict], *, criterion: str, context: dict) -> DiscoveryDecision`。固定handler保存同轮semantic实际请求；若规则只显式请求深判而没调用semantic，使用默认规则公开的criterion与宿主上下文，不凭空拼另一套业务目标。外来request不能改变候选和来源。

- [ ] **Step 1: 写失败测试。** 捕获真实backend请求，断言rule提供的criterion/context进入JEV与fallback，text/candidates及来源身份一致；模型适配器不再额外附加“闲聊必须ignore”。`test_private_greeting_can_request_new_channel`用typed确定性分类替身验证路由契约，不用fake模型证明自然语义质量。
- [ ] **Step 2: RED。** `apps/argus/.venv/bin/python -m pytest apps/argus/test/argus_app/test_semantic_discovery.py apps/argus/test/argus_app/model_provider/test_discovery_fallback.py -q`；Expected：缺criterion/context或旧硬编码业务提示导致断言失败。
- [ ] **Step 3: 实现规则criterion及上下文。** 把Channel概念和回应标准写入默认规则源，使用现有history只读能力（最多8条、按当前position截止）。宿主注入conversation，不改变稳定System Prompt。验证JSON/大小，超大输入明确未决，不默默截到一个看似可判断的前缀。
- [ ] **Step 4: 复用持久深判。** 低confidence或合法review_required只认领一次；非语义transport/auth错误仍不兜底。输出review_required在持久应用前归一化，不成为模型授权或新routing identity。复用既有claim/finish/fence，不返还失败请求预算；reason不带原始HTTP异常。
- [ ] **Step 5: 后续回应规则测试并实现确定策略。** 创建Channel固定安装规则源中的 `DEFAULT_RESPONSE_RULE`，该规则只处理后续unread，不重复首条seed；删除恒False bootstrap作为新Channel默认值。`test_first_active_commit_omitting_rule_keeps_conversation_responsive`必须真实跑首条new→首次commit保持active且不传rule_source→同主题第二条，断言经保留默认规则passed且无重复seed；另测显式替换规则生效、completed不自动重开。`test_existing_does_not_force_seed_or_wake_paused`确保不绕过规则/paused限制；`test_same_group_new_topic_remains_discoverable`覆盖新话题。self人工请求/可能回声作为有标签语义样本或明确defer测试，不宣称当前没有可信发送账本时已经确定性识别作者。
- [ ] **Step 6: GREEN。** `bash apps/argus/scripts/test.sh apps/argus/test/argus_app/test_semantic_discovery.py apps/argus/test/argus_app/model_provider/test_discovery_fallback.py apps/argus/test/argus_app/plugins/workflow_rules/test_discovery.py apps/argus/test/argus_app/plugins/workflow_rules/test_discovery_fallback.py -q`；Expected全部通过。

### Task 3: 已有业务进展与下一Session选择原子提交

**Files:** Modify `apps/argus/src/argus_app/contracts/commit.py`、`infrastructure/store.py`；Modify `apps/argus/test/argus_app/infrastructure/test_commit.py`、`test_store.py`、`test_continuous_discovery.py`；Create `apps/argus/test/argus_app/infrastructure/test_response_channel_sessions.py`。不为本任务修改Channel业务模型、ChannelSnapshot或新增迁移脚本。

**Interfaces:** CommitPayload仅增加可选next_session(reuse/new)，progress/rule/source/lifecycle沿用已有契约。`begin_round`首次生成runtime.session_key，并在round冻结同一key。`_apply`消费next_session而不写成业务列；new只在成功commit后产生下一key，重放固定回执不再生成。共享契约由Agent Task2提供：session_key只在SessionRequest/WakeRequest内，ChannelSnapshot维持现有goal/progress/规则/生命周期字段，不新增state。

- [ ] **Step 1: 写失败测试。** `test_commit_updates_progress_and_next_session_atomically`：冻结内容全部确认、progress与规则更新、下一key改变；缺/重复ref、非法next_session、旧version导致所有字段不变。已有字段省略保持、null拒绝。
- [ ] **Step 2: RED。** `bash apps/argus/scripts/test.sh apps/argus/test/argus_app/infrastructure/test_response_channel_sessions.py -q`；Expected因next_session/冻结key未支持失败。
- [ ] **Step 3: 实现。** 保留现有progress/规则业务version比较和snapshot，next_session只改变执行metadata而不隐式结束Channel；每次新round明确冻结key，已分配session不能被交付重试替换。不新增state JSON或数据库列。
- [ ] **Step 4: 既有数据兼容测试。** 现有Channel.runtime缺session_key时只认领一次并保持goal/progress/规则/历史不变；重复初始化稳定。断言Argus两表及业务列集合不因本任务增加state，不执行DDL迁移或重建表。
- [ ] **Step 5: 回归。** `test_commit_retry_creates_one_next_key`、`test_new_session_keeps_channel_progress_and_rule`、`test_completed_channel_not_reopened`、`test_old_ignored_observations_not_replayed`。完整Session关联由Agent SessionStore拥有、journal只审计，不放Channel.runtime数组。
- [ ] **Step 6: GREEN。** 运行本任务所有测试文件，再整套Argus；Expected全部通过。无Argus业务schema迁移步骤。

### Task 4: 薄Bridge交付、保守恢复与端到端验收

**Files:** Modify `apps/argus/src/argus_app/plugins/icarus_bridge/client.py`、`plugin.py`；Modify `apps/argus/test/argus_app/plugins/icarus_bridge/test_plugin.py`、`test_discovery_e2e.py`、`test_closed_loop.py`、`test_host_integration.py`、`test/real_scenarios.py`、`test/manual_real_scenarios.py`、`test/argus_app/test_real_scenario_harness.py`；Update README/config example及本feature文档（实现后才写arch.md）。

**Interfaces:** `BridgeClient.ensure_session(channel_id, request_id, session_key) -> str`；`wake`转发新增冻结session_key与现有Channel快照。status含replay_safe；缺字段按false，不因旧服务未返回就允许重放。任务终态且commit未定时，只有明确replay_safe才reconcile；否则保留round并有限记录execution_outcome_unknown，不循环启动Agent。

- [ ] **Step 1: 写失败测试。** `test_delivery_reuses_frozen_session_selection`：同wake重试Session不变；`test_failed_executed_round_without_commit_is_not_replayed`：发送后崩溃，不出现第二Agent Task；`test_next_session_keeps_old_callback_identity`验证下一轮新Session与旧回执不交叉。
- [ ] **Step 2: RED。** 聚焦bridge/e2e测试，Expected旧session API或自动reconcile断言失败。
- [ ] **Step 3: 最小接线。** 不引入平台发送客户端；通过Gateway调用Agent选择服务。完整能力Session的普通assistant输出不能当业务确认。沿用receipt查询与未定保留，启动失败前未执行和执行后未知分开记录。
- [ ] **Step 4: 确定性闭环。** 在隔离PG＋真实Host/Gateway/SessionRuntime＋本地mock SnowLuma MCP中跑：私聊招呼→new→MCP send成功→commit；连续聊天→existing且复用Session；大任务new key→第二Session且goal/progress/规则保留；非零retcode不能宣称已回复；send成功commit回执丢失无重复发送。所有消息为合成，平台不外发。
- [ ] **Step 5: 付费验收计划。** 增加固定标签回应发现样本：私聊招呼/群聊相关未@/第三人闲聊/引用/新旧话题/self回声。取得授权后最多12次JEV、6次perception；如未触发真实兜底如实报告。真实QQ测试另确认账号、单个会话、内容和发送次数，不发入任意群做实验。
- [ ] **Step 6: 最终验证。** 最小文件→`bash apps/argus/scripts/test.sh`→`make test-agent`→`make test-gateway`→`make typecheck-agent`→compileall/diff。列出两个现有Agent drain失败是否仍在；未获授权不修改它们或把失败屏蔽。
- [ ] **Step 7: 文档和交付。** 更新当前实现arch（仅在实现后）与运行说明，保留历史失败/概念变更。记录哪些步骤未执行、哪些模型/平台只mock验证；不自动提交、推送、重启或重放生产消息。

## 自查与实施批准

覆盖映射：协议问题→Task1；规则概念/上下文/perception→Task2；状态与选择键→Task3；交付/确认/发送恢复→Task4。Review Focus五项分别被Task1、Task1/2、Task2、Task2/4、Task3/4测试覆盖。共享DTO和Agent实际Session身份只消费Agent计划，Gateway仅做薄转发。

本轮按用户确认取消新增业务state字段及对应迁移，沿用progress/规则/历史。session_key/next_session接口、保守未知动作不重跑仍是作者提出的具体实现选择；需要与设计一并审阅。用户明确说开始开发前，不执行任何实现步骤。

### 方案可实施性检查记录

2026-11-03做过一次独立只读文档检查；反馈为四项作者可确定的契约缺口，无需新增产品选择。已在文档中处理：active Channel确定安装DEFAULT_RESPONSE_RULE；Session选择/删除键由Agent原有数据库中的具体关联表保存；旧会话采用以首次持久关联为一次性门槛；self回声缺可信发送关联时明确限制，不把is_self/语义推断当确定作者证据。同步统一了conversation/history字段及session_key所属DTO。后续按用户确认取消业务state字段，不再扩展ChannelSnapshot业务字段或创建Argus迁移脚本。没有为了检查而修改源代码或运行配置，也未新增真实模型/平台测试。
