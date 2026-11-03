# 正常 Agent 能力与多 Session 关联实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取消Argus来源造成的额外工具限制，接入SnowLuma MCP，并用持久的一对多关联替换固定Channel→Session身份。

**Architecture:** SessionApi按普通Session策略构造执行环境，仅附加Argus Plugin/上下文；Agent侧选择服务通过既有SessionStore中的关联表拥有实际Session映射，journal用于审计，Argus只提供冻结session_key。正式Agent直接调用MCP行动，随后一次argus_commit确认并更新；未知外部动作不自动重新派发。

**Tech Stack:** Python / pytest / 既有SessionStore、SessionRuntime、MCP stdio、公共Argus DTO；SnowLuma现有Node MCP包，不新增SDK。

**Spec:** [plan-design.md](plan-design.md)。依赖：[Argus设计和计划](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan.md)、[Gateway薄接线](../../../../gateway/docs/spec/2026-11-03-response-oriented-channels/plan.md)。

**Status:** 用户已明确授权在方案收简后开始开发；已注册SnowLuma settings并修改Agent接线，尚未提交或重启部署。真实线上消息由用户验收；见[执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/execution.md)。基线43312c4。

## Global Constraints

- Argus触发的正式Agent拥有普通Session在当前配置/workspace/权限下的完整能力，不能强行缩成只读Agent，也不能绕过普通权限。
- 规则和perception分类兜底仍只读；不把通用Runtime/ReActAgent改成Argus或QQ专用，不用Hook发起下游插件或记录业务回执。
- Channel:Session为一对多，Session历史和实际ID归Agent；同一Session首版只关联一个Channel。
- Session选择必须重试/重启稳定；同轮binding记录明确session_id，不按channel“当前identity”猜测旧任务身份。
- MCP服务器配置复用mcpServers、环境插值和既有三工具入口；OneBot HTTP不是MCP HTTP。
- 密钥不进settings/源码/日志。SNOWLUMA_MCP_TOKEN是OneBot入站API token，不等于ARGUS_QQ_REPORT_SECRET上报签名secret。
- Agent直接执行MCP后提交，Argus不代发；HTTP200、模型宣称完成都不是平台动作成功。
- 不新增业务state或ChannelSnapshot.state；交接复用goal/progress、规则和历史。next_session=reuse/new，省略reuse，不接受模型填写技术session_id。
- 不自动重放可能有外部副作用的未定任务，不声称跨平台exactly-once。
- 不修改当前settings/本机拓扑/数据库或启动服务直到明确开发批准；真实发送、模型预算、迁移、重启另外确认；未授权不commit/push/创建分支。

## Review Focus

1. 移除Argus硬编码白名单后，普通能力可用但显式用户限制仍被保留，不因桥接扩权。
2. 同一选择键在重试、并发、进程重启和创建中断后对应同一Session，旧Session回调不能写入新Session journal。
3. 已发送但未成功commit的任务停止后不能由新的Session自动重发，未知不被当no_action或完成。
4. MCP传输成功但OneBot retcode非0时必须作为业务失败；self上下文不足保留defer，不能把语义回声样本通过夸大成平台级确定性防循环。
5. 短对话和大任务跨Session都正确保留Channel业务进展/规则，Session结束不隐式完成Channel。

---

## 文件职责和执行顺序

- SessionApi/SessionRuntime：正常能力继承，保留既有显式权限机制。
- settings与MCP配置测试：声明SnowLuma的stdio入口和环境引用；不引入平台发送后端。
- 新 `application/services/argus_sessions.py`：已有SessionStore之上的选择/关联普通组件，不注册Plugin。
- `application/repositories/session_entities.py` / `session_store.py`：在既有icarus.db中拥有argus_session_links；不级联删除已消费键。Session journal保存关联审计，不再被当作唯一映射索引。
- `argus_protocol.py`：共用DTO新增session_key，保留现有ChannelSnapshot字段；Gateway不复制模型。
- `argus_bridge.py` / `plugins/argus/tools.py`：选中的真实Session、任务绑定、确认与保守恢复。
- 现有SessionStore SQLite：持久关联及删除tombstone；现有Session journal：执行证据与对话，不另建通用工作流数据库。

顺序：Task1可独立验证；Task2先发布共享契约给Argus/Gateway；Task3与Argus进展更新/commit任务对齐；Task4修复完整能力后的重放边界；Task5做全链路验收。

### Task 1: 继承普通Session能力并声明SnowLuma MCP

