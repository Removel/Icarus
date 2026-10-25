# Argus 真实 Agent 接线跨应用约定

- 日期：2026-10-21；首次进入 Git 时核对日期，之后保持文件名稳定。
- 状态：第二阶段接线与简化 MCP 接入已实现；2026-10-22 完成离线回归、独立审阅问题修复和本机真实链路验证，尚未正式部署。
- 范围：Argus、Gateway、Agent。第一阶段 mock 已实现；第二阶段通用观测交接；第三阶段真实 QQ 动作。

## 2026-10-22 简化接入决议（优先于下文第二阶段历史约定）

用户明确选择直接复用已有MCP工具，不新增QQ动作白名单、账号/群范围校验、独立发送模块或发送
outbox。Argus后台会话与普通会话使用同一可信配置的mcpServers，沿用现有list/search/execute；
后台固定说明允许模型按需使用QQ工具，最终正文不自动发布，想发消息须实际调用工具并检查结果。

下文“后台MCP为空/第二阶段不发QQ/第三阶段先做动作策略”记录原阶段取舍，已被本决定覆盖。
Argus观测读/报告的binding校验、逐项记账、同scope执行占用和退出机制仍保留；这些约束不延伸到
通用MCP动作，不承诺SnowLuma仅操作当前账号/群、平台发送幂等或OS/凭据隔离。配置的write模式
开放哪些能力，就可由模型调用哪些能力。真实发送验证与正在运行服务的重启仍由用户另行发起。

## 修订依据与实施边界

用户先接受规格修订，随后授权当前feat/argus原地实现与本地提交，未授权推送或正式部署。
自动回归使用隔离数据库和本地模型fixture；用户后续真实群聊测试已由持久记录核对，运行配置、
凭据和原始群聊记录不提交。以下默认值来自本轮设计选择，MCP部分以顶部简化决议为准：

- 保持严格停止承诺：输入队列收束、run finally、会话内工具实际结束及停止记录落盘，缺一不可。
- 永久拒绝报告持久结案；网络未知重试，不能让 rejected 永久堵住 outbox，也不伪造完成。
- 不加 seq 列；Argus 在 scope 锁内分配严格递增的入库水位。
- 旧 binding 可完成其 origin_boot 的工作；新 boot 消息对旧 binding 仅作 context，留给新执行。
- 长消息提供正文分块续读；敏感词限制所有 Argus→Agent 的内容出口，不只限制叫醒/Jev。
- Argus 后台会话不继承任何全局 MCP 服务器；interactive 保持现状，不提前设计第三阶段动作策略。
- 远程使用专用服务入口；普通 Gateway RPC、Argus 无认证 GET 继续只在 loopback，不能直接外放。
- 后台 execution 总名额默认 8，包含 preparing/queued/running/cancelling 和 pending_sync；wait
  保留名额和本 scope，不占 Argus 推进 worker。其他 scope 只有在全局名额可用时才能启动。

## 文档所有权与现有基线

| 所有者 | 设计 | 当前架构与差距 | 实施计划 |
|---|---|---|---|
| Argus：事实、过滤、读取/等待、逐项记账、真实客户端 | [design](../apps/argus/docs/spec/2026-10-21-argus-agent-handoff/design.md) | [arch](../apps/argus/docs/spec/2026-10-21-argus-agent-handoff/arch.md) | [plan](../apps/argus/docs/spec/2026-10-21-argus-agent-handoff/plan.md) |
| Gateway：受认证窄 RPC、隔离的服务入口与错误 | [design](../apps/gateway/docs/spec/2026-10-21-argus-agent-handoff/design.md) | [arch](../apps/gateway/docs/spec/2026-10-21-argus-agent-handoff/arch.md) | [plan](../apps/gateway/docs/spec/2026-10-21-argus-agent-handoff/plan.md) |
| Agent：持久执行/会话、真正停止、普通 Argus 插件 | [design](../apps/agent/docs/spec/2026-10-21-argus-agent-handoff/design.md) | [arch](../apps/agent/docs/spec/2026-10-21-argus-agent-handoff/arch.md) | [plan](../apps/agent/docs/spec/2026-10-21-argus-agent-handoff/plan.md) |

