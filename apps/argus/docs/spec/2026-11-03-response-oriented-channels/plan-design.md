# 回应驱动 Channel 修正设计（Argus）

日期：2026-11-03。**状态：设计已获用户授权实施，额外业务state字段已按用户要求取消；此文是设计约束，不是部署记录。** 基线 `43312c487fd02307f5745fef456c586bd08bea94`。当前实现与验证见[arch.md](arch.md)和[execution.md](execution.md)；真实线上消息由用户验收。

## 1. 已确认目标

Channel 是需要 Agent 回应或处理的业务上下文，而不是“长期跟进任务”的同义词，也不是 QQ 群、私聊或 Agent Session 的别名。

- 一次打招呼可建立短 Channel，一个 Session 即可完成；连续相关问答优先延续已有 Channel。
- 大任务可跨多个 Session、多个业务阶段，Channel 的业务身份、进展和规则持续存在。
- 同一来源可以有多个不同主题 Channel；群聊不机械要求 @，由规则和语义判断是否需要 Agent 回应。
- 规则决定 `new / existing / ignore / defer`；不是每条消息都建 Channel，也不是每次唤醒都建 Session。
- 执行 Agent 直接使用正常会话能力，包括 SnowLuma MCP；动作完成后调用 Argus 工具一次确认并更新。Argus 不代发 QQ 消息。
- perception 是现有命名模型配置，默认用无工具单次 LLM 做较深语义判断；它不是被唤醒的执行 Agent。

协作设计：[Agent 设计](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan-design.md)、[Gateway 接线计划](../../../../gateway/docs/spec/2026-11-03-response-oriented-channels/plan.md)。实现计划：[plan.md](plan.md)。各应用拥有各自实现，因此不新增根跨应用 spec，也不覆盖既有历史设计。

## 2. 已核实缺陷与证据边界

| 问题 | 当前代码位置 | 影响 |
|---|---|---|
| 仅接受 `post_type=message` | `plugins/protocols/qq_protocol/plugin.py:11` | 合法 heartbeat/notice/self-message 都被转成422，产生误导日志 |
| 丢失消息结构和身份上下文 | 同文件:16–22 | 只存 text/sender_id；回复、@、私聊对端和自己发送信息不足 |
| discovery 把闲聊定义为 ignore | `model_provider/semantic.py:40` | “请打个招呼”等瞬时回应请求被排除 |
| 业务判断标准写死在模型适配器 | 同文件:40–51；`model_provider/discovery_fallback.py:14–23` | 规则未向决策模型明确传入 Channel 概念与回应标准，兜底也沿用错误目标 |
| 默认规则只发送 text；discovery只使用text/candidates | `plugins/workflow_rules/discovery.py:32–40`；`model_provider/semantic.py:46` | 不知道私聊/群聊、回复关系和历史语境 |
| 新 Channel 初始规则恒为不通过 | `plugins/workflow_rules/discovery.py:43–45` | Agent 如保持active却未发布后续规则，后续内容可能归属正确但不再唤醒 |
| ignored/exhausted 是该消息修订的发现终态 | `infrastructure/store.py:634` | 修改规则不会自动重判历史消息，不能把“改提示词”当旧消息已修复 |

只读内存 HTTP 复现：普通私聊message→200/入库；message_sent、notice、meta_event→422/不入库。历史日志同时存在200和周期422；此前“真实私聊一律422”的推断证据不足，抓包监听实际超时。此前数据库查询曾显示显式打招呼请求ignored，但本轮共享PG、Argus和Gateway都已停止，未重启或重新查询实时数据。本设计不宣称拿到了每条422的原始请求。

## 3. 正确主链与所有权

```text
平台消息 → 鉴权/协议规范化 → Observation持久化 → Event
  → 已有Channel的规则 + 新Channel发现（彼此独立）
      ignore：持久记录无需处理
      existing：记录归属，是否唤醒由对应Channel规则判断
      new：事务创建Channel并seed首批内容
      defer：规则允许时进入perception深判；不确定仍保留未决
  → 为当前处理轮选择关联Session或新建关联Session
  → 正常Agent处理/经MCP行动
  → argus_commit：确认冻结内容 + 更新业务状态/规则/生命周期/后续Session选择
```

- 协议 Plugin 仅规范化；WorkflowRules 负责业务编排与提交校验。
- 业务语义来自规则文件；model_provider 只处理供应商协议、typed问答和结果校验。
- Plugin Runtime 不解释具体Event、不引入平台业务；不新增父子Plugin；ReActAgent仍无Argus/Blackboard/UI依赖。
- 动态Channel、来源、业务要求放当前User Prompt，不改变稳定System Prompt。
- 一个来源已有订阅不阻断新话题发现；existing不是绕过Channel规则的强制唤醒。

## 4. 协议输入与自己发送的消息

### 4.1 HTTP应答

先验证原始body签名，再判断事件类型。合法 `meta_event` / 当前未支持的 `notice` / `request` 返回200：`{"accepted":true,"ignored":true,"reason":"unsupported_event_type"}`；不创建Observation、不调用模型。非法签名仍401；消息结构错误仍422并给有限稳定错误码。未知post_type返回422，不把所有垃圾请求吞成成功。

