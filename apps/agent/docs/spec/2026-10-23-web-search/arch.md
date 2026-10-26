# Agent 按需联网工具

本文描述当前源码实现。改造目标、评审边界与任务拆分见 [实施计划](plan.md)，
实际检查及未完成的外部验收见 [验证记录](plan-validation.md)。
请求选项由 [Gateway](../../../../gateway/docs/spec/2026-10-23-web-search/arch.md)
校验；展示由 [WebUI](../../../../webui/docs/spec/2026-10-23-web-search/plan.md) 负责。

## 组件与依赖

`agent_app.plugins.web_search` 是独立业务插件，提供两个常驻工具：

| 工具 | 业务参数 | 结果 |
| --- | --- | --- |
| `web_search` | `query`，去除首尾空白后 1–128 字符 | 本次结果总数、来源摘要、稳定编号及剩余预算；不自动抓取 |
| `web_fetch` | `url`，最长 8192 字符，通过本地 URL 策略 | 非空正文节选、完整长度、截断标记、稳定编号及剩余预算；允许直接抓取 |

`tools.py` 提供工具 schema 与同步/异步入口；`plugin.py` 管理任务身份、权限、预算、
进度和生命周期；`sources.py` 管理任务内编号及深拷贝快照；`pipeline.py` 只保留纯
解析、规范化与排序；`url_policy.py` 做本地 Fetch URL 检查；`http_backend.py` 封装
Search/Fetch API、错误转换和请求间隔。factory 仅装配 HTTP backend 与插件。

独立查询规划模型和回答前的 SearchPipeline 已移除。ReActAgent、通用 Plugin Runtime、
Blackboard 和供应商适配层没有新增搜索专用逻辑。主 Agent 通过现有 ToolExecutor
循环决定是否调用、查询词、页面及后续查证。

## 输入上下文与执行权限

`UserInputEvent` 到达后，插件先按 `task_id` 保存原问题及严格布尔开关快照，再立即
发布 `status=completed` 的 `ContextContributionEvent`。贡献包含本轮时间、联网是否
启用、深度查证及引用指引，不包含预取资料，也不发起规划或搜索请求。
这只是初始上下文准备完成；之后仍可执行多个工具操作。

SessionRuntime 保留 `web-search` 为 required context source 的等待关系：任务快照
建立、完成态贡献、context.ready、Agent 启动、工具调用按既有事件流程衔接。
动态内容进入当前 User Prompt，稳定 System Prompt 不变。不开工具的回答零搜索请求。

执行入口要求插件仍接纳工作、任务快照存在且有效、TaskChannel 为 RUNNING、
本轮 `web_search is True`、搜索 Key 已配置。未知、结束、取消或关闭的任务均拒绝，
不借用“最近任务”。工具常驻可见，关闭时 User Prompt 明确提示调用会失败。
该开关仅约束这两个联网工具；Bash 等其它工具的网络权限不由此控制。

两个业务 schema 均拒绝额外参数。ToolExecutor 先解析并剥离合法的 `_execution`
预算请求，再将可信 `task_id` 和有效超时通过独立执行关键字传给工具。模型不能
通过业务参数覆盖身份或额度；直接调用工具时携带 `_execution` 也会被拒绝。
同步入口通过已绑定的 RuntimeLoopRunner 回到同一个循环，不另建事件循环。

## 预算与请求边界

| 额度 | 普通 | 深度 |
| --- | --- | --- |
| Search 尝试 | 2 | 5 |
| Fetch 尝试 | 3 | 6 |
| 单页正文节选 | 6000 字符 | 6000 字符 |
| 累计正文节选 | 24000 字符 | 24000 字符 |

重复、失败、重试请求越过本地发起边界后均计次；无隐式重试。Search 此时追加 query。
参数/权限拒绝，以及任务锁或限流等待中取消/超时，不计请求次数。实际成功 Search
响应的原始行数累计为 `total`，与去重来源数不同。空搜索是成功的空结果；接口错误
和非法响应是失败。Fetch 只有正文非空才增加已浏览页面，重复页仍消耗正文额度。

`deadline_seconds` 默认 45 秒，现在表示本任务所有已接纳联网操作活跃区间的并集
长度。包含任务锁等待、跨任务限流等待、HTTP 和处理；不包含输入到达后尚未调用
工具的时间，也不包含两次操作间的模型思考。失败、超时、取消已花费的时间照常扣减，
同任务意外并发不双扣重叠时间。

设剩余工作时间为 R、框架有效超时为 F，本次操作上限为
`min(Search 15s / Fetch 20s, F, R)`。从进入执行/排队即启动同一个截止时间，取得锁
或请求槽位后不重置。每任务使用一把操作锁，工具不声明可并行运行。
同进程、同一事件循环内所有 backend 实例共享 ≥210ms 的发起间隔；不同进程或循环
不共享。HTTP client 内部 15/20 秒超时同时受上述外层总截止时间约束。

