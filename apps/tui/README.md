# Icarus REPL TUI

`apps/tui` 是用于验证 Agent Core 的最小标准库 REPL。

## 运行

先在 `apps/agent/.env` 配置模型 API Key 和数据目录：

```dotenv
ICARUS_DATA_DIR=/Users/you/.icarus
```

从仓库根目录启动：

```bash
apps/agent/.venv/bin/python -m apps.tui.main
```

可选指定 Trace Session ID：

```bash
apps/agent/.venv/bin/python -m apps.tui.main --session-id demo-session
```

输入 `exit`、`quit` 或发送 EOF 退出。当前版本串行执行任务，并展示文本增量、工具名称、参数和执行状态。多轮对话上下文由 Agent Core 的 Blackboard 维护。

## 测试

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
```
