# Argus 第二阶段交接实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 经真实 Gateway 交接 Agent，提供安全同 scope 读取/等待/逐项报告，可靠核对停止和释放。

**Architecture:** 沿用现有分层、规则和采集；HandoffRepository/Service 拥有绑定、出口、报告/封口，
GatewayAgentClient 拥有传输。Agent 拥有真实执行生命周期，Argus 不新建会话 runtime 或 QQ 动作端。

**Tech Stack:** 既有 FastAPI、SQLAlchemy/asyncpg、PostgreSQL17、httpx、WebSocket JSON-RPC、pytest。

**Spec:** [design](design.md)、[共同契约](../../../../../spec/2026-10-21-argus-agent-handoff.md)。

## Global Constraints

- 本轮已获当前feat/argus原地实施授权；不操作正式PG/模型/QQ MCP，不分支/commit/push，不自动部署。
- schema v2 只加 handoff/exposed_wake_id 两列/必要索引；保旧数据/mock，不加业务表/seq。
- observed_at 在 scope 锁内严格递增，平台 sent_at 不改；不能只串行 commit 便声称不漏读。
- binding.origin_boot 为工作代际；新 boot 消息对旧 binding 仅 context，不自动恢复旧待办。
- 敏感词/权限约束所有内容出口，完整文本检测先于截断；受限历史/冻结 payload 不旁路。
- 读到 incidental 不写 wake_id/不改 new，report 接受才结清；未报 initial 才 unresolved。
- report 接受/确定拒绝都持久幂等；网络未知不假拒绝。resolved 仅封口，零接受报告也可 resolved。
- 页默认20/最大50、文本20000字符、read 分块、report1..50/reason≤1000，HTTP body≤1 MiB。
- wake1..100/excerpt总计100000字符；控制四方法版本均1；后台workspace仅Agent可信配置。
- 本机公开 listener 保持 loopback；远端服务 listener 只挂内部认证路由，同进程只启动一次服务。

## Review Focus

- 锁前时间反序、等时 UUID 反序、时钟回拨不能导致 wait 漏项（Task 1）。
- 敏感尾部/ignored历史/旧冻结payload/元数据不可绕过出口，过滤页可推进但预算未返项不可跳过（Task 2）。
- 旧绑定跨重启、读后撤回、永久拒绝丢回执不能误清账或永久堵 outbox（Tasks 1/2/4）。
- submit 未知、scope/capacity busy、stopped 但未释放不能换ID或提前新开（Task 3）。
- 双 listener 不重复 boot/scheduler，远程内部服务不能暴露无认证原文 GET（Task 4）。

---

## 文件与职责（以 apps/argus 为根）

| 动作 | 路径 | 职责 |
|---|---|---|
| 改 | src/core/config.py | execution.mode、可信控制URL/服务listener、secret与硬预算校验 |
| 改 | src/core/database.py、db/schema.sql | schema2和显式迁移入口 |
| 新 | db/migrations/002_agent_handoff.sql | 加两列/索引、旧mock标记，保数据 |
| 改 | src/common/models/{observation,wake}.py | 两列映射 |
| 改 | src/common/schemas/wake.py | v1 scope身份/status与预算，删除workspace_path |
| 新 | src/common/schemas/handoff.py | binding/item/page/read/report/finalize类型 |
| 改 | src/common/repositories/observation_repository.py | scope锁/严格水位/真实活跃约束 |
| 新 | src/common/repositories/handoff_repository.py | binding/grant/report_log/封口原子事务 |
| 新 | src/common/services/observation_access_policy.py | 复用敏感检测、所有出站策略 |
| 改 | src/common/services/judgement_service.py | 复用出口策略，保原判断顺序 |
| 新 | src/common/services/handoff_service.py、observation_notifier.py | 有界页/分块/可取消longpoll |
| 新 | src/common/api/handoff.py | Bearer/版本/body限制/安全错误 |
| 改 | src/common/api/router.py | 本机/内部路由集合分开 |
| 新 | src/common/clients/gateway_agent_client.py | submit/status/cancel/release typed adapter |
| 改 | src/common/clients/gateway_client.py | 保留安全error.data、RPC大小限制 |
| 改 | src/common/clients/mock_agent_client.py | 模拟接口兼容，保持simulated=true |
| 改 | src/common/services/wake_service.py、src/common/tasks/observation_scheduler.py | 真实对账/busy退避/释放门禁 |
| 改 | src/surfaces/qq/services/observation_service.py | commit后notify |
| 改 | src/main.py | 单服务lifespan、双listener路由壳、显式mode装配 |
| 改 | settings.json.example、README.md | 默认mock、迁移/专用URL/部署边界 |

