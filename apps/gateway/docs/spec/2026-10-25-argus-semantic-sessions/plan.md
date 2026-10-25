# Gateway 语义会话控制边界实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development after user approval. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Argus 提供受认证的有限 session 候选、目标 session 交接和无正文通知，继续复用同一个 AgentRuntime，不在 Gateway 存业务状态。

**Architecture:** 专用 /argus/rpc 校验和认证后调用 Agent 的可信 facade；普通 /rpc 和 session.steer/cancel 维持既有语义。协议显式 v2，v1 已接纳任务可以对账收尾；不把领域选择、待读队列、TTL 或报告 outbox 搬到 Gateway。

**Tech Stack:** 现有 FastAPI WebSocket、Pydantic、JSON-RPC、pytest；无新依赖。

**Spec:** [共同约定](../../../../../spec/2026-10-25-argus-semantic-sessions.md)、[Agent 计划](../../../../agent/docs/spec/2026-10-25-argus-semantic-sessions/plan.md)、[Argus 计划](../../../../argus/docs/spec/2026-10-25-argus-semantic-sessions/plan.md)。

**实施记录（2026-10-25）：** Tasks1–3本地实现和离线验收完成，逐批与最终跨组件修正审阅通过；最终Gateway148通过、一个既有Starlette/anyio警告。当前代码见 [arch](arch.md)。新版正常/受控延迟确认及旧版真实三应用边界均有本地provider验收，没有真实模型/QQ业务或正式部署验证。

## Global Constraints

- 用户已通过 /goal 授权预审、实现和本地隔离测试；部署、正式数据、真实模型/QQ/MCP 与 Git 变更仍需另行授权。
- Gateway 不加表、不读 Agent 私有 _store/_entries、不创建第二 runtime、不跨应用导入 Argus 源码。
- 新操作只有专用 /argus/rpc 可用，沿用 ARGUS_GATEWAY_TOKEN 常量时间比较与 1 MiB 帧限制。
- notify 只表达已持久关联消息可用，不带正文/任意提示/资源路径，不能转成 session.steer/cancel/submit。
- 普通 session.steer 仍是用户显式纠正；后台 session 仍不能通过普通 API/订阅旁路访问。
- JEV 路由归 Argus，候选资格/停止/同群锁归 Agent；WS 断线不表示执行停止。

## Review Focus

- 已知后台 session ID、伪造 target 或 notify 不能绕过 scope/所有权验证（Task 1/2）。
- v1/v2 形状混用、布尔版本、unknown 字段与巨大候选页必须明确拒绝（Task 1）。
- notify accepted 不被当成正文 delivered 或业务 handled；返回丢失可重试（Task 2/3）。
- 普通 steer/cancel 的行为不被后台分支替换，原 TUI/WebUI 行为保持（Task 2）。
- 仅 fake runtime 测试无法证明不打断 LLM/工具；离线真实运行时必须验证（Task 3）。

## 文件与接口

| 修改路径（相对 apps/gateway） | 职责 |
|---|---|
| `src/protocol/argus_models.py` | v2 SessionsParams/NotifyParams、WakeParams target 字段及有界返回值 |
| `src/protocol/argus_methods.py` | 严格 allowlist/认证/版本/安全错误→Agent facade |
| `test/fixtures/argus_v2.json`（新） | 与两应用本地 fixture 内容一致，无根共享包 |
| `test/test_argus_methods.py` | 请求/错误/guard 单元契约 |
| `test/handoff_host.py` | 真实运行时 fixture 扩展，无假业务成功 |
| `test/test_argus_semantic_sessions.py`（新） | 不打断/主动读/续接/排队的真实离线集成 |
| `test/test_methods.py`、`test/test_app.py` | 普通 RPC 和专用入口隔离回归 |

消费 Agent 公共可信接口：

```python
async def scoped_sessions(scope, *, cursor=None, limit=10) -> dict: ...
async def scoped_notify(*, wake_id: str, session_id: str) -> dict: ...
async def scoped_submit(request) -> ExecutionView: ...
```

已有 scoped_status/cancel/release 保留。数据面 available/attach/deliver/read 由 Agent↔Argus 内部 HTTP 承担，Gateway 不代理正文。

## Task 1：固定 v2 wire 与安全校验

**Interfaces:**

