# Agent 的 Argus 第二阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在隐藏后台会话中完成真实观测交接、主动读取/等待、逐项报告与可靠停止；不发 QQ 消息。

**Architecture:** application/SessionStore 拥有身份、scope/总名额与控制器；普通 Argus 插件拥有 prompt/
工具/outbox。工具存活采用通用 tracker，复用原输入队列和 AgentPlugin.drain，不另建 runtime。

**Tech Stack:** 既有 Python、SQLAlchemy SQLite、Pydantic、Event/capability、httpx、pytest。

**Spec:** [design](design.md)、[共同契约](../../../../../spec/2026-10-21-argus-agent-handoff.md)。

## 2026-10-22 用户简化接入补充（覆盖原MCP禁用步骤）

后台直接保留现有mcpServers，复用普通MCP list/search/execute；不新增动作策略、scope校验、
QQ发送模块或发送outbox。原Task1关于后台清空MCP的实现/测试已改为继承配置不修改原配置，
并增加真实SessionRuntime+离线backend的工具发现/执行测试。固定说明允许实际调用QQ工具回复，
最终正文不自动发布。以下其余第二阶段步骤保留，当前架构见arch，运行重启/真实发送由用户发起。

## Global Constraints

- 本轮已获当前feat/argus原地实施授权；不分支/commit/push、不改本机配置、不部署/迁移正式库。
- ReActAgent 无状态；PluginRuntime/Hook 不认识 Argus/QQ；不新增 TaskControl.wait_exited。
- 固定 session 指导初始化后不变；实际身份/原文只当前 User Prompt，不增加子插件。
- sessions 只加 scope_key/properties，报告/游标用 conversation_updates，无新业务表/owner 锁。
- 单 data_dir 单宿主；后台名额默认 8、1..100；总期限默认 900 秒，包含排队和 wait。
- 后台 mcpServers 固定为空，interactive 不变；不新增第三阶段 MCP profile/policy。
- 四停止屏障全部成立才 stopped；同步线程/async 取消未知保留占用，不因超时强制解锁。
- 报告永久拒绝可持久结案但不算处理；未知网络报告不丢弃；初始/途中未报告区别封口。
- 接口取共同 wire；页 20/50、文本 20000 字符、report 1..50/reason≤1000、body≤1 MiB。

## Review Focus

- input/deliver 尚未完成且 active_runs 为空时，不能空 drain 提前释放（Task 2）。
- 同步工具线程或不响应取消的 async 工具仍活跃、等待者被取消，不得假装停止（Task 2）。
- report 被永久拒绝/丢拒绝回执/重启，outbox 可结案且不伪造完成（Tasks 3/4）。
- 宿主已经配置 QQ MCP，后台仍零连接零可用 MCP 服务器（Task 1）。
- SQL/内存列表、未知 ID 预订阅和 title/status 等旁路不能绕过后台隔离（Tasks 1/5）。

---

## 文件与职责（以 apps/agent 为根）

