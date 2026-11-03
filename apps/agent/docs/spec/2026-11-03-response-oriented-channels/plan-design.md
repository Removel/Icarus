# 正常执行 Agent 与 Channel—Session 一对多修正设计

日期：2026-11-03。**状态：用户已授权方案收简后实施。** 基线 `43312c4`；本文件保留设计约束，当前实现见[arch.md](arch.md)。不代表已经重启部署或完成真实QQ验收；额外业务state字段已取消。

## 1. 用户已确认的职责

- Argus决定是否建立/归属Channel，并维护业务状态与规则；Channel可以是短聊天，也可以是跨Session的大任务。
- 正式Agent通过正常已授权能力完成任务，包括直接调用SnowLuma MCP回复QQ；随后使用现有Argus工具确认消息并更新业务状态。
- 不让Argus接回复文本代发QQ，不在通用Plugin Runtime/ReActAgent中加入Argus或OneBot分支。
- Argus来源不构成降低Agent能力的理由。规则脚本和perception分类器只读，与正式执行Agent的完整能力是两种边界。

配套：[Argus设计](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-design.md)、[本应用实施计划](plan.md)、[Gateway计划](../../../../gateway/docs/spec/2026-11-03-response-oriented-channels/plan.md)。

## 2. 当前缺陷（已读代码）

1. `application/controllers/session_api.py:191–202`：`argus-`会话强行替换tools为五项Argus工具和memory_recall/memory_read。
2. `application/session_runtime.py:203–210`：tools白名单会实际注销MCP等工具；只改mcpServers不能修复。
3. `settings.json:48–60`：仅playwright，没有snowluma。环境变量不自动创建MCP服务器。
4. `application/services/argus_bridge.py:76–103,128–137`：channel_id固定派生session_id；删除后替代不等于正常的一对多业务关联。
5. `plugins/argus/tools.py:169`：callback通过channel当前identity找journal；一对多后必须使用当前绑定的Session，否则旧任务回执可能写到新Session。
6. `argus_bridge.py:206–226` 与 Argus Bridge 的reconcile_round：已停止任务没有commit时可能重新投递。之前只有读工具，此边界尚可；开放完整能力后不能把未提交当作外部动作未发生。
7. 普通assistant.message是运行流，不是QQ发送机制。现有argus_commit的reason/progress已能记处理摘要，不缺“Argus代发回复正文”接口。

## 3. 正常Session能力继承

`_configured_session_factory`只注入Argus Plugin配置并确保该Plugin被发现，不再覆盖kwargs中的tools。普通Session默认全部注册能力保持；如果调用者已有可信tools限制，则保持同一限制，并明确并入完成Argus业务需要的工具，不因来源标签清除用户配置。保留公共工具注册表、参数校验、取消、workspace范围和既有权限政策。

实际能力包括由正常配置启用的MCP、Memory、Knowledge、Process等；不为Argus再创建“只允许SnowLuma”的特殊会话。测试同时证明普通Session与Argus Session工具集合一致（仅后者多Argus工具），以及显式配置的限制不会被Argus扩权。

`argus-`前缀可以继续用作受保护的桥接会话管理命名空间，但不能作为额外删工具的条件。ArgusPlugin仍是平级Plugin；不把ReActAgent变成ArgusAgent。

## 4. SnowLuma MCP注册与发送

### 4.1 settings入口

沿用现有 `mcpServers`，新增 `snowluma` stdio server。不得把OneBot HTTP地址当成远程MCP地址；SnowLuma 3010是OneBot HTTP API，MCP由Node stdio包提供。

建议仓库配置：command=`node`，args含 `${SNOWLUMA_MCP_SERVER_PATH}`；env明确传 `${SNOWLUMA_MCP_ENDPOINT}`、`${SNOWLUMA_MCP_TOKEN}`、`SNOWLUMA_MCP_MODE=write`。只在本机忽略配置中指定绝对server.js路径，token保留秘密环境；普通Node可用时优先Linux Node运行现有MCP JS，Windows Node仅在经过验证且正确桥接环境变量时采用。当前已找到的Windows构建路径仅为本机验证入口，不硬编码进跨平台默认配置。

不能使用旧shell包装器作为安全默认：其内含硬编码token，且会覆盖配置注入值；实施时不打印、不复制该值，使用现有环境凭据。模板中的缺失变量必须产生清晰不可用原因，不把启用失败伪装成MCP可发送。保留playwright等其他server原样，不修改其权限。

