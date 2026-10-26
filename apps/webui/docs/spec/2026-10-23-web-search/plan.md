# 联网搜索优化实施计划

首次记录：2026-10-23。

## 原规格与本地位置

用户提供的《DeepSeek 联网搜索复刻 · 实施规格书》已存在于
`F:/project/dswui/docs/deepseek-websearch-implementation-spec.md`，此前没有纳入
Icarus WebUI 文档。本目录保存其 [完整规格](plan-reference.md) 与
[协议取证依据](plan-protocol-evidence.md)。原规格的代码基线是独立 dswui，
其中路径、接口推测与价格不是 Icarus 当前实现的描述。

## 已核对的接口修正

2026-10-23 读取 DeepSeek 官方 [Search](https://api-docs.deepseek.com/api/search)、
[Fetch](https://api-docs.deepseek.com/api/fetch) 与
[Search & Fetch 指南](https://api-docs.deepseek.com/guides/search)：

| 项目 | 当前官方契约 | 实施约束 |
| --- | --- | --- |
| Search | `POST https://api.deepseek.com/v1/tools/search` | 统一构造 `/v1`，不使用根路径 `/tools/search` |
| Fetch | `POST https://api.deepseek.com/v1/tools/fetch`，请求 `{url}`，返回 `{title, content}` | 只有非空正文的 Fetch 成功才可标为已浏览 |
| Search 参数 | query 为 1–128 个 Unicode 字符；count 为 1–50，默认 10；可传 ori_question、need_content | 实际返回数组长度之和作为 total；不足不补造 |
| Search 字段 | title、url、snippet、summary、content、site_name、site_icon、date、rerank_score、authority | 兼容原有来源解析，保留发布日期与排序信号 |
| 限流 | Search 100 QPS，Fetch 25 QPS，另有站点域名限流 | 客户端仍采用规格的保守间隔 ≥200ms，不宣称它是当前官方限制 |
| 价格 | 两接口各 $0.75/1000 次成功请求；失败、空结果与超时不计费；2026-11-01 北京时间起计费 | 显示接口估算，另计模型规划和回答费用；不沿用未核实的人民币报价 |

浏览器 CORS 和任意第三方中转是否转发接口仍未确认；不能把接口不可用伪装成空结果或成功。

## Icarus 接入边界

当前 `packages/chat-app/src` 经 Gateway JSON-RPC 提交任务、接收 RuntimeUpdate、恢复服务端历史。
`dswui/` 仅包含展示资源，规格中的 `src/api/stream.ts`、`src/chat/generation.ts`、
`StreamBuffer` 和 localStorage 会话并不存在。模型凭据与供应商协议继续留在后端；
不得为了移植规格引入浏览器直连模型或另一套会话存储。

Icarus 的动态时间和检索材料应进入当前 User Prompt，遵守仓库规则；不能照搬原规格中
“动态资料插入 system”的方案。ReActAgent 保持无状态；搜索执行、来源管理、抓取和预算由
独立业务组件负责，通用 Plugin Runtime 不判断搜索事件类型。

验收保留原规格的不伪造、不阻塞、真实来源编号、本机时区、停止、历史恢复、无第三方
favicon 请求与默认浅检索要求。UI 的数量只能来自后端真实结果，不能从工具名称推断已抓取。

## 初次实施顺序（历史）

1. 保存原规格、核对正式 API、对照当前代码明确接入位置。
2. 实现多 query 规划、部分失败容错、URL 规范化、合并择优、排序、独立 Fetch 和正文预算。
3. 对接当前任务的开关、深度、时间与取消；保持排队和 submission_id 重试一致。
4. 将真实检索进度、来源、成功浏览记录投影到现有时间线与历史，接编号引用和来源面板。
5. 验证关闭搜索零工具请求、失败降级、停止、历史回放与移动端，再运行应用检查。

## 已落地的 Icarus 适配

检查结果、应用全套失败的基线对照与真实服务验收边界见 [验证记录](plan-validation.md)。

- [Agent 检索插件](../../../../agent/docs/spec/2026-10-23-web-search/arch.md)：主 Agent 按需
  Search/Fetch、稳定编号、正文预算、取消和降级，采用本轮 User Prompt 指引，不改变 Agent 内核。
- [Gateway 请求选项](../../../../gateway/docs/spec/2026-10-23-web-search/arch.md)：严格校验
  搜索开关、深度、本地时间和时区，沿用原任务与幂等提交机制。
- WebUI 将开关和深度快照绑定每条待发送消息；首次发请求时记录带时区偏移的本地时间，
  未知提交结果重试复用原快照。引导继续使用当前任务，不触发另一轮搜索。
- `web_search.updated` 进入现有思考与工具时间线；展示聚合结果数、1200ms 检索式轮播、
  最多 4 个本地站点标记、只含真实成功项的浏览列表、超过 5 项的展开和右侧来源面板。
- 回答中的真实 `[n]` 渲染为可点击角标；代码、公式、普通链接及不存在的编号保持原样。
  历史按 sequence 回放，终止时把仍在执行的检索标为已停止。

2026-10-23 初版为每轮一次有预算的多查询研究，当时不支持“资料不足再搜一轮”。
2026-10-26 工具化后由主 Agent 在预算内决定补搜。真实 Key 环境的结果数量和
模型是否遵守引用规则，仍需实际服务验收；不能保证外部引擎每个问题都返回 15 条以上。

## Agent 自主工具调用适配（2026-10-26）

Agent 的工具化改造与分阶段验收见
[Agent 计划](../../../../agent/docs/spec/2026-10-23-web-search/plan.md)。上段描述原有预取
实现的限制；工具化实现允许主 Agent 在剩余预算内多次 Search/Fetch，WebUI 继续
消费同一个任务的完整累计快照，不新增请求字段、事件字段或本地会话存储。

本应用适配限于 `ChatSearch.tsx`：直接 Fetch 尚未完成、没有已浏览页面时，也显示
真实的浏览中状态；成功直接 Fetch 且搜索 total 为 0 时显示“已读取 N 个网页”，
不误报“未搜索到合适内容”，也不补造搜索结果数。只有已成功抓取的页面进入列表。

`test/packages/chat_app/test_web_search.py` 增加直接抓取、来源引用与历史回放，以及
连续累计快照保留旧页面/引用编号的浏览器测试。验证记录由 Agent 功能目录的
[实施验证记录](../../../../agent/docs/spec/2026-10-23-web-search/plan-validation.md) 汇总，
该记录区分本地 fixture、浏览器检查和未进行的真实外部服务验收。

真实付费调用需要按仓库 CONTRIBUTING.md 的执行/费用授权规则单独进行；mock 通过不等同于
真实 Key 环境验收。未取得明确授权前不读取或调用生产 Key。
