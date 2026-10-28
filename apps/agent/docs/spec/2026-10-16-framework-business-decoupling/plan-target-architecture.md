# Agent 框架与业务解耦｜目标设计与决策

> 状态：**待审阅的实施目标，不是已实现架构**。2026-10-16 从原 `arch.md` 的目标讨论整理而来。
> 当前实现见 [arch.md](arch.md)，迁移阶段见 [plan.md](plan.md)，逐项代码任务见 [plan-tasks.md](plan-tasks.md)。
> 保留 D1–D42 编号供追踪；其中 D6 / D39 按用户确认的存储语义修订。本文不表示已执行任何代码变更。

## 1. 已确认的范围

本次将 `apps/agent` 分成可复用框架和 Icarus 应用，框架暂不移入根 `packages/`，不发布到包索引。
目标不是热插拔、嵌套插件、重写 Agent 算法、重做历史存储、升级厂商协议或修改客户端产品行为。

**本轮用户明确确认：**

1. 保留现有存储语义：Blackboard 管理可重放模型消息并通过插件状态恢复；公共 RuntimeUpdate 历史保存在 SessionStore；两者不合并成新的权威记录源。
2. Hook 改为只改值、不决定流程；记录与日志从 Hook 分离，实施时同步修订 `AGENTS.md`。
3. 当前只编写代码任务文档，不实施代码、不安装依赖、不做 Git 变更。

其余接口、文件名、默认值和实施切片为**计划作者提案**，供任务文档审阅；不是新增的用户承诺。

## 2. 目标布局与依赖

```text
apps/agent/
├── pyproject.toml
├── requirements.txt / requirements-dev.txt
├── settings.json                       # 保留 checkout 中现有用户配置入口
├── src/
│   ├── icarus/
│   │   ├── __init__.py                  # 小型、无业务副作用的框架门面
│   │   ├── core/
│   │   │   ├── plugin.py / manifest.py / registration.py / types.py
│   │   │   ├── identity.py / resource_ref.py / runtime_update.py
│   │   │   ├── llm.py / assembly.py
│   │   │   ├── models/messages.py
│   │   │   ├── agents/                  # 原 capability 中的 BaseAgent / ReActAgent
│   │   │   ├── capabilities/            # 请求/响应契约与调用助手；不与 agents 同名
│   │   │   ├── events/ / hooks/ / tools/ / run_control/
│   │   │   └── contracts/               # 跨插件共享的输入/上下文/进程事件类型
│   │   └── runtime/
│   │       ├── assembly.py / discovery.py / resolver.py / registry.py
│   │       ├── capability_registry.py / event_bus.py / manager.py / worker.py
│   │       ├── host.py / session.py / state_coordinator.py / diagnostics.py
│   │       ├── paths.py / assets.py / storage.py / fanout.py / recording.py
│   │       └── wrappers/
│   └── agent_app/
│       ├── __init__.py / application/    # 公开 AgentRuntime 门面；内部按职责拆分
│       ├── config/ / model_provider/    # 产品配置、厂商选择与适配
│       ├── plugins/                    # 11 个插件，每插件一个目录
│       ├── tools/builtin/
│       ├── utils/redactor.py
│       └── resources/default_settings.json
└── test/
    ├── icarus/core/ / icarus/runtime/
    └── agent_app/                       # 与目标源码目录镜像
```

依赖方向：`agent_app → icarus.runtime → icarus.core`，同时允许应用直接使用 core。
core 不依赖 runtime；框架不 import `agent_app`、旧 `apps.agent.src` 或具体厂商 SDK。
运行时拥有插件对象不等于运行时可以 import 领域插件；装配只处理通用契约、注册项与注入函数。

根 `packages.gateway_protocol` / `packages.runtime_environment` 的其他应用消费者不随本次安装机制重构。
Gateway、TUI 仍可保留仓库根 bootstrap；框架包的可独立导入与整个 monorepo 的可分发不是一回事。

## 3. 决策台账

### 包、模型、应用与存储：D1–D14

