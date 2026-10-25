# Gateway 的 Argus 第二阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供受认证的四方法专用服务入口，复用 Agent 持久执行，不开放普通 RPC 到远端。

**Architecture:** Gateway typed校验/认证/错误转换→AgentRuntime公开scoped facade。普通/rpc保持
loopback；/argus/rpc专用allowlist可由同进程第二listener提供，单runtime/hub/pump，无业务库。

**Tech Stack:** 既有FastAPI WebSocket、Pydantic、uvicorn、JSON-RPC、pytest；无新平台依赖。

**Spec:** [design](design.md)、[共同契约](../../../../../spec/2026-10-21-argus-agent-handoff.md)。

## Global Constraints

- 本轮已获当前feat/argus原地实施授权；不真实模型/MCP/正式库/部署，不自动Git变更。
- 不自存scope/幂等/任务状态，不调用_store/_entries，不新建第二Agent进程或runtime。
- 四方法均必填protocol_version=1/caller_token，wire/error/预算与共同fixture一致。
- wake1..100项、excerpt总100000字符、控制帧1MiB；不是读取页50项/20000字符预算。
- 普通/rpc不注册argus.*，专用/argus/rpc只四方法；普通listener只loopback，不改旧用户认证契约。
- 远端只可信LAN/VPN专用listener+ACL，无TLS/公网；缺token拒绝启用，不信forwarded header。
- cancel accepted/terminal event/stopped都不能代替release_complete，未知不冒充not_found。
- 后台隔离由Agent公开guard负责，pump不识别领域Event，保持TUI/WebUI普通行为。

## Review Focus

- SecretStr外的结构/未知字段验证、异常日志也不得回显合成token（Task 1）。
- 四方法版本字段/ExecutionView释放信号/not_found形状/硬预算各应用不能各猜一套（Task 1）。
- 远端不带token试普通session.create/submit不能绕过专用入口（Task 3）。
- 已知后台ID、未知ID预订阅、title/status/discard_empty旁路不能读写隐藏会话（Tasks 2/4）。
- 双listener重复runtime启动/第二端口失败/WS断线不得重复执行或假装任务停了（Tasks 3/4）。

---

## 文件与职责（以apps/gateway为根）

| 动作 | 文件 | 职责 |
|---|---|---|
| 新 | src/protocol/argus_models.py | 严格Scope/Wake/四params/result/ExecutionView wire |
| 新 | src/protocol/argus_methods.py | token/auth/四方法allowlist、facade和安全错误 |
| 改 | src/protocol/methods.py | 普通session订阅guard；不混入argus dispatch |
| 改 | src/protocol/models.py | 必要安全strict配置，不改变旧wire输出 |
| 改 | src/app.py | 普通/专用路由壳，共享单runtime/hub/pump/lifespan |
| 改 | src/main.py | loopback普通listener、可选service_host/port双server |
| 改 | src/connection.py | 注入通用dispatcher、解析前body限制，不自存业务状态 |
| 改 | scripts/test.sh | 聚焦pytest参数透传，无参全套+compile/真实退出码 |
| 新 | test/fixtures/argus_v1.json | 与三方等价的规范/错误/边界fixture |
| 新 | test/test_argus_methods.py | auth/shape/wire/facade/errors |
| 新 | test/test_service_listener.py | 专用路由、双listener生命周期 |
| 新 | test/test_argus_handoff.py | 真WS/runtime/Argus离线集成 |
| 新 | test/test_test_script.py | 聚焦参数/失败退出码 |
| 改 | test/test_app.py、test/test_methods.py、README.md | 普通兼容/隔离/部署说明 |

消耗Agent公开async接口：scoped_submit(request)->ExecutionView、
scoped_status(*,wake_id|scope)->ExecutionView|None、scoped_cancel(wake_id,reason)->ExecutionView、
scoped_release(wake_id)->ExecutionView；ensure_public_session(*,workspace_key,session_id)->None。
每个调用对象只使用自己的边界类型转换，Gateway不import Agent插件或Argus源码。

## Task 1: 固定JSON契约、认证、错误与聚焦脚本

**Files:** argus_models/argus_methods骨架、models、fixture、scripts/test.sh。
**Test:** test/test_argus_methods.py、test/test_test_script.py。

**Interfaces:** ArgusMethods(runtime,*,token_loader:Callable[[],str|None])，async
`dispatch(method:str,params:dict,subscriptions:set)->dict`，与连接既有三参dispatch兼容，
subscriptions在专用入口不参与业务；内部wake/status/cancel/release返回根result对象。

- [ ] 写失败fixture/认证测试：四方法均缺版本/字符串版本拒绝；token缺/错/空配置拒绝且所有异常
  不包含合成secret。严格UUID/账号/finite time、不接受bool为整数、source/chat/locator不一致拒绝。
  items100条/100000字符合法、101条/超总字数拒绝；未知字段拒绝；规范化fixture三边一致。
- [ ] 写失败wire测试：status未找到固定found=false/execution=null；正常ExecutionView全字段及
  null；stopped但pending_sync/release_complete=false不被转换成空闲；safe data.code/retryable/owner。
  脚本单文件参数和非零退出码验证。
- [ ] RED：`apps/gateway/.venv/bin/python -m pytest apps/gateway/test/test_argus_methods.py apps/gateway/test/test_test_script.py -q`。
- [ ] 实现StrictModel/hide_input_in_errors/SecretStr和constant-time compare；认证后去token再构造
  ScopedWakeRequest。error=-32000/data={code,retryable,owner?}，shape=-32602，未知内部=-32603；
  不把任意KeyError当未找到。版本/预算固定取共同契约，不另建Gateway业务状态机。
