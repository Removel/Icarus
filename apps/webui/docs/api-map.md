# WebUI 与仓库接口映射

2026-10-01 核对仓库实现与测试。记忆、知识库和对话模块分别接入 Mem0、OpenKB、Gateway。开发和生产入口均提供同源代理；生产访问控制与配置见 [deployment.md](deployment.md)。

## Mem0

依据 `apps/mem0/server/main.py`、`apps/mem0/tests/test_server_params.py` 及 Agent 的 `plugins/memory/mem0_http_adapter.py`。

管理分页的服务职责、查询快照与字段归属见 [Mem0 分页架构](../../mem0/docs/spec/2026-10-05-memory-browser/arch.md)。

| 页面操作 | 现有接口 | 接入要求 |
| --- | --- | --- |
| 列表、筛选、排序 | `GET /memories/page` | 管理接口；传 `page`、`page_size`、`state`、`query`、`category`、`user_id`、`run_id`、`descending`。服务先筛选、排序再分页，返回结果总数、实际页码、状态计数及筛选选项。前端固定传入每页 15 条，并用响应的 `page_size` 计算页数；接口默认 12 条、允许 1–100 条。查询快照沿用 1000 条上限，超出时返回 `truncated=true` 并在页面提示。 |
| 详情 | `GET /memories/{id}` | 已接入服务 ID 和元数据，支持单独加载不在列表中的详情。 |
| 手动添加 | `POST /memories` | `messages: [{role: "user", content: "…"}]`，所属身份与范围由 Gateway `memory.get_context` 读取配置和解析工作区，再附带 metadata；手动原文录入使用 `infer: false`。 |
| 修正、有效期 | `PUT /memories/{id}` | 正文字段叫 `text`，支持 `metadata`、`expiration_date`；显式 `null` 清除有效期。此接口不能修改所属用户或范围。 |
| 变更记录 | `GET /memories/{id}/history` | 读取全量后每页展示 10 条；`changes` 中的分类/有效期前后值映射为操作标签，正文差异默认展示变化区段、可展开全文。旧历史不反推缺失属性。 |
| 删除 | `DELETE /memories/{id}` | 确认后执行，服务成功才更新列表。 |
| 语义搜索（待接入） | `POST /search` | `query`、`filters`、`top_k`、`threshold`、`show_expired`；当前搜索框是服务端文本筛选。 |

工作区约定来自现有 Agent 适配器：`run_id = global` 或 `workspace:<workspace_key>`。Mem0 没有单独的 enabled 字段；WebUI 用 `1970-01-01` 表示停用，恢复清空有效期并明确提示变为长期有效。批量操作逐项提交、跳过无需变更的条目并报告部分失败。更新保留现有 metadata；创建原文使用 `infer: false`。当前是服务授权范围内的单用户管理入口，不提供按登录用户隔离。

## OpenKB

依据 `apps/openkb/openkb/api.py`、`api_models.py`、`api_documents_router.py`、`api_pages_router.py`、`api_graph.py`，以及 `tests/test_api.py`、`tests/test_api_documents.py`。

| 页面操作 | 现有接口 | 接入要求 |
| --- | --- | --- |
| 知识库切换 | `GET /api/v1/kbs` | 已接入；使用服务名称。 |
| 新建 | `POST /api/v1/init` | 传 `kb`；UI 不暴露服务器文件系统路径。 |
| 资料与页面清单 | `POST /api/v1/list` | 传 `kb`；返回 documents、summaries、concepts、entities、reports。资料包含稳定 `hash`、`doc_name` 与相对 `source_path`；页面清单不直接包含全部正文。 |
| 概览（待接入） | `POST /api/v1/status` | 可提供资料计数、最后编译与检查时间。 |
| 文件导入 | `POST /api/v1/add` | 已接入非流式结果；上传真实文件并逐项展示跳过、失败和完成。默认单文件 100 MiB、单请求 500 MiB，可被服务器环境配置覆盖。 |
| 阅读资料 | `POST /api/v1/document/source` | `kb` + 文档 `hash`，避免同名资料歧义；返回转换后的可读文本，非原始 PDF 二进制。 |
| 重新编译 | `POST /api/v1/recompile` | 已接入非流式结果；`doc_name` 传精确 hash，由服务解析到目标。 |
| 阅读知识页面 | `POST /api/v1/page` | `kb`、`path`。 |
| 修正正文 | `PUT /api/v1/page` | `kb`、`path`、`content`；服务维护 frontmatter，返回保存内容和失效链接处理结果。 |
| 页面关联（可选） | `POST /api/v1/page/links` | 独立文档关联画布使用全图响应中的有向边；单页接口可用于按需核对局部链接。 |
| 知识关联 | `POST /api/v1/graph` | React Flow 画布将服务有向 `edges` 展示为实线箭头；来源声明来自节点 `sources`，展示为虚线箭头，摘要页来源可进一步指向资料；共同来源独立标为派生线索。 |
| 质量检查 | `POST /api/v1/lint` | 在独立检查页显式启动，传 `fix: false`；会调用模型并在后端生成报告，不修正知识。完成后刷新服务端报告目录，打开页面只读目录，不重复检查。 |
| 移除资料 | `POST /api/v1/remove` | 已接入；以精确 hash 获取 `dry_run` 影响并在用户确认后执行。服务会在执行时重新计算影响。 |