### 4.2 规范化结构

保留现有 `Observation.content/detail/source_key`，不新增平台消息模型。

- content：text、sender_id、message_type(group/private)、必要message段（text/at/reply及非文本类型元信息），不主动下载媒体或转存base64。
- detail：protocol、self_id、is_self、peer_id、reply_to（存在时）、规范化schema_version；id均规范化为字符串，原始输入为数据而非授权。
- 群聊source_key继续 `qq:<account>:group:<group_id>`。
- 入站私聊使用对端user_id；自己发出的私聊优先 `target_id`，缺乏可靠对端则明确拒绝，不猜成自己或别的私聊。群临时会话保留必要group上下文。
- `message_sent` 作为可规范化消息接收，不通过post_type直接决定业务ignore；`is_self`进入规则语义。不能仅凭账号相同把人工请求一律丢弃。**首版边界：目前没有可信的outbound-message关联账本，不能仅凭is_self、相同文本或模型判断确定消息是Agent回声，也不能承诺确定性防回声循环。** 默认规则对self上下文不足返回defer，可由只读perception判断；没有明确新请求证据不启动新的副作用任务。相关测试只证明分类政策与未决保留，不是平台级去重证明。保持本机reportSelfMessage现状，不在上线时自动开启；若真实使用必须开启且要求严格防循环，应先另行实现可信发送回执关联及echo早于commit的等待机制，再宣布该用例就绪。
- 所有上下文有界，继续服从现有入口body、Observation和Wake大小限制；媒体只有上下文引用，不声称已有视觉理解。

## 5. 规则拥有回应判断

### 5.1 公共semantic discovery调用

保持现有 `SemanticJudgeRequest`、`DiscoveryDecision`，在discovery参数增加显式 `criterion: str` 和 `context: dict`，保留text；可信候选仍由broker注入。criterion非空且≤4000字符；text≤16000字符，超过不静默截断关键语义：返回 `input_too_large`/defer并注明需分段或摘要。context规范JSON≤16384 UTF-8字节，历史最多8条、不得晚于当前Observation；候选最多20个、每个≤4096 UTF-8 JSON字节。超限不删除候选后假装“不存在”。

默认发现规则在自己的规则源里明确给模型：

公开上下文固定为 `conversation={source_key, platform:"qq", account_id, kind:"group"|"private", peer_id, sender_id, is_self:bool, reply_to?:str, mentioned_ids:list[str]}`；所有ID为字符串，peer_id为群ID或私聊对端，mentioned_ids最多32项。其他协议采用相同公共字段但platform不同，不伪装QQ字段。`recent_history` 为最多8个 `{id,observed_at,text,sender_id,is_self,reply_to?}`，由宿主按数据库position排序和截止；position只作内部边界，不给Observation公共DTO重复加字段；text保留在总context字节预算内；太大则明确history_incomplete，不让截断后内容伪装完整。规则用history能力获取本来源截至当前position的证据，宿主broker缓存实际返回并在semantic边界注入，conversation也由规范化元信息注入，覆盖脚本伪造的技术身份。默认规则可读取这份公开上下文；不取消RuleEngine对event.detail的剥离。

- Channel定义：需要Agent回应/处理的一件事，可短对话、可多阶段任务。
- 先判断相关性和回应必要性，再在候选中选归属；需要处理且无合适候选才new。
- 私聊直接招呼、提问、明确请求通常值得回应；群聊根据上下文判断，不要求@，也不自动回复群内所有人之间的闲聊。
- 引用、否定、假设、机器人回复回声不构成新的请求；但它们可能是已有事项的证据，不能全部机械ignore。
- 上下文不足要defer，不用“候选为空”默认new。

`SemanticBackend.discover` 不再内置“闲聊必须ignore/等待结果才是任务”；只把规则criterion/context/candidates编码为一次Choice问题。ignore/new/候选选项只作路由定义，业务含义由criterion表达。原生Choice.confidence保留0.7门槛，不改用较高probability。普通match仍0.5。

### 5.2 让深判获得同一判断任务

固定handler保存这一轮实际semantic调用的criterion与有界可信上下文；低置信度或合法规则结果显式 `deep_semantic_required` 时可调用perception。避免让规则任意伪造路由身份：rule产物扩展 `DiscoveryDecision` 的可选 `review_required: bool=False`，仅允许action=defer时true；它是业务请求深判，不是供应商异常旁路。最终持久路由仍四种action，review_required不成为新状态机。

LLM兜底接收同一criterion/context/candidates，不再使用固定“长期跟进”system业务标准。稳定System只规定无工具分类、JSON契约和不执行输入指令；当前规则要求和消息放User。保持同修订最多一次持久兜底尝试、20秒、输出2048 tokens、SDK重试0、未知结果不重发。高置信度ignore不因为事后不满意无限复评；标准修正靠规则与独立样本验收。