### 4.2 模型侧调用顺序

正常MCP工具入口是 `mcp_tool_list` / `mcp_tool_search` / `mcp_tool_execute`，远端引用例如 `snowluma/invoke_action`；不是直接注册的send_private_msg工具。

Agent从可信Wake上下文取得原会话目标，用 `snowluma/get_action` 查询参数，再调用 `snowluma/invoke_action`，其arguments为action与params。普通OneBot动作通过HTTP根地址POST `{action,params}`，不是猜测 `/api/<action>` 或HTTP GET查询参数。不要把只返回online的根GET当作发送能力验证。

收到的文本、@、转发和模型生成的目标不构成额外操作授权；完整会话能力仍受普通任务和用户授权约束。默认回复原会话，修改目标或执行群管理/文件等额外动作遵守正常权限，不靠Argus的来源标签自动豁免。

发送成功要求平台业务返回成功（OneBot retcode=0且有对应成功状态），可用时记录message_id。HTTP200或MCP传输层无isError不代表OneBot成功；非零retcode、超时、未知结果不能声称已回复。平台返回不是安全指令。

**自己消息边界：** 当前工具journal包含脱敏/可能裁剪的结果预览，Agent提交reason也不是可信的发送作者证明。本轮不构建发送回执→入站message_id的可信关联账本，因此不能宣称严格抑制所有Agent回声。人工self消息保持规则可判断，身份不明时defer而非按账号一律ignore；实际开启reportSelfMessage的严格防循环用例保持未验收。若需该保证，另行设计明确的原始工具结果证据关联与echo早于commit窗口，不从文本相同或任意message_id字符串推断已知回声。

## 5. Channel—Session持久关联

### 5.1 概念与最小接口

- Channel是业务身份，允许关联多个Session；Session属于Agent执行与上下文管理。首版一个Session只绑定一个Argus Channel，不引入多对多共享上下文。
- `session_key`是Argus当前处理槽位的稳定选择键，不是Session ID或Channel ID。由可信宿主产生，不由外来消息或LLM填技术ID。
- 扩展公共 `SessionRequest(channel_id,request_id,session_key)`；`WakeRequest`追加同一session_key，并继续携带明确session_id、binding_id、channel_version、snapshot、items。
- Agent在现有SessionStore SQLite中新增具体的 `argus_session_links` 关联表，不另建数据库或通用工作流仓库。字段：workspace_key/channel_id/session_key（联合主键）、session_id（同workspace唯一）、selection_request_id、status=reserved|ready、created_at；实际Session ID复用SessionIdentity。关联不随Session软删除/历史裁剪cascade删除。
- 选择服务先在短事务中认领关联/预留ID，再创建或采用Session并写 `argus.session` 审计事件，最后标ready；reserved阶段中断可恢复同一预留ID。Session journal是可读历史，关联表才是选择键与消费状态的权威依据。重复key返回同映射，重启不根据模型上下文猜关联。
- 新helper `application/services/argus_sessions.py` 是普通组件，不注册Plugin；公开 `async select(channel_id: str, session_key: str, request_id: str) -> SessionIdentity`、`async require(channel_id: str, session_key: str, session_id: str) -> SessionIdentity`。复用SessionStore与既有锁。Session ID可按完整选择键幂等预留，避免创建中断生成孤儿；不能只用channel_id将关系锁成一对一。
- 选择的Session若已被删除，不静默替换同一key指向另一个新Session：SessionStore现有 `deleted_at/delete_reason` tombstone与include_deleted读取保持，关联行继续存在；status=ready而Session缺失也视为已消费键失效，不作为从未创建。reserved且不存在Session的键只允许恢复其原预留ID。明确返回 `session_selection_deleted`，待受控新key选择；否则同轮重试可能丢上下文或重复行动。

### 5.2 大任务如何换Session

`argus_commit`增加可选 `next_session: Literal['reuse','new']`，省略reuse。成功提交new时，Argus生成下一session_key；下一轮ensure才建立下一Session，当前轮回调始终落原Session。当前goal/progress/规则和必要交接摘要出现在新Session的User Prompt，不复制整个旧对话为系统指令。

