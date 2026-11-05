# Agent 语义输入与投递恢复设计

更新：2026-11-05。**本设计的核心实现已写入工作区，尚未提交或部署；不能把离线验证当成完整上线验收。** 本轮结果见 [执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-execution-2026-11-05.md)。 2026-11-03 的已实现能力与历史验证见 [arch.md](arch.md) 和 [Argus 执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/execution.md)。旧轮次的开发授权不代表本轮自动开始实施。

[实施计划](plan.md) · [Argus 设计](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-design.md) · [Gateway 设计](../../../../gateway/docs/spec/2026-11-03-response-oriented-channels/plan-design.md) · [简明飞书审阅稿](https://tcnyvm7vds2s.feishu.cn/docx/Un1fdra63o5EscxU3q4cnuapn5e)

## 下一阶段：恢复窗口正文的插件交付

用户确认先commit当前实现，再补消息窗口与插件交付；具体范围见[plan-observation-delivery.md](plan-observation-delivery.md)。Bridge只启动任务/保存绑定，ArgusPlugin以动态贡献交付完整当前窗口，最终仍在User Prompt的plugin_context中。不是把解释句降为可选附件，也不按“六条不到一秒”定义窗口。运行中领取补充是第二阶段，当前阶段不扩已冻结工作集合。

## 1. 先把对象说清楚

- 一个 Icarus 实例就是一个独立对象。不同实例各自观察、记忆、处理和行动，不默认共用内部状态。
- Source 是持续可观测、可交互的外部区域；QQ 账号是接入与行动身份，不是 Icarus 自身，也不等于来源区域。
- 两个独立实例进入同一个 QQ 群，各自保存自己的记录和进展，不合并成一个对象。
- Channel 是正在处理的一件事，Session 是执行上下文；同一来源可以有多个事项，一个事项可以跨来源、跨 Session。
- 不再把“两个机器人同群要不要合并”列为待决项。本次不设计一个实例多 QQ 账号的假想需求。

实例隔离要核对实际数据目录、数据库与外部记忆的范围，不能只靠 source key 中有账号来证明。现有 Memory 配置包含 user_id/agent_id，调用还使用 workspace_key；现有配置字段不等于已验证跨实例隔离。若部署复用外部存储，必须验证互不可读写；缺少隔离能力时明确阻塞，不静默共享。

## 2. 当前问题与保留的能力

调查代码基线为 `e2ca65c`。当前 Bridge 将固定 GUIDANCE、完整规则、SDK 和内部 DTO 一起放入每轮 User Prompt；Memory 自动召回直接使用该 prompt。当前 Argus 工具使用轮次内数字引用，仍暴露不少后台身份。

Session 选择已持久化且可一对多，正常工具能力已经保留，这些不回退。但选择成功后 `runtime.submit()` 仍可能失败。隔离复现证明会留下 Session 和失败 wake；当前一律保守处理可能使 Channel 卡住。没有证据证明这个故障发生在指定真实测试窗口内。

特别注意：UserInputPlugin 是先入队、再发布接受事件、再返回；SessionAPI 又在返回后写 user.message。所以抛异常、没有接受日志，都不能单独证明未执行。

## 3. 每轮只给 Agent 必要语义

直接给当前真实消息、必要的发送者/区域/时间、简单引用，以及理解消息确实需要的上下文。目标和进展只补必要部分；媒体、@、引用关系按实际内容出现。不把所有业务字段每轮完整重贴。

后台自己保存 UUID、版本、游标、绑定、SDK、规则路径和回执。平台工具需要的真实发送目标仍须可用；隐藏内部 observation ID 不等于隐藏回复所需的平台 message_id。

### 回复契约（2026-11-05 用户确认的精确引导）

Agent 是在来源区域交流的 Icarus 主体，平台账号是它的接入/行动身份，不是让它向某个内部操作者转述消息。由语义、角色和当前事项决定是否回应，不能机械回复所有消息。

每条当前消息增加动态 `reply_target`，关联其来源引用、平台、会话类型和实际目标。QQ 私聊明确 user_id、群聊明确 group_id，不能把群发送者当私聊目标；飞书保留 chat_id/thread_id。回复所针对的 platform_message_id 是当前消息，不是它引用的上一条消息；身份不明时不猜目标。

稳定引导明确：普通聊天不一定需要工具生成答案，但向外部平台投递答案必须调用对应平台能力。需要回复时先经 MCP/CLI 实际发送、检查业务结果，再 argus_commit；最终 assistant 文本只属于内部输出，不会自动发送。没有行动用 no_action；纯内部处理可以 handled，不强制每条 handled 都有发送。

这是提示词/用法与目标表达的修正，不新增“所有处理都必须发 QQ”的硬校验，不宣称程序已自动认证所有平台回执。实际模型遵循程度需另获授权的真实交互验证。系统原则通过静态贡献加载，已加载的 Session 不会因源码改变自动换用新原则。

拟增 `plugins/argus/presentation.py`，纯函数负责消息投影和主题提取；不依赖 Argus 应用内部模型。`render_round` 产生实际模型输入，原始 WakeRequest 留在绑定与日志。

引用由 `plugins/argus/references.py` 管理，拟用消息 `mN`、来源 `sN`。会话内不重新绑定，先持久化再展示；重启从完整映射恢复，不按当前消息顺序重新编号。旧数字引用只在原 binding 内解释；映射缺失/冲突时拒绝相关读和提交。

### 工具分工

- `argus_channel_read`：查询当前事项目标、进展、来源；规则源码按需获取，版本由代码维护。
- `argus_history`：查询相关历史，只作参考，不成为本轮待确认消息；分页位置通过引用解析。
- `argus_read`：单条消息读取。当前内容已完整可见，不强制再读；若 history 已覆盖需求，新表面可不保留独立入口。实施前先验证具体调用者，旧绑定兼容不能直接删除。
- `argus_commit`：提交本轮处理结果和业务变化，内部身份由代码填充。来源用已知且获授权的短引用，不要求模型拼 source key。

最终模型视图必须包含本轮待处理消息，才允许确认；生成过投影不等于实际可见。历史不能冒充本轮工作。工具返回也采用必要业务结果，不再倾倒内部 DTO。

## 4. 工具不会用时再查说明

已确认采用：**短系统引导 + 现有工具发现 + 按需指南 + 当前语义上下文**。

ArgusPlugin 通过现有 `PromptContributor.static_prompt_sections()` 贡献短、稳定的 session 层原则。当前消息、来源、业务状态与配置不进入 System Prompt。不要把完整教程搬进系统提示词。

复用 `tool_search` / `tool_execute`，不另造搜索框架或 CLI。拟增只读 `argus_guide(topic)`，主题为 overview/rules/sources/commit。普通回复不读规则教程；修改规则才查输入输出、限制、示例及检查/激活区别。SDK 数据沿用 Argus `rule_guide()` 的元数据，避免重复维护常量。

已知且获授权的工具可直接执行；搜索、读指南都不授予权限。指南不触发模型、不修改规则、不决定路由。“已配置”不代表远端健康。

## 5. 记忆照常查，不查询后台教程

不是阻止其他插件对 Argus 的业务内容召回。真实消息仍应查到相关记忆，也不因此自动把所有消息写入长期记忆。

工程提案：通用输入增加可选 `semantic_text`，由 Bridge 提供干净的实际业务主题；Memory 用它查询，没有字段时仍用 prompt。不让 MemoryPlugin 解析 Argus JSON，也不让通用插件导入 Argus 实现。

字段贯通 SessionAPI → SessionHost/InputSubmission → UserInputPlugin/PendingInput → UserInputEvent。Memory 的查询、缓存指纹和结果 query 三处一致。Persona 若用语义主题保存对话片段，完整输入的安全检测不得缩小。

字段缺省/None 时保持旧 fingerprint 算法；有值时使用可识别的新指纹。旧 wake 的原 prompt/主题不重新生成，同身份换内容仍冲突。拟定非空上限 8192 字符，不额外调用摘要模型；超限明确拒绝或以有界主题提取处理，不能伪造完整内容。

## 6. 明确没接收才重试

保留当前 Session 选择；不要求创建 Session 与执行成为跨库原子事务，更不靠删除 Session 修复。

| 接收阶段 | 含义 | 可做什么 |
|---|---|---|
| prepared | 已持久化准备，还没进入提交端口 | 沿原投递继续 |
| not_admitted | 有明确证据证明该次尝试未交给输入提交端口 | 可恢复错误在原投递内重试 |
| accepted | 已拿到接收结果 | 不再提交，继续查执行/业务回执 |
| submitting / unknown | 可能入队，缺少确定结果 | 保守对账，不自动重放 |

拟增 `SubmissionNotAdmitted(code, retryable=...)`，仅在 SessionAPI 调用输入提交端口之前的确定失败使用。跨入 `entry.runtime.submit()` 后的异常不能包装成未接收。取消不能靠缺日志判安全；应继续传播，并按是否跨边界保守记录。

`argus.submission` 日志记录 attempt、phase、retryable、error_code。先持久化 submitting，再调用 runtime.submit；拿到 InputAccepted 再记录 accepted。接收后日志失败、崩溃留下 submitting、证据矛盾均为 unknown。旧 `argus.failed` 无阶段证明也不回填成安全失败。

拟增 `services/argus_delivery.py` 归约日志。not_admitted 只证明对应 attempt，旧响应不能覆盖新尝试；任意 accepted 或未排除的 submitting/unknown 都不能被别次失败重新打开重试资格。

同 wake 安全重试保持 Session/binding/prompt/task 身份；不同 wake 仍受未解决执行门禁。已有外发成功但 commit 失联，只对账，不重发。`replay_safe` 不能取代“本次是否接收”的判断，绝不承诺跨平台 exactly-once。

## 7. 兼容、隔离与不做的事

- 新旧绑定分别解析，老会话历史不删除、不重新交给模型执行；旧工具参数按原契约兼容。
- 实例独立性由存储与配置范围验证；不为实例之间引入自动同步，不让两个实例因同群而去重掉彼此的独立观察。
- 保留普通会话工具策略、同步异步一致性、原子 commit 和回执对账。
- 不改 ReActAgent，不让通用 Runtime 识别 Argus/QQ，不用 Hook 记录外发或驱动路由。
- QQ 文本转义、跨主机文件路径必须在对应平台后端处理；没有后端代码与部署授权时明确外部依赖，不往通用 MCP 客户端塞 QQ 分支。
- 本稿是设计，`arch.md` 继续只描述已核实实现。部署、真实外发、付费模型、迁移、Git 操作另行授权。
