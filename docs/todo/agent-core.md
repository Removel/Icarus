# Agent Core TODO

## 已确认方向

- Agent Kernel 是一次任务执行的主体，负责模型决策、工具调用、结果回填和运行中响应。
- Plugin Runtime、Blackboard、Memory、Knowledge 等位于 Kernel 外部，通过明确协议提供信息
  或能力，不直接改写 Kernel 内部状态。
- Hook 是底层持久化、观测和监督基础设施，不替代 EventBus，也不改变主流程行为。
- `read`、`write`、`insert`、`bash` 是默认通用基础 Tool；领域 Plugin 可以额外贡献 Tool。
- Tool 集合在不同 Agent Run 之间可以变化，但单次 Run 内保持稳定，不实现 Run 内热更新。

## 下一步开发顺序

下一阶段先完成 Agent 基础能力，再进入 Session 和 UI 产品化：

1. Agent Kernel 增量整理与运行保护：
   - [x] 提取 `invoke`、`ainvoke`、`stream`、`astream` 的重复内部实现，保留四个公开入口
     及现有同步、异步、流式和非流式语义；
   - [x] 由 Harness 在每次模型 Step 前执行 `max_steps` 检查，默认允许 256 个 Step，准备进入
     第 257 个 Step 时确定性截停；
   - [x] 使用统一 Task Error Event 表达致命与非致命错误，只有致命错误改变 Task 终态。
2. 基础上下文与输入能力：
   - [x] 使用上一轮最后一次模型调用的 Usage 记录 Blackboard 当前上下文 Token；
   - [x] 每轮开始时在旧历史达到模型上下文窗口 85% 后执行 Compact，成功后用一条摘要替换
     全部旧历史，失败时保留历史并终止本轮；
   - [x] 将本地图片复制到现有 Session `assets/`，Context 只保存稳定相对引用，由 Provider
     Adapter 转换为厂商协议。
3. 产品化阶段：
   - [ ] 完善对话索引、业务历史持久化和恢复；
   - [ ] 提供对话列表与切换能力，并由 TUI、GUI 和 WebUI 封装 Agent 基础接口。

当前依赖顺序为：ReAct 去重 → 256 Step Harness → 统一错误 Event → Blackboard Compact →
本地图片引用。Session 持久化、恢复、切换和 UI 展示在上述能力稳定后再推进。详细设计见
`apps/agent/docs/arch/agent-core-capability-completion-design.md`，实施步骤见
`apps/agent/docs/plan/agent-core-capability-completion-development-plan.md`。

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
  Tool 和 Event 拓扑，变更只在下一次 Runtime 启动后生效。
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

- [ ] 持久化对话元数据和原始业务消息，支持枚举、选择和恢复；Blackboard 继续拥有当前
  Session 的有效模型历史，不把历史状态放入无状态 ReActAgent。
- [ ] 提供对话切换的应用层契约，处理运行中任务、状态保存、订阅/UI 投影切换和目标对话恢复；
  不把多对话管理职责塞进 Agent Kernel。
- [ ] 在 TUI、GUI 和 WebUI 中展示 Token、Compact、图片与错误信息，并补齐产品端到端测试。
