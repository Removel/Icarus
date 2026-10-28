# WebUI 性能改造验证

日期：2026-10-24（Asia/Shanghai）。基线：`44d4e51`，工作分支 `feat/webui`。
已 fetch 并确认该分支包含当时的 `upstream/feature` 与 `origin/feature`；没有创建分支、合并或推送。
验证完成后，经用户授权，将渲染与缓存优化、静态资源压缩及文档分别提交。

## 构建与资源

同一工作区、相同 Node/pnpm，修改前后运行 `pnpm build`。既有依赖版本未变，新增虚拟列表依赖并将已有 Markdown 解析依赖显式声明在聊天包。

| 指标 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 最大 JS chunk，未压缩 | 674.31 KB | 392.04 KB |
| ChatApp chunk，未压缩 | 606.11 KB | 341.53 KB |
| ChatApp chunk，构建报告 gzip | 187.48 KB | 约 106 KB |
| 聊天 CSS，未压缩 | 102.05 KB | 约 68 KB；公式/高亮 CSS 独立按需加载 |

生产服务器浏览器冷上下文实测：默认首页请求 5 个 JS，编码体积合计 260,975 B，
解码体积合计 897,609 B。具体资源为入口、ChatApp、Gateway、共享 Markdown 依赖、Select。
未请求 `mathRenderer`、`ChatCodeHighlight`、`MemoryApp`、`KnowledgeApp`、`EvidenceMap`。
此数据不包含 CSS、字体、HTML，也不是首字延迟或帧率测量。

额外的纯解析微基准：使用相同 unified/remark 语法配置，100 个标题、100 个完成段落，
初始 21,690 字符，随后追加 120 个尾部片段。3 轮耗时中位数：每次全篇解析 526 ms，
分块尾部解析 23 ms。该基准不包含 React、DOM、网络，也不覆盖引用定义等整篇解析回退路径，
不能解释为用户界面加速倍数。

## 针对性验证

环境：WebUI 自有 `.venv`、Playwright 1.58、系统 Edge，独立 Vite 端口 5179。
使用模拟 RPC/HTTP 服务，不调用真实模型或修改真实会话。

```powershell
$env:WEBUI_BASE_URL = 'http://127.0.0.1:5179'
$env:WEBUI_BROWSER_CHANNEL = 'msedge'
.venv/Scripts/python.exe -m pytest test/packages/chat_app/test_performance.py -q
.venv/Scripts/python.exe -m pytest test/server/test_server.py -q
```

- `test_performance.py`：10 项通过。覆盖冻结段落 DOM、最终更正、引用上下文、未闭合公式/表格、
  高亮延后、IDB 提前展示与权威校准、缓存超时熔断、删除缓存、79→80 行边界、240 行长会话的
  移动/桌面尺寸、未挂载目录跳转、阅读上文与回到底部。240 行场景初始挂载展示行少于 40。
- `test_server.py`：12 项通过。压缩 q 值、Brotli/gzip 解压逐字节一致、HEAD、鉴权、路径边界、
  HTTP/WebSocket 代理、缓存和首页资源请求均已验证。
- `pnpm typecheck`、`pnpm lint`、`pnpm build`、改动文件的 Prettier 检查通过。
- Python 测试目录 compileall 与 `git diff --check` 通过。

## 完整测试与既有问题

以下为性能轮次的历史记录，其中“既有”仅相对当时的本地 HEAD，并非 PR 的远端
`upstream/feature` 基线。2026-10-25 按 `04b203f` 复核后，夹具、滚动容器、提示断言及
DSWUI 文件格式问题均属于此次 PR 需要处理的范围，已在工作区修复；这些历史失败不再作为豁免。

完整 WebUI 套件首次结果：**239 passed、15 failed、13 skipped、2 errors**，共 267 项，
运行约 11 分 55 秒。完整原始报告保留在本地 `apps/webui/test-results/performance-full.xml`。
其中 1 个失败为主题事件尚未应用的测试时序问题；补充明确等待后，整个聊天外壳测试文件 **9 passed**。
其余 14 个失败均使用独立 HEAD 源码应用和相同解释器/浏览器对照复现，未顺带修复。

对照方式：`git archive HEAD apps/webui` 提取到系统临时目录，工作区源码不回退；
workspace 包指向临时目录内的原始源码，既有第三方依赖复用相同版本。
临时基线 Vite 使用 5181 端口；生命周期测试还提取 HEAD 的 `scripts/icarus/environment.py`。

