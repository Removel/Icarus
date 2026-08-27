# TUI Clipboard Image Paste Development Plan｜TUI 剪贴板图片粘贴实施计划

## 目标与依据

依据 `apps/tui/docs/arch/tui-clipboard-image-paste-design.md`，实现 macOS 下：

```text
Ctrl+V 图片 → Composer [#imageN] → TUI PendingMessage
→ AgentRuntimeService.submit(prompt, input_images)
```

本计划只修改 `apps/tui/`，复用已经完成的 Agent Runtime 本地图片导入、Session Asset 和 Provider
转换能力。第一版不实现 Windows/Linux、文件选择器、图片缩略图或 `/image` 命令。

## 实施原则

- 先补确定性单元测试，再接真实 macOS 系统调用；
- 平台判断只存在于 `read_clipboard_image()`；
- 同步系统调用通过 Worker 或 `asyncio.to_thread()` 离开 Textual 事件循环；
- `[#imageN]` 只属于 TUI 草稿语法，不修改 Agent 层；
- 文本和图片必须作为同一个 PendingMessage 排队、提交和撤回；
- App 临时目录存活到 TUI 退出，不在 Runtime 接收后立即删除；
- 普通文本 Paste Event 与当前 TextArea 剪贴板语义保持兼容；
- 不增加 Clipboard Backend/Reader 类层级。

## 当前进度

| 模块 | 状态 |
|---|---|
| AgentRuntimeService 图片入口 | 已完成 |
| macOS 剪贴板读取 | 已完成 |
| Composer 图片草稿 | 已完成 |
| ChatState 图片队列 | 已完成 |
| App 提交与临时文件生命周期 | 已完成 |
| 测试与文档收口 | 已完成 |
| 真实 macOS 终端图片验收 | 待手工验证（`TUI-19`） |

自动化验证结果：

- TUI：`120 passed`，其中 `9 snapshots passed`；
- Agent application：`24 passed`；
- `compileall` 和 `git diff --check`：通过；
- macOS 固定系统脚本已完成无图片场景冒烟，未改写用户剪贴板构造实图样本。

## 实施顺序

```text
剪贴板数据类型与平台函数
→ Composer Marker 与附件映射
→ ChatState PendingMessage
→ App Worker、临时文件与 Runtime submit
→ Queue/Conversation/提示文案
→ Pilot、Snapshot、真实 macOS 验证
```

## 任务一：实现统一剪贴板图片函数

**新增文件**

- `apps/tui/src/clipboard.py`
- `apps/tui/test/test_clipboard.py`

**开发内容**

- 定义 ClipboardImage 和 ClipboardImageReadError；
- 实现 `read_clipboard_image()`，按 `sys.platform` 选择私有平台函数；
- 第一版只为 `darwin` 调用 `_read_macos_clipboard_image()`，其他平台返回 None；
- 使用固定 osascript/JXA 脚本读取 NSPasteboard PNG、JPEG 或 TIFF；
- TIFF 使用系统能力转换为 PNG；
- 校验返回 media_type、扩展名、非空 bytes 和 Base64；
- 系统命令设置短超时，stderr 不直接进入用户 Prompt 或 Event。

**测试内容**

