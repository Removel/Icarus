# 正常 Agent 与多 Session 关联：当前实现

核验日期：2026-11-03。基于当前工作区源码和本地测试；不是部署记录。设计见[plan-design.md](plan-design.md)，联合验证见[Argus执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/execution.md)。

## 能力与执行

SessionApi对受保护argus-Session仅注入ArgusPlugin和动态业务上下文，不再覆盖tools参数。普通默认工具能力全部保留；若可信调用者提供显式tools限制，仍原样遵守，不通过桥接扩权。ReActAgent、通用Runtime、Hook机制均没有Argus/QQ分支。

settings增加Node stdio snowluma MCP，JS路径、OneBot HTTP endpoint和token由环境提供。Agent经现有mcp_tool_list/search/execute远端调用invoke_action，不把OneBot HTTP当成MCP远程传输。原会话来源与回复指导在当前User Prompt；规则脚本限制单独说明，不再约束正常执行Agent工具。

Agent需检查OneBot业务返回，普通assistant文本不自动外发；提交仍为argus_commit，增加next_session=reuse/new意图，不新增业务state字段。reason/progress是业务摘要，不是自动验证的可信发送回执。

## Session关联与回调

ArgusSessions为普通应用服务。Agent既有icarus.db增加具体argus_session_links，联合键workspace/channel/session_key对应一个真实Session，按workspace/session_id唯一；reserved→创建/审计→ready支持中断恢复。关联不随Session软删除删除，ready指向删除/缺失Session明确失效，不能静默换ID。旧唯一可验证Session只在Channel首次无任何关联时采用，后续新key不重复采用。

SessionRequest和WakeRequest包含明确session_key；wake验证真实关联。回调通过wake.session_id取得精确SessionIdentity，不能按Channel当前会话猜测。一个Channel可有多个关联Session，不意味同一批内容在多个Session并行重复执行。

已有wake可能派发但没有确定commit时，状态replay_safe=false；其他Session不能用新wake绕过该Channel未定执行。只读commit状态查询和原request_id固定回执继续可恢复，未知外部副作用不自动重放。

## 当前验证与限制

真实SQLite覆盖选择重试、重启、一对多、legacy采用、删除键失效和创建gap；本地Gateway+stdio假MCP覆盖发送后提交。Agent套件仍有两项开发前已复现的SessionRuntime shutdown drain失败，未屏蔽或顺手改动。尚无真实QQ/线上模型验收，未重启已运行服务，代码未提交。self-message作者/回声不能只靠语义给确定性保证。