- `argus.sessions`：`protocol_version=2,caller_token,scope,cursor?,limit=10`；limit 1..20，cursor 有界；返回 `protocol_version,sessions,next_cursor`。
- `argus.wake` v2：现有 frozen payload 增加必填可空 target_session_id；身份不由模型选择，wire 不含工作区路径或 callback URL。
- `argus.notify`：`protocol_version=2,caller_token,wake_id,session_id`；无正文，结果固定 `protocol_version,accepted,merged`，其中 merged 为严格 bool 表示已合并提醒。接受仅表示已核验并投递/合并提醒，不表示模型已消费。
- status/cancel/release 按请求版本回应；v1 只读/收尾既有记录，新增 v1 路由功能不推断。所有版本均不能省略认证。

- [ ] RED：三应用本地 argus_v2.json 的相同业务输入得到相同规范化/指纹，target 也计入指纹；null target 不等于省略字段；v1 不允许 v2 字段，版本为 true/字符串拒绝。
- [ ] RED：sessions 页上限、cursor/UUID/scope、notify 多余 text/resources/binding 字段拒绝；未知操作拒绝；错误及日志无合成 token/正文泄漏。
- [ ] Run：`apps/gateway/.venv/bin/python -m pytest -q apps/gateway/test/test_argus_methods.py`。
- [ ] 实现 Pydantic 严格 schema，认证后转边界值，复用 Agent 应用类型，不导入插件或业务 repository。INVALID_PARAMS/业务错误/未知内部异常保持分层。
- [ ] GREEN；v1 旧 fixture/测试继续证明已有 status/cancel/release 收尾兼容，不重算旧指纹。

## Task 2：可信转发，保留 steer 与后台隔离

- [ ] RED：sessions 仅调用 scoped_sessions；notify 仅调用 scoped_notify，fake runtime 的 steer/submit/cancel 设为一调用即失败；v2 wake 只透传验证过的 target_session_id。
- [ ] RED：跨群/非后台/未知 target 的 Agent 业务拒绝原样安全映射；notify unavailable 不假 accepted；status not_found 不吞服务错误。
- [ ] RED：普通 /rpc 无 argus.sessions/notify；专用入口无 session.steer；已知后台 ID 的普通 get/history/steer/subscribe 拒绝；普通 interactive session.steer 仍下一步骤纠正，cancel 仍停止。
- [ ] Run：`apps/gateway/.venv/bin/python -m pytest -q apps/gateway/test/test_argus_methods.py apps/gateway/test/test_methods.py apps/gateway/test/test_app.py`。
- [ ] 实现两个可信 facade 转发和 allowlist；不缓存候选/执行/TTL，不扩展普通 token 权限，不为通知创建独立服务或第二 listener。
- [ ] GREEN；客户端重连/重复 notify 使用原 wake/session 身份，由 Agent 去重，不在 Gateway 增加幂等数据库。

## Task 3：真实运行时离线集成

**Files:** test/handoff_host.py、新 test_argus_semantic_sessions.py；需要 Agent/Argus 计划的生产实现与隔离 PostgreSQL/SQLite。

- [ ] RED：真实 TCP Gateway+AgentRuntime，确定性本地模型和阻塞工具：A 执行时 attach/notify A2，当前 LLM/工具完整结束；下一步只见固定提醒，模型主动 argus_wait 后才见 A2 正文。用唯一 sentinel 断言此前所有 provider 输入都无 A2。
- [ ] RED：A/B 同群不同 session 串行，A2 不被误投 B；超过五分钟同话题复用；任务结束竞争后同 session 启动新 task；24 小时 idle 退出候选但历史仍在。
- [ ] RED：报告绑定/重复通知/丢回执/重启恢复/v1 未收尾兼容，迁移表集合不变；无真实 QQ 发送，不用 fixture accepted 冒充业务回执。
- [ ] Run：`apps/gateway/.venv/bin/python -m pytest -q apps/gateway/test/test_argus_semantic_sessions.py`；缺隔离 PG 前提则报告未运行，不改用正式数据库。
- [ ] 仅补生产接线；GREEN 后 `make test-gateway`、`make test-agent`、`make test-argus`，再 `apps/gateway/.venv/bin/python -m compileall -q apps/gateway/src apps/gateway/test`、`git diff --check`。
- [ ] 记录测试身份/退出码和未验证项，更新入口说明及实现后的架构链接，不自动提交/部署。

## 回滚与自审

停止新 v2 提交仍保留旧 execution 的 status/cancel/release 和数据面报告收尾，不用关闭 WS 冒充已停止。Gateway 无需数据库回滚；不因降级而把 queued/unknown 标 completed。

自审：新增方法均有 auth/版本/形状/安全错误/普通入口拒绝和真实运行时验证；没有隐式 steer、正文 notify 或 Gateway 领域状态。该计划只描述目标，不声称接口已经存在。