源码基线 `7fdb6bc`。arch 描述已检查的代码，目标明确标记；不沿用旧测试通过数作为二阶段证据。
两边独立存储，不 import 另一应用的 Python 类型、不互写数据库、不建立根共享包。

不新增业务表：Argus 现有 wake_requests/observed_items 各加 handoff/exposed_wake_id；Agent
现有 sessions 加 scope_key/properties，报告 outbox 用 conversation_updates。Agent 的 properties
是 SQLite JSON，不是 PostgreSQL jsonb。具体迁移、索引和内部方法由各应用文档拥有。

## 共同职责与非目标

Argus 唯一采集消息，保存不可变版本并拥有规则/业务记账。Agent application 持久保存
wake→execution/session/task 和排他 scope，复用 SessionRuntime；Gateway 仅验证/转发控制面，
不建数据库或第二套 runtime。控制面 wake/status/cancel/release 经 Gateway；数据面
bind/deliver/read/history/wait/report/finalize 由 Agent 直连 Argus。

Argus 是 Agent 普通一级插件，不注册子插件。固定后台说明在初始化时通过 static_prompt_sections
进入 System 的 session 层；动态 scope、群名、消息原文和进度仅进入当前 User Prompt。
ReActAgent 保持无状态；Plugin Runtime 只按来源插件路由；Hook 不承担业务路由或记账。

第二阶段不配置、启动或调用 SnowLuma/QQ MCP，不发 QQ 消息。保留正常工具/记忆能力与既有
项目确认规则，不承诺 OS/network 沙箱或外部服务动作回滚。历史回放、自动恢复旧待办、thread
聚类、运行中强推消息、动作核实 verified、新语义记忆和完整动作日志均不是本阶段目标。

## 身份、规范化与预算

- wire 版本固定 1。UUID 为规范小写带连字符字符串；时间为 UTC ISO8601（locator.time 例外）。
- wake_id：Argus 生成的稳定幂等键；origin_boot_id：Argus 创建该 wake 时的 boot，不是占用命名空间。
- scope：`{source,account_id,chat_kind,external_id}`。v1 仅接受 source=qq、chat_kind=group/private；
  其他来源明确拒绝，不猜测 QQ schema。账号/目标为 1..64 位无前导零的正十进制 ASCII 字符串。
- execution_id/session_id/task_id 由 Agent 分配，模型不可自选；binding_id 由 Argus 分配。
- observation_id 是不可变版本 UUID；同一 message_id 的编辑/撤回创建新 UUID，不覆盖旧正文。
- 后台 workspace 和服务 URL 来自可信配置，wire 不携带路径、callback URL、密钥或任意会话指令。

WakeRequest 必填字段：protocol_version、wake_id、origin_boot_id、scope、reason、rule_id、items。
reason 为 1..1000 字符；rule_id 为 null 或 1..128 字符。items 为 1..100 个不重复 observation_id：
`{observation_id,source,chat_id,message_id,revision,locator,excerpt,truncated}`，字段全部必填。
revision 为整数 ≥1，message_id 为 1..128 字符，truncated 为严格 bool；所有 excerpt 合计最多
100000 Unicode 字符。chat_id 必须精确为 `qq:{account_id}:{chat_kind}:{external_id}`。
locator 精确为 `{account_id,chat_kind,external_id,message_id,time}`，与 scope/item 逐项一致；
time 是有限 Unix 秒，不接受 bool。控制请求 UTF-8 JSON 最大 1 MiB；token 不计入业务指纹。