| 动作 | 路径 | 单一职责 |
|---|---|---|
| 改 | src/agent_app/application/repositories/session_entities.py | 两列/索引映射 |
| 改 | src/agent_app/application/repositories/session_store.py | 迁移、reserve/查询/释放、raw 内部日志 |
| 新 | src/agent_app/argus_protocol.py | 纯HTTP边界类型/安全错误，宿主和插件单向共用 |
| 新 | src/agent_app/application/models/scoped_execution.py | typed 请求/ExecutionView/phase/errors |
| 新 | src/agent_app/application/controllers/scoped_execution.py | 占用/绑定/启动/停止/同步/对账 |
| 新 | src/agent_app/application/services/argus_handoff_client.py | 宿主 bind/finalize/报告对账 HTTP |
| 改 | src/agent_app/application/controllers/session_api.py | 可信预留任务入口、公开 guards/scoped facade |
| 改 | src/agent_app/application/agent_runtime.py | 保持公开导出兼容 |
| 改 | src/agent_app/application/services/session_registry.py | context/effective_config 装配 |
| 改 | src/agent_app/application/session_runtime.py | context/报告端口、可信 drain_execution |
| 改 | src/agent_app/application/services/update_pipeline.py | 可信 flush 屏障，不新增领域 update 分支 |
| 改 | src/icarus/core/session.py | 不可变通用 SessionContext |
| 新 | src/icarus/core/tools/execution_tracker.py | pending future 生命周期/等待 |
| 改 | src/icarus/core/tools/tool_executor.py | 实际 sync/async 调用跟踪和 snapshot 共享 |
| 改 | src/icarus/core/assembly.py、src/agent_app/assembly.py | tracker 显式注入，core 不 import application |
| 改 | src/agent_app/plugins/agent/factory.py | 会话 tracker 传给 assembly |
| 新 | src/agent_app/plugins/argus/{__init__.py,manifest.json,config.py,factory.py,plugin.py,prompt.py,tools.py,client.py,store.py} | 一级插件、最终工具结果确认及普通组件 |
| 改 | src/agent_app/config/config_model.py、settings.json.example | 可信后台 workspace/URL/期限/总额，secret 只环境 |
| 改 | scripts/test.sh、scripts/check-package.sh 和实际打包测试 | 聚焦参数/新 manifest 资产 |

测试留在 test/ 镜像源层；新增包含 __init__.py，不注册嵌套插件。fixtures 在各自测试目录，不建根共享包。

## Task 1: 持久身份、scope/总额与可信后台装配

**Files:** entities/store/models、session_api/registry/session_runtime/core/session、config、test.sh。
**Test:** test/agent_app/application/test_scoped_execution.py、test_session_store.py、test_agent_runtime.py、
test_session_runtime.py、test/test_test_script.py。

**Interfaces:**
- SessionContext(kind='interactive',scope:Mapping|None=None,metadata:Mapping=...)：深冻结、无 secret。
- store.reserve_scoped(request,*,identity,execution_id,task_id,max_active_executions)->ExecutionView，
  get_scoped(*,wake_id|scope)->ExecutionView|None、update_scoped(wake_id,changes)->ExecutionView、
  complete_scoped_release(wake_id)->ExecutionView，全部 async，变更使用同库事务。
- SessionApi.ensure_public_session(*,workspace_key:str,session_id:str)->None（async）；后台/未知拒绝。
  内部可信 get/load 不复用此 guard；公共参数没有 kind/scope/trusted。

- [ ] 写失败测试：旧 sessions 无列时显式迁移不丢历史；wake 唯一跨 workspace、相同指纹复用、
  异指纹 conflict；scope 同时竞争仅一成功；两个不同 scope 在 max=1 同时 reserve 仅一成功，
  capacity_busy 不留下新 session；pending_sync 算名额，释放后才腾位。
- [ ] 写失败测试：SQL+内存列表双隐藏，公开 get/submit/steer/cancel/unload/delete/history/title/
  discard_empty/task status 拒绝后台；guard 未知 workspace_key/session 拒绝；interactive 不回归。
  假全局 QQ MCP 配置下后台 effective_config.mcp_servers={}，connector 未调用，interactive 仍原配置。
  脚本单文件参数透传且保留非零退出码。
- [ ] RED：`apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/test_scoped_execution.py apps/agent/test/test_test_script.py -q`。
- [ ] 实现两列 ADD COLUMN、wake_id 表达式唯一索引、scope 部分唯一索引；SQLite BEGIN IMMEDIATE
  内统计总额并 reserve，不靠单次 status 或进程内计数。旧空 properties 为 interactive。
  装配 SessionContext 与后台空 MCP 的配置副本，绝不就地清全局配置影响普通会话。
- [ ] 实现公开 guard 与可信入口分离、脚本参数透传；同路径 GREEN，再 session store/runtime 回归。
  记录真实退出码，不自动提交。

