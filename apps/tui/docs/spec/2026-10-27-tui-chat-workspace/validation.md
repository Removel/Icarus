# TUI Chat Workspace Validation｜实施与实机验收记录

日期：2026-10-27。实现基线 `94c7d22`，工作分支 `feat/tui`。本次改动未提交、未推送；用户原有 `apps/agent/settings.json`、agent spec 和 `.claude/` 内容不属于本次改动。

## 1. 自动验证

最后一次完整 `make test-tui`：**353 passed in 133.78s**。该脚本也执行 compileall；另跑 `python -m compileall -q apps/tui/src apps/tui/test packages` 和 `git diff --check`，通过。

基线完整运行：332 passed。最终数量不是简单相加：原 Service 测试迁移到实际生产 prepare/execute/complete/dispatch/update/attachments 接口，去掉了不再存在的测试专用生产入口；既有关键业务场景仍保留。窗口、文本、队列、协议、剪贴板、命令和页面状态测试目录已随代码归位。

重点覆盖：

- sequence gap/重复、聚合历史跳号、early updates、完整文本与 delta 对账；
- submit/steer、原目标与 submission_id 精确重试、FIFO/LIFO/取消、资源拒绝；
- 同 Session/task 的旧 epoch 状态结果、旧候选与慢清理；
- App/页面分离、后台更新不额外挂载 Markdown、原 Composer/草稿保留；
- 菜单键盘与鼠标只提交一次、Escape/Tab/换行、五尺寸下输入/菜单/metadata 几何；
- 工具局部滚轮不移动正文、超长文本有界视口、Thinking 折叠/完成/展开保留；
- IO 线程取消后排空、只释放 owned 文件、draft lifetime 和 Session generation 交付校验。

## 2. 独立 review 与修复

一次独立整体 review 发现 5 个 Important 问题，全部以真实 owner 的回归测试复现后修复；没有把“已有测试绿”当作反驳依据：

| 问题 | 复现测试 | 修复 |
| --- | --- | --- |
| advisory task-status 查询失败使正常 stream 永久 fatal | `test_advisory_status_failure_does_not_freeze_accepted_task` | 查询失败不改变权威任务/stream 状态。 |
| 无变更跨页返回也重建正文，丢外层/内层位置 | `test_navigation_without_changes_preserves_inner_and_outer_offsets`；背景补更另测 `test_background_inner_scroll`、`test_background_anchor` | 无变更保留节点；真正需要 snapshot 时保存/恢复各单元偏移，在 LocalScroll 最终布局尺寸更新时应用待恢复值，抑制竞争跟随。 |
| 渲染 await 中切页，排队的 suspend 标记太晚 | `test_slow_mount_does_not_render_hidden_markdown_or_ack_revision` | 有效性直接检查 `app.screen is chat_screen`；挂载入口/后续步骤/确认 revision 前检查真实可见 owner。 |
| snapshot 重建将已关闭执行段登记成当前 open card | `test_snapshot_closed_card_does_not_absorb_next_tool` | projection 记录执行段关闭/segment；closed card 不登记为当前可追加执行段。 |
| 延迟图片在跨 Session 后附到新草稿 | `test_deferred_clipboard_image_cannot_cross_session_generation` | deferred clipboard 保存 generation，再次交付校验；Session 变化使 draft lifetime 失效并释放过期 owned 图片。 |

五项最初回归运行 **5 failed**，修复后 **5 passed**；补充有后台更新的阅读位置测试后，最终完整套件 353 passed。

本次 review 未认证 native Windows/macOS IME 或剪贴板，这些不以 Linux 测试假装覆盖。

## 3. 启动的是真实 Textual，不是 HTML 重画

### 原生终端

使用真实 tmux 120×40 启动，实际键入草稿后 Enter：

```bash
./apps/tui/scripts/start.sh --preview
```

确认出现已完成 Assistant、六条工具记录、Thinking、顶层用户纠偏、任务状态、持久 Composer 和 metadata。再键入 `/`，真实 Textual 展开下方菜单。

