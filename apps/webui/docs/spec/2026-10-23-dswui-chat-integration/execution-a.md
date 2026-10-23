# A 阶段执行记录：基线与适配边界

日期：2026-10-23。对应 T01–T02。实施基线为 `04b203f`，沿用原有未提交布局改动；不提交或合并。跨会话入口：[累计进度](progress.md)。

## 基线与环境

- `git fetch origin`、`git fetch upstream` 成功；HEAD 与 `upstream/feature` 相同，相对 `origin/feature` 为 86/0。不改动已有分支。
- Node 24.14.0、pnpm 10.32.1、Windows Edge；测试使用已有 uv 缓存环境（Python 3.13.14、pytest 8.4.2、Playwright 1.58.0）。普通 `pnpm test` 与离线 uv 环境准备等待，已中止；改用相同版本缓存解释器。
- 5173 实际是 dswui 预览，最初测试指向错误服务而超时，结果无效；5174 已占用。为 Icarus 启动独立 Vite `http://127.0.0.1:5180/`，测试显式设置 `WEBUI_BASE_URL`，不停止外部服务。
- 服务替身截图已保存：`test-results/dswui-integration/baseline-{chat,memory,knowledge}-1280.png`。只使用仓库固定示例，无官网私密内容。截图脚本也在该忽略目录。
- 真实后端未作为此次基线前提；没有模型调用、服务数据写入或配置/凭据修改。后续测试结果按追加记录补齐，不把未运行结果填成通过。

## 能力与来源清单

视觉参考固定 dswui `3eaf63119028e844dccd68e677e4fbd1a5fa59b6`，位置 `F:/project/dswui-review-20261023`。参考侧栏 261px、内容容器 840px、输入 16px/24px；正文有内边距，不能直接将 840px 当净文本宽度。390/768/1280/1536px、浅深色按阶段截图检查。

| 参考文件/组件 | Icarus 目标 | 方式与保留范围 | 替换依赖 |
| --- | --- | --- | --- |
| `styles/tokens.css`、`shell.css`、`sidebar.css` / Sidebar | `ChatApp.tsx`、局部 CSS | 按布局参考重写单侧栏、品牌、新建与底部设置 | 沿用 useChatSession 的服务端列表与操作 |
| `components/chat/Welcome.tsx`、`styles/welcome.css` | ChatApp 欢迎容器 | 重写欢迎标题、居中输入 | Icarus BrandMark，不搬品牌/字体/动画资源 |
| ConversationHeader / ChatMessageList | 现有 ChatTranscript / ChatProcess | 保留 Icarus 渲染与事件投影，调整视觉尺寸 | 不使用 dswui store 或消息格式 |
| Composer / composer.css | 现有受控输入、队列与操作栏 | 按卡片布局重写样式 | 保留 send/steer/cancel、IME、未知结果语义 |
| SettingsDialog / settings.css | ChatSettings + shell props | 自建设置界面与管理页链接 | 主题归 shell、业务导航回调，不跨业务包导入 |
| ScrollNav | 现有消息定位/回底 | 保留当前逻辑并验证布局 | 不加入第二套滚动调度 |

没有直接复制 dswui 源码、图片、字体或第三方依赖；其根目录未见 LICENSE，不声明 MIT。对照外观独立实现，无需为了已有布局工作等待授权。若之后直接移入源码，必须另记具体授权及 notices。

不导入 dswui api、chat/actions、Zustand persist、provider/Key 设置、演示初始化或单文件构建。Gateway JSON-RPC `/rpc`、Session/RuntimeUpdate、现有 Markdown 与代码高亮继续复用。

## 承接范围与后续

承接 `shell/App.tsx`、`workspace.css`、`chat/ChatApp.tsx`、`chat.css` 及相关测试的已有未提交布局；共享 UI 仅复用原有控件和 NavigationDrawer。知识库、记忆内部源码/布局不改。`.gitignore`、旧布局文档和根参考 HTML 不属于本次实现。

下一阶段 B：默认路由改为 chat；全局导航/顶栏只在管理页展示；同一 chat 实例持续挂载；设置传入主题及管理页回调；所有新样式与 portal 限于 chat 作用域。
