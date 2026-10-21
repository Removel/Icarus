# 框架解耦验证记录

> 2026-10-17。代码未提交、未推送；所有原有无关未跟踪文件保留。
> 当前架构见[arch.md](arch.md)，任务见[plan-tasks.md](plan-tasks.md)。
> 本记录不包含凭据、模型完整输入输出或生产数据。

## 1. 官方与安装验收

| 验收 | 实际结果 |
|---|---|
| `make test-agent` | **784 passed**，26.68s |
| `make test-gateway` | **20 passed**，4.16s；既有Starlette弃用警告1项 |
| `make typecheck-agent` | **Success: no issues found in 222 source files** |
| `bash apps/agent/scripts/check-package.sh` | wheel构建、11manifest/默认配置/无.env资源检查、隔离环境安装与发现全部通过 |
| Agent安装后的非仓库cwd/资源/惰性发现测试 | 已纳入完整Agent套件，使用`python -I` |
| Gateway独立venv `python -I`导入Agent门面/资源 | 通过 |
| `scripts/tests` | 44 passed；开发环境原有根控制接口未改 |
| TUI CLI/Gateway client契约 | 15 passed |
| WebUI现有TypeScript入口 `tsc --noEmit` | exit 0 |
| compileall / `git diff --check` | 通过，最终交接前再运行 |

Agent和Gateway各自venv安装同一个本地editable项目。开发工具安装在用户明确批准后执行；
先前受限时使用PYTHONPATH的测试未冒充打包验收。wheel校验安装到一次性环境，不发布distribution。

## 2. TUI 已有失败对照

重构前：253 passed、13 failed；重构后：253 passed、13 failed。
对比**完整测试身份集合**，新增失败为空、消失失败为空。全部为既有快照差异：

- test_snapshot_initial_welcome
- test_snapshot_session_picker
- test_snapshot_empty_session_picker
- test_snapshot_initializing_with_multiline_queue
- test_snapshot_short_initializing_with_queue
- test_snapshot_image_markers_in_queue_and_draft
- test_snapshot_streaming_markdown_with_draft
- test_snapshot_streaming_markdown_detached_from_bottom
- test_snapshot_running_with_pending_queue
- test_snapshot_tool_success_uses_positive_state_color
- test_snapshot_run_card_thinking_correction_and_tool_preview
- test_snapshot_tool_failure_and_agent_error
- test_snapshot_narrow_running_layout

文件均为`apps/tui/test/test_app_snapshots.py`。没有改无关UI，也没有新增skip或接受新快照来隐藏失败。

## 3. 真实 socket 端到端

`apps/gateway/test/test_runtime_e2e.py`：实际TCP监听的Uvicorn/FastAPI → WebSocket JSON-RPC →
真实SessionApi/SessionHost → 11插件装配/事件总线 → 生产模型HTTP适配器 → SQLite/插件状态文件。
外部模型/记忆HTTP服务使用本地确定性fixture，**不是RuntimeStub**。

覆盖：health、create、subscribe、submit、完整文本、终态、历史游标、unload、停止整个Gateway、
重启新Gateway、恢复同Session并继续下一次模型调用；验证第二次真实模型请求带上一次Message历史。
该测试包含在Gateway 20项验收内。数据使用pytest临时目录。

## 4. 真实远端模型端到端

用户单独批准一次受限费用调用后执行：

- 实际Gateway/TCP/WebSocket与生产Runtime、模型厂商HTTP适配器；使用现有配置的远端模型。
- 最大输出128 tokens，max_steps=1，工具禁用；SDK客户端自动重试关闭。
- 全新临时Workspace和ICARUS_DATA_DIR；不读取生产会话，不写入Memory/Knowledge服务。
- 可选服务插件在该隔离smoke中禁用；没有启动原本未运行的本地Mem0/OpenKB。
- 任务状态：**completed**；预期返回标记匹配：**true**；公共历史游标：**7**；正常unload。
- 最后临时数据自动清理；没有输出或保存认证凭据。

此smoke证明真实远端模型与真实运行链路可用；不等于Mem0/OpenKB部署或远端服务写入验收。

## 5. 独立代码审查与修复

fresh-context reviewer完成只读审查并复现问题。将13项全部按实际影响纳入修复，不因标为Minor而忽略资源泄漏：

1. 有界插件inbox阻塞唯一router：绑定定向能力时明确拒绝此组合；默认无界inbox不变。
2. 超时后correlation复用：requester/session生命周期内拒绝已使用ID，迟到回复不能误配新请求。
3. host能力port与catalog：结构性Protocol校验，能力类必须是权威目录同一对象。
4. 禁用/回滚能力：撤销graph registry与manager绑定，关闭pending请求。
5. Hook上下文expires_at保护。
6. Agent返回历史逐条保护ToolCall/ToolResult身份和角色。
7. Hook实际字段类型校验，非法输出回退。
8. 记录后端部分启动/停止失败隔离，收束所有已启动后端。
9. 通用Host无需HookDispatcher也绑定core RecordSink并记录插件生命周期。
10. 启动前订阅不再被容量解析破坏。
11. 兼容writer拥有者stop关闭线程。
12. required_config在工厂模块import前校验。
13. 非流式LLM记录真实usage/finish/duration/字符数，不记录全文。