另运行 `scripts/replay_events.py --tui-real` 并提交消息，确认完整离线 fixture；并修复该工具原 fixture scope 与客户端 scope 不同导致数据被合法过滤的问题（`test_replay_updates_use_the_public_clients_adopted_scope` RED→GREEN）。

### 五尺寸真实渲染

`capture_preview.py` 实际启动同一个 App、输入文字、等待离线 task 完成、展开工具/Thinking、输入 `/` 并正常退出。分别执行：

| 终端尺寸 | 状态 |
| --- | --- |
| 120×40 | welcome/chat/details/commands 全部已捕获 |
| 100×32 | 同上 |
| 70×24 | 同上 |
| 58×24 | 同上 |
| 100×12 | 同上；菜单减少可见行，保留正文/输入/metadata |

共 20 个 Textual/Rich SVG，另转 PNG 供浏览器查看。人工查看宽屏 welcome/chat/commands、短屏 commands 和并排对照页。字体转换使用安装的中英文字体并去除 Rich SVG 的 textLength 间距强制，以避免浏览器将 CJK 压叠；原生 tmux 中中文正常，原始 SVG 保留。PNG 不是像素级 Windows Terminal 兼容承诺。

临时证据位于 `.superpowers/sdd/plan-2026-10-27-tui-chat-workspace/preview-*` 与 `review/`（gitignored，不作为快照基线提交）。已打开本机 `http://localhost:8877/`，左侧为已认可 HTML demo，右侧为真实 Textual PNG，可切换三状态/详情和五尺寸。仅绑定 127.0.0.1。

## 4. 与目标 demo 的对比

已实现：同屏 Hero 自然上移；独立 Assistant 文本；工具动态区域默认折叠、局部滚动；Thinking 动画/展开/完成；纠偏顶层用户呈现；InputDock 内 status → queue → Composer → slash menu → metadata；右侧轮次导航；轻量 New messages；没有原型外层预览按钮进入产品。

明确差异：

- 终端按行/cell 布局，非浏览器像素布局；边框、字形和可见内容比例不承诺逐像素相同。
- 模型、思考等级、当前上下文缺公共来源，显示 `未知`/`—`；最新版本 `未检查`。原型 demo-model/24k 仅保留在明确的 HTML 演示中。
- 其他业务页面未创建，跨页能力用真实测试 Screen 验证，不能当作已有设置页。
- Windows/Linux 图片剪贴板未新增，native 平台 IME/键盘/系统读取仍需在对应平台进一步验收。

## 5. 用户可直接启动

### 离线实际界面

从仓库根：

```bash
./apps/tui/scripts/start.sh --preview
```

输入任意文字并 Enter 等待示例；输入 `/` 查看指令；Ctrl+C 按草稿/队列/取消/退出优先级操作。只提供一个离线演示任务，耗尽后再次提交会提示 replay 用尽；可退出重开，不伪装成真实模型。

### 正式连接

```bash
icarus start gateway
icarus tui
```

当前根 `icarus tui` 控制器在启动前检查 Gateway 就绪。实际尝试 `./bin/icarus tui --preview` 被 `tui -> gateway: not ready` 拦截，因此没有把它写成可无服务启动的命令，也没有越界修改根基础设施。

### 重新生成证据

```bash
apps/tui/.venv/bin/python apps/tui/scripts/capture_preview.py --out /tmp/icarus-tui-preview --width 120 --height 40
```

## 6. 实施裁定与保留限制

