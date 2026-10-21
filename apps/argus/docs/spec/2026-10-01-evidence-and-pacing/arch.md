# Argus 证据与等待架构

> 历史架构记录：下文描述已被替换的 SQLite/证据/等待链路，不再代表当前源码。
> 当前架构见 [第一阶段源码架构](../2026-10-21-argus-positioning/arch.md)。原 design/plan 仅保留历史取舍。

## 层次与依赖

`connector/qq_connector.py` 验签和核对账号，`QQHandler.handle` 解析 QQ 观察后直接调用 `QQService.receive`。service 调 evidence 识别首次强信号，转为 entity 的表记录并调用 DAO 同事务保存；成功提交后才返回空204，不等待模型或投递。已知meta_event/notice/request由handler确认忽略，不调用service。未知类型或格式错误仍422。

- `clients` 封装 Gateway/Jev 协议、session 简单操作和类型化投递回执，不写DAO。
- `entity/records.py` 定义三张ORM表记录，业务对象提供同层to_row/from_row映射，不继承ORM。
- `dao/database.py` 持有SQLite与session生命周期；两个store共用公开事务入口，只收发表记录/查询结果。
- `evidence` 获取/组装聊天、真实工作状态与近期交付事实，QQ facts、transcript和prompt在evidence/qq。
- `strategy` 统一evaluate(value)契约，判断时机、介入、动作和发送资格；不拥有后台任务或数据库写接口。
- `decision_graph` 分离node/edge/graph/compiler/state/runner，不依赖业务组件。service显式编译后执行。
- `service` 编排接收、批次状态事务和submit/steer。BatchScheduler是service内部计时/恢复组件，BatchRuntime消费ready批次ID并执行周期检查；不是新的架构层。
- 入站队列与转手worker及其旧包装测试已移除；接收链只走connector→handler→service，批次ready队列仍保留。
- `utils/datetime_utils.py` 只做时间归一化等辅助操作。

## 图与单次状态

NodeBinding使用输入映射和requires，GraphCompiler校验声明并生成稳定顺序/只读索引，GraphRunner只执行编译产物。边有唯一id、可选collect动作、状态与产物；condition/collect失败只记该边失败，不破坏已成功源节点，目标blocked。节点与collect共用步数预算，passive边不计步。

QQ图为human → decision_evidence边（顺序收集conversation/work/activity/facts）→ code → 可选model → intervene → silent或relation/action → end。self-only不经过证据收集。

GraphRunner 返回通用 GraphRun(status/output/executions/selected_edges/run_id/error_type)，可以返回整数、字符串或 None。业务类型校验与故障映射留给 service。每次执行状态独立，节点结果映射只读；输出类型本身应使用不可变数据，映射只读不等于深度冻结任意对象。

分支仍为 all/any/selected；失败依赖 blocked、未选路径 skipped、any 冲突失败。节点/总超时与总步数限制保持，取消传播。RepeatBlock 每轮结果空间独立、共享预算，不用于跨时间等消息。节点级隐式版本反射与人工图版本记录已移除。

## 证据

聊天读取在固定本地事务水位下取得当前成员、前序10条、后到最多20条；超过后到上限保留首10/末10并标截断。后到消息不改变本批成员，也不计为已经交付。

Icarus 读取使用两次 session 快照夹住 task 状态和有界历史读取。active_task_ids 包含已接收但尚未开始的任务；accepted、running 和 unknown 分别表示，不挑第一个 ID 当目标。历史仅保留已落库的请求、完整 assistant 消息、任务与工具完成/开始摘要，单条文本4000字符、总20000字符；没有完整 token 流或未完成工具结果。

history 可能触发远端历史恢复，因此再次核实会话。workspace/session/task 身份不符是错误；状态查询 unavailable 不是任务完成。原请求缺失时可以按可靠task ID查本库已交付参数，不能按文本相似性匹配。

近期正常判断与成功交付用独立时间统计，排除错误/等待/纯自消息。强信号在首次收存保存，重投不因名字配置变化改写；连续不回应只计重置依据之后的结果。提交被接受不等于已发到QQ。