**Files:** Modify `apps/agent/src/agent_app/application/controllers/session_api.py`；Modify `apps/agent/settings.json`（仅批准开发后）；Modify `apps/agent/test/agent_app/application/test_session_runtime.py`、`plugins/mcp/test_config.py`、`plugins/mcp/test_integration.py`；Create `apps/agent/test/agent_app/application/test_argus_session_capabilities.py`。

**Interfaces:** `_configured_session_factory(identity, *, config, **kwargs)` 不再把tools改成固定七项；无显式tools限制时按普通默认能力注册并附加Argus工具，若显式限制存在则保持限制语义和业务必需工具的明确合并。Argus不是新的权限域，不能通过前缀绕过原权限。

settings新增stdio定义：`mcpServers.snowluma.command="node"`、`args=["${SNOWLUMA_MCP_SERVER_PATH}"]`；env引用现有endpoint/token并mode=write。真实绝对路径由本机忽略配置提供，测试用临时合成MCP服务；不要调用包含硬编码token的旧包装器。Linux Node不可用时先报告，未经许可不安装/升级SnowLuma或改机器全局Node。

- [ ] **Step 1: 写失败测试。** `test_argus_session_keeps_normal_capabilities_plus_argus_tools`：同一配置下MCP/Memory/其他普通已启用工具在两类Session中一致，仅Argus多业务工具。`test_argus_does_not_override_explicit_tool_policy`：被可信配置禁止的工具仍不可执行。使用替身提供模型，不启动真实MCP或外部服务。
- [ ] **Step 2: RED。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/test_argus_session_capabilities.py -q`；Expected旧强制allowlist断言失败。
- [ ] **Step 3: 最小实现。** 删除Argus来源特有tools覆盖，不修改正常Registry授权、tool_execute或ReActAgent。确保Argus Plugin仍通过可信桥接注入，不向普通用户开放伪造binding。
- [ ] **Step 4: MCP配置测试后改settings。** 缺变量报清晰不可用原因；token插值不出现在日志。保留playwright原配置。测试 `mcp_tool_search(server="snowluma")` 可发现invoke_action并能经mcp_tool_execute进入本地假MCP服务，而不是只检查配置字符串。
- [ ] **Step 5: GREEN。** 聚焦新Session能力测试、MCP config/integration及已有Argus工具测试；Expected全部通过。现有两项SessionRuntime drain失败另记，不把改超时作为本任务实现。

### Task 2: 公共DTO与持久的一对多Session选择

**Files:** Modify `apps/agent/src/agent_app/argus_protocol.py`、`application/services/argus_bridge.py`、`application/repositories/session_entities.py`、`application/repositories/session_store.py`；Create `apps/agent/src/agent_app/application/services/argus_sessions.py`、`apps/agent/test/agent_app/application/test_argus_sessions.py`；Modify `apps/gateway/test/test_argus_bridge.py` 的契约fixture（Gateway处理逻辑由Gateway计划拥有）。

**Interfaces:**
- SessionRequest追加必填session_key: Id；WakeRequest追加session_key: Id；ChannelSnapshot保留现有goal/progress/规则/生命周期字段，不新增state。保持既有512KiB总Wake/32KiB规则限制。
- `ArgusSessions.__init__(self, runtime, workspace: Path)`；`async select(self, channel_id: str, session_key: str, request_id: str) -> SessionIdentity`；`async require(self, channel_id: str, session_key: str, session_id: str) -> SessionIdentity`。
- `ArgusBridge.ensure_session(self, channel_id, request_id, session_key) -> dict`：返回session_id。新的identity选择从session_key幂等预留（可用UUID5(channel_id,session_key)保证创建中断后恢复），显式持久关联行是归属依据、journal只是审计；不是从channel_id单值绑定一对一。采用旧可验证Session时允许沿用旧真实ID。

- [ ] **Step 1: 写失败测试。** 同channel/key三次选择、并发选择、重建服务对象后均返回同Session；同channel新key得到第二Session，两者关联仍可查询；create_session成功但append关联前中断，再试不产生第二Session。
- [ ] **Step 2: RED。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/test_argus_sessions.py -q`；Expected缺选择组件或固定identity导致失败。
- [ ] **Step 3: 实现关联组件与持久门槛。** SessionStore的argus_session_links具体表在原icarus.db内建联合主键workspace_key/channel_id/session_key、同workspace session_id唯一约束；status=reserved/ready。先事务reserve后创建/写审计再ready，重启复用reserved ID。新增SessionStore方法 `reserve_argus_session_link(identity: SessionIdentity, channel_id: str, session_key: str, request_id: str, *, legacy_identity: SessionIdentity | None = None) -> SessionIdentity`、`get_argus_session_link(workspace_key: str, channel_id: str, session_key: str) -> tuple[SessionIdentity, Literal['reserved','ready']] | None`、`complete_argus_session_link(identity: SessionIdentity, channel_id: str, session_key: str) -> None`；reserve只在该Channel从未有任何关联行时允许采用唯一验证legacy_identity，存在任何行后新key必须新Session。不得级联删除关联；ready指向删除/缺失Session时明确session_selection_deleted，不能重建同key。journal继续写argus.session审计，但不是唯一权威映射。不在Channel.runtime记录历史数组，不另起数据库。
- [ ] **Step 4: 去除固定identity假设。** wake用require验证所选映射，任务binding保存明确SessionIdentity；_find/status按历史wake查找该Session，不再要求session_id等于identity(channel_id)。当前round技术绑定仍唯一且冻结。
- [ ] **Step 5: 旧数据与权限测试。** `test_adopt_verified_legacy_session_without_losing_history`、`test_legacy_adoption_then_new_key_creates_different_session`、`test_ambiguous_legacy_sessions_block_selection`、`test_deleted_session_selection_requires_new_key_after_restart`（检查include_deleted及关联行仍在）、`test_cross_channel_session_selection_rejected`、`test_forged_session_key_rejected_before_submit`。旧无key在途wake仅用于只读对账，不无条件转换并重放；采用门槛与首次关联在同一短事务落实。
- [ ] **Step 6: GREEN。** 新Session选择测试与Gateway DTO测试全部通过；变更只在Agent应用服务/公共DTO内，不把Session管理放进Argus或Runtime通用层。

