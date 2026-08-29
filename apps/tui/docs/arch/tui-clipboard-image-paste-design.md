# TUI Clipboard Image Paste Design｜TUI 剪贴板图片粘贴设计

> 本文记录剪贴板读取和 Composer Marker 的已实现基础。提交链已从本地 Path 直传迁移为
> `$ICARUS_DATA_DIR/incoming/` ResourceRef，经 Gateway 交给 AgentRuntime，并在返回 task_id 前导入
> Session Asset。

## 1. 背景

### 1.1 核心结论

Icarus Agent Runtime 已支持通过
`AgentRuntimeService.submit(prompt, input_images)` 接收本地图片路径，并在 UserInputPlugin 中将图片
导入当前 Session `assets/`。本设计解决 TUI 原先只维护纯文本草稿和 `deque[str]` 待发送队列、
无法从输入框使用底层图片能力的问题。

本次在 TUI 增加以下最小产品入口：

```text
复制一张图片
→ 在 Composer 中按 Ctrl+V
→ 光标处出现 [#image1]
→ Enter 后文字和图片一起进入本地待发送队列
→ 调用 AgentRuntimeService.submit(prompt, input_images)
```

剪贴板读取使用一个统一功能函数按当前平台分发，不增加 Backend、Reader、Provider 或 Manager
抽象。第一版只实现 macOS；Windows 和 Linux 只保留函数分支位置，不创建无调用方的空类。

### 1.2 现有基础

- `PersistentComposer` 已拥有光标、选择区、清空与草稿恢复能力；
- `ChatState` 已拥有 TUI 本地 FIFO 队列、单活动任务和 LIFO 撤回语义；
- `IcarusTextualApp._dispatch_next()` 是唯一 Runtime 提交位置；
- `AgentRuntimeService.submit()` 已接受 `list[ImagePart | str | Path]`；
- UserInputPlugin 在 Task 开始时把本地路径转换为 Session Asset；
- OpenAI Adapter 将 Asset 转换为 Data URL，Anthropic Adapter 将其转换为 Base64 source。

### 1.3 实施前缺口

| 位置 | 当前状态 | 缺口 |
|---|---|---|
| Composer | `Submitted` 只包含 `text` | 无法保存图片与占位符映射 |
| ChatState | `pending: deque[str]` | 图片无法跟随对应待发送消息 |
| App | 只调用 `service.submit(prompt=prompt)` | 没有传递 `input_images` |
| Textual Paste | `Ctrl+V` 默认读取应用内文本剪贴板 | 终端不会把系统图片二进制作为 Paste Event 交给 TextArea |
| 平台能力 | 没有系统剪贴板图片读取函数 | macOS 图片无法进入草稿 |

### 1.4 当前实现状态

设计已经落地：TUI 通过统一 `read_clipboard_image()` 读取 macOS 剪贴板，Composer、ChatState 和
Runtime 提交链路使用同一个 `PendingMessage` 保存文字与图片。PNG/JPEG 可直接读取，TIFF 在固定
JXA 脚本内转换为 PNG；读取通过后台线程执行，临时文件以 `0600` 权限保存并在 TUI 退出后清理。

自动化验证覆盖 Clipboard、Composer、队列、App 提交、错误回退和视觉快照。真实 macOS 终端中
复制系统截图和浏览器图片的交互验收仍单独记录在 `docs/todo/tui.md` 的 `TUI-19`。Windows/Linux
仍属于后续平台扩展。

## 2. 对标与方案取舍

| 方案 | 用户交互 | 实现特点 | 结论 |
|---|---|---|---|
| `/image <path>` 命令 | 需要先得到文件路径 | 跨平台直接，但不像聊天产品，用户操作较重 | 不采用 |
| 原生文件选择器 | 点击或快捷键选择文件 | 依赖 GUI 和各平台桌面能力，不适合纯终端与 SSH | 不采用 |
| `Ctrl+V` 图片附件 | 复制图片后直接粘贴 | 最符合聊天习惯；仅剪贴板读取部分具有平台差异 | 采用 |
| 在终端内渲染缩略图 | 输入框直接显示图片 | 依赖 Kitty/iTerm2 等终端协议，兼容面窄 | 本阶段不做 |

已采用方向借鉴聊天 Composer 的“可见引用 + 结构化附件”模式，但不把图片二进制或终端渲染协议
放入 TextArea、ChatState 或 Agent Context。TUI 中的 `[#imageN]` 只是一条草稿内的可编辑引用。

## 3. 目标与非目标

### 3.1 目标

