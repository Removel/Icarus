# Argus 决策脚本与原子提交实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 不自动启动多代理工作流；用户当前要求先编写文档，实施需另行确认。

**Goal:** 实现只用于决策的文件级Python规则、安全隔离、纯静态检查和一次原子业务提交，并持续发现新事项。

**Architecture:** 沿用协议持久Observation→固定Workflow handler→两表Store及正常Bridge/Agent链。检查/审查/制品准备在事务外，最终消息结果、业务字段、规则引用和回执在短事务中共同提交；脚本通过只读能力broker运行于无网络/凭据的隔离单元。

**Tech Stack:** Python>=3.11、FastAPI、SQLAlchemy/asyncpg、Icarus PluginRuntimeHost、Linux bubblewrap/libseccomp、pytest。

**Spec:** [设计](plan-design.md)、[Agent设计](../../../../agent/docs/spec/2026-11-02-decision-script-atomic-commit/plan-design.md)、[共同边界](../../../../../spec/2026-11-02-decision-script-atomic-commit.md)。

## Global Constraints

- 恰好observed_items/channels两张Argus表；源码只存在受管不可变文件，不新增确认/提交/审查表。
- main(event, channel)同步入口；源码32768字节，AST4000节点，输入2MiB，IPC行131072字节，输出/能力结果131000字节。
- CPU2秒、内存192MiB、总墙钟默认10秒且最大30秒、能力请求最长5秒；每次最多3次能力调用且semantic最多1次。
- lint不执行源码、能力或LLM；隔离不可用拒绝执行，不降级宿主exec。
- Agent只提供results及可选progress/source_keys/rule_source/lifecycle；技术字段由运行时产生，源码和状态一次业务commit。
- 共享PG使用独立Argus库/受限账号；测试仅随机*_test数据库，不操作生产数据。
- ReActAgent无状态，Event/Capability边界不变；不把动态业务背景写入System Prompt。
- 不自动提交/推送/安装系统依赖；后续每任务提交仅在用户授权时进行，无Co-Authored-By。

## Review Focus

- DB已提交但绑定已清空、HTTP响应丢失：仍能查回原成功回执，不重复业务。
- 正常Python辅助函数/try/标准库与恶意反射混合：正常代码通过、危险权限受静态和OS双重阻断。
- 源码顶层/default/decorator含隐藏行为：静态检查不执行，隔离锁定先于任何源码执行。
- 同群新事项与并发同事项：不被订阅遮蔽，不产生重复Channel，不重放旧修订。
- 能力服务无权限/超时与预算耗尽：保持错误可见，不变成ignore或假成功。

---

## 文件与职责

保留engine负责宿主执行/预算、files负责不可变制品、worker负责隔离内入口。新增`rule_sdk/checks.py`负责纯静态解析/策略，`rule_sdk/isolation.py`负责Linux执行单元，`model_provider/safety.py`负责可选无工具LLM风险审查，`contracts/commit.py`负责复用严格Fact基类的服务端业务提交契约。不把安全策略、协议与Store塞进一个新框架。

协议/事务先交付可测试后端，再接Agent/Gateway；普通Python脚本**必须等隔离门禁就绪才能启用**。后端单元测试使用可信fake launcher，不作为正式隔离验收。Agent工具解耦是独立子项目，见其计划。

### Task R1：提交契约和原子Store

**Files:** Create `apps/argus/src/argus_app/contracts/commit.py`；Modify `contracts/__init__.py`、`infrastructure/store.py`；Test `apps/argus/test/argus_app/infrastructure/test_commit.py`、`test_store.py`（其余路径均相对apps/argus/src/argus_app）。

