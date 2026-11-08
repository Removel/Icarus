# 第二阶段：恢复运行中收件箱

日期：2026-11-05。用户明确要求继续恢复 feat/argus 已实现过的运行中关联/通知/read-wait/report机制。本稿先于产品改动；阶段一工作区保留，不自动commit、push、重启服务或真实外发。

**Goal:** 当前Agent处理同一Channel时，新窗口的相关工作可以被通知、按需领取和读取，在同一次执行中处理；不另起Agent，不丢消息或重复处理。

**Architecture:** 保留旧探索的“无正文可用通知＋工具读取＋终态停止”；适配当前单次原子commit。原wake和初始冻结items永不修改；新增不可变的补充批次，最终提交覆盖初始工作与已领取批次，未领取的保持pending。借助既有Event/PromptUpdateEvent，不修改ReActAgent或Hook。

**Spec:** feat/argus `observation_scheduler._commit_group`、`notifications.watch_availability`、`handoff_service.wait/report/finalize`；当前[窗口计划](plan-message-windows.md)、[Agent插件交付计划](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan-observation-delivery.md)。

## 全局约束

- 只领取当前Channel规则已接受、来源仍获授权且完整封存的窗口工作；不是将所有新来信无条件塞进当前任务。
- 同Source不同话题仍可走其他Channel；不同实例不共享收件箱。
- 通知仅表示可读，不携带正文、不作确认、不重写系统提示、不取消或steer当前任务。
- 正文按完整窗口保留，不按“需不需要单独回应”删句。history与当前非工作正文不可误确认。
- 每补充批次最多20工作项，总计最多100工作项（初始20＋最多4批）；一次批次正文有界128KiB，溢出不截断当前窗口，保持pending。提交schema提升到100，但仍必须严格等于本轮真正领取范围。
- 任务结束/取消/commit成功即停止监听与等待；结果未知不启动新Task恢复，已发送动作不重放。

## 拟定接口与职责

### Argus Store/控制协议

1. `inbox_state(binding_id)`：返回pending_count与稳定watermark，仅统计当前轮次尚未领取的合格工作；不转移所有权。
2. 新控制kind `agent.batch_requested`，payload只含limit；沿用agent/events持久意图和固定回执。一次请求ID只领取一次，响应丢失重试同ID返回同一批。
3. batch保存独立batch_id、observation_ids、完整window正文与原响应。初始round.observation_ids/wake_payload不变。
4. `agent.delivered`允许确认初始或已领取批次的完整工作；commit确认集合是初始items与所有batch工作并集。claim与commit共用Channel行锁，finalize竞态只允许先领取后确认或先关闭后拒绝领取。
5. 未领取补充留下一轮；已领取但未完整可见不能提交，无权清除。终止后保持当前未决安全边界，不自动重新执行。

### Agent工具

新增`argus_receive(timeout_seconds=0, limit=20)`：0立即读取，最长30秒等补充；复用旧read/wait语义合成一个小接口，不新造CLI。请求身份/批次ID全部由代码生成，不要求模型搬运。

工具先检查之前领取批次的最终模型工具消息是否完整包含原始投影视图，才发送delivered；剪裁/只看head-tail不算完整。长正文按完整JSON语义视图做连续页，`max_chars`默认8192、范围256..16384，偏移由代码管理；被工具预算裁剪时缩小页长重读当前位置。只在最终工具消息与已发页完整相等时推进连续偏移，全部页可见后才ack整个批次。如果上一批未完整展示，再次receive继续/重呈现同批，不领取新批。正式commit前同步所有可见证明，必须对每个工作ref给handled/no_action。返回当前work_refs并集，模型无需记技术批次ID。

`argus_read(ref)`扩展到已经领取的消息，用于重新读取；history仍不可确认。现有指南更新说明收到notice时可在合适步骤receive，不承诺模型必读，也不能把已经发出的回答自动撤回。

### Agent插件与Gateway通知

- 受保护`POST /argus/notify`接收wake_id/session_id/binding_id/watermark，复用Agent公共DTO与Bearer；不提供新建任务或正文注入能力。
- ArgusBridge验证真实绑定、任务仍存活，再唤醒对应ArgusPlugin任务信号；已结束/未知任务返回未通知，不复活。
- 插件在context.ready后查询available；有pending则一次PromptUpdateEvent提示。重复watermark只通知一次，信号合并；丢信号补查最多30秒一次，只在存活任务内进行。读取为空清除notice门禁，下一波可再次提示。
- InputFinishedEvent/quiesce取消并join后台worker；所有状态以task_id隔离，不让旧Session通知当前任务。
- Argus Bridge沿既有恢复扫描仅在accepted/running任务且watermark变化时notify；不每秒刷重复notify，不因通知失败重发业务任务。

## 任务及测试

### Task1：原子补充批次

**Files:** Argus infrastructure/store.py、contracts/commit.py、protocols/agent_protocol/plugin.py、application.py；测试新增 infrastructure/test_live_inbox.py。

- [ ] 先RED：领取不改初始wake；同请求重试不重复领取；原子commit覆盖并集；未领取保持pending；未delivered拒绝；关闭后claim拒绝；不同Source/Channel不串；预算/容量不丢项。
- [ ] 实现并跑最小PG测试，再保留现有commit/窗口回归。

### Task2：读取/等待与实际可见证明

**Files:** Agent plugins/argus/tools.py、拟增inbox.py、guide/prompts/manifest；测试新增 test_live_inbox.py。

- [ ] RED：receive原始批次响应失联重试同ID；actual tool message全量相等才ack；裁剪不ack；不能提交未领取ref；读/取消/已完成拒绝；等待30秒上限，无空转。
- [ ] 新批次只分配持久短ref，保留目标/完整正文；不改wake fingerprint；sync/async一致。

### Task3：活动任务通知

**Files:** Agent plugins/argus/plugin.py、拟增notifications.py、application/services/argus_bridge.py、argus_protocol.py；Gateway src/protocol/argus.py；Argus Bridge client/plugin。

- [ ] RED：A补充只通知A；一次watermark一次通知；正文不出现在notice；未context.ready不抢跑；已终止不通知；InputFinished取消查询；无第二次runtime.submit。
- [ ] 复用现有PromptUpdateEvent→TaskContextInputEvent，拒绝任务控制方向/身份改写。

### Task4：真实本地E2E与完整回归

**Files:** 新增Argus/Gateway live-inbox E2E。使用真实临时PG、SessionHost和本地模型/MCP fixture，不外发真实平台、不调用付费模型。

- [ ] 初始窗口启动Agent→在工具安全点阻塞→新相关窗口进入→收到可读notice→receive→看到补充再回复→原子commit→只有1Task/1send；下一轮不再处理已领取工作。
- [ ] 在commit竞态中未领取的补充保留，已关闭通知不能唤醒旧Task。
- [ ] 最小文件→完整Argus/Agent/Gateway→typecheck/compile/diff；记录实际失败、旧套件不放宽断言。

## 执行与完成边界

核心实现和真实本地PG/Agent/MCP闭环已写入工作区，最终Agent1383/Gateway61/Argus366通过，types288清洁；结果与限制见[执行记录](plan-execution-live-inbox.md)。阶段一未提交代码仍保留。本阶段未自动部署加载，不复活未知旧任务。部署加载、真实QQ体验与模型策略质量另授权。不延伸到旧unknown永久轮询或生产旧任务修复。