每项均有先失败后通过的针对性测试，位于`test_review_*`；修复后完整Agent/Gateway/mypy通过。
不新增第二轮Reviewer来替代覆盖测试；执行台账保留完整裁决与RED→GREEN证据。

## 6. 计划偏差与边界

- Region需要两个装配接缝：restore前有定义、最终级联后清理并freeze，不能只在末尾注册。
- `optional_consumed_events`为严格consumed子集，允许已明确可降级插件的通知发布者缺失。
- Blackboard保留具名optional legacy ContextContribution handler以支持既有手工测试图，不保留任意consume兜底。
- 公共容量参数以None区分省略与显式覆盖，实际缺省仍4096；启动前订阅保留。
- 应用AgentFactory/agent_runtime仍有薄兼容组合/导出；通用实现单一，不再复制类型定义。
- 原子序号SQL/业务schema留应用Repository；只有连接生命周期/PRAGMA下沉runtime。
- request correlation tombstone集合随会话存在；有界插件inbox+RPC尚不支持，启动时明确拒绝。
- 根packages和Gateway/TUI仓库bootstrap未强拆；框架没有上提根packages。

## 7. 后续契约登记三项修复（2026-10-17）

用户确认按显式manifest契约模块方案继续实施，范围仅是类型发现/校验与启用项注册，不是persona、Region/Hook贡献或通用UI状态功能。

### 交付

1. **只注册成功启用项**：能力注册循环过滤registrations，已禁用扩展的未知能力不会在后续阶段再次resolve并拖垮Host。
2. **工厂前校验**：JSON发现后先执行纯manifest的依赖/版本/Python门禁，再集中加载契约模块、检查所有事件/能力ID及提供版本；重新解析依赖完成级联，最后才import工厂。
3. **正式类型登记入口**：manifest v2增加可选`contracts`绝对模块名，必须显式导出`EVENTS`映射和`CAPABILITIES` tuple；Host/Assembly接受EventCatalog、CapabilityCatalog构造注入，并复制条目保留类型身份。

定义不激活能力提供者，不扩大manifest权限；原有11插件无需新增字段。相同模块每Host处理一次，同一ID/类型的共享导出可复用；
类型冲突或畸形bundle原子拒绝，不残留半份定义。未知/加载/冲突/版本错误有独立diagnostic，base或显式required仍阻断。

跨插件共享及跨目录reexport的契约模块、事件/能力/请求响应/port实际定义导入根保留到Host结束，
实现禁用不会使消费者引用的类型被卸载重建；两Host共享root直到最后一个退出，启动回滚与正常结束释放全部root。
Python import和父包初始化仍执行可信扩展代码，不是沙箱；契约模块及父包必须保持轻量。

### 验证

| 命令/覆盖 | 结果 |
|---|---|
| 初始三问题回归 | 7项按预期失败，再通过；后续共享定义、冲突、早拒绝和清理测试随实现验证 |
| 聚焦fresh review | 两项Important经复现RED→GREEN：契约import前依赖门禁、reexport真实定义root保留 |
| 契约加载/共享/清理/原Host聚焦矩阵 | 34 passed，2.32s |
| `make test-agent` | **819 passed，40.88s** |
| `make test-gateway` | **20 passed，5.17s**，包括实际TCP/WebSocket重启恢复；既有弃用警告1项 |
| `make typecheck-agent` | **223 source files无错误** |
| `make check-package-agent` | 最终源码wheel构建、资源、依赖安装、隔离导入/发现通过 |
| compileall、`git diff --check` | 通过 |

测试包含：自带非内置事件及新请求响应能力的两个外部插件，经真实串行worker完成事件→请求→响应；
未知provided/required/event不执行工厂；无效必需项失败；缺依赖插件不import契约或污染健康插件；
共享模块只读一次、所有冲突方拒绝、畸形bundle无部分注册、调用方目录不被污染、两Host类型身份和最终清理。

一次全量运行出现既有Skill通知测试`test_submit_returns_queued_then_background_job_succeeds_and_notifies`时序失败：
观察到终态早于异步Draft清理和通知；测试只sleep(0)一次。未修改该无关实现或测试、未skip；单测复跑通过，随后全量819项通过。

本轮没有再次调用真实远端模型、启动服务或触碰生产数据；前述单次授权模型smoke属于主重构验收。
TUI未因本轮纯Agent加载变更重复运行，其已知快照基线结果保留，不宣称本轮重新全绿。

## 8. 未作的承诺

未提交/合并/推送，未发布包或Artifact；未做生产数据迁移；未清理软删除资产；
未新增跨SQLite/JSON事务、远端RPC取消/重试、热插拔或第二套总线。
Mem0/OpenKB真实服务未启动，未进行真实服务写入；WebUI做类型检查和协议兼容检查，未声称全浏览器套件通过。
