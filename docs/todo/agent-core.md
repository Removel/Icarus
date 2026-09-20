# Agent Core TODO

## 已确认方向

- Agent Kernel 是一次任务执行的主体，负责模型决策、工具调用、结果回填和运行中响应。
- Plugin Runtime、Blackboard、Memory、Knowledge 等位于 Kernel 外部，通过明确协议提供信息
  或能力，不直接改写 Kernel 内部状态。
- Hook 是底层持久化、观测和监督基础设施，不替代 EventBus，也不改变主流程行为。
- `read`、`write`、`insert`、`bash` 是默认通用基础 Tool；领域 Plugin 可以额外贡献 Tool。
- Tool 集合在不同 Agent Run 之间可以变化，但单次 Run 内保持稳定，不实现 Run 内热更新。

## 下一步开发顺序

Agent 基础能力首期已经完成，但真实长对话验证暴露了跨 Run 完整历史、运行中上下文、
Tool Result 和任务中断之间的协议缺口。当前应先完成下文“长对话、Tool 与中断二次收敛”，
再继续扩大设备级 Runtime、Session 和 UI 产品化范围：

1. Agent Kernel 增量整理与运行保护：
   - [x] 提取 `invoke`、`ainvoke`、`stream`、`astream` 的重复内部实现，保留四个公开入口
     及现有同步、异步、流式和非流式语义；
   - [x] 由 Harness 在每次模型 Step 前执行 `max_steps` 检查，默认允许 256 个 Step，准备进入
     第 257 个 Step 时确定性截停；
   - [x] 使用统一 Task Error Event 表达致命与非致命错误，只有致命错误改变 Task 终态。
2. 基础上下文与输入能力：
   - [x] Blackboard 已记录当前跨轮历史的粗略上下文 Token；
   - [x] 每轮开始时在旧历史达到模型上下文窗口 85% 后执行 Compact，成功后用一条摘要替换
     全部旧历史，失败时保留历史并终止本轮；
   - [ ] 改为基于实际发送给 Provider 的完整请求计算压力，覆盖 System Prompt、Tool Schema、
     完整 Session History、当前动态 Context、Active Run 消息、Tool Result、图片和输出预留；
   - [x] 将本地图片复制到现有 Session `assets/`，Context 只保存稳定相对引用，由 Provider
     Adapter 转换为厂商协议。
3. 产品化阶段：
   - [x] 将当前固定单 Session 的 `AgentRuntimeService` 演进为设备级 `AgentRuntime` 管理多个
     `SessionRuntime`；每个 Session 继续使用一套独立 PluginRuntimeHost、Plugin 实例和 EventBus；
   - [x] 同一 SessionIdentity 只允许一个活动 SessionRuntime，并发 resume 共用同一次恢复；
     Plugin/配置在 SessionRuntime 生命周期内冻结，下一次 SessionRuntime 启动时读取变化；
   - [x] 通过 `SessionStore` 完善对话索引、业务历史持久化和恢复；
   - [x] 提供对话列表与切换能力，并由 TUI 率先封装 Agent 基础接口；GUI 和 WebUI 后续接入。

已完成的基础能力顺序为：ReAct 去重 → 256 Step Harness → 统一错误 Event → Blackboard Compact →
本地图片引用；设备级 AgentRuntime、SessionRuntime、Gateway 边界和 Session 恢复能力也已落地。
当前优先基于真实长对话暴露的问题继续加固 Kernel，再扩展 UI 产品化。基础能力设计见
`apps/agent/docs/arch/agent-core-capability-completion-design.md`，实施结果见
`apps/agent/docs/plan/agent-core-capability-completion-development-plan.md`。

## 长对话、Tool 与中断二次收敛

### 问题结论与现场证据

当前“对话十几轮后开始失真”不能简单归因于达到模型硬 Context Window。只读检查本机已有
Session、Blackboard State 和 Trace 元数据后，已确认更直接的问题是：实际模型请求随工具执行
快速膨胀，同时跨 Run 历史会被取消轮次和动态 Context 破坏消息结构。

- 一条 13 轮真实 Session 中有 5 个取消 Task，持久化历史为 13 条 User、10 条 Assistant，
  已出现连续 4 条 User Message；
