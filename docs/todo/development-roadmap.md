# Icarus 近期开发路线图

## 文档定位

本文记录 Icarus 未来一段时间已经对齐的开发主线、阶段顺序和关键依赖。它是路线图与
TODO 索引，不替代具体功能的架构设计和实施计划。进入每个阶段前，仍需在对应应用的
`docs/arch/` 与 `docs/plan/` 中完成设计、评审和任务拆分。

详细 TODO：

- Agent Core：`docs/todo/agent-core.md`；
- TUI：`docs/todo/tui.md`；
- 端到端产品体验与对象生命周期：`docs/todo/product-experience.md`。

## 四条开发主线

### TUI 稳定性与体验

持续修复当前 Textual TUI 的真实 Bug，完善异常恢复、长内容展示、滚动、输入和队列体验。
不依赖 Agent Core 的问题可以直接推进；任务级取消等跨层能力在 Kernel 契约完成后接入。

### 端到端产品体验与对象生命周期

从用户输入 `icarus` 命令开始，到最终内容展示完成或程序退出为止，系统性检查完整使用链路，
而不是只修复某个界面上的孤立现象。需要梳理命令进程、TUI 壳层、Runtime、Session、输入与
本地队列、Agent Task / Run、Model Step、Tool Call、Event 订阅与界面投影、输出段和清理过程等
对象的生命周期。

每类对象至少明确所有者、创建时机、状态转换、用户可见反馈、正常与异常终态、取消或重试
语义、资源清理以及跨层关联方式。该主线贯穿各阶段：当前先从启动、布局、滚动和退出体验
建立基线，后续再随 Kernel 与 Core 能力逐步补齐跨层生命周期。

### Agent Kernel 的“陷入内核”

Agent Kernel 是整个任务执行的主体，负责一次 Agent Run 内的多步模型决策、工具调用、
结果回填和运行中响应。“陷入内核”用于让当前 Run 响应来自主执行流之外的异步变化，
使 Memory、Knowledge、外部监督和其他能力可以与 Agent 主流程在时间上解耦，并让晚到的
信息仍有机会影响当前 Run。

“陷入内核”是通用机制，不预设只支持补充信息和终止两种操作。每一种内核操作分别定义：

- 来源与作用目标；
- 响应时机和响应强度；
- 由 Agent、Harness 或其他明确责任方处理；
- 处理后的 Run 状态和可观测结果。

业务语义相关的信息由 Agent 判断如何使用；终止、超时、预算和安全限制等确定性控制由
Harness 在代码层执行，不能交给模型自行决定。

### Agent Core 系统性重构

重构不仅整理目录，还包括完善缺失功能、修正职责边界、规范模块和接口，以及改善不合适、
重复或臃肿的代码实现。Kernel 继续作为任务执行中心；Plugin Runtime、Blackboard、Memory、
Knowledge 等位于 Kernel 外部并提供协作能力；Hook 保持底层观测基础设施。

## 已确认的关键约束

- Agent Kernel 是任务执行主体，外围 Plugin 不直接篡改 Kernel 内部状态或决策结果。
- “陷入内核”接收的是来自当前 Agent 主执行流之外的异步变化，不要求来源位于进程之外。
- 不同内核操作可以具有不同的权威级别、响应时机和后续行为，不提前固化为少数枚举。
- Hook 用于持久化、观测和监督，不替代 EventBus，也不改变主流程行为。
- `read`、`write`、`insert`、`bash` 继续作为默认提供给 Agent 的四个通用基础 Tool。
- 领域 Plugin 可以额外贡献 Tool；Kernel 使用统一 Tool 能力，不依赖 Tool 的来源。
- Tool 集合可以在不同 Agent Run 之间变化，但单次 Run 内使用稳定快照；当前不实现 Run
  执行过程中的 Tool 热加载、热卸载或替换。
- SkillPlugin 已完成工具化重构：Agent 通过五个 Skill Tool 主动发现、搜索、生产、演化和
  查询 Job，并用通用 `read` 渐进读取正文；不再自动 RAG 注入或隐式维护。

## 阶段路线

### 阶段一：建立基线，TUI 持续止血

- 收集、复现并分类 TUI 的 Bug、体验问题和新增功能；
- 补齐 TUI 关键异常路径、回放和视觉回归基线；
- 分开测量命令启动、首帧可见、Runtime Ready、首个 Task 开始和最终输出完成等关键时间点；
- 建立当前端到端对象生命周期清单，记录所有者、用户可见状态、终态和清理缺口；
- 梳理 Agent Core 当前行为、依赖关系、热点模块和必须保留的兼容行为；
- 不依赖 Kernel 新能力的 TUI 修复持续独立交付。

完成标志：近期 TUI 问题具有可复现记录和回归保护；Agent Core 重构对象具有事实清单，
不再以模糊的“代码整理”描述。

