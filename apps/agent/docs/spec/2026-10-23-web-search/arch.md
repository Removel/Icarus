# 按请求执行的联网检索

WebUI 行为规格与公开接口核对见
[WebUI 实施计划](../../../../webui/docs/spec/2026-10-23-web-search/plan.md)。

## 组件与依赖

`agent_app.plugins.web_search` 是独立插件。`pipeline.py` 负责查询规划、检索聚合、URL
规范化、重复记录择优、排序、页面选择、正文预算与编号资料；`http_backend.py` 负责
Search/Fetch 请求、错误转换和进程内保守限速。它们不依赖 UI、Store、Blackboard 实现或
ReActAgent。`factory.py` 通过现有 LLMFactory 与 ObservableLLM 注入非流式规划器，不在
业务层判断模型供应商协议。

`UserInputEvent.request_options` 是最长 4096 字符的 JSON 对象快照。核心层只传递通用
请求选项；Gateway 的应用边界校验本轮 `web_search`、`deep_search`、`local_time` 与
`time_zone`。选项随 PendingInput 排队，参与 submission_id 指纹，不改写用户原文。

SessionRuntime 在插件实际加载后，将其身份加入 Blackboard 的 required_context_sources。
插件总会发布终态 ContextContributionEvent，包括关闭检索或失败的情况。时间与资料经
ContextBlock 进入当前 User Prompt；稳定 System Prompt 和 ReActAgent 均不改动。

## 执行与降级

- 默认关闭；关闭时不调用规划模型、Search 或 Fetch，只贡献本轮时间。
- 开启时规划 3–5 条互补查询；默认取 2 条，深度最多取 5 条。规划失败或解析失败用
  启发式 2 条继续。规划最多 8 秒，整条链路默认最多 45 秒。
- 每条 Search 请求 count=10。实际结果数组长度累加为 total，去重后的来源独立编号。
  部分检索失败保留成功批次；全部失败或无结果时贡献明确的降级说明，继续普通回答。
- 默认选择最多 3 页，深度最多 6 页，单独调用 Fetch。只把非空正文的成功页面发给前端；
  失败项不进入“已浏览”列表。每页正文最多 6000 字符，整段检索上下文最多 24000 字符。
- 来源不足不补造；Fetch 全失败时仅使用明确标为“未浏览正文”的搜索摘要。
- 所有 Search/Fetch 共用事件循环内的 ≥210ms 请求发起间隔。Search/Fetch 超时分别为
  15/20 秒。取消或任务结束会取消后台工作及所有子请求，并保留已获得的来源。

## 事件与历史

`agent_app.contracts.web_search.WebSearchUpdatedEvent` 承载 status、原始 total、实际
queries、去重 sources、已成功 pages、browsing 与 notice，不携带网页正文。
RuntimeUpdatePlugin 只接受 `web-search` 身份的该事件，转换为 `web_search.updated`。
现有 AgentRuntime 的更新持久化与 Gateway 历史接口保存这些快照；浏览器按 sequence
去重回放。通用 Plugin Runtime 不解释搜索事件。

引用序号由最终排序后的来源生成，并原样写入模型资料和公开来源列表。外部文本带明确
的反指令声明；搜索摘要与已抓取正文分别标明。前端图标仍在本地生成，不请求 favicon。

## 配置

Gateway 进程环境中的 `ICARUS_WEB_SEARCH_API_KEY` 是独立搜索凭据，不自动借用模型 Key。
DeepSeek 官方 Search/Fetch 使用普通 DeepSeek API Key；部署时可显式复用同一个 Key，
填入根 `.env` 的该变量。根 `.example.env` 提供空值入口，不包含真实凭据。
可通过 Agent 设置的 `runtime.plugin_config.web-search` 配置：

```json
{
  "endpoint": "https://api.deepseek.com/v1",
  "api_key_env": "ICARUS_WEB_SEARCH_API_KEY",
  "deadline_seconds": 45
}
```

endpoint 若没有 path，自动补 `/v1`；带 path 则按配置使用。仅回环地址允许 HTTP，远端要求
HTTPS；不跟随重定向，不把原始 HTTP 响应或凭据带入公共错误。缺少 Key 时前端收到可读
提示，正常回答继续。配置用于新建或重新加载的会话，更新代码/进程环境需重启 Gateway。
HTTP 401、402、403、429 分别转换为密钥无效、余额不足、访问被拒绝和限流提示；
Fetch 的 403 单独表示网页被安全策略拒绝，不推断为搜索权限不足。错误不包含响应原文或密钥。

## 验证入口

`test/agent_app/plugins/web_search/` 覆盖管线、HTTP 契约、限速、容错与真实插件图中的
任务取消/动态时间；`test/agent_app/application/test_search_submission.py` 覆盖请求指纹。
所有外部服务与模型均为隔离 fixture，不使用生产 Key。
