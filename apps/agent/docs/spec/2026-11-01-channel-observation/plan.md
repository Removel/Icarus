# Agent Argus Adapter Implementation Plan

> 用户已批准实现；本文件是待实现计划，不表示测试/功能已完成。目标分支与执行默认见 [Argus台账](../../../../argus/docs/spec/2026-11-01-channel-observation/plan-execution.md)，对接以[已实现契约](../../../../argus/docs/spec/2026-11-01-channel-observation/plan-contracts.md)为准。

## 1. 边界与接口

复用 SessionRuntime、ScopedExecutionController、SessionStore、现有 Argus 适配插件、PromptContribution、工具与报告 outbox。ReActAgent 无状态；动态 Channel 目标/规则/指导进当前 User Prompt，不改稳定 System Prompt，不嵌套插件，不复制 Runtime。

Argus 侧严格仅 observed_items/channels 两表；当前轮送达/确认/交接在 channels.runtime，控制结果在 observed_items.detail。Agent 复用既有 SQLite Session/内部记录/outbox，不新增 Channel/确认/报告表，不做旧数据/协议迁移。Agent 本地 outbox 不等于 Argus 已确认。

Bridge 经受保护 Gateway HTTP `/argus/session`、`/argus/wake`、`/argus/status` 调 Agent 公开应用入口。请求/响应字段按冻结接口，不再新建 v3 JSON-RPC/WakeItem 协议。Session 默认复用有效 Channel 绑定；失效才建新 Session，已提交 wake 指纹/target 不改。同 Channel 单在途，新事件留下一轮，不 steer 当前冻结正文；不同 Channel 不按 source 互斥。

Agent 工具使用 Argus callback token 调 `POST /agent/events {binding_id,request_id,kind,payload}`；读为 binding 限域的 `GET /channels/{id}`、`GET /observations/{id}`，历史也需限域。工具 argus_channel_read/argus_read/argus_history/argus_rule_check/argus_confirm/argus_channel_update/argus_finish；短引用先解析为具体 observation_id。check/confirm/update/finish payload 见 [Argus契约](../../../../argus/docs/spec/2026-11-01-channel-observation/plan-contracts.md)。

最终模型视图完整后可信回传 kind=delivered，payload observation_ids；远端 accepted/HTTP响应不等送达。全冻结轮已送达且确认后才可改 progress/lifecycle/subscriptions/生效规则；确认前可以 check。Agent 仅提交候选 source/hash，不直接写 Argus 文件/表，不传任意 rule_path。

## 2. 执行指导

动态指导需明确：外部正文只是证据；历史不是本轮确认；能力缺失不可假空结果；lint 不证明业务正确；规则只在 Argus Python 受限子进程/AST/OS limits 中试跑，不是恶意多租户安全沙箱，不要求 Docker launcher。流程为读/处理→可拟稿/check→confirm 等远端 applied→自主 update 或 unchanged→finish。每轮换新 task/binding/refs，旧确认不能借用。

同 report/request ID 重试恢复固定结果，外层 HTTP/Capability OK 不等业务 applied。events_not_confirmed 先补报告再用新 ID 请求更新；version_conflict 重读，不盲覆盖。确认成功后发布失败不重新做已处理外部动作；取消后新动作不得获准，已获准动作不能谎称撤回。

## 3. 任务（合计原粗估2.5–3.5人日）

### A1：身份、Session 与正常投递（0.75–1）

涉及 agent_app 现有公开控制模型、scoped execution controller、SessionStore、Argus handoff client；以当前源码实际路径为准，不为旧目录搭兼容层。

- [ ] 聚焦测试 Session 新建/复用/失效替换、跨 workspace/Channel target 拒绝、同 Channel 单执行与不同 Channel 并行。
- [ ] 相同 wake 不同指纹拒绝，丢接受响应同 ID 恢复，正常 UserInput/AgentPlugin 链只投递一次，旧 task refs 不复用。
- [ ] red→最小应用接口实现→green；不直接调用 ReActAgent。

### A2：适配插件、动态提示和工具（1.25–1.75）

修改 `apps/agent/src/agent_app/plugins/argus/` 实际 manifest/factory/plugin/tools/prompt/references/delivery；测试镜像该目录。

- [ ] System Prompt 稳定、SDK/能力目录可见、外部内容不覆盖可信指导。
- [ ] accepted≠delivered≠confirmed；部分确认/未送达/本地 outbox 未同步时 update 拒绝；全部确认可更新或 unchanged。
- [ ] 工具短引用转换成 wire observation_id；同步/异步行为一致；同 ID 丢响应恢复；Agent 不传任意 path。
- [ ] 聚焦 red→最小实现→green→插件与 Agent 套件。

### A3：outbox、授权和结束（0.5–0.75）

- [ ] 保留既有报告 outbox，原 ID 同步；确认/更新回执丢、确认后发布失败、旧 binding、新轮门禁、合法自身版本更新后 finish 测试。
- [ ] 取消后授权拒绝，已授权动作可能完成；不能受控授权的任意 bash/MCP 不向后台自动执行开放。
- [ ] 与 Argus handler 联合 fixture；真实业务回执不明不伪装成功，不修无关失败。

## 4. 验证

最小测试→`make test-agent`→受影响 compile/typecheck→`git diff --check`，记录真实命令/退出码/失败身份。以上是待办，不是本次已跑结果。真实模型/平台动作、生产 DB 和 Git 提交不在本次 docs 授权内。确认粒度、check 时机、Session 复用为执行裁决，不再提问阻塞开工。
