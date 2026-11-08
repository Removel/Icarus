# 正常 Agent 与多 Session 关联：当前实现

核验日期：2026-11-05。描述当前工作区源码与本地测试，不是提交/部署记录。设计见[plan-design.md](plan-design.md)，2026-11-03 的历史结果保留在[Argus执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/execution.md)。本轮最新验证单列记录，不覆盖历史失败。

## 能力与执行

SessionApi对受保护argus-Session仅注入ArgusPlugin和动态业务上下文，不再覆盖tools参数。普通默认工具能力全部保留；若可信调用者提供显式tools限制，仍原样遵守，不通过桥接扩权。ReActAgent、通用Runtime、Hook机制均没有Argus/QQ分支。

settings保留Node stdio snowluma MCP，路径、endpoint和token由环境提供；Agent经普通MCP工具执行。ArgusPlugin通过PromptContributor把短稳定原则放入System Prompt；当前语义消息和必要来源/目标位于User Prompt，不再每轮携带固定教程。规则SDK用法由只读argus_guide按overview/rules/sources/commit主题获取，复用现有tool_search/tool_execute，读取指南不授权。

Agent需检查OneBot业务返回，普通assistant文本不自动外发；提交仍为argus_commit，增加next_session=reuse/new意图，不新增业务state字段。reason/progress是业务摘要，不是自动验证的可信发送回执。

## Session关联与回调

ArgusSessions为普通应用服务。Agent既有icarus.db增加具体argus_session_links，联合键workspace/channel/session_key对应一个真实Session，按workspace/session_id唯一；reserved→创建/审计→ready支持中断恢复。关联不随Session软删除删除，ready指向删除/缺失Session明确失效，不能静默换ID。旧唯一可验证Session只在Channel首次无任何关联时采用，后续新key不重复采用。

SessionRequest和WakeRequest包含明确session_key；wake验证真实关联。回调通过wake.session_id取得精确SessionIdentity，不能按Channel当前会话猜测。一个Channel可有多个关联Session，不意味同一批内容在多个Session并行重复执行。

已有wake可能派发但没有确定commit时，状态replay_safe=false；其他Session不能用新wake绕过该Channel未定执行。只读commit状态查询和原request_id固定回执继续可恢复，未知外部副作用不自动重放。

## 语义视图、引用与召回

新wake保存view_version=3、完整原始绑定、简短触发prompt与独立observation_prompt。Bridge不再把整包正文当作直接user_request：ArgusPlugin消费UserInputEvent并通过PromptContributionEvent贡献完整当前窗口；受保护会话把argus设为必需贡献来源，失败不允许空上下文开跑。Blackboard仍统一组装动态User Prompt。presentation保留完整当前窗口messages，work_refs只定义确认边界，较早观察单列history；内部UUID/版本/游标不进入模型视图。旧view_version=2及数字引用绑定保留原prompt/原指纹。会话日志argus.references保存mN消息、sN来源别名；重启恢复而非每轮重编号，历史只读参考不能被当作本轮工作确认。工具响应过滤内部字段，Channel规则按需读取；旧绑定保持旧引用语义。

通用输入链新增可选semantic_text，Bridge从真实消息提取主题；Memory的查询、缓存指纹和结果query使用同一主题，不认识Argus私有格式。None保留旧请求指纹，Persona安全检测仍覆盖完整prompt。普通调用不传字段时行为不变。

工具提交确认检查完整observation_prompt是否真的出现在该任务最终User消息中；Blackboard对插件内容的JSON编码会先被解码核对，不拿短触发prompt代替正文可见证明。window_items必须包含冻结工作原始内容，不能同ID换正文；history不能覆盖工作或跨来源授权，所有字段仍受总请求512KiB约束。

## 运行中收件箱（第二阶段）

配置了受保护Bridge的Argus任务在context.ready后启动任务级可用性worker。Argus发现已有Channel接受的后续封存窗口时，向受保护notify发送无正文水位；同水位只通知一次。插件通过PromptUpdateEvent提供“有新补充可读”提示，不steer/cancel、不直接插正文、不新建Task。丢信号仅在活动任务内最多30秒补查，任务结束/quiesce取消并join。await可用查询返回后再次检查binding与TaskChannel活性，成功commit或终态不能再产生提示。

`argus_receive(timeout_seconds=0..30,limit=1..20,max_chars=256..16384)`兼具旧read/wait语义。原wake不变，代码生成稳定batch请求身份，丢响应沿原请求恢复；领取批次分配持久短ref，返回完整窗口与所有当前work_refs。小响应直接返回messages；长正文按连续JSON语义页返回，工具预算剪裁后可缩页重读；只有最终模型tool消息全量相等才推进页偏移，全批覆盖后才发送delivered。未完整看到的批次不继续领取，不能commit。等待利用任务signal，不持续高频查状态。

最终commit必须覆盖初始工作与最多4个补充批次的精确并集，总计最多100；非工作正文/history不可确认。读取不是完成，不要求每个气泡单独回复。Claim与commit在Argus同Channel锁下互斥，未领取工作保留下一轮。中断后不自动复活原Agent或重放外部动作。

第二阶段已在工作区并经真实本地PG/Agent/MCP验证，未部署；详细结果见[第二阶段执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-execution-live-inbox.md)。

## 回复身份与目标

当前稳定引导明确 Icarus 在来源区域参与交流，平台账号是自己的行动身份；需要回复则先实际发送、检查结果，再业务提交。普通聊天的免工具原则只适用于生成内容，不意味着外部投递无需工具。最终assistant文本不是平台发送；不要求每条观察都回复，也不要求纯内部处理必须发送。

每条新消息的动态reply_target包含来源引用、平台、类型和可用目标：QQ私聊user_id/群聊group_id，飞书chat/thread，以及当前平台消息ID。群消息的发送者不能替代群目标，引用的旧消息不能替代当前回应消息。未知目标不猜测。这是语义/引导修正，没有新增通用平台回执认证器；已加载会话须重载才使用新的静态原则。

## 接收与执行恢复

Session ready只表示选择完成。Bridge以argus.submission记录prepared/submitting/not_admitted/accepted等证据；仅SessionAPI在进入输入提交端口前可证明的失败允许安全重试原wake，最多三次。入队后异常、取消、接收后日志失败及旧无证据失败均保守unknown，不靠缺user.message证明未执行。attempt归约防止旧失败重新打开新执行的重试资格。

Gateway返回接收状态而非只裁task_id/status；Argus消费状态。已外发但commit失联仍只查询固定回执，不重复发送。

## 当前验证与限制

本轮全Agent套件曾运行1346通过，Gateway55通过，新增按需指南与实际Memory主题的Session E2E通过；后续补充用例与独立审阅后的最终结果另记。make typecheck-agent已检查286源文件无错误。TUI套件526通过。本轮未复现历史两项drain失败，不能抹除历史记录。

一个Icarus实例是独立对象。测试验证不同本地存储及不同配置agent_id的Memory客户端请求范围，未验证部署中的外部后端隔离；相同存储/相同身份配置不会自动产生隔离。没有真实QQ发送、付费模型、服务重启或Git提交。self-message仍无可信回声关联账本。
