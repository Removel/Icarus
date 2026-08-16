# Icarus

Icarus 是一个面向可扩展 Agent 系统的 Monorepo。项目通过模型接入层、Agent 编排层、Plugin Runtime、EventBus、Hook 和持久化组件保持职责解耦，并为后端、WebUI、客户端和终端应用提供统一的 Agent Core。

## 当前应用

- `apps/agent`：模型接入、Agent 编排、工具、插件运行时、持久化与应用服务。
- `apps/tui`：用于验证 Agent Core 的最小标准库 REPL。

架构设计位于 [`apps/agent/docs/arch/`](apps/agent/docs/arch/)，开发计划位于各应用的 `docs/plan/`。

## 快速开始

安装 Agent 依赖：

```bash
python -m venv apps/agent/.venv
apps/agent/.venv/bin/pip install -r apps/agent/requirements.txt
```

在 `apps/agent/.env` 中配置模型 API Key 和数据目录，并在 `apps/agent/settings.json` 中选择模型：

```dotenv
OPENAI_API_KEY=your-api-key
ICARUS_DATA_DIR=/Users/you/.icarus
```

从仓库根目录启动 REPL：

```bash
apps/agent/.venv/bin/python -m apps.tui.main
```

输入 `exit`、`quit`，或发送 EOF 退出。

## 测试

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test apps/tui/test -q
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test apps/tui
```
