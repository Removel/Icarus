# 提示词插件｜验证记录

> 2026-10-17，源码基线b1cc666。以下记录实现阶段及提交前实际执行的验证，不代表远端已部署。

## TDD与聚焦测试

- 契约：8个缺功能RED→GREEN；与Runtime contracts加载回归26 passed。
- composer/工作区：22个缺功能RED→GREEN；Unicode/排序/整节丢弃/有界发现。
- 私有存储：4个缺功能RED→GREEN；现有store/history/stream组合20 passed。
- lazy create/restore：缺少instructions参数RED→GREEN；完整原文跨AgentRuntime恢复。
- Prompt EventBus：5个缺插件RED→GREEN；能力查询不被贡献等待阻塞，optional/required silence，乱序/重复。
- Blackboard：2个缺功能RED→GREEN；required失败不启动，同task compaction刷新；另加refresh失败重试。
- 应用装配：缺task_channels注入RED→GREEN；65个组合回归通过。
- runtime更新确认、optional错误隔离：2个行为RED→GREEN；不接受的上下文不消耗确认预算。
- optional单节超限保留同来源其它有效节：RED→GREEN。
- DB事务失败注入：真实SQLAlchemy Session.flush失败回滚，指令-only session可discard。
- 初始化采样：真实外部插件on_session_start后采样/optional启动失败剔除，2 passed。

## 当前整套与工具链

- make test-agent：**891 passed in43.07s**（审查修复后）。
- make test-gateway：**33 passed in7.42s**，1条既有Starlette/AnyIO DeprecationWarning（审查修复后）。
- make typecheck-agent：**Success: no issues found in232 source files**。
- make check-package-agent：wheel构建、12manifest、临时venv安装、非repo cwd导入和发现全部通过。
- TUI gateway_client/test_client.py：**9 passed in0.29s**，未修改客户端协议。
- compileall Agent/Gateway src/test与git diff --check：通过。

一次手动从repo root执行未带项目config的mypy命令产生import-untyped噪声；使用仓库规定的
make typecheck-agent（读取app pyproject和mypy_path）后通过，未添加ignore来掩盖错误。

## 实际TCP/WebSocket端到端

`apps/gateway/test/test_prompt_e2e.py::test_prompt_contributor_to_model_and_private_instructions_survive_real_gateway_restart`
**1 passed in4.90s**，已加入Gateway suite。

- Uvicorn真实监听、WebSocket JSON-RPC、生产AgentRuntime/SessionRuntime/PluginAssembly/Blackboard/Prompt/ReAct/LLM适配器/SQLite。
- 临时磁盘创建独立外部贡献插件，manifest发现而非直接手动consume：静态persona+动态mode均抵达模型HTTP请求。
- lazy创建保留私有指令，停止整个Gateway，再创建新Gateway恢复同session，工作区新文件生效而外部指令原文不丢。
- 指令不出现在公共历史/通知，公共sequence从1连续。
- 模型/记忆服务是本地确定性HTTP fixture，不能把这项描述为远端模型调用。

## 实际远端模型烟测

按用户本轮“真实端到端测试”目标执行一次受限调用：

- 真实Gateway/TCP/WebSocket→独立外部贡献插件→Prompt/Blackboard/ReAct→现有deepseek-flash远端模型。
- 临时workspace+data，mcp为空、Agent工具列表为空、max_steps=1、max_output_tokens=128、SDK自动重试关闭。
- 外部记忆/知识指向本地空结果fixture，不访问真实用户服务数据；未打印/保存凭据。
- 静态贡献static_code=S23，动态贡献dynamic_code=D47，外部session指令要求只回两码；
  **task_status=completed，both_plugin_codes_match=true，private_instructions_hidden=true，history_cursor=7**。
- 临时数据自动清理。诊断脚本/日志只在git忽略的本计划workspace，不进入源码或提交。

该结果证明真实远端模型能同时使用两个插件提示词层；不代表所有模型遵循度、安全性或Persona产品能力已验收。

## 实施时的明确取舍