- 同一 Session 的部分 Task 执行了 14 至 16 次模型调用，单个 Task 最多产生 21 次 Tool Call；
- 已观察到约 53.7 KB 的单个 Tool Result；当前 `read`、`bash` 和 MCP Tool 没有统一的结果预算；
- 现存 Session 没有实际触发过 Compact；当前 1M Context 配置下，85% 阈值是 850k Token，
  而 Blackboard 只估算已提交历史，不计算当前 Task 的 Tool Result 和 Tool Schema；
- `task.usage.input_tokens` 当前是一个 Task 内多次模型调用的累计值，不能当成单次请求 Context；
- 自动 Memory 通过运行中 Context 追加在当前 User Request 之后，形成 `user -> user`，并让
  Memory 成为 Provider 看到的最后一条 User Message；
- 当前 ReAct 在没有 Tool Call 时直接结束，没有完整区分 `stop`、`length`、空响应、拒答和错误。

因此本阶段的目标不是直接复制其他 Agent 的完整 Turn Loop，而是补齐 Icarus 自己的请求投影、
执行记录、资源预算和运行控制不变量。

### 必须守住的不变量

- Blackboard 保存协议完整、可重放的 Agent Run Message：User、Assistant、Tool Call、预算后的
  Tool Result、已应用 Runtime Context 和用户 Steer 跨 Run 保留；长 Tool Result 正文位于 Session
  Tool Result 文件，隐藏 Reasoning 和内部控制不进入消息历史；
- Tool Call 是 `assistant` 发起的 Action，Tool Result 使用独立 `tool` 角色；Provider Adapter
  可以转换厂商协议，但不得改变内部语义；
- 每个 Assistant Tool Call 必须有且只有一个匹配 `tool_call_id` 的终态 Tool Result；并发执行
  可以乱序完成，但写回模型的结果保持原调用顺序；
- 当前 User Request 是本轮唯一且最后生效的用户指令。Memory、Knowledge、Skill 和其他
  Pre-run Context 必须在同一 User Prompt 内位于请求之前，不能追加成更晚的 User Message；
- Cancelled Run 提交最近协议完整的安全前缀，并以明确 Assistant 中断消息闭合；未闭合 Tool Group、
  未完成 Assistant 和未应用 Steer 不进入后续模型历史；
- 每次 Provider 调用前都基于实际 Wire Request 校验消息结构、Tool 配对和 Context Budget；
- 中断、超时、预算和审批是 Harness 控制操作，不转换为要求模型自行遵守的普通提示；
- 已开始的外部副作用不假定可回滚；无法确认结果时记录为 `unknown`，恢复后先对账，禁止盲目重试。

### P0：先恢复完整历史与运行中纠偏

- [x] 按 `apps/agent/docs/arch/agent-run-history-steering-design.md` 恢复 Blackboard 完整 Agent Run
  历史，正常完成时整体提交 `AgentResponse.task_messages`，不再只保留 User 与最终 Assistant。
- [x] 为新 Run 增量增加严格结构校验；为旧 Session 增加只作用于请求副本的兼容修复，处理孤立
  User、孤立 Tool Result、缺失 Tool Result、空 Assistant 和重复 Tool Call ID，不原地改写旧数据。
- [x] Cancelled Run 提交最近安全检查点并用 Assistant 中断消息闭合；Failed Run 只有明确携带安全
  检查点时才提交，禁止从混合消息中猜测历史。
- [x] 增加 Agent 层 Steer：属于当前 Task 和 Agent Run，不创建新 Task、不停止当前 Run，在完整
  Tool Group 后的安全边界注入；已应用 Steer 进入完整历史，Stop 丢弃未应用 Steer。
- [x] Gateway 增加 `session.steer`，TUI 运行中提交默认 Steer、空闲提交默认新 Task；图片复用
  Session assets 导入链路，被拒绝的完整输入保留到本地队列。
- [x] 将自动 Memory 等 Pre-run Context 合并进 Blackboard 生成的当前 User Prompt，顺序固定为
  `dynamic context -> current user request`，整轮初始输入只产生一条当前 User Message。
- [x] 区分 Plugin Runtime Context 与用户 Steer；同一安全点合并为一条 User Message，顺序固定为
  `runtime context -> user correction`，不得插入 Assistant Tool Call 与对应 Tool Result 之间。
- [x] 补充请求级结构断言和回归测试：Tool Call/Result 一一配对、当前请求锚点唯一、停止历史闭合、
  Steer 不丢失且不会破坏消息协议。

### P0：约束 Tool Result 与 Active Run 工作集

