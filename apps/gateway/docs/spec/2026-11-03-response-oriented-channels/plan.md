# 回应 Channel 的 Gateway 薄接线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不增加业务逻辑的前提下转发显式Channel—Session选择键和保守恢复状态。

**Architecture:** 公共DTO仍属于Agent，Gateway仅负责现有受保护HTTP端点鉴权、DTO解析和调用Agent服务。Argus持有业务状态与冻结轮，Agent拥有一对多Session关联和实际MCP执行，Gateway不代发消息、不维护关联数据库。

**Tech Stack:** Python / FastAPI / 现有Agent公共DTO与pytest TCP集成测试。

**Spec:** [Agent设计](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan-design.md) §5–6；[Argus设计](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-design.md) §6。

**Status:** 用户已授权开发；共享key薄转发与本地MCP闭环测试已实现，尚未提交、部署或发送真实消息。基线43312c4；联合结果见[执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/execution.md)。

## Global Constraints

- 不复制SessionRequest/WakeRequest/ChannelSnapshot模型，不解释Channel语义或OneBot协议。
- 保留ARGUS_GATEWAY_TOKEN鉴权和public RPC不可访问桥接接口的边界。
- 既有 `/argus/session`、`/argus/wake`、`/argus/status` 足够，不新增平台reply或Channel管理HTTP路由。
- SessionRequest新增session_key必填，Wake携带同key与现有Channel快照；不增加业务state字段；status转发replay_safe，旧服务缺字段由Argus保守处理。
- 不创建分支/提交/推送、不启动服务或发送真实消息，除非获得对应明确授权。

## Review Focus

1. 缺失或伪造session_key不能绕过关联验证，也不能落入旧的一对一默默fallback。
2. snapshot身份不匹配或整体Wake超限必须在执行前拒绝，保持现有快照字段和大小限制。
3. 同一选择请求重复提交不能产生多个Session。
4. status不能丢弃replay_safe，避免Argus把未知外部动作当作可安全重跑。
5. 改路由参数不能弱化Bearer鉴权或暴露到普通RPC。

---

### Task 1: 公共DTO薄转发与契约测试

**Files:** Modify `apps/gateway/src/protocol/argus.py`；Modify `apps/gateway/test/test_argus_bridge.py`、`apps/gateway/test/test_argus_tools_e2e.py`。

**Interfaces:** 消费Agent计划Task2的 `SessionRequest(channel_id,request_id,session_key)` 与 `bridge.ensure_session(channel_id,request_id,session_key)`；WakeRequest和Bridge.status直接使用Agent公共定义/返回值，含session_key/replay_safe；ChannelSnapshot维持现有字段。

- [ ] **Step 1: 写失败测试。** 现有鉴权测试扩展：无token仍401；缺key/快照身份不符/整体Wake超限为422且不调用业务；同key重复选择返回同session_id；新key得到第二Session；status透传replay_safe=false；非法跨Channel映射在业务执行前拒绝。
- [ ] **Step 2: RED。** `apps/gateway/.venv/bin/python -m pytest apps/gateway/test/test_argus_bridge.py -q`；Expected旧ensure_session参数或缺失字段导致断言失败。
- [ ] **Step 3: 最小实现。** `/session`只把新增key转发给Agent服务；确保选择冲突映射409，与wake的BridgeConflict处理一致。不创建Session映射表，不判断new/ignore，不接收回复正文。
- [ ] **Step 4: GREEN与整套。** 聚焦契约/e2e→`make test-gateway`→compile/diff。验收使用合成消息和假MCP；真实TCP不代表真实QQ发送。Agent整套两个已知drain失败在联合记录中列明，不能冒充Gateway失败或顺手修改无关实现。
- [ ] **Step 5: 联合切换检查。** Agent、Argus、Gateway必须共同使用新key契约；不在运行旧服务时只改一端重试碰运气。停新wake/对账/重启均另获授权，完成后再更新当前架构文档。不自动提交或推送。

## 自查

五项Review Focus均在Task1的契约测试覆盖；跨应用实施顺序为Agent公共DTO→Gateway薄转发→Argus消费→联合e2e。该应用没有独立业务设计变化，故只写plan.md，不造placeholder arch.md。