1. 在现有feature工作区实现，不创建worktree/分支/提交，遵守仓库Git授权边界；代价是改动仍未隔离成分支。
2. 计划使用T1..T9标题而执行helper要求Task N，故用忽略目录ledger记录任务；代价是手动维护检查点。
3. 端到端先用真实网络+本地确定性服务，再增加一次128-token真实远端模型；代价是一次受限调用费用，不能代表全部供应商行为。
4. 阶段检查点在相关测试全部跑过前标为provisional；代价是不能仅靠聚焦测试提早宣布整任务完成。
5. 文案参考Claude/Hermes原则但不复制身份/不存在的工具；代价是其他模型上的文案效果仍需迭代。
6. 保留sequence0私有记录以满足无新表及公共连续性；代价是版本回退需保留兼容reader。
7. 所有改动未提交，忽略目录ledger/日志保留而不删除；代价是本地scratch保留待后续集成。
8. composition用例合并在test_budget.py而非新增重复的test_composition.py，增加test_assembly.py验证恢复后采样；代价是文件名与初稿不同。
9. 静态来源只在before_ready发现并采样，而不在before_restore重复登记；代价是注册发生在初始化末端，仍符合恢复后采样。
10. 类型验证使用项目make入口而非root裸mypy；代价是开发者需使用项目配置入口。

11. 审查发现运行期包裹计费偏差，公开既有TaskChannel formatter为format_runtime_context，所有原调用复用相同实现；代价是小幅新增core公共函数，但无业务语义或反向依赖。
12. 测试桩先进入PREPARING_CONTEXT再assert start_run成功，替代之前忽略返回值的设置；代价是fixture更严格，不放松生产检查。

## 独立审查与单轮修复

Fresh-context Opus审查确认5项缺陷，无stylistic minor：

1. SQLAlchemy私有insert异常包含bound parameters，Gateway traceback会泄漏正文。新增真实SQLite失败trigger测试，RED复现日志哨兵；在Store事务边界把SQL异常替换为不带原始异常链的安全错误，保留rollback。
2. 多次运行期提醒按一个准备JSON块计费，漏掉每批runtime_context包裹。新增生产TaskChannel多次drain测试，RED实际644>512；复用现有formatter公开函数，独立批次保守计费+pending预留。
3. 准备结束即允许更新，未校验成功/RUNNING。两项RED证明成功但未运行和失败准备仍能注入；现在要求succeeded+RUNNING+run_id。
4. 未校验确认来源与run，伪造结果可释放预留。真实EventBus RED；应用从task.control能力解析可信来源，确认需来源+task+run+request匹配。
5. 已过期input的零延时timer可被贡献抢跑。早到/晚到两项RED；保存绝对cutoff并检查接收时间和Event时间，过期先完成失败。

上述新增review+live integration测试：**9 passed in0.35s**；SQL日志隐私：**1 passed in2.38s**。
修复中发现旧测试桩未先mark_preparing_context，start_run实际返回False；改为真实状态推进并assert成功，不在生产放松状态门禁。
没有第二次重复代码审查，最后以全量回归、类型和打包验证收口；无deferred minors。

## 最终收口

审查5项均完成单轮TDD修复。最终Agent891/Gateway33通过；make typecheck-agent 232文件通过；
review修复后wheel重建、12manifest、隔离venv安装和导入通过。compileall与diff检查通过。
不做第二次重复审查；没有deferred minors。开发验收阶段未自动提交；之后按用户单独授权分层提交，不推送。

TUI全套实际执行：**253 passed、13 failed**（138.36s），13项均为test_app_snapshots.py既有快照失败；

```text
test_snapshot_initial_welcome
test_snapshot_session_picker
test_snapshot_empty_session_picker
test_snapshot_initializing_with_multiline_queue
test_snapshot_short_initializing_with_queue
test_snapshot_image_markers_in_queue_and_draft
test_snapshot_streaming_markdown_with_draft
test_snapshot_streaming_markdown_detached_from_bottom
test_snapshot_running_with_pending_queue
test_snapshot_tool_success_uses_positive_state_color
test_snapshot_run_card_thinking_correction_and_tool_preview
test_snapshot_tool_failure_and_agent_error
test_snapshot_narrow_running_layout
```

已从输出提取失败身份，与2026-10-16-framework-business-decoupling/plan-validation.md的基线集合精确比较，
**没有新增或缺失的失败身份**。生成snapshot_report移入git忽略的本计划workspace，不纳入功能提交。
WebUI全套未运行：本功能不改wire更新/客户端UI。
