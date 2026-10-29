# TUI Desktop Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 macOS / Windows / Linux 桌面本机与 WSL 提供五项一致操作：图片粘贴、系统文本复制/粘贴/输入框剪切、点击网址打开浏览器。

**Architecture:** 平台差异收在 `platform/`（剪贴板读写 + URL 打开）；I/O 走 App Worker + 普通 service，结果经 Message 串行回填；选区与编辑由 Widget/Screen 负责，`Ctrl+C` 按选区→中断分级。跨消息选区先做隔离 spike（Task 1），以其实测结论决定原生 API 或稳定端点适配。

**Tech Stack:** Python 3.12、Textual 8.2.8（`>=8.2.8,<9`）、ctypes（Win32）、子进程工具（`wl-clipboard`/`xclip`/`pbcopy`/`pbpaste`/WSL 宿主辅助脚本）、pytest + Pilot。

**Spec:** `apps/tui/docs/spec/2026-10-28-tui-desktop-integration/design.md`（本文从该设计出发，执行者须同时阅读）

## 执行状态（2026-10-29）

平台阶段及后续桌面 UI 开发均已在本地 `feat/tui` 实现，当前处于最终回归与用户实机验收交接。
**Task 8 的真实桌面验收由用户保留；不把自动化测试写成实机通过。**

- Task 1：原 spike 错误推断已更正；以实际 Pilot 拖选、渲染文字、流式及历史窗口语义测试重新验证，采用局部稳定端点/片段快照。
- Task 2：跨平台类型化系统剪贴板、单文件图片受控快照、限额/资源释放与平台审查修复已完成。
- Task 3：DesktopService 线程 I/O、串行写入、取消后排空、URL 校验、App 生命周期已接线。
- Task 4：聊天正文选择、阅读顺序拼接、排除装饰/折叠内容、跨窗口/流式稳定、高亮重映射、键盘选择与 Ctrl+C 优先复制已实现。
- Task 5：Composer 复制与安全剪切快照、写入成功后删除/撤销、编辑/提交/会话/选区过期保护已实现。
- Task 6：用户/助手裸 URL 与 Markdown 目标、代码排除、点击/拖选/双击仲裁、键盘消息→URL picker、平台宿主打开已接线。
- Task 7：App 完成消息、失败提示、关闭排空、欢迎说明、架构/验证记录已完成；原四态中断和 slash Escape 行为保留。
- Task 8：真实 Windows Terminal/外部编辑器/系统截图/宿主浏览器验收 **等待用户执行**，各平台条目仍是“未验证”；本地离线终端驱动和宽/窄/短实际 App capture 已运行。
- 最终完整回归：**501 passed（173.10s）**；平台/service/新桌面交互组合 **176 passed**，含反向鼠标跨窗扩选与展开工具链接更新。compileall / diff check 通过。真实条件与证据见 [validation.md](validation.md)。
- 依赖：app-owned `Pillow>=11.3,<13`，已获安装授权。WSL URL 使用固定 PowerShell + UTF-8 stdin，不经 cmd 解释 URL。
- Git：agent 已按授权合入远端 feature；当前本地 feat/tui 吸收该基线。**用户于 2026-10-29 授权本地提交 TUI 修正与新交互；不推送、不合入 feature，真实使用验收仍待用户完成。**
- 平台历史分段清单见 [plan-platform.md](plan-platform.md)，当前源码架构见 [arch.md](arch.md)。


## 开发交付清单

- [x] Task 1：替换错误 spike 推断，重新验证正文选择/流式/窗口/链接交互，采用稳定端点适配。
- [x] Task 2：跨平台剪贴板类型化读写、文件优先级、图片校验、资源释放。
- [x] Task 3：线程化桌面 service、URL 校验、写入顺序与关闭排空。
- [x] Task 4：聊天选择、阅读顺序、中文/emoji、跨窗口和流式稳定、复制优先级。
- [x] Task 5：输入框复制与写入成功后安全剪切、撤销、过期结果拒绝、系统/终端粘贴分离。
- [x] Task 6：用户/助手/展开正文 URL、代码排除、点击/双击/拖选仲裁、键盘 picker 与宿主打开。
- [x] Task 7：App 消息/Worker、通知、完整关闭、帮助、架构记录与独立审查修复。
- [ ] Task 8：按用户要求保留真实桌面使用验收；不在自动化完成标记中冒充实机通过。