`DocumentItem` 没有持久的统一处理阶段字段，WebUI 在资料列表展示派生知识数量，正文读取状态仅用于阅读加载与失败提示；导入展示本次操作结果，不将浏览器加载状态称为处理进度。派生知识沿来源声明链计算，区分直接引用与间接派生，不沿普通正文链接推导。知识图中的来源可能先指向摘要页，再由摘要页指向资料；资料匹配优先使用服务给出的相对 `source_path`，重复名称保留待确认状态。当前只定位到对象级，没有段落或引用锚点。开发服务器从仓库根 `.env` 或进程环境读取 `ICARUS_OPENKB_API_TOKEN`，在代理侧转为 Bearer 请求头；浏览器代码不内置令牌。生产 Node 入口也在代理侧添加服务凭据，并验证入口认证及请求来源。

## Agent Gateway

依据 `apps/gateway/src/protocol/methods.py`、`apps/agent/src/agent_orchestration/plugins/runtime_update/plugin.py` 和公共 RuntimeUpdate 类型。浏览器通过同源 `/rpc` WebSocket 调用 JSON-RPC 2.0。

| 操作 | 方法 | 行为 |
| --- | --- | --- |
| 记忆归属与范围 | `memory.get_context` | 可选绝对 `workspace_path`；仅返回 user_id、agent_id、run_id 和规范化路径，不创建会话、不返回凭据 |
| 会话列表、新建、状态 | `session.list` / `session.create` / `session.get` | 传服务端 `workspace_path`，会话使用稳定 session ID；WebUI 新建传 `load_runtime: false`，首次提交再加载 Agent |
| AI 会话标题 | `session.generate_title` | 传 workspace_path、session_id；从首条已保存的用户消息生成并保存标题，返回 title，失败时为 null。列表同时提供 title、created_at、updated_at |
| 订阅 | `session.subscribe` | 传状态返回的 workspace_key 与 session_id |
| 恢复历史 | `session.get_history` | 每页最多 500，使用 after_sequence 游标；先订阅并缓存通知，再加载历史 |
| 提交 | `session.submit` | 文本 prompt 与 submission_id；明确失败保留草稿，未知结果不自动重发 |
| 引导 | `session.steer` | 当前 task_id、prompt 与 submission_id；accepted 表示已接收，user.correction 表示已应用。已结束或正在取消时恢复草稿，不自动创建新任务 |
| 排队 | 本页队列 + `session.submit` | 执行或等待确认时 Enter 入队；前一任务结束且历史同步成功后依次提交。停止、发送失败或未知结果暂停队列；编辑、删除与继续由用户操作 |
| 取消 | `session.cancel` | 逐个取消当前会话的 active_task_ids，等待 finished 通知更新状态 |
| 实时更新 | `runtime.update` 通知 | 支持 user、assistant text/thinking、tool started/completed、task error/finished；未知类型忽略 |

完整消息替代同一步 delta；按持久化 sequence 去重，迟到 delta 不覆盖完整文本。断线自动重连并恢复历史，显式重试复用原 submission_id。Gateway 重启后提交幂等记录不保证保留，用户需先核对历史。当前仅文本输入，没有资源上传、语音或多模态入口。

## 本轮取舍与后续顺序

本轮保留内容管理主流程；去掉 Mem0 商业付费入口，OpenKB Chat 交给 Icarus 统一对话入口。服务配置、watch、Deck、Skill、整个知识库删除、独立知识页面删除暂不放入此版；这不代表后端缺少相应能力。

资料与知识详情使用弹窗，地址栏保留对象及原始入口；关闭后恢复原列表或关联画布。画布拖动、缩放和布局重置均为前端状态，不新增写入接口。知识模块已具备真实加载、错误提示、非流式操作和安全 Markdown 阅读（代码块、表格、引用、列表、链接、知识双链与文章目录）。知识列表按服务索引中的标题／摘要匹配，资料列表按文件名匹配；正文加载不改变列表标题与搜索结果。后续处理知识操作的流式进度、大规模库分页和服务端检索。Shell 只管理导航与公共上下文；服务特有的请求、响应和错误解释留在知识模块。共用 UI 包承载视觉组件、路由、未保存修改保护与两个业务模块复用的 Gateway 传输客户端，不承担业务代理。

报告管理复用 `POST /api/v1/list` 的 `reports` 与 `POST /api/v1/page` 的 `reports/<filename>`；新增 `POST /api/v1/report/delete`，参数 `kb`、`path`，带现有 Bearer 认证。只删除报告，不调用知识页面的链接清理。

会话删除使用 `session.delete`，参数为 `workspace_path` 和 `session_id`。返回 `discarded`、`busy` 或 `not_found`；仅在成功或已不存在时移除前端列表项。服务拒绝执行中或正在加载/卸载的会话，空闲会话先停止运行时再软删除。
