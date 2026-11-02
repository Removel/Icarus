# Agent 原子提交适配实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 当前只编写文档，实施需另行确认；不自动扇出子代理。

**Goal:** 让Agent自主取证并编写文件级决策脚本，一次提交业务结果，运行时处理技术参数和幂等恢复。

**Architecture:** 保持SessionRuntime/AgentPlugin/ReActAgent正常执行链，Bridge提供可信动态快照，Argus插件开放五个业务工具并复用授权记忆只读工具。SessionStore沿用callback/receipt日志，不新增应用表；最终原子性由Argus负责。

**Tech Stack:** Python>=3.11、现有严格Pydantic WireModel、httpx、Icarus工具/SessionStore、pytest。

**Spec:** [本应用设计](plan-design.md)、[共同约定](../../../../../spec/2026-11-02-decision-script-atomic-commit.md)。

## Global Constraints

- Agent只填results及可选progress/source_keys/rule_source/lifecycle；不暴露request_id/expected_version/hash/path等技术字段。
- results恰好覆盖当前1..20条冻结消息；source_keys最多16、history最多50、源码UTF-8最多32768字节、progress最多8192字符。
- 动态业务背景只进当前User Prompt，System Prompt只修正通用工具机制的错误描述。
- 凭据不进prompt/source/tool结果/journal；memory仅开放memory_recall/memory_read，不开放任意写工具。
- 版本来自明确可见快照/成功read，不盲刷新；成功回执优先于活动绑定检查。
- 不自动提交、推送、调用付费API；获授权的任务提交不加Co-Authored-By。

## Review Focus

- 取消后服务器提交已成功但响应未返回：基础设施可对账，不能重做业务或假称撤回。
- 当前轮完整消息被压缩/裁剪：不能用旧Session内容冒充delivered。
- 两个task复用Session或同payload：回执严格按binding/task隔离。
- 记忆中的旧结论或恶意消息：不能扩权或绕过服务端版本。
- 工具Schema允许值与HTTP后端不一致：边界统一、错误结构化而非循环试错。

---

### Task A1：共享Wake与动态上下文

**Files:** Modify `apps/agent/src/agent_app/argus_protocol.py`、`application/services/argus_bridge.py`；Test `apps/gateway/test/test_argus_bridge.py`、Create `apps/agent/test/agent_app/plugins/argus/test_guidance.py`。

**Interfaces:** WakeRequest新增channel_snapshot与rule_sdk严格对象；snapshot字段由本设计冻结，复用现有WireModel/Id，不复制Observation/Channel。GUIDANCE渲染版本化SDK元数据及main示例，channel_snapshot和messages有清晰证据边界。

- [ ] **Step 1：写失败测试。** `test_wake_snapshot_requires_matching_channel_and_version`、`test_guidance_contains_main_static_check_and_single_commit`、`test_context_has_no_credentials_or_runtime_binding`；总包络512KiB、items384KiB完整保留。
- [ ] **Step 2：运行red。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/argus/test_guidance.py -q`；HTTP联合测试用具依赖的Gateway环境，确认不是缺依赖导致的假red。
- [ ] **Step 3：实现DTO和prompt。** 与Argus R5/Gateway任务同版本交付；不强制channel_read，不要求每轮搜索；记录本task snapshot_version但不给模型填写。复用原最终视图delivered检测，不降低完整性条件。
- [ ] **Step 4：green与视图测试。** 当前prompt被裁剪、旧Session有相似内容、SDK文本带恶意外部片段时，不产生伪delivered；Channel id/version不匹配拒绝。跑Argus插件和Gateway DTO聚焦套件。
- [ ] **Step 5：diff及授权后提交。** 建议「feat(agent): provide current channel context for decision scripts」。

### Task A2：五工具表面与只读取证

**Files:** Modify `apps/agent/src/agent_app/plugins/argus/tools.py`、`plugin.py`、`application/controllers/session_api.py`；Test `apps/agent/test/agent_app/plugins/argus/test_tools.py`、`test_guidance.py`。

**Interfaces:** TOOL_NAMES变为channel_read/read/history/rule_check/commit五项；Check只需source；CommitArguments按设计；会话可信allowlist额外允许现有memory_recall/memory_read，其他工具依旧被裁掉。

- [ ] **Step 1：写失败测试。** `test_commit_schema_only_requests_business_fields`断言properties仅results/progress/source_keys/rule_source/lifecycle；`test_check_has_no_event_refs_or_request_id`；`test_memory_reads_allowed_but_writes_and_bash_denied`；history=50通过、51拒绝。
- [ ] **Step 2：运行red。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/argus -q`。
- [ ] **Step 3：实现工具schema/独立描述。** 全量results映射ref→真实ID；省略字段保持、显式null拒绝，未知字段拒绝。工具不从自然语言猜handled；check调用无副作用静态端点。memory使用现有可信scope，不重建记忆API。
- [ ] **Step 4：green与同步一致性。** results缺项/重复/历史引用拒绝；read非必需，快照足够可直接commit；记忆不可用明确错误。sync/async同payload得到相同行为，依旧不能调用未授权工具。
- [ ] **Step 5：授权后提交。** 建议「feat(agent): simplify Argus business tools and allow scoped memory reads」。