以下逐步任务为原实施模板，保留原始文件/命令与约定便于追溯；当前交付以本节及执行状态/validation 为准。
原模板的 Git commit 步骤不覆盖仓库授权规则；本轮仅按用户 2026-10-29 的明确授权本地提交，不 push。

## Global Constraints

- 范围：macOS、Windows、Linux 本机、WSL；SSH 不支持；单次粘贴仅一张图片。
- 所有平台判断、桌面/WSL 探测只存在于 `platform/`；service 无 Textual import；Widget 不 import `client/gateway`、services、app。
- 复制/剪切目标是真实系统剪贴板：禁止用应用内部缓存或 OSC 52 发送成功冒充系统写入成功。
- 剪切：捕获快照 → 系统写入成功 → 快照未变 → 才删除；写入失败原文保留；无选区不剪整行；聊天区不剪切。
- `Ctrl+C`：有有效选区只复制（失败也不落入中断）；无选区保持 `domain/chat_state.py:interrupt_action` 现有四态行为，不新增双击退出。
- 图片：仅 PNG/JPEG 文件格式，DIB/TIFF 剪贴板表示转 PNG；文件引用优先于图片表示，图片优先于文本；多文件整体拒绝；单张编码 ≤20 MiB、解码 ≤40,000,000 像素（实施中若发现更严下游约束，先改 design.md 再编码）；文件引用内容快照进受控资源目录，不修改源文件。
- 粘贴：终端 bracketed Paste 事件直接插入文字，不读系统剪贴板；应用粘贴动作读系统剪贴板；两者不重复插入；结果沿用草稿 token/会话 generation 校验。
- URL：仅 `http`/`https`，拒绝其他 scheme 与控制字符；不拼 shell 命令；代码块不 linkify；WSL 打开 Windows 宿主浏览器。
- 系统调用不在 Textual 消息处理函数内同步阻塞；后台线程不得直接改 Widget；关闭时排空真实读取并释放临时文件。
- 提交拆分按任务；实现与其测试同 commit；不混入无关重构；文档单独提交。每任务提交前跑该任务测试 + 相关套件，最后统一 `git diff --check`。

## Review Focus

- **CJK/emoji 宽字符选区**：拖选跨中文与 emoji 时，复制结果必须是完整字符而非按 cell 切断——Task 4 用 CJK+emoji 文本断言逐字符完整。
- **后台迟到结果**：复制/剪切完成前用户已改输入或切会话，迟到的删除/插入必须被拒——Task 5（剪切快照校验）、Task 7（token/generation 拦截，回归 `test_final_review_regressions.py` 既有用例）。
- **流式期间选区**：流式 delta 持续追加时选择聊天文本，选区与阅读位置不得被冲掉——Task 1 spike 记录结论；修复落在 Task 4。
- **剪贴板忙碌**：Windows 其他程序占用剪贴板时读取/写入须有限重试后明确失败，而非异常崩溃——Task 2。
- **链接拖选误开**：从链接上开始拖选/双击选择不得打开浏览器——Task 4。

---

### Task 1: 跨消息选区 Spike（可丢弃验证）

**Files:**
- Create: `apps/tui/docs/spec/2026-10-28-tui-desktop-integration/spike-selection-findings.md`
- Create: `apps/tui/test/spikes/test_selection_spike.py`（验证后删除或保留为回归，由结论决定）

