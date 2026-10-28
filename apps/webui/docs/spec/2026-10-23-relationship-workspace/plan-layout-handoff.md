# 人物工作台：布局设计交接

更新日期：2026-10-27。用途：保留重新设计前的任务背景、接口能力与实施边界。当前入口为 [分阶段实施计划](plan-layout-implementation.md) 与 [交付记录](plan-delivery-report.md)：人物卡片、阅读式详情、圈子管理和代理已落地；[layout-redesign.html](layout-redesign.html) 已同步卡片布局。本文下方早期任务说明、尚未实现的描述及布局结论均属历史背景，不描述当前完成状态；接口仍须核对源码和测试。

## 1. 当前任务与交付边界

用户需要为 WebUI 的 rela 能力设计「人物」管理页面。已有一次静态 HTML 布局探索，用户不满意，希望交接给另一个 AI 继续设计。

本阶段先商讨页面结构并提供可直接打开的 HTML 布局稿。示例数据用于展示布局，不代表服务返回内容。正式接接口、修改应用和增加后端能力应在后续任务中明确范围。

布局可以重新设计。不要把上一位 AI 的分栏、卡片、标签页、全页详情或弹窗建议视为用户决定。遵循仓库根 AGENTS.md；未经明确请求不创建分支、提交、变基或推送。

## 2. 已确认的入口位置

这是本轮已经定下来的产品决策：

- 点击左下角现有账户入口，打开现有菜单。
- 「知识库」「记忆」「人物」与「系统设置」在菜单中同级并列。
- 点击前三项，在主区域进入对应管理页面；管理页面本身作为页面打开。
- 系统设置保持现有交互。这次讨论没有要求改造系统设置。
- 不增加独立「工作台」按钮，也不在账户菜单内再嵌一层「工作台」子菜单。
- 这意味着现有知识库、记忆入口也需要迁移；目前源码尚未落实该迁移。

菜单示意仅表示层级，顺序和分隔线样式可按现有菜单规范调整：

```text
左下角账户菜单
  知识库
  记忆
  人物
  ──────
  系统设置
```

用户曾担心约 330px 的侧栏里横排三个入口过窄，竖排又占历史对话空间，因此选择复用左下角菜单。侧栏应继续以对话与历史会话为主。

## 3. 布局讨论记录：哪些不能当定稿

| 探索 | 用户反馈 / 当前状态 |
|---|---|
| 对话侧栏顶部横排「知识库 / 记忆 / 人物」 | 用户认为约 330px 下可能过窄，已改为左下角菜单入口 |
| 三个入口竖排常驻 | 用户担心占空间，已改为左下角菜单入口 |
| 左下角新增统一「工作台」菜单按钮 | 用户澄清要复用现有菜单，与系统设置同级 |
| 主内容区左侧人物列表、右侧详情 | 曾评价「大致可以」，随后指出与记忆、知识库布局不一致，最终要求重新交接设计；不能当作最终认可 |
| 搜索筛选 + 人物卡片首页，点击进入完整详情 | 上一位 AI 的后续建议，未获得用户确认；不能直接作为下一版实现要求 |


## 4. rela 是什么，以及现在做到哪里

通俗理解：rela 是 Agent 的「人物通讯录与档案」。它保存人物身份、稳定资料、圈子，以及人与人的关系。

| 内容 | 归属 | 示例 |
|---|---|---|
| 人物身份、稳定档案、关系、圈子 | rela-mem | 小王的微信身份；喜欢咖啡；小王是我的同事 |
| 事件与经历 | Memory / Mem0 | 昨天与小王一起吃饭 |
| 文档及派生知识内容 | OpenKB | 团队文档、研究资料、知识页面 |

前端不自行推断、分类或在不同服务之间复制这些事实。人物页让用户看清 Agent 认识谁、记录了什么，并能纠正错误。

### 4.1 服务端

`apps/rela-mem` 是项目内实现的独立 HTTP 服务。仓库第三方来源记录没有将它列为某个人物管理项目的上游导入。主要依赖：FastAPI、Pydantic、FalkorDB、PostgreSQL、SQLAlchemy。

- FalkorDB 保存人物、别名、圈子、成员及关系图。
- PostgreSQL 保存档案条目、追加历史和删除墓碑。
- 服务自身不调用模型，不依赖 Agent 或 Gateway 的业务实现。
- 单用户、静态 Bearer Token；没有多租户、RBAC、人物合并或硬删除能力。
- 已接入根生命周期，默认 HTTP 地址为 `http://127.0.0.1:8767`。

### 4.2 Agent 接入

relationship 插件已有五个工具：