规范化：先验证，UUID 规范化，locator.time 的整数值统一成整数（1 与 1.0 等价），其他字段
保持内容不变，不折叠 Unicode/空白；items 按 observation_id 排序。对完整业务对象（包括版本和
显式 null）用 UTF-8、对象键排序、无多余空白、ensure_ascii=false 的 JSON 求 SHA-256。
相同 wake_id+指纹返回原身份；不同指纹 conflict。所有应用使用相同 fixture 验证，不共享源码。

## 控制 RPC 契约

普通 `/rpc` 保持既有行为且只监听 loopback。新增专用 `/argus/rpc` 只注册以下四方法，在
loopback 服务和可选独立服务 listener 上均可提供；它不接受 session/memory/runtime 等普通方法。
所有四方法都必填 `caller_token` 和 `protocol_version:1`，未知字段拒绝。

| 方法 | 除公共字段外的输入 | 成功 result |
|---|---|---|
| argus.wake | WakeRequest 的全部其余字段 | `{protocol_version:1,accepted:true,execution:ExecutionView}` |
| argus.status | `wake_id` 或 `scope` 恰好一个 | `{protocol_version:1,found:bool,execution:ExecutionView\|null}` |
| argus.cancel | `wake_id,reason`（1..1000 字符） | `{protocol_version:1,accepted:true,execution:ExecutionView}` |
| argus.release | `wake_id` | `{protocol_version:1,released:true,execution:ExecutionView}` |

status 未找到使用 found=false/execution=null，不用 error，不代表其他查询或服务不可用。
其他成功响应 execution 非 null。cancel 的 accepted 仅说明请求接受；release 只催办/卸载
已安全收尾的资源，运行或仍 pending_sync 时返回 busy，不强制解锁，不删除幂等记录。

ExecutionView 的全部字段均必填（可空者明确为 null）：

| 字段 | 类型/语义 |
|---|---|
| wake_id,origin_boot_id,execution_id,session_id,task_id | UUID 字符串 |
| scope | 上述 Scope |
| phase | preparing / queued / running / cancelling / stopped |
| exit_kind | null / completed / failed / cancelled / timed_out / interrupted / start_failed |
| cancel_requested | bool |
| execution_stopped | bool；满足完整停止屏障才为 true |
| report_cursor | 非负整数；Argus 已持久接受报告的连续序号，初始 0 |
| sync_state | pending / pending_sync / finalized |
| release_complete | bool；仅停止、报告结案、远端封口（或下文确定未绑定例外）及本地占用清除后为 true |

stopped/exit_kind 不等于 release_complete。execution_stopped=false 时 exit_kind=null；
release_complete=true 蕴含 phase=stopped、execution_stopped=true、sync_state=finalized。
按 scope 查询只返回仍持有 scope 的 owner；已释放记录仍可按 wake_id 查询。新提交由持久事务
再次竞争 scope/全局名额，不能把 status 的检查当锁。释放名额不要求运行资源卸载成功。

形状/版本/预算错误沿 JSON-RPC INVALID_PARAMS(-32602)，认证错误和业务错误使用
BUSINESS_ERROR(-32000)，固定 `data:{code,retryable}`，必要时加安全的 owner:ExecutionView。
code：unauthorized、submission_conflict、scope_busy、capacity_busy、busy、not_found、
execution_unknown、unavailable。unauthorized/conflict/not_found 不可重试；busy/capacity_busy/
execution_unknown/unavailable 可重试，但不能以重试失败证明空闲。内部未知异常为安全
INTERNAL_ERROR(-32603)。不返回 token、原文、连接串或 traceback。

## 启动、取消、停止与重启

固定顺序：reserve execution+scope+session/task 并持久指纹 → Argus bind 确认 → 持久 bound →
持久 launching → 内部预留 task 提交到既有队列。bind 网络请求不持数据库事务锁，返回后重查取消。
launching 后崩溃不重新入队，只对账/中断；接受明确的“未执行但中断”窗口，不承诺跨网络 exactly-once。
preparing/bound 重试复用原身份；取消 preparing 且 bind 结果未知时先对账 binding，不能直接丢记录。

