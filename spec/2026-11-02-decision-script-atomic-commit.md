# 决策脚本与原子业务提交：跨应用约定

日期：2026-11-02。状态：待实施设计；用户已确认方向，本文不表示源码已经改造。新feature，原[Channel Observation spec](2026-11-01-channel-observation.md)作为开发快照保留；本feature实施时以下条款替代其先确认后更新、单函数受限脚本等旧方向。不修改既有验证结论。

## 所有权与文档

| 应用 | 所有权 | 设计与计划 |
|---|---|---|
| Argus | 决策脚本、安全检查与隔离、两表原子commit、持续发现、JEV实测 | [设计](../apps/argus/docs/spec/2026-11-02-decision-script-atomic-commit/plan-design.md)、[实施](../apps/argus/docs/spec/2026-11-02-decision-script-atomic-commit/plan.md) |
| Agent | 业务工具、版本/幂等参数、正常Session执行、动态上下文与授权只读取证 | [设计](../apps/agent/docs/spec/2026-11-02-decision-script-atomic-commit/plan-design.md)、[实施](../apps/agent/docs/spec/2026-11-02-decision-script-atomic-commit/plan.md) |
| Gateway | 严格共享Wake DTO、受保护HTTP薄转发 | [实施](../apps/gateway/docs/spec/2026-11-02-decision-script-atomic-commit/plan.md) |

通用工具发现/执行解耦是独立的Agent-owned feature：[计划](../apps/agent/docs/spec/2026-11-02-tool-discovery-execution-decoupling/plan.md)，不在Argus中做特殊绕过。

## 不可拆分的共同要求

1. 协议先持久事件、固定规则handler处理、Store事务提交；使用现有Icarus Host/Event/Capability/Session，不复制Runtime或EventBus。ReActAgent无状态，动态业务背景只进当前User Prompt。
2. Argus仍恰好两张应用表observed_items/channels；Agent复用SessionStore/journal/outbox，Gateway不保存业务状态。源码在内容寻址不可变文件，不在Channel JSON重复存储。
3. 规则为文件级Python，以同步main(event, channel)为入口；可组织辅助函数/常量/异常处理。只做本地计算和授权只读取证，不直接文件/网络/SQL/环境访问，不执行业务修改。Argus拥有静态能力策略、OS隔离及预算；隔离不可用时拒绝执行，不降级宿主exec。
4. lint只静态解析/编译/安全策略检查，不运行脚本、不调用semantic/LLM。可选LLM安全审查是独立发布门禁，不能豁免确定性检查和隔离。
5. Agent使用一个argus_commit，提交当前冻结轮每条消息的handled/no_action结果及可选progress/source_keys/rule_source/lifecycle。旧confirm/update/finish不再是模型必须执行的多步流程。技术ID、版本、hash/path、幂等键由运行时补齐，不要求模型搬运。
6. Argus一个短事务内共同保存最终结果、Channel业务状态、规则引用、订阅、pending/round收尾和成功回执。检查/审查/制品准备在事务外；任何失败不得只生效一部分。业务commit不是Git操作，不承诺外部服务exactly-once。
7. agent.delivered仍表示完整冻结消息实际进入最终模型视图，不等处理完成。task completed也不等业务committed；未commit轮次失败不能被当已处理清除。规则/历史查询不扩大冻结集合。
8. Agent outbox发送前持久完整请求，网络重试复用同body/request_id；Argus先查固定回执再要求活跃binding。已提交响应丢失可恢复，不能因round清空而失去成功结果。版本来自明确给Agent的快照，不在提交前自动读取最新版本覆盖旧意图。
9. Wake增加严格有界Channel业务快照和规则SDK元数据；仍使用Agent-owned共享类型，经Gateway薄转发，保持冻结消息完整与总包络预算。Agent可自主结合Channel、历史和授权记忆等信息，不要求每轮先channel_read。记忆不替代数据库版本事实。
10. 同一来源允许存在不同事项Channel；新事项发现与已有Channel订阅分开，具体去重/退避/候选策略由Argus负责。不能把重投/旧消息重新建为新事项，也不能因已有订阅漏掉新话题。

## 依赖和切换

后端commit与隔离门禁先做；Agent/Gateway共享DTO联合对齐后切换业务工具。普通Python执行不得先于OS隔离上线。开发切换需停止新wake、对账旧在途任务，保留未解决事实；不自动删除生产数据，不建设长期旧协议双轨。

真实模型预算及安全审查默认归Argus配置；本轮文档不调用付费模型、不接真实平台、不部署。原开发快照已保存为a97d226/11bc050/c373c07，无Co-Authored-By，未推送。新文档暂未提交。后续实现须再次核验源码与计划，不把计划当现有能力。