**Interfaces:**
- Consumes: 现有 `ConversationView`（`apps/tui/src/widgets/chat/conversation.py`）、`StreamingMarkdown`（`apps/tui/src/widgets/chat/markdown.py`）、Textual 8.2.8 `Screen.selections` / `Screen.get_selected_text()`。
- Produces: spike 结论文档，回答 Task 4 的一个分叉——原生 Screen 选区在「跨 24 轮窗口切换」「流式末块替换」「CJK 文本提取」下是否达标；不达标时记录需要哪些稳定端点（消息身份 + 文字偏移映射），Task 4 按此实现。

- [ ] **Step 1: 写 spike 测试**：Pilot 测试覆盖 design §5.1 的六个场景——跨用户/助手消息拖选、CJK+emoji 提取、流式 append 后选区保留、Markdown 末块替换后提取、`page_up` 跨 24 轮窗口切换后选区存活、从链接 widget 起拖不触发 `LinkClicked`。每个场景断言 `get_selected_text()` 的具体期望文本。

- [ ] **Step 2: 运行并记录**：跑测试，逐场景记 pass/fail 与实际行为到 `spike-selection-findings.md`（含复现步骤）。

- [ ] **Step 3: 得出分叉结论**：文档末尾明确写「原生选区足够」或「需稳定端点，映射方案为 <消息身份, 文字偏移>」。此结论是 Task 4 的输入。

- [ ] **Step 4: Commit**

```bash
git add apps/tui/docs/spec/2026-10-28-tui-desktop-integration/spike-selection-findings.md apps/tui/test/spikes/
git commit -m "docs(tui): record cross-message selection spike findings"
```

### Task 2: platform 剪贴板读写与内容识别

**Files:**
- Modify: `apps/tui/src/platform/clipboard.py`
- Create: `apps/tui/src/platform/host.py`（WSL 探测 + 宿主互操作入口，供 clipboard/opener 共用）
- Test: `apps/tui/test/platform/test_clipboard.py`（扩展）、`apps/tui/test/platform/test_host.py`

**Interfaces:**
- Consumes: 现有 `read_clipboard_text() -> str | None`、`read_clipboard_image() -> ClipboardImage | None`、`ClipboardImage(data, media_type, extension)`、`ClipboardImageReadError`。
- Produces（Task 3/4/6 依赖）:
  - `class ClipboardContent`: frozen dataclass，字段 `kind: Literal["text", "image", "file"]`、`text: str | None = None`、`image: ClipboardImage | None = None`、`files: tuple[Path, ...] = ()`。
  - `class ClipboardUnavailableError(RuntimeError)`：平台能力缺失/工具不可用/超时。
  - `read_clipboard_content() -> ClipboardContent`（同步，由 service 放线程）；多文件或混合内容抛 `ClipboardUnavailableError("multiple files")` 等。
  - `write_clipboard_text(text: str) -> None`：系统写入失败抛异常，成功返回 None。
  - `read_clipboard_text/image` 保留现有签名，内部复用新实现。

- [ ] **Step 1: 写失败测试**（`test_clipboard.py` 扩展 + `test_host.py` 新建，全部 monkeypatch `subprocess`/`ctypes`，不碰真实系统）：
  - `test_read_content文件引用优先于图片和文本`：伪造 `powershell.exe`/`osascript`/`wl-paste -l` 输出单个 `.png` 文件引用 → `kind == "file"`。
  - `test_read_content多文件拒绝`：两个文件引用 → raises `ClipboardUnavailableError`，message 含 "multiple"。
  - `test_read_content图片表示选择`：wl-paste 提供 `image/png` + `image/jpeg` → 取 png，不算多图。
  - `test_read_content纯文本`：`pbpaste`/`xclip -o`/`powershell Get-Clipboard` stdout → `kind == "text"`。
  - `test_read_content空剪贴板与工具缺失区分`：exit 1 + empty stdout → kind text 空串或 `ClipboardUnavailableError("empty")`；`FileNotFoundError` → `ClipboardUnavailableError("tool")`。
  - `test_write_text各平台命令正确`：macOS `pbcopy`、X11 `xclip -selection clipboard`、Wayland `wl-copy`、Windows ctypes `SetClipboardData`、WSL 宿主脚本 stdin；断言命令 argv 与 stdin 数据，UTF-8 无 BOM。
  - `test_write_text失败不静默`：nonzero returncode → raises；exit 0 → None。
  - `test_host_is_wsl探测`：`/proc/version` 含 "microsoft" → True；不含 → False；文件不存在 → False（mock `Path.read_text`）。
  - `test_windows文本写入句柄释放`：参照现有 test_windows文本剪贴板读取并释放资源 的 SimpleNamespace 风格，断言 `GlobalUnlock`/`CloseClipboard` 在成功与异常路径都被调用。
  - `test图片dib转png`：CT 头部字节（宽高 2×3）经选定转换实现 → PNG 魔数头；损坏数据 raises。