正常完成或取消均满足四个屏障后才持久 execution_stopped=true：

1. 本次输入队列及上下文准备已收束，不能因 AgentPlugin 尚无 active run 就提前判断结束。
2. 现有 AgentPlugin.drain() 确认 run/finally 真正退出；不新增 TaskControl.wait_exited。
3. 会话内工具执行均已实际结束；取消等待者不等于同步线程结束，async 取消未知也不能释放。
4. 终态与停止证据已落盘；报告 outbox 独立按下文结案。普通显式后台进程继续遵守其既有生命周期。

工具生命周期跟踪是通用执行设施，不识别 Argus/QQ；既有取消调用可以快速返回，停止控制器另等
实际退出。默认总执行期限 15 分钟，含排队/主动 wait，由 Agent application 计时发起取消。
超时后仍未确认退出则保持 cancelling，不按墙钟强制释放。不得把外部系统副作用已回滚作为承诺。

同一 Agent data_dir 只支持一个宿主，不新增 owner 锁。单宿主重启可确认旧进程内线程/协程已消失，
将未完成执行记 interrupted，恢复 outbox/封口而不是重新运行模型；此证据不宣称外部服务操作已撤销。
若旧宿主是否还活着不明确，不允许启动第二宿主。Argus 独立重启不证明 Agent 已停止。

Agent 后台执行名额默认 8（可信配置可调 1..100）；同 scope 只能一个。正在 wait 的 execution
仍占自己的名额，不占 Argus 调度/RPC worker，不额外占模型请求许可；本阶段不新增模型请求限流器。
满额的新 wake 返回 capacity_busy，不持久创建 execution；Argus 保留原 waiting wake，公平退避重试。
不承诺满额时第九群立即执行，不把 max_concurrent_chats 误当全局执行数。

Argus 自己的“从未远端创建执行”也必须可终结：在网络发送前先持久标 submission_may_have_run=true，
之后任意崩溃/超时保守视为可能已发。只有该标记从未设置，或此唯一发送尝试收到明确未 reserve
拒绝且没有更早未知尝试，才可本地 failed、标 local_terminal=true 并解除本地活跃屏障。capacity_busy
本身仍保留 waiting 重试；新 boot 放弃旧未发送 waiting 可走此分支。只要此前有未知尝试，即便
后一次返回 busy 或 status found=false，也不能据此放槽，须对账/取消原身份。

若已持久 reserve 但明确从未 bind/入队、outbox 为空，失败可凭此证据标 start_failed、
sync_state=finalized 并清槽/release_complete；这里 finalized 表示无远端结果待同步，不伪造 Argus
finalize 回执。Argus 按此状态将未进入执行的 wake 记 failed。只要 bind 结果未知，就不适用该例外，
须用原身份重试 bind/对账，确认存在绑定则正常 finalize。binding 缺失的配置/协议错误不自动推断
为“从未绑定”。

## 观测读取、过滤与跨 boot 资格

固定 scope 的四工具：read(observation_id,text_offset=0,max_chars=20000)、history(cursor?,limit=20)、
wait(cursor?,timeout_seconds=30,limit=20)、report(items)。模型不填写账号/群、wake/binding/session/task。
页最多 50 项，文本预算每次 20000 Unicode 字符，内部 HTTP 请求/响应均限制 1 MiB。
read 对稳定版本的规范文本按字符偏移分块，返回 next_text_offset；消息页游标不能充当正文偏移。

敏感词先按完整原文及将输出的文本元数据判定，之后才截断。叫醒、冻结摘录、bind、deliver、read、
history、wait 均适用，不能从 ignored 历史绕过。受限引用不输出原文/摘录/附件引用/命中词，
read/deliver 返回 content_restricted，页查询跳过受限项；不将其记 processed，不授予 work grant。
页可以推进越过被安全过滤的行，但不能跳过因页/文本预算尚未返回的可见行。规则过滤与读取权限
分离：普通 rule ignore/self-message 可作 context，权限级别 <1 禁止该 scope 的内容输出。
当前配置禁止的旧冻结 payload 不重新外送；若先前提交结果未知，仅按 ID 对账/取消，不换 payload。