**Interfaces:** Consumes现有Fact/Observation/Channel和两表；Produces `CommitPayload(expected_version, results, progress?, source_keys?, rule_path?, rule_hash?, lifecycle?)`、`Store.apply_control(observation_id: str) -> dict`对`agent.commit_requested`的原子支持，以及只读`Store.commit_status(binding_id: str, request_id: str) -> dict`（state=not_found/pending/applied/rejected，已有终态附result）。服务端results使用observation_id，Agent表面ref在适配层解析；复用现有严格基类，不创建重复Channel/Observation。

- [ ] **Step 1：新增失败测试。** `test_commit_applies_results_state_rule_and_close_atomically`断言一次提交后状态/规则hash匹配、version只增一次、round为空、成功回执在同事务；`test_commit_unchanged_closes_without_version_increment`断言不变更也收尾。参数化非法最后一项结果/生命周期/来源/版本，断言Channel与pending完全不变。
- [ ] **Step 2：运行red。** `bash apps/argus/scripts/test.sh apps/argus/test/argus_app/infrastructure/test_commit.py -q`，预期缺契约/新控制类型失败；若PG缺失则先修复测试环境，不能把fixture错误当red证据。
- [ ] **Step 3：实现契约与事务。** 同事务先查控制result再查binding，验证完整frozen集合/送达/expected_version；copy验证后一次_save与回执保存。取消旧confirm可独立最终成功的路径；reconcile不丢未commit项。无规则变更保留旧制品。
- [ ] **Step 4：补并发和回滚测试并green。** `test_commit_receipt_survives_closed_binding`、`test_commit_status_four_states_are_read_only`、`test_db_failure_rolls_back_channel_and_receipt`、`test_competing_commit_changes_one_version`、`test_terminal_without_commit_retains_retryable_pending`；同Task命令通过后跑infrastructure目录。成功回执重复不需活跃binding；未知结果不标成功。
- [ ] **Step 5：检查diff。** `git diff --check`；如已获本任务提交授权，按「feat(argus): commit round results and channel changes atomically」保存实现与测试。

### Task R2：文件级规则和纯静态检查

**Files:** Create `rule_sdk/checks.py`；Modify `rule_sdk/engine.py`、`files.py`、`__init__.py`；Test `apps/argus/test/argus_app/rule_sdk/test_checks.py`、`test_engine.py`。

**Interfaces:** Produces `check_source(source: str) -> RuleCheckResult`、`RuleCheckResult(passed, source_hash, policy_version, diagnostics)`、`RuleDiagnostic(code, message, line, column, stage)`；files.check调用同一检查入口。所有类型放checks.py，不引入独立类型框架。

- [ ] **Step 1：新增失败测试。** `test_file_helpers_constants_try_and_safe_import_compile`覆盖re/json辅助函数、isinstance、try；`test_check_never_executes_top_level_or_entry`以会产生副作用的源码断言只返回诊断、不产生文件/网络/broker调用。允许普通helper参数，不允许main默认参数/装饰器/异步。
- [ ] **Step 2：运行red。** `apps/argus/.venv/bin/python -m pytest apps/argus/test/argus_app/rule_sdk/test_checks.py -q`，预期缺check_source失败。
- [ ] **Step 3：实现静态检查。** ast.parse＋compile、固定main声明和模块级纯声明策略；支持设计8个计算型标准库；拒绝动态执行、危险导入、反射、dunder和capability覆盖。诊断含稳定code和位置；支持Python3.11，不无保护访问type_params。
- [ ] **Step 4：运行边界测试。** `test_rejects_hidden_default_and_decorator_calls`、`test_rejects_reflective_and_dynamic_imports`、`test_utf8_size_and_ast_budget`，断言32768字节与4000节点边界，不只按字符计。运行checks目录及已有SDK用例；不在此步把普通Python执行开关接到旧worker。
- [ ] **Step 5：diff检查及授权后提交。** 建议「feat(argus): statically check file-level decision scripts」。

### Task R3：隔离执行与只读broker