| 工具 | 功能 |
|---|---|
| `relationship_find` | 姓名候选搜索或精确身份查询 |
| `relationship_context` | 有限长度的人物档案、圈子、关系上下文 |
| `relationship_remember` | 记录稳定档案、身份，按规则选择或创建人物 |
| `relationship_forget` | 忘记指定档案字段，追加墓碑 |
| `relationship_relate` | 写入关系，或明确要求后将关系失效 |

插件已实现消歧、身份冲突检查、输入校验和部分写入失败提示；当前五工具不提供人物删除、圈子或成员管理、完整档案历史浏览。插件 factory 不在启动时探活。`required_plugin_ids` 表示启动必需性，不应仅凭 relationship 不在该列表就判定五工具未加载；实际加载与可用性需要检查运行诊断和配置。

Persona 还有独立的只读联动：会话启动时读取用户档案与关系摘要，默认预算 200 tokens，服务失败时跳过。仓库 Agent 设置开启该联动，包内默认关闭；它不是完整档案接口，也不是页面修改后的实时同步机制。

### 4.3 WebUI

截至本次代码核对：

- Shell 只有对话、记忆、知识库路由。
- 没有人物应用包、人物数据请求层或 rela 专用代理。
- 当前知识库、记忆入口仍在侧栏上部；左下角账户菜单只有系统设置。
- 对话中的工具结果使用通用展示，没有人物卡片或人物详情专用跳转。
- 本轮只新增静态 HTML 和设计交接文档，没有接通正式功能。

## 5. 重新设计时需要覆盖的用户任务

这些是设计应考虑的能力，具体首期范围与页面组织仍可讨论：

1. 查找一个人：通过姓名、别名或身份线索找到候选，明确区分同名人物。
2. 看懂一个人：查看姓名、别名、精确身份、稳定档案、所在圈子和关系。
3. 核对一条信息：查看来源、记录时间，以及指定档案字段的修改历史。
4. 纠正信息：补充或修正档案、忘记指定字段、添加或失效关系。
5. 关联浏览：从某人的关系到达另一个人，能清楚返回。
6. 对话联动：未来工具结果可打开人物详情，消歧后使用稳定 Person ID 继续操作。

关系图、圈子及成员完整管理可作为后续范围。服务已有接口不代表每项都必须在首页或首期展示。人物合并、精确身份重绑与人物归档的用户流程需要单独设计；不能简单放入一个泛化的「删除」动作。

### 信息表达要求

- 姓名不等于身份：同名合法，界面应提供可辨识线索，操作绑定 Person ID。
- 关系用自然语句明确表达，例如「王明是鱼梦的同事」「李欣是王明的姐姐」。
- 档案来源可能只有 `source` / `source_ref`；引用不是天然可点击的会话链接，不伪造来源跳转。
- 档案 key 是开放字段，值允许任意合法 JSON。职业、饮食、兴趣等中文分组只是旧稿示例，不是后端固定字段目录；必须考虑未知 key、对象、数组及空值的展示。
- 人物类别有 `user / agent / human / virtual`。用户和助手的特殊身份由部署配置初始化，普通创建接口只能创建 human/virtual；不能写死示例用户、助手 ID。
- 空数据、无搜索结果、服务离线、鉴权失败、部分区块加载失败与尚有下一页应有可区分的状态。

## 6. 与现有 WebUI 的一致性

用户明确提出「人物布局与记忆、知识库不太一样」，后续 AI 应先检查当前页面与代码再设计。

当前记忆和知识库的共同模式：外层管理页标题与返回对话入口、分类导航、搜索筛选、内容卡片集合；记忆详情与知识阅读详情主要通过弹窗打开。知识库的文档关联页另有图探索结构。

建议对照的重点：

- 进入/返回管理页的方式、对话侧栏保留与收起行为。
- 标题、分类导航、主要操作和搜索筛选的位置。
- 内容区宽度、边距、卡片/列表密度、圆角、分割线、字体与主题色。
- 详情出现方式、关闭/返回、刷新定位、筛选和滚动位置恢复。
- 主题切换、窄屏导航和键盘使用。

一致性要求不等于用户已选定卡片、弹窗或某个具体结构。尤其要区分：已确认「人物管理入口打开页面」，尚未确认「页面内单个人物详情采用弹窗、抽屉还是独立页面」。

