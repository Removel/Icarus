# Argus QQ 观测闭环设计

> 状态：设计已确认，尚未实施。本文描述拟实现的流程，不代表当前源码已有这些能力；架构现状以代码和测试为准。

## 目标和本期边界

让 Argus 在不依赖 TUI 的情况下接收 SnowLuma 的 OneBot v11 QQ 消息，可靠收存，经队列消费、最小 Jev 策略判断，再按需通过现有 Gateway 操作 Icarus session。第一版重在每段流程可验证，不建立可迭代策略框架，不将 Windows demo 直接搬迁。

本期只修改 `apps/argus` 及必要的 Argus 配置说明；不改 Agent/Gateway 协议或内部、不实现 `packages/` 的 mem0/openkb SDK、不检索长期记忆或知识库、不由 Argus 直接向 QQ 发消息。不承诺跨 Argus/Gateway 的严格恰好执行一次：提交结果无法确认时宁可停在可查看的未知状态，也不盲目重复提交。

## 端到端职责与数据流

```text
main: 加载配置 → 初始化 Argus 独立数据库 → 配置可选 QQ connector
      → 启动 worker 与补入循环 → 分批从 DB 还原未完成观察并放入 queue → 就绪

SnowLuma HTTP POST → connector 验原始字节的签名、账号、大小
    → handler 校验并生成 QQObservation
    → DAO 提交 pending 记录（按来源 ID 去重）
    → 入有界内存 queue（失败仍由补入循环恢复）→ HTTP 204，无 JSON 体

worker 从 queue 取 QQObservation → service 按观察 ID 原子领取
    → DAO 读取同一 subject 的有界近期记录、会话关联
    → strategy 提供 Jev wake/silence 最小政策
    → service 组装 state、调用 Jev、解析并记录判断
    → silence：终结；wake：核对/创建 session，持久化提交意图
    → Gateway session.submit → 记录 task_id 或 submission_unknown
```

队列是正常路径，worker **始终从 queue 读取对象**；DAO 不是正常消费队列。数据库是持久化收件箱与恢复事实源：启动及入队失败时，扫描未完成记录还原子类并补入同一 queue。补入可以产生同 ID 的重复队列项；原子状态转换确保仅一项执行业务。单进程一个 worker，先不建设多进程队列或外部 MQ。`queue.task_done()` 必须在该项的处理/状态记录完成后调用；故障不能令 worker 悄悄退出而让健康检查继续报告 ready。

HTTP 204 仅表示消息已安全提交数据库，不承诺已交给 Icarus；重复合法上报返回同样的已收存语义，但不得重新入队或创建第二次业务处理。数据库事务失败则返回非 2xx。SnowLuma 当前对非 2xx 会记录失败，但没有被证实的自动重试，因此不能以其重试代替本地持久恢复。队列满时不得丢弃已提交的 pending；补入循环须在运行期继续尝试，不能只在进程重启时恢复。停机顺序先停止入站、等待/中止 worker 并记录可恢复状态，最后关闭数据库和 Gateway 客户端。

## QQ connector、handler 与业务实体

命名约定：`connector/QQConnector.py` 定义 `QQConnector`，`handler/QQHandler.py` 定义 `QQHandler`，`entity/QQObservation.py` 定义 `QQObservation`。后续平台特定实现也采用对应平台名加职责名；通用模块沿用现有 Python 命名习惯。测试文件镜像实现文件。

QQ 是可选 connector：未启用时不挂载 `/onebot/events`，全栈 `icarus start` 保持可用；启用时要求单个机器人 `bot_id`、非空上报密钥和 Gateway/工作区配置，缺失则启动失败。非机密选项放 Argus `settings.json`，密钥在仓库根 `.env` 的专用环境变量中，用 `load_icarus_environment()` 加载，绝不写入 JSON 或日志。监听保持 loopback。正式 QQ 路由没有无鉴权的手工事件注入。

`connector/QQConnector.py` 中的 `QQConnector` 在读取 JSON 前限制请求字节数，按 SnowLuma 的 `X-Signature: sha1=<HMAC-SHA1>` 对**未经修改的请求体字节**作常量时间比较，并校验 `X-Self-ID` 与事件 `self_id` 和配置机器人 ID 一致。签名缺失/错误拒绝；JSON 无效、账号不符或消息不符合协议拒绝，不落库、不入队。客户端成功响应必须是无 JSON 体的 204，避免 SnowLuma 将 JSON 当作快捷操作执行。不复制或分发 SnowLuma 源码，只走 OneBot 网络协议。

