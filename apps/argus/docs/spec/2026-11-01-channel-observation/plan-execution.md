# Argus 重构执行台账

> 2026-11-01。已获用户实施批准，目标 `feat/argus-plugin-refactor`；协调者报告当前基线包含 fetched `origin/feature`。本台账由文档任务建立，不证明代码或测试完成，不执行分支/提交/推送操作。

依据：[当前源码架构](arch.md) · [计划](plan.md) · [技术方案](plan-technical-design.md) · [契约](plan-contracts.md)。冻结接口约束实现对接；如发现差异先协调，不各自更改 wire/方法。

## 执行裁决

- 恰好 observed_items/channels 两表，无 channel_observations/wake_requests/其他支撑表。runtime 仅有界 pending/errors+当前 round/确认/有限回执，不积累历史。
- 共享 Mem0 现有 PG 容器/实例，独立 Argus DB/受限账号；不新增生产 DB 容器，无迁移/清库。
- main.py 进程入口、application.py 组合公共 Host；protocols 与 rule_capabilities 分组下各 leaf 平级注册，workflow_rules/icarus_bridge 同级。必须补通用 builtin 遍历，不借 external plugin_dirs。
- Python `-I` 受限子进程、AST、OS limits、墙钟超时、JSON IPC；不是恶意多租户安全沙箱，不增加强制 Docker launcher。缺 limits 明确不可执行，不宿主 exec。
- 全冻结轮送达且 confirmed 后才能改生效状态/订阅/规则；确认前允许 check。Session 默认复用。更新可选，确认已接受不因发布失败倒退。
- pending 默认256（遵循冻结接口），round≤20、来源≤16、当前轮 receipt 摘要≤40、runtime≤1MiB；规则≤32KiB、detail≤48KiB、processing≤8KiB。额外字节/摘要上限是工程执行默认，配置必须保有界；满则背压，不丢 cursor 后工作。默认30天留存但保护未消费/在途引用，过期旧 binding 拒绝。
- 首期不含自动发现、多机制品分发或先验并发框架；缺凭据仅影响真实 smoke。付费 API/生产服务/平台动作/Git 提交不在本任务授权内。

## 已实现与验证状态（2026-11-02）

已完成：通用builtin分组发现、main/application装配、两表Store、三个协议插件、六类函数能力、文件规则检查/执行、当前轮确认后发布、Bridge、Agent动态指导/受限lazy工具、Gateway受保护HTTP入口和模拟真实TCP闭环。完整真实命令和次数见[验收记录](plan-validation.md)，实际架构见[arch.md](arch.md)。

本轮保留独立分支，不提交/推送/合并。应用默认复用有效Session，全部冻结事件确认后才更新；Agent可确认前check或确认后unchanged结束。自动发现、自动30天GC、真实账号联通/真实模型质量、任意平台动作、多机并发不属于已实现声明。

原任务清单（拆分任务以实际代码与验收记录为准）：

- [ ] P0：实际基线失败身份、依赖/隔离 PG/目录权限。
- [ ] T1：通用 grouped builtin discovery 聚焦测试与支持；main/application 公共 Host 生命周期。
- [ ] T2：两表 DTO/Store/schema，PG 并发、cursor/pending 原子、容量与 GC 保护。
- [ ] T3：三协议 leaf；认证、先入库再通知、控制幂等。
- [ ] T4：查询/机械/语义及配置化外挂能力 leaf；授权/边界/错误类型。
- [ ] T5：文件/hash/check、受限进程/IPC、版本重判、扫漏。
- [ ] T6：固定控制 handler；实际送达、全轮确认、激活/finish 与有界结果。
- [ ] T7：runtime 当前轮、默认 Session 复用、HTTP Bridge 稳定身份/对账。
- [ ] A1–A3：Agent 正常链/动态提示/工具/outbox/动作授权。
- [ ] G1：Gateway 冻结 HTTP 模型/鉴权/薄转发。
- [ ] T8：闭环与丢通知/丢回执/文件失败/容量/保留测试；先最小测试，再应用套件、compile/diff。

实施者在完成每项时补真实命令/退出码/red-green/失败身份，不以此文档勾选替代证据。本次未运行新功能测试。

## 冻结接口简审

已对齐：pending=256、冻结后 pending 保留到 finish、Store.set_round_delivery 只记远端接受；实际 delivered callback 才置送达。wire 使用 observation_id，不是短 ref；Bridge 使用 HTTP 公开接口，不再计划新 JSON-RPC。Store 内部可接受 rule_path，但对外仅 rule_hash，规则 handler 验证后解析 path。RuleFiles 同步、RuleEngine 异步。

仍需实施时补齐的窄接口事项，不阻塞开工：

1. 冻结 Store 未列出通过 wake_id 更新 Bridge 对账状态/失败重试的完整方法；用现有 Store 事务扩展最小有界 round 更新，先与所有者协调，不旁路 SQL 或增加表。
2. 原 request_id 查询是丢响应恢复需求；冻结 HTTP 清单尚未明确其 GET 路由。可先相同 POST 幂等重试，若加查询路由须协调 Agent/Argus，不能假称已冻结。
3. runtime 额外字节上限/当前轮摘要、控制 strict schema、保留保护需要聚焦测试落实；冻结接口的 dict 类型不是允许任意无界 JSON。
4. 实际受限账号 DB/OS limits 可用性需环境核验；缺失记录对应 blocked 项，不宣称安全沙箱或开启生产操作。

结构性必做是 builtin 分组发现及两表有界协调实现；不是新的产品选择题。完整原粗估19–29人日仅作参考，首批测试后重新估算新增明确工作。
