# Gateway 保留联网选项缺省值执行方案

日期：2026-11-05。状态：实现与离线验收完成，见 [验证记录](plan-validation.md)。

整体目标及顺序见 [Agent 执行方案](../../../../agent/docs/spec/2026-10-23-web-search/plan-agent-autonomy.md)。
当前接口见 [arch.md](arch.md)。本文件只负责 Gateway 的传输与真实入口验收。

## 目标与边界

Gateway 不再把缺省 `web_search` 合成为 false。字段缺失保留给 Agent 解析为默认按需允许；
显式 true/false 原样传递。Gateway 继续严格校验选项类型、未知字段和时间长度，不调用搜索服务、
不加入模型判断、不修改 prompt/display_text、不新增 RPC 或协议字段。

当前 `RequestOptions` 的字段定义可保留，关键变更是 `_session_submit` 使用
`model_dump(exclude_unset=True)` 透传已提供字段。禁止用 `exclude_defaults=True`，它会把用户
明确传入、恰与默认值相同的 false 删除，破坏显式关闭。

## 基线与依赖

编制时 HEAD 为 `13743b0`，实际实施基线为 `96006ec`；实施前已核对工作树和 upstream/feature。
提示词工作已提交并保留。本方案与 Agent 缺省自动策略已配套完成，通过生产运行链离线测试。
既有测试环境、凭据及数据库保持原状，端到端测试只使用临时数据和本地 HTTP fixture。

## 任务 A：保留字段是否提供及严格输入校验

修改 `src/protocol/methods.py` 和 `test/test_web_search_options.py`。

- [x] 参数化添加失败用例：整个对象省略、`{}`、仅 local_time、仅 time_zone、deep_search=true、
  显式 web_search=true/false、false + deep=true。
- [x] 断言 Runtime 实际收到的选项，不只检查模型解析成功；省略对象和空对象可按既有空值约定
  不传或传 `{}`，但不能生成 web_search/deep_search 默认字段，不能丢失显式 false。
- [x] 记录当前普通 `model_dump()` 引出的失败，再改为 `exclude_unset=True`，保留字段校验定义。
- [x] 验证 null、字符串、数字等非法开关、未知字段/凭据、超过 100 字符的时间仍拒绝，且不调用 Runtime。
- [x] 保持 prompt、display_text、resources、submission_id 原样传递，已有客户端显式 true 可继续使用。

## 任务 B：Socket 级默认使用和重试验收

新增 `test/test_web_search_e2e.py`，复用 `test/test_runtime_e2e.py` 的本地服务设施。
必要时仅对原文件做小范围 fixture 提取，保留原 restart 用例，避免将它改成大型分支集合。

- [x] 本地模型服务 fixture 返回真实协议的工具调用序列：发现 web-search、执行 Search/Fetch、
  最后回答。本地搜索服务 fixture 返回确定性来源与正文；仅配置测试 Key。
- [x] 使用生产 Gateway WebSocket、AgentRuntime、适配器、执行器、插件图和临时 SQLite，
  不替换 Agent 或业务 RPC 方法。
- [x] 以 TUI 当前请求形状提交：不带 request_options；确认搜索请求实际发生、工具结果进入下一轮
  模型请求，任务完成且历史包含真实来源及搜索进度。
- [x] 仅时间/时区的请求也可调用；显式 false 即使模型强行调用仍零搜索 HTTP。
- [x] 同一 submission_id 的相同原始 options 重试只接纳一次；改成显式 false 等不同选项继续冲突。
- [x] 模型不调用工具时零搜索 HTTP；缺 Key 时正常完成回答，不把默认允许误判为服务已配置。
- [x] 测试 teardown 只清理本用例启动的服务与临时数据，不依赖外部服务或生产会话。

## 验证与交付

```powershell
& apps/gateway/.venv/windows/Scripts/python.exe -m pytest apps/gateway/test/test_web_search_options.py -q
& apps/gateway/.venv/windows/Scripts/python.exe -m pytest apps/gateway/test/test_web_search_e2e.py apps/gateway/test/test_runtime_e2e.py -q
& apps/gateway/.venv/windows/Scripts/python.exe -m compileall -q apps/gateway/src apps/gateway/test
```

最小范围通过后运行 `make test-gateway`，再执行上述 compileall 和 `git diff --check`。
记录实际命令、退出码、失败身份，
不将依赖缺失或 collection 失败记为通过。更新 `arch.md` 的缺省与显式值说明，新增本应用
`plan-validation.md` 记录本轮结果，与 Agent 验收互链；不要复制 Agent 具体实现到 Gateway 架构。

## 版本切换与回退

旧版补出的 false 与新版保留缺省的请求指纹可能不同。服务端配套切换前排空在途任务和
待确认提交；客户端先核对历史，不能在重试中静默重建 options 或 submission_id。
Gateway 回退与 Agent/WebUI 配套，按原补丁恢复序列化方式，无数据库迁移和历史删除。
实施验证时未执行部署、Git 提交或真实模型调用；2026-11-06 用户验收通过并授权提交 PR，
本次交付仍不合并或部署。