## 节奏与等待

普通群消息与私聊的短收集默认6秒，从最后一条人类消息的received_at计时；连续新消息重新计算安静期限。单批20条、持续收集60秒仍为关批上限。私聊分支先执行收集，不因强信号/6条压力立即关批，收集后保持原先不参与普通群聊退避的行为；群聊@、称呼、6条人类积压仍可提前判断。普通群连续两次no后从15秒指数退避到300秒；近期300秒内每次新submit增加15秒最短间隔。两项取较晚期限；普通消息不重置退避，自消息不计压力。

业务结果 silence/submit/steer/wait 与图成功/失败独立。需回应但工作排队、加载、未知或不适合追加时进入waiting。只有唯一明确running任务且无排队工作才可能steer；确认空闲时才submit。

waiting默认5秒检查一次，指纹包含任务状态、工作计数、历史进度与人类消息水位，不包含查询时刻。无变化只推进检查期限，不重新调用模型；新消息可提前检查。一个subject优先处理最早未结束批次，后继不能越过waiting/submitting/unknown。ready有界队列完成一个head后立即补入，避免自身消息前缀饿死人类消息。

## 持久化与发送

schema v3仍为三张表。观察增加首次强信号；批次固定 first/last ingest_seq，保存 judged_at、intervention、judged_through_seq、decision_message_seq、delivered_at、next_check_at、wait_fingerprint、resume_status、delivery_attempts 和逐轮trace。

judging重启回原ready/waiting；submitting回unknown；已提交不重放。等待重判不改变成员、不覆盖之前的安全图记录。旧试验schema启动被拒绝，不自动迁移/清库，旧迁移入口已移除。

发送前核实工作与人类水位；会话解析/创建完成后，最后得到的会话状态与本地人类水位还要再检查，变化则等待重判，不照旧决定发送。明确追加not_running/already_finished在同一事务记录rejected与waiting，进程中断后不能把已知拒绝误改unknown；连接故障/超时/不可信回执unknown，不重发。每次实际发送前持久化准确参数和操作身份，未知结果不能生成新尝试绕过幂等。

尚未尝试发送时的可恢复网关故障保留decided，并保存下次检查期限；补入排除未到期记录，避免即时重试饿死其他会话。

有前序聊天、后到消息或工作历史时，私聊/@也要经过上下文判断；近期交付统计窗结束不表示历史不存在。

本地锁只保证Argus内部每会话串行；其他客户端的查询后提交竞态仍存在，Agent输入队列负责串行执行，不承诺跨客户端原子独占。

## 诊断与验证

本机观察状态端点返回route、waiting原因/期限和分页安全runs；摘要按允许类型与固定原因码生成，不序列化任意上下文或模型文本。参数after_run/limit与next_after_run/has_more表示逐轮分页。

隔离测试覆盖图通用性、依赖和取消、等待恢复、任务结束但没有新消息、请求已解决、后到取消、模型期间到新消息、自消息不唤醒、正常追问可再次启动、发送拒绝与未知区别、安全诊断。真实判断质量仍需后续明确授权的QQ/模型测试，不用提交间隔或沉默人数充当质量标准。

验证命令仍为 `make test-argus`（含编译检查），每阶段记录实际通过结果；没有做真实外部冒烟。新增覆盖图边执行、行对象边界、固定事务快照、接收期间后台模型阻塞和通知失败恢复。

## QQ 交付提示词补全

`evidence/qq/qq_prompt.py` 组装当前User Prompt与回复位置提示；QQConversationReader在最近10条前序观察内排除已submitted批次消息，DAO只执行有界行查询。其余上文仅供理解（最多8000字符、显式标截断），不重新执行旧请求，不附带后到背景，也不复制整份历史prompt。当前消息与上文用JSON文本引用隔离。沿用Agent已有会话历史，不凭交付或完成记录声称QQ已回复。

回复位置说明要求面向QQ、普通聊天简短、发送成功后不复述或额外向TUI汇报；这是提示词约束，不是输出拦截或styling插件，实际模型遵从仍需真实验证。图片仍为占位，不在本次补全中传递媒体。