首版不启动完整perception Agent。若未来确实需要自主读取多处证据，可在同一结果契约下增加明确预算的只读Agent实现，另行批准；不是给规则脚本开放工具执行循环。当前历史能力已能满足已确认用例。

### 5.3 后续消息与终态

短对话可以完成，也可以保持active处理续聊；**确定政策：创建Channel时由固定handler安装规则源中定义的 `DEFAULT_RESPONSE_RULE`，替换旧恒False bootstrap。** 首条Observation依旧只seed一次；该规则只评估后续unread，并按本设计的回应criterion判断是否passed。首次argus_commit若省略rule_source则保留这份有效规则；Agent可显式替换为长任务专用规则。不得让ordinary existing直接seed绕过规则。completed不自动重开，明确新请求可new；是否关闭由业务判断，不按“每回复一次就完成”机械处理。

历史ignored/exhausted不启动时自动全库重放。验收用新message_id；若需重新处理旧消息，后续提供单条、可审计、显式批准的重评，不在本轮静默清状态。

## 6. Channel业务状态、Session关联与处理轮分离

- Channel生命周期：active/paused/completed。
- `progress` 继续保存业务阶段、进展和待办摘要；`goal` 保存目标，规则定义后续触发条件，Session历史保存执行上下文。**本轮不新增 `Channel.state`、`CommitPayload.state` 或快照state字段，也不引入固定业务状态机。**
- Agent沿用现有argus_commit更新progress/规则/生命周期，省略字段保持；跨Session交接使用已有Channel快照和必要历史，不维护第二份重复业务状态。
- Session运行状态属于Agent；Channel不把Session当业务状态。Session关联键与当前选中Session属于既有runtime执行metadata，不混入progress业务摘要。

### 6.1 一对多接口

Agent拥有实际Session和持久关联。Argus只指定本轮应选的关联槽位 `session_key`，不决定真实Session ID。

- 新Channel首次begin_round生成并持久化随机 `runtime.session_key`；每轮冻结该key，HTTP重试不能换key。
- `POST /argus/session` 的SessionRequest扩展必填 `session_key`；request_id仍用于幂等选择请求，不等于Session身份。
- Agent返回已关联或新建的session_id；WakeRequest增加同一session_key，且仍携带session_id。Agent验证(channel_id,session_key,session_id)的持久关联，不再验证由channel_id计算的固定ID。
- 多轮可复用同key；Agent在 `argus_commit` 中可传 `next_session="new"` 请求下一轮新执行上下文，省略或reuse保持。Argus事务提交时生成下一session_key并保存progress等已有业务字段，当前轮仍绑定旧Session。`next_session`不会作为业务列写入Channel，处理后消耗掉；它不表示强制关闭旧Session或Channel。
- 不在Channel.runtime保存无界Session列表；完整关联与删除后的消费标记由Agent既有SessionStore数据库内的具体关联表拥有，journal保留审计历史。当前key及round保持有界。
- 当前Store的每Channel冻结处理轮约束保留以复用现有CAS/确认机制；这是提交协调边界，不是把数据关系变回一对一。本轮不新增多机调度或并行多Agent工作流，后续并行必须单独设计冲突合并。

### 6.2 确认与状态更新

继续一个argus_commit覆盖每条冻结Observation，results.status=handled/no_action；可同时更新progress/rule/source/lifecycle/next_session。未成功的MCP发送不得宣称handled=已回复，不能因HTTP200认定平台动作成功。Argus不收reply_text代发，不引入普通assistant文本转发。

## 7. 数据与切换

本轮不新增Argus业务列，不需要channels.state列迁移，也不新增 `migrate-response-channels.py`。session_key复用既有Channel.runtime JSON，业务交接复用progress/规则/历史；观察表与Channel表均不重建、不删旧消息，不重置其他应用库。Agent侧Session关联的持久化仍按其计划单独实现，这与新增Argus业务状态字段无关。

旧Channel没有session_key时由受控切换认领一次key；只有该Channel在Agent关联表从未出现过任何键且没有未对账旧round，Agent才允许采用唯一可验证旧Session，否则正常新key选择或因歧义阻塞。首次持久关联关闭采用门槛，之后next_session=new不会再次选回旧Session。旧在途round保留旧binding直到对账；暂停新wake后升级，不在新旧协议间无限重试。切换流程详见Agent设计。

## 8. 验收与批准边界

必须覆盖：私聊招呼/续聊、群聊无需@但相关、同群多主题、否定/引用、self人工消息/Agent回声、短Channel和跨Session大任务、旧消息不重放、perception同上下文、候选变更/并发认领、暂停/完成生命周期、MCP成功/失败/未知、发送后commit丢失。

真实平台发送、第三方消息作为模型输入、付费模型调用、服务重启、数据库迁移均是单独明确的执行步骤，不由“批准文档”自动授权。本轮批准前不修改源代码或settings。当前baseline测试：Argus260、Gateway52通过；Agent1318通过/2项SessionRuntime drain超时（单文件复现），记录为验收阻塞，不顺手修复或伪称全绿。
