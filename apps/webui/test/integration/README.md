# 真实服务验收

测试通过生产 Node 入口访问运行中的 Mem0、OpenKB、Gateway，不拦截 HTTP 或 WebSocket。先构建 WebUI 并启动后端；入口服务凭据从仓库根 `.env` 读取，进程环境可覆盖端点和凭据。

在 `apps/webui` 使用 PowerShell：

```powershell
pnpm build
$env:WEBUI_BROWSER_CHANNEL = 'msedge' # 已安装 Chromium 时可不设置
$env:WEBUI_LIVE_TESTS = '1'
pnpm test test/integration -q
```

以上默认只执行 Mem0 真实写入和 OpenKB 库生命周期。以下开关会真实调用模型，后端必须配置有效模型名、地址和密钥：

```powershell
$env:WEBUI_LIVE_MODEL = '1'
$env:WEBUI_LIVE_KNOWLEDGE_MODEL = '1'
pnpm test test/integration -q
```

- `WEBUI_LIVE_MODEL`：Gateway 文本回复、刷新历史、真实 WebSocket 断线恢复和取消，以及实际调用 bash 与 memory_remember 后验证持久化。工具测试的记忆按唯一 source_session_id 清理。
- `WEBUI_LIVE_KNOWLEDGE_MODEL`：导入一个短 Markdown，真实编译摘要，再验证正文编辑、图谱、阅读和刷新。
- 不提供 `WEBUI_LIVE_URL` 时，测试自行启动临时端口上的生产 Node 入口并生成临时 Basic 凭据。
- 提供 `WEBUI_LIVE_URL` 与 `WEBUI_LIVE_PASSWORD` 时，使用该入口（例如 WebUI 容器），用户名须为 `live-test`。入口的 Origin 配置为相同 URL，后端地址需可从容器访问。

所有记忆使用随机测试用户和服务返回的 ID，知识库使用 `webui-live-<uuid>` 名称，清理只作用于本次创建的数据。Gateway 使用 pytest 临时工作区；持久化测试会话保留在 Gateway 数据目录，不删除已有会话。模型测试应在独立验收环境运行。

测试结束后清除本终端的 `WEBUI_LIVE_*` 环境变量，可恢复普通回归。CI 默认跳过真实服务测试，不能将 skipped 记录算作模型验收。
