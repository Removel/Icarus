# Desktop Integration 验证记录

首次日期：2026-10-28；更新：2026-10-29。环境：Linux / WSL2，Python 3.12，Textual 8.2.8。

## 自动化

| 验证 | 结果 | 边界 |
| --- | --- | --- |
| `python -m pytest apps/tui/test/platform/ apps/tui/test/services/ apps/tui/test/test_desktop_lifecycle.py -q` | 151 passed | OS API/桌面工具/PowerShell 调用使用替身；不等同真实桌面验收。PNG 内容解码使用真实 Pillow。 |
| `make test-tui` | **501 passed，173.10s** | 整个 TUI 套件，包含实际 Pilot 中文拖选、跨窗口键盘/鼠标扩选、复制/安全剪切、用户/助手/展开工具网址、单击/双击/拖选和 picker；并非真实 OS 桌面验收。 |
| 平台/service/新桌面交互组合 | **176 passed，28.98s** | 平台、资源、剪贴板序列化、Composer、selection、links 和 App/Screen 路由。 |
| layering/import 边界 | **3 passed** | 无新增 Widget→service/client/app 或 service/platform→Textual 反向依赖。 |
| `python -m compileall -q apps/tui/src apps/tui/test` | 通过 | 语法编译，不代表宿主脚本实测。 |
| 固定 WSL 剪贴板 / opener 脚本经 Windows PowerShell AST Parser | 两段均 exit 0 / syntax OK | 仅解析语法，不执行脚本，不读取/写入剪贴板，不唤起浏览器。 |
| `git diff --check` | 通过 | 工作区 whitespace 检查。 |

## 独立审查修复

一次独立只读审查返回原生平台问题；本轮已用先失败后通过的回归覆盖并修复：
macOS `$.NSURL` bridge 引用、转换前编码/像素上限与有界子进程输出，Windows 写剪贴板的有效 owner 窗口，
Linux 后台 clipboard daemon 继承管道、JPEG/文本 MIME 选择、连接失败与 X11 回退，
WSL 错误分类、取消 close 后再次排空，以及常见 32-bit BI_BITFIELDS DIB。

Node 严格 bridge 替身实际执行 JXA 固定源码，不能替代 macOS AppKit 实机验收。
DIB 仍非任意 BMP 格式通用解码器：不支持的位深/压缩明确转为平台错误，不退化为文本。
网络 UNC 若不能经 wslpath 访问则明确报错，不声称已经建立网络共享桥接。


## UI 审查与真实离线终端

- 第二次独立审查聚焦桌面 UI，确认阅读顺序与折叠选区两项问题。已先复现失败，修复为身份与顺序分离、折叠失效与卸载保留分开，再通过专项和完整套件。
- 额外复现并修复：Escape 不得抢占 slash menu；reload 同样文字仍使 cut 版本失效；live assistant 无 RunCard 时重挂不丢语义高亮。
- 真实 tmux 终端运行 `./apps/tui/scripts/start.sh --preview`（100×32），提交离线预览问题，看到完成回复、工具/Thinking 记录，操作 slash/Escape，并经 Ctrl+C 退出；没有调用模型/Gateway。
- 实际 App capture 在 120×40、60×24、100×12 三尺寸均完成提交、详情展开、菜单与关闭，检查输入框/底栏未越界。临时 SVG 帧保存在本 plan 执行目录，不新增持久 SVG 基线。已查看真实 tmux 文本帧；环境无 SVG rasterizer，未声称逐像素图像审阅。

## 实机矩阵

macOS、Windows 原生、Linux Wayland、Linux X11、WSL+Windows Terminal 全部为 **未验证**。
本次没有读取用户真实剪贴板、覆盖剪贴板、唤起浏览器或模拟外部编辑器粘贴。
WSL 内能够运行命令不代表剪贴板互操作验收通过。

完整 design §9.3 的跨消息选择、Ctrl+C/Ctrl+X、链接 UI 已实现并通过本地自动化；
用户明确保留真实使用验收，因此 Task 8 对外编辑器外贴/截图/宿主浏览器等项目仍等待用户。

### 用户验收路径

1. 启动正常 TUI；中文/emoji 跨用户与助手正文拖选，Ctrl+C 后在外部编辑器粘贴，检查空行/字符完整。
2. Ctrl+Shift+S 进入聊天键盘选择，Shift+方向键/翻页扩展至更早窗口，Ctrl+C 外贴；Escape 取消。
3. 输入框选区 Ctrl+X，确认写入系统后删除，可撤销；无选区不剪整行；期间继续编辑不误删新输入。
4. Ctrl+V/右键读取系统文本/截图/复制单图片文件；终端 Paste 只插入一次；多文件应明确拒绝。
5. 单击正文网址，在默认浏览器打开；拖选或双击网址不打开。Ctrl+Shift+O 按消息→URL 选择目标。
6. 有选区 Ctrl+C 只复制；无选区仍按清草稿→恢复待发→取消任务→空闲退出；退出恢复终端。

用户于 2026-10-29 明确授权本地 commit；验收前不推送 TUI 或合入 feature。

## 后续实机验收准备（尚未执行）

