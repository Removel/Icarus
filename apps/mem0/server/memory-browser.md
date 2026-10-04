# 记忆分页接口

`GET /memories/page` 是 Icarus 工作台使用的管理接口，需要管理员身份或管理 Key。原有 `GET /memories` 以及 SDK 的接口保持兼容。

参数：`page`（从 1 开始）、`page_size`（1–100，默认 12）、`query`（正文、分类与用户的文本匹配）、`category`、`user_id`、`run_id`、`state`（all / active / expired）、`descending`（默认 true，按更新时间排序）。筛选与排序发生在分页之前。

响应包含 `results`、`page`、`page_size`、`total`、`counts`（all / active / expired）、`categories`、`users`、`scopes`、`truncated`。页码超出结果范围时返回最后一页；空结果返回第 1 页。筛选选项及状态计数来自整个查询快照，批量操作只作用于当前页选中的记录。

该接口沿用现有管理列表最多 1000 条的查询快照上限。服务会额外探测一条记录，超过上限时返回 `truncated: true`；这不是数据库全量分页，超过上限的数据不在搜索或统计范围内。工作台与 Mem0 服务需要同时更新。

列表记录与 SDK 详情采用相同字段归属：`memory` 是正文，`user_id` / `agent_id` 是身份，`run_id` 是全局或工作区作用范围。`metadata.source_run_id` 才是 Agent 来源执行，`source_session_id` 是来源会话，`source_workspace_key` 是来源工作区；它们不能替代作用范围。SDK 内部检索字段不展示为用户属性。
