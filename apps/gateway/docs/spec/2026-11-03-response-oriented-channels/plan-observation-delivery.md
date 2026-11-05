# 消息窗口与插件交付：Gateway 验证计划

> 本阶段只做既有受保护契约薄转发和真实本地链路验证，不在Gateway实现消息窗口或Argus业务。

**Goal:** 验证完整窗口经Argus→Gateway→Agent插件→最终模型输入，且一次事项回应不会按气泡拆成多次。

**Architecture:** 复用Agent公共WakeRequest，新增window_items/history_items由Agent拥有；Gateway不复制模型，不解释Source/平台协议。继续保持Bearer、Session关联、接收证据与完整原请求幂等。

**Tech Stack:** 现有FastAPI、httpx、pytest、真实本地AgentRuntime和stdio MCP fixture。

**Spec:** [Argus窗口计划](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-message-windows.md) · [Agent交付计划](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan-observation-delivery.md)。第二阶段新增notify/read/wait不在本阶段实施。

## Global Constraints

- 不新增平台reply或notify路由，不改变公共RPC权限，不把规则或计时器塞进Gateway。
- 动态正文位于插件上下文，稳定系统仅原则；User Prompt仍是模型最终动态信息角色。
- 合成平台/脚本模型只证明编排，不证明真实LLM正确理解或实际QQ发送。
- 不启动/重启生产服务，不主动真实外发，不读取生产消息作为测试夹具。

## Review Focus

1. DTO扩展放大总载荷绕过512KiB：Task1。
2. context缺失时模型仍启动/commit：Task1。
3. 读取完整窗口后回执确认范围扩到history：Task2。
4. 同Session第二窗口沿用首轮ref、错误重复确认：Task2。
5. 同一原请求恢复时正文重渲染造成指纹改变：Task2。

## Task1：公共契约与模型实际输入

**Files:** `apps/gateway/test/test_argus_bridge.py`、`test_argus_semantic_context_e2e.py`；生产`src/protocol/argus.py`只有公共DTO要求必要时修改。

- [ ] 先写失败断言：传完整window_items/history_items，观察plugin_context有正文，user_request仅简短主题；系统不含动态数据。
- [ ] 测试输入超限/非法来源/重复window消息/缺工作消息拒绝，仍然执行前失败。
- [ ] 必需Argus贡献未到/失败时不调用provider，不把短启动请求当内容已送达。
- [ ] 消费Agent最小实现，保持protected端点的完整接收状态透传。

## Task2：完整窗口、一次回应与旧契约回归

**Files:** `test_argus_tools_e2e.py`、`test_argus_mcp_reply_e2e.py`、新`test_argus_message_windows_e2e.py`；Argus真实TCP/PG场景由Argus应用测试拥有。

- [ ] 一段可配置时间窗口的6条合成消息（包含解释句）全部进入首次provider输入；一次本地MCP send后commit，而不是每个气泡一次send。
- [ ] 时间间隔2秒补充、quiet到期、max30秒、超20条余项留待下一窗口；使用fake clock，不等待真实30秒。
- [ ] 当前窗口无需单独回应的原话仍在主正文；history有身份但不可确认，items范围不变。
- [ ] 第二窗口复用Session时按当前work_refs确认；脚本provider不能靠“第一个ref”碰运气选工作项。
- [ ] 已发送commit失联不重发，旧view_version兼容，新wake原文不可变；改配置不重建旧请求。
- [ ] 最小测试RED→GREEN，再`make test-gateway`、相关Agent/Argus套件、compileall/diff。

## 交付

保留运行服务当前状态；本阶段完成后如需加载新插件机制，另确认空闲任务和重载。旧生产消息不重放、已发送内容不补发；更新arch仅根据实际实现与测试证据。
