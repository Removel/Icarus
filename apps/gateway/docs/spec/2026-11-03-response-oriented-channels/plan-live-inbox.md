# Gateway活动收件箱通知接线计划

日期：2026-11-05。**Goal:** 只向已有活动Argus任务转发“有补充可读”通知，不承载正文或新建执行。

**Architecture:** 新受保护POST /argus/notify消费Agent NotifyRequest，Bearer不变，调用Bridge.notify。Gateway不判断路由/窗口，不维护批次，不复制DTO，不生成平台回复。

**Spec:** [Agent计划](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan-live-inbox.md)与[Argus控制批次契约](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-live-inbox.md)。

- [ ] DTO只含wake_id/session_id/binding_id/watermark；跨映射409，未认证401，非法schema422；不能通过notify生成Task或注入正文。
- [ ] accepted/running活动任务通知通过，同水位合并；终态不复活。
- [ ] 本地真实Agent运行在安全工具点时通知；PromptUpdateEvent进入既有运行时上下文，再工具receive取得完整补充，最终只有一个Task/send。
- [ ] 原wake重试/缺正文/取消/commit失联套件保持；完整Gateway套件、compile/diff。

不新增公开RPC、不绕过Tool权限、不启动真实服务或发送平台测试消息。核心接线与验证已完成，结果见[执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-execution-live-inbox.md)；部署另授权，不代表运行进程已加载。