**Files:** Create `rule_sdk/isolation.py`；Modify `rule_sdk/worker.py`、`engine.py`、`config.py`、`application.py`、`pyproject.toml`、`scripts/install.sh`；Create `apps/argus/scripts/check-isolation.sh`；Test `apps/argus/test/argus_app/rule_sdk/test_isolation.py`、`test_engine.py`。

**Interfaces:** Produces `IsolationLauncher.probe() -> IsolationStatus`、`IsolationLauncher.start(source: str, event: dict, channel: dict) -> IsolatedWorker`，worker提供有界async send/receive/terminate_wait接口；`RuleEngine.run/evaluate`签名保持，内部通过launcher运行。具体FD封装由实施者按asyncio subprocess实现，不引入通用sandbox服务。

- [ ] **Step 1：新增失败测试。** `test_missing_isolation_never_falls_back_to_host`、`test_isolation_blocks_network_host_files_env_and_process_creation`、`test_kill_reaps_entire_worker`、`test_stdout_cannot_forge_capability_protocol`；预期缺launcher或OS断言失败，不能把无bwrap跳过当安全通过。
- [ ] **Step 2：准备可重复隔离环境。** 在获系统安装授权的Linux/WSL2提供bwrap和libseccomp绑定，app-owned声明依赖；`bash apps/argus/scripts/check-isolation.sh`返回0才运行真实隔离测试。运行时构造最小只读Python/标准库/worker树，不挂载整个venv/仓库。
- [ ] **Step 3：实现launcher与worker。** clearenv、各namespace、无网络/宿主目录/凭据；预加载安全模块、安装seccomp后再exec规则；IPC专用FD；CPU2秒/192MiB/进程限制/10秒墙钟与输出限制。错误code isolation_unavailable/rule_timeout/rule_limit，不降级宿主exec。
- [ ] **Step 4：验证正常能力与恶意输入。** main/helper/re/try成功；危险脚本即使绕过静态fake检查也无法读秘密、联网、写文件或fork；history/semantic经宿主重验scope，注入控制kind/私有name失败；3次与1次semantic边界、超时/取消都回收。运行SDK目录，集成测试需明确实际OS保护，而不只断言命令参数。
- [ ] **Step 5：应用健康接线和diff。** 入库可用但规则执行不可用时health明确分项，恢复扫描保留待办；授权后提交「feat(argus): isolate decision script execution」。

### Task R4：静态check、可选安全审查和制品准备接入

**Files:** Create `model_provider/safety.py`；Modify `rule_sdk/files.py`、`plugins/workflow_rules/plugin.py`、`plugins/protocols/agent_protocol/plugin.py`、`config.py`、`application.py`；Test `apps/argus/test/argus_app/plugins/workflow_rules/test_controls.py`、`test_commit_controls.py`、`test_safety_backend.py`。

**Interfaces:** Produces `RuleSafetyReview.review(source: str, goal: str, *, source_hash: str, policy_version: str) -> SafetyVerdict`；disabled时不调用模型，enabled时allow/deny/uncertain独立typed响应。commit控制复用`apply_control`，source剥离后将checked path/hash交Store；check返回静态报告，不含decisions。

- [ ] **Step 1：新增失败测试。** `test_rule_check_never_calls_engine_or_models`断言check不试跑/不审查；`test_commit_rechecks_exact_source_and_rejects_wrong_hash`；`test_file_failure_leaves_old_state_and_rule`，包括fsync失败。
- [ ] **Step 2：运行red。** `bash apps/argus/scripts/test.sh apps/argus/test/argus_app/plugins/workflow_rules -q`，确认失败是旧行为/新接口缺失。
- [ ] **Step 3：实现接线。** check与commit都调用R2静态检查；commit可在事务外R4审查，allow后fsync文件和目录，之后R1短事务。API handler不能绕过协议入库，旧confirm/update/finish kind拒绝。新制品未激活不算更新成功。
- [ ] **Step 4：覆盖审查与恢复。** `test_review_disabled_makes_zero_calls`、enabled的deny/uncertain/timeout拒绝、审查源码含诱导指令不取得工具/凭据、相同hash策略结果可复用；`test_commit_crash_after_prepare_before_db`保旧状态，`test_replayed_commit_does_not_rerun_review_after_receipt`成功回执先返回。新增`GET /agent/commits/{binding_id}/{request_id}`只查已存控制记录，返回not_found/pending/applied/rejected；测试四态均不调用ingest/handler、不改Channel，关闭binding仍可查。用fake模型做确定性测试，不启动付费调用。
- [ ] **Step 5：完整Workflow+Store回归与授权后提交。** 建议「feat(argus): validate and publish decision rules through one commit」。

