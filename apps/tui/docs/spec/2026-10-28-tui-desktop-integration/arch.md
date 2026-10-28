# TUI Desktop Integration 实现记录

首次设计日期：2026-10-28。实现记录更新：2026-10-29。源码和测试是本文件的依据。
本地开发完成不等同真实桌面验收；用户保留真实使用验收，TUI 改动未合入远端 feature。

## 职责与依赖

- `platform/host.py` 收口 WSL / Linux 桌面探测、工具定位、固定 PowerShell argv 与 UTF-8 stdin、wslpath 映射。
- `platform/clipboard.py` 同步读取类型化 `ClipboardContent`，系统写文本；文件→图片→文字优先级，多文件整体拒绝。
- `platform/opener.py` 校验结构有效的 HTTP(S) URL，拒绝空白/控制字符/非法 host 或 port，然后委托系统默认关联。
- `services/attachments.py` 在工作线程中只读取一次类型化快照，校验真实图片，保存受控副本，沿用身份与临时文件释放生命周期。
- `services/desktop.py` 在线程中写系统剪贴板和打开网址。真实写入串行化；调用方或 close 等待者取消不放弃后台操作；关闭排空。
- `PersistentComposer` 捕获 `CutSnapshot(text,start,end,text_version,draft_token)`；edit/load/undo/redo 同步推进版本，写入成功且快照仍匹配才通过 TextArea replace 删除，可撤销。
- `ConversationView` 拥有局部语义选区，不写 ChatState；`selection.py` 保存已选渲染文字片段与字符端点，不建立第二份全文 Store。
- `LinkText` / `StreamingMarkdown` 产生链接意图，不直接调 OS。代码不自动 linkify；点击延迟至 Textual 双击窗口结束，双击/拖选不打开。
- `ChatScreen` 判断选区拥有者、优先复制、取消、键盘模式、两阶段 message/URL picker，接收完成消息应用剪切。
- `App` 管理 I/O Worker 和串行 Message 完成投递，装配 service/platform；关闭时排空真实系统调用。

方向为 `App -> service -> platform`、`Screen -> Widget`。platform/service 不依赖 Textual，Widget 不 import services/client/app。

## 选区与流式/窗口化

Screen 原生选择负责可见交互与高亮；局部适配读取渲染 Content 的字符偏移，不按 cell 切字符串、不从 Markdown 源码截取。
局部身份由 turn、单元类型及其同类序号、Thinking step / Tool call_id、正文块序号构成；阅读排序独立保存，不能用身份字符串排序。
仅保留选择范围片段。Window remount 后重新映射仍挂载的高亮；卸载范围的选中文本保留，折叠的已选正文则丢弃。

原生 selection 变更采用响应式 watch，不周期扫描整个 DOM。流式/窗口替换前捕获，替换后重映射。
有选择或正在拖选时停止追尾，达到 24 轮时转 detached，不以无限增大 DOM 规避窗口要求。
普通翻页/键盘扩选允许切换窗口；主动会话切换、轮次跳转、离开页面和 Escape 取消选择。

已验证中文/emoji 实际拖选、跨消息空行拼接、强调渲染后的正文、流式不扩选/不跳阅读位置、
55 轮历史跨多个窗口键盘扩选（始终 24 轮挂载）、RunCard 重挂映射、工具/Thinking 阅读顺序、折叠选中文本失效。

## 输入与完成链

`Ctrl+C`：输入框非空范围 → 系统复制；否则聊天有效范围 → 系统复制；否则原四态中断（清草稿/恢复待发/取消活动/退出）。
空白选区同样有效；写入失败不能落入中断。输入框新选择/编辑取消旧聊天选择，聊天选择启动清输入框范围。

剪切路径是 Composer 意图 → Screen 请求 → App Worker → DesktopService → DesktopCompleted → Screen/Composer。
完成检查 generation、活动页面、token、编辑版本、范围及所选文字；过期结果只提示已复制但未剪切。
无输入框选区不剪行；聊天区不剪切；失败保留文字与范围。

应用 Ctrl+V/右键走系统类型化读取与既有草稿 token/generation 管线；终端 Paste 直接插文字，不触发系统读取。

## 平台与 URL

| 平台 | 剪贴板读取 | 文本写入 / URL |
| --- | --- | --- |
| macOS | JXA 文件引用、AppKit PNG/JPEG/TIFF→PNG、pbpaste | pbcopy / open |
| Windows | CF_HDROP、注册 PNG、DIB、CF_UNICODETEXT | 有效隐藏 HWND owner / 系统 URL association |
| Linux Wayland | 显式 URI/image/text MIME | wl-copy（不捕获 daemon 继承管道）/ xdg-open |
| Linux X11 | TARGETS、URI、PNG/JPEG、显式文本 target | xclip / xdg-open |
| WSL | 固定 STA PowerShell JSON/base64，文件经 wslpath | UTF-8 stdin 固定脚本 / Start-Process |

PNG/JPEG 校验用 app-owned Pillow；编码 ≤20 MiB、解码 ≤40,000,000 像素。非压缩 24/32-bit DIB 用 stdlib 转 PNG，
常见 BI_BITFIELDS 用 Pillow 处理掩码，其他表示明确拒绝。macOS 转换/编码前检查限额，子进程响应有界。
WSL URL 不经 cmd /c start，避免元字符解释；OS 接受不等于页面加载成功。

## 可发现键位

- Ctrl+V：系统文字/图片粘贴；终端 Paste 独立插入文字。
- Ctrl+C：选区复制优先，否则原中断。
- Ctrl+X：仅输入框范围，成功写系统后再剪切。
- Ctrl+Shift+S：当前阅读区域进入键盘聊天选择；方向键移动，Shift+方向键扩选，翻页跨窗，Escape 退出。
- Ctrl+Shift+O：选择当前阅读区域的消息，再选择 URL。
- Escape 优先保持既有斜杠菜单关闭行为，不清草稿。

## 验证边界

自动化、离线真实 TUI 终端和宽/窄/短实际 App 帧记录见 `validation.md`。
真实 macOS/Windows/Linux/WSL 剪贴板外贴、宿主浏览器与具体终端截键行为等待用户验收，不能以 mock/Pilot 代替。