`handler/QQHandler.py` 中的 `QQHandler` 仅解析群聊和私聊的 `message` / `message_sent`：要求有效且非零的 OneBot `message_id`（SnowLuma 的有符号 32 位哈希可能为负），QQ 身份则按正整数或正整数字符串校验；纯文本和消息段数组均可解析，带 CQ 码的字符串明确拒绝而不假装已解析。保留文本、图片引用等有序片段，不下载远程媒体，不把不认识的段误标成文本；真正提及机器人仅由 `at` 消息段判定，`@all` 或普通文字不算。自发消息通过 post_type 或 sender ID 识别；自发私聊使用 `target_id` 指向对端；群临时私聊仍是私聊。非聊天事件 `notice/request/meta_event` 暂不生成聊天观察、也不进入策略；入口明确拒绝这些尚未支持的类别。

业务实体位于 `entity/`，是不可变的数据对象，**不继承 SQLAlchemy ORM**：

- `Observation` 基类：内部 ID、`SourceRef(platform, account_id, source_message_id, occurred_at)`、`SubjectRef`（平台、位置类型、外部 ID）、`ActorRef`（外部 ID、展示名）、有序 `ContentPart`、Argus 接收时间。只表达观察事实，不含判断或 session 状态。
- `QQObservation(Observation)`：`message_type`、`group_id`、`is_self`、`mentions_bot` 等 QQ 语义。没有仅供包装的重复 `QQMessageFacts`。通用层接收 `Observation`，QQ 特定回复目标/格式留在 QQ 适配位置；通用 service 不通过 `isinstance(QQObservation)` 分叉平台协议。

未来来源可继承 `Observation`，仅将确实通用的字段放到父类。`message_id` 缺失/非法时拒绝而不是用内部 UUID 冒充来源 ID。前期 demo 是测试参考，不复用其中无 ID 的合成载荷。

## DAO 与映射

Argus 单独使用 `$ICARUS_DATA_DIR/argus.db`（不读取 `icarus.db` 内部表），SQLAlchemy 2.x async + aiosqlite。DAO 提供面向业务对象的 insert/get/recent/claim/transition/recover 方法，service 不直接拼 SQL；ORM row 与不可变 entity 分离。建库权限及连接设置参考本仓库 Agent 的 SessionStore 模式，但不 import Agent 的映射。初版只有两张业务表：

1. `observations`：通用检索列包括内部 ID、`platform`、`account_id`、`subject`（位置类型及外部 ID 的稳定编码）、`source_message_id`、`sender_id`、发生/收到时间；有序 `content` JSON、`subtype`、`detail_version`、`detail` JSON；处理状态、策略版本、裁决及故障兜底原因、目标 session、稳定 `submission_id`、`task_id`、提交结果与更新时间。唯一约束 `(platform, account_id, source_message_id)` 做入站幂等；主要索引 `(platform, account_id, subject, occurred_at)` 及待处理状态。事实字段插入后不变，处理字段由 DAO 状态转换更新。
2. `session_links`：`platform + account_id + subject + workspace_path/workspace_key + session_id` 的关联历史、建立及结束时间。Argus 的对应关系并非 Icarus session 状态事实源；同一 subject 可先后关联不同 session，仅一个当前候选。新增关联前检查现有候选，不能仅凭内存会话路由。

`subtype='qq_message'`、`detail_version=1` 时由 QQ 编解码器映射 JSON 中的特有事实并还原 `QQObservation`；未来新平台只加自己的子类/编解码器，不改旧 QQ 数据，不为未知平台在统一表铺大量空列。未知 subtype/version 须明确隔离或报错，不能悄悄降格为父类；JSON 是明确结构，不是 pickle。QQ 特化字段可通过 SQLite JSON 路径查询；本期优先按平台、账号、subject、时间查，不先为未知查询负载建表达式索引。策略结果放同一观察行；不另建 strategy_decisions 或 task_submissions 表，确需多次裁决/提交历史时再拆。

## Service、最小策略与 Jev

`strategy/` 只定义一个稳定的 Jev `wake/silence` Choice 问题、两种判定标准和简单策略版本；不建设规则 DSL、策略注册中心、热更新或自动演化。`service/` 负责基于当前观察、同一账号/subject 的有限近期观察以及已有 session 关联组成有界 state，调用 Jev，严格解析结果、更新 DAO，并根据意图落实现有 Gateway session。QQ 的回复目标提示由 QQ 专用适配代码生成，不写死在平台无关 service。Argus 不自行发送 QQ 回复；Icarus 如需回复仍由既有工具完成。