| 决策 | 目标与实施约束 |
|---|---|
| D1 包布局 | 顶层 `icarus` 与 `agent_app`，src-layout；框架出现第二个非 agent_app 的直接消费者时再评估上提。Agent 实现包用 `core.agents`，能力 RPC 用 `core.capabilities`，消除原 capability 包/模块重名。 |
| D2 模型接入 | 通用 Message/ToolCall/Usage/LLM 类型和 BaseLLM 进 core。core.LLMFactory 只包装注入的按角色创建函数；实际读取配置、选择协议、创建 SDK 的 ModelProviderFactory 留 `agent_app.model_provider`。不把当前厂商工厂原样搬入 core。 |
| D3 模型角色 | model_settings 改为带唯一 name 的数组，LLMRole 为 str。所有角色读取改为按 name 查找；主 Agent、压缩、Skill 生成、标题的角色由配置显式给出，默认仍对应现有 thinking/perception。不得在骨架搬迁时顺手改 schema。 |
| D4 SessionRuntime | 通用 SessionHost 在 runtime，负责装配和生命周期，不是插件。应用保留薄 SessionRuntime 装配适配器以维护当前 session_factory 注入接缝。 |
| D5 应用拆分 | 对外 SessionApi（AgentRuntime 同一类的兼容导出）；内部 SessionRegistry、SubmissionGuard、UpdatePipeline、HistoryProjector、ResourceImporter、SessionReaper，各自一个职责，不成对创建无调用需求的接口。 |
| D6 存储切分（修订） | 保留完整模型消息/公共展示历史两种语义与现有路径格式。路径、身份、资产机制进框架；插件状态/trace/log 后端留插件；公共历史 schema、原子取号 SQL、完整性规则、checkpoint 时机留应用。仅抽取真正通用的 SQLite 连接与 WAL 生命周期设施；**本次不新增无人调用的 ConversationStore Protocol，不改用公共更新恢复模型，不新增统一历史表**。 |
| D7 RuntimeUpdate | 稳定冻结 DTO 进 core，生产者 runtime-update 留业务插件；现有 type 字符串、JSON 约束、payload、时间与 sequence 语义不变。 |
| D8 插件通信 | 运行期插件有状态查询通过能力请求/响应；通知通过普通业务事件；共用无状态工具移 utils；插件直接使用基座设施。装配期Region由应用回调在restore前注册定义，最终失败级联后清理失效owner并freeze，不在factory里等待尚未启动的总线。 |
| D9 tier | manifest 声明 base/core/external：base 失败阻断，core 降级警告，external 禁用并通知。显式 required_plugin_ids 可将任意插件升级为必须成功，不允许降级 base。缺失的必需插件清单由应用配置提供。 |
| D10 Hook | 只同步或在已有 async 调用边界串行改写值，不触发下游插件、不创建/删除调用点、不决定是否执行。具体值类型和执行位置必须登记。 |
| D11 事件链 | published/consumed 推导出的诊断视图，不新增链声明、worker 或顺序引擎。能力请求/响应不进入普通订阅图。 |
| D12 Gateway | 保留参数校验、JSON-RPC、错误映射、订阅与 wire 转换。分页策略进入应用；新增分页入口，保留原 get_session_history 二元组接口，wire 四字段不变。 |
| D13 声明去类路径 | manifest事件使用稳定类型ID，与实例UUID区分；工厂仍module:function。内置类型目录由core持有，扩展可通过Host目录注入或manifest可选contracts模块声明EVENTS/CAPABILITIES；发现仍只读JSON，可信定义模块在工厂前单独加载并校验，runtime不写具体业务分支。 |
| D14 扇出 | runtime 提供有界 mailbox/fanout 原语；应用订阅和 Gateway 发送队列复用，溢出仍断开慢消费者。统一默认容量 4096 与配置来源，但保留显式构造参数覆盖。 |

### 注册、契约与插件：D15–D29

