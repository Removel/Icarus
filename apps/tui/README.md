# Icarus Textual Terminal Client

`apps/tui` 是 Icarus 的 Textual 全屏终端客户端。它通过本机 Agent Gateway 使用 AgentRuntime，
只消费公共 RuntimeUpdate，不直接访问 SessionRuntime、Plugin Runtime、EventBus、Blackboard 或
具体 Plugin。对话在应用内部滚动；退出后恢复启动前的终端画面。

TUI 使用独立的 `apps/tui/.venv`，运行环境不安装模型 SDK 或 Gateway 服务依赖。它只依赖公共
Gateway 协议与客户端库。

当前界面提供：

- 宽屏 ASCII Icarus Logo、紧凑屏文字回退和当前 Workspace；
- 基于角色视觉的暖黑、羽翼粉与缎带粉主题；Tool 运行、成功、失败分别使用暖金、眼眸绿和红色；
- 欢迎页显示后并发连接 Gateway 并初始化目标 Session；初始化完成前页面保持可编辑且不展示启动日志，
  只有消息正在等待 Runtime 时才显示 `Initializing`；
- 固定在底部、可增长到八行的持久多行输入框；
- Agent 运行期间继续编辑和提交；
- TUI 本地 FIFO 待发送队列，以及从队尾撤回的 LIFO 操作；
- 当前 Workspace 的 Session 列表、恢复和开始新对话；
- macOS 剪贴板图片粘贴，Composer 使用 `[#imageN]` 表示随消息提交的图片；
- 流式 Markdown、工具状态、错误和任务终态；
- 按公共 RuntimeUpdate.type 投影任务、文本、Tool、错误、Usage 和 Compact 状态。

## 运行

先在 `apps/agent/.env` 配置模型 API Key 和数据目录：

```dotenv
ICARUS_DATA_DIR=/Users/you/.icarus
```

安装 TUI 自己的运行依赖：

```bash
./apps/tui/scripts/install.sh
```

依赖安装在 `apps/tui/.venv`。需要运行测试时使用：

```bash
./apps/tui/scripts/install.sh --dev
```

Gateway 使用独立虚拟环境，需要单独安装和启动：

```bash
./apps/gateway/scripts/install.sh
./apps/gateway/scripts/start.sh
```

进入任意 Workspace 启动一次新会话：

```bash
cd /path/to/workspace
/absolute/path/to/Icarus/apps/tui/scripts/start.sh
```

在仓库根目录执行过 `make install` 或 `make install-commands` 后，也可以直接运行：

```bash
cd /path/to/workspace
icarus
```

可选指定 Session ID；已有 Session 会恢复历史，不存在时创建：

```bash
/absolute/path/to/Icarus/apps/tui/scripts/start.sh --session-id demo-session
```

对应的全局命令是：

```bash
icarus --session-id demo-session
```

按键：

- `Enter`：把非空草稿加入 TUI 本地队列；Runtime 空闲时立即提交，否则排队；
- `Shift+Enter`：在终端能区分该按键时插入换行；
- `Ctrl+J`：在所有支持终端中插入换行；
- `Ctrl+V`：macOS 剪贴板存在图片时，在光标处插入 `[#imageN]`；没有图片时回退为普通
  文本粘贴。Windows/Linux 暂未实现系统剪贴板图片读取；
- Composer 聚焦时，左右键、上下键和 `Home` / `End` 在 TextArea 中移动光标，滚轮不控制
  Conversation；
- Conversation 聚焦时，上下键、`Home` / `End` 和滚轮浏览对话；用户上滚后流式输出不会
  把阅读位置拉回底部；
- `PageUp` / `PageDown`：无论当前焦点在哪，都按页浏览 Conversation，同时保留 Composer
  草稿、光标和焦点；
- `Ctrl+End`：回到 Conversation 底部并恢复自动跟随；
- `Ctrl+D`：输入框为空时退出；有内容时执行 TextArea 的向右删除；
- `exit`、`quit`：作为完整提交内容时退出。
- `/resume`：仅在完全空闲时列出当前 Workspace 的非空 Session；方向键选择，`Enter` 恢复，
  `Escape` 取消；
- `/clear`：仅在完全空闲时开始一个新对话；当前非空 Session 会保留，当前已经为空时不会重复创建。

`/resume` 和 `/clear` 是 TUI 本地命令，不进入待发送队列，也不会发送给 Agent。命令带图片附件时
不会执行，并会恢复完整草稿。

`Ctrl+C` 只执行第一条满足条件的动作：

1. 输入框非空：清空当前草稿；
2. 输入框为空、待发送队列非空：撤回最新加入的消息，并恢复完整内容到输入框；
3. 输入框和队列为空、Agent 正在运行：调用 `cancel_task(task_id)` 请求取消，显示
   `Cancelling` 并保留已产生的输出；
4. 输入框和队列为空、Agent 空闲：退出 `icarus`。

正常队列消费从队首开始，撤回从队尾开始。取消只结束当前 Task，不停止或重启 Runtime；
收到 `task.finished(status="cancelled")` 后才调度下一条消息。多轮业务对话上下文仍由
Agent Core 的 Blackboard 维护；TUI Conversation 只是当前进程的 UI 投影。
指定已有 `--session-id` 时，TUI 会在进入 Ready 前通过 Gateway 恢复新格式 Session 的公共会话
记录，包括用户消息、助手文本、Tool、错误和中断终态。旧 Session 不从 Trace 迁移历史，只从升级后
产生的新任务开始显示可恢复记录。

图片 Marker 可以和文字一起编辑。提交时只发送草稿中仍然存在完整 Marker 的图片，并按 Marker
第一次出现的顺序建立附件映射；删除 Marker 会让对应图片退出本次提交。图片写入
`$ICARUS_DATA_DIR/incoming/` 并只通过 ResourceRef 提交，Runtime 接受任务并复制到 Session Asset 后
删除暂存文件；失败或未确认请求保留文件用于重试。

## 测试

```bash
./apps/tui/scripts/install.sh --dev
./apps/tui/scripts/test.sh
```

确定性 transcript replay：

```bash
apps/tui/.venv/bin/python apps/tui/scripts/replay_events.py \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
```

完整 Textual shell replay（不调用模型、不写 Session）：

```bash
apps/tui/.venv/bin/python apps/tui/scripts/replay_events.py \
  --tui-real --speed 8 \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
```

视觉快照：

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/test_app_snapshots.py -q
```
