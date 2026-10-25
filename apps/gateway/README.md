# Icarus Agent Gateway

`apps/gateway` 是 Icarus 的本机网络入口。它通过 FastAPI WebSocket 上的 JSON-RPC 2.0 暴露
AgentRuntime，并向 TUI、WebUI 和未来 Backend 推送公共 RuntimeUpdate。WebUI 通过其 Node
同源代理连接 `/rpc`，不直接读取 Agent 的持久化目录。

Gateway 负责连接、协议校验、调用路由、消息分发及图片资源传输，不承担用户、权限、Memory、知识库
存储或其他产品业务。当前 Gateway 与 AgentRuntime 在同一进程运行。

`POST /resources` 接收单张图片原始字节，校验类型和 20 MiB 上限后写入既有 incoming 目录，
返回 session.submit/steer 已支持的 ResourceRef。`GET /resources` 用于读取上传暂存图或指定会话的
历史图片。WebUI 通过同源代理访问，Agent 导入与模型转换逻辑不变。
接口、路径边界和暂存生命周期见 [图片资源架构](docs/spec/2026-10-24-image-resources/arch.md)。

`memory.get_context` 接受可选的绝对 `workspace_path`，委托 AgentRuntime 返回配置中的
`user_id`、`agent_id`、`run_id` 及规范化工作区路径；不创建 Session、不返回服务地址或凭据。
WebUI 新增记忆使用这个归属信息，不能仅升级 WebUI 而保留不支持此方法的 Gateway。

公共更新通过开放的 `type: str` 与 `payload: dict` Envelope 传输。thinking delta 只进入实时订阅，
完整 thinking 与 Tool 安全预览同时进入实时订阅和 Session 历史；Gateway 不聚合 thinking，也不读取
Agent 侧完整 Tool Result。`session.steer` 可透传稳定 `submission_id`，供客户端在响应未知时幂等重试。

## 安装依赖

运行环境：

```bash
./apps/gateway/scripts/install.sh
```

开发和测试环境：

```bash
./apps/gateway/scripts/install.sh --dev
```

依赖安装在 `apps/gateway/.venv`。Gateway 自己的直接依赖声明在 `requirements.txt`；安装脚本还会
组合安装 `apps/agent/requirements.txt`，并 editable 安装本地 `apps/agent` distribution；两者不共用 venv。
Gateway 从 `agent_app.application` 导入 AgentRuntime；仓库根 bootstrap 只用于 Gateway 与共享 wire 包，
不是 Agent 新包的安装机制。

## 启动

推荐通过仓库控制命令在后台启动并登记状态：

```bash
icarus start gateway
icarus status gateway
icarus stop gateway
```

以下 App 私有脚本保留用于开发时以前台方式调试：

```bash
./apps/gateway/scripts/start.sh
```

兼容命令 `icarus-gateway` 仍以前台方式运行，并支持直接传入监听参数：

```bash
icarus-gateway
```

可覆盖监听地址：

```bash
./apps/gateway/scripts/start.sh --host 127.0.0.1 --port 8765
```

默认端点：

```text
GET http://127.0.0.1:8765/health
WS  ws://127.0.0.1:8765/rpc
POST/GET http://127.0.0.1:8765/resources
```

第一阶段默认只监听本机地址，不提供远程认证。

## Argus 后台交接（默认不启用）

本机 `/rpc` 保留普通用户方法；`/argus/rpc` 仅提供 `argus.wake/status/cancel/release`，每次请求
都需 `protocol_version=1` 和环境 `ARGUS_GATEWAY_TOKEN` 对应的 `caller_token`。缺 token 拒绝，
普通入口不会转发这些方法，专用入口也不接受 session.create/submit。后台会话不出现在普通列表，
普通会话 API 和未知 ID 预订阅不能访问它。status 的 `execution_stopped` 不等于 `release_complete`。

启用前在 Agent 可信配置中设置 `argus.enabled=true`、已存在的绝对后台 workspace 和 Argus
`base_url`，并提供独立的`ARGUS_AGENT_API_TOKEN`。后台现在直接继承已配置的MCP服务器，可由模型
按需调用QQ工具；没有新增动作白名单、当前群范围强制检查或发送outbox，最终正文不自动发布。
同一 Agent data_dir 只允许一个宿主进程。默认最多8个后台执行，wait/pending_sync仍占名额。

独立部署可显式传 `--service-host <私有LAN或VPN地址> --service-port <端口>`；普通 `--host`
仅允许 loopback，远端 listener 只有 `/argus/rpc` 和 health。两入口同进程共用一个 runtime。
必须有网络 ACL；本阶段不做 TLS、不支持公网，也不把 forwarded header 作为身份。示例不自动
启用、不写真实密钥，正式迁移/部署/模型冒烟需要另行授权。

## 测试

```bash
./apps/gateway/scripts/test.sh
```

架构设计和实施计划按功能聚合在 `apps/gateway/docs/spec/YYYY-MM-DD-<feature>/`。架构文档以当前
源代码和测试为事实依据，用于描述架构与系统设计，不反向限制源代码演进。
