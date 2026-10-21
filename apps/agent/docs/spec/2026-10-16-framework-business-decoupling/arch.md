# Agent 框架与业务解耦｜当前架构

> 状态：重构代码已落地并完成当前验收；本文以 2026-10-17 当前源码与测试为依据。
> 目标和 D1–D42 见 [plan-target-architecture.md](plan-target-architecture.md)，任务及验收映射见
> [plan-tasks.md](plan-tasks.md)，执行证据见 [plan-validation.md](plan-validation.md)。

## 1. 包、安装与依赖方向

`apps/agent/pyproject.toml` 定义本地 distribution `icarus-agent`，src-layout 导出：

```text
apps/agent/src/
├── icarus/
│   ├── core/       类型、接口、Agent/Tool、能力请求、Hook值契约
│   └── runtime/    发现装配、单总线、串行worker、SessionHost、资产/存储/记录/扇出设施
└── agent_app/
    ├── application/{controllers,services,repositories,models}/
    ├── config/
    ├── model_provider/
    ├── contracts/  应用级提示词贡献与读取契约
    ├── plugins/    12个具体插件
    ├── utils/
    └── resources/
```

依赖方向：`agent_app → icarus.runtime → icarus.core`；core不依赖runtime；框架不import产品插件、
ConfigModel或厂商SDK。`icarus`公开BasePlugin/Event/BaseTool/BaseAgent，导入时不加载SDK/业务插件。

Agent和Gateway**各自的venv**均editable安装同一个本地项目；CI临时环境显式使用with-editable。
Gateway/TUI仍保留自己的仓库根bootstrap，用于它们与根packages，不再作为Agent导入机制。
wheel携带12份manifest和default_settings.json，不包含测试、根apps命名空间或.env。

## 2. 会话与应用结构

```text
Gateway JSON-RPC/WebSocket
  → agent_app.application.AgentRuntime（同对象别名SessionApi）
      ├── SessionRegistry / SubmissionGuard / ResourceImporter
      ├── UpdatePipeline / HistoryProjector / SessionReaper
      ├── SessionStore（业务SQLite schema/事务）
      └── SessionRuntime（应用装配适配器）
          → icarus.runtime.SessionHost
              ├── 唯一TaskChannelRegistry / 资产设施
              └── PluginRuntimeHost
                  ├── discovery → resolver → PluginAssembly
                  ├── 插件/工具/能力/Hook注册
                  ├── 一个EventBus ingress/router
                  ├── 每插件一个串行inbox worker
                  └── PluginStateCoordinator / RuntimeRecorder
```

SessionApi保留原对外入口、dataclass/异常身份和同步/异步行为；旧agent_runtime模块只转发canonical对象。
服务实现不回引Controller；共享SessionEntry仍拥有原mutation/title锁、single-flight加载/卸载任务与失败锁存。

模型创建分工：core.AgentAssembly缓存通用Agent并接收LLMFactory与policy_for_role；应用ModelProvider factory
负责ConfigModel、厂商选择与SDK创建。模型配置按唯一name数组管理，model_roles选择主Agent/压缩/Skill/标题，
旧thinking/perception映射仍在加载时转换。少量app AgentFactory兼容组合根不进入框架。

## 3. 插件静态与运行契约

- manifest v2声明身份、tier、能力/版本、稳定事件ID、工具、状态域、required_config。
- manifest可选`contracts`绝对模块名，显式导出`EVENTS`与`CAPABILITIES`，扩展定义不必加入core内置名单。
  Host接受目录构造注入并复制条目；发现阶段仍仅JSON，定义模块统一在工厂前导入，同模块处理一次，冲突原子拒绝。
- 未知事件/能力及提供版本不匹配在工厂执行前分别诊断，按base/required失败或可选禁用并级联。
  运行注册仅遍历成功启用项，已禁用扩展不会因其未知能力再次拖垮Host。
- 类通过`@handles`绑定具名方法，handler返回事件序列或None；延迟产出继续publish。
  工具只从build_tools导出，装配阶段校验名称/唯一性并冻结。
- 业务handler与manifest严格一致；明确标记optional的兼容ContextContribution处理除外，不存在兜底任意事件入口。
- 类能力绑定必须是权威catalog中的同一个契约类；host端口按runtime_checkable Protocol校验，
  不能用非None任意对象冒充state.store使快照静默失效。
- provide/require按能力ID解析，不要求消费者知道提供者plugin_id。重复提供者失败，不采用first-wins。
- 基座设施identity/paths/assets/task_channels直接注入，不登记插件能力；Redactor是业务叶子utils。
- 用户配置schema和注入资源分离。manifest必填字段在import工厂前检查，完整schema在插件类可用后校验，失败走清理。
- default required清单保留；调用方明确移出required后，core降级警告、external禁用通知，base失败阻断。
  optional_consumed_events为consumed的子集，允许可选发布者缺失但不放松其余事件声明。

共享外部契约声明模块及导出类型的实际定义导入根在Host生命周期内保留；定义拥有者的实现禁用不会卸载消费者仍引用的类型。
Host结束或启动回滚时释放全部已获取导入根。模块import属于可信扩展代码执行，不是安全沙箱。

## 4. 启动、Region与结束

