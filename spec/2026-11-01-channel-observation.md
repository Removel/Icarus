# Argus 插件化重构：跨应用共同约定

状态：2026-11-01，用户已批准在 `feat/argus-plugin-refactor` 开始实施；协调者报告基线包含 fetched `origin/feature`，不是本文重新执行 Git 核验的结论。以下为共同要求，不声称已经实现。目录日期保持稳定。

## 1. 范围与所有权

[原始需求](https://tcnyvm7vds2s.feishu.cn/docx/L52KdcwLBoKiFBxIkBgcV2IYnA7)以用户最新硬约束为准；本次不访问 Feishu。本文只保留跨应用边界，不复制应用内部实现。

| 所有者 | 负责 | 文档 |
|---|---|---|
| Argus | 协议入库、文件规则/能力、Channel 状态、确认门禁与 Bridge | [技术方案](../apps/argus/docs/spec/2026-11-01-channel-observation/plan-technical-design.md)、[契约](../apps/argus/docs/spec/2026-11-01-channel-observation/plan-contracts.md)、[计划](../apps/argus/docs/spec/2026-11-01-channel-observation/plan.md)、[执行台账](../apps/argus/docs/spec/2026-11-01-channel-observation/plan-execution.md) |
| Agent | Session/task 身份、正常执行链、Argus 适配提示/工具、实际送达/处理报告与动作授权 | [计划](../apps/agent/docs/spec/2026-11-01-channel-observation/plan.md) |
| Gateway | 鉴权、严格公开模型、薄 HTTP 转发 | [计划](../apps/gateway/docs/spec/2026-11-01-channel-observation/plan.md) |

## 2. 不可分共同要求

1. Argus 使用公共 Icarus PluginRuntimeHost/manifest/factory/contracts/EventBus/Capability/生命周期，不复制 Runtime。协议→规则→Bridge 加独立能力 leaf，所有具体插件平级；命名分组不是父子插件。
2. Argus **恰好两张应用表 observed_items 与 channels**；禁止 channel_observations/wake_requests 等支撑表。每 Channel 当前 pending/round/送达/确认/有限回执仅在严格有界 channels.runtime，控制结果在 observed_items.detail；不存无界执行历史。无旧数据迁移或兼容双轨。
3. Argus 复用 Mem0 现有 PG 容器/实例，独立数据库/受限账号，不读写 Mem0 数据，不新增生产 PG 容器。此要求不授权自动建角色、清库或部署。Agent 复用既有 SQLite/outbox；Gateway 无业务持久化。
4. 协议先持久 Observation 再发事件，规则先持久通过再通知 Bridge；总线不是可靠队列。恢复由 cursor/runtime/pending 控制记录承担，幂等 ID 和未知结果保守对账不能丢。
5. 规则可授权查询已存多来源历史，错时到达正常；查询不推进消费、不扩大待确认集合。规则源码仅在受管不可变 hash 文件，不在 Channel JSON 重复保存。
6. Bridge 通过 Gateway/Agent 公开应用接口投递至 Argus 适配插件，正常 Agent 链执行，不直调 ReActAgent。有效 Session 默认复用，同 Channel 单在途，冻结轮不随新事件扩张。
7. 最终可见 delivered 与 handled/no_action confirmed 不同。**本轮全部送达并确认成功后**，才可激活 progress/lifecycle/订阅/规则更新；确认前允许 check/拟稿/试跑，不允许生效。Agent 可以不更新；本地 outbox、HTTP 接受或 Session 结束不等确认。
8. 回传通过 Argus 第一层落受信控制 Observation，规则固定 handler 验身份/版本/确认门禁后提交；平台正文不能伪造 kind/binding。确认成功后发布失败不撤销确认、不重做业务动作；不承诺外部 exactly-once。
9. 动态 Channel 背景/规则指导进入当前 User Prompt，不改稳定 System Prompt。ReActAgent 无状态；Runtime 不理解平台/Channel 业务，Hooks 不决定流程/记录，供应商差异留底层。
10. Python 规则采用受限子进程、严格 AST、OS limits、超时与薄 JSON IPC；不宣称恶意多租户安全沙箱，不强制另建 Docker launcher，不降级宿主 exec。

## 3. 接口与执行依赖

[已实现契约](../apps/argus/docs/spec/2026-11-01-channel-observation/plan-contracts.md)定义 DTO、Store、插件方法与 HTTP wire。Gateway 使用 `/argus/session`、`/argus/wake`、`/argus/status`，不是新 v3 JSON-RPC；Agent 经 Argus `/agent/events` 回传，读接口受 binding 限域。协议变化必须先协调调用双方，不能各自造同义模型。

先补最小通用 builtin 分组发现（不借 external plugin_dirs 绕过）及两表 Store，再接插件/应用适配。验收包括真实隔离 PG、容量背压、多源查询、冻结轮门禁、重复/丢通知/丢回执恢复、文件/DB失败、受限执行与默认 Session 复用。当前未执行的项目明确记待办，不以握手/fixture 代替真实业务成功。

执行默认已裁决，不再重复征询架构；具体阈值与测试台账归 Argus。凭据/数据库权限/OS 支持缺失只阻塞对应部署或 smoke，不是停止所有实现的理由。用户批准实现不授权付费 API、平台写入、生产数据清理或 Git 提交。