## Task 2: 通用工具存活与完整停止屏障

**Files:** execution_tracker/tool_executor/core assembly、app assembly/agent factory、session_runtime/
update_pipeline、scoped controller 的停止部分。
**Test:** test/icarus/core/tools/test_execution_tracker.py、test_tools.py、test/agent_app/plugins/agent/test_plugin.py、
test/agent_app/application/test_scoped_execution.py、test_session_runtime.py。

**Interfaces:**
- ToolExecutionTracker.pending_count、track(future)->None、async drain()->None；线程安全注册/完成通知，
  drain 取消不影响被跟踪工作。ToolExecutor(...,tracker:ToolExecutionTracker|None=None)；snapshot 共享。
- AgentAssembly/AgentFactory 可选 tool_execution_tracker 参数；每 SessionRuntime 一个，不加业务字段。
- SessionRuntime.drain_execution()->None（可信应用内部 async），UpdatePipeline.flush()->None（async）。
  不新增 TaskControl/SessionHost 的 per-task wait_exited 契约。

- [ ] 写失败测试：冻结同步 tool 的线程，取消 run 后 AgentPlugin.drain 已返回但 tracker.pending_count=1，
  controller 仍 execution_stopped=false；释放线程后才 stopped。async tool 忽略取消超过 1 秒同理。
  snapshot 共享 pending；取消 tracker/drain 的等待者不取消工具；sync invoke 和 async ainvoke 都登记。
  原“取消后不等待同步迟到结果”测试继续成立，新增的是独立停止等待而非取消调用阻塞。
- [ ] 写失败测试：堵住初始 /deliver 或 required prompt 时 active_runs 为空仍不 stopped；prompt 失败/
  排队取消可以收束 queue；task terminal event 已发但 finally 未退仍不 stopped；应用更新未落盘不 stopped。
- [ ] RED：`apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/tools apps/agent/test/agent_app/plugins/agent/test_plugin.py apps/agent/test/agent_app/application/test_scoped_execution.py -q`。
- [ ] 实现实际 future 跟踪：to_thread 独立 task 注册后 shield 等待，取消 wrapper 不假移除；async grace
  超时保持 pending。同步执行登记真实完成 future；结果/异常实际完成才清除，迟到错误安全消费。
- [ ] 实现 input/full-graph drain → run drain → tracker drain → update flush/持久 evidence；监控等待
  不持 mutation_lock、shield 原执行。正常 drain 不先 quiesce；配置 deadline 仅发取消不强解锁。
  取消/失败的 required prompt 必须经既有任务失败通路让输入 queue 收束，不让 Hook 驱动下游。
- [ ] 同路径 GREEN，补 assembly 注入/普通 interactive 取消回归；未结束工具保持占用是正确结果，
  测试必须用显式 Event 最终释放 fixture，不能用长 sleep 或真的挂死测试。

## Task 3: 固定 wire、持久启动门控与宿主报告结案

**Files:** argus_protocol.py、models/scoped_execution、controllers/scoped_execution、services/argus_handoff_client、
store/session_api/agent_runtime；test/agent_app/application/fixtures/argus_v1.json。纯HTTP边界类型放应用级
argus_protocol.py，插件不能反向import application控制器/仓库；内部ExecutionView类型仍application拥有。
**Test:** test/agent_app/application/test_scoped_execution.py、test_argus_handoff_client.py、test_session_store.py。

**Interfaces:**
- AgentRuntime.scoped_submit(ScopedWakeRequest)->ExecutionView、scoped_status(*,wake_id:str|None=None,
  scope:Scope|None=None)->ExecutionView|None、scoped_cancel(wake_id,reason)->ExecutionView、
  scoped_release(wake_id)->ExecutionView，全部 async。