- macOS 用户在 Composer 聚焦时按 `Ctrl+V` 可以附加剪贴板中的一张图片；
- 图片在光标或当前选择区位置显示为稳定的 `[#imageN]`；
- 文字、图片映射和队列项具有同一生命周期；
- 撤回待发送消息时同时恢复文字和图片引用；
- Agent 收到清晰的占位符映射文本和按相同顺序排列的结构化图片；
- 普通文本粘贴、Textual 应用内复制粘贴和既有输入行为不回归；
- 平台差异限制在统一功能函数内部，TUI 上层不判断 macOS。

### 3.2 非目标

本阶段不实现：

- Windows Clipboard API、Wayland、X11 或 WSL 图片读取；
- `/image` 命令、文件选择器、拖拽文件或 Finder 文件列表粘贴；
- Kitty、iTerm2、Sixel 等终端图片预览协议；
- 图片裁剪、压缩、编辑、OCR 或上传进度；
- WebUI 的真实缩略图展示；
- 将 `[#imageN]` 语法下沉到 Agent Kernel 或 Provider Adapter。

## 4. 方案

### 4.1 全景流程

```text
macOS NSPasteboard
        │ Ctrl+V
        ▼
read_clipboard_image()
        │ ClipboardImage(data, media_type, extension)
        ▼
IcarusTextualApp 临时目录
        │ 0600 临时图片文件
        ▼
PersistentComposer
  ├─ 草稿文本：比较 [#image1] 和 [#image2]
  └─ 引用映射：image1 → path1, image2 → path2
        │ Enter
        ▼
ChatState PendingMessage
  ├─ text
  └─ images
        │ FIFO dispatch
        ▼
AgentRuntimeService.submit(model_prompt, image_paths)
        │
        ▼
UserInputPlugin → Session assets/ → Blackboard → ReActAgent
```

### 4.2 剪贴板功能入口

新增单文件：

```text
apps/tui/src/clipboard.py
```

公开给 TUI 内部使用的统一函数：

```python
@dataclass(frozen=True)
class ClipboardImage:
    data: bytes
    media_type: str
    extension: str


class ClipboardImageReadError(RuntimeError):
    pass


def read_clipboard_image() -> ClipboardImage | None:
    if sys.platform == "darwin":
        return _read_macos_clipboard_image()

    # 后续在这里增加 win32 与 Linux 分支。
    return None
```

返回语义：

- `ClipboardImage`：剪贴板中存在可读取图片；
- `None`：剪贴板中没有图片，或当前平台尚未支持图片读取；调用方继续走 Textual 现有文本
  `action_paste()`，不显示错误；
- `ClipboardImageReadError`：当前平台支持读取，但本次系统调用或图片转换失败。

不建立 `ClipboardBackend`、`ClipboardReader` 或平台类层级。只有在 Windows/Linux 真实实现使单文件
明显膨胀后，再把私有平台函数移动到子模块。

### 4.3 macOS 读取

`_read_macos_clipboard_image()` 使用系统已有的 `osascript` 查询 NSPasteboard，不增加 PyObjC 等
Python 依赖。固定脚本按顺序读取：

1. PNG；
2. JPEG；
3. TIFF。

TIFF 使用 macOS 系统能力转换为 PNG，使返回格式保持在 Agent Runtime 已支持的
PNG/JPEG/GIF/WebP 范围内。系统脚本是代码内固定内容，不拼接用户输入；调用设置短超时并限制为
一次图片读取。没有图片是正常的 `None`，命令失败、无效 Base64 或转换失败才抛领域错误。

该同步函数不能直接阻塞 Textual 事件循环。App 使用 `asyncio.to_thread(read_clipboard_image)` 在后台
读取；同一时刻只允许一个图片粘贴 Worker，重复按键不创建并发系统剪贴板请求。

### 4.4 Composer 草稿模型

TUI 定义两个扁平数据类型：

```python
@dataclass(frozen=True)
class DraftImage:
    reference: str       # image1
    path: Path

    @property
    def marker(self) -> str:
        return f"[#{self.reference}]"


@dataclass(frozen=True)
class PendingMessage:
    text: str
    images: tuple[DraftImage, ...] = ()
```

`PersistentComposer` 维护当前草稿附件映射和单调递增编号。图片粘贴时：

- 用 App 已暂存的路径创建 `DraftImage(reference="imageN")`；
- 用 TextArea 当前 selection 替换语义在光标或选区处插入 `[#imageN]`；
- 删除已有图片时不重排后续编号；
- 新草稿从 `image1` 开始；
- 恢复队尾消息时保留原编号，并让下一编号大于已恢复的最大编号。

草稿文字是显示事实，附件映射是文件事实。提交时只选择文本中仍然存在完整 Marker 的图片：