后端请求在独立受追踪的 task 中执行。外层操作负责截止时间、计数及成果提交；超时
或取消后立即使请求回调失效并取消请求。即使传输层吞掉取消，外层也能结束，迟到
结果不能写入任务。取消未获确认时不会宣称已完成后台清理。

## 来源、事件与历史

来源以请求 URL 的规范化结果为身份；已有规范化规则去除片段、常见跟踪参数、
默认端口、`www.` 和路径末尾斜杠。首次交付时按批次排序为新来源追加从 1 开始的
`citeIndex`，以后不因排序变化重排。重复来源只为后续输出补充更完整元数据。
直接 Fetch 非空成功也可创建来源。来源、结果和事件均深拷贝，后续补充不回写旧快照。

服务响应没有可验证的最终 URL，重定向或正文 canonical 提示不会改变引用身份。
不同请求 URL 落到同一页面仍可能产生不同编号，这是当前去重边界。
新任务重新编号；历史使用对应任务已持久化的公开快照。

复用原有 `WebSearchUpdatedEvent` 的全部字段，没有扩展事件协议：
`status,total,queries,sources,pages,browsing,notice`。每次事件是完整累计快照，正文
仅通过 Tool Result 给 Agent，不写入公开搜索事件或插件持久化 state。

| 操作 | 开始 | 结束 |
| --- | --- | --- |
| Search | `searching,browsing=false` | `complete`、合法空结果为 `empty`、失败为 `error` |
| Fetch | `complete,browsing=true`，保留已有成果 | 非空成功为 `complete`，空正文或失败为 `error`，均 `browsing=false` |
| 在途任务取消 | 已有 Search/Fetch 开始快照 | `stopped,browsing=false` 先于 `task.finished` |
| 发起前拒绝或取消 | 不新建进度节点 | 保留已有事实；等待中的操作不覆盖另一个活跃 Fetch |

单次 `complete` 允许之后再次调用；只有输入终态结束任务。取消 handler 标记停止并
取消在途操作，操作本身负责有界发布终态，再传播取消；没有工作时不重标已完成成果。
所有 `InputFinishedEvent` 状态均清理任务。旧状态对象必须仍是当前任务映射值且有效
才能提交进度；结束、停止或重载后不能由迟到响应重建状态。

插件 `stop()` 先拒绝新调用并使状态失效，取消外层操作和后端请求；它与 HTTP client
关闭共享最多 5 秒等待。超时抛出清理失败，上层更短取消继续向上传播。残余任务只
回收异常，不允许修改状态；正常任务取消的公开终态在 quiesce 阶段完成。

RuntimeUpdatePlugin 仅接受 `web-search` 身份的进度事件。AgentRuntime 按现有机制
保存 sequence 与历史，Gateway 透传，WebUI 按 task 覆盖累计状态并回放引用。
WebUI 为 `total=0` 的直接 Fetch 显示浏览中/已读取页面；只列成功页面，不补造搜索数。

## URL 安全与服务配置

Fetch 两个入口均检查 HTTP(S)、标准端口、无凭据、无控制字符或歧义 host，拒绝
环回、私网、链路本地、组播、保留 IP、映射 IPv6、非标准数值 IP、本地主机名和
本地域名后缀。来自 Search 的 URL 再 Fetch 时也必须通过相同检查。

域名通过本地格式检查不能证明远端抓取安全。此实现不在本机解析 DNS，也不直接
连接目标网页；实际抓取在配置的服务侧执行。`follow_redirects=False` 只限制
Agent 到搜索 API 的重定向。远端逐跳 URL/IP 校验、连接地址绑定与 DNS rebinding
防护尚无本次验证证据，是开放任意公网 Fetch 的部署前置条件。fixture 403 或
本地黑名单通过不构成该证据；具体待验事项见 [验证记录](plan-validation.md)。

服务端使用独立 `ICARUS_WEB_SEARCH_API_KEY`，不自动借用模型 Key。可配置
`runtime.plugin_config.web-search`：

```json
{
  "endpoint": "https://api.deepseek.com/v1",
  "api_key_env": "ICARUS_WEB_SEARCH_API_KEY",
  "deadline_seconds": 45
}
```

endpoint 无 path 时补 `/v1`，远端要求 HTTPS，仅回环允许 HTTP；模型不能指定服务
endpoint 或凭据。401/402/403/429 和网络错误转为安全、非致命提示，不带响应原文或
密钥。Fetch 403 表示服务安全策略拒绝，不推断为搜索权限不足。配置和环境变动在
新会话或重载后生效，更新进程环境需重启 Gateway。无 Key 时工具明确失败、Agent
仍可正常回答。外部资料均标为不可信数据；引用摘要不等于阅读正文。