### Task A3：技术参数、持久提交和恢复

**Files:** Modify `plugins/argus/tools.py`、`application/services/argus_bridge.py`；Test `test_tools.py`、`apps/gateway/test/test_argus_tools_e2e.py`；必要时只补SessionStore现有journal查询公共接口，不新增表。

**Interfaces:** 在原_callback上实现固定body与payload指纹去重；kind=agent.commit_requested；成功回执含applied/committed/channel_id/version/rule_hash。Argus R1/R4是后端前置。

- [ ] **Step 1：写失败测试。** `test_commit_auto_binds_ids_hash_and_snapshot_version`、`test_commit_response_loss_replays_identical_body`、`test_commit_receipt_replays_after_binding_closed`、`test_unknown_commit_blocks_different_payload`。
- [ ] **Step 2：运行red。** Agent Argus插件测试与Gateway聚焦E2E；fake HTTP先校验服务器实际收到body。
- [ ] **Step 3：实现固定outbox/回执恢复。** 发送前追加callback，ID由binding＋canonical payload摘要生成；同内容复用；结果未知优先查原提交。channel_read成功才刷新task版本，commit前不得自动抓最新版本。已成功后重复调用仍能查回回执，不能先被inactive检查挡住。
- [ ] **Step 4：green与异常矩阵。** 同Session不同task不串回执；取消后只GET回执、不POST新/旧提交；GET的not_found/pending不能假称回滚或成功；服务器DB提交后连接断开/客户端journal写失败/进程重启仍恢复；冲突显式read后新意图，两个并发commit串行协调。完整模型视图缺失时禁止commit。
- [ ] **Step 5：授权后提交。** 建议「feat(agent): persist and reconcile atomic Argus commits」。

### Task A4：联合验收与工具机制依赖

**Files:** Modify Argus插件/Gateway E2E测试；文档更新仅在实现后修改旧arch中的当前行为说明。通用工具解耦按[独立计划](../2026-11-02-tool-discovery-execution-decoupling/plan.md)完成，不在此复制代码任务。

**Interfaces:** Consumes R1-R5＋Gateway G1＋工具解耦T1-T2；Produces正常Agent链的自主处理/取证/单次commit验收。

- [ ] **Step 1：写端到端失败测试。** `test_two_rounds_commit_without_researching_known_tools`、`test_agent_can_recall_evidence_before_commit`、`test_failed_commit_preserves_old_channel_and_rule`、`test_task_completed_without_commit_is_not_business_success`。
- [ ] **Step 2：使用确定性模型fixture跑red。** Fixture验证正常工具链但不冒充真实模型能力；真正自然任务由Argus R7授权实测。
- [ ] **Step 3：完成对齐并green。** 无legacy confirm/update/finish旁路，最终completed状态与commit回执一致，冻结集合和新事件分轮。
- [ ] **Step 4：应用验证。** 最小文件→`make test-agent`→`make test-gateway`→`make typecheck-agent`→compile/diff。缺FastAPI等依赖应使用对应应用环境，不修改测试跳过掩盖。
- [ ] **Step 5：按授权保存实现与独立文档提交。** 当前仅文档编写，不自动执行本任务。

## 自检与交付

设计覆盖映射：快照/视图A1，简化工具与记忆A2，版本/幂等/恢复A3，联合业务成功A4；五项Review Focus均有测试落点。Argus事务/隔离/JEV不在Agent重复实现。代码交付前再次检查相对链接、真实文件路径与共享字段一致性。