- 手动删除 `[#image1]` 后，image1 不进入提交；
- 破坏或只删除 Marker 的部分字符，不再视为有效图片引用；
- 同一个 Marker 出现多次只提交一次图片；
- 图片顺序按 Marker 在文本中的第一次出现位置确定；
- 手工输入但没有映射的 `[#image99]` 只是普通文本，不尝试访问文件。

`PersistentComposer.Submitted` 改为携带 `PendingMessage`。`clear_draft()` 同时清空文字和图片映射；
`restore_draft()` 同时恢复二者。

### 4.5 Ctrl+V 与普通文本粘贴

Textual 当前有两种粘贴路径：

- `Ctrl+V` 调用 TextArea 的 `action_paste()`，默认读取 Textual 应用内文本剪贴板；
- 终端模拟器发送 bracketed paste 时产生 `events.Paste(text)`，由 TextArea 直接插入文本。

本次只覆盖 Composer 的 `Ctrl+V` action：

```text
Ctrl+V
→ 请求 App 异步读取系统剪贴板图片
→ 读到图片：暂存并 attach_image()
→ 没有图片：调用 TextArea 原有 action_paste()
→ 读取失败：保持草稿不变并显示非致命通知
```

`events.Paste(text)` 不修改，因此终端普通文本粘贴保持原行为。第一版对外承诺的图片快捷键是
`Ctrl+V`；macOS 终端是否把 `Command+V` 作为文本 Paste Event 发送，继续由终端自身决定。

### 4.6 TUI 临时文件生命周期

ClipboardImage 是内存数据，而 Agent Runtime 的正式入口接收 Path。`IcarusTextualApp` 按需创建一个
进程级 `TemporaryDirectory(prefix="icarus-tui-clipboard-")`：

- 每张剪贴板图片写入唯一文件名，文件权限为 `0600`；
- 路径只存在于 Composer、PendingMessage 和 Runtime submit 参数；
- 临时目录存活到 TUI 退出，不能在 `submit()` 返回后立即删除，因为 UserInputPlugin 在自己的
  Worker 中异步导入图片；
- 正常退出时统一清理；异常进程退出后的残留由系统临时目录清理策略兜底；
- 原始路径不显示给用户，也不写入 Prompt、Event 或 Conversation。

本阶段不新增 TUI AssetStore 或逐 Task 引用计数。

### 4.7 本地队列与撤回

ChatState 的队列从 `deque[str]` 改为 `deque[PendingMessage]`。公开交互保持扁平：

```python
state.enqueue(text, images=())
state.begin_dispatch() -> PendingMessage | None
state.accept_dispatch(task_id) -> PendingMessage
state.pop_pending_tail() -> PendingMessage | None
```

QueuePanel 仍以文本方式展示消息；`[#imageN]` 已经位于 `PendingMessage.text` 中，不需要单独图片
Widget。`Ctrl+C` 语义保持：

- Composer 有文字或图片：清空整个草稿；
- 待发送队列非空：撤回队尾 PendingMessage，并把文字与图片一起恢复到 Composer；
- 活动 Task 存在：取消活动任务；
- 均为空：退出。

### 4.8 提交给 Agent 的内容

TUI Conversation 和 QueuePanel 展示用户原始草稿：

```text
请比较 [#image1] 和 [#image2] 的页面布局
```

调度时生成模型 Prompt：

```text
请比较 [#image1] 和 [#image2] 的页面布局

<attached_images>
[#image1] 对应第 1 张附件图片
[#image2] 对应第 2 张附件图片
</attached_images>
```

并调用现有正式接口：

```python
await service.submit(
    prompt=model_prompt,
    input_images=[image1.path, image2.path],
)
```

如果草稿去掉有效 Marker 后只剩空白，模型 Prompt 使用：

```text
请分析所附图片。

<attached_images>
[#image1] 对应第 1 张附件图片
</attached_images>
```

Blackboard 随后用现有 `<user_request>` 包装该 Prompt，并把导入后的 ImagePart 追加为同一 User
Message 的结构化图片内容。Agent 不接触 macOS Clipboard、TUI 临时目录或 Marker 到 Path 的映射。

### 4.9 错误与反馈