- [x] 为 Tool Result 建立统一的单结果大小预算；超限内容写入 Session Tool Result `.txt` 文件，
  模型只接收有边界的 head/tail Preview、截断说明和稳定路径。
- [x] 建立单个 Tool Batch 的总结果预算；超过预算时按大小公平分配并外置长结果，保证一个批次不能占满
  整个模型窗口。
- [ ] 建立 Active Run 工作集预算；旧 Tool Result 可以压缩为摘要或引用，但最近完整 Tool Group、
  当前用户请求和仍待处理的操作必须保留。
- [x] 让 `read`、`bash`、MCP 和未来 Plugin Tool 统一经过结果预算层；具体 Tool 不各自实现一套
  不一致的截断逻辑。
- [x] 为 Agent Tool 增加统一 `_execution` 超时/输出预算申请，为 Bash 增加进程组终止和 16 MiB
  采集上限，并让 Read、MCP、Skill、Knowledge 列表能力默认有界。
- [ ] 限制重复、无进展和异常大的 Tool Plan；在现有 Step 上限之外增加模型调用数、Tool Call 数、
  重复 Tool 指纹和无进展预算。

### P0：以实际请求管理 Context

- [ ] 增加统一 Request Assembler，在每次模型调用前组成 Request-local Message 副本并完成结构校验；
  不在发送阶段反向修改 Blackboard 完整 Session History。
- [ ] 分开记录单次模型请求 Usage、Task 累计 Usage 和 Session 历史估算，避免把累计计费量误当成
  当前 Context 大小。
- [ ] Context Pressure 覆盖 System Prompt、Tool Schema、完整 Session History、Dynamic Context、
  Active Run、Tool Result、图片和输出预留，并优先使用 Provider 返回的真实 Usage 锚点。
- [ ] 区分硬 Context Window 与工作质量预算；不要因为模型宣称支持 1M Context 就等到 85% 才处理，
  先通过真实长会话评测确定较小的 Quality Working-set 阈值。
- [ ] 将 Compact 下沉为 ReAct 基础能力可调用的 Context 管理机制，同时保留 Blackboard 对
  完整 Session History 的所有权；当前 Task 内裁剪与跨轮历史压缩分别处理。
- [ ] Compact 保留最近完整 Turn 和 Tool Group，摘要为空、缺 Usage 或 `finish_reason=length` 时不得
  替换原历史；失败必须保留原上下文并提供可重试状态。

### P1：收敛中断语义

- [ ] 将用户运行中操作明确区分为 Hard Stop、Redirect、Steer 和 New Task，不再用一个 Cancel
  行为承担全部语义。
- [ ] Hard Stop 原子进入 `CANCELLING`，阻止新 LLM Step 和新 Tool Batch，取消模型请求，释放
  Pending Approval，并等待工具按自身取消契约收束后再进入 `CANCELLED`。
- [ ] Redirect 只中断当前模型生成并在同一 Task 内应用用户纠正；Partial Assistant 只用于 UI，
  不进入可重放历史。工具执行期间的 Redirect 降级为安全边界上的 Steer，不强杀未知副作用。
- [ ] Steer 在当前完整 Tool Batch 结束后作为真正的 User Correction 生效；多个纠正保持到达顺序，
  不与 Tool Result 拼成同一条 Tool Message。
- [ ] New Task 先收束旧 Task，再为新输入创建独立 Task；两个 Task 不共享同一个可变消息列表。
- [ ] 为 UI/Gateway 定义显式控制类型，不让模型通过自然语言猜测用户是 Stop、Redirect 还是
  New Task；产品默认语义在接入前单独评审。
- [ ] 取消后为同一批次每个 Tool Call 写入 `completed`、`failed`、`cancelled_before_start` 或
  `unknown` 结果，保证协议闭合；完整 Session History 不提交孤立 User。

### P1：补齐 HITL 与副作用闭环

- [ ] 在 Tool Executor 前增加统一 Tool Policy，根据 Tool 和参数判断 `allow`、`deny` 或
  `require_approval`；Hook 继续只观测，不承担授权决策。
- [ ] Tool 至少声明 Read-only、Reversible Write、Destructive、Privileged 和 External Side Effect
  风险；无法证明安全时由策略层提升审批等级。
- [ ] Approval 是 Session/Task 作用域的 Control Event，不是 User Message；Approve、Deny、Timeout
  和 Stop 都必须释放等待，并生成对应 Tool Result。