三个维度分开：exposed 是看见，initial 是冻结 payload 的任务归属，handled/no_action 是明确完成。
origin=initial/incidental 仅由初始集合派生；role=work/context 仅代表可报告性，不表示认领。

role 以 binding.origin_boot_id 为工作代际，而非服务当前 boot：

- initial：属于冻结集合、归本 wake、仍为最新非撤回版本且符合输出策略，才可 work。
- incidental：属于 binding.origin_boot_id、仍 new 且 wake_id 为空、最新非撤回且符合策略，才可 work。
- 其他 boot、已处理/忽略/失败、旧 revision/撤回及其他 wake 所有的历史为 context；敏感/无权限
  的内容直接不输出，不以 context 绕过。既有 grant 不绕过版本、状态或安全策略复查。

Argus 重启后旧 binding 可继续报告原 initial 和其 origin_boot 的合法 incidental，不自动恢复旧
待办。旧 binding 可看新 boot 的安全消息，但只为 context，不得结清；新消息仍 new，待占用
释放后由新 boot 规则判断。新 binding 不接手旧 boot 遗留任务。这一选择避免把重启当旧报告失效。

bind 持久 initial_cursor 为初始引用最大的入库水位。同 scope 新写入必须取得严格递增水位，
不只串行 commit；具体算法归 Argus。首次 wait 省略 cursor 从 initial_cursor 开始，后续从插件
持久 last_returned_cursor 继续；只有工具结果成功接受后推进。显式游标仍校验 scope/方向。
history 默认从 initial_cursor 之前向旧翻页；wait 向新翻页，两者游标不可混用。无新行的 wait
超时返回空页与原游标；扫描到的仅受限行可返回空页和推进后的游标。新消息不自动强推到 run。

## 送达、逐项报告与结果封口

bind 只授予执行身份，不算送达。插件按预算组装初始贡献后调用 /deliver，登记实际放入提示词的
引用，成功才发布贡献；required 贡献失败不启动模型。工具 read/history/wait 在服务端准备响应
时登记 grant；插件 backend 仅暂存页，不能在 HTTP 返回即提交 exposed/默认游标。只有现有
AgentToolCompletedEvent 所带的最终预算后结果已进入 task，且完整引用页与暂存页一致时，插件
才确认 exposed 并持久游标。若通用预算截断/文件预览改写/预算异常/取消，整页保守不确认、不
推进，允许重读；模型可用更小 limit 或更高既有工具输出预算重取。不得只从未截断 metadata
声称正文/引用已交付；具体确认接线归 Agent，不用 Hook 或领域化 ReActAgent。
report 同时验证服务端 grant 与本地 exposed；确认事件尚未处理时 report 可返回可重试的
`delivery_pending`，不先落业务 outbox；wait 仍用上一已确认游标，
最多重复读取，不跳项。送达不承诺模型理解、网络恰好一次或外部动作已核实。

report 输入是 backend 生成的 report_id 和 `items[{observation_id,status:handled|no_action,reason?}]`；
1..50 项、ID 不重复、reason≤1000 字符。可以部分、多次；整批原子接受或整批拒绝。
相同 report_id+相同规范化内容返回原回执，异内容 conflict；相同 observation 的相同结论可用新
report_id 幂等报告，结论不同拒绝。reason 是有界说明，不把任意完整工具输出写进日志。

报告先持久 outbox 再发送。只有明确接受才计入 report_cursor（每个新接受的 report_id 加 1；
重试原报告不递增）。Agent 每 execution 串行发送，顺序按 outbox 序号；不得并行越过 pending。
所有已落盘报告最终须为 accepted/synced 或 definitive rejected；网络超时/503/未知不是 rejected。
确定拒绝也持久记录安全回执，移出重试队列，不增加 report_cursor、不算任何消息已处理。
模型修正后可新报；不能删除原拒绝记录或重跑模型补账。Argus 对合法 binding 下结构正确的报告
持久保存接受/拒绝幂等结果；认证/结构错误不伪造业务报告回执。