- [ ] **Step 2: 运行验证失败**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/platform/ -q`
Expected: FAIL（`ClipboardContent` / `write_clipboard_text` / `test_host` 未定义）

- [ ] **Step 3: 实现**

`clipboard.py`：新增 `ClipboardContent`、`read_clipboard_content`（各平台分发：macOS Pasteboard via `osascript`、Windows ctypes 含 `CF_HDROP` 文件列表与 DIB→PNG、Linux 按 `host.py` 探测走 `wl-paste`/`xclip` TARGETS、WSL 走 `host.py` 宿主脚本）；`write_clipboard_text` 同样分发。DIB→PNG 用 stdlib `zlib`+`struct` 手写 PNG chunk 封装（无新依赖）；超过 40,000,000 像素先拒绝再转换。`host.py`：`is_wsl() -> bool`（读 `/proc/version`，模块级缓存）、宿主互操作命令构造（`powershell.exe` 绝对路径，数据经 stdin/参数传递，不拼图片名/文本进脚本字符串）。Windows 剪贴板打开忙碌时 5 次 × 10ms 重试后抛错。

- [ ] **Step 4: 运行验证通过**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/platform/ -q`
Expected: PASS（含既有 9 项不回归）

- [ ] **Step 5: Commit**

```bash
git add apps/tui/src/platform/ apps/tui/test/platform/
git commit -m "feat(tui): add cross-platform clipboard read/write and content typing"
```

### Task 3: 桌面 I/O service（复制写入 + URL 打开）

**Files:**
- Create: `apps/tui/src/services/desktop.py`
- Test: `apps/tui/test/services/test_desktop.py`

**Interfaces:**
- Consumes: Task 2 的 `write_clipboard_text(text) -> None`、`ClipboardUnavailableError`；Task 6 的 `open_url(url) -> None`（先以 `Callable[[str], None]` 注入，Task 6 实现真身）。
- Produces（Task 4/6/7 依赖）:
  - `class DesktopService`: `__init__(self, *, write_text: Callable[[str], None] = write_clipboard_text, open_url: Callable[[str], None] | None = None)`；`async def copy_text(self, text: str) -> None`（`asyncio.to_thread`，异常原样上抛）；`async def open_link(self, url: str) -> None`（校验 + 注入的 opener）。
  - `def validate_http_url(url: str) -> str`：仅 `http`/`https`，拒绝控制字符/空白/非有限 scheme，返回规范化 URL；非法 raises `ValueError`。
- 装配：`app.py` 构造 `self.desktop = DesktopService()`（open_link 的 opener 在 Task 6 接线）。

- [ ] **Step 1: 写失败测试**：`copy_text` 走线程不阻塞（注入记录调用的 fake）；注入 write 抛 `ClipboardUnavailableError` 时上抛；`validate_http_url` 接受 `https://example.com/路径?q=1`、拒绝 `javascript:alert(1)`、`file:///etc/passwd`、含 `\n`/`\x00` 的 URL、空串；URL 顺序参数不被 shell 解释（fake opener 记录 argv）。