### Task 3: Agent确认与状态更新、换Session及QQ指导

**Files:** Modify `apps/agent/src/agent_app/plugins/argus/tools.py`、`application/services/argus_bridge.py`；Modify `apps/agent/test/agent_app/plugins/argus/test_commit_tools.py`、`test_reconciliation.py`、`test_guidance.py`；Modify `apps/agent/test/agent_app/application/test_argus_sessions.py`。

**Interfaces:** Commit仅新增可选next_session(reuse/new)，progress及Receipt/ref/status/reason沿用现有契约；把字段原样传Argus业务payload，由Argus事务消费。ArgusTool._callback及bridge journal操作使用当前任务binding的SessionIdentity（源自wake.session_id并require校验），不能读“Channel当前identity”。

- [ ] **Step 1: 写失败测试。** next_session通过工具校验且保留现有progress和冻结refs/version；非法枚举/null拒绝。Session B已选中后Session A旧回执仍写A，不能读取B的callback并误报提交成功。
- [ ] **Step 2: RED。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/argus/test_commit_tools.py apps/agent/test/agent_app/plugins/argus/test_reconciliation.py -q`；Expected缺字段或旧identity调用断言失败。
- [ ] **Step 3: 实现并更新动态GUIDANCE。** 当前User Prompt说明：业务Channel概念、原QQ会话身份、正常工具能力、直接用MCP回复、检查OneBot retcode后提交；progress/规则更新和next_session表达业务进展与后续执行意图，技术ID由宿主填充。不要强制每条都发消息或每次都关Channel；相关信息可no_action，短对话也可继续active。
- [ ] **Step 4: 业务结果边界。** mock MCP返回retcode≠0、超时、缺message_id（不同动作按真实返回契约判断）不能被当平台成功；正文不复制为Argus代发payload，不新增reply_text路由。发送成功时reason/progress可记message_id，但注明其为Agent提交的摘要，不是自动验证的typed平台凭据。
- [ ] **Step 5: GREEN。** 聚焦Argus tools/guidance/reconciliation全部通过；同时断言未修改稳定System Prompt或通用MCP中的OneBot分支。

### Task 4: 完整能力执行后的保守重放边界

**Files:** Modify `apps/agent/src/agent_app/application/services/argus_bridge.py`；Modify `apps/agent/test/agent_app/plugins/argus/test_cancel_commit.py`、`test_reconciliation.py`；Modify `apps/gateway/test/test_argus_bridge.py` 的status输出fixture；Argus侧消费见其Task4。

**Interfaces:** Bridge status增加 `replay_safe: bool`。一旦已持久化wake并可能派发，未确定业务commit前默认false；不以缺tool.completed、无message_id、task.failed等推导“没有外部动作”。已有确定回执的恢复仍按原request_id，不重新发送MCP。Gateway只转发字段。

- [ ] **Step 1: 写失败测试。** `test_sent_reply_then_task_stops_without_commit_is_not_replay_safe`、`test_wake_dispatch_gap_is_conservatively_unknown`、`test_completed_text_without_commit_is_not_business_completion`：status可以failed/completed，但replay_safe=false，旧绑定和待处理内容保留。
- [ ] **Step 2: RED。** 聚焦取消/对账测试，Expected旧终态无commit被释放或字段缺失失败。
- [ ] **Step 3: 实现。** 区分交付前的可重试网络/选择失败和已有可能执行的wake。沿用现有session journal、callback/receipt查询，不把通用MCP包装成Argus专用发送器，也不通过Hooks记录业务结果。
- [ ] **Step 4: 丢回执与跨Session恢复。** `test_commit_response_lost_reuses_receipt_without_resending_message`：假QQ send计数1，commit状态查询恢复applied；`test_unknown_send_does_not_select_fresh_session`：未知动作下不能用new key重新跑同批消息。确认未定轮次恢复是明确阻塞，不假装自动修复。
- [ ] **Step 5: GREEN。** Argus恢复、Gateway status和本任务测试全部通过。对缺replay_safe的旧服务按false，部署升级不得默许重放。

### Task 5: 跨应用闭环、基线失败与受控上线

**Files:** Modify `apps/gateway/test/test_argus_tools_e2e.py`、`apps/argus/test/argus_app/test_discovery_e2e.py`、`apps/argus/test/argus_app/test_closed_loop.py`；Update本feature设计/计划和应用文档，只有实现后才新增arch.md。

**Interfaces:** 使用Task1的正常MCP入口、Task2的一对多选择、Task3的原子提交字段、Task4的status恢复，不另造一套测试专用桥接契约。假MCP工具schema与SnowLuma一致，OneBot返回为真实envelope格式。

- [ ] **Step 1: 写失败闭环测试。** 真实本地Gateway TCP、Agent SessionRuntime、Argus隔离PG与假SnowLuma MCP：招呼→new→send_private_msg→argus_commit；群聊需回应无@；续聊复用Session；阶段推进progress/规则并next_session=new→同Channel第二Session；失败/取消/回执丢失无重复发送。
- [ ] **Step 2: RED。** `apps/gateway/.venv/bin/python -m pytest apps/gateway/test/test_argus_tools_e2e.py -q`，以及Argus runner指定e2e文件；Expected失败来自缺少预期行为，不是把依赖缺失当RED。
- [ ] **Step 3: 修补接口接线至GREEN。** 只修改任务前述拥有者；Gateway不接管业务或添加reply HTTP路由。读写Session状态与Channel业务state区分，不把task.completed当channel.completed。
- [ ] **Step 4: 全面验证。** provider/MCP/Argus tools最小集→`make test-agent`→`make test-gateway`→`bash apps/argus/scripts/test.sh`→`make typecheck-agent`→compile/diff。当前两项Agent baseline失败是 `test_session_runtime使用runtime_update并保留单session行为`、`test_session_runtime_e2e持久化完整agent_run历史`，均为shutdown `RuntimeError: drain:`；若仍存在，逐名记录并与相同环境基线比较，不改断言或跳过冒充通过。新的相关失败必须修复。
- [ ] **Step 5: 运行时配置与真实验证（另获执行授权）。** 检查Node/server路径、OneBot入站token和上报secret分开；只做MCP initialize/list/get_action不发消息确认启动。经用户确认后在指定私聊发送一条固定合成回复，计数1，查看平台返回与Argus提交；群聊不自动测试。启动/重启Gateway与Argus、数据库迁移均先说明影响并确认。
- [ ] **Step 6: 切换与交付记录。** 暂停新wake→对账旧round→关联旧Session→发布新版本；未知结果保留，不重放旧ignored消息或删除Session。更新文档，注明未执行的实测、剩余阻塞、回滚边界。无新Git授权不提交或推送。

## 自查与批准

Review Focus映射：能力→Task1；选择身份→Task2/3；外部动作恢复→Task4/5；业务发送成功/回声→Task3/5与Argus Task1/2；多阶段状态→Task3/5。公共DTO属于Agent、业务PG属于Argus、Gateway只有鉴权转发，接口名与配套文档一致。

需要一并审阅的实现选择：用session_key显式选择与next_session=new请求下一上下文；未知外部执行保守阻塞而非自动重跑。按用户确认，不新增业务state及其快照/迁移，沿用progress/规则/历史。本文件本身不授权真实平台发送或服务操作。