测试 test/core、test/common/{repositories,services,clients,api,tasks}、test/app 镜像源层，新增包有
__init__.py。生产不 import Agent/Gateway 源码。跨应用 fixtures 在各应用测试目录各存等价 JSON。

## Task 1: schema2、单调水位和可结案报告事务

**Files:** db/core database/models、schemas/wake/handoff、两repository。
**Test:** test/core/test_handoff_migration.py、test/common/repositories/test_handoff_repository.py、
test_observation_repository.py。

**Interfaces:**
- HandoffRepository(database,*,clock)：bind(wake_id,binding)->BindingView、
  mark_exposed(binding_id,item_ids)->None、report(binding_id,report)->ReportReceipt、
  finalize(binding_id,exit_evidence)->FinalizeReceipt、get_report(binding_id,report_id)->ReportReceipt|None。
  全async；报告拒绝用 typed receipt，HTTP转换归API，不从repository直接发HTTP异常。
- observed_at 锁内 `max(clock_now, scope_max+1us)`；scope advisory锁→message锁→wake/观测锁的
  全部写入保持一致锁顺序。exposed_wake_id 存 wake UUID，不是 execution UUID。
- handoff.finalize.initial_results 的 handled/no_action/unresolved 与 observation status分离。

- [ ] 写失败迁移测试：v1带旧mock历史，v2行数/content/status/outcome全保留；空库v2与迁移v2约束
  一致；未知版本拒绝，迁移版本锁并发只有一次。mock旧boot accepted不变成真实永久占用。
- [ ] 写失败水位测试：锁前timestamp反序、冻结相同clock且UUID反序、时钟回拨/并发commit、duplicate
  不进水位，wait按游标必能读到后提交行；sent_at不改、不同scope不串成全局锁。
- [ ] 写失败报告事务测试：grant后incidental仍new/无归属；report才关联processed；partial不处理其他项；
  mixed invalid整批拒绝；same report同body重放、异body conflict；新ID同status幂等、异status拒绝。
  accepted/rejected回执丢失后重放不变，rejected不增加cursor；同wake并发报告不覆盖JSON。
- [ ] 写失败封口测试：正常/失败/取消/零报告都可resolved；未报initial failed+unresolved并保归属，
  incidental仅读仍new/无归属；旧版报告不结清新版；finalize同证据幂等、异证据冲突，已接受不回滚。
- [ ] RED：`apps/argus/scripts/test.sh apps/argus/test/core/test_handoff_migration.py apps/argus/test/common/repositories/test_handoff_repository.py -q`。
- [ ] 实现事务迁移/索引、水位、binding/report_log与封口；先重放旧回执再重验状态，拒绝无观测更新但
  有持久receipt。真实跨boot活跃屏障在status确认release_complete后才清，不能仅按resolved索引。
- [ ] 同路径GREEN，再repository目录回归。所有PG fixture隔离数据，生产迁移另获授权；无自动提交。

## Task 2: 出口策略、跨boot角色、分块读取/等待内部HTTP

