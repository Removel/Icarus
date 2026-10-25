# Gateway v2 当前架构

## 位置与证据

本文件描述已实现的窄控制边界，计划见 [plan](plan.md)，共同语义要求见 [root spec](../../../../../spec/2026-10-25-argus-semantic-sessions.md)。已检查 `src/protocol/argus_models.py`、`argus_methods.py` 和镜像 `test/test_argus_methods.py`、`test/test_app.py`、`test/test_methods.py`。实现报告及最终顺序回归Gateway148通过、一个既有Starlette/anyio警告；独立审阅已通过，新版正常/受控延迟确认和旧版三应用HTTP/WS离线验收也已通过。这里只证明本地确定性provider链路，不证明真实QQ业务动作。

## 控制面

`ArgusMethods` 继续由专用 `/argus/rpc` 使用，六个方法：wake、status、cancel、release、sessions、notify。普通 `/rpc` 不因此获得 Argus 权限，专用接口不提供普通 session.steer。

所有方法先固定服务 token 常量时间比较，再进行严格形状/版本校验。帧大小限制和 listener 隔离沿用既有设施，没有新增 Runtime、数据库或状态缓存。

## 版本与适配

- 基础 service 参数接受严格整数1/2；新增 sessions/notify 仅版本2。
- WakeParams 复用 Agent 公开 ScopedWakeRequest，增加 SecretStr token；没有复制一份嵌套消息/target 业务模型。
- v2 target_session_id 必须存在但可 null，v1 不接受该字段；规范化、指纹及目标资格由 Agent 公共边界/应用负责。
- sessions 传入严格 Scope、cursor和limit（默认10、最大20），将 Agent 的内部 `{items,next_cursor}` 仅在这里映射为 wire `{protocol_version,sessions,next_cursor}`。
- notify 只传 wake_id/session_id，返回真实 accepted/merged bool，无正文、资源或操作指令。Gateway 不调用 steer/submit/cancel 代替提醒。
- wake/status/cancel/release 的成功 envelope 使用调用方已验证的版本；旧 v1 收尾不会重写某个 v2 execution 的已存指纹或数据面版本。

## 错误与所有权

形状错误为 INVALID_PARAMS，已知 Agent 业务拒绝保留安全 code/retryable/必要 owner；停止或SQL异常为 unavailable，未知异常仅记录类型并返回安全 INTERNAL_ERROR。JSON-RPC 错误不是带虚假 protocol_version 的成功对象。

候选资格/时限和执行锁归 Agent；JEV 归属归 Argus；待读正文和权限归 Argus 数据面。Gateway 不访问 `_store`/`_entries`、不解释 QQ retcode、不持有提醒队列，也不把 WS 断线当成执行停止。

## 验证边界

测试覆盖鉴权、严格版本、target缺失/null、冻结fixture、翻页上限、字段拒绝、普通/专用入口隔离、公开后台guard、仅可信facade转发和真实Agent候选/提醒设施。三应用v2fixture的请求与指纹一致。

运行中Agent只收到提醒、正文由其主动读取，以及五分钟后同语境续接，已由新的三应用真实运行时离线场景验证；24小时及并发退场恢复另有Agent假时钟/可信绑定回归。分类器/provider为确定性本地fixture，没有真实模型/QQ/MCP或生产部署验证。