- [ ] **Step 2: 运行验证失败**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_desktop.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `services/desktop.py`**（无 Textual import）

- [ ] **Step 4: 运行验证通过**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_desktop.py apps/tui/test/services/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/tui/src/services/desktop.py apps/tui/test/services/test_desktop.py apps/tui/src/app.py
git commit -m "feat(tui): add desktop service for clipboard write and url validation"
```

### Task 4: 聊天区选择与复制（按 spike 结论实现）

**Files:**
- Modify: `apps/tui/src/widgets/chat/conversation.py`、`apps/tui/src/widgets/chat/markdown.py`、`apps/tui/src/screens/chat/screen.py`、`apps/tui/src/screens/chat/messages.py`
- Test: `apps/tui/test/widgets/chat/test_conversation.py`（扩展）、`apps/tui/test/screens/chat/test_screen.py`（扩展）

**Interfaces:**
- Consumes: Task 1 spike 结论；Textual `Screen.get_selected_text()`/`selections`；Task 3 `DesktopService.copy_text`。
- Produces（Task 6 依赖）:
  - `ConversationView.has_selection() -> bool`、`get_selected_text() -> str | None`（None = 无有效选区）。
  - `ConversationView.action_cancel_selection()`（Escape 入口）。
  - `ChatScreen` 消息 `class CopyTextRequested(Message): text: str`（定义在 `screens/chat/messages.py`，App 侧 Task 6 消费）。
  - 有选择/拖选期间 ConversationView 暂停自动追尾与窗口回收（挂载中的 `_visible_turns` 裁剪与 `_page_window` 换窗 defer 到选区结束）。

- [ ] **Step 1: 写失败测试**：
  - `test跨消息拖选提取阅读顺序文本`：Pilot `mouse_down` 用户消息 → `mouse_move` 越过助手消息 → `mouse_up`；断言提取文本 == 两段正文以空行拼接，不含 `❯ You`/`✦ Icarus` 标签。
  - `test_cjk_emoji选区逐字符完整`：正文「中文😀测试」，拖选中段 → 复制结果不含 U+FFFD、字符数正确。
  - `test流式追加不扩选不冲掉`：开始选区后 `apply_action(AppendAssistantDelta(...))` → 选区文本不变，阅读位置不变（anchor 未跳）。
  - `test跨窗口翻页选区按spike结论处理`：向上翻过 24 轮窗口边界，选区起点文字仍可提取（原生不可行时按稳定端点映射，断言语义端点还原文本）。
  - `test链接处拖选不打开`：从 Markdown 链接 widget 起拖 → 无 `LinkClicked`、无 open 调用。
  - `test有聊天选区时_ctrl_c发复制不发中断`：建立选区 → `pilot.press("ctrl+c")` → 断言发出 `CopyTextRequested` 且无 `InterruptRequested`。
  - `test_escape取消选择`。
  - `test折叠内容不隐式复制`：Thinking 折叠时选区越过其标题 → 提取不含折叠正文。

- [ ] **Step 2: 运行验证失败**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/chat/test_conversation.py apps/tui/test/screens/chat/test_screen.py -q`
Expected: FAIL（新接口未定义）

- [ ] **Step 3: 实现**

原生选区足够（spike 结论 A）：`ConversationView` 包装 `get_selected_text`，过滤界面装饰（按 widget 类/`markup=False` 装饰节点排除），拼接消息间空行；拖选期间 defer 窗口回收与自动追尾。需稳定端点（结论 B）：在 ConversationView 维护 `<(turn_index, unit_key), start/end 文字偏移>` 语义选区，鼠标事件映射到挂载文本，跨窗口重挂载后按 `ConversationProjection` 快照重新映射提取；不建全文 Store。折叠块提取遵循 `ALLOW_SELECT` 现状。`ChatScreen.action_context_interrupt` 改为：composer 有选区 → composer 复制路径；否则 view `has_selection()` → 发 `CopyTextRequested`；两者皆无 → 现有 `interrupt_action` 四态不变。

