# Demo 功能与仓库接口映射

2026-09-28 核对仓库实现与测试。以下是后续接入参考，当前 Demo 没有网络数据请求；并非已经完成这些接口的联调。

## Mem0

依据 `apps/mem0/server/main.py`、`apps/mem0/tests/test_server_params.py` 及 Agent 的 `plugins/memory/mem0_http_adapter.py`。

| 页面操作 | 现有接口 | 接入要求 |
| --- | --- | --- |
| 列表、筛选 | `GET /memories` | `user_id`、`agent_id`、`run_id`、`top_k`、`show_expired`；查看失效记录要显式包含过期数据。无身份筛选的全量列表有管理员限制。 |
| 详情 | `GET /memories/{id}` | 使用服务的 ID 和元数据，不依赖 Demo 字段齐全。 |
| 手动添加 | `POST /memories` | `messages: [{role: "user", content: "…"}]`，附带所属身份、范围、metadata；手动原文录入使用 `infer: false`。 |
| 修正、有效期 | `PUT /memories/{id}` | 正文字段叫 `text`，支持 `metadata`、`expiration_date`；显式 `null` 清除有效期。此接口不能修改所属用户或范围。 |
| 变更记录 | `GET /memories/{id}/history` | 将服务历史映射为时间线，不能把 Demo 的 history 对象当接口结构。 |
| 删除 | `DELETE /memories/{id}` | 确认后执行，服务成功才更新列表。 |
| 语义搜索（待接入） | `POST /search` | `query`、`filters`、`top_k`、`threshold`、`show_expired`；当前搜索框只有本地文本筛选。 |

工作区约定来自现有 Agent 适配器：`run_id = global` 或 `workspace:<workspace_key>`，检索同时限定用户与 Agent。Mem0 本身没有单独的 enabled 字段；Demo 用 `1970-01-01` 表示停用，恢复清空有效期。正式接入应明确恢复是否保留原期限，避免把这一展示约定误当成服务协议。Demo 只使用一个 Agent，并未提供 Agent 切换器。

## OpenKB

依据 `apps/openkb/openkb/api.py`、`api_models.py`、`api_documents_router.py`、`api_pages_router.py`、`api_graph.py`，以及 `tests/test_api.py`、`tests/test_api_documents.py`。

| 页面操作 | 现有接口 | 接入要求 |
| --- | --- | --- |
| 知识库切换 | `GET /api/v1/kbs` | 返回 `knowledge_bases`，使用服务名称与计数。 |
| 新建 | `POST /api/v1/init` | 传 `kb`；UI 不暴露服务器文件系统路径。 |
| 资料与页面清单 | `POST /api/v1/list` | 传 `kb`；返回 documents、summaries、concepts、entities、reports。页面清单不直接包含全部正文。 |
| 概览 | `POST /api/v1/status` | 资料计数、最后编译与检查时间。 |
| 文件导入 | `POST /api/v1/add` | multipart 文件和 `kb`，可使用流式结果；处理跳过、部分失败和完成状态。默认单文件 100 MiB、单请求 500 MiB，可被服务器环境配置覆盖。 |
| 阅读资料 | `POST /api/v1/document/source` | `kb` + 文档 `hash`，避免同名资料歧义；返回转换后的可读文本，非原始 PDF 二进制。 |
| 重新编译 | `POST /api/v1/recompile` | `kb`、`doc_name`，支持预览和流；不能仅依靠计时器标记成功。 |
| 阅读知识页面 | `POST /api/v1/page` | `kb`、`path`。 |
| 修正正文 | `PUT /api/v1/page` | `kb`、`path`、`content`；服务维护 frontmatter，返回保存内容和失效链接处理结果。 |
| 页面关联 | `POST /api/v1/page/links` | `kb`、`path`；前向与反向引用应使用服务结果。 |
| 图谱 | `POST /api/v1/graph` | 使用返回的 nodes、edges、types；当前六节点示意图不是实际知识关系。 |
| 检查与修复 | `POST /api/v1/lint` | `kb`、`fix`；分别展示结构报告、知识报告和修改结果。 |
| 移除资料 | `POST /api/v1/remove` | 先以 `dry_run: true` 获取影响，再确认执行；支持 `keep_raw`、`keep_empty`。Demo 仅模拟来源移除。 |

`DocumentItem` 没有持久的统一 phase 字段，Demo 的 ready / compiling / failed 是展示模型；正式实现需结合操作响应或流式事件，并确定刷新后的状态来源。资料到页面的来源关系也需读取真实页面元数据，不能按文件名自行推测。

## 本轮取舍与后续顺序

本轮保留内容管理主流程；去掉 Mem0 商业付费入口，OpenKB Chat 交给 Icarus 统一对话入口。服务配置、watch、Deck、Skill、整个知识库删除、独立知识页面删除暂不放入此版；这不代表后端缺少相应能力。

先评审布局、信息密度与关键流程，再接入认证和模块各自的数据适配层。随后补充真实加载、空状态、权限错误、部分失败、流式进度、分页/长列表，以及完整 Markdown 阅读。Shell 只管理导航与公共上下文；服务特有的请求、响应和错误解释留在各自业务模块。现有 UI 共用包只承载视觉组件，不承担业务代理。