启动：JSON发现 → manifest依赖/版本/Python门禁 → 集中加载契约模块/类型登记 → 声明ID/冲突校验 → 重新依赖解析 →
全部工厂构造/构建级联 → 应用before_restore注册Region定义 → 插件start/状态restore/自检 →
最终失败级联 → 应用before_ready撤销失效owner、initialize/freeze → 工具冻结 → 总线激活 → READY。
定义必须在restore前存在，避免已有session Region快照因未知定义被跳过。

SessionHost拥有每会话唯一TaskChannelRegistry，Agent/UserInput/Host共享；不再由Agent提供给兄弟插件。
`input.submit`、`task.control`、`state.store`只供Host直连，不传给其他插件。

结束：拒绝新请求并解开pending能力等待 → 反向quiesce → drain → 状态snapshot → 反向stop →
记录器聚合flush/后端停止 → 资产设施停止。start失败和取消均走已有shielded清理。

## 5. 定向能力请求

内置业务请求有 `conversation.read`（Skill向Blackboard读取已提交Message快照）以及 `prompt.read`
（Blackboard向Prompt读取静态system快照；compaction后在同task首次模型请求前刷新）。
请求助手位于core，通过注入函数发送，拥有correlation/Future/超时/来源与响应类型/task校验；
总线只按不透明地址投递，能力流量不进入普通订阅和链推导。

响应在调用者普通inbox入队前完成配对，因此handler await不需要第二个worker或队列。
缺省30秒超时；取消传播CancelledError；停机/不可用合成UNAVAILABLE；提供者错误合成FAILED。
同一requester生命周期内correlation不可复用，防止旧回复完成新请求。

**当前明确限制：** 定向能力与有界插件inbox组合在绑定时拒绝；默认inbox仍无界。
这是为了避免唯一router阻塞在满普通inbox后，能力响应也无法路由。客户端扇出队列仍有界。
同步Skill工具通过已有宿主loop线程桥接，不能在同loop线程同步等待自己。

## 6. 保留的存储语义

| 数据 | 所有者/用途 | 持久化 |
|---|---|---|
| 完整可重放Message | Blackboard跨Run模型工作历史；安全提交、取消闭合、压缩 | plugin-state/blackboard.json |
| 公共RuntimeUpdate | 客户端展示/断线对账；完整文本/thinking/工具预览/终态 | icarus.db/conversation_updates |
| Session/标题/软删除 | 应用Repository | 现有SQLite表 |
| 创建时外部指令 | 应用私有初始化记录；Prompt静态external层 | conversation_updates的保留sequence=0，不占公共序号 |
| 图片/超大工具结果 | 基座资产与工具设施 | 会话文件/相对引用 |
| trace/log | 诊断，不参与恢复 | 现会话JSONL/log路径 |

数据库序号更新与插入仍同事务，成功持久化后再发布；失败仍取消任务、锁存failed并卸载。
模型历史不从UI投影反推；thinking不进入Message；模型compact不删除公共历史。
不新增跨SQLite/JSON原子事务、不迁移数据、不伪造旧版本丢失的消息、不物理清理软删除会话。

## 7. Hook与记录

Hook在登记位置顺序传值，按priority及注册序稳定执行；每一步隔离复制、类型/不变量校验后提交，
失败保留上一步值。不可改工具/路由/关联/task/run/状态/上下文expiry等控制事实。
Agent after还校验整个消息链的角色、ToolCall/ToolResult配对身份，不只最终回答。

LLM/Agent请求前可改真实参数；非流式返回可改允许的内容；已yield的流不可事后改写。
工具参数在并行性和策略判断前改写，结果在权威预算及最终截断/外置前改写；策略拒绝不能改成成功。

记录端口与Hook处理分离：core.RecordSink可由ContextVar绑定，runtime记录器负责点位/聚合，
persistence提供脱敏JSONL/log后端。通用Host无HookDispatcher也能记录插件生命周期和显式core.record调用。
高频成功路由计数聚合，错误立即记录；完整文本/thinking不重复进trace。后端启停失败隔离并收束其余后端。

## 8. 外部兼容与分页/扇出

Gateway继续负责JSON-RPC、校验、错误映射、WebSocket和(workspace_key,session_id)订阅过滤。
分页进入HistoryProjector，新get_session_history_page返回HistoryPage，旧二元组接口保留。
先legacy投影再分页，wire仍为records/history_cursor/next_after_sequence/has_more。
SessionStatus/TaskStatus/UnloadResult采用显式wire模型，领域对象仍dataclass。

BoundedMailbox/BoundedFanout为runtime通用原语，应用订阅与Gateway发送队列复用。
默认容量4096从runtime配置解析，显式构造覆盖独立保留；启动前订阅不因解析默认容量而丢失。
慢消费者溢出仍单独关闭，Gateway发送溢出关闭码1013。

## 9. 验证证据与限制

详见[plan-validation.md](plan-validation.md)。已验证安装后Agent/Gateway、类型、wheel、真实socket重启恢复；
TUI原有13项快照失败按测试身份与基线对比，没有将它们修成与本次无关的改动。
真实远端模型smoke在单独授权后使用临时数据执行，结果单独记录，不把本地fixture冒称远端模型。
