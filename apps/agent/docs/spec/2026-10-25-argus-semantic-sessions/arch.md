# Agent 语义会话与插件主动读取当前架构

## 位置与证据

需求见 [共同约定](../../../../../spec/2026-10-25-argus-semantic-sessions.md)，实施步骤见 [plan](plan.md)。已检查应用session_store/controller/client/reaper/semantic_context、Argus插件及references/delivery/notifications/actions普通组件、镜像测试。最终顺序完整回归Agent1288、Argus224、Gateway148通过（Gateway一个既有Starlette/anyio告警），类型检查276文件通过；各本地批次及最终跨组件修正均已通过独立审阅。新版正常/受控延迟确认两条v2场景及旧版两场景走真实HTTP/WS/runtime的离线验收，不等于真实模型或QQ业务验收。

## 所有权与无新表存储

四张Agent应用表不变：workspaces、sessions、session_titles、conversation_updates。

- sessions.properties保存稳定后台kind/owner/scope、有限语义摘要、相关活动时间和current_wake_id；scope_key只表示当前实际执行占用。
- conversation_updates的私有argus_execution行每wake保存一次接纳快照；payload事务更新，sequence/occurred_at不变。报告、游标、短引用和动作事实继续作为私有记录保存，公开history过滤内部类型并校验所有序号。
- 启动迁移将旧单wake属性转换为等价接纳记录及原binding报告归属，保留旧UUID/指纹/记录顺序，损坏证据使迁移回滚，不推断释放。
- 按wake查精确执行；加载session取稳定SessionContext。旧wake重复release只能释放自己的占用，不能解锁已续接的新wake。

同语境空闲session复用历史并启动新task/execution/binding；当前task的新消息则留在Argus持久待读集合，不重新submit。正常execution完成只报告/封口/释放，不立即卸载语义runtime。reserved submit按原entry锁等待已存在的unload/load，在锁外shield等待，再重查并提交，防止复用停止中的旧runtime。

## 实际HTTP client与可信设施

application/services/argus_handoff_client.py是实际注入client，按持久wake/binding版本编码、验证响应。v1旧指纹/解析保留；v2必填可空target包含在冻结指纹中，重试不选新目标。

应用给Argus插件注入identity-bound的load_binding(task_id)、load_records、append_record及session availability Event。插件不导入仓库/controller，不修改冻结SessionContext。图装配时注册稳定四工具，任务开始才加载并校验自己的binding；缺失不回退上一轮。

候选查询使用稳定scope及相关活动，远端session_pending核对无binding等待项；网络未知不是空闲。Gateway只适配可信facade，普通session API仍隐藏/拒绝后台session。

## 模型视图与短引用

v2初始贡献和主动读取使用高密度消息：发送者、聊天/平台目标、发出和观测时间、文本、关注原因和规则。内部wake/binding/observation UUID、report ID和opaque cursor留在插件；真实平台操作需要的定位信息保留为place，不让模型另行考古。

ObservationReferences是插件普通组件，通过原有内部记录恢复session-wide #N，不会在新task里把#1重新指向别的消息。v2工具：read(ref,text_offset?,max_chars?)、history(limit?)、wait(limit?,timeout_seconds?)、report(items[{ref,status,reason?}])。v1旧UUID/cursor路径显式保留。

application/semantic_context.py以有界提取生成实际话题初始prompt和候选摘要，不新增总结LLM调用。自动Memory召回因此不再只查询固定的Process the bound observations调度句。

## 初始与工具正文确认

ObservationDelivery先保留原始wire身份及预算前模型视图。v2不再on_input提前deliver；可信context.ready的最终Blackboard输入真正包含完整、未截断的初始消息时才确认。省略/截断项仍可由Argus pending-first wait/read读回，不以初始最大时间当已读。