- macOS：使用系统自带 osascript / AppKit / pbpaste / pbcopy，记录终端实际传入 Ctrl+V 的方式。
- Windows：使用原生 Python / Win32 剪贴板 API，另以占用剪贴板场景检查有限重试和失败反馈。
- Linux Wayland：在真实 WAYLAND_DISPLAY 会话中准备 wl-clipboard；X11 在 DISPLAY 会话中准备 xclip。工具安装不由应用自动执行。
- WSL：确认 Windows PowerShell 与 WSL interop 可用，wslpath 能解析盘符与 UNC 路径；记录 Windows Terminal 的按键/右键行为。
- 各平台先保存任何需要保留的剪贴板内容，再人工测试文本/截图/浏览器复制图片/单图片文件/多文件；确认外贴成功、源文件未改、受控副本释放。
- 宿主浏览器请求是外部副作用，待用户在真实桌面明确选择测试目标后进行；不得以 AST parser 的 syntax OK 替代真实打开。
- 记录通过/失败/未验证三态，不把 headless Pilot 或 monkeypatch 结果填入实机通过栏。

## 2026-10-29 折叠摘要点击崩溃回归

- `feat/tui` 已 rebase 同步 `origin/feature=ba0dd68`；隔离环境Textual 8.2.8，与截图所在原环境一致。
- 同步后迁移前基线：`make test-tui` **513 passed，191.17s**。旧文档中的快照失败记录不作为当前失败基线。
- 确认根因：重绘前 compositor 仍可能命中已卸载的 `DisclosureSummary`（parent=None）；Static默认允许文本选择，Screen在MouseDown的选择初始化里读取container.region而崩溃。真实App/process内复现与截图同一行AttributeError，无hit-test替身。
- 修复限定为摘要控件 `ALLOW_SELECT=False`，不改Textual依赖/私有实现，不全局禁用正文选择；点击和Enter/Space切换保留。
- 新回归在 `App.batch_update()` 内卸载真实摘要并在旧帧坐标投递MouseDown，修前 **1 failed / 3 passed**（相同AttributeError），修后文件 **4 passed**；宽100×32、窄短60×12验证点击及键盘折叠。
- 摘要回归 + 正文跨消息/中文emoji拖选/跨窗口选择聚焦组合：**13 passed，21.41s**。此次不改变布局，不恢复SVG基线；未进行真实Gateway/模型或宿主剪贴板调用。
- 修后 `make test-tui` **516 passed，191.67s**；`compileall apps/tui/src apps/tui/test packages`、`git diff --check`通过。原process内复现脚本再次执行exit0，仍命中parent=None的旧摘要，但不再启动选择或崩溃。本修复尚未提交/推送。

## 2026-10-29 导航与流式响应性回归

- 用户报告：右侧输入轮次轨道不跟随正文、长内容卡顿、第一Thinking后延迟/批量展示、流式输出时拖滑块卡顿。
- 已确认存在24轮窗口化，而非段落级可见区虚拟化；35轮真实App探针中滚到窗口顶部时可见11/12轮，旧current/rail仍34；跳0后rail仍34。现通过滚动/布局变化消息同步，显式jump停止follow且布局后定位。
- 性能诊断（本地合成数据，无Gateway/模型）：23轮×30段+当前Thinking，868 DOM节点、300条折叠Thinking追赶，原3.942s且UI计时器无法推进；仅禁用无效选区捕获的进程内对照0.046s。修复前无选区也逐delta扫描完整DOM，这是主要已证实瓶颈，不归因网络。
- 修复：空选区早退、ChatChanged连续合并而不合并业务更新、revision/generation/window/chrome均不变的刷新早退、取消每delta全DOM滚动偏移恢复、每32个按序动作让出事件循环。最新相同规模探针0.0361s，首个UI回调0.0024s；数值为本机证据，不承诺跨环境固定时延。
- 历史分页由整24轮重建改为复用16轮重叠、只装卸进入/离开8轮；真实App保留身份16/16，24轮上限不变，局部探针含一次Pilot刷新0.793s。锚点依layout-refresh后的虚拟位置恢复，不用旧屏幕坐标。
- 回归先失败再通过：rail实际滚动、空选区扫描、空转chrome、128动作公平性、100通知合并、overlap身份/阅读offset。已有取消追赶/选择/跨窗口/源文本完整性测试保留。
- 独立审查两项Important已复现并修复：取消中的shift留下卸载引用（WidgetError）、旧分页anchor覆盖新jump。新增取消后再分页/立即jump测试修前2 failed，修后核心组合19 passed。
- 中间全量3 failed/519 passed（chrome直接赋值、历史tail rail、fatal状态）已修复；后续全量523 passed（188.10s）。审查修复后最终 `make test-tui` **526 passed，192.95s**，compileall/diff及文档链接检查通过。
- 实际离线App capture在100×32和60×12完成welcome/chat/details/commands及关闭，未建SVG基线；无可用SVG rasterizer，未声称逐像素审图。正文选择和键盘折叠已通过Pilot实测。
- 未新增单条长回复段落级虚拟化：窗口内长Markdown仍完整解析/挂载，极长单轮成本仍为限制，不以隐藏正文或截断规避。

## 依赖安装

Pillow 是 TUI app-owned 显式依赖；用户明确授权安装到 `apps/tui/.venv`。
首次 wheel 下载因 SHA-256 不匹配被 pip 拒绝；核对 PyPI 元数据后，无缓存重试完整下载，
pip 保持哈希验证并成功安装 Pillow 12.3.0。未关闭或绕过包完整性校验。
