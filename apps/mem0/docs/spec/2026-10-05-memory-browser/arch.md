# Mem0 管理记忆分页架构

设计说明首次进入 Git：2026-10-05。2026-10-06 根据 [服务路由](../../../server/main.py)、[分页与序列化组件](../../../server/memory_page.py) 和 [功能测试](../../../tests/test_memory_page.py) 核对当前实现。

## 接口与职责

`GET /memories/page` 是 Icarus 工作台使用的管理接口。路由沿用 `verify_auth`，启用鉴权时只允许管理员身份或管理 Key；关闭鉴权的部署沿用既有行为。原有 `GET /memories` 以及 SDK 的接口保持兼容。

服务路由负责参数验证、授权、读取向量存储快照与标记截断。`serialize_memory` 负责字段归属；`memory_page` 在传入快照上执行筛选、排序、统计与分页，不访问存储。WebUI 的调用与批量操作约定见 [接口映射](../../../../webui/docs/api-map.md)；批量启停或删除由客户端逐条提交，不属于分页接口。

## 查询与响应

参数：`page`（从 1 开始）、`page_size`（1–100，默认 12）、`query`（最长 1000 字符）、`category`、`user_id`、`run_id`、`state`（`all` / `active` / `expired`）、`descending`（默认 `true`）。WebUI 显式传入每页 15 条，服务默认值仍为 12。

文本查询去掉首尾空白后进行不区分大小写的子串匹配，覆盖正文、分类与用户；分类、用户与作用范围使用精确匹配。缺失分类显示为「未分类」。非空 `expiration_date` 早于服务当天日期才算过期，当天到期的记录仍属有效状态。

筛选与排序发生在分页之前。排序采用 `updated_at`，缺失时使用 `created_at`，不可解析的时间按 0 处理；时间相同时按字符串 ID 排序，降序开关同时作用于这两个排序键。

响应包含 `results`、`page`、`page_size`、`total`、`counts`（`all` / `active` / `expired`）、`categories`、`users`、`scopes`、`truncated`。`total` 是筛选后的数量，筛选选项及状态计数来自整个查询快照，不随当前筛选收窄。页码超出结果范围时返回最后一页；空结果返回第 1 页。

## 数据边界与字段归属

该接口沿用现有管理列表最多 1000 条的查询快照上限。服务会额外探测一条记录，超过上限时返回 `truncated: true`；这不是数据库全量分页，超过上限的数据不在搜索或统计范围内。工作台与 Mem0 服务需要同时更新。

列表记录与 SDK 详情采用相同字段归属：`memory` 是正文，`user_id` / `agent_id` 是身份，`run_id` 是全局或工作区作用范围。`actor_id`、`role`、`attributed_to` 和 `expiration_date` 也保留在顶层。`metadata.source_run_id` 才是 Agent 来源执行，`metadata.source_session_id` 是来源会话，`metadata.source_workspace_key` 是来源工作区；它们不能替代作用范围。正文、哈希、时间、ID 与内部检索字段不会混入自定义 `metadata`。

## 验证

功能测试覆盖筛选与排序先于分页、完整快照的计数与筛选选项、页码收敛、当天到期边界、空结果，以及来源元数据与身份字段的序列化。WebUI 的实际页面大小与真实服务联调结果记录在 [2026-10-05 验收记录](../../../../webui/docs/spec/2026-10-01-delivery-readiness/acceptance-2026-10-05.md)。