- 在用户指定的现有 feat/tui 工作区原地实现；未擅建 branch/worktree、merge、commit、push。无关改动保留。
- Textual 多文件 TCSS 变量不是自动跨文件共享；由 App.get_css_variables 显式注入中心 tokens 文件，未在各文件重复颜色定义。
- 网络串行状态和异步 UI 渲染解耦后，渲染失败可能晚于下一条 steer 的权威接受；测试验证已接受消息不重入队、未确认消息保留 reservation，而不是要求网络等绘制结束。
- LocalScroll 是用户/Assistant 真实共用的局部滚轮与偏移恢复实现，不新增通用动画/路由/DI 框架。
- 增加 TUI CLI 的 `--preview` 是验收所需的明确离线模式，不改变根控制器 readiness 规则。
- 使用已有项目环境中的 Ruff 进行格式/import 检查，没有安装新的运行依赖；psutil 不加入。
- 两个阶段在过渡中有协调迁移（移除旧代理与引入真实 UI 需要同时改测试），所有阶段目标以最终实测为准，不宣称每个中途 checkpoint 已独立完成。
- 原始三张图未复制进仓库；复制请求被权限规则拒绝，未绕过。已认可 HTML 原型和目标条款已保存在 feature 文档。

未延期的 reviewer minor：无。未实测的平台能力如上明确列出；不以不必要的第二次 review 替代新增回归测试与全量门禁。

## 后续用户校正：Run 中心的执行记录（2026-10-27）

本节取代上文第一轮实现中的“Assistant 超长正文局部滚动/多个执行段”约定：AI 消息全文展开、不设内部高度/滚动限制；每轮 Run 一个固定高度执行记录（普通 8 行、short 3 行），有滑块默认最新；当前最大 step 的 Thinking 位于记录边框下，旧 Thinking 归档进同一记录列表。中途/未完成 Assistant 同样完整保留在外层，不再降级成 progress。原型中错误的局部滚动理解不能作为实现依据。新增 test_run_execution_layout.py 三项最初 3 failed，修正后通过；test_run_thinking_archive.py 验证固定高度/跟随/滑块、旧 step 迟到 complete 与当前 Thinking 不混淆。最终本次完整结果另记。

本次校正最终门禁：**358 passed in 131.38s**；五尺寸共 20 张真实 Textual 画面已重新生成，验收页右侧已刷新为 Run 中心布局。Assistant 无内部滚动和高度上限；执行记录普通固定 8 行/short 3 行，最新 Thinking 在边框下，旧 step 可在滚动记录中展开。未提交/未推送。

## 连续更新闪黑与重建中断（2026-10-27）

连续多个修订到达时，旧实现只保留 last_change，页面跳号便隐藏、清空并重建全部对话控件；重建中的提前返回没有恢复 visibility。新增回归最初复现了消息控件被替换、后台积压后消息被替换，以及中断重建时控件保持隐藏三项失败。随后补充多轮积压的导航测试，发现按最终 turn_count 给每条消息编号会将不同问题绑定到同一轮，已改为按 task_id 定位。

ChatStore 现在有界保留最近 512 次变更（包括纯状态修订），同 generation 按序补齐；reset、缓存过期和未完成渲染仍使用窗口快照。快照重建由 App.batch_update 保持上一帧，不再隐藏对话区。原有历史原子展示测试改为检查批量抑制绘制；用户消息故障注入改为拦截真实增量 append 入口，而非依赖不必要的快照重建。

首轮专项 **8 passed**，首轮 `make test-tui` **364 passed in 128.72s**（含 compileall）。后续独立复核新增两项 RED 测试，分别确认导航快照已消费的 delta 被重复追加，以及取消部分增量更新后重试重复追加；修复为共享 render_lock、window_generation 失效校准和 await 前置 dirty 标记。全量回归进一步发现快照重建缺失 detached 状态，补齐后“回到最新”行为恢复。最终专项 **10 passed**，完整 `make test-tui` **366 passed in 127.77s**（含 compileall），staged/unstaged `git diff --check` 通过。真实 App 离线回放在 120×40、100×32、70×24、58×24、100×12 生成各四张临时 SVG；浏览器 SVG 转换有中文字体压缩，不能将其作为终端中文排版结论。tmux 100×32 原生启动、提交 hello、完成离线回放、PageUp/Ctrl+End 和 slash 菜单已实际驱动并检查 capture-pane；中文正文在原生输出中完整。没有调用真实模型，没有修改“执行记录”样式。代码与测试提交为 `82acd54`；按用户确认另行提交文档，不加 coauthor，不推送。
