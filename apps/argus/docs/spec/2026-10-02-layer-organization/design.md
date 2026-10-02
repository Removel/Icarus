# Argus 层级职责整理设计

> 状态：待审阅设计，尚未实施；不是当前源码架构。本文覆盖 C：按明确的层级整理现有代码，并将证据绑定到边、判断绑定到节点。前置设计为 [A 图基座](../2026-10-02-decision-graph-substrate/design.md) 与 [B DAO 边界](../2026-10-02-dao-boundary/design.md)。设计确认后再写实施计划。

## 1. 目标与约束

用户主要目标是整理当前代码结构，让文件名、目录职责、依赖和调用链一致，不继续把业务判断、证据、类对象及持久化混放。

本轮包含已经讨论的完整范围，但不新增业务功能：

- A 整理轻量图基座并支持边动作。
- B 整理表记录类型与 DAO 边界。
- C 整理接入链、策略/证据、client 简单操作及 service 编排。

保留现有 QQ 判定、上下文截断、收集/退避/等待、幂等、状态查询和投递安全语义。修改内部接口与图形状，不改变外部 HTTP/RPC 合约、schema v3 或真实用户数据。未经另行要求不提交、不推送、不启动真实服务。

## 2. 层级契约

| 层 | 做什么 | 不做什么 |
|---|---|---|
| clients | 封装对外 API、回执校验、协议错误及简单组合操作 | 不管理 Argus 批次，不写本地 DAO，不决定是否回应 |
| config | 读取、校验和应用配置 | 不编排消息或图 |
| connector | 管理外部应用连接/监听、验签及传输边界，交原始内容给 handler | 不直接调用 service、DAO、策略或队列 |
| handler | 原始内容转业务对象，直接调用 service 处理 | 不自行裁决、不直接持久化，不生成交付 prompt |
| service | 处理业务对象、持久化编排、安排计时/恢复及后台批次执行、组装并运行图、执行判断结果 | 不内联证据组装或业务判断，不把业务写入 graph/DAO |
| dao | 对表记录执行增删改查、事务与条件更新 | 不定义业务/表记录对象，不生成证据/业务决定 |
| entity | 业务对象、表记录、策略输入输出及对象映射 | 不执行数据库、网络或流程编排 |
| evidence | 收集、补充、整理事实及其表述（包括 User Prompt） | 不决定是否回应/提交/追加，不执行业务投递 |
| strategy | 基于输入判断、输出决定；判断可以使用外部 API；表述业务判断轨迹 | 不写 DAO、不拥有后台循环，不自行调证据组件 |
| decision_graph | 图/节点/边声明、编译、状态与轻量执行 | 不 import 业务层，不知道 QQ/DAO/client；不自行寻找业务组件 |
| utils | 实际复用的辅助函数/类 | 不放业务编排、策略或持久化实现 |
| main | 配置、组件装配、启动停止及本机运维路由 | 不承担业务消费循环或业务判定 |

`utils` 不要求每个函数跨平台通用。是否归 utils 看是否是辅助算法；识别强信号的业务规则归 evidence，是否允许发送的裁决归 strategy。不能仅按函数长度决定层级。

## 3. 依赖与调用方向

### 3.1 接入调用链

本次取消入站队列，接入直接调用：

```text
connector 获取原始内容
  → handler 转成业务对象
  → service 接收业务对象、持久化、登记待处理工作
  → 持久化完成后返回接收结果
```

handler 直接等待 service 的接收入口，不等待后续图判断和投递。删除 InboundQueue 及仅用于转手的 QueueWorker 接入环节；不实现队列/直调双模式开关。

直接接收不等于每条消息立即执行完整图。收集窗口、退避、等待重查和恢复仍由 service 内部的 BatchScheduler 及现有后台批次执行机制安排。

connector 的外部客户端用于接收/核验原始数据，service 的 client 用于业务所需 API 操作。调用顺序不等于禁止任何共享类型依赖，也不能靠“上游/下游”标签判断所有 import 是否反向。

### 3.2 图使用链

main 创建并注入实际 evidence、strategy、clients、DAO。service 将实际 callable 绑定到图并调用 GraphCompiler/GraphRunner：

```text
service 使用图基座
  判断节点 a1 → 选中边 b1，执行注入的证据收集 → 判断节点 a2 → …
```

底座只执行注入接口，不 import/实例化 evidence 或 strategy。evidence 与 strategy 彼此不 import；通过 entity 的输入输出和图单元产物连接。两者都可以通过注入 clients 从外部获取所需内容，不能把“纯判断”误解成“策略禁止模型请求”。

### 3.3 允许的项目内依赖