- [ ] 为副作用操作增加 Effect Journal，记录 `planned -> approved -> started -> completed/failed/
  cancelled/unknown`；执行前先持久化意图，避免崩溃恢复后重复执行。
- [ ] Tool 声明 `cancel_safe`、`yieldable` 或 `non_interruptible`；对已进入同步线程且无法强制停止的
  Tool，不再把停止等待误报成副作用已经取消。
- [ ] 默认无人值守场景失败关闭；Approval 重连、超时、重复响应和跨 Session 隔离具有确定状态。

### P1：响应终止与恢复

- [ ] 根据 `finish_reason` 处理 `stop`、`length`、`tool_call`、`content_filter`、`error` 和空响应；
  `length` 或 Partial Response 不得作为完整 Final Assistant 提交。
- [ ] 未知 Tool、损坏参数和重复 Tool Call ID 通过与调用一一匹配的错误 Tool Result 让模型有限
  自修复；超过重试上限后以明确 Partial/Failed 终态退出。
- [ ] 为模型请求、Tool 执行、Approval 和 Context Compact 分别建立有界超时、取消清理和恢复策略。
- [ ] 将当前默认 256 Step 重新校准为保护上限，并使用独立的无进展/重复循环检测，避免长循环
  在形式上未超 Step 但已经失去任务目标。
- [ ] 用工具规划与普通自然对话的真实评测校准 Temperature；当前 Thinking 模型的 `0.9` 先作为
  需要验证的质量风险，不直接凭经验修改。

### P2：观测与验收

- [ ] 为每次模型请求记录结构化指标：Wire Token、各 Context 分区占比、消息角色序列、Tool Group
  数量、最大/总 Tool Result、历史压缩代次和 Usage 来源；不得把敏感正文写入指标。
- [ ] 建立 30 至 50 轮确定性 Replay，覆盖普通聊天、连续 Tool、Memory 注入、取消、Redirect、
  Approval、超大 Tool Result、进程恢复和多 Provider 转换。
- [ ] 每次回放都断言：当前用户目标仍可定位、角色结构合法、Tool 一一配对、取消轮次不污染历史、
  Wire Request 不超过预算、执行副作用可追溯。
- [ ] 在第 1、10、20、30、50 轮测量目标保持、事实保持、Tool 选择准确率、无关内容率、延迟和
  Token 成本；只有行为指标稳定后才扩大 Context 阈值。
- [ ] 将 Usage、Compact、Waiting Approval、Cancelling、Cancelled、Partial 和 Unknown Effect
  投影为产品层可理解状态，由 TUI、GUI 和 WebUI 复用，不在各客户端重新推导。

建议按以下顺序逐项设计和修复：完整 Run History 与 Agent Steer → Tool Result Budget →
Request Assembler 与 Wire Budget → Redirect → HITL 与 Effect Journal → 长会话 Replay。
每一项进入实现前单独写设计和开发计划，不把本节直接当成一次性大改的实现规格。

## 后续能力池

以下内容不代表近期实施优先级，按路线图进入对应阶段后再设计和拆分：

- 在本地图片完成后，根据真实需求继续扩展其他多模态输入。
- 改造 Blackboard 的上下文组织与动态上下文收集能力。
- 已完成 SkillPlugin 的主动发现、渐进读取、显式生产与演化重构；后续根据真实使用结果优化。
- 实现角色卡片风格化输出插件。
- 实现情感响应插件。
- 已完成 AgentPlugin 首期运行中补充信息与任务取消控制；后续安全能力按真实场景扩展。

## Agent Kernel 边界

- [x] 基于当前实现记录一次 Agent Run 从输入、上下文、模型 Step、ToolCall、ToolResult 到终态
  的完整状态与所有权。
- [x] 明确 Agent Kernel、Harness、Plugin Runtime、Blackboard 和 Hook 的职责与依赖方向，
  保持 Kernel 是任务执行主体。
- [x] 明确 Run 身份、生命周期、稳定处理边界、完成、失败、取消和未来其他终态的语义。
- [x] 明确 Session、Task、Agent Run 和 Step 的身份与包含关系，以及应用层对外暴露的权威
  控制目标。提交结果和相关 Event 必须让调用方能够获得正确的取消目标，但此处不提前假定
  `task_id` 与 `run_id` 必须相同。
- [x] 只为“陷入内核”完成必要的最小边界调整；系统性代码重构留到真实场景验证之后。