**Files:** access_policy/judgement、handoff_service/notifier/api/router、QQ observation_service；
test/common/fixtures/argus_v1.json。
**Test:** test/common/services/test_observation_access_policy.py、test_handoff_service.py、
test_judgement_service.py、test/common/api/test_handoff_api.py。

**Interfaces:**
- ObservationAccessPolicy(sensitive_keywords,permission_for)：allows(item)->bool，使用完整规范正文与
  输出元数据，不输出命中词；JudgementService共享检测，不改变规则/预算角色。
- repository.read(binding_id,item_id)->版本行；history/new_items(binding_id,*,cursor,limit,scan_limit=500)
  ->候选页，service先过滤/预算再mark_exposed实际返回项。
- HandoffService.bind/deliver/read/history/wait/report/finalize 对接根HTTP schema；
  read(...,text_offset=0,max_chars=20000)->ReadResult；wait(...,timeout_seconds=30)->ObservationPage。
- cursor编码含scope、direction和(observed_at,id)水位，history/wait分开；不能用正文offset当消息游标。

- [ ] 写失败JSON fixture测试：HTTP每请求/返回版本、ReportReceipt accepted/rejected、ReadResult
  next_text_offset、页结构/role/origin、safe errors与根一致；非QQ来源/越群ID/游标拒绝。
- [ ] 写失败出口测试：敏感只在第25000字/元数据/ignored历史/旧payload仍拒绝；无权限scope不出正文；
  截断前检测；受限bind只安全标识，deliver原子拒绝；旧配置变化后submit未知只按ID对账不再发原文。
  页跳过仅受限行可推进，扫描500仍未到头则has_more，不用无限扫描；预算未返可见项不跳过。
- [ ] 写失败读取测试：25000字分两块完整重组，offset=len为空、越界422、max_chars边界；历史向旧/
  wait向新、首次wait从initial_cursor不漏早到回答、无新行超时原游标、过滤空页可推进。
  DB查/注册通知之间arrival不丢、commit后notify、取消释放waiter。
- [ ] 写失败角色测试：Argus重启旧binding继续，原boot合法initial/incidental仍work；新boot消息只
  context不可report但仍待后续调度；新binding不重做旧boot。编辑/撤回/策略变化后report明确拒绝且
  同ID重试回执稳定，context/grant不伪造处理。
- [ ] RED：`apps/argus/scripts/test.sh apps/argus/test/common/api/test_handoff_api.py apps/argus/test/common/services/test_handoff_service.py apps/argus/test/common/services/test_observation_access_policy.py -q`。
- [ ] 实现出口检测/角色、按字符正文续读和bounded页；按实际返回ID登记grant，/deliver仅初始集合；
  longpoll查库→登记→重查，通知非事实源；Bearer/版本/body≤1MiB/安全错误，HTTP409拒绝receipt
  可区分unknown，认证/存储异常不假rejected。初始空安全贡献明确可失败，不发送受限原文。
- [ ] 同路径GREEN，再api/services目录及原judgement测试；不调用模型/真实网络。

## Task 3: 真实adapter、busy退避与释放对账

**Files:** gateway_agent_client/gateway_client、schemas/wake、wake_service/scheduler、config/main装配。
**Test:** test/common/clients/test_gateway_agent_client.py、test_gateway_client.py、
test/common/services/test_real_wake_service.py、test/common/tasks/test_scope_reconcile.py。

**Interfaces:**
- GatewayAgentClient.submit(WakePayload)->WakeReceipt、status(*,wake_id:UUID|None=None,
  scope:Scope|None=None)->ExecutionStatus|None、cancel(wake_id,reason)->ExecutionStatus、
  release(wake_id)->ExecutionStatus、aclose()，全async。mock保持模拟模式，不接真实API。
- IcarusClient GatewayBusinessError新增安全data/retryable/owner供adapter，不记录原始params/token。
- WakeService.advance(wake_id)每次仅有界RPC推进；真实reconcile不占长运行worker。