| 决策 | 目标与实施约束 |
|---|---|
| D15 独立注册表 | 插件、工具、Hook、能力提供者独立登记；内部 helper 不登记，不做 object 万能注册表。 |
| D16 能力声明 | manifest provide/require + 版本继续用于启动校验、拓扑排序、失败级联；运行期插件查询改事件化，不取消能力声明。 |
| D17 六类 manifest 契约 | 身份、能力、事件、工具、状态域、配置。required_config 只列用户配置字段，不要求用户在 settings 写入 identity、assets、logger 等注入对象。 |
| D18 能力类 | 一个能力契约绑定请求/响应类型；state_store 是基座启动前必须同步调用的专门直连接缝，不为了统一形式伪造 RPC。 |
| D19 权威清单 | core提供内置能力定义，Host会话目录合并显式扩展契约；runtime registry独立绑定启用provider。未知ID在工厂前早失败；共享定义不激活能力，重复provider和类型冲突不采用first-wins，受影响插件按tier/required策略处理。 |
| D20 注册唯一性 | 四个产品内置工具仅从插件注册。删除 AgentFactory 的未使用自动注册分支；Skill 私有生成工具仍通过其私有 ToolRegistry 登记，不注册成嵌套插件，也不泄漏给主 Agent。 |
| D21 handler | @handles 绑定业务事件和能力请求；无兜底 consume。响应由框架配对，不进入业务 handler。 |
| D22 能力类声明 | 类绑定其提供的能力类型，启动时交给能力注册表校验，不向消费者暴露具体提供者类型。 |
| D23 manifest 权威 | 对外契约以 manifest 为准，类只做实现绑定；业务 handler/能力 handler 分开校验，不错误要求能力事件也进入业务 consumed_events。 |
| D24 返回值与产出 | handler 返回 Sequence[Event] 或 None，后台延迟产出继续 self.publish。现有 manager 已限制未声明 publish，本次扩展此守卫而非从零重建。能力响应按能力契约校验，不受普通事件 published 集合错误拦截。 |
| D25 工具 | BasePlugin.build_tools 唯一导出插件工具的路径，manifest 校验名称，runtime 加执行策略与预算；Agent 从 ToolRegistry 获取可用工具，不直接接收业务实现列表。 |
| D26 双写校验 | 类能力类型与 manifest ID/版本在启动时强制一致。 |
| D27 配置校验 | manifest 提前校验必填用户字段；插件 pydantic schema 校验类型/默认值/跨字段规则；依赖注入字段另行校验，避免 JSON 与 Python 两套完整 schema。 |
| D28 生命周期 | 保留 start → restore → ready，以及反向 quiesce → drain → snapshot → stop。新增 async on_session_start() -> None，自检失败用异常表达并交给 tier/required 处理；不造布尔结果 DTO。只覆写有工作的方法，不要求 11 插件实现空方法。 |
| D29 能力 ID | 按契约命名而不是提供者；RequiredCapabilityManifest 删除 plugin_id。runtime/session/redactor 不改名成 RPC，而从插件能力清单移除，分别变为设施和 utils；未消费的 skill_management 删除。完整映射见 task 文档。 |

### 请求通路、Hook、应用与工程：D30–D42

