# Argus 来源语义与安全投递修正设计

更新：2026-11-05。**本设计的核心实现及用户追加命名步骤已写入工作区，未提交或部署；新增 PostgreSQL 事务仍待隔离环境验证。** 本轮结果见 [执行记录](plan-execution-2026-11-05.md)。 2026-11-03 已实现的回应 Channel、一对多 Session 与原子提交保留；当前架构见 [arch.md](arch.md)，历史验证见 [execution.md](execution.md)。

[本应用计划](plan.md) · [Agent 设计](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan-design.md) · [Gateway 设计](../../../../gateway/docs/spec/2026-11-03-response-oriented-channels/plan-design.md) · [飞书简明审阅稿](https://tcnyvm7vds2s.feishu.cn/docx/Un1fdra63o5EscxU3q4cnuapn5e)

## 下一阶段：按来源恢复消息窗口

当前代码已保存为检查点，新增窗口方案见[plan-message-windows.md](plan-message-windows.md)。恢复旧quiet5秒/max30秒的来源窗口思路，但取消私聊/@自动绕过聚合。窗口不是Channel：规则看完整窗口决定各消息归属，同Channel同窗口决定一次事务落库，再整体投递。所有当前原话都交给Agent，不能先按是否需要独立回应删句。当前阶段先实现窗口与插件贡献，后续运行中读取另列。

## 1. 独立对象与来源区域

**一个 Icarus 实例就是一个独立对象，不同实例之间相互独立。** 两个实例看同一个群，各自记录、判断和处理，不自动合并记忆、游标或任务状态。

| 概念 | 含义 |
|---|---|
| Icarus 实例 | 谁在观察和行动 |
| Source | 持续产出信息、可以观测和交互的外部区域 |
| 平台账号 | 该实例接入和行动时使用的身份 |
| Observation | 从区域中观测到的一条消息或修订 |
| Channel | 需要回应或处理的一件事 |
| Session | Agent 处理事情时使用的执行上下文 |

QQ 群/私聊、飞书话题/群组/私聊/会议、仓库 issue/PR 区是来源区域的例子，不代表当前每种适配器都已实现。Channel 可跨来源、跨 Session；一个来源里可有不同主题 Channel。

撤销“多机器人同群是否合并”的待决问题；不为单实例多账号的假设增加产品要求。source key 是机器标识，不能用它的字符串格式代替实例本体、区域描述和接入身份。

## 2. 本轮修什么，保留什么

基线 `e2ca65c` 已有正确的 QQ 持久区域 key、按来源游标、先已有 Channel 后发现新事项、冻结轮次、原子 commit，以及 Agent 直接使用平台 MCP。这些保留。

本轮修正：

1. 保留有用消息事实，不让 Agent 从 CQ 字符串或内部 ID 猜语义。
2. 公共规则逻辑不再按 QQ 式 source key 位置猜所有平台的区域类型。
3. 对投递“确定未接收 / 已接收 / 结果未知”分别处理。
4. 验证实例的数据隔离，不把同一外部区域误当成共享内部状态的理由。

Agent 的工具指南、模型视图、引用和召回主题由 Agent 应用拥有；Argus 不代发平台回复、不控制普通 Agent 工具权限。

## 3. 平台适配层提供准确事实

沿用 Observation 的 content/detail，不复制平台完整原始对象。拟增加普通来源事实类型 `contracts/source.py`，字段为 platform、kind、region_id、可选 parent_id/display_name；接入身份 account_id/tenant_id 单独表达。复用公开类型和有界字典，不新增 Source 注册中心、数据库表或父子 Plugin。

可信协议适配器构造 `detail.source`；保存可选 `detail.sent_at`（UTC ISO）、content.sender_name、reply/root/parent/thread 标识及有界媒体描述。缺失就缺失，不伪造；入库 observed_at 与平台 sent_at 分开。模型视图如何精简由 Agent 处理，Argus→Agent 字段要有真实序列化往返测试。

### QQ

- 保留昵称/群名片、发送时间、@ 和回复关系，媒体类型/稳定标识等必要事实。
- 现有 `qq:<account>:<group/private>:<peer>` key 首次修复保持兼容，不自动合并/重分区历史。旧编码中的账号只是历史命名空间，不是来源本体定义。
- 出站私聊仍用可靠 target_id 确定对端；`is_self` 不能证明是 Agent 回声，不承诺自动防循环。

### 飞书

- chat_type 表示群聊/私聊，message_type 表示内容种类，不能把 text 解释成区域类型。
- 保留真实存在的 thread/root/parent 关系；只有 root_id 不足以自动建立一个新的话题 Source。
- 先保持已有 chat 级 key；话题级身份切换须有真实接入要求、样本和历史游标迁移方案，不能静默改消息归属。会议/GitHub 接入仍是未实现能力，不在本轮补齐全套连接器。

### 历史记录与媒体

公共 `semantic_context()` 读取规范化事实，不再自行 split(':')。旧 QQ/飞书 key 只由各自协议 helper 有限解释；未知平台明确 unknown。旧记录缺少昵称/时间，不回填猜测。

纯图片/表情不是空消息。默认规则根据规范化媒体事实判断是否有内容；无法理解时允许语义判断或 defer，不编造图像含义。既有用户规则不自动重写。不下载签名媒体 URL、不存内联二进制、不声称保留元数据就具备视觉能力。

## 4. 选择会话不等于内容接收

当前 Bridge 先 ensure_session，wake 成功后才保存 session/task。Session 选择成功后，读快照、发请求或加载执行环境都可能失败；当前保守门禁可能留下无法自动继续的轮次。Agent 已做隔离故障复现，但不是指定真实测试时段的已证实事故。

新顺序：

1. 尽量先完成观测、规则、版本与载荷检查，减少无用空 Session。
2. 选择 Session 后立即持久化本轮选择；相同 session_key 仍由 Agent 幂等返回原 Session。
3. 发同一个 wake；按明确接收状态保存结果。
4. 确定未接收且错误可恢复时，沿用原 wake/binding/session/prompt 重试；已接收只查状态；未知先对账。

Agent 拥有实际 Session 与是否接收的证据。Argus Store 只保存当前 round 的投递元数据，不复制完整会话执行账本。

拟增 `set_round_session(channel_id, wake_id, session_id)`、`set_round_submission(channel_id, wake_id, admission_state, *, attempt, retryable=False, error_code=None)`。沿用 Channel 行锁及 runtime JSON；只在 accepted 时使用 set_round_delivery 表示真正接收，unknown 不伪装成功。

## 5. 恢复规则

- `prepared`：尚未进入执行提交端口，可继续原投递。
- `not_admitted`：有明确证据证明该 attempt 未接收；retryable=true 才允许自动尝试，总计最多 3 次（含首次）。失败之间由扫描周期退避，不 busy-loop；耗尽后留 pending 与原因。
- `accepted`：不能再次调用 submit；继续查执行与 commit。
- `submitting/unknown`：可能已经入队/执行，不能用没有 user.message、HTTP 超时或 500 当未执行证明。
- 未知/缺字段的旧状态保守停留，不直接开放重试。原 wake 幂等重发不是换一个业务身份重新启动。

attempt 与阶段只前进，旧失败响应不能覆盖新状态；任一次 accepted 或尚未排除的 unknown 都阻止用另一尝试的 not_admitted 重新开放投递。冲突证据归 unknown。

`replay_safe` 继续用于已停止业务执行的重放边界，不替代接收状态。不靠新 Session/new key 绕过未解决任务；平台发送成功但 commit 回执丢失，恢复原回执，不重发平台消息。失败 Channel 不阻塞其他 Channel 扫描。

## 6. 实例独立必须落到存储范围

现有 Argus 配置包含 database_url/data_dir 与外部 memory_user_id/agent_id/workspace_key。要验证每个实例实际范围独立；同一数据库服务可以物理共享，但不能因此共用未隔离的业务表或记忆范围。

首选使用现有配置实现独立数据目录、Argus 数据库和各后端支持的隔离范围；不凭空设计跨实例协同。不擅自新增或迁移生产数据库。现有部署若无法表达隔离，报告缺口后另定最小修正，不能以平台账号代替实例隔离。

## 7. 用户追加：新建前由 LLM 概括名称

2026-11-05 用户明确要求，在“判断需要新建”和“实际创建 Channel”之间加入 LLM 总结命名。只生成 title，不改 goal、路由或是否创建的判断。

复用 Agent 已配置的命名模型，默认 perception；只读单次请求，无工具、SDK retries=0、最多256输出tokens、10秒超时。返回严格 JSON title，1–60字符、无控制字符，提示通常4–20字，不复制整条请求或内部编号。

调用在 Channel 创建事务之外，也不占主发现锁；Observation.detail.channel_name 保存一次性 claim（15秒失效）与完成/失败结果。同修订重启不重复付费，已存标题直接复用；未知/超时/失败或缺模型配置回退原始首句标题，继续创建，不丢业务请求。事务创建仍复核候选版本、修订与已有接受，不由名称决定技术身份。

配置 `ARGUS_CHANNEL_NAMING_ENABLED` 默认true，`ARGUS_CHANNEL_NAMING_MODEL` 默认perception，可指定 `ARGUS_CHANNEL_NAMING_SETTINGS_PATH`。离线测试注入命名器或明确关闭，不实际调用付费模型。真实标题质量与新增延迟/费用在另获授权后验证。

## 8. 兼容和非目标

不删除消息、不自动重放旧 ignored/exhausted、不重建观察表、不自动改旧 source key。Agent 先具备新旧接收契约，Gateway 薄转发，Argus 再消费；切换前暂停新投递并对账，真实服务操作单独授权。

不在通用 Runtime/ReActAgent 或 MCP 客户端中添加 QQ 分支，不让 Hook 记录平台回执或驱动下游。文本转义、Windows/WSL 文件上传要改对应平台后端；未定位后端前明确外部依赖，不以指南替代协议修复。

本设计不声称 exactly-once、确定性回声识别或已完成跨实例隔离验证。`arch.md`/`execution.md` 保留现行事实与历史结果，实施完成后再据代码更新架构。
