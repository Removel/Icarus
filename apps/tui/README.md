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
- Agent 运行期间继续编辑和提交；所有普通输入先进入同一个 FIFO，出队时按实时状态选择追加当前
  Task 或创建新 Task；
- TUI 本地 FIFO 待发送队列，以及从队尾撤回的 LIFO 操作；
- 当前 Workspace 的 Session 列表、恢复和开始新对话；
- macOS 剪贴板图片粘贴，Composer 使用 `[#imageN]` 表示随消息提交的图片；
- Assistant Markdown 全文显示（含中途/未完成输出），不使用 AI 消息内部滚动或高度限制；
- 每轮 Run 一个固定高度、有滑块的执行记录区，默认跟随最新；工具详情和旧 Thinking 放在记录中，默认折叠；
- 当前最新 Thinking 单独显示在执行记录边框下方，新 step 出现时旧 Thinking 归档到记录区；
- Thinking 运行时显示字符动画，完成、页面隐藏或减少动效时停止；正文展开时才解析；
- 已应用的追加/纠偏在顶层以用户消息呈现，保留原任务/step，不额外建立导航轮次；
- 长对话保留完整历史数据，但只挂载当前附近的部分消息 Widget；右侧以不超过十个圆点浏览用户输入轮次，悬停展开紧凑摘要列表，点击后跳到对应消息；
- RunCard 外独立呈现最终 Assistant Markdown；
- 按公共 RuntimeUpdate.type 投影任务、文本、thinking、Tool、错误和终态；Usage/Compact 尚不提供真实底部元信息；
- 输入 `/` 在输入框下方显示真实已注册命令建议，支持上下选择、Tab 补全、Enter 执行、Esc 关闭；
- 底部任务状态与模型/会话/Workspace 元信息分开；模型/思考/上下文缺少公开来源时显示未知，不填演示值；
- App 持有会话和后台 Worker，聊天页被其他页面覆盖时不关闭网络或丢草稿。当前没有新增其他业务页面。

## 运行

先在仓库根 `.env` 配置模型 API Key 和数据目录：

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

Gateway 使用独立虚拟环境，需要先安装和启动：

```bash
icarus install gateway
icarus start gateway
```

进入任意 Workspace 启动一次新会话：

```bash
cd /path/to/workspace
icarus tui
```

也可以在首次安装前直接使用 App 私有脚本：

```bash
cd /path/to/workspace
/absolute/path/to/Icarus/apps/tui/scripts/start.sh
```

可选指定 Session ID；已有 Session 会恢复历史，不存在时创建：

```bash
icarus tui --session-id demo-session
```

旧命令形式暂时兼容：

```bash
icarus --session-id demo-session
```

按键：

- `Enter`：把非空草稿加入 TUI 本地队列；Runtime 空闲时立即提交，否则排队；
- `Shift+Enter`：在终端能区分该按键时插入换行；
- `Ctrl+J`：在所有支持终端中插入换行；
- `Ctrl+V`：macOS 剪贴板存在图片时，在光标处插入 `[#imageN]`；没有图片时回退为普通
  文本粘贴。Windows/Linux 暂未实现系统剪贴板图片读取；
- Composer 聚焦时，左右键、上下键和 `Home` / `End` 在 TextArea 中移动光标；
- 鼠标位于 Conversation 时，滚轮和右侧 ScrollBar 始终用于浏览对话，不要求先切换键盘焦点；
  用户上滚后流式输出不会把阅读位置拉回底部；
- 鼠标位于右侧用户输入圆点轨道时，滚轮只翻阅轨道所列轮次，不滚动正文；悬停圆点展开对应的输入摘要列表，点击圆点或摘要跳转到该轮。轨道聚焦时，`↑`/`↓`、`PageUp`/`PageDown`、`Home`/`End` 选择轮次，`Enter` 跳转；
- Conversation 聚焦时，上下键和 `Home` / `End` 浏览对话；
- `PageUp` / `PageDown`：无论当前焦点在哪，都按页浏览 Conversation，同时保留 Composer
  草稿、光标和焦点；
- `Ctrl+End`：回到 Conversation 底部并恢复自动跟随；
- `Ctrl+D`：输入框为空时退出；有内容时执行 TextArea 的向右删除；
- `/exit`：通过本地命令注册表执行正常退出；裸 `exit` 和 `quit` 作为普通用户消息提交；
- `/resume`：仅在完全空闲时列出当前 Workspace 的非空 Session；方向键选择，`Enter` 恢复，
  `Escape` 取消；
- `/clear`：仅在完全空闲时开始一个新对话；当前非空 Session 会保留，当前已经为空时不会重复创建。

`/resume`、`/clear` 和 `/exit` 是 TUI 本地命令，不进入待发送队列，也不会发送给 Agent。命令带图片
附件时不会执行，并会恢复完整草稿。未知 `/xxx` 会显示命令错误，不作为普通消息发送。

`Ctrl+C` 只执行第一条满足条件的动作：

