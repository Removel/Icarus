# Gateway 的 Argus 交接架构

## 状态

2026-10-22 实现，尚未正式部署；[design](design.md) 为设计，[plan](plan.md) 为实施步骤。
独立审阅问题已修复，提交前Gateway43项通过，一条既有Starlette弃用warning；另经用户本机
真实测试核对QQ工具调用和任务封口，不将fixture或有限真实测试称为全面生产验收。

## 入口和调用边界

FastAPI owner app仍持单个AgentRuntime、ConnectionHub和RuntimeUpdatePump；普通/rpc只注册既有
用户方法，新/argus/rpc由独立ArgusMethods allowlist仅注册wake/status/cancel/release，所有请求
version=1/token必填。普通入口不转发argus方法，专用入口不接受session.create/submit。

argus_models执行严格类型/未知字段验证，wake再转Agent公开ScopedWakeRequest，校验scope/items/
locator及预算。认证constant-time比较，仅环境token，不进入模型/指纹或日志。安全业务错误保留
code/retryable/owner，status found=false/null与unknown/unavailable分开，stopped不冒充已释放。
Gateway不拥有业务数据库，不缓存持久幂等，不访问runtime私有store/entries；执行/封口归Agent。

Connection在JSON解析前按dispatcher.max_request_bytes约束专用帧1MiB；普通传输保持原16MiB
边界。JSON-RPC request id只用于响应相关性，断线不取消已持久执行。数据read/wait/report不由
Gateway转发，Agent直连Argus内部HTTP。

## 后台隔离

普通会话API由Agent public guard隐藏后台；subscribe在加入(workspace_key,session_id)前校验持久
interactive身份，未知/后台拒绝，不能预订阅未来后台ID。pump仍内部订阅设备流，仅向获准tuple
扇出，不按Argus领域Event分支；内部报告outbox不是公开RuntimeUpdate。

## 双listener生命周期

CLI普通--host仅loopback；可选--service-host/--service-port成对提供，服务token缺失拒绝，限制
可信LAN/VPN地址并要求部署ACL，不做TLS/公网。create_app(shared_app=owner,service_only=True)
路由壳复用owner runtime/hub、不再执行runtime lifespan；main同时驱动两个server，任一退出协作
停止，owner只start/stop一次。远端仅/argus/rpc+health，不挂普通/rpc。无自动部署/真实密钥示例。

## 验证

普通基线34项通过；提交前43项通过，一条既有Starlette弃用warning。
包含认证/版本/not_found、普通兼容、公开订阅guard、专用方法隔离、frame上限和单runtime路由壳。
Argus测试另通过真实TCP启动Gateway子进程+AgentRuntime/provider/local模型/隔离数据库的
read→wait→report→finalize闭环两分支。真实网络ACL未部署验证；本机真实QQ回复另经持久记录
核对，不新建role/device路由系统。