### Task R5：Wake快照与能力元数据

**Files:** Modify `plugins/icarus_bridge/plugin.py`、`client.py`、`application.py`、`contracts/capabilities.py`；Create `rule_sdk/guide.py`；Test `apps/argus/test/argus_app/plugins/icarus_bridge/test_plugin.py`、`test_application.py`。共享wire变更见Gateway任务，必须先联合对齐。

**Interfaces:** ProducesWake的`channel_snapshot`及`rule_sdk`元数据；snapshot字段严格按Agent设计，metadata为SDK/policy版本、能力schema/configured/预算；GET channel_read同样返回元数据。字段不含runtime、endpoint秘密或未验证healthy标志。

- [ ] **Step 1：新增失败测试。** `test_wake_contains_exact_version_snapshot_and_rule_source`、`test_capability_metadata_does_not_claim_remote_health`、`test_snapshot_envelope_never_truncates_frozen_items`，保留items384KiB/包络512KiB约束。
- [ ] **Step 2：运行red。** Bridge插件和application聚焦文件；需要PG则使用app测试脚本。
- [ ] **Step 3：实现snapshot与单一guide描述。** 从冻结round版本一致的Channel读快照；并发不一致拒绝/重新开始未提交wake，不能把新版本状态冒充旧版本。与Gateway共享DTO联合校验。六能力的入出说明由现有类型/插件公共契约复用，不另建路由系统。
- [ ] **Step 4：green与保密测试。** 缺memory/semantic配置反映configured=false，不能从message覆盖；rule_source hash正确，history limit50工具面一致；existing HTTP/Bridge用例通过。
- [ ] **Step 5：授权后提交。** 建议「feat(argus): deliver bounded channel context and rule guidance」。

### Task R6：持续发现与事项幂等

**Files:** Modify `plugins/workflow_rules/discovery.py`、`plugin.py`、`contracts/models.py`、`infrastructure/store.py`、`model_provider/semantic.py`、`plugins/rule_capabilities/semantic_judgement/plugin.py`；Test `apps/argus/test/argus_app/infrastructure/test_discovery.py`、`plugins/workflow_rules/test_discovery.py`、`test_discovery_e2e.py`、`test_semantic_backend.py`。

**Interfaces:** Produces `DiscoveryDecision(action: ignore|existing|new|defer, reason, channel_id?)`及 `Store.record_discovery`新契约；new的title/goal/topic_key由handler按设计的规范化文本规则生成。扩展semantic的mode=discovery，单次typed Choice＋Noul请求返回可信候选/new/ignore，score<0.7为defer；普通match模式不变。runtime仅增加有界discovery元信息。两表不变，候选摘要最多20项、每项4096字节，过量defer，来源scope固定。

