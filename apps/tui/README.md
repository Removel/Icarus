# Icarus Textual Terminal Client

`apps/tui` 是 Icarus 的 Textual 全屏终端客户端。它通过
`AgentRuntimeService` 使用 Agent Runtime，不直接访问 Plugin Runtime、EventBus、Blackboard
或具体 Plugin。对话在应用内部滚动；退出后恢复启动前的终端画面。

当前界面提供：

- 简洁欢迎页和当前 Workspace；
- 欢迎页显示后并发初始化 Agent Runtime；初始化完成前页面保持可编辑且不展示启动日志，
  只有消息正在等待 Runtime 时才显示 `Initializing`；
- 固定在底部、可增长到八行的持久多行输入框；
- Agent 运行期间继续编辑和提交；
- TUI 本地 FIFO 待发送队列，以及从队尾撤回的 LIFO 操作；
- 流式 Markdown、工具状态、错误和任务终态；
- 按 Plugin 来源投影 Runtime Event；当前注册来源为 `agent` 和 `user-input`。

## 运行

先在 `apps/agent/.env` 配置模型 API Key 和数据目录：

```dotenv
ICARUS_DATA_DIR=/Users/you/.icarus
```

从仓库根目录安装全局命令：

```bash
uv tool install --editable /absolute/path/to/Icarus
```

进入任意 Workspace 启动一次新会话：

```bash
cd /path/to/workspace
icarus
```

可选指定 Trace Session ID：

```bash
icarus --session-id demo-session
```

按键：

- `Enter`：把非空草稿加入 TUI 本地队列；Runtime 空闲时立即提交，否则排队；
- `Shift+Enter`：在终端能区分该按键时插入换行；
- `Ctrl+J`：在所有支持终端中插入换行；
- Composer 聚焦时，左右键、上下键和 `Home` / `End` 在 TextArea 中移动光标，滚轮不控制
  Conversation；
- Conversation 聚焦时，上下键、`Home` / `End` 和滚轮浏览对话；用户上滚后流式输出不会
  把阅读位置拉回底部；
- `PageUp` / `PageDown`：无论当前焦点在哪，都按页浏览 Conversation，同时保留 Composer
  草稿、光标和焦点；
- `Ctrl+End`：回到 Conversation 底部并恢复自动跟随；
- `Ctrl+D`：输入框为空时退出；有内容时执行 TextArea 的向右删除；
- `exit`、`quit`：作为完整提交内容时退出。

`Ctrl+C` 只执行第一条满足条件的动作：

1. 输入框非空：清空当前草稿；
2. 输入框为空、待发送队列非空：撤回最新加入的消息，并恢复完整内容到输入框；
3. 输入框和队列为空、Agent 正在运行：调用 `cancel_task(task_id)` 请求取消，显示
   `Cancelling` 并保留已产生的输出；
4. 输入框和队列为空、Agent 空闲：退出 `icarus`。

正常队列消费从队首开始，撤回从队尾开始。取消只结束当前 Task，不停止或重启 Runtime；
收到 `InputFinishedEvent(status="cancelled")` 后才调度下一条消息。多轮业务对话上下文仍由
Agent Core 的 Blackboard 维护；TUI Conversation 只是当前进程的 UI 投影。

仓库内开发启动：

```bash
apps/agent/.venv/bin/python -m apps.tui.src.main
```

## 测试

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
```

确定性 transcript replay：

```bash
apps/agent/.venv/bin/python apps/tui/scripts/replay_events.py \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
```

完整 Textual shell replay（不调用模型、不写 Session）：

```bash
apps/agent/.venv/bin/python apps/tui/scripts/replay_events.py \
  --tui-real --speed 8 \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
```

视觉快照：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/tui/test/test_app_snapshots.py -q
```
