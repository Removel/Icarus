# Gateway 决策脚本提交协议配套计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 本文件为待实施计划，不表示功能已完成。

**Goal:** 为新业务commit提供严格有界的Wake快照转发，保持Gateway无业务状态。

**Architecture:** 复用Agent-owned共享WireModel、原受保护/argus/session/wake/status三接口和同一AgentRuntime。commit从Agent直接回传Argus，不经过Gateway旁路，不新增业务提交表或Channel缓存。

**Tech Stack:** Python、FastAPI、Pydantic、现有Agent应用接口、pytest。

**Spec:** [跨应用约定](../../../../../spec/2026-11-02-decision-script-atomic-commit.md)、[Agent设计](../../../../agent/docs/spec/2026-11-02-decision-script-atomic-commit/plan-design.md)。

## Global Constraints

- 保持ARGUS_GATEWAY_TOKEN鉴权与保留argus-* Session普通RPC隔离，不输出凭据。
- Wake总包络<=512KiB，items完整且<=384KiB，snapshot规则源码<=32768字节。
- Gateway只校验/转发，不运行规则、不审查LLM、不写Argus表、不决定业务成功。
- 不复制Observation/Channel/Wake DTO，不引入v3 JSON-RPC或旧协议长期双轨。
- 不自动提交/推送/接真实平台；获授权的提交不加Co-Authored-By。

## Review Focus

- Snapshot与channel_id/version不匹配应拒绝，不能转发成有效旧意图。
- 加入规则源码后包络超限应明确失败，不截断消息。
- 重投wake指纹变化应冲突，不绕过幂等重新执行。
- 普通公共RPC不得以新增字段取得Argus后台会话权限。
- commit回执丢失不能由Gateway以task completed替代业务成功。

---

### Task G1：联合共享DTO与薄转发回归

**Files:** Modify `apps/agent/src/agent_app/argus_protocol.py`（Agent A1所有，联合交付）；视需要Modify `apps/gateway/src/protocol/argus.py`；Test `apps/gateway/test/test_argus_bridge.py`、`test_argus_tools_e2e.py`。

**Interfaces:** ConsumesWakeRequest新增channel_snapshot/rule_sdk，精确字段见Agent设计；Produces原session/wake/status响应，语义保持。无需新增Gateway commit入口。

- [ ] **Step 1：写失败测试。** `test_wake_forwards_bounded_channel_snapshot`、`test_wake_rejects_snapshot_identity_or_version_mismatch`、`test_snapshot_overflow_does_not_truncate_items`、`test_changed_snapshot_conflicts_on_same_wake`；同时断言未认证/额外字段/普通RPC不能访问保留会话。
- [ ] **Step 2：跑red。** `apps/gateway/.venv/bin/python -m pytest apps/gateway/test/test_argus_bridge.py -q`，确认新字段行为失败而不是环境缺依赖。
- [ ] **Step 3：复用A1共享模型并完成薄转发。** 不改变业务字段、不生成新wake、不以超时重建task；不在Gateway计算commit技术字段。HTTP响应和错误原样保持，日志脱敏。
- [ ] **Step 4：联合green。** `test_commit_goes_directly_to_argus_not_gateway`、`test_task_completed_does_not_fabricate_commit_receipt`；运行bridge与tools E2E。消息完整性/真实工具链由Agent应用验证，不用手工写DB替代。
- [ ] **Step 5：应用回归及授权后提交。** `make test-gateway`→compile→`git diff --check`；如无生产路由代码变化，提交只包含共享契约归属及回归测试，不为形式修改app.py。

## 切换

Argus后端commit门禁和Agent新工具必须先就绪；停止新wake并对账旧在途后再整体启用新Wake。PG/隔离/JEV是Argus验收前置，不由Gateway绕过。当前新文档暂不提交，旧Channel Observation文档保持历史。
