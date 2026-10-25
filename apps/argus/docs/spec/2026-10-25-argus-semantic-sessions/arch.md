# Argus 语义归属与持久待读当前架构

## 位置与证据

实现目标见 [plan](plan.md)，共同边界见 [跨应用约定](../../../../../spec/2026-10-25-argus-semantic-sessions.md)。本文件基于已检查的scheduler、SessionRoutingService、ObservationRepository/HandoffRepository、HandoffService/API、schema3迁移和镜像测试。Argus本地批次及最终跨组件修正独立审阅已通过；最终完整Argus224通过，包含正常/受控延迟确认两条v2场景及旧版两条场景的真实三应用离线链路。

这些证据不等于视觉能力、真实JEV或QQ业务成功验收。旧v1交接兼容保留，新v2场景使用真实服务边界与本地确定性provider/分类器fixture。

## 源与规则

QQ parser 保留原始 ContentPart、发送者/时间/定位和回复证据。source-owned可读文本事实剔除独立@号码、空白及仅媒体占位；最高优先级不可处理规则先于私聊/@唤醒。有意义图文保留文字和原图片引用，不下载、不OCR。语义判断使用按时间排序的窗口（sender/text/sent_at/current），Icarus 自消息作为上下文；Jev 先回答 `can_speak`（是否适合说话），再回答 `speak_now`（现在就说吗），两次都是是才唤醒。窗口优先来自本地观测；配置 `qq.history` 时用 SnowLuma history 回填缺失消息（尤其是机器人自身消息），失败时回退。

JudgementService仍先安全检查完整内容，再做有界选择和既有规则/JEV叫醒判断；自发、撤回、权限、敏感策略保持。候选/历史摘要同样受出口策略，不因作为context而绕过过滤。

## 语义路由与同群调度

SessionRoutingService复用现有JevClient一次有界多问题Choice，候选标签由代码分配并映回已授权session或Argus本地waiting wake；每消息可分组，不把整批交错话题强行归一。不存在候选且单消息时可确定新语境；已有候选但分类不可用时不猜新话题，保留pending。

scheduler将三件事分开：推进当前可能执行owner、路由新观测、scope空闲时接纳最早waiting批次。同群实际执行串行，但运行中仍接收/路由新消息。同语境附加到当前未封口binding后只发送无正文notify；另一语境持久waiting。候选查询的预期Gateway业务/传输失败只作为本轮路由不可用，保留新消息pending，不阻止已持久waiting批次推进；编程错误仍传播，不伪装空候选。

明确回复只选可信候选页返回的session。每轮最多两页各10个；已知目标不在页内只延后该条，持久公平分页/尝试水位。未延后的消息照常路由；未尝试新到达项优先，再轮转延后项，避免旧回复堵整群。退场目标不自动复活，该回复可保留排查，不伪装已经处理。

## 原表持久化和执行屏障

业务表仍为 `observed_items`、`wake_requests`，另有原有 `schema_version`，没有execution/topic/inbox新表。

- observed_items.detail.argus_session_id保存语义归属，正文仍在原content，wake_id表示当前处理批次。
- wake_requests.handoff保存target、版本、提交可能发生标记、binding、报告/封口和提醒代数；冻结payload/指纹不因运行中附加改变。
- schema3只替换旧唯一索引并加必要查找索引：v2 waiting可共存，可能实际执行的owner仍唯一；v1未知执行仍构成屏障。
- assignment取得scope锁后重新FOR UPDATE/populate_existing读取并校验当前归属，竞争的不同目标不能用旧ORM快照覆盖。
- prepare_submission在事务内竞争实际owner；状态查询不是锁，网络未知不证明scope空闲。首次可能提交前若冻结集合因编辑/撤回失效，事务封存旧的未发送批次，并释放其中仍最新、未展示、未处理的当前boot消息回pending，保留语义归属；可能已外送的请求不适用，不改写其冻结指纹。

## 交接、主动读取和确认

内部HTTP继续服务认证、严格版本和scope/binding关联校验。`available`只返回准确pending_count/watermark；session pending包含无新binding的waiting，供Agent续接资格/清理核对，不带正文。

v2 read/history/wait不会在生成HTTP响应时登记已读。wait先查当前语义/批次尚未确认的initial和附加work，不被initial最大时间水位跳过；其他群历史仅context，不能因此report。

pending资格在SQL限页之前过滤boot/session/status/deleted/self/latest/exposure。准确计数按500条fetch chunk流式检查出口策略，不把500当总数。一次wait扫描上限仍有界；全窗口被策略过滤时返回opaque pending-scan continuation与has_more，后续继续到安全尾部。普通forward cursor不能跳过尚未确认的work。

`deliver`仅对当前合法assigned work登记曝光。服务器不跟踪模型实际看到的字符范围；Agent插件必须在最终prompt/tool视图完整或连续正文范围完整后才调用，部分HTTP成功不等于已交付。

报告继续验证scope/session/boot/revision/safety/exposed，保持原report幂等和accepted/rejected/unknown区别。finalize与attach串行；原initial未结清仍unresolved，未处理附加项解除旧wake认领但保留语义归属，后续同session重新分批，不自动no_action。旧boot遗留附加项不借新binding自动成为work。

## 提醒与故障

新附加work增加handoff.notify_generation，重复attach不增加。notify网络调用在DB锁外；成功只条件确认发送时看到的代数，旧成功回执不能清掉更新一代超时后的重试义务。正文始终已在库中，提醒本身不推进exposed/report/正文游标。

严格解析后的v2错误保留原请求版本；预验证/认证错误遵循既有安全默认，客户端不能把未知版本响应当成功。assigned continuation同样使用配置的总字符预算，超出项留在原语境pending，不重新分类或伪造完成。

## 验证与后续边界

测试使用隔离PostgreSQL、唯一schema、本地模型/传输fixture；211完整Argus测试包括迁移表集合不变、旧v1指纹、竞争归属、>500计数/过滤尾部、回复公平性、通知代数、分批预算和真实生产装配。没有正式迁移、实际JEV或QQ/MCP调用。

Agent拥有最终模型视图/分块确认、短引用、监听退出与300/86400生命周期；Gateway拥有候选items→sessions和v2可信转发。实际三应用v2主动读取与不打断执行已有完整离线验证；provider和分类器决策为本地确定性fixture，真实JEV语义质量及QQ动作仍留用户验收。