- `_submit_reserved_task(identity,task_id,prompt,*,execution_id)->InputAccepted` 可信内部方法。
- ArgusHandoffClient.bind(request,execution)->BindingView、send_report(binding,report)->ReportReceipt、
  finalize(binding,exit_kind,report_cursor)->FinalizeReceipt、aclose()（async）。重试原 report 亦用于对账；
  与插件 client 复用本应用 boundary schema/HTTP错误，不 import Argus。
- SessionStore.append_internal(identity,*,task_id,update_type,payload)->int 与
  read_internal(identity,*,update_types,after_sequence=0)->tuple[InternalRecord,...]（async）；原子共用
  conversation_updates.sequence，raw outbox 不走 RuntimeUpdate 反序列化，公共历史按原投影。

- [ ] 写失败契约 fixture：四 RPC 版本均必填、ExecutionView 每字段/null/释放蕴含关系；100 条 wake
  合法、超限拒绝；顺序/UUID/time 数值规范化同指纹；safe errors 与另两应用 fixture 相同。
- [ ] 写失败测试：reserve/bind/bound/launching 各阶段崩溃或回执丢失不启动第二 run；bind 未确认不入队；
  cancel 与 bind 回执竞争需对账；launching 重启只 interrupted 不重入队。单宿主重启停止证据不复跑模型。
- [ ] 写失败测试：raw outbox pending/synced/rejected 与公共历史隔离；永久拒绝可结案，accepted cursor
  单调且仅成功递增；丢拒绝回执重试原 ID；report_id_conflict/closed 未核清不解锁；零报告 finalize。
  finalize 回执丢失/清槽前崩溃可重试；卸载资源失败不重新占槽；status 按 scope 不返回已释放记录。
- [ ] RED：`apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/test_scoped_execution.py apps/agent/test/agent_app/application/test_argus_handoff_client.py -q`。
- [ ] 实现 reserve→bind→bound→launching→入队，接 Task2 停止屏障；deadline/cancel/reconcile 仅控制原身份。
  实现 raw outbox 串行 flush：accepted/rejected 都落盘，只有 accepted 增加报告水位，未知不跳过；
  finalize 确认后事务清槽/release_complete，离线恢复只记账。无 binding 的确定未启动可 start_failed
  本地收尾；未知 binding 不偷放槽。HTTP 401/协议错误告警不当永久业务拒绝。
- [ ] 同路径 GREEN，旧 SessionStore/SessionApi 历史及 submission_id 行为回归；不要把新的内部日志
  枚举塞入通用 RuntimeUpdateType，不通过删历史记录掩盖未知 update_type 错误。

## Task 4: 一级插件、分块四工具与送达/outbox

**Files:** argus 插件全部、session_runtime 配置设施、打包脚本/测试；本应用边界 schema 复用 Task3。
**Test:** test/agent_app/plugins/argus/{test_factory.py,test_plugin.py,test_prompt.py,test_tools.py,test_client.py,test_store.py}。

**Interfaces:**
- Argus plugin implements PromptContributor.static_prompt_sections，消费 UserInputEvent 及来源为Agent的
  既有AgentToolCompletedEvent，发布同task PromptContributionEvent；后台required，无context空贡献/无工具。
- backend read(id,*,text_offset=0,max_chars=20000)、history(*,cursor=None,limit=20)、
  wait(*,cursor=None,timeout_seconds=30,limit=20)、report(items)；身份由 backend 注入。
- store.exposed为task内存集合，backend按task/tool_call_id暂存pending_delivery；ToolExecutor经
  _supported_execution_arguments按签名传可信tool_call_id=ToolCall.id，不暴露在模型schema。
  只在最终预算后的AgentToolCompletedEvent.output与暂存规范JSON完整相同才确认exposed并写argus_wait_cursor。
  截断/预览/预算异常整页不确认；报告先append_internal(argus_report)，由同一发送端结案。