- [ ] **Step 4: 运行验证通过 + 全量 TUI 套件**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/chat/test_conversation.py apps/tui/test/screens/ -q && make test-tui`
Expected: PASS（重点看既有 `test_streaming_markdown替换后旧节点不会触发鼠标选择崩溃` 不回归）

- [ ] **Step 5: Commit**

```bash
git add apps/tui/src/widgets/chat/ apps/tui/src/screens/chat/ apps/tui/test/widgets/chat/ apps/tui/test/screens/
git commit -m "feat(tui): add cross-message selection with copy-first ctrl+c"
```

### Task 5: Composer 复制、安全剪切与粘贴整合

**Files:**
- Modify: `apps/tui/src/widgets/chat/composer.py`
- Modify: `apps/tui/src/screens/chat/screen.py`
- Test: `apps/tui/test/widgets/chat/test_composer.py`（扩展）、`apps/tui/test/screens/chat/test_screen.py`（扩展）

**Interfaces:**
- Consumes: Task 3 `DesktopService.copy_text`；Task 2 `read_clipboard_content`（经既有 AttachmentsService/clipboard Operation 管线）；现有 `attach_image`、`deliver_clipboard`、`draft_token`。
- Produces（Task 6/7 依赖）:
  - `PersistentComposer.get_selection_text() -> str | None`（None = 无有效选区）。
  - `PersistentComposer.begin_cut() -> CutSnapshot | None`：无选区返回 None（不剪整行）；有选区返回 frozen dataclass `CutSnapshot(text: str, start: Location, end: Location, text_version: int, draft_token: int)`。
  - `PersistentComposer.apply_cut(snapshot: CutSnapshot) -> bool`：文字版本与 token 未变才经 `replace("")` 删除，可撤销；已变返回 False 不动文字。
  - `PersistentComposer.action_copy_selection()`：发 `class CopyTextRequested(Message): text: str`（composer 局部 Message）。
  - `deliver_clipboard` 扩展：`ClipboardContent.kind == "text"` 走现有 `events.Paste`；`"image"`/`"file"` 走 AttachmentsService 暂存 → `attach_image`；空/失败反馈（沿用现有 `Paste failed` notify 通道补文案）。

- [ ] **Step 1: 写失败测试**：
  - `test无选区ctrl_x不剪行`：光标在行中无选区按 `ctrl+x` → 文字不变。
  - `test剪切成功后删除且可撤销`：mock 写入成功 → 选区删除、`action_undo` 还原。
  - `test写入失败原文保留`：mock write raises → 文字与选区不变，notify 文案出现。
  - `test剪切期间编辑则不删除`：begin_cut 后改文字再 `apply_cut` → 返回 False、文字保留。
  - `test剪切期间提交清空草稿则不删除`：`clear_draft()` 后 apply_cut → False。
  - `test有输入框选区ctrl_c优先复制`：选区存在按 `ctrl+c` → 发 composer `CopyTextRequested`，无 `InterruptRequested`。
  - `test普通paste事件不触发系统读取`：`post_message(events.Paste("文本"))` → 系统读取 mock 计数为 0（守终端粘贴路径）。
  - `test应用粘贴文本替换当前选区`：经 `deliver_clipboard` 送 text → 选区被替换、光标正确、未提交。
  - `test应用粘贴图片落附件marker`：kind image → `[#imageN]` 插入、`_images` 增加、源路径内容不变。
  - `test多文件粘贴提示不支持`：kind file 多文件（由 platform 层保证不会到达；此处断言 service 异常路径的 notify 文案）。

- [ ] **Step 2: 运行验证失败**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/chat/test_composer.py apps/tui/test/screens/chat/test_screen.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

Composer：`_text_version` 计数器在每次 `TextArea.Changed` 递增；`ctrl+x`/`ctrl+c` 绑定改为上述快照流程（写入经 Screen → App Worker → DesktopService，完成回 Composer.apply_cut；快照携带 token/版本，迟到结果被拒）。`ctrl+v` 走现有 ImagePasteRequested → clipboard Operation 管线（不变）。Screen：`action_context_interrupt` 补 composer 选区分支（Task 4 已有 view 分支，此处补顺序：composer 选区 → 聊天选区 → 中断）。

- [ ] **Step 4: 运行验证通过**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/chat/ apps/tui/test/screens/ -q`
Expected: PASS（含既有 35 项 composer/clipboard 附件测试不回归）

- [ ] **Step 5: Commit**

```bash
git add apps/tui/src/widgets/chat/composer.py apps/tui/src/screens/chat/ apps/tui/test/
git commit -m "feat(tui): safe cut with system clipboard and unified paste delivery"
```

### Task 6: URL 打开（platform + 交互接线）

**Files:**
- Create: `apps/tui/src/platform/opener.py`
- Modify: `apps/tui/src/widgets/chat/markdown.py`、`apps/tui/src/widgets/chat/message_cards.py`、`apps/tui/src/app.py`
- Test: `apps/tui/test/platform/test_opener.py`、`apps/tui/test/widgets/chat/test_conversation.py`（扩展）

**Interfaces:**
- Consumes: Task 2 `host.py`（WSL 探测）；Task 3 `DesktopService.open_link`（本任务注入真 opener）。
- Produces:
  - `open_url(url: str) -> None`（`platform/opener.py`）：校验后按平台分发——macOS `subprocess.run(["open", url])`、Windows `os.startfile(url)`、Linux `xdg-open`、WSL `cmd.exe /c start`（经 `host.is_wsl()`，argv 列表传参）；失败 raises `OSError`。
  - `app.py` 装配：`DesktopService(open_url=open_url)`。
  - 消息/Markdown 侧：`StreamingMarkdown`/用户消息 Static 的裸 URL（http/https，非代码块）渲染为可点击 action；点击 → Screen → `App.on_copy_text_requested` 同层新增 `App` handler 调 `desktop.open_link`。

- [ ] **Step 1: 写失败测试**：
  - `test_open_url各平台命令`：monkeypatch `sys.platform`/`os.startfile`/`subprocess.run`，断言 argv（`["open", url]` / `["xdg-open", url]` / WSL `["cmd.exe", "/c", "start", url]`）且 `shell=False`。
  - `test_open_url拒绝非http`：`javascript:`/`file:` → raises `ValueError`（不触达 subprocess）。
  - `test_open_url失败上抛`：returncode 1 → raises `OSError`。
  - `test_wsl走宿主`：`is_wsl()` mock True → argv 含 `cmd.exe`。
  - `test裸url点击打开`：Pilot 构造含 `https://example.com` 的助手消息 → click → App `open_link` fake 收到该 URL。
  - `test代码块url不linkify`：代码块内 URL click → 无 open 调用。
  - `test键盘打开链接入口`：触发「打开链接」action（键位实现时避开现有绑定确定，写入 welcome/帮助文案）→ 列出当前消息链接（fake picker 或直接选中第一个断言调用）。
  - `test拖选从链接开始不打开`（Task 4 已测行为在此回归接线）。

- [ ] **Step 2: 运行验证失败**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/platform/test_opener.py apps/tui/test/widgets/chat/test_conversation.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`opener.py`：复用 Task 3 `validate_http_url`（移到 platform 或经参数传入，保持 platform 无 service import——实现取 platform 本地校验函数、service 侧改为 re-export）。Markdown/Static 裸 URL：在 `_CJKMarkdownParagraph`/用户消息 Content 生成处用正则识别 http(s) URL（排除 fence 块），注入 `@click` action；action 路由到 Screen 请求。`app.py` 接线 `DesktopService(open_url=open_url)`，open 失败走现有 notify。

- [ ] **Step 4: 运行验证通过**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/platform/ apps/tui/test/widgets/chat/ apps/tui/test/services/ -q && make test-tui`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/tui/src/platform/opener.py apps/tui/src/ apps/tui/test/
git commit -m "feat(tui): click-to-open http(s) links with host browser routing"
```

### Task 7: App 接线收尾、通知与文档

**Files:**
- Modify: `apps/tui/src/app.py`、`apps/tui/src/widgets/chat/welcome.py`、`apps/tui/docs/spec/2026-10-28-tui-desktop-integration/design.md`
- Test: `apps/tui/test/test_final_review_regressions.py`（扩展）、`apps/tui/test/screens/chat/test_integration.py`（扩展）

**Interfaces:**
- Consumes: Task 3–6 全部产出。
- Produces: 完整用户路径；design.md 状态行更新为已实现并按实际代码修订偏差。

- [ ] **Step 1: 写失败测试**：
  - `test复制请求串行且迟到失败不覆盖新写入`：两次 copy 请求，第一次迟到失败/成功均不影响第二次结果（fake 记录调用序）。
  - `test关闭时排空clipboard读取`：回归既有 `test_cancelled_clipboard_read_is_drained_before_close` 语义扩展到 write 请求。
  - `test_paste_failed通知保留`：clipboard Operation 失败仍走现有 notify（回归）。
  - `testctrl_c全矩阵`：composer 选区/聊天选区/无选区有草稿/无选区有待发/无选区活动任务/空闲退出——六态各断言（无选区四态断言 `interrupt_action` 返回值，选区态断言复制消息）。
  - `test欢迎提示包含新操作说明`：welcome 渲染含 Ctrl+C 复制/剪切仅输入框/终端粘贴说明。
  - `test临时文件释放`：粘贴图片后 cancel 提交 → `AttachmentsService.release` 被调、文件删除。

- [ ] **Step 2: 运行验证失败**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/test_final_review_regressions.py apps/tui/test/screens/chat/test_integration.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**：welcome/帮助文案；app.py 层面的串行与关闭排空复核（若 Task 3 已满足仅补测试）；design.md「状态与文档定位」改为已实现记录、修订与实际不符处；新增 `apps/tui/docs/spec/2026-10-28-tui-desktop-integration/arch.md`（按 AGENTS.md 从实现代码记录）。

- [ ] **Step 4: 全量验证**

Run: `make test-tui && apps/tui/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test && git diff --check`
Expected: PASS / exit 0

- [ ] **Step 5: Commit**

```bash
git add apps/tui/src/ apps/tui/test/ apps/tui/docs/spec/2026-10-28-tui-desktop-integration/
git commit -m "docs(tui): finalize desktop integration wiring, notifications and spec records"
```

### Task 8: WSL 实机验收（手动）

**Files:**
- Create: `apps/tui/docs/spec/2026-10-28-tui-desktop-integration/validation.md`

**Interfaces:**
- Consumes: Task 1–7 完成、`./apps/tui/scripts/start.sh` 可运行。
- Produces: design §9.3 验收矩阵的 WSL 行实测记录；其余平台条目标「未验证」。

- [ ] **Step 1: 按 design §9.3 逐项实测**（WSL + Windows Terminal）：外部粘贴多行文本、截图/浏览器图片/单文件图片粘贴、跨消息复制到 Windows 记事本、剪切+外贴+撤销、Windows 宿主浏览器打开链接、流式期间选区、Ctrl+C 全矩阵、退出资源清理。记录终端名与设置。

- [ ] **Step 2: 记录 validation.md**：通过/失败/未验证三态，失败项回 Task 归属修复。

- [ ] **Step 3: Commit**

```bash
git add apps/tui/docs/spec/2026-10-28-tui-desktop-integration/validation.md
git commit -m "docs(tui): record wsl desktop integration acceptance results"
```