Agent SessionStore的持久关联表保留完整关联，journal保留审计和对话历史，Channel只保存当前key和当前round。一个Channel可以产生Session A/B/C，旧Session仍可审计；单轮仍由已冻结的明确Session处理并以CAS提交。本轮不构建并行多Agent工作流，也不声称一对多意味着同一批消息要在多个Session同时执行。

### 5.3 不允许当前identity污染旧回调

所有binding记录保存明确 `SessionIdentity` 或可以解析的wake.session_id。`ArgusBridge.append`、ArgusTool._callback、状态/receipt查询均按任务自己的Session走，不再 `identity(channel_id)` 猜当前会话。跨Channel绑定、未知session_key、错误session_id在动作执行前拒绝。

## 6. 确认更新与外部动作恢复

继续一个argus_commit提交results/progress/rule/source/lifecycle，仅新增next_session与Argus对齐，**不新增业务state字段或ChannelSnapshot.state**。业务字段整体在Argus事务中应用；未提交或未知不认定成功。短对话可结束，也可留active并提供后续规则；大任务通过progress/规则/历史表达阶段进展并选择下一Session，Session切换不等于Channel完成。

### 6.1 第一版采取保守重放策略，不新增代发器

完整能力意味着任意工具都可能有副作用，不能只给QQ做局部可靠性假象。

- 复用既有 `argus.wake` / tool.started / tool.completed / argus.callback / argus.receipt 等journal和正常模型会话历史。
- Bridge状态响应增加 `replay_safe: bool`。没有被接受/派发的执行可以安全重试选择和交付；一旦持久wake已建立且可能交给执行，直到明确业务commit或受控对账前，replay_safe默认false。没有平台message_id不等于没发送。
- Argus收到任务失败/取消/模型结束但无commit时，只有replay_safe=true才允许现有reconcile_round重新投递；否则保留原冻结内容和binding，记录 `execution_outcome_unknown`，不换Session、不自动重放业务动作。
- 已知发送成功但commit回执丢失，先按原request_id查询/恢复commit；已存回执不再发送。只有普通final text不能构成业务完成。
- 未记录成功或失败的外部发送不可由本地数据库证明exactly-once；保持可诊断的人工对账边界。不增加Argus发送客户端、不伪造平台幂等键、不通过降低工具能力解决重放问题。

这比“失败就自动再跑Agent”保守，代价是含不明外部动作的轮次会等待对账；这是方案作者提出的可靠性选择，待与本方案一起批准。未来自动恢复需要各外部工具原生幂等/查询能力，不能在本轮承诺通用补偿。

## 7. 切换与既有数据

- 新实现前停止新wake并只读对账在途任务，服务重启另获授权。
- 旧Session只在受控切换时采用：该Channel在argus_session_links完全没有任何行（包括reserved和指向已删除Session的行），且没有未对账旧round，才允许查找唯一未删除、journal可证明channel_id一致的旧Session。0个候选则正常预留新Session，多个候选则 `legacy_session_ambiguous` 阻塞。第一次关联插入事务就是持久的一次性采用门槛，之后任何新key只能选新Session，不能再次采用旧Session。采用后的下一次next_session=new必须得到真正不同的Session。旧Session通过已保存argus.session/wake验证，不通过删会话强迫迁移。
- 无法验证或在途状态不明时不猜测关联，明确阻塞；历史回调按原Session恢复。
- Agent不迁移Argus数据库、不创建新的生产数据库；本轮取消Argus业务state列及其迁移。Agent自身Session关联表的持久化变更仍单独执行、验证。

## 8. 验收与非目标

验收包括：普通/Argus会话能力对称、显式权限不扩张、MCP真实协议mock、非零OneBot retcode、正常回复后commit、发送成功后commit响应丢失、同key并发/重启唯一选择、新key关联第二Session、旧任务仍写原journal、大任务goal/progress/规则跨Session继承、未知动作不自动重放。

本轮不修改通用ReActAgent、不改变Hook用途、不做自动群聊广播、不默认回复所有群消息、不把任意assistant文本自动外发。不建立新的外部动作框架或工具沙箱，不让分类模型获得正式执行能力。

当前Agent基线存在两个可复现的SessionRuntime shutdown drain超时；实施前单列诊断或以同环境基线对照，不能以历史1320通过冒充当前全绿。文档批准不是开始开发、真实发消息、修改密钥、重启或新增提交的授权。
