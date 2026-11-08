# 运行中收件箱恢复：第二阶段执行记录

日期：2026-11-05。用户明确要求第二阶段也恢复，先补文档后继续开发。本轮没有新建分支、commit/push、重启生产服务、真实平台发送或付费模型调用；第一阶段未提交工作区一并保留。

## 先写的文档

Argus/Agent/Gateway既有特性目录新增plan-live-inbox.md。参考feat/argus的attach、watch_availability、read/wait/report/finalize，适配当前独立不可变批次＋一次原子commit，而非另造新Agent调度系统。

## 已实现

1. Argus Store：`inbox_state`只统计本Channel规则接受、来源仍授权且窗口判断完成的未领取工作。新增`agent.batch_requested`控制，复用原observed_items固定请求/回执；初始wake/items不修改，补充批次独立保存。同请求响应丢失后仍取同一批。
2. 领取与commit共用Channel行锁。commit严格确认初始工作＋已领取工作的并集，所有工作必须先有delivered；未领取保持pending，不由当前任务顺手清空。最多4批×20工作、总计100、每批完整正文128KiB；容量超限明确返回错误，工作不丢。
3. Agent工具`argus_receive`合并旧读取/等待能力。等待0..30秒；正常小批直接返回完整窗口和work_refs。读取不等于处理，所有领取工作需要在最终commit给结果。
4. 长正文支持max_chars连续页（256..16384，默认8192），偏移由代码维护。实际最终tool消息必须完整匹配某个已发页，连续覆盖后才ack整批；工具preview、assistant复制、被剪裁输出不能冒充已读。未读完整的批次先重呈现/续读，不再领取下一批。
5. ArgusBridge验证已有活动task的wake/session/binding，Gateway增加Bearer保护的notify薄转发；不接收正文、不创建执行。跨Session409、多余body字段422、错误认证401，结束任务不复活。
6. Argus Bridge在pending水位变化时通知原活动任务，重复扫描不反复notify。Agent插件context.ready后发布一次无正文可读提示，经现有PromptUpdateEvent/TaskContextInputEvent进入安全步骤，不steer/cancel。
7. InputFinished/quiesce取消并join监听。成功commit会唤醒worker退出；await available返回后重查活性，防止任务结束期间返回的旧结果再发notice。无signal遗漏时活动worker30秒补查；receive等待使用signal，不高频空转。
8. 引导与指南说明当前完整窗口、运行中receive、最终工作并集、纯内部工作可handled/no_action及真实平台发送在前；不把完整教程塞进稳定系统。新增工具仍走原权限和同步/异步链。

## RED→GREEN与真实闭环

- Store2项RED于缺inbox_state，补原子批次后legacy commit＋inbox15项通过；追加4批容量、claim/commit竞态测试通过。
- receive集成RED于缺工具；丢领取响应沿原ID重试、未完整可见不ack、最终并集commit通过。纯可见性helper的首条单测随实现加入，没有独立RED；不声称所有helper单独经历RED。
- Plugin/Bridge通知分别RED于缺notify，后验证一次水位一次无正文notice、context准备后才查、结束取消/join、跨绑定拒绝、未新建Task。
- 真实临时PG→HTTP Argus/Gateway→AgentRuntime→本地脚本provider/stdio MCP：初始消息启动后，provider暂时阻塞；补充窗口被当前Channel接受，notice进入同Task；receive取得补充原文，再1次模拟平台send、1次原子commit。Session只有1个argus.wake/任务启动；未使用真实QQ或计费模型。
- 初次E2E失败来自测试脚本名mcp.py遮蔽安装包，改名synthetic_mcp_server.py后通过，没有把夹具错误当产品修复。

## 独立审阅及修复

一次只读第二阶段审阅报告2项Important，均补复现并RED→GREEN：

1. 合法128KiB批次可能超过工具最终预算，反复receive原整包永远被裁剪而无法commit。增加max_chars连续读取恢复，测试使用真实render_tool_result强制600-token预算，先证原包被裁剪，再逐256字符完整页推进；全部正文可重组，最终仅1次delivered。大包不以head-tail假确认。
2. watcher只在await available前查活性，查询期间commit/终态会产生迟到notice；plugin.notify也可在终态清理前报告成功。测试在available阻塞时完成commit/TaskChannel，旧实现失败；补await后活性检查与notify状态门禁，两种均通过。

额外收紧空等待：原0.25秒查询循环在0.55秒测试中产生4次GET，RED后改任务signal等待，仅开始/到期读取，不进行持续短轮询。

## 验证结果

- 最终signal等待修改后`make test-agent`：1383通过，68.94秒。
- `make test-gateway`：61通过，34.32秒；1个既有Starlette/anyio依赖弃用警告。
- Argus完整临时PostgreSQL套件：366通过，139.50秒。
- make typecheck-agent：288源文件清洁。
- 最新receive/notification聚焦11通过；最新Store/运行中真实E2E5通过，全部Argus工具55通过。
- Agent/Gateway/Argus compileall、`git diff --check`和本特性目录相对链接检查全部通过。

## 保留边界

实际模型是否主动receive、是否正确理解补充，需要另获授权的模型/平台烟测；脚本provider只证明正确交付/生命周期。已经发出去的消息不能自动撤回。

不同实例仍需独立数据目录/数据库和Memory身份范围，未自动新增实例协同。旧unknown任务不因为本机制被重跑，旧永久轮询问题不在本次延伸；通知worker只活在当前执行中。

容量是显式有界选择，超出留下一轮；中断后的已领取批次不自动复活执行，仍遵守外部副作用未知不重放。源码未自动提交，也未加载到已有进程，不能称线上已生效。
