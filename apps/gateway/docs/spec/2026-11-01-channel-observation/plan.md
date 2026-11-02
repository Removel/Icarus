# Gateway Argus Bridge Implementation Plan

> 用户已批准实现；本文为计划而非已实现声明。遵循[已实现契约](../../../../argus/docs/spec/2026-11-01-channel-observation/plan-contracts.md)、[共同约定](../../../../../spec/2026-11-01-channel-observation.md)、[Argus台账](../../../../argus/docs/spec/2026-11-01-channel-observation/plan-execution.md)和[Agent计划](../../../../agent/docs/spec/2026-11-01-channel-observation/plan.md)。

## 1. 职责

Gateway 仅鉴权、严格模型校验、向同一 Agent 应用公开控制器薄转发。不创建第二 AgentRuntime，不运行规则、不直接写 Argus Channel/文件，不加表/队列/Channel缓存。Argus 严格 observed_items/channels 两表，其业务当前轮状态由 Argus channels.runtime 持有，Gateway 不镜像。

本次冻结为受保护 **HTTP** 接口，不再把旧 `/argus/rpc` 或 v3 JSON-RPC 当新协议；不做旧数据/协议迁移、自动降级或双轨兼容。

## 2. G1：控制面（原粗估0.5–1人日，已计总计划）

使用 `Authorization: Bearer ARGUS_GATEWAY_TOKEN`：

| 路由 | 请求 | 响应 |
|---|---|---|
| POST /argus/session | channel_id、request_id | session_id |
| POST /argus/wake | channel_id、wake_id、binding_id、channel_version、session_id、items:[Observation]、argus_url | task_id、status |
| POST /argus/status | wake_id | task_id、session_id、status |

items 为冻结接口的公共 Observation 模型，不另抄 WakeItem；status 为 accepted/running/completed/failed。默认复用有效 Session，幂等 ensure 结果由 Agent 既有持久化承载，Gateway 不缓存。相同 wake 重试不重复输入，提交后不可变 fingerprint/target；未知/鉴权/归属错误不得映射成 idle/success。取消路由若启用需先对齐调用方，不自行另造协议。

Agent callback 的 delivered/confirm/check/update/finish 使用 `ARGUS_AGENT_API_TOKEN` 直达 Argus `/agent/events`，由协议入库和规则门禁处理；Gateway 不能变成旁路更新入口。remote accepted 不等最终视图 delivered，更不等全轮确认。

### 任务与聚焦测试

修改 Gateway 实际 HTTP 路由/公开模型/受保护监听器，复用 Agent 公开类型；测试镜像对应源位置，不假定旧 argus_methods JSON-RPC 文件就是新入口。

- [ ] 未认证/错误 token/普通公共 RPC 不能 ensure 或 wake；token 不输出到日志。
- [ ] 严格字段、bool/int、items=0或21、帧/请求大小限制；多来源 Observation 原样转发。
- [ ] ensure 重试 request_id 不变；同 wake 不同指纹拒绝；跨 workspace/Channel Session 拒绝；同 source 不同 Channel 不互斥。
- [ ] 状态/错误完整保留，不因超时新建 wake；不存在直接 Channel 更新接口。
- [ ] 最小行为 red→薄路由实现→green→Gateway 套件与 Argus/Agent 确定性 HTTP fixture。

## 3. 验证与边界

最小测试文件→`make test-gateway`→受影响 compile→`git diff --check`，记录实际退出码/失败身份；本次 docs 未运行功能测试。停机拒绝新投递并保守对账未知在途；不以移除路由假称执行已取消。无真实 Feishu/付费模型/生产 DB/Git 操作；不重复询问已定 Session/确认/check 默认。