## “陷入内核”

- [x] 以“当前 Run 的异步业务信息补充”和“确定性终止当前任务”为首批真实调用方，设计
  通用的运行中响应机制，使当前 Agent Run 可以接收来自主执行流之外的异步变化。
- [x] 只抽取两个真实调用方已经证明共有的最小协议；机制保持可扩展，但不先建设没有具体
  调用方的操作类型、路由层或抽象层级。
- [x] 将“陷入内核”定义为可扩展的上层机制，不限定为补充信息和终止两种操作。每种操作
  单独声明来源、目标、响应时机、响应强度、处理责任和处理后的 Run 状态。
- [x] 业务语义相关操作由 Agent 判断如何使用，例如 Memory、Knowledge、外部观察或监督
  信息；这些补充信息只通过内部 Plugin Event 进入，不向 WebUI/TUI 暴露直接写入接口，
  外围 Plugin 也不直接篡改 Agent 的执行状态和决策结果。
- [x] 终止、超时、预算和安全限制等确定性控制由 Harness 在代码层执行，不能转换为一条
  由模型自行决定是否遵守的业务提示。
- [x] 明确并发、重复、迟到、目标 Run 已结束以及终态竞争时的处理原则。
- [x] 定义 Harness 相对于模型请求、模型 Step、Tool 启动和 Tool 执行的检查边界，区分协作式
  取消、停止等待和已确认停止；明确完成、取消、超时、预算和安全失败之间的终态优先级。
- [x] 为内核操作的请求、接受、处理、拒绝、失败和最终结果提供可测试、可观测的证据。
- [x] 首批通过“当前 Run 的异步业务信息补充”和“任务级终止”验证两种不同责任层级，
  但不将通用机制固化为这两个用例。

## 任务级取消

- [x] 在 `AgentRuntimeService` 提供按 `task_id` 取消当前任务的公开接口，不要求停止或
  重建整个 Runtime。
- [x] 让 `UserInputPlugin`、`AgentPlugin`、模型流和工具调用真实传播取消信号，并提供
  `InputFinishedEvent(status="cancelled")` 终态。
- [x] 取消轮次由 Blackboard 提交最近的协议完整消息前缀；部分
  Assistant 和不完整 Tool Batch 不进入 Session History，已发生的文件修改和外部副作用不回滚。
- [x] 为重复取消、已结束任务、错误 task ID、工具执行中取消和资源清理增加测试。

任务级取消是“陷入内核”的 Harness 控制场景之一，但仍需保留独立、确定性的取消契约。
它不是交给 Agent 判断的一条语义消息。

## Tool 与 Plugin

- [x] 保留 `read`、`write`、`insert`、`bash` 四个默认通用基础 Tool，并将其作为默认装配
  能力，而不是散落在 Kernel 主循环中的特例。
- [x] 设计领域 Plugin 通过 Manifest 和 `PluginRegistration` 向 Agent Kernel 贡献一个或多个
  Tool 的正式机制；Tool 是 Plugin 内部普通组件，不注册成 Runtime 子 Plugin。
- [x] Kernel 通过统一 Tool 契约使用默认 Tool 和 Plugin Tool，不依赖其具体来源。
- [x] 明确 Agent Run 开始时取得稳定 Tool 快照；第一阶段在 Runtime READY 后冻结 Plugin、
  Tool 和 Event 拓扑。这里的 Runtime 是当前单 Session Host；目标架构下变更只在下一次
  SessionRuntime 启动后生效。
- [x] 明确快照持有本次 Run 允许的 Tool 定义和执行对象；第一阶段 Runtime 运行中不支持
  Plugin 卸载或重启，名称冲突在 READY 前处理，资源由所属 Plugin 在退出阶段清理。
- [x] 当前不实现同一个 Run 内的 Tool 热加载、热卸载和替换；未来只有在出现明确场景后
  才重新评估。
- [x] 完成 Tool 名称冲突和基础形式校验、Runtime READY 前冻结、单次 Run Tool 快照与显式
  allowlist，并支持按 Tool 声明组织并发批次、保持结果顺序、传播异步取消和清理 Bash 子进程。
- [ ] 根据真实调用方继续补齐通用 Tool 权限与安全策略、全局并发/资源上限，以及无法强制终止的
  同步副作用 Tool 契约；不把各具体 Tool 已有的局部限制误当成统一沙箱。

## Runtime Host 与 Plugin Manifest

