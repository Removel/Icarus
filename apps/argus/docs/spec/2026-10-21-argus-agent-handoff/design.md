# Argus 第二阶段交接设计

> 第二阶段设计；2026-10-22 已接线并完成离线回归及本机真实测试，尚未正式部署。实际现状见arch。
> [共同契约](../../../../../spec/2026-10-21-argus-agent-handoff.md) 拥有 wire 与跨应用规则；
> [arch](arch.md) 为源码基线，[plan](plan.md) 为本应用实施任务。

## 1. 目标与边界

保留 QQ 采集、revision、规则/去抖、预算和私聊强信号；将 mock adapter 扩为显式可选真实
Gateway adapter。Agent 自主读/查历史/等待/逐项报告，不预定其工作步骤。Argus 不拥有会话
runtime、不猜测 Agent 是否已停、不发 QQ 消息、不接 SnowLuma/QQ MCP、不新建平台监听路径。

控制面 wake/status/cancel/release 经 Gateway 专用 /argus/rpc；Agent 的 bind/deliver/read/
history/wait/report/finalize 直连 Argus。两边独立数据库和边界 schema，不 import 对方源码。

## 2. schema v2 与数据意义

只增加两列并保留原状态 CHECK/数据：

- wake_requests.handoff JSONB：mode、binding、origin_boot_id、request_fingerprint、initial_cursor、
  phase/stopped、accepted report_cursor、report_log、finalize 结果。binding 含 binding_id 与可信
  execution/session/task/scope。report_log 按 report_id 存 fingerprint、disposition、receipt、
  有界 items；不存整段模型/工具输出。PG JSON 更新必须锁 wake row，避免部分报告互相覆盖。
- observed_items.exposed_wake_id UUID：当前/最近一次合法提供它的 wake（不是 execution UUID）。
  已有 wake_id 才是工作归属；origin 由冻结 payload 的 observation_id 集合派生。

schema v1→v2 为版本锁内事务迁移，未知版本拒绝。旧 wake 标 mode=mock，status/outcome 保持原值。
新空库与迁移库终态一致；不 DROP、不 create_all 冒充迁移，不新增业务表、seq 或 report 表。
同 scope 的 (source,chat_id,observed_at,id) 和真实活跃 wake 跨 boot 约束；真实活跃条件是
handoff.mode=gateway、local_terminal不为true、且尚未status确认release_complete，不只看resolved。
local_terminal仅在持久证明从未发送、或唯一发送明确未reserve且没有此前未知尝试时设置；网络
调用前先写submission_may_have_run=true，未知不能凭found=false/后次busy解除。明确未发失败
不会等待不存在的execution。capacity_busy本身继续waiting；旧boot安全放弃未发waiting可local_terminal。
legacy mock 继续原 boot 约束，不能不加区分地把旧 mock accepted 永久锁群。

### 严格单调入库水位

observed_at 在 v2 表示严格单调的本 scope 入库水位，sent_at 仍表示平台时间；不迁移改写旧记录。
事务先取 scope advisory xact lock，再取既有 message lock（所有写入同顺序，避免死锁）。
新 revision 在锁内查询此 scope 最大 observed_at，取
`max(可信 UTC clock_now, max_observed_at + 1 microsecond)`，没有旧行则 clock_now。
不直接沿用 parser 的锁前 draft.observed_at；duplicate 不新增行/不推进水位。scope 锁一直持有到
commit，后继时间严格更大，uuid4 只作稳定次排序，不依赖随机 UUID 单调，也不怕等时或时钟回拨。

原有 debounce 基于该入库时钟；旧未来水位会被保守延续，平台 sent_at 不被更改。跨 scope 不要求
全局有序。确定性测试包括锁前时间反序、相同 clock/反向 UUID、clock 回拨、commit 阻塞与重复消息。

## 3. 内容出口策略与跨 boot 工作资格

新增应用内 ObservationAccessPolicy，复用现有 NFKC/casefold 敏感检测，不在 API 层复制规则。
在完整规范正文及将输出的文本元数据上先检测再截断；敏感/无权限内容不能经初始 excerpt、bind、
deliver、read、history、wait 或 ignored 历史绕过。read/deliver 已知受限 ID 返回 content_restricted；
页查询跳过、不给 work grant，不把消息改 processed，日志不写原文/命中词。

冻结 payload 在首次构建和每次出站前复查；若未发过且策略变化，不修改原 payload/指纹，而是明确
失败本 wake。若可能已发，按 ID/status 对账和取消，不再发受限 payload，也不生成另一份同 ID 内容。
旧 payload 存库不等于允许重新外送。reason、引用文本元数据也经过相同检测；受限 bind initial
只给安全 ID/标记，不输出正文/locator/名称。安全初始贡献全空时 Agent start_failed 后封口。