incidental 读取只写 exposed_wake_id，不写 wake_id、不改全局 new；报告成功才原子关联本 wake 并置
processed。initial 已有归属逐项结清。报告不终止 run、不释放 scope，已接受部分不因后来失败回滚。

执行真正停止后，先让全部 outbox 结案，再 finalize 携 exit_kind 和已接受 report_cursor。
Argus 原子封存：未接受报告的 initial 保留归属并记 unresolved；仅读未报 incidental 仍 new、
wake_id 为空，不变成 unresolved/failed。unresolved 放逐项封口结果，不新增观察状态 CHECK。
finalize 幂等，相同证据返回同封口；封口后只重放已有报告幂等回执，不接受新报告或内容读取。

任何真实执行（含零接受报告、失败或取消）完成封口后 wake.status=resolved，含义仅为“执行已
封口”，不声称业务全部完成；逐项结果与 exit_kind 独立表示成功/未完成。未进入真实执行且明确
拒绝的 wake 可 failed；规则不叫醒仍 no_action。legacy mock 状态保持不变。

finalize 确认后（或确定未绑定且无 outbox 的安全本地收尾），Agent 在同一事务清 scope 占用、
释放名额并标 release_complete。失败保持 stopped/pending_sync，后台重试记账，不重跑模型。
Argus 对可能已有远端执行的 wake 必须按 status 确认已释放才启动下一 wake；只有上文明确未发/
未 reserve 的 local_terminal 可本地解除屏障。不能以 resolved、execution_stopped、墙钟到期或
普通 task 查不到代替这些证据。

### 多问题验收

| 执行期间 | 退出并同步后 |
|---|---|
| 没读 B；或读 B 但没报告 | B 仍 new/无 wake 归属，释放后重新过规则，不保证再次叫醒 |
| 先报告 B，继续处理 A | B 结清，run/scope 仍活跃 |
| 报告 A/B 后退出 | 已结清项不再投递 |
| A 失败/取消，B 仅读 | 未报 initial A 为 unresolved，incidental B 仍 new |
| B 已报告，随后 A 失败 | B 不回滚 |
| 不同群 | 独立 scope，在全局名额可用时推进 |
| Argus 重启，旧 Agent 继续 | 旧 origin_boot 合法 work 可报告，新 boot 消息只作 context、留给后续 |

## 内部 HTTP wire

prefix 为 `/api/argus/internal/wakes/{wake_id}`，POST，header Bearer 固定服务身份；每个 body
必填 protocol_version:1。bind 接收 `{protocol_version,execution_id,session_id,task_id,scope,
origin_boot_id,request_fingerprint}`，其余端点必填 binding_id；身份与路径交叉核对。

| 端点 | 除公共版本/binding_id 外的字段 | 成功 result（均带 protocol_version:1） |
|---|---|---|
| /bind | 上述身份，不带 binding_id | binding_id、execution_id、session_id、task_id、scope、initial_items、initial_cursor |
| /deliver | observation_ids | delivered_ids；role/origin 由服务端生成，不允许扩初始集合 |
| /read | observation_id、text_offset=0、max_chars=20000 | item、text、next_text_offset:int\|null、truncated |
| /history | cursor?、limit=20 | items、next_cursor、has_more、truncated |
| /wait | cursor?、limit=20、timeout_seconds=30 | 同页结构 |
| /report | report_id、items | report_id、disposition:accepted、report_cursor |
| /finalize | execution_stopped:true、exit_kind、report_cursor | finalized:true、report_cursor、initial_results |