- [x] 完成 Manifest 驱动的 Runtime 生命周期架构设计，明确发现、解析、校验、启动、恢复、
  运行、收束、快照和停止阶段。
- [x] 明确 Runtime 只发现 Icarus 内置 Plugin 和配置显式目录，不扫描 Workspace，不在启动时
  自动安装 Python 依赖。
- [x] 明确 Factory 返回完整 `PluginRegistration`，Host 校验后原子注册 Plugin、Capability、
  Tool 和状态提供者。
- [x] 明确 Event 发布与消费由 Manifest 声明，Host 自动生成现有 EventBus 的来源订阅，
  EventBus 继续不解释领域 Event。
- [x] 明确 Tool 执行直接透传 `task_id`、`run_id`、`step` 和不可变 `task_messages`，不新增
  上下文包装类，也不通过 Hook 隐式读取身份。
- [x] 明确 Runtime 退出复用现有任务取消，Plugin 收束自身后台工作，按 Workspace 与 Session
  保存持久状态但不恢复运行栈。
- [x] 实现 Manifest 模型、发现器、Python 依赖检查、依赖图和启动诊断。
- [x] 实现 `PluginRegistration`、Capability 注册、Plugin Tool 收集和原子校验。
- [x] 实现 Manifest 驱动的 Event 自动订阅和未声明 Event 发布保护。
- [x] 实现 Tool 执行身份参数透传和单次 Run Tool 快照。
- [x] 实现 Plugin `quiesce`、状态快照/恢复、退出收束和启动失败回滚。
- [x] 将当前内置 Plugin 迁移到 Manifest 装配，并保持现有行为与测试兼容。

详细设计见 `apps/agent/docs/arch/plugin-runtime-manifest-lifecycle-design.md`。

## SkillPlugin 重构

- [x] 停止每轮自动 RAG 检索和 Blackboard Skill Context 注入，由 Agent 通过
  `skills_list`、`skill_search` 和通用 `read` 主动发现、选择并读取 Skill。
- [x] 搜索采用确定性的简单关键词包含匹配；不使用 Embedding、BM25、编辑距离、拼写纠错
  或自动分词。
- [x] 提供 `skill_produce`、`skill_evolve` 和 `skill_job_status`，生产与演化作为后台 Job
  执行，并通过运行中 Context Event 尝试通知仍活跃的主 Agent。
- [x] `allow_produce` 与 `allow_evolve` 独立、严格且默认关闭；关闭时只允许发现、读取和使用。
- [x] Producer/Evolver 获取 Blackboard 对话历史与当前 `task_messages`，使用独立受控工具 Agent
  在 Job Draft 中生成完整 Skill 目录；Repository 校验成品后事务式发布。
- [x] Produce 在预检和提交时检查全局与 Workspace 两个作用域；Evolve 对全局 Skill 只创建
  Workspace override，并使用完整目录快照 Hash 防止并发覆盖。
- [x] Workspace Skill 位于 `<current-workspace>/skills`；生成 Job 没有固定总超时，内部子 Run
  记录在 Session Trace 中但不进入 Blackboard。
- [x] 删除 Embedding、usage SQLite、会话累计注入、轮状态和隐式自动维护链路。
- [ ] 使用真实模型和逐渐增长的 Skill 集合评估搜索词选择、召回质量、Job 生成质量与交互体验。

## 系统性代码重构

- [x] 整理当前 `invoke`、`ainvoke`、`stream`、`astream` 中重复的 ReAct 状态转换与 Tool
  回填逻辑，保持四种入口行为一致。
- [x] 完成首期 Run Control：记录当前 Step，在稳定边界注入异步 Context，阻止取消后的新模型
  Step 或 Tool Batch，保留协议完整的历史检查点，并确定性处理完成、取消和迟到操作竞争。
- [x] 在现有 Run Control 上增加默认 256 个模型 Step 的确定性上限；暂不增加 Agent Run 总超时、
  Token/金额预算和启发式循环检测，保持 ReActAgent 无状态且不反向依赖 Plugin Runtime。
- [x] 使用统一 Task Error Event 表达任务内致命与非致命错误；EventBus 只路由，只有致命错误
  由任务所有者收束为 failed。
- [x] 精简 `AgentRuntimeService` 的组装职责：具体 Plugin 构建、Capability 依赖装配、Tool 注册、
  Event 订阅拓扑、状态恢复与 Runtime 生命周期已下沉到 Manifest Factory 和 Runtime Host。
