# Agent Core TODO

## 已确认方向

- Agent Kernel 是一次任务执行的主体，负责模型决策、工具调用、结果回填和运行中响应。
- Plugin Runtime、Blackboard、Memory、Knowledge 等位于 Kernel 外部，通过明确协议提供信息
  或能力，不直接改写 Kernel 内部状态。
- Hook 是底层持久化、观测和监督基础设施，不替代 EventBus，也不改变主流程行为。
- `read`、`write`、`insert`、`bash` 是默认通用基础 Tool；领域 Plugin 可以额外贡献 Tool。
- Tool 集合在不同 Agent Run 之间可以变化，但单次 Run 内保持稳定，不实现 Run 内热更新。

## 后续能力池

以下内容不代表近期实施优先级，按路线图进入对应阶段后再设计和拆分：

- 完善 Agent Core 的多模态输入能力。
- 改造 Blackboard 的上下文组织与动态上下文收集能力。
- 重构 SkillPlugin 的召回、使用与生成交互。
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
- [x] 取消轮次由 Skill 清理临时状态，Blackboard 提交最近的协议完整消息前缀；部分
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
- [ ] Tool 名称冲突和基础形式校验已在 READY 前处理；后续继续完善作用域、权限、安全策略、
  并发上限、取消与资源清理。

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

- [ ] 重新设计 SkillPlugin 与 Agent Kernel 的交互：由 Agent 在执行过程中像使用工具一样
  主动回忆相关 Skill 信息，并自行判断是否采用，而不是默认把检索结果或完整 Skill 列表
  注入上下文。这里先记录交互方向，不提前确定最终接口形态。
- [ ] 为 Agent 是否允许生成或维护 Skill 提供显式开关；关闭时只允许召回和使用，不能
  隐式创建、扩充或修改 Skill。
- [ ] 在进入该阶段后，对比并评估“RAG 自动匹配后注入”与“向 Agent 暴露 Skill 列表后
  自主选择”两类现有方案。重点验证多步骤任务中的召回充分性、相关性、上下文开销，
  以及 Skill 持续生成造成的列表膨胀问题。
- [ ] 基于实际评估结果再设计 Skill 的发现、召回、选择、生成、反馈和维护边界，并迁移
  当前实现；现阶段不把任何一种既有方案确认为最终架构。

## 系统性代码重构

- [ ] 整理当前 `invoke`、`ainvoke`、`stream`、`astream` 中重复的 ReAct 状态转换与 Tool
  回填逻辑，保持四种入口行为一致。
- [ ] 完善 Agent 执行的步骤、超时、预算、循环和取消等安全控制，同时保持 ReActAgent
  无状态且不反向依赖 Plugin Runtime 或具体业务 Plugin。
- [ ] 精简 `AgentRuntimeService` 的组装职责，分离具体 Plugin 构建、依赖装配、订阅拓扑与
  Runtime 生命周期管理。
- [ ] 拆分体积过大或职责混合的 Skill 模块；优先按真实职责拆分，不为目录整齐制造抽象。
- [ ] 统一公共接口、类型、状态、异常、日志和配置规范，删除重复模型与隐式约定。
- [ ] 补齐模块级功能测试、跨层集成测试、取消与并发竞态测试，以及必要的真实模型冒烟验证。
- [ ] 每项重构先核对当前代码和测试，再同步更新 `apps/agent/docs/arch/` 与
  `apps/agent/docs/plan/`，不让文档描述超前于实现事实。
