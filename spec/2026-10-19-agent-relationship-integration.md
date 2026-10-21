# Agent Relationship｜人物档案与关系接入跨应用约定

- 日期：2026-10-19；仓库级入口首次进入 Git 时核对日期，之后文件名保持稳定。应用功能目录沿用各自已有日期，不随本入口改名。
- 状态：应用实现与真实验收已完成；具体证据和已知限制由应用文档维护。
- 范围：`apps/agent` 的 relationship 插件与 `apps/rela-mem` 的 HTTP 契约；不扩展 Gateway、TUI、WebUI 或 Argus。

## 背景与目标

Agent 通过工具使用 rela-mem 保存人物身份、稳定档案和关系；事件与经历仍由 Agent 的 MemoryPlugin / Mem0 路径负责。rela-mem 保持独立服务，不承担分类或模型调用。

该接入要求 Agent 对人物身份和确认流程的判断与 rela-mem 的搜索、身份唯一性及写入语义一致，并依赖服务端提供安全的候选别名。因此在根 `spec/` 保留共同契约入口；可拆分的实现、架构和计划仍归各应用，不在本文件重复。

## 文档索引与所有权

| 所有者 | 目标设计 | 当前架构 | 实施计划与验证 |
| --- | --- | --- | --- |
| Agent：五工具、人物选择、写入保护、HTTP Adapter、静态指导及 Memory 分工 | [relationship 设计](../apps/agent/docs/spec/2026-10-18-relationship-plugin/design.md) | [relationship 架构](../apps/agent/docs/spec/2026-10-18-relationship-plugin/arch.md) | [relationship 计划与验收记录](../apps/agent/docs/spec/2026-10-18-relationship-plugin/plan.md)、[验收汇总](../apps/agent/docs/spec/2026-10-18-relationship-plugin/acceptance.json) |
| rela-mem：人物、别名、档案与关系的存储及 REST 契约 | [rela-mem 设计](../apps/rela-mem/docs/spec/2026-10-18-rela-mem/design.md) | [rela-mem 架构](../apps/rela-mem/docs/spec/2026-10-18-rela-mem/arch.md) | [rela-mem 计划（含 Agent 候选别名阶段）](../apps/rela-mem/docs/spec/2026-10-18-rela-mem/plan.md) |

源码与测试是当前行为的依据；应用级文档维护实际实现和验证状态。后续变更只有涉及共同契约时才同步本入口。

## 共同契约与职责边界

### 应用边界

- 依赖方向为 Agent relationship 插件 → rela-mem HTTP；不导入服务端 Python 模块，不共享数据库访问实现，不引入新的共享 package。
- relationship 是普通一级、纯 Tool 插件，提供 find/context/remember/forget/relate 五个工具；不自动召回、不注册 Region，不通过 Event 或 Hook 隐式触发人物查询。
- 事件/档案分类与用户交互归主 Agent；rela-mem 不运行额外 LLM。插件注册、静态指导和工具执行复用现有框架，不要求修改 ReActAgent 或 Plugin Runtime。

### 人物身份与候选

- `person_id` 是已选择人物的稳定引用；名字搜索只产生候选，不等价于精确身份解析。
- `GET /v1/persons` 每个候选附最多 10 条别名，仅公开 `alias_id`、`namespace`、`value`、`display_value`，保持人物级分页、过滤和认证语义，不公开内部身份索引字段。
- 候选别名是展示摘要，不能据其缺失断言某人没有其他别名。自动使用姓名必须建立在完整证据上，且仅限精确同名、唯一的情况；模糊、歧义或证据不完整时不能猜测写入对象。
- 新精确身份未绑定不证明人物不存在；存在姓名候选时先由用户选择已有者或确认新人。纯姓名无匹配也必须先确认创建，不自动按名字合并人物。
- 精确身份的唯一约束由服务端保障，归档不释放身份。Agent 不得忽略跨人物冲突；别名 409 只有重新核实属于同一 active 人物时才可视为已存在。

### 写入、删除与失败

- 人物与初始精确身份在同一次服务请求中创建；档案来源由 Agent 固定为 `agent`，来源引用由代码生成，不交给模型填写。
- 档案删除追加墓碑，不删除人物、关系或历史；合法 JSON null 与删除不同。关系失效保留现有服务语义，不虚构此次来源审计或关系历史。
- `a → b` 表示 a 是 b 的正向称谓；双方遵守同一方向约定，不因双向端点规范化交换业务含义。
- 服务通过分页状态说明后续数据，Agent 的工具结果明确反映不完整性；不能将截断、服务故障或认证失败当作“没有人物/事实”。
- 不提供跨请求事务、name-only 跨 Session exactly-once 或取消回滚保证；来源引用不是幂等键。写操作不自动重试，部分完成不能宣称原子成功。
- rela-mem 不可用只影响相关工具，不阻止 Agent Session 启动和其他工具运行；失败不应静默转写 Memory。

### 认证与部署

- 业务 HTTP 请求使用 Bearer Token；Secret 不进入仓库配置、日志或工具结果，非 loopback 接入要求 HTTPS。
- Agent 插件仅管理 HTTP client；“不扩展根全栈启动列表”是本接入最初的历史范围。
  后续仓库基建已统一登记 rela-mem 及其依赖，部署与数据安全边界见
  [统一生命周期架构](../docs/spec/2026-10-19-component-lifecycle/arch.md)；应用 HTTP 契约不变，插件仍不启动或迁移数据库。

## 依赖顺序与共同验收

1. rela-mem 提供保留原有分页语义的安全候选别名返回，再由 Agent 消费；双方各自保持应用内源码和镜像测试。
2. 最小契约和工具测试之后，分别运行 Agent、rela-mem 应用测试及必要的 Gateway 宿主回归；具体命令、测试数量与未通过项见各自计划。
3. 真实验收贯通 WebSocket → 生产 Gateway / AgentRuntime → 真实模型 → relationship 工具 → 真实 rela-mem 与存储，并以真实数据核验写入、墓碑和关系失效，不以模型口头回复或 Mock 测试代替。
4. 单独验证服务离线时新 Session 可启动、相关工具安全失败、其他工具和对话可继续。
5. 真实服务及付费模型验收须获执行授权，使用隔离数据和临时凭据，结束后仅清理本次创建的资源。

## 风险与变更边界

- 姓名候选不完整、身份冲突及并发写入可能导致误选或重复建档；优先拒绝不确定写入，不承诺自动合并或全局强唯一。
- HTTP 契约变更须同时检查 Agent Adapter 与服务端契约测试；不能只以一方单元测试通过判断接入兼容。
- 回退 Agent 插件不应清除 rela-mem 的人物、档案或关系；不通过回滚代码隐式迁移、重绑或删除业务数据。
- 工具参数、输出预算、包布局、配置校验和具体测试步骤归应用文档；本入口不重复后续基建的生命周期实现，也不新增圈子管理工具、Argus 接入或 UI 工作。