| 模块 | 允许直接依赖 |
|---|---|
| entity | 同层、无业务含义的 utils；records 模块使用 SQLAlchemy |
| utils | 标准库或所需第三方工具，不依赖业务层 |
| clients | entity 的 API 结果类型、utils、所需 SDK；不依赖 dao/service |
| config | 配置库、必要 utils、既有 runtime_environment |
| decision_graph | 标准库和包内模块 |
| dao | entity 的表记录类型、utils、SQLAlchemy；不依赖业务实体/判断 |
| evidence | entity、dao、clients、utils |
| strategy | entity、clients、utils、需要的 graph 结果类型 |
| service | entity、dao、clients、evidence、strategy、decision_graph；同层 scheduler/批次执行组件 |
| handler | entity、注入的 service 接收接口 |
| connector | handler、clients（连接操作）、config、utils |
| main | 装配需要的各层 |

禁止：service → handler/connector；dao → evidence/strategy/service；entity → dao；decision_graph → 任意业务层；evidence ↔ strategy；connector → service/dao/queue。

不为上述规则新建全套 Protocol 或插件注册系统。已有具体组件由 main 实例化并注入；只在真实调用边界使用现有 callable/类型。

## 4. 直接接入与持久化

### 4.1 取消入站队列，保留后台批次处理

删除 `src/queue.py` 的 InboundQueue 与 `src/worker.py` 中仅转交观察给 scheduler.offer 的 QueueWorker 接入用途，更新装配、健康检查及对应测试。接收链不再入队/出队，不引入队列项回执、Future 或消息信封。

- handler 解析后直接调用注入的 service 接收入口。
- service 调 evidence 取得首次强信号等本地事实，将业务对象转为表记录并调用 DAO 持久化。
- 持久化后的登记/唤醒只做轻量本地操作；不能在接收入口原样等待现有 scheduler.offer，因为它可能触发远端工作证据读取。
- 成功收存后返回接收结果。批次形成、到期/等待检查、图判断与投递留后台，不阻塞后续消息接收。

现有 `scheduler.ready` 保存可执行批次 ID，与已删除的观察入站队列不是同一用途。本轮保留它及后台消费者，不重新设计 put/get 形式，也不为它新增独立的架构层。

BatchScheduler 是 service 内部的计时/恢复组件：取得 strategy 的时机判断后安排工作；它不自行裁决回应规则。main 负责装配和任务生命周期；消费与定时检查实现留 service 内部，健康检查监督现有任务，不再要求不存在的入站 worker 存活。

### 4.2 204 仍代表持久化完成

handler 等 service 接收入口完成数据库事务后返回，再由 connector 转成 HTTP 回执；直接 await 本地收存，不需要队列回执关联，不等待图或模型。

- 新事实成功落库或重复事实一致：204，无 JSON 正文。
- 相同来源但事实冲突：仍映射 409。
- 解析错误：422；已知非聊天事件在 handler 内识别并忽略，不调用 service。
- 服务停止接收时明确返回不可用响应；没有完成持久化就不能返回成功。
- 已经提交但客户端未收到回执时，重投依靠现有来源去重。
- 后台批次队列满或轻量唤醒失败不撤销已提交事实，不把它误报为未收存；数据库补入保持恢复路径。
- 持久化之后不在请求里启动无界的临时任务；后台处理沿用有界批次队列和受生命周期管理的消费者。

service 计算/记录首次强信号时调用 evidence，不由 connector 判断。重投不按新 bot_names 改写首次元数据。

## 5. 对象与文件归位

| 当前 | 目标 |
|---|---|
| `dao/entities.py` | `entity/records.py`（B） |
| `dao/qq_observation_codec.py` | `QQObservation.to_row/from_row`（B） |
| dao 内 SessionLink | `entity/session_link.py`（B） |
| strategy 内 QQBatchFacts、BatchWindowResult | entity 中对应业务结果模块，不继续散在实现文件 |
| `strategy/qq/qq_batch_facts_policy.py` 与 service._facts | `evidence/qq/qq_facts.py`；变为事实构造，不继承 BasePolicy |
| `service/qq_prompt.py` + handler.reply_hint | `evidence/qq/qq_prompt.py`；回复目标文本作为 prompt 辅助，不单独增加只有一个函数的模块 |
| `service/decision_trace.py` | `strategy/decision_trace.py`；保留业务类型与原因白名单，不移入通用图底座 |
| `utils/qq_signals.py` | `evidence/qq/qq_signals.py`；首次强信号/称呼事实识别 |
| `utils/qq_transcript.py` | `evidence/qq/qq_transcript.py`；聊天事实表述 |
| action 文件中的共享工作态判断 | `strategy/work_state.py`；动作/介入/发送复核共享，service 不散用谓词自己写规则 |
| batch_store.utc | `utils/datetime_utils.py`（B） |

`QQSelfMessagePolicy` 保留为真正的入口判断，防止 self-only 进入后续外部读取。事实 reader 可以复用已有 human_present，但不为了删一个小文件让自消息必须先查 Gateway。检查守卫重复时区分“候选批次前缀排空”和“图是否介入”两个时点，不改变任何回环保护。

不引入多级 service/evidence 子包去包装原有小文件。具体 QQ 证据与策略沿用 evidence/qq、strategy/qq，非 QQ 判断与 API 操作不冠以 QQ 名称。

## 6. 策略接口