origin=initial/incidental 看冻结集合；role=work/context 根据 binding.origin_boot_id、status、
wake_id、最新 revision/撤回和输出策略判断：

- initial：归本 wake 且仍最新、非撤回、符合策略的版本为 work；bind 不算 deliver。
- incidental：属于 binding.origin_boot_id、new/wake_id=null、最新非撤回且符合策略才 work。
- 其他 boot、已处理/忽略/失败、非最新、撤回、其他 wake 所有项只 context；敏感或权限不足不输出。

Argus 独立重启后，旧 binding 可以结清其原 boot 的合法 initial/已读或新读 incidental。新 boot
内容对旧 binding 只为 context，保持新 boot 待办供释放后的新 wake。新 binding 不恢复旧 boot
待办。已有 grant 不冻结版本/安全资格；后续编辑/撤回/策略变化导致拒绝，按报告拒绝终态结案。

## 4. bind、送达、分页与长消息续读

所有内部请求 header Bearer，body protocol_version=1，路径 wake 与 binding 身份交叉校验。
bind 只接受可信宿主 execution/session/task/scope/origin_boot/fingerprint，核对冻结 payload；
同身份重试返回同 binding，不同 execution 冲突。持久 initial_cursor 为初始引用最大水位。

初始 prompt 先有界组装、/deliver 实际展示 ID，再发布贡献；/deliver 只接受冻结 initial，不能
把 incidental 升级认领。/deliver 全批原子登记 exposed_wake_id，受限/未知项拒绝，不部分送达。
后续 read/history/wait 在准备实际响应页时登记 grant，插件另记实际交 task 的 exposed；
exposed_wake_id 不表示模型理解或消息已处理，不将 incidental.status 改 delivered/wake_id 改归属。

- read(observation_id,text_offset=0,max_chars=20000)：同 scope 的不可变版本，返回 deleted/latest
  和完整规范文本的有界片段，next_text_offset 为下一 Unicode 字符位置或 null。offset 0..len，
  越界 422，max_chars 1..20000；偏移不计元数据，正文为空时 offset=0 正常返回。附件只给有界
  安全引用，不自动下载。每块复查策略，不悄悄换新版；旧版可读但 context 不可新报。
- history(cursor?,limit=20)：默认从 initial_cursor 之前向旧翻，同 scope 可跨 boot，1..50 项。
- wait(cursor?,timeout_seconds=30,limit=20)：向新翻，0..30 秒；插件省略 cursor 时注入初始或
  已持久成功水位，服务端不取调用时 head。显式 cursor 校验 scope/方向，history 与 wait 不混用。

页 items/next_cursor/has_more/truncated 遵守共同 wire，文本合计≤20000 字符，响应≤1 MiB。
页预算截断只截已返回项正文，不能游标越过尚未返回的可见项；单条正文尾部用 read 续读。
服务端安全过滤的行可扫描并推进 cursor（空页也可推进），否则受限行会造成无限空轮询。
为限制稀疏过滤扫描，每次最多扫描 500 行；未到 head 则 has_more=true，wait 不再次长等。
没有扫描到新行的超时保持原 cursor。work grant 只登记实际返回引用。

longpoll 先查 DB → 登记 scope notifier → 再查 DB → 等待/重查；通知 commit 后发且仅优化延迟，
PG 为事实。无新消息 200 空页，取消传播并清 waiter；异常不吞成 task failed。服务最多一个进程
拥有 scheduler；独立 listener 与本机 listener 复用同一服务和 notifier，不建第二常驻监听。

## 5. 逐项报告、拒绝终态与封口结果

report 全批校验 binding、scope、真实 observation_id、grant、role、当前版本/状态，不接受模型
指定执行身份。handled/no_action 是 Agent 声明，不是 verified；reason≤1000 字符且不存任意输出。
重复 report_id 先查已存 fingerprint/回执，再验当前状态：同内容重放原回执，不能因已 processed
而拒绝旧成功。同 ID 异内容返回 report_id_conflict，不覆盖原记录。

为防跨 ID 同义重报，已由本 wake 报告的同 observation、同 status 幂等成功（保留原 reason），
不同 status result_conflict；一个新 accepted report_id 使 report_cursor+1，相同 ID 重试不增加。
incidental 接受时事务内校验仍 new/无其他归属，原子写 wake_id 本 wake、status=processed；
initial 则结清原归属。整批失败不更新任何观察项。