工具完成必须匹配真实provider来源、task、tool call/name和完整最终模型视图；renderer改变内容时不登记已读。长正文按实际成功片段维护连续字符区间，终止片段没有前缀不能确认整条。全量交付后调用Argus deliver，再更新local exposed/默认游标。历史页包含长截断消息时保留该已确认页的条目集合和下一游标，后续分段读完所有条目后才推进history_cursor；失败/被裁剪/有缺口不推进，旧页完成结果也不能倒退新游标。报告未知短引用、未展示、不可报告及真正确认中分别处理；outbox恢复按精确wake/binding，不把旧报告重送给新binding。

## 运行中新消息：只有可用性提醒

Argus负责先持久归属，可信notify只设置Event。插件任务级tracked watcher等待初始最终确认，再查询available的计数/水位；它只发布固定短PromptUpdate：

> 当前语境有新的待读消息，可在合适时通过argus_wait读取。

Agent在现有安全步骤边界收到提醒，自行决定读取正文。没有automatic steer、取消/重启当前LLM或工具，也不把到达正文直接写入模型历史。重复通知合并，丢信号定期对账，不以Event作为消息事实源。

input.finished按真实input.submit来源校验后取消并await watcher、在途查询和退避；stop只是兜底。真实task.control/context来源由装配graph注入，监听不会因常驻后台等待而阻塞完整execution drain。

## 五分钟软过期与安全退场

配置session_idle_seconds=300、session_retention_seconds=86400，有限正数且后者更长。五分钟只允许安全卸载，session仍可按语义续接；24小时无相关活动且无工作时设置retired_at退出自动候选，保留历史，不物理删除。

相关活动来自真实语义消息/工作结束，不因状态轮询、报告重试或重复release续时。清理先核对远端waiting/已送达未报告，未知/无时区/未来时间保守保留；entry锁下复核当前owner、活动变化、load/unload、runtime snapshot、tool tracker、outbox。周期cleanup loop实际调用cleanup_argus_sessions，交互session原规则不变。

远端待办快照与并发到达之间不能建立跨库原子性，因此退场目标的已分配后续工作使用保守恢复：认证pending为正且时间合法后，内部先持久接纳一个仍退场/隐藏的preparing执行，再以精确Argus bind证明协议、scope、boot、请求指纹和item集合；绑定持久化与清除retired_at在同一SQLite事务完成。没有调用方revive开关或HTTP跨DB锁。明确业务拒绝可以安全结案未启动的临时接纳；HTTP200但正文超限/协议损坏等不明确结果仍持有原身份对账，不假装远端没有绑定。确定性旧零快照→A2排队→退场→B释放→原A续接及拒绝/恢复/超限成功响应测试已核验。

## 动作事实与外部依赖

ActionFacts按task/tool call去重保存argus_action。MCP协议success或任意OneBot retcode不被提升为QQ业务成功；无明确回执时仅记录unconfirmed尝试，不能被当成已发送回复。

代码支持一个显式可选structured_content.business_receipt v1消费契约，校验kind/success/target/message_id。没有声称bundledSnowLuma已产生该结构，也没有新增QQ发送模块或适配框架。真实业务回执由下游集成和用户真实端到端验收确认。

## 验证与非目标

聚焦验证覆盖实际SessionRuntime/ReAct scripted provider输入、初始引用/正文确认、运行中提醒不带正文、监听退出、连续区间、长历史完成后继续翻页、报告恢复、299/300/301/86399/86400、历史保留、A2排队/B占群、远端未知及跨退场恢复。最终Agent1288、mypy276以及三应用离线贯通通过。受控延迟deliver响应使脚本真实遇到delivery_pending，只有这一明确状态有限重试，其他工具错误立即失败；不以盲目按请求序号停止掩盖未接受报告。没有真实QQ/JEV/MCP业务动作验收。

没有新数据库表、领域化ReActAgent/Plugin Runtime、Hook业务控制、嵌套插件、动态工具发现重复建设、自动视觉或QQ专用一等工具。所有开发仍未提交，未迁移正式库、改本机settings或重启服务。