| 既有失败组 | 数量 | 对照结论 |
| --- | ---: | --- |
| `test_connected_layout_keeps_workspace_editing_out_of_transcript` | 4 | 原始夹具同样缺少 session_id；本轮另有 2 个 teardown error，根因相同 |
| `test_bulk_lifecycle_and_selection_scope` | 1 | 原始记忆批量操作同样未恢复预期焦点 |
| `test_card_grid_global_sort_and_return_context` | 1 | 原始布局中 shell-main 同样不滚动，该旧断言读取到 0 |
| `test_missing_agent_context_keeps_draft_without_writing_memory` | 1 | 原始 Gateway 客户端对 -32601 使用固定提示，测试仍期待服务端原文 |
| 生命周期安装/启动测试 | 7 | HEAD 对照同样 7 failed、3 passed；Windows 缺少夹具需要的 POSIX 工具，且无扩展名 node 脚本不被 Windows 查找机制识别 |

已确认既有测试 `test_connected_layout_keeps_workspace_editing_out_of_transcript` 的 records
缺少 `session_id`，但其共用 RPC 夹具按 `record['session_id']` 筛选，导致 KeyError。
使用 `git show HEAD:apps/webui/test/packages/chat_app/test_chat.py` 提取原始测试与原始夹具，
在不加载任何修改后的应用源码的情况下复现同一异常。保留原测试数据，未顺带修复。

全量 `pnpm format:check` 报告 22 个未改动的 dswui 导入文件存在格式问题。
逐一与 HEAD 对比：忽略 CRLF/LF 后内容全部相同，其中 17 个在 HEAD 的 LF 内容上也不符合 Prettier，
其余为当前检出换行格式差异。未执行无关的整树格式化。

主题样式回归测试增加了等待 `data-theme` 应用完成的断言，避免异步系统主题事件尚未到达时，
将主题变化误判为模块样式污染。

## 2026-10-25 PR 修复复验

PR 对照为 `upstream/feature@04b203f` 到 `feat/webui@620989c` 加工作区改动。
已修复图片草稿删除保护、再次提问恢复图片、导航后清理 Select 弹层、测试夹具及过时断言，
并统一 DSWUI 文件格式。全量复验另发现批量更新后焦点提前恢复；`useMemories.refresh`
现同步设置加载状态，等待新列表就绪后再恢复焦点。

浏览器及生命周期测试在 WSL Ubuntu 运行，使用 WebUI 自有 Python 3.14 环境、
Playwright 1.58.0 和 Chromium headless shell。Ubuntu 26.04 下使用
`PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64`；浏览器通过 localhost 临时转发访问 Vite。

| 执行范围 | 结果 | 本地报告 |
| --- | --- | --- |
| WSL 全量首轮 | 282 passed、1 failed、13 skipped、13 errors | `test-results/fix-review-full-wsl.xml` |
| 焦点修复后的 WSL 记忆页完整测试组 | 38 passed | `test-results/fix-review-memory-wsl.xml` |
| Windows 服务代理与静态资源测试组 | 13 passed | `test-results/fix-review-server-windows.xml` |

首轮唯一功能失败为上述焦点时序问题。13 个 setup error 来自共享 Windows node_modules 的
Linux 原生构建依赖和挂载目录读取问题；服务端测试改在原生 Windows 环境重新构建并验证。
按测试标识取最后一次结果，**296 passed、13 skipped，无剩余失败或 error**。
跳过项为需要显式开启的真实服务/模型集成测试。此结果由全量与受影响组的复测共同组成，
不表示一次 WSL 全量命令全部通过。

`pnpm lint`、`pnpm format:check`、`pnpm typecheck`、生产构建、测试目录 compileall
及 `git diff --check` 均通过。测试截图、XML 和临时环境保持为忽略产物；未提交或推送。

## PR 发布准备复验

隐藏输入框“深度思考”按钮后重新构建，WSL 欢迎页与搜索测试 **17 passed**；
390px/1440px 的独立 Chromium 检查确认按钮不存在，联网搜索及图片上传控件可见，页面无横向溢出。
截图与报告位于忽略的 `test-results/pr-composer-no-thinking-*.png`、`pr-final-composer-wsl.xml`。

Agent 在 WSL 补跑会话 Runtime、共享设置、标题、搜索提交、搜索插件、输入插件和公开事件投影，
结果 **110 passed**。Gateway RPC、搜索参数、图片资源及模拟模型端到端测试 **27 passed**。
这些为相关范围验证，不代表两应用全量套件。隐藏按钮后的 lint、格式、类型检查与生产构建再次通过。

调研文档归入 WebUI `docs/research`，同步更新链接；个人忽略规则移到 Git 本地 exclude，
不改变提交历史。PR 描述见 [提交说明](../2026-10-23-dswui-chat-integration/plan-pull-request.md)。

## 保留边界

未测真实模型首字延迟，未给出跨机器 FPS 保证。IndexedDB 只用于可提前展示的数据，
浏览器刷新仍全量重建服务端历史；完整历史传输与服务端分页不在本轮范围。
长会话虚拟化会卸载离屏消息；浏览器原生全文查找/选区的边界见 plan.md。