业务永久拒绝 item_not_reportable/content_restricted/result_conflict 同样在 handoff.report_log
持久 fingerprint 与 rejected receipt，但不增加 report_cursor、不结清项。HTTP 409 返回明确
rejected，Agent 可持久结案；认证/结构/存储失败不冒充 definitive rejection。报告日志更新和处理
事务同锁串行，丢成功/拒绝回执均可重试原 ID。finalize 后仍可重放已存报告，不接受新报告。

finalize 仅可信宿主、execution_stopped=true、report_cursor 与服务端接受水位一致。服务端不靠
accepted/cancel_requested 猜停止，也不负责证明宿主工具已停。原子封存 handoff.finalize：

`{exit_kind,report_cursor,initial_results:[{observation_id,result:handled|no_action|unresolved,
reason?}]}`。报告结果从已接受日志派生；未报告 initial 保留 wake_id，observation.status=failed、
status_reason=unresolved，具体 exit_kind 放 finalize（不把未处理伪装 processed，不新增 CHECK）。
已报告项保持 processed；未报 incidental 即使 exposed 仍 new/wake_id=null。正常、失败、取消
退出适用相同来源区分，未报 initial 不自动重投，新 revision 不受旧版本结清影响。

所有已绑定真实执行封口后 wake.status=resolved，含零报告/failed/cancelled；resolved 只表示封口，
详细业务结论看 initial_results 和 exit_kind。未进入执行的明确提交拒绝可 failed。重复 finalize
同证据重放，异证据 conflict；封口后不提供新内容/新报告，直到 Agent status 确认 release_complete
仍保留本地真实占用屏障。不能从 resolved 本身判断群空闲。

## 6. 真实客户端、状态与调度

execution.mode=mock|gateway，默认 mock；gateway 缺配置明确失败，不回退。Gateway URL 指向
/argus/rpc（本机或 LAN/VPN 专用 listener），ARGUS_GATEWAY_TOKEN 环境提供。Agent 数据面 token
ARGUS_AGENT_API_TOKEN 独立，不复用 OneBot secret。workspace 不出现在 wire/config 的远端提交字段。

GatewayAgentClient 复用 IcarusClient.call；透传完整安全 error.data，用共同 fixture 保证业务
code/retryable/owner。wake/status/cancel/release 输入均带版本和 token。业务 wake_id/规范化 payload
不变，实际 JSON-RPC id 可变。status found=false 才映射 None；unavailable/unknown 不是 None。
scope_busy/capacity_busy 保留 waiting、原 wake_id、公平退避，不耗尽为未知失败，不重新调 Jev。

真实占用跨 boot 持久核对，handoff.phase 可 cancelling/reconciling/stopped，与 observation 结清独立。
首次发送前重验版本/出口政策；可能已发后的不确定回执先对账，不换初始集合。旧 Agent 活跃则等
待或 cancel；cancel accepted 不释放。默认 15 分钟由 Agent 计时，原 mock 60 秒只约束 mock。

可能已有远端execution的wake，只在status.release_complete=true后允许下一同scope wake；
确定从未发送/未reserve的local_terminal例外按第2节本地收尾，未知不得套用。查询不可用记录
reconciling。
每次推进只发有界控制 RPC，不挂住 scheduler worker 等完整 run/wait；max_concurrent_chats 仍仅
推进 worker 数。Agent 默认 8 个持占用的 execution 名额才是实际全局后台上限，两者不混同。
shutdown 有界发 cancel，确认不了持久 reconciling，其他 scope 正常；不恢复旧业务、不清真实占用。

## 7. 独立部署与验收

现有本机 FastAPI listener 保持 loopback，挂公开 GET/OneBot/health；新增可选服务 listener 只挂
内部认证 prefix+health，不挂原文 GET/OneBot。两个 app 路由壳在同进程复用一次 lifespan 初始化的
数据库、services、scheduler；不能每 listener 再启动一次 boot/scheduler。远端 listener 无 token
拒绝启动。服务地址仅可信私有 LAN/VPN，显式 ACL，无 TLS，不支持公网，不信 forwarded header。

部署/迁移另获授权。离线隔离 PG + 本地模型 fixture 验证：v1 保留、严格水位、全文敏感尾部/旧
payload出口、分页过滤推进与预算不跳项、长文本全部可续读、跨 scope/boot/版本资格、送达不认领、
部分接受/永久拒绝/幂等封口、零报告 unresolved 展示、未知 submit 不重跑、scope 释放前不再开、
远程服务不暴露 GET、本机功能兼容。mock 历史和模拟语义保留，不以旧真实任务切 mock 冒充完成。