| 参考对象 | 源文件 |
|---|---|
| Shell 路由、管理页标题与导航 | [App.tsx](../../../apps/shell/src/App.tsx) |
| 管理页尺寸、边距、响应式布局 | [workspace.css](../../../apps/shell/src/workspace.css) |
| 当前侧栏、账户菜单 | [DswSidebar.tsx](../../../packages/chat-app/src/DswSidebar.tsx) |
| 管理页承载与对话状态 | [ChatApp.tsx](../../../packages/chat-app/src/ChatApp.tsx) |
| 管理目标类型与设置交互 | [ChatSettings.tsx](../../../packages/chat-app/src/ChatSettings.tsx) |
| 记忆首页与筛选 | [MemoryApp.tsx](../../../packages/memory-app/src/MemoryApp.tsx)、[memory.css](../../../packages/memory-app/src/memory.css) |
| 记忆详情 | [MemoryDetail.tsx](../../../packages/memory-app/src/MemoryDetail.tsx) |
| 知识库首页 | [KnowledgeApp.tsx](../../../packages/knowledge-app/src/KnowledgeApp.tsx)、[knowledge.css](../../../packages/knowledge-app/src/knowledge.css) |
| 知识阅读详情 | [KnowledgeReader.tsx](../../../packages/knowledge-app/src/KnowledgeReader.tsx) |
| 公共组件、样式与导航 | [UI 包](../../../packages/ui/src/index.tsx)、[styles.css](../../../packages/ui/src/styles.css)、[navigation.ts](../../../packages/ui/src/navigation.ts) |

## 7. 可直接使用的 REST 能力

除 `/healthz` 外，所有业务接口及 `/openapi.json` 要求 `Authorization: Bearer <token>`。下面列出主要接口，完整参数以当前源码及鉴权后的 OpenAPI 为准。

| 资源 | 接口 |
|---|---|
| 人物搜索/分页 | `GET /v1/persons?query=...&kind=...&include_archived=false&limit=20&cursor=...` |
| 人物详情 | `GET /v1/persons/{person_id}` |
| 创建/更新人物 | `POST /v1/persons`、`PATCH /v1/persons/{person_id}` |
| 精确身份解析 | `GET /v1/identity/resolve?namespace=wechat&value=...` |
| 添加/删除别名 | `POST /v1/persons/{person_id}/aliases`、`DELETE /v1/persons/{person_id}/aliases/{alias_id}` |
| 当前档案、关键词/字段筛选 | `GET /v1/persons/{person_id}/profile?q=...&keys=...&limit=20&cursor=...` |
| 当前档案 key 清单 | `GET /v1/persons/{person_id}/profile/keys` |
| 写入/忘记档案字段 | `PUT /v1/persons/{person_id}/profile/{attr_key}`、`DELETE .../{attr_key}?source=user&source_ref=...` |
| 单字段历史 | `GET /v1/persons/{person_id}/profile/{attr_key}/history` |
| 人物所在圈子 | `GET /v1/persons/{person_id}/circles` |
| 圈子搜索/创建/修改 | `GET /v1/circles`、`POST /v1/circles`、`PATCH /v1/circles/{circle_id}` |
| 圈子成员读取/加入/退出 | `GET /v1/circles/{circle_id}/members`、`PUT` 或 `DELETE /v1/circles/{circle_id}/members/{person_id}` |
| 关系查询 | `GET /v1/relations?person_id=...&include_incoming=true&status=active&limit=20&cursor=...` |
| 新增或更新/修改/失效关系 | `POST /v1/relations`、`PATCH /v1/relations/{relation_id}`、`DELETE /v1/relations/{relation_id}` |
| 最短路径 | `GET /v1/graph/shortest-path?from=...&to=...&max_depth=4` |

### 接口限制会影响布局

- 分页返回 `items / has_more / next_cursor`，默认每页 20，上限 200。一般列表没有总数或总页数，不以第一页数量冒充全部数量；旧稿数字是示例。
- 游标绑定过滤条件，条件改变后需重新分页。人物当前按 ID 分页，不提供任意更新时间排序；设计中的排序需要服务实际支持。
- 人物搜索是姓名/别名文本子串匹配，不是语义检索。`name` 不能用于精确身份解析；精确身份未找到返回 `200 {"person": null}`。
- 人物列表附最多 10 个别名，不能宣称完整；人物详情有完整别名。列表侧没有精确的别名截断计数可用。
- `include_archived=true` 表示包含归档项，不是「仅已归档」。不能假定已有归档专用筛选、精确统计或回收站接口。
- 档案是逐 key 追加历史；当前值按 `recorded_at DESC, id DESC` 选定。active 的 JSON null 是合法值，不能显示成「已删除」。
- 忘记档案追加 deleted 墓碑；人物/圈子归档和关系失效也不是物理删除。关系没有完整变更史接口，不能把档案历史功能照搬到关系上。
- 关系 `from → to` 表示「from 是 to 的 forward_detail」。双向更新正反称谓时必须同时提交两者；服务可规范化存储端点，前端不能按返回端点顺序猜测原输入。
- 关系响应还可能包含 `other_person / relative_direction / detail / detail_missing`。特别注意 `detail` 的查询视角，展示自然语句前核对正反称谓；单向缺少反向称谓不等于关系不存在。
- 最短路径只按 active 关系做无向连接遍历，最多 5 跳，有 5000 节点/邻接行预算；不自动推导亲属关系，也没有独立全图聚合接口。
- 多字段/多请求写入没有跨请求事务。写超时可能已经生效，`source_ref` 只是来源引用，不是幂等键；不要自动重试后宣称一次完成。
- 当前档案来源枚举为 `user / agent / import / observer`；人工修改可使用 user，具体 source_ref 生成方式属于后续接入设计。

