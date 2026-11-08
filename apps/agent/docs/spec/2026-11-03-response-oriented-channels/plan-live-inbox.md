# Agent运行中观察读取：第二阶段计划

日期：2026-11-05。用户授权恢复feat/argus已实现的可用通知和读取/等待，不是从零新造系统。第一阶段完整窗口/插件贡献不回滚，源码尚未部署。

**Goal:** 活动Argus任务可在同一执行中读取后来的相关窗口，不另起Agent。

**Architecture:** 原始wake不变，补充批次不可变；通知只是可读提示，正文由工具返回，最终实际工具消息全量可见后才ack。一次commit确认初始与已领取工作并集；未领取留下一轮。

**Spec:** [Argus批次契约与任务顺序](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-live-inbox.md)。Agent不导入Argus内部模型，Gateway只消费公共DTO。

## 改动

- `plugins/argus/inbox.py`：生成代码拥有的request_id、重试同一batch请求、读取/等待、投影视图、实际最终tool结果可见证明、delivered同步。拿到未展示批次不继续领取，保持原响应可重呈现；大正文支持max_chars连续分页，裁剪后可缩页重读，完整工具视图逐页覆盖后才ack，不能死循环返回同一超大输出。
- `tools.py`：新增argus_receive(timeout_seconds0..30,limit1..20)；argus_read和commit只解析初始/领取的ref，commit schema最多100；验证服务器仍要求精确集合。
- `notifications.py`：任务级signal，context.ready后available一次；通知无正文，重复水位抑制；30秒一次丢信号补查，无常驻旧任务轮询。
- `plugin.py`/manifest：声明prompt.update，注册活动binding listener；InputFinished/quiesce停止并join watcher。PromptUpdateEvent复用现有运行时上下文通道，不steer/cancel/扩大权限。
- `application/services/argus_bridge.py`：notify校验wake/session/binding和live任务，不写新任务，不复活旧执行。
- `argus_protocol.py`：NotifyRequest共享DTO；稳定系统短引导与按需receive/commit指南同步，不常驻完整协议教程。

## 测试与验证

- [ ] response丢失/再次receive使用同请求；完整tool输出相等才delivered，预览截断/旧工具输出不能伪造读取。
- [ ] 所有读都检查当前task取消/finished；跨绑定/wake通知拒绝，重复信号仅一个notice。
- [ ] 任务终止取消inflight available并join，无worker泄漏；无新模型Task。
- [ ] 读取两批后一次commit全并集，不包含history/只读正文；schema增加不是自由提交未知UUID。
- [ ] 真SessionHost+本地provider/stdio MCP＋临时PG：补充来时Agent正停在安全工具点，看到notice后receive，再1次send/commit。
- [ ] 最小集→make test-agent/make test-gateway/隔离Argus→typecheck/compile/diff；不调用真实平台/付费模型，不重启服务。

当前核心已实现并完成真实本地闭环，结果见[执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-execution-live-inbox.md)。尚未提交或部署。运行中新补充不是改写初始window_items，不以通知当工作确认，也不自动撤回之前外发。