item 是 `{observation_id,source,chat_id,message_id,revision,locator,observed_at,deleted,latest,
role,origin,excerpt,truncated}`；read 的正文仅在 text，excerpt 可空，不复制全量 content。
initial_items 是冻结集合的安全有界引用，受限引用只返回 observation_id/role=context/origin=initial/
content_restricted=true，不返回其他文本；整个 initial 安全贡献为空时本次输入 start_failed。
/deliver 只接受冻结初始引用，incidental 送达由工具页登记。

结构正确的业务报告拒绝使用 HTTP 409，安全 body 为
`{protocol_version:1,report_id,disposition:rejected,code,report_cursor}`，code 固定为
item_not_reportable / content_restricted / result_conflict / report_id_conflict / execution_closed。
只有前三类且服务端明确确认的本次请求拒绝可结案；report_id_conflict 需对账原指纹/回执，不得
用新内容覆盖，execution_closed 需取原回执或 finalize 结果，未核清前保持 pending_sync。
其他 HTTP 错误 `{protocol_version:1,code,retryable}`：400 版本、401 认证、403 scope/身份或
内容权限、404 未知 ID、409 其他生命周期冲突、422 结构、503 存储不可用。认证/协议/配置错误
使同步进入显式错误并告警，不自动当报告拒绝或解锁。空页/等待超时为 200，协程取消继续传播。

## 认证、部署与第三阶段

Gateway ARGUS_GATEWAY_TOKEN 与 Agent→Argus ARGUS_AGENT_API_TOKEN 独立、只从环境提供，
不可复用 OneBot 签名 secret。token 不进入模型、持久业务指纹、工具结果、日志或验证异常。
后台会话不能通过普通 session API/订阅访问，内部 trusted application 入口不得用模型可填 bool。

同机默认 loopback。独立部署可选服务 listener 只绑定可信 LAN/VPN 地址：Gateway 仅提供
/argus/rpc+health（四个受认证方法）；Argus 仅挂内部认证 prefix+health，不挂公开 GET 或 OneBot
入口。原 listener 仍 loopback。两个入口复用同一进程、同一 runtime/services，不启动第二 Agent
或第二 Argus scheduler。未配置服务 token 则拒绝开启远程 listener；部署还须显式网络 ACL。
本阶段不做 TLS，不支持公网或不可信网络。不得用 forwarded header 把远程请求当 loopback。

第三阶段启用 QQ 动作前，另行设计通用 MCP 白名单与凭证隔离：SnowLuma 是通用 OneBot action
代理，目标来自每次 group_id/user_id，read 模式含凭证类 action，write 模式暴露整个账号动作面；
发送无幂等键，间接 message_id 无法天然证明群归属。普通 Bash/浏览器能否取得凭据也须解决；
不能用 prompt 或第二阶段读取接口代替动作授权，不自动开启任何真实群/模型冒烟。

## 依赖顺序、验收与回滚

1. 冻结上述 JSON 契约并在三应用各自测试目录存等价 fixture，不建立共享 Python 包；先做 Agent
   通用会话属性/持久占用及执行生命周期设施，按应用拥有实现与测试。
2. Argus schema 迁移、严格水位、内容过滤、绑定/读取/等待/报告/封口。
3. Agent 普通插件、背景 MCP 隔离与 outbox；Gateway 四 RPC/专用入口；真实 adapter/对账接线。
4. 离线贯通真实 Gateway/AgentRuntime/Argus、隔离数据库、本地确定性模型；覆盖多问题矩阵、
   队列未启动不提前释放、同步线程/async 取消未知、永久拒绝报告可结案、跨 boot、丢回执、
   乱序游标、敏感尾部、正文续读、已有 QQ MCP 不继承、满额公平重试、远程普通入口不可达。
5. 真实模型、SnowLuma/QQ MCP 和正式部署另获授权；本轮计划不执行。

保留旧数据/mock 历史，不清库、不自动降 schema。回退前确认真实执行满足全部停止屏障并且
outbox 持久；保留 status/cancel/记账路径直到收尾，不切 mock 冒充旧真实任务完成。