Jev 参考已验证 demo 的 TypeSafe SDK/OpenRouter `typesafe/jev-1.13` 接法，配置模型、端点、超时和密钥；密钥只从根 `.env` 读取，不依赖真实模型完成单元测试。调用失败、超时或无法解析：**首次失败后再重试两次，共最多三次调用**，每次有限超时与有界退避；三次仍失败则明确记 `silence` 且记录 `judge_fallback` 原因，绝不当作正常的 Jev silence，也不触发 Icarus。结构化结果只接受已声明的 wake/silence，不能凭自由文本猜测。不能凭“项目有 LLM”调用 Agent 的内部模型层。

## Session 操作、未知结果与恢复

service 根据 `session_links` 找候选，再经 Gateway `session.get(workspace_path, session_id)` 校验实际 session 与 `workspace_key`；`unloaded` 可恢复，不等于不存在。新建时使用持久化且稳定的目标 session ID，以便创建响应丢失后可查询并避免盲目创建第二个；未查实存在/归属前不提交。`session.list` 只能得到同一 workspace 的摘要，不能推断 QQ subject 对应关系。针对同一 subject 的处理串行化；不同 subject 不因全局锁阻塞。策略目前只判断是否发起对话；第一版由 service 优先复用经过核实的关联，没有有效候选则创建新 session。session ID 选择与 Gateway 实际操作不放在策略里。

**提交前** DAO 持久化目标 session、稳定 `submission_id` 与即将发送的状态。确定拿到 `session.submit` 返回的 `task_id` 则记录成功，可用 `task.get_status`、`runtime.update` 和分页 `session.get_history` 跟踪。发送后超时/连接断开、或者恢复时发现已进入发送阶段但没有 `task_id`：记录为 `submission_unknown`，不自动重发；可订阅更新和历史用于人工排查，但不能用文本或时间接近当作可靠接受凭证。当前 Gateway 的更新/历史不含 `submission_id`，提交去重只在当前会话运行期，因此本期不声称能自动证明“响应丢失但已接受”。明确业务拒绝可按具体错误标记失败；不得把所有 Gateway 错误算作未知。提供不泄露聊天内容的状态查询/日志，显示 observation ID、session ID、submission ID、状态和错误类型，方便人工处理；记录提交尝试数和未知次数。未来持久化 `submission_id → task_id` 的 Gateway 查询是独立跨应用任务，不偷改现有 Agent/Gateway。

重启恢复遵循阶段边界：未发送的 pending / 可安全重新判断的处理中记录，经 DAO 还原子类补回 queue；发送前已经持久化的提交意图如果无法确定是否越过网络边界，保守转换为 `submission_unknown` 而不自动提交。队列补入与原子领取共同阻止重复处理；重复来源 ID 不产生第二次唤醒。处理已落库的历史消息时使用有界时间/条数窗口，避免积压后突然唤醒很久以前的话题，超出窗口以明确的过期状态留档而不是无声丢弃。

## 测试与验收

测试镜像 connector、handler、entity、DAO、service、strategy、worker/config 的代码位置，用 pytest 原生 assert。使用临时 SQLite、合成 OneBot 消息、可控时钟、可替换的 Jev/Gateway 客户端；默认测试不启动 SnowLuma、不调用付费模型、不发送真实 QQ 消息。

重点验证原始字节验签、无签名/错误签名/错账号/超大请求、204 空体、缺 `message_id`、消息段顺序、自消息回环防护、来源去重、落库与入队竞态、队列满时运行期补入、重启恢复、未知版本拒绝、同 subject 串行处理、Jev 三次失败后的 silence 兜底、已有/卸载/不存在 session、提交成功/明确拒绝/响应丢失后的未知状态不重发。按仓库规范运行最小测试、Argus 全套、受影响应用测试、编译和 `git diff --check`。真实 SnowLuma 与 Jev 冒烟需另行确认连接条件、费用和目标 QQ；不把尚未运行的真实冒烟描述成已验证。

## 文档与明确不做

实现后根据当前代码与测试编写对应的 `arch.md`，同步 Argus README 与配置示例；`plan.md` 在本设计获批后单独编写。既有骨架文档保留历史用途，实施时尊重当前工作区的未提交内容和预存的三个无关变更；不创建分支、commit 或 push，除非用户再次明确要求。

本期不接新 IM 平台、不向 QQ 直接发消息、不设计复杂策略框架、不提供跨进程事务或严格 exactly-once、不做 mem0/openkb SDK、不修改 Agent/Gateway 来增加提交查询 RPC、不将 CQ 字符串或未知媒体无依据地转成文本。