- monkeypatch 平台值验证路由；
- monkeypatch subprocess 验证 PNG/JPEG/TIFF、空剪贴板、超时、非零退出和非法 Base64；
- 断言命令参数来自固定脚本，不包含用户草稿或路径。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_clipboard.py -q
```

## 任务二：为 Composer 增加图片草稿

**更新文件**

- `apps/tui/src/widgets/composer.py`
- `apps/tui/test/widgets/test_composer.py`

**开发内容**

- 定义 DraftImage 与 PendingMessage，或从 TUI 公共类型模块导入；
- PersistentComposer 维护当前 DraftImage 映射和 next image number；
- 增加 `attach_image(path)`，按 TextArea selection 替换规则插入 `[#imageN]`；
- 覆盖 Ctrl+V action，只发布图片粘贴请求，不在 Widget 内调用系统命令；
- 增加调用父 TextArea 文本 paste 的明确回退方法；
- `submit()` 按 Marker 第一次出现顺序构造 PendingMessage；
- 重复 Marker 去重，缺失 Marker 的附件不提交；
- 允许只有有效图片 Marker 的草稿提交；
- clear_draft 与 restore_draft 同时处理文字、附件和下一个编号。

**测试内容**

- 光标插入、选区替换、连续编号和删除后不重排；
- 删除/破坏 Marker、重复 Marker、手工未知 Marker；
- image-only 提交；
- clear/restore；
- 普通 Paste Event 与无图片回退。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/widgets/test_composer.py -q
```

## 任务三：升级 ChatState 本地队列

**更新文件**

- `apps/tui/src/chat_state.py`
- `apps/tui/src/widgets/queue_panel.py`
- `apps/tui/test/test_chat_state.py`
- `apps/tui/test/widgets/test_queue_panel.py`

**开发内容**

- 把 `pending: deque[str]` 改成 `deque[PendingMessage]`；
- `enqueue(text, images)` 创建不可变队列项；
- begin_dispatch、accept_dispatch 和 pop_pending_tail 返回完整 PendingMessage；
- `pending_messages` 返回只读 PendingMessage 元组；
- `pending_items` 保持字符串投影，供 QueuePanel 生成预览并原样显示 Marker；
- Ctrl+C 的草稿判断改为 Composer `has_draft`，避免孤立附件被忽略。

**测试内容**

- STARTING/READY/RUNNING 阶段排队不丢附件；
- FIFO 提交、LIFO 撤回恢复完整图片映射；
- 提交失败保留同一个 PendingMessage；
- QueuePanel 不显示临时路径。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/tui/test/test_chat_state.py \
  apps/tui/test/widgets/test_queue_panel.py -q
```

## 任务四：接入 App Worker、临时文件和 Runtime submit

**更新文件**

- `apps/tui/src/app.py`
- `apps/tui/test/test_app.py`

**开发内容**

- App 按需创建 TemporaryDirectory 并在统一 cleanup 中关闭；
- 处理 Composer 图片粘贴请求，通过 `asyncio.to_thread(read_clipboard_image)` 读取；
- 同一时刻只允许一个剪贴板图片读取 Worker；
- 读到图片后以唯一文件名写入临时目录并 chmod 0600，再调用 attach_image；
- 返回 None 时调用 Composer 原有文本 paste；
- ClipboardImageReadError 或写入失败只显示非致命通知，不进入 fatal latch；
- on Submitted 将完整 PendingMessage enqueue；
- `_dispatch_next()` 生成模型 Prompt 与图片 Path 顺序，调用现有：

  ```python
  await service.submit(prompt=model_prompt, input_images=image_paths)
  ```

- submit 成功后 Conversation 只显示 PendingMessage.text；
- submit 失败仍保留队首完整内容；
- Ctrl+C 恢复 PendingMessage 到 Composer；
- 退出时在 Runtime 停止后或 finally 中清理临时目录。

**Prompt 生成规则**

- 文字与 Marker 原文保留；
- 追加 `<attached_images>` 映射块；
- 图片列表与映射按 Marker 第一次出现顺序一致；
- image-only 使用“请分析所附图片。”默认文本；
- 未知 Marker 不访问文件。

**测试内容**

- 图片读取不阻塞 App 继续处理事件；
- 成功粘贴、没有图片、读取失败和临时文件写入失败；
- Runtime 尚在 STARTING 时图片消息完整排队；
- Runtime submit 参数、Conversation 显示和 QueuePanel 显示；
- submit 失败与 Ctrl+C 撤回；
- 图片 Task failed 后继续调度下一项；
- shutdown 与 Clipboard Worker 竞争时不泄漏临时目录。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_app.py -q
```

## 任务五：交互提示、Snapshot 与真实终端验证

**更新文件**

- `apps/tui/src/widgets/messages.py`
- `apps/tui/test/test_app_snapshots.py`
- 必要的 Snapshot SVG；
- `docs/todo/tui.md`。

**开发内容**

- Welcome Help 增加 `Ctrl+V image` 提示；
- 增加包含 `[#image1]` 的 Composer/Queue Snapshot；
- 不引入缩略图、终端图片协议或新的大面积视觉样式；
- 更新 TUI-15 状态与后续 Windows/Linux 边界。

**真实 macOS 验证（待手工完成，跟踪项 `TUI-19`）**

1. 截图复制到系统剪贴板，Ctrl+V 后出现 `[#image1]`；
2. 浏览器复制图片，Ctrl+V 后图片可提交；
3. 复制普通文本，原文本粘贴行为保持；
4. 连续粘贴两张图片并调整 Marker 顺序，Runtime 收到一致顺序；
5. 删除 Marker、清空草稿、撤回队列和重新提交；
6. 在图片提交前删除 TUI 临时文件，得到可见失败且后续队列继续；
7. 退出后临时目录清理。

**检查点**

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
```

## 最终验证

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/application -q
apps/agent/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test
git diff --check
```

## 完成标准

- macOS Composer 中 Ctrl+V 图片生成 `[#imageN]`；
- 图片与文本在草稿、排队、撤回和提交中保持同一生命周期；
- Agent Runtime 收到映射后的 Prompt 和正确顺序的图片路径；
- Conversation、QueuePanel、Event 和 Trace 不暴露临时绝对路径或图片二进制；
- 普通文本粘贴与现有 Ctrl+C/队列语义不回归；
- Windows/Linux 未实现时不影响纯文本 TUI，后续只扩展统一函数内部平台分支；
- TUI 全量测试、相关 Agent Application 测试、compileall 与 diff check 全部通过；
- 真实 macOS 图片剪贴板交互按 `TUI-19` 单独验收。
