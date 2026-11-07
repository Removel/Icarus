# 记忆写入结果反馈修复计划

## 目标与基线

日期：2026-11-06。状态：已实施并通过应用回归。

本计划属于既有 Memory 功能目录；沿用目录首次进入 Git 的日期。开发基线为 PR #17 的 284bcc5ec4decb74a95d6d5a1aa13b09b48c82be，分支与统一 PR 安排见 [WebUI 实施入口](../../../../webui/docs/spec/2026-11-06-chat-reliability/plan.md)。

用户反馈“Agent 能调用记忆，但页面看不到”。代码确认 Mem0HttpAdapter.remember 使用 infer=true；MemoryPlugin.remember 可能返回 memories=[]，MemoryRememberTool 仍将正常空结果作为成功调用返回。空抽取与重复内容可以是正常无变更，不应编造数据库错误，也不应声称新增已经落库。

## 修改边界与文件

- src/agent_app/plugins/memory/plugin.py：复用现有结果 dict，补充简明的写入状态与数量，保留 memories 字段。
- src/agent_app/plugins/memory/tools.py、prompts.py：要求根据实际返回记录确认写入，不凭工具调用成功声称“已记住”。
- src/agent_app/plugins/memory/mem0_http_adapter.py：如发现异常响应被当空结果吞掉，在此边界验证返回结构。
- test/agent_app/plugins/memory/test_plugin.py、test_tools.py 和现有 Adapter 测试：同步/异步行为按已有接口覆盖，不为本任务新增另一套后端模型或客户端。

## 实施步骤

- [x] 补充非空记录、正常空结果、畸形响应、后端异常四类测试，先观察缺失行为导致的失败。
- [x] 正常非空结果使用明确的 stored 状态并返回实际数量及已有引用；正常空结果使用 no_change 状态和零数量，保留工具调用成功语义。
- [x] no_change 只能说明服务未返回新增记录；未获得服务原因时，不猜测是去重、抽取为空或数据库迁移。
- [x] 错误响应及结构损坏继续是失败，不能降级成 no_change；不在 Adapter 里拼造没有实际返回的记忆 ID。
- [x] 保留原文写入、全局/工作区作用域、user_id/agent_id 边界和来源 metadata；不修改稳定 System Prompt。
- [x] 说明中明确：只有得到实际记录才能确认保存；若为空，可先核对既有记忆，不能盲目反复写入。
- [x] 自动召回保持只读，ReActAgent 不引入 Mem0 或 UI 依赖；不用 Hook 触发页面刷新或其他插件。

## 验收与回滚

~~~bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/memory -q
make test-agent
# 若实际修改 Gateway 契约或 RuntimeUpdate，再执行 Gateway 聚焦及应用套件。
make typecheck-agent
apps/agent/.venv/bin/python -m compileall -q apps/agent/src/agent_app/plugins/memory apps/agent/test/agent_app/plugins/memory
git diff --check
~~~

- [x] 回归明确区分“调用完成”和“新增记忆”，旧 memories 引用仍可读取。
- 本轮未修改 Gateway 服务端，未运行 `make test-gateway`。
- 未执行真实模型写入；本轮使用隔离响应，不把当前用户数据库作为测试夹具。
- [x] 本任务只改反馈与验证，回滚不删除已写入记忆，不触碰历史库。


## 实施记录

2026-11-06：插件返回 `status=stored|no_change` 和实际 `count`，保留 `memories`；Adapter 拒绝缺失或畸形 results、空 ID。正常空结果仍成功且不能推断原因，工具说明和动态指引要求依据真实引用确认写入。未改变身份、作用域或稳定 System Prompt。

先观察到新增回归失败，再修复：Memory 插件目录 68 passed；`make test-agent` 1363 passed，退出 0；`make typecheck-agent` 成功检查 274 个源码文件。Mem0 内部另行修复持久化失败返回成功的问题，见其完整分页计划。
