# Gateway 的 Argus 交接设计

> 第二阶段设计；2026-10-22 已接线并完成离线回归及本机真实测试，尚未正式部署。实际现状见arch。
> [共同契约](../../../../../spec/2026-10-21-argus-agent-handoff.md) 是唯一 wire 定义；
> [arch](arch.md) 为现状，[plan](plan.md) 拥有本应用任务。

## 1. 职责与入口

Gateway 是同进程 AgentRuntime 的薄 JSON-RPC 门面，只做身份/typed 参数校验、公开 facade 调用、
安全错误转换。不建业务库/第二调度器，不直接查 Agent/Argus 表，不解释消息业务或代发 QQ 动作。
不新增 Argus role/device binding/proposal 审批/跨设备命令转发系统。

现有本机 listener 的 `/rpc` 保留普通用户 RPC 参数与行为，仍限 loopback。新增 `/argus/rpc`
使用独立四方法 allowlist；普通 `/rpc` 不注册 argus.*，专用入口不注册 session/memory/runtime
方法，未知方法 METHOD_NOT_FOUND。不通过同一远端入口先尝试普通 handler 再做 token 校验。

可选独立服务 listener 只挂 `/argus/rpc` 和无敏感信息 health，绑定可信 LAN/VPN，需显式网络 ACL；
不能通过 --host 直接把普通 listener 外放。同机可直接使用 loopback 的专用路径。两个路由 app
壳共用单个 runtime/hub/pump 和一次 lifespan；不重复 start/stop AgentRuntime。第二阶段不做 TLS，
公网/不可信网络不支持，不相信 forwarded header 改变来源；普通 RPC 不因本次引入全局 token。

## 2. 固定 RPC wire 与预算

四方法均要求 caller_token 和 protocol_version=1，StrictModel 严格类型/extra=forbid/安全验证错误。
请求/返回 JSON 的完整字段、枚举和 null 语义、指纹规范化依共同契约，各应用持有等价 fixture，
不建立共享 Python schema 包。Gateway 不定义第二套 ExecutionView。

| 方法 | 业务输入 | result |
|---|---|---|
| argus.wake | wake_id/origin_boot_id/scope/reason/rule_id/items | protocol_version、accepted=true、execution |
| argus.status | wake_id 或 scope 恰好一个 | protocol_version、found、execution（未找到时 null） |
| argus.cancel | wake_id、reason | protocol_version、accepted=true、execution |
| argus.release | wake_id | protocol_version、released=true、execution |

wake 1..100 个唯一 observation_id，excerpt 总计≤100000 Unicode 字符，控制帧≤1 MiB；不能套用
读取页 50 项/20000 字符上限。source=qq、账号/目标正十进制、chat_id、locator、message_id、有限
Unix time 与 scope 逐项匹配。UUID 规范化、整数/boolean 区分、版本不是字符串，不偷偷宽松转换。
后台 workspace 来自 Agent 可信配置，wire 不携带路径、callback、密钥或任意会话 instructions。

ExecutionView 必须含 release_complete/sync_state/report_cursor，不以 stopped 代替可开始下一次。
status found=false 才是明确未找到；unknown/unavailable 是错误，不映射 null。scope 查询返回当前
占用者；已释放执行可按 wake_id 查询。提交去重/占用原子性属于 Agent 持久事务，不在 Gateway
缓存内存结果来冒充跨重启幂等。同 scope 冲突和全局满额（capacity_busy）不得伪造 accepted。

## 3. 认证与错误

ARGUS_GATEWAY_TOKEN 从环境 token_loader 提供；缺/空配置拒绝启用服务 listener，本机专用方法
也拒绝，不降级免认证。caller_token 使用 SecretStr、constant-time compare；认证在构造领域请求前
完成，token 从 params 移除，不进入 fingerprint/model/log/error。未知字段验证不得回显假 secret。
URL/响应错误不暴露原文、连接串或 traceback；1 MiB 限额在解析前生效。

shape/版本/预算为 -32602；业务 -32000/data={code,retryable,owner?}，固定 code 与根一致。
owner 仅安全 ExecutionView，不包含路径/原文/secret。内部异常 -32603 固定安全文字，不把所有
KeyError 映射 not_found。cancel accepted 不等于 execution_stopped；运行/pending_sync release
返回 busy，不能从 Gateway 强制清槽。完整释放顺序归 Agent/Argus，共同契约不重复定义。

## 4. 公开会话隔离与依赖接口

消耗 Agent facade：scoped_submit(request)、scoped_status(*,wake_id|scope)、scoped_cancel(wake_id,
reason)、scoped_release(wake_id)，全部 async，返回 ExecutionView（status 可 None）。release facade
返回既有 ExecutionView，使 Gateway 不需两次查询拼出结果。Gateway 不访问私有 _store/_entries。

普通 session API 依赖 Agent public guard 隔离 kind=argus_background；subscribe 用
`ensure_public_session(*,workspace_key,session_id)`，不存在/后台均拒绝，验证成功才加入订阅 tuple。
不能先预订阅未知 ID，等将来后台会话创建再读流。get/list/history/submit/steer/cancel/unload/delete/
task.get_status/generate_title/discard_empty 一并覆盖。内部 scoped 控制器使用可信接口而非布尔后门。

RuntimeUpdatePump 继续订阅设备内部流，ConnectionHub 只向通过 guard 的 session 订阅扇出；pump
不按 Argus Event 类型分支，不把内部 outbox 当普通 RuntimeUpdate。单纯隐藏列表不是完整隔离。

## 5. 生命周期、配置和验证

可注入 token_loader，生产从环境读取；服务 listener 地址/端口由显式 CLI 参数提供，不建立新的
Gateway 配置框架。关机先停接收/关两 listener 连接，再 pump、runtime；WS 断开不等于任务停止。
启动第二 listener 失败也只清理已拥有资源，不留下重复宿主/泄漏连接。

离线测试包括固定 JSON roundtrip、认证脱敏/未知字段、100 条合法边界/超限、not_found/unknown/
unavailable、scope/capacity_busy、stopped 未释放、cancel 仍活跃、真实 WS 普通 API/预订阅隔离、
远端专用入口拒绝普通 RPC（无 token 也不能绕道创建普通会话）、单 runtime 双 listener 生命周期。
模型 fixture 本地确定性，PG/SQLite 隔离；无真实 QQ MCP/模型调用。scripts/test.sh 透传聚焦参数，
无参仍全套+compile，保留真实退出码。部署和真实模型冒烟另获授权，不自动启动公网服务。