服务入口源码：[persons.py](../../../../rela-mem/src/api/persons.py)、[profile.py](../../../../rela-mem/src/api/profile.py)、[circles.py](../../../../rela-mem/src/api/circles.py)、[relations.py](../../../../rela-mem/src/api/relations.py)。

## 8. 正式接入时的技术边界

正式人物界面归 `apps/webui` 的应用包，Shell 负责导航与页面承载，人物包负责 rela 的类型、请求、数据状态及业务展示。包名和路由名尚未确定。

浏览器通过同源代理访问 rela，服务端附加 `RELA_MEM_API_TOKEN`。默认端点可复用 `ICARUS_RELATIONSHIP_ENDPOINT`；例如 `/api/rela` 可作为专用代理前缀，但该前缀尚未实现或定稿。当前 `/api/v1` 已代理 OpenKB，不应与 rela 混用。

需要同步考虑的文件：

- 开发代理：[vite.config.ts](../../../apps/shell/vite.config.ts)。当前按 `ICARUS_` 前缀加载环境，`RELA_MEM_API_TOKEN` 需要服务端正确读取，不能误暴露给浏览器。
- 生产代理：[server/index.mjs](../../../server/index.mjs)。沿用现有来源校验、请求头处理与鉴权方式。
- 启动环境：[icarus_start.py](../../../scripts/icarus_start.py)。补充所需变量的加载。
- 生命周期：[根 icarus.toml](../../../../../icarus.toml)。当前 WebUI 尚无 rela 依赖和 endpoint_env 映射；应考虑可选依赖，使服务离线时仍可打开 WebUI。
- 连通检查：[check-services.mjs](../../../server/check-services.mjs)。当前只检查 Mem0、OpenKB 和 Gateway。
- 路由与菜单：Shell、ChatApp、ManagementPage 类型、账户菜单及现有记忆/知识库入口一起调整。

人物浏览不创建 Agent 会话、不触发模型；不要从浏览器连接数据库。Agent 继续通过 relationship 插件访问 HTTP，不向 ReActAgent 塞入人物查询、UI 状态或具体服务协议。人物数据当前不按 WebUI 工作区隔离，不可用当前 workspace 假造后端隔离能力。

## 9. 验证记录与验收建议

仓库保留 [2026-10-19 Agent relationship 验收记录](../../../../agent/docs/spec/2026-10-18-relationship-plugin/acceptance.json)，记录过真实模型 → Gateway → 五工具 → rela REST/独立数据库的在线流程，也记录服务离线时任务继续完成。这是历史验收，不代表本机服务当前在线。

本次交接以源码与测试文件核对为主，没有重新运行真实数据库、模型或应用全量测试，也没有使用生产人物数据。

后续布局验收应至少覆盖：同名人物、长姓名/别名、大量人物、任意 JSON 档案值、零档案、无关系、单/双向关系、归档信息、分页、部分加载失败、服务离线、深浅主题和手机宽度。正式功能还需覆盖明确的写入范围、冲突与部分失败、墓碑/失效后的可追溯读取、刷新和返回定位。

## 10. 给下一位 AI 的任务说明

可直接复制：

> 请先阅读 `apps/webui/docs/spec/2026-10-23-relationship-workspace/plan-layout-handoff.md`，并查看当前记忆、知识库页面的代码和实际视觉。重新设计人物管理页，先给独立 HTML 布局稿供讨论。唯一已确定的导航决策是：知识库、记忆、人物放在左下角现有账户菜单，与系统设置同级，点击进入主区域管理页。人物页内部布局尚未定稿；已有 `layout.html` 用户不满意，后续卡片方案也未确认。设计需要考虑现有对话侧栏的空间占用，与记忆/知识库的视觉和交互协调，并遵守文档列出的实际接口能力。不要先改生产应用或把示例数据能力当成后端已有功能。

## 参考文档

- [人物工作台计划入口](plan.md)
- [rela-mem README](../../../../rela-mem/README.md)
- [rela-mem 当前架构](../../../../rela-mem/docs/spec/2026-10-18-rela-mem/arch.md)
- [Agent relationship 当前架构](../../../../agent/docs/spec/2026-10-18-relationship-plugin/arch.md)
- [Agent / rela 共同契约](../../../../../spec/2026-10-19-agent-relationship-integration.md)
