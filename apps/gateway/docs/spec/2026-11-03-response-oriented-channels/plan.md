# Gateway 接收契约与联合验证实施计划

> 实施时使用 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development`，先失败测试、再最小实现。本地实现与套件验证已执行，未提交或部署；最终范围与限制见[执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-execution-2026-11-05.md)。

**Goal:** 不丢失 Agent 的接收证据，验证精简语义输入与安全恢复的真实调用链。

**Architecture:** Gateway 只负责已有受保护 HTTP 入口、公共 DTO 校验与薄转发；Agent 拥有接收判定和实际 Session，Argus 拥有来源和 Channel 业务。Gateway 不解释 source key、不代发平台消息。

**Tech Stack:** 现有 Python/FastAPI、Agent 公共 DTO、pytest、本地 provider 与 stdio MCP fixture；不新增依赖。

**Spec:** [design](plan-design.md) · [Agent 计划](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan.md) · [Argus 计划](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan.md)。更新：2026-11-05；调查基线 `e2ca65c`；本稿是下一轮计划，不把历史已实现接线重写为未完成，也不宣称新功能就绪。

## Global Constraints

- 一个 Icarus 实例是独立对象；Gateway 不建设跨实例共享、来源合并或自动去重。
- 不复制 Agent DTO，不导入 Argus 内部模型，不解析平台协议。
- 复用 `/argus/session`、`/argus/wake`、`/argus/status` 与 Bearer 鉴权，不开放公共 RPC 桥接入口。
- 不按 HTTP 错误/超时自行认定未接收，不把 ready/accepted/committed 混为一谈。
- 不修改生产配置、重启服务或真实外发；不 commit/push，除非明确授权。

## Review Focus

1. wake 响应不能裁掉接收阶段，缺字段不能伪装安全：Task 1。
2. schema、身份校验和 Bearer 仍在执行前拒绝：Task 1。
3. 系统提示稳定、当前消息直接可见、普通轮次不读手册：Task 2。
4. 入队后异常/回执丢失不重复模型与平台动作：Task 2。
5. 两实例共享外部群时仍互不读写内部状态：Task 2，隔离证据依赖 Agent/Argus 各自的存储测试。

### Task 1：接收状态一致转发

**Files:** 修改 `apps/gateway/src/protocol/argus.py`、`apps/gateway/test/test_argus_bridge.py`。Agent 公共 DTO 与 Bridge 返回由 Agent 计划 Task 1 拥有。

**Interfaces:** wake/status 转发 task_id/session_id/status、commit_state/replay_safe 与新增 admission_state/attempt/retryable/error_code；session 接口仍只选择会话。具体响应类型由 Agent 公共契约统一，Gateway 不额外维护状态机。

- [ ] 写 wake 与 status 不丢字段测试，含 not_admitted、accepted、unknown；不把 accepted 的 HTTP 回应等同业务 commit。
- [ ] 写未认证401、非法/跨 Channel 绑定409、DTO不合法422、超限不执行业务测试；原显式工具权限不变。
- [ ] 写后端缺接收证据/请求异常时不能合成 retryable=true；未知旧状态保持保守。
- [ ] RED：`apps/gateway/.venv/bin/python -m pytest apps/gateway/test/test_argus_bridge.py -q`，确认旧裁剪响应失败。
- [ ] 最小改动：按 Agent 公共响应契约透传；不新增 reply 路由、Session 表或恢复逻辑。
- [ ] GREEN：重跑契约测试，证明重复选择仍返回同 Session，新 key 不用于绕过未解决执行。

### Task 2：实际模型输入与投递故障 E2E

**Files:** 修改 `test_argus_tools_e2e.py`、`test_argus_mcp_reply_e2e.py`、`test_argus_bridge.py`；新增 `apps/gateway/test/test_argus_semantic_context_e2e.py`。fixture 使用现有真实 AgentRuntime/SessionHost、本地 HTTP/provider/stdio MCP；Argus 集成使用隔离 PostgreSQL。

**Consumes:** Agent 的语义投影/引用/指南/主题字段；Argus 的规范化来源事实和接收恢复。测试不能重建一条假业务捷径绕过实际执行链。

- [ ] 写普通回复实际 provider 输入：必要正文、发送者、区域可见；内部 UUID/SDK 教程不出现；系统仅稳定原则，改变消息不改系统。
- [ ] 替换旧 `test_argus_tools_e2e.py:95–104` “教程必须在User Prompt”的断言；保留动态消息不进入系统的正确约束。
- [ ] 写规则任务按需发现/读取指南、再查源码/检查；check成功不自动激活；已知工具可不重复搜索。
- [ ] 写新旧轮次/重启别名稳定、历史不确认为当前工作、最终输入被截断不能全量 commit。
- [ ] 写从 Argus 序列化 content/detail 到模型视图的往返：飞书 group+text、QQ昵称/时间、纯媒体；不只检查两边各自生成的字典。
- [ ] 写 semantic_text 真实到 Memory 后端，普通请求缺省不变；不得由 Gateway 解析 Argus 内容来替代通用透传。
- [ ] 写接收前加载失败→明确not_admitted→原Session恢复；入队后异常/接收后写日志失败保持unknown；超时但已接收/commit回执丢失都只执行一次外部动作。
- [ ] 写迟到低attempt失败不能放行新attempt；故障Channel不影响另一Channel；实例A/B各自观察同群、互不确认对方工作。
- [ ] RED：新增测试先在旧行为上失败，再消费各拥有者最小实现，不能只改fixture返回值冒充修复。
- [ ] GREEN：本任务全部 E2E；明确脚本模型与平台fixture不代表真实QQ/模型语义质量。

### Task 3：应用验证与切换说明

- [ ] 最小文件：`apps/gateway/.venv/bin/python -m pytest apps/gateway/test/test_argus_bridge.py apps/gateway/test/test_argus_tools_e2e.py apps/gateway/test/test_argus_mcp_reply_e2e.py apps/gateway/test/test_argus_semantic_context_e2e.py -q`。新增文件仅在创建后执行。
- [ ] `make test-gateway` → `make test-agent` → `bash apps/argus/scripts/test.sh`。只用隔离测试数据，记录实际失败身份，不以历史通过数量替代新验证。
- [ ] `apps/gateway/.venv/bin/python -m compileall -q apps/gateway/src apps/gateway/test` 与 `git diff --check`。
- [ ] 上线前暂停新投递/对账，顺序 Agent新旧兼容→Gateway透传→Argus消费。旧 unknown 不因升级自动变安全；任何重复外发或引用错绑先停止新投递。
- [ ] 不自动重启、迁移、外发、提交或推送；完成实现后再据代码更新架构，当前只更新 design/plan。

## 文档与实施边界

实例独立性已明确，不再保留“多机器人同群是否合并”的待决项。平台文本转义、跨主机文件上传、视觉输入不属于 Gateway 修复，不用通用层特例假实现。具体引用/指南/主题字段已有工作区实现和本地测试，不能据此宣称生产部署或完整Argus数据库闭环已验证。
