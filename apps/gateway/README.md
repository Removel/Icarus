# Icarus Agent Gateway

`apps/gateway` 是 Icarus 的本机网络入口。它通过 FastAPI WebSocket 上的 JSON-RPC 2.0 暴露
AgentRuntime，并向 TUI 和未来 Backend 推送公共 RuntimeUpdate。

Gateway 只负责连接、协议校验、调用路由和消息分发，不承担用户、权限、文件上传、Memory、知识库
或其他产品业务。当前 Gateway 与 AgentRuntime 在同一进程运行。

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
组合安装 `apps/agent/requirements.txt`，因为 Gateway 进程内加载 AgentRuntime。

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
```

第一阶段默认只监听本机地址，不提供远程认证。

## 测试

```bash
./apps/gateway/scripts/test.sh
```

架构设计和实施计划按功能聚合在 `apps/gateway/docs/spec/YYYY-MM-DD-<feature>/`。架构文档以当前
源代码和测试为事实依据，用于描述架构与系统设计，不反向限制源代码演进。