按已选择的收紧契约：所有实际判断策略继承 `BasePolicy[InputT, OutputT]`，统一 `async evaluate(value) -> OutputT`。一个 value 是该判断真实需要的不可变业务输入，不是包含所有字段的通用 PolicyInput。

- batch 时机、code 介入、模型介入、任务关系、动作、发送前安全复核分别使用所需输入；复用已有实体，不重复建同义模型。
- 图 binder 只负责从单元产物取值、构造策略输入，再调用 evaluate(value)。
- evidence 不是策略，不继承 BasePolicy；可以是普通异步 callable/reader。
- Jev 的问题与 criteria 留在 strategy，JevClient 负责协议、响应校验、超时重试。
- scheduler 取得策略的 close/wait 结果后执行关批持久化，这是编排，留在 service；不能把 `close_batch` 的数据库写本身搬进 strategy。
- 是否空闲、是否允许追加、证据是否改变以及 graph 失败如何映射业务状态，放在 strategy。service 根据结果执行等待/持久化/发送，不重复这些 if 规则。

不增加策略 registry、自动发现、通用单例或基于厂商名称的上层分支。

## 7. QQ 图归位

A 阶段只机械适配编译/执行接口；C 才改变业务图的声明形状。所有判定分支与结果保持原义。

- 入口 self-message 判断基于当前批次，不做远端读取。
- 需要介入判断的路径上，边收集 conversation → work → activity → facts；这些采集依赖有明确顺序，以一个注入的收集 callable 顺序调用现有 reader 完成，返回一个业务证据包。不为每个 reader 增加假判断节点。
- code-first 判断仍优先；只有 undetermined 分支进入 Jev；后续 relation/action 决定 submit/steer/wait/silence。
- 控制汇合/出口是基座已有连线能力，不复制策略，也不把出口 fallback 放进 graph。
- 业务读取的原子水位、双 session 快照、历史界限和等待指纹保持不变。
- prompt 组装通过 evidence 调用；用于投递的 prompt 可以在发送前现有时点生成，不为形式对称增加一个只做 identity 的判断节点。

等待唤醒、批次计时、发送前核实处于跨时间 service 流程，不强制搬进单轮图。它们的事实读取仍调用 evidence、判断仍调用 strategy，定时和业务副作用留 service。

## 8. Clients 简单操作下沉

IcarusClient 封装：

- `ensure_session(workspace_path, session_id)` 的 session.get/create 和明确 session_exists/not_found 收敛；由调用方给定稳定 ID，不在 client 内生成 Argus 批次关联。
- submit/steer 回执结构校验、明确接受/拒绝/传输未知结果。API 结果类型放 entity，保留需要持久化的原始回执。

client 不能自行重发提交或追加，不能根据网络错误生成新的 operation_id，也不能接触 DAO。

service 保留会话关联、发送意图及尝试参数的落库、操作 ID 生成和原子事务边界。发送前“证据是否变了/目标是否仍可追加”由 evidence + strategy 完成，而不是让 client 作 Argus 业务判断。

## 9. 配置、测试与文档

- 参数/密钥加载仍由 config；main 传配置到需要的组件，不到处直接读 settings。
- 源码应用内导入统一相对路径；不把 import 风格整改扩散到别的应用。
- 删除旧路径转发和真正未使用的导入；不能把“无同名测试”等同于“没有测试”。
- 小层级测试镜像 src，保留 `test/application/` 作为跨层流程测试。新增对应移动的 evidence/strategy/entity 测试；handler/service 接收测试改为直接调用，删除只验证旧入站队列/转手 worker 的测试，保留其停机、恢复与错误传播的有效覆盖。
- 当前 arch.md 按实现完成后的真实代码更新。旧 design/plan 保留历史，不批量改写成当前事实；旧 QQ arch 指向新现状。

## 10. 分阶段验收

三个阶段均须可独立跑通应用测试，不能 A/B 合入后留一个不可运行的中间版本。

1. A：通用图结构、边执行/失败、joins、预算和取消；旧 QQ 图机械迁移。
2. B：记录类迁移、DAO 调用边界、证据组装上移，仍可挂在旧 QQ 节点位置。
3. C：connector/handler/service 直接接入、移除入站队列、保留后台批次处理、策略输入一致、证据挂边、client 简单操作及结构清理。

重点保持：

- 204 不早于持久化；停机、重复/冲突回执正确，后台批次队列满时已收存记录仍可恢复。
- self-only 不调用模型、不去读取不需要的远端事实。
- waiting 头批次顺序、无新消息也能唤醒、不变证据不重复问模型。
- 最后发送前工作/人类水位变化仍阻止发送。
- 明确追加拒绝不转成新 submit；未知投递不自动重发。
- 安全 trace 不输出任意证据、聊天全文或模型文本；增加边记录后分页接口字段和含义保持可解释。
- 按最小测试 → `make test-argus` → 编译/diff 检查，隔离配置与临时库；不启动真实服务，不修改本地 settings 或真实数据库。

本文件及 A/B 当前都是设计草稿。审阅确认后再按仓库规范写分阶段实施计划，源代码修改尚未开始。
