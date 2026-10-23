# 本地人工验收服务启动记录

日期：2026-10-23。用户明确要求代理启动后端，由用户手动验收。

## 运行入口

- Icarus 开发前端：`http://127.0.0.1:5180/#/chat`，现有 Node/Vite 进程保持运行。
- dswui 原版对照：`http://127.0.0.1:5173/`，未停止或调整。
- Gateway：Windows 可访问 `http://127.0.0.1:8765/health`，实际运行于 WSL Ubuntu，监听回环地址；AgentRuntime 嵌入同一进程。
- 工作区需填写服务端 Linux 路径：`/mnt/f/project/Icarus`，对应 Windows 的 `F:\project\Icarus`。
- Mem0：`http://127.0.0.1:8888`；OpenKB：`http://127.0.0.1:7566`。浏览器继续通过前端同源代理访问。

## 实际启动与环境修复

1. Docker Desktop 原先未运行，已启动并恢复已有预览容器 `mem0-dev-postgres-1`、`mem0-dev-mem0-1`、`openkb-dev-openkb-1`。Mem0 首次启动时数据库尚在启动，待数据库健康后再次启动成功。没有删除容器、卷、知识库或会话。
2. 仓库控制面 `start gateway` 因 WSL 未启用 Docker 集成而无法检查 Compose 容器，未能完成；原 Gateway 状态记录另有旧进程身份不匹配。保留原状态记录，没有向不明 PID 发信号。
3. Gateway 已有 WSL 私有环境，但缺少当前 Agent distribution。通过该环境的 pip editable 安装本地 `apps/agent`，补装声明的 `typesafe-sdk==0.7.2` 及 `tenacity`。随后 `pip check` 返回 `No broken requirements found.`。未用根 PYTHONPATH 代替 Agent 安装。
4. 使用现有 `apps.gateway.src.main` 启动 Gateway，WSL PID 为 775（仅是记录时值，后续应重新核验身份），保留现有 `.env` 配置和 `/home/guoshangyong/.icarus-repro` 数据目录。未修改后端源码、模型配置或凭据。
5. 本次临时启动器、独立 PID 信息、安装下载和日志均在忽略目录 `apps/webui/test-results/dswui-integration/`：`start-gateway-acceptance.py`、`gateway-acceptance-process.json`、`gateway-acceptance.log`。此 Gateway 不由旧控制面 PID 记录管理，后续停止时需按独立记录核对进程，不能依赖旧记录盲目操作。

## 已完成的只读验证

- Gateway `/health` 返回 `{"status":"ready"}`。
- 仓库 `server/check-services.mjs`：Mem0、OpenKB、Gateway 全部 ready，退出码 0。
- 经 `ws://127.0.0.1:5180/rpc` 调用 `runtime.get_status`、`memory.get_context`、`session.list` 均成功，退出码 0。
- 经 5180 前端代理读取 Mem0 记忆列表、OpenKB 知识库列表均 HTTP 200，且响应结构匹配，退出码 0；未输出业务内容或凭据。

以上证明服务启动与前端代理可用。此次恢复的是原有 Mem0/OpenKB 预览镜像，未重新构建；未据此声明镜像已包含仓库全部最新后端变更。未自动发送真实模型消息或执行业务写入，模型响应、完整流式交互和管理写入留待用户人工验收。dswui 改造整体仍在进行中，不能将服务就绪等同于所有阶段验收通过。