- [ ] **Step 1：新增失败测试。** `test_existing_subscription_does_not_hide_new_topic`、`test_paused_channel_does_not_own_all_source_topics`、`test_same_topic_concurrent_events_reuse_channel`、`test_processed_revision_never_rediscovers_after_completion`。
- [ ] **Step 2：运行red。** `bash apps/argus/scripts/test.sh apps/argus/test/argus_app/infrastructure/test_discovery.py apps/argus/test/argus_app/plugins/workflow_rules/test_discovery.py -q`。
- [ ] **Step 3：实现发现与订阅解耦。** 新脚本main输出DiscoveryDecision；移除来源级owned抑制和decide中的discovery忽略副作用；候选context/历史边界固定，来源+topic_key在同协调锁下复核并复用；existing不绕过active规则链、不唤醒paused。默认发现规则升级为可利用候选与一次semantic的明确业务判断，服务不可用不当ignore。
- [ ] **Step 4：补边界与E2E。** 两事项同群可建两个Channel，同事件重投不建第二个；相同活动topic并发复用、分类期间候选变化重新判定；过时修订不能建事；候选超20明确背压；错误5/30秒退避最多3次、重复通知不绕过；typed Choice只能选可信ID，Noul<0.7为defer；completed的旧事件不重建，新请求可同key新建、不自动重开旧Channel。跑从零与已有群的真实PG/TCP确定性E2E。
- [ ] **Step 5：授权后提交。** 建议「feat(argus): discover new topics alongside existing channels」。

### Task R7：真实JEV、自然场景与预算化验收

**Files:** Modify `test/manual_real_model.py`、`model_provider/semantic.py`、`config.example.toml`、`README.md`；Create `test/manual_jev_semantic.py`、`apps/argus/test/argus_app/test_manual_budget.py`；Update既有验证记录时另加新日期段，不抹掉旧失败。

**Interfaces:** manual CLI增加 `--max-agent-requests=120 --max-scenario-requests=50 --max-semantic-requests=64 --max-safety-requests=16 --scenario-timeout=600`；保留显式--authorize-real-model与独占输出目录，模型和semantic计数独立，所有上游重试算attempt。

- [ ] **Step 1：新增失败测试。** `test_budget_blocks_before_next_upstream_attempt`、`test_auth_failure_stops_without_paid_retry_loop`、`test_default_semantic_uses_configured_typed_backend`、`test_summary_distinguishes_agent_semantic_safety_usage`；不读真实凭据、不联网。
- [ ] **Step 2：运行red/实现预算接线/green。** 使用fake transport验证计数与停止，去掉默认DeepSeek semantic替身；规范化context JSON，明确base_url/model来源，不猜key所属服务。配置没有endpoint/model就显式报告路由选择，不宣称已验证。
- [ ] **Step 3：在独立授权和正确服务路由下小规模JEV预检。** 首条鉴权/权限失败立即停；成功后才跑正例/否定/引用/无关/同义/条件等样本。报告真实match/score/耗时，不能用供应商HTTP200代替业务正确。
- [ ] **Step 4：自然场景端到端。** 前置R1-R6、Agent、Gateway任务通过；合成输入只描述业务，不教工具顺序、不硬写关键词实现。覆盖从零、同群新事、跨源跟进、预算中断、提交响应丢失；核对持久Channel/规则/commit回执和实际semantic调用。
- [ ] **Step 5：完整验证和文档同步。** 最小测试→`bash apps/argus/scripts/test.sh`→`make test-agent`→`make test-gateway`→受影响`scripts/tests`→compile/type/diff。隔离缺失/PG缺失/JEV权限错误逐项记阻塞；代码完成后再更新arch.md、rule-guide、contracts和README，不能提前宣称完成；授权后分实现/验证文档提交。

## 执行门禁与交付

推荐顺序R1→R2→R3→R4，与Agent工具解耦可独立；R5与共享Wake/Agent接线联合交付，R6之后跑R7。未完成R3不发布普通Python规则；未完成Agent/Gateway契约对齐不切换生产式新commit入口。每任务最小red→green后再应用套件。禁止为通过扩大权限、跳过安全验收或修无关失败。

本计划只定义待实施步骤。当前设计/计划无需付费调用或环境安装；当前用户授权的Git操作仅保存先前开发快照，新增设计文档暂不提交。