- [ ] 写失败测试：四方法带版本/token，专用/argus/rpc URL；wake硬预算与配置100条/100000字符兼容；
  found=false映None、unknown/unavailable保持错误；scope/capacity_busy保留waiting原wake退避且不重调Jev。
- [ ] 写失败对账测试：丢submit回执同ID/指纹重试；结果未知不failed/解锁；旧boot仍执行不新开；
  cancel accepted仍活跃；stopped/finalized但release_complete=false仍阻塞；本地resolved不代替远端释放。
  未发送即策略变化failed可local_terminal继续下一wake；发送前持久标记后崩溃仍按未知对账；
  明确未reserve且无此前未知才允许本地终结，capacity_busy正常继续waiting而不误failed。
  报告部分接受、拒绝结案/零报告、未报B重判、已报不回滚；shutdown未知持久reconciling。
- [ ] RED：`apps/argus/scripts/test.sh apps/argus/test/common/clients/test_gateway_agent_client.py apps/argus/test/common/services/test_real_wake_service.py apps/argus/test/common/tasks/test_scope_reconcile.py -q`。
- [ ] 实现严格typed adapter/error.data，真实mode缺配置失败不退mock；区分传输timeout与900秒执行期限，
  旧60秒仅mock。有限提交重试耗尽仍对账，不声称远端未收；满额安全拒绝继续公平退避，不换wake。
  未知发送先持久submission_may_have_run；本地从未发送/明确未reserve且无此前未知可标local_terminal
  清本地屏障，否则只在远端release_complete后清；其他scope轮转。
- [ ] 同路径GREEN，全 `make test-argus`；更新因契约变化的mock测试，不删除失败测试收尾。

## Task 4: 专用listener、三应用离线贯通与交付

**Files:** main/config/router、test/app/test_service_listener.py、test_real_handoff.py、settings.example/
README和arch；Gateway/Agent实现归各自计划，不在Argus生产import对方源码。

**Interfaces:** 可选 service_host/service_port，与本机host/port不同；service app只内部prefix+health。
共享一次初始化的app services，双uvicorn server同进程协作启动/关闭，不重复lifespan/scheduler。

- [ ] 写失败listener测试：远端app内部Bearer正常、公开GET/OneBot不存在；本机GET可用；无token拒绝
  开服务listener；两路同时运行boot/scheduler计数=1，第二端口启动失败清理自有资源，无重复DB关闭。
  listener禁止公网/不可信目标作为部署前提，不信forwarded header绕过路由隔离。
- [ ] 写RED集成：真实Gateway/AgentRuntime/Argus、隔离PG/SQLite/本地模型：A处理中B/补充→wait→
  只报A则B新；报B则不再投；A失败B已报保留，未报initial unresolved；Argus重启旧执行可报原boot，
  新boot只context；永久拒绝/丢回执/宿主重启outbox结案；取消同步线程未退不另开；满额第九scope
  保留wake公平重试；敏感消息不出站、长文续读完整、远程无普通RPC/原文GET入口。
- [ ] RED：`apps/argus/scripts/test.sh apps/argus/test/app/test_service_listener.py apps/argus/test/app/test_real_handoff.py -q`。
- [ ] 仅补接线，双listener同进程单服务；fixture直接向隔离Argus写入测试消息，不接真实平台/MCP。
- [ ] GREEN后按应用运行 `make test-argus`、`make test-gateway`、`make test-agent`，再
  `apps/argus/.venv/bin/python -m compileall -q apps/argus/src apps/argus/test`、`git diff --check`；
  记录真实退出码/已知失败身份/未执行项，更新arch；不改正式服务或自动提交。

## 回滚

先取消并确认完整停止屏障、outbox持久；保留schema2/身份/回执，不自动降级、不清数据，不切mock
冒充旧真实执行结束。正式数据库迁移与远程服务启用另获授权，本计划中的命令只用于隔离开发验证。