### 阶段二：整理最小 Agent Kernel 边界（已完成）

- 明确 Agent Kernel、Harness、Plugin Runtime、Blackboard 和 Hook 的职责边界；
- 明确一次 Agent Run 的身份、生命周期、稳定状态和终态；
- 定义默认基础 Tool、Plugin 贡献 Tool 和 Run 内稳定快照的契约；本阶段可使用测试替身验证
  边界，不要求提前完成 Plugin 生命周期与生产注册机制；
- 只完成“陷入内核”需要的最小边界调整，不在本阶段提前完成全部 Core 重构。

完成标志：可以清楚说明谁拥有任务执行状态、谁有权控制运行、谁只提供信息或能力，以及
外围能力如何在不侵入 Kernel 内部实现的前提下参与当前 Run。

### 阶段三：从真实调用方开发通用“陷入内核”能力（首期已完成）

- 以“当前 Run 的异步业务信息补充”和“确定性终止当前任务”为首批真实调用方；
- 从两个调用方中抽取已经被证明共有的最小内核操作协议，不先建设脱离场景的操作框架；
- 机制在概念上保持来源无关，后续来源和操作通过真实需求继续扩展；
- 支持 Agent 负责的业务语义操作与 Harness 负责的确定性控制操作；
- 明确不同响应时机、并发到达、重复请求、迟到请求和终态竞争的处理原则；
- 为请求、接受、处理、拒绝、失败和最终结果提供可观测证据；
- 不把首批用例直接固化为通用机制的全部能力。

完成标志：在预先定义的响应边界前到达并被接受的业务信息，能够影响当前 Run 后续至少
一次 Agent 决策；被接受的终止操作能够阻止后续模型 Step 或 Tool 启动；迟到操作具有明确
结果；每个被接受的操作只产生一个可观测终态。

### 阶段四：通过真实场景验证并接入上层（首批场景已完成）

- [x] 以 Skill Job 终态通知验证 Plugin 可以与主流程并行工作，晚到信息可在当前 Run 的稳定
  边界生效。
- [x] 验证用户终止由 Harness 确定性执行，并接入 TUI 的任务级取消。
- [x] 验证业务信息是否采用仍由 Agent 判断，不由 Plugin 直接改写执行状态。
- [x] 验证干预不会被错误展示成用户输入，也不会污染不完整的会话历史。
- [x] 验证操作不重复应用，迟到操作具有明确结果，且不会从已结束 Run 泄漏到新 Run。
- [ ] 后续使用 Memory、Knowledge 或外部监督接入第二个业务语义场景，验证机制不依赖
  SkillPlugin；该项不纳入当前 Agent 基础能力阶段。
- [ ] 记录从事件产生到 Kernel 响应的时延和真实使用效果。

完成标志：至少一个业务语义场景和一个 Harness 控制场景端到端成立，能够用真实结果反证
或确认 Kernel 边界。

### 阶段五：推进 Agent Core 系统性重构（进行中）

- [x] 完成 Tool 由默认基础能力和 Plugin 贡献能力共同装配的生产机制，包括 Plugin 生命周期、
  注册集成、READY 前冻结和单次 Run 快照。
- [x] 完成 SkillPlugin 的召回、使用、生产和演化交互重构。
- [x] 将具体 Plugin 构建、依赖装配、Event 订阅和生命周期从 `AgentRuntimeService` 下沉到
  Manifest Factory 与 Runtime Host。
- [x] 完成首期 Tool 形式校验、并发分批、顺序回填、取消传播和 Bash 子进程清理。
- [x] 整理 ReAct 执行流程中四种入口仍然重复的状态转换与 Tool 回填实现。
- [x] 由 Harness 增加默认 256 个模型 Step 的硬上限；暂不增加 Agent Run 总超时、Token/金额
  预算和启发式循环检测。
- [x] 使用统一 Task Error Event 表达致命与非致命错误，供产品层和其他功能订阅观察；只有
  致命错误改变 Task 终态。
- [x] 在 Blackboard 增加基于上一轮模型 Usage 和固定 85% 阈值的 Compact；成功后用一条摘要
  替换旧历史，失败时保留历史并结束本轮。
- [x] 将本地图片导入现有 Session `assets/`，Context 保存稳定相对引用，由 Provider Adapter
  转换为厂商图片协议。
- [ ] 继续规范尚未覆盖模块的异常、日志和配置，并基于具体调用方引入必要抽象。

本阶段下一步按以下顺序推进：先在不改变四个公开入口的前提下整理 ReAct 内部重复实现，
再增加 256 Step Harness、统一错误 Event、Blackboard Compact 和本地图片稳定引用。这是现有
Agent 基础能力的增量整理与补全，不重新划分 Kernel、Plugin Runtime 或 EventBus 的职责。
详细设计见 `apps/agent/docs/arch/agent-core-capability-completion-design.md`，实施步骤见
`apps/agent/docs/plan/agent-core-capability-completion-development-plan.md`。