- [x] SkillPlugin 已按 Catalog、Scanner、Tool、Job、Producer/Evolver、Generation Tool 和
  Repository 等真实职责拆分，并删除旧维护链路的重复模型与隐式状态。
- [x] 已在 Plugin Runtime、Run Control 和 SkillPlugin 范围内统一公开类型、状态与显式调用上下文，
  删除这些链路中的重复模型和隐式约定。
- [ ] 继续统一尚未覆盖模块的异常、日志和配置规范；只在具体改造中收敛，不做一次性全局抽象。
- [x] 已为 Plugin Runtime、Tool、Run Control 和 SkillPlugin 补齐模块级功能测试、跨层集成测试、
  取消与并发竞态测试。
- [ ] 补充真实模型冒烟和逐渐增长的 Skill 目录体验验证，并随新增能力继续补齐回归测试。
- [x] 已完成的 Runtime Manifest、运行中介入和 SkillPlugin 重构均同步更新了对应架构设计、
  实施计划与当前状态文档。

## 基础上下文与多模态输入

- [x] 模型配置提供 `context_window`；AgentResponse 保留 Run 总 Usage 和最后一次模型调用 Usage，
  不把缺失 Usage 误计为零。
- [x] Blackboard 在每轮开始时检查旧历史；上一轮上下文达到窗口 85% 时调用模型 Compact，
  成功后用一条摘要替换旧历史，失败时保留历史并结束当前 Task。
- [x] `ImagePart` 使用扁平的 `source`、`source_type` 和 `media_type`；本地图片导入 Session
  `assets/`，Blackboard 只保存相对引用，Provider Adapter 负责最终协议转换。
- [x] 为 Compact 的未触发、成功、失败、Usage 缺失和本地图片的导入、移动、缺失及双 Provider
  转换补齐确定性测试；真实模型冒烟因本轮未使用凭据而未执行。

## 产品化阶段能力

- [x] 新增设备级唯一的 `AgentRuntime` 和 Session Registry；将当前 `AgentRuntimeService` 的单
  Session 组装与控制能力复用为内部 `SessionRuntime`，迁移期保留兼容入口。详细设计见
  `apps/agent/docs/arch/device-agent-runtime-session-design.md`。
- [x] 将有状态 Plugin 的运行状态统一按 SessionIdentity 快照；Skill Job/通知不再由多个 Session
  覆盖同一 Workspace Plugin State。以 `state_version` 作为状态格式兼容契约；核心 Plugin 恢复失败
  阻止 Session Ready，非核心 Plugin 由 Host 禁用并级联依赖。每个 SessionRuntime 独立持有现有
  Persistence 资源，Logger Handler 只写入与自身完整 SessionIdentity 匹配的日志。补齐同 Session
  并发 resume、多 Session Persistence 日志、Trace 和状态写入的回归测试。
- [x] 提供 `unload_session(SessionIdentity)` 释放运行实例并保留持久化数据；运行中或排队中的
  Session 返回忙，不隐式取消。连续 6 小时没有状态变化且没有 Task、排队工作或 Plugin 后台工作
  时，由 AgentRuntime 加锁复检后自动调用同一 unload 流程；连接、订阅和只读查询不刷新空闲时间，
  新建或 resume 进入 Ready 时初始化计时，下一次提交自动 resume。第一阶段不设置活动
  SessionRuntime 上限，也不按数量或内存压力淘汰。
- [x] 在 Agent 应用层完成内部 Plugin Event 到公共 RuntimeUpdate 的投影与多 Session 聚合；
  Gateway 和 UI 不直接解释 `source_plugin_id + Event`。
- [x] 通过 `SessionStore` 持久化对话元数据和原始业务消息，支持枚举、选择和恢复；Blackboard 继续拥有当前
  Session 的有效模型历史，不把历史状态放入无状态 ReActAgent。
- [x] 提供对话切换的应用层契约，处理运行中任务、状态保存、订阅/UI 投影切换和目标对话恢复；
  不把多对话管理职责塞进 Agent Kernel。
- [ ] 在 TUI、GUI 和 WebUI 中展示 Token、Compact、图片与错误信息，并补齐产品端到端测试。
  TUI 已接入 macOS `Ctrl+V` 图片输入并显示 `[#imageN]` 引用；图片历史展示、其他平台输入以及
  Token、Compact 和错误信息展示仍待产品化。