| 决策 | 目标与实施约束 |
|---|---|
| D30 调用点 | await self.request(ReadConversationRequest(...))；由请求类型定位能力，调用方不传 provider ID。 |
| D31 请求响应 | correlation_id、有限正 timeout、OK/NOT_FOUND/UNAVAILABLE/TIMEOUT/FAILED、可选 error_code/error_message；传输失败合成同类响应。unwrap 可抛领域化调用错误；调用协程取消仍传播 CancelledError，不伪装为业务失败。 |
| D32 单一传输 | 复用 EventBus ingress，无新队列/worker/订阅机制，不重试、不广播、不做内容过滤。禁止在插件 start/selfcheck 阶段调用 request。 |
| D33 职责分工 | registry 做能力→provider 绑定；bus 只查不透明地址投递。core requester 配对/超时；响应在接收方普通 inbox 入队前经注入配对回调完成，避免 handler 等待自己队列中的响应。provider 请求循环仍只靠超时终止，不暗造并发模型。 |
| D34 值传递 | handle(event, value) -> value；事件元数据仍 frozen。真实值有独立类型，不复用观测摘要；Hook 串行执行。 |
| D35 优先级 | 小数值先执行，同优先级按注册序；同步与异步执行顺序一致。 |
| D36 失败 | 非侵入失败：异常/类型不合法记录后保留该 Hook 之前的值。必须隔离可变参数，不能先改坏原对象再声称回退。无具体调用者前不新增 critical。 |
| D37 挂点 | 开放但登记位置/输入输出类型；工具参数在策略/并行性判断前改写，结果在最终预算裁剪前改写；stream 请求可以改，已 yield 的块不能事后重写。 |
| D38 记录器 | runtime 定义记录点、按任务/运行聚合高频路由计数；persistence 提供 JSONL/log 后端与脱敏。不将记录作为业务事件，不再用通配 FileTraceHook 记录一切；完整 thinking/text 不重复进入 trace。 |
| D39 接口按接缝 | 保留 BaseTool/BaseLLM/插件状态存储/UpdatePublisher 等真实接缝。不新增 ConversationStore；SessionStore 维持业务名称，不强制改名 SqliteSessionStore，也不为单实现应用类创建 Protocol。mypy 逐层纳入真实跨包契约。 |
| D40 触发生命周期 | 应用决定何时加载/卸载/删除；runtime SessionHost 只执行通用动作；提示词和 plugin_configs 由应用注入。TaskChannelRegistry 是每会话基座设施，不由 agent 插件提供给 user-input。 |
| D41 安装（纠正） | agent 与 gateway 有各自 venv，均安装同一个 apps/agent editable distribution；CI uv 临时环境也显式安装本地项目。Gateway/TUI 仓库根启动机制不在本次强拆；Agent 新包自身不再靠 cwd。settings 保留 checkout 用户入口，普通安装使用随包默认配置并可指定路径。 |
| D42 归属 | 有 manifest 的能力提供者是插件；无 manifest 的系统设施可以有状态，不进 PluginRegistry。按拥有者与变化原因归属，不能以有无状态或 required 标记替代判断。runtime 只依赖基座接口，不 import 具体插件。 |

## 4. 存储不变量

- `SessionIdentity` 的路径规范化与 workspace_key 哈希算法不变。
- `icarus.db` 表名、主键、连续物理 sequence、事务、软删除、标题语义不变；本次不做数据迁移或新旧历史双写。
- `blackboard.json` 的 envelope、state version、Message 序列化、旧状态兼容和安全前缀规则不变；不因 manifest/schema 变化清空历史。
- 压缩只改模型工作历史，不替换公共展示记录；thinking 仍不进入 Message。
- 公共历史成功落库后才对外发布；持久化失败仍取消活动任务、锁存失败并卸载。
- 两种存储之间目前没有跨数据库/文件的原子事务，本次不承诺增加该保证。
- 资产哈希文件名、路径逃逸保护、工具完整结果引用与本地路径脱敏不退化。

## 5. 失败与兼容边界

- 17 个 Gateway 既有公开入口实际是 16 个方法加 is_running 属性；构造参数、返回对象、异常身份、同步/异步形状、订阅关闭/溢出也属于兼容面。
- 新分页方法是兼容性扩展，不修改旧 get_session_history 返回二元组；wire 继续 records/history_cursor/next_after_sequence/has_more。
- 不能同时以新旧模块执行同一份源码；过渡 shim 只 re-export canonical 对象，最后删除内部旧路径。
- discovery 读取 manifest 不执行插件实现；错误 manifest 未必能可靠提供 tier，因此必需缺失依靠应用 required 清单，不能靠损坏文件中未经验证的字段猜测。
- 模型 SDK 选择分支只留 model_provider。默认提示词、默认插件集合与配置组装不进入 runtime。

## 6. 实施前审阅重点

具体默认能力 ID、tier 表、Hook 挂点、记录点和接口均见 `plan-tasks.md`。这些是可审阅的计划选择，不是待实现者自行发明的空白。
任何改变存储事实源、公开 wire、用户可见默认行为的需求必须另行讨论，不可借本次大重构顺手实施。