| 场景 | 行为 | 草稿/队列影响 |
|---|---|---|
| 剪贴板没有图片 | 回退 Textual 原有文本粘贴 | 不改变图片映射 |
| 当前平台未支持 | 与没有图片相同，保留文本粘贴 | 不改变图片映射 |
| macOS 读取失败 | 非致命通知 `Clipboard image paste failed` | 草稿保持不变 |
| 临时文件写入失败 | 非致命通知 | 不插入 Marker |
| 用户删除 Marker | 提交时忽略对应附件 | 不阻止其他内容发送 |
| 排队期间临时文件丢失 | Runtime 返回 `image_import_failed` | 当前 Task failed，后续队列继续 |
| 连续 Ctrl+V | 已有读取 Worker 时忽略重复请求或显示轻量提示 | 不产生重复编号 |

剪贴板失败属于产品输入失败，不发布新的 Agent Event；只有图片已经进入正式 Runtime submit 后的
导入失败，才沿用现有 TaskErrorEvent。

### 4.10 兼容性

- `AgentRuntimeService.submit(prompt, input_images)` 不修改；
- Agent、Blackboard、Persistence 和 Provider 不增加 TUI Marker 语义；
- 无图片的 PendingMessage 与当前纯文本队列行为一致；
- ReplayRuntimeService 继续接受 `input_images` 参数，可以在 TUI 测试中记录但不读取真实剪贴板；
- macOS 以外平台仍可使用所有纯文本功能；
- 不要求安装新的 Python 包或系统包。

## 5. 自测

### 5.1 剪贴板函数

- 平台分发只在 `sys.platform == "darwin"` 时调用 macOS 私有函数；
- PNG、JPEG 和 TIFF→PNG 返回正确 media_type、extension 和 bytes；
- 没有图片返回 None；
- 系统命令失败、超时和非法输出转换为 ClipboardImageReadError；
- 固定脚本不拼接用户输入。

### 5.2 Composer

- Ctrl+V 图片在当前光标处或选择区插入 `[#imageN]`；
- 连续粘贴编号稳定，删除前一张不重排后一张；
- 删除 Marker 后提交不包含对应图片；
- 重复 Marker 只提交一次；
- 图片顺序按第一次出现位置；
- clear 和 restore 同时处理文字与附件；
- 普通 Textual Paste Event 和无图片 Ctrl+V 文本回退不回归。

### 5.3 队列与 App

- PendingMessage 在 STARTING、READY、RUNNING 三种阶段保持文字和图片；
- FIFO 提交与 LIFO 撤回恢复完整附件；
- Runtime submit 收到映射后的 Prompt 和正确路径顺序；
- Conversation 只显示原始草稿，不显示附加映射块或临时路径；
- 图片 Task failed 后可以继续调度下一条消息；
- TUI 退出后临时目录被清理。

### 5.4 回归

- Composer、ChatState、App Pilot、QueuePanel 和 Conversation 单元测试；
- 图片草稿与排队状态 Snapshot；
- macOS 真实终端手工验证 Ctrl+V 图片和普通文本粘贴；
- TUI 全量测试与 Agent Runtime 提交集成测试。

## 6. 里程碑

| 优先级 | 里程碑 | 完成标志 |
|---|---|---|
| P0 | macOS 剪贴板读取函数 | 能区分无图片、读取成功和读取失败 |
| P0 | Composer 图片草稿 | Ctrl+V 插入 Marker，clear/submit 行为正确 |
| P0 | PendingMessage 队列 | 图片随消息排队、撤回和恢复 |
| P0 | Runtime 提交闭环 | Prompt 映射与 Path 顺序正确，Session Asset 导入成功 |
| P1 | 产品反馈与回归 | 非致命错误反馈、真实终端验证和 Snapshot 完成 |

Windows 与 Linux 读取在后续真实平台需求中分别实现，不阻塞 macOS 首期。

## 7. 风险

| 风险 | 影响 | 规避方式 |
|---|---|---|
| 终端拦截 Ctrl+V | TUI 收不到按键 | 明确首期验证的终端；保留 bracketed text paste，不修改 Agent 接口 |
| macOS Clipboard 只提供 TIFF | 无法直接进入 Runtime 支持格式 | 使用系统能力转换为 PNG |
| 异步读取期间用户继续编辑 | Marker 插入位置可能变化 | 同时只读一次，并在完成时使用 Composer 当前 selection；读取不修改其他文字 |
| Marker 被局部编辑 | 映射与显示不一致 | 提交时仅识别已注册的完整 Marker，其他内容按普通文本处理 |
| 临时文件过早删除 | Runtime Worker 导入失败 | 临时目录保留到整个 TUI 生命周期结束 |
| Queue 仍只保存文本 | 撤回或延迟提交时丢图 | 将队列元素升级为 PendingMessage，禁止旁路保存 Path |
| 平台逻辑扩散 | 后续 Windows/Linux 改造 TUI 主流程 | 所有平台判断只留在 read_clipboard_image() |