1. 输入框非空：清空当前草稿；
2. 输入框为空、待发送队列非空：撤回最新加入的消息，并恢复完整内容到输入框；
3. 输入框和队列为空、Agent 正在运行：调用 `cancel_task(task_id)` 请求取消，显示
   `Cancelling` 并保留已产生的输出；
4. 输入框和队列为空、Agent 空闲：退出 `icarus`。

正常队列消费从队首开始，撤回从队尾开始。队首发送时才决定调用 `session.steer` 还是
`session.submit`；响应未知时保留同一消息、目标和 `submission_id`，在连接恢复后精确重试。
取消只结束当前 Task，不停止或重启 Runtime；
收到 `task.finished(status="cancelled")` 后才调度下一条消息。多轮业务对话上下文仍由
Agent Core 的 Blackboard 维护；TUI Conversation 只是当前进程的 UI 投影。
指定已有 `--session-id` 时，TUI 会在进入 Ready 前通过 Gateway 恢复新格式 Session 的公共会话
记录，包括用户消息、助手文本、完整 thinking、Tool 安全预览、追加内容、错误和中断终态。旧 Session
不从 Trace 迁移历史，只从升级后产生的新任务开始显示可恢复记录。

图片 Marker 可以和文字一起编辑。提交时只发送草稿中仍然存在完整 Marker 的图片，并按 Marker
第一次出现的顺序建立附件映射；删除 Marker 会让对应图片退出本次提交。图片写入
`$ICARUS_DATA_DIR/incoming/` 并只通过 ResourceRef 提交，Runtime 接受任务并复制到 Session Asset 后
删除暂存文件；失败或未确认请求保留文件用于重试。

## 离线查看实际界面

无需 Gateway、API Key 或真实模型：

```bash
# 从仓库根运行真实 Textual；输入任意文字并 Enter 后播放示例，输入 / 查看菜单
./apps/tui/scripts/start.sh --preview
```

该入口使用明确标记的离线数据，不写业务 Session。当前根 `icarus tui` 仍检查 Gateway 依赖是否就绪，
因此 Gateway 未启动时不要用 `icarus tui --preview`；离线预览直接用上面的 App 脚本。
正式会话使用 `icarus start gateway` 后的 `icarus tui`。

捕获真实 App 的欢迎、对话、详情、命令菜单四种画面（临时 SVG，不是快照基线）：

```bash
apps/tui/.venv/bin/python apps/tui/scripts/capture_preview.py \
  --out /tmp/icarus-tui-preview --width 120 --height 40
```

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

视觉回归由 `test/screens/`、`test/widgets/` 的 Pilot 测试和 replay 覆盖。当前不维护 SVG 快照基线。

## 结构

```text
apps/tui/src/
├── main.py                    # 参数、配置、具体依赖组装
├── app.py                     # 应用级 Worker、串行结果应用、导航与最终退出
├── configs/config.py          # 不可变配置
├── client/gateway/            # 按服务对象组织的网络客户端、协议端口与订阅
├── services/
│   ├── chat/                  # Session、操作执行、派发、更新顺序与状态规则
│   └── attachments.py         # 系统读取、图片暂存、ResourceRef、释放
├── domain/                    # 队列状态机、提交对象、命令规则，无 UI/磁盘 IO
├── stores/                    # ChatStore 与唯一 ConversationProjection
├── viewmodels/chat.py         # 只读状态/底部元信息显示值
├── screens/chat/              # 保留的 ChatScreen 与显式 Message
├── screens/session_picker.py  # 纯 Session 选择弹窗
├── widgets/chat/              # 编辑、输入组合、对话视口、消息、执行区、轨道
├── event_pipeline/            # RuntimeUpdate → UiAction
├── platform/clipboard.py      # 操作系统差异适配
├── tools/                     # 离线 replay、transcript、预览数据
└── styles/                    # tokens/base + screens + widgets 的 TCSS
```

业务变更在 App 消息循环调用 ChatSession 后写入 Store；I/O Worker 只产生捕获身份的结果。
Screen 读取投影增量或有界窗口快照，不再持有另一份对话事实；ViewModel 不发请求。
页面内草稿、光标、滚动和动画继续由 Widget 拥有。App 不使用魔法属性/query/消息代理。
TCSS 的变量通过 App.get_css_variables 显式提供给各文件，规则按页面/组件限定。
导入方向由 `test/test_layering.py` 检查；测试目录镜像 `client/gateway`、`domain`、`services/chat`、
`stores`、`screens/chat`、`widgets/chat` 和 `tools`。

## 文档

架构设计和实施计划按功能聚合在 `apps/tui/docs/spec/YYYY-MM-DD-<feature>/`。架构文档以当前
源代码和测试为事实依据，用于描述架构与系统设计，不反向限制源代码演进。

聊天工作台改版文档：
[目标设计](docs/spec/2026-10-27-tui-chat-workspace/design.md) ·
[实施总计划](docs/spec/2026-10-27-tui-chat-workspace/plan.md) ·
[HTML 交互原型](docs/spec/2026-10-27-tui-chat-workspace/assets/demo.html)。
当前实现结构见[架构说明](docs/spec/2026-10-27-tui-chat-workspace/arch.md)；HTML 只用于布局对照，真实界面通过离线预览启动。
