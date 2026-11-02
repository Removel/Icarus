# Agent 原子业务提交适配：当前架构

2026-11-02，当前未提交源码；[设计](plan-design.md)、[计划](plan.md)。实现从c373c07之后开始，未部署或推送。

## 会话与上下文

保留argus-* Session由受保护Gateway Bridge初始化，普通公共RPC不能创建/提交/steer这类会话。仍经SessionRuntime、AgentPlugin、无状态ReActAgent正常执行。应用allowlist在目录/executor绑定前裁剪真实registry，允许五个Argus工具和现有memory_recall/memory_read，不开放写记忆、bash、任意MCP或平台写动作。

Agent-owned WakeRequest新增严格ChannelSnapshot：id/title/goal/progress/lifecycle/version/source_keys/rule_source，id/version必须与Wake匹配；SDK元数据有独立32KiB上限，总包络512KiB。Bridge将可信指导、当前快照、SDK和完整冻结消息组成当前User Prompt；System Prompt只修正通用工具机制，不包含Channel业务。

Agent可按需查询，不强制channel_read第一步；只有显式read成功才更新本task snapshot_version。记忆帮助解释业务，不能替换服务端版本校验。最终模型视图必须包含本轮完整prompt才回传delivered，旧会话内容和HTTP接受不代替送达。

## 工具与提交

`plugins/argus/tools.py`提供channel_read/read/history/rule_check/commit，manifest同为五工具。history limit1..50，check仅source；commit仅results及可选progress/source_keys/rule_source/lifecycle。每个工具有独立描述。results短ref由代码解析，必须恰好覆盖本轮冻结集合；历史或重复ref拒绝。

binding/channel/request_id/expected_version等由运行时提供，规则hash/path由Argus生成。source使用UTF-8字节限制。无业务变化也可提交结果并结束，不再要求confirm/update/finish三步。只有applied＋committed回执代表完成。

## 幂等与恢复

发送前在既有SessionStore追加argus.callback，body固定；幂等ID是binding＋kind＋canonical业务payload的摘要。同task同请求优先返回argus.receipt，不跨task/复用Session串回执。上个commit未知时先GET服务器回执，不能并行改意图。成功后标记binding finished；同内容重试能取原回执，随后终态状态对账清理内存绑定。

`ArgusBridge._reconcile_commits`对终态/重启后任务的持久commit意图仅做只读GET。applied/rejected保存回执；pending/not_found/网络失败保持pending/unknown，不重发POST、不假称回滚。Argus Bridge看到unknown/pending不清轮次或重投动作。没有持久意图且任务终态时按正常有限重投规则处理。

同步和异步调用沿用既有执行链，取消禁止新动作。数据库提交原子性在Argus，不在Agent或Gateway重复实现。真实JEV鉴权未通过，不能由适配工具成功推断语义质量。

## 验证

当前Agent全套1294通过、typecheck280源文件通过；Gateway正常Agent工具/严格快照联合12通过、终态只读恢复4个参数场景通过。具体最终审阅和后续修复以[Argus实施记录](../../../../argus/docs/spec/2026-11-02-decision-script-atomic-commit/plan-execution.md)为准。工具发现/执行解耦另见[独立架构](../2026-11-02-tool-discovery-execution-decoupling/arch.md)。