- [ ] 实现脚本透传，GREEN同路径再经脚本执行；无参全套+compile行为保留，记录结果不提交。

## Task 2: 四facade接线与普通公开guard

**Files:** argus_methods、methods/app、必要models。
**Test:** test/test_argus_methods.py、test/test_methods.py、test/test_app.py。

**Interfaces:** 依赖Agent计划Tasks1/3公开facade，Gateway只typed转换和调用；
普通subscribe先 `await runtime.ensure_public_session(workspace_key=...,session_id=...)` 后加tuple。

- [ ] 写失败测试：同wake同body同execution，异body conflict；scope_busy/capacity_busy安全错误；
  unknown/unavailable不是not_found；cancel accepted execution_stopped=false；release busy；
  facade请求不含token/path。fake只验证适配，不能作为二阶段端到端证据。
- [ ] 写失败公开隔离：list SQL+内存结果来自Agent过滤；已知后台ID的get/submit/history/title/
  cancel/unload/delete/task.get_status/discard_empty拒绝；subscribe未知ID拒绝，不能先注册后等
  后台创建；interactive原有参数/行为不变。设备内部update流不无条件外放给普通连接。
- [ ] RED：`apps/gateway/scripts/test.sh apps/gateway/test/test_argus_methods.py apps/gateway/test/test_methods.py -q`。
- [ ] 实现四handler真实调用facade、不内存缓存；所有公开路径用Agent guard，pump只按tuple扇出。
  普通/rpc不注册四方法，token要求不影响旧session调用。release facade直接返回View，避免额外
  status读取拼状态。所有known error精确映射，内部存储错误不透连接串。
- [ ] GREEN，旧app/methods一起跑；确认不新增role/device routing或私有store访问。

## Task 3: 专用路径与双listener单宿主生命周期

**Files:** app/main/connection、test/test_service_listener.py、README。
**Interfaces:** create_app保留原嵌入测试用法；可新增create_argus_app(shared_services,token_loader)
作路由壳，main拥有唯一lifespan+两个uvicorn.Server，服务app不再start/stop runtime。
CLI新增 --service-host/--service-port 成对提供；普通--host只接受loopback，远端URL为/argus/rpc。

- [ ] 写失败测试：本机/rpc旧方法可用、argus方法不存在；本机/argus/rpc仅四方法且要token；
  远端app只有/argus/rpc+health、/rpc不存在，即使无token试session.create/submit也不能执行。
  缺token或只给一个service参数拒绝；请求>1MiB解析前拒绝，不能先Pydantic全量处理。
- [ ] 写失败生命周期测试：两listener共用runtime/hub/pump，各只start/stop一次；第二端口失败
  能关闭第一listener和已拥有runtime；任一WS断线不取消业务执行；关闭停止接收后再清资源。
  forwarded header不能暴露本机路由；原--host非loopback明确报迁移说明，不自动开公网。
- [ ] RED：`apps/gateway/scripts/test.sh apps/gateway/test/test_service_listener.py -q`。
- [ ] 实现dispatcher注入（保留既有接口），专用allowlist拒绝普通方法；main同进程双server协作
  启停，单一资源所有者。禁止为隔离另起Agent进程/runtime；不新建settings框架。
- [ ] GREEN，再旧CLI/app测试；README写loopback兼容、服务token/URL/ACL/无TLS和执行状态含义。
  不提供真实secret，不启动任何真实LAN服务。

## Task 4: 真WS离线集成与故障验收

**Files:** test/test_argus_handoff.py、已有runtime_e2e fixtures、arch。
**Interfaces:** 依赖Argus内部API/真实adapter、Agent插件/停止屏障/outbox全部完成。

- [ ] 写RED集成：真实TCP/双入口Gateway+AgentRuntime连接隔离Argus/PG、SQLite，本地确定性模型；
  A执行时B/补充出现→wait读到，未报B仍new、报B不重投；initial未报unresolved；partial report不
  提前释放；同步线程/async取消未知不新开；满额capacity_busy保留wake，其他scope有名额可推进。
  丢submit响应同wake重连；Argus重启旧work可报、新boot只context；永久拒绝丢回执/host重启
  outbox可结案不重跑；敏感尾部不出站/长文续读；WS预订阅/旁路隔离与远端普通RPC不可达。
- [ ] RED：`apps/gateway/scripts/test.sh apps/gateway/test/test_argus_handoff.py -q`，不以fake accepted
  或mock固定resolved代替真实插件/SessionRuntime路径。
- [ ] 只补接线，不接QQ MCP、不读取用户秘密、不调用真实模型/正式库。
- [ ] GREEN后 `make test-gateway`、`make test-agent`、`make test-argus` 按私有环境运行；再
  `apps/gateway/.venv/bin/python -m compileall -q apps/gateway/src apps/gateway/test`、`git diff --check`。
  记录真实退出码/已知失败身份/未运行项，更新arch已实现行为，不自动Git提交/部署。

## 回滚

不靠断WS声称任务停；保留真实任务status/cancel/记账通路直到完整停止与outbox持久。
不清Agent/Argus库、不删除幂等记录、不切mock伪装完成；正式启用/迁移/真实模型需另外授权。