- [ ] 写失败测试：静态 System session 一份固定说明，多 task 不变；实际 scope/原文仅 User Prompt；
  同 task 重复 input 去重；bind 不算送达，预算后 /deliver 确认才贡献；受限初始全部为空则明确失败
  收束输入；无 scope 不暴露工具；宿主已有 QQ server 后台仍无连接/工具目标。
- [ ] 写失败测试：25k 字符 read 可两次拼回同版全文；offset=长度空块、越界拒绝；页游标不作正文
  offset；敏感/跨群拒绝；wait 首次不漏早到回答、返回成功才持久游标、HTTP失败不推进、取消中止。
  /deliver后暴露初始引用；工具HTTP成功但finalize_group截断/文件预览/整组预算异常时exposed和
  默认游标都不推进，完整AgentToolCompletedEvent才确认；确认事件滞后report可重试delivery_pending
  且不写outbox，wait最多重复。read/history/wait保留服务端role/origin，incidental不认领；
  跨boot新消息只context。
- [ ] 写失败测试：仅 exposed work 可报告；同 task 相同 pending 指纹复用 ID；partial accepted 不终止 run；
  读后撤回/结果冲突可持久 rejected 不堵槽；unknown 仍 pending；sync/async backend 一致。
- [ ] RED：`apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/argus -q`。
- [ ] 实现 manifest/factory/prompt/tools/client/store，复用 BaseTool/ToolExecutionResult 和 async 桥接；
  不另起 event loop、不用 Hook 触发贡献。client 共享边界/错误与报告序列化发送端，token 不进上下文。
  backend只暂存交付，插件在通用AgentToolCompletedEvent最终结果完整匹配后确认exposed/游标，
  失败不靠metadata猜引用已送达；不修改ReActAgent、不用Hook截断后补业务。
  Argus工具的invoke/ainvoke声明可选执行参数tool_call_id，ToolExecutor同步/异步按签名注入可信
  ToolCall.id，普通不支持者行为不变；模型参数无法覆盖。暂存未确认页在task结束后清理，默认
  游标不因此推进。
  required contributor失败走既有失败事件通路，固定System不追踪动态处理进度。
- [ ] GREEN 后 `make typecheck-agent`、`make check-package-agent`；更新新增 manifest/资产真实计数，
  不能关闭打包验证或提前启用本机插件设置。

## Task 5: 真实运行时离线集成与交付

**Files:** test/agent_app/application/test_argus_handoff_integration.py、settings.json.example、README 与 arch；
跨应用 TCP/PG 集成分别由 Gateway/Argus 计划拥有，不能 import 对方 ORM 绕过协议。

**Interfaces:** 消费前四任务和 Argus HTTP/Gateway RPC；产出完整停止和记账证据。

- [ ] 写 RED 集成：initial A 执行时来 B/补充，只报告 A 后 B 仍 new；报告 B 后不再投递；A 失败 B
  已报告不回滚；未报 initial unresolved；不同 scope 在有名额时并行，满额保留原 wake 公平重试。
  增加 Argus 重启旧 Agent 继续（原 boot 可报，新 boot 只 context）、永久拒绝丢回执/宿主重启、
  input 未开始/工具线程未退不释放、后台普通 API/订阅旁路拒绝。必须走生产插件/SessionRuntime。
- [ ] RED：`apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/test_argus_handoff_integration.py -q`。
- [ ] 仅补生产接线，模型用本地确定性 fixture，测试数据隔离，不调用真实模型/MCP/正式库。
- [ ] GREEN 后按应用运行 `make test-agent`、`make test-gateway`、`make test-argus`、
  `make typecheck-agent`、`make check-package-agent`，再 compile/diff，记录每项真实退出码及未执行项。
- [ ] 更新 arch 已实现部分和部署约束，不自动 Git 提交；正式启用/真实模型另获授权。

## 回滚

先确认输入/run/工具/持久停止屏障、outbox 持久，再回退；保留 sessions 新列/执行身份与
status/cancel/补同步路径，不清库、不自动降级、不把旧真实执行切 mock 视为完成。
