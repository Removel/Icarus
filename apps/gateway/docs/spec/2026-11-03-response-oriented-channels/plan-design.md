# Gateway 接收状态薄转发设计

更新：2026-11-05。**接收状态薄转发与本地联合验证已写入工作区，未提交或部署。** [实施计划](plan.md) · [Agent 设计](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan-design.md) · [Argus 设计](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-design.md)。代码调查基线 `e2ca65c`。

## 下一阶段验证

消息窗口与插件交付的联合验收见[plan-observation-delivery.md](plan-observation-delivery.md)。复用Agent新增window_items/history_items，不在Gateway判断窗口、消息语义或Channel归属；当前阶段不新增notify端点。

## 1. Gateway 只接线，不接管业务

一个 Icarus 实例是一个独立对象。Gateway 不因两个实例接入同群而共享 Channel、Session、回执或记忆，也不建立跨实例路由/去重。隔离的实际存储与配置由各应用负责并联合验证。

Source 是外部区域，平台账号是接入身份。Gateway 不解析 source key，也不解释 QQ、飞书或 Channel 的业务内容；复用 Agent 公共 DTO，不能复制一套近似模型。

## 2. 为什么要改

当前 `/argus/wake` 将 Bridge 返回裁成 task_id/status；`/argus/status` 才直接转发。要让 Argus 区分“确定未接收”和“可能已执行”，wake/status 必须提供一致的接收信息，不能把 Session ready 当任务 accepted。

真实接收边界与日志归约属于 Agent；Gateway 不能根据 HTTP500、超时或缺日志自行判可重试，也不实现补偿发送。

## 3. 拟定契约

沿用受保护的 `/argus/session`、`/argus/wake`、`/argus/status`，保持 Bearer 鉴权和 public RPC 无桥接入口。

- session 继续传 channel_id/request_id/session_key；选择成功只返回 Session，不表示内容已接收。
- wake/status 保留 task_id/session_id/status、commit_state/replay_safe，并按 Agent 公共契约转发 admission_state、attempt、retryable、error_code。
- 接收阶段为 prepared/not_admitted/accepted/submitting/unknown；Agent 掌握判定依据，Gateway 不重新推导。
- 输入 DTO 继续拒绝伪造字段、跨 Session/Channel 绑定和超限数据；来源/媒体新事实通过现有 content/detail 往返，不导入 Argus 私有类型。
- 调用失败但无明确接收结果时，不合成 retryable=true。`replay_safe` 不替代 admission 判断。

## 4. 模型输入测试的职责

Gateway 集成测试要捕获实际 provider 输入，而不是只看中间格式化函数：

- 系统原则短且稳定；消息、来源和业务状态只在当前 User Context。
- 普通回复不带规则手册或后台 UUID；需要规则说明时可通过现有工具发现读取指南。
- 消息、历史和当前事项目标各有职责，不要求重复读已经完整可见的消息。
- 真实业务内容仍触发 Memory 查询，但教程和内部信封不进入召回主题。
- 接收前失败能恢复，可能已入队则不二次执行；commit 回执丢失不重发 MCP。

## 5. 兼容与限制

Agent 先支持新旧契约，Gateway 再透传，Argus 最后消费；不让半升级状态的扫描擅自重放。旧结果缺字段保持保守，不能补成“确定未接收”。已有在途任务按原绑定对账，不修改历史。

不新增平台 reply 路由，不维护 Session 映射数据库，不修改普通 RPC 权限，不引入 QQ 参数自动重写。隔离测试不等于真实平台验收；部署、付费模型与真实外发均另行授权。
