# Icarus

Icarus 是一个面向可扩展 Agent 系统的 Monorepo。项目通过模型接入层、Agent 编排层、Plugin Runtime、EventBus、Hook 和持久化组件保持职责解耦，并为后端、WebUI、客户端和终端应用提供统一的 Agent Core。

## 当前应用

- `apps/agent`：模型接入、Agent 编排、工具、插件运行时、持久化与应用服务。
- `apps/gateway`：本机 FastAPI WebSocket + JSON-RPC Agent Gateway。
- `apps/tui`：基于 Textual 的全屏 Agent 终端客户端，提供应用内滚动、持久输入框、本地
  消息队列和 macOS 剪贴板图片输入。

架构设计位于 [`apps/agent/docs/arch/`](apps/agent/docs/arch/)，开发计划位于各应用的 `docs/plan/`。

## 快速开始

开发环境安装：

```bash
python -m venv apps/agent/.venv
apps/agent/.venv/bin/pip install -r apps/agent/requirements.txt
```

使用 `uv` 把全局 `icarus` 命令安装到用户工具环境：

```bash
uv tool install --editable /absolute/path/to/Icarus
```

代码或依赖更新后可执行：

```bash
uv tool upgrade icarus-agent
```

在 `apps/agent/.env` 中配置模型 API Key 和数据目录，并在 `apps/agent/settings.json` 中选择模型：

```dotenv
OPENAI_API_KEY=your-api-key
ICARUS_DATA_DIR=/Users/you/.icarus
```

先启动本机 Gateway：

```bash
icarus-gateway
```

再进入任意 Workspace 启动 TUI：

```bash
cd /path/to/workspace
icarus
```

当前目录会作为 Agent Workspace，并默认创建一个新 Session。`Enter` 把消息提交到 TUI
本地队列；Agent 运行期间输入框仍可编辑，待发送消息会显示在输入框上方，并在当前轮次
结束后按 FIFO 自动发送。受支持终端可用 `Shift+Enter` 换行，所有支持的终端都可用
`Ctrl+J` 换行。在 macOS 上复制截图或浏览器图片后，可在 Composer 中按 `Ctrl+V` 插入
`[#imageN]` 并随消息提交；如果剪贴板没有图片，则保持普通文本粘贴。Windows/Linux 的
系统剪贴板图片读取暂未实现。

`Ctrl+C` 会依次处理当前草稿、撤回最新排队消息、取消正在运行的 Task，或在完全空闲时
退出。取消过程中会显示 `Cancelling`，并保留已经输出的内容；收到取消终态后才继续调度队列。
输入 `exit`、`quit`，或在空输入时按 `Ctrl+D` 也会退出。Textual 退出后恢复启动前的终端画面。

仓库内开发启动仍可使用：

```bash
apps/agent/.venv/bin/python -m apps.tui.src.main
```

Gateway 仓库内开发启动：

```bash
apps/agent/.venv/bin/python -m apps.gateway.src.main
```

## 测试

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test apps/gateway/test apps/tui/test -q
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src apps/agent/test \
  apps/gateway/src apps/gateway/test \
  apps/tui/src apps/tui/test packages
```