完成标志：新增 Plugin 和 Tool 不需要修改 Kernel 主循环；核心模块边界可以独立理解和测试；
关键同步、异步与终态行为一致；重构后的文档与实现保持一致。

### 阶段六：产品化对话与上下文能力

- [ ] 将 Agent 基础层已有的 Usage、Compact、Error Event 和图片引用继续封装为产品可理解的状态
  与交互，不在 UI 中重复实现基础逻辑。TUI 已完成 macOS `Ctrl+V` 图片输入首期闭环；Usage、
  Compact、错误详情、图片历史展示以及 GUI/WebUI 接入仍待完成。
- [ ] 完善对话元数据、消息历史和索引持久化，支持进程重启后枚举并恢复已有对话。
- [ ] 增加对话切换的应用层接口和 TUI 交互，明确运行中任务收束、状态保存、Runtime/Blackboard
  恢复以及输出订阅和 UI 投影切换。
- [ ] 覆盖新建、恢复、切换、Compact 展示、错误展示、跨会话图片交互和异常退出的端到端验证；
  当前 TUI 图片草稿、排队、提交和清理已有自动化回归。

依赖顺序：Agent 基础能力先稳定；对话持久化和索引是恢复与切换的前置条件。对话生命周期由
应用层和 Persistence/Blackboard 协作完成，ReActAgent 继续保持无状态。

完成标志：用户可以查看、恢复和切换对话；产品界面正确呈现基础层已有的 Usage、Compact、
错误和图片状态，失败或重启不会造成历史混淆。

## 并行关系与依赖

```text
TUI 独立 Bug 与体验修复 ───────────────────────────────▶
                                      │
                                      └─ Kernel 控制能力完成后接入任务取消

端到端产品体验与对象生命周期基线 ───────────────────────▶
                  随 TUI、Kernel 与 Core 的真实能力持续完善

Agent Core 现状基线
        ↓
最小 Kernel 边界
        ↓
通用“陷入内核”
        ↓
真实场景验证
        ↓
Agent Core 系统性重构
        ↓
产品化对话与上下文能力
```

依赖关系：

- TUI 任务级取消依赖 Harness 的任务控制和 Kernel 的明确终态；
- 端到端生命周期不是新的跨层控制中心；每个对象仍由所属层管理，产品链路只统一状态语义、
  用户反馈、关联证据与验收口径；
- 当前 Run 内的 Memory、Knowledge 补充依赖通用“陷入内核”，不依赖 Run 内 Tool 热加载；
- Plugin 贡献 Tool 是 Core 重构目标，但不阻塞“陷入内核”的首期设计；
- SkillPlugin 已通过通用 Tool、Capability 和 Event 边界接入，没有向 Kernel 增加领域分支；
- 系统性重构以真实场景暴露的问题为输入，不要求在新能力开发前一次完成。

## 当前暂不决定

- 内核操作在代码中的具体类型、接口和调度结构；
- 业务信息最终如何转换为模型可理解的上下文；
- 各类内核操作的完整集合和固定枚举；
- Skill 在大规模真实目录下的搜索词选择与召回体验；
- Run 内动态变更 Tool 集合。
- 对话切换时复用 Runtime 还是重建 Runtime 的具体实现。

## SkillPlugin 工具化验收记录

- [x] 发现：`skills_list` 支持 `all|global|workspace`，Workspace 同名覆盖全局，结果只返回
  `name`、`description`、`scope` 和 `path`。
- [x] 召回：`skill_search` 对名称、描述和可选关键词做简单归一化包含匹配，稳定排序并限制
  10 项；普通用户输入不再触发自动检索或 Prompt 注入。
- [x] 生产：`skill_produce` 受独立权限控制，显式选择写入作用域，预检和提交均检查双作用域
  冲突，并通过后台 Job 在专属 Draft 中生成包含脚本、资料和资源的完整 Skill 目录。
- [x] 演化：`skill_evolve` 受独立权限控制，捕获目标快照；Workspace Skill 原位更新，全局
  Skill 生成 Workspace override，并通过完整目录 Hash 在并发变化时失败关闭。
- [x] Job 与通知：状态可查询并按 Workspace / Session 持久化；终态通过
  `TaskContextInputEvent` 尝试通知活动 Agent，通知失败不改变 Job 结果；退出阶段确定性收束。
- [x] 生成边界：独立 Agent 获取完整脱敏上下文和私有受控 Tool；Repository 统一校验并事务式
  发布 Draft；Workspace Skill 使用 `<current-workspace>/skills`，内部轨迹只进入 Session Trace。
- [ ] 真实模型与大目录体验验证：在凭据和代表性 Skill 集合可用后执行，不以单元测试替代。

这些问题在进入对应阶段后，基于当前代码、测试与真实使用场景单独设计。
