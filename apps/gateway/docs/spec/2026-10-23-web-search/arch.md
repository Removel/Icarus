# 联网检索请求选项

`session.submit` 新增可选 `request_options`：

```json
{
  "web_search": false,
  "deep_search": false,
  "local_time": "2026-10-23T20:00:00+08:00",
  "time_zone": "Asia/Shanghai"
}
```

前两项为严格 boolean；时间与时区字符串各最多 100 字符；拒绝未知字段和凭据。
省略整个对象时保持旧客户端的调用方式。Gateway 仅校验并透传，不调用搜索或模型，
不修改 prompt/display_text。Agent 在通用输入选项中保存快照并纳入提交幂等指纹；
同一 submission_id 改变开关、深度或时间将返回提交冲突。

进度通过既有 `runtime.update` 的 `web_search.updated` 类型传递，历史仍由
`session.get_history` 分页读取。没有新增浏览器直连模型/搜索接口。

WebUI 行为与接口修正见 [WebUI 计划](../../../../webui/docs/spec/2026-10-23-web-search/plan.md)；
检索实现与配置见 [Agent 架构](../../../../agent/docs/spec/2026-10-23-web-search/arch.md)。
专项测试为 `test/test_web_search_options.py`。
