# 对话体验补齐与后续能力规划

2026-10-23。承接现有未提交的 DSWUI 接入代码。本轮需求优先于同目录首期范围；原计划的“仅 WebUI、不提供改名/公式/导出/模型配置”已由本轮扩展范围取代。未创建分支、提交、合并或推送。核对 `upstream/feature` 为 `04b203f`，当前 HEAD 包含此基线；未将 origin 与 upstream 混同。

上述 Git 状态记录于功能实施阶段。随后用户明确授权整理工作区并拆分本地提交，提交覆盖此前已有改动；仍未创建分支、合并或推送。独立演示文件 `pelican-spring.html` 按用户要求保留本地。

## 对照依据

- 原版 DSWUI 固定提交 `3eaf63119028e844dccd68e677e4fbd1a5fa59b6`，本地源码 `F:/project/dswui-review-20261023`。逐项核对 SessionMenu、UserMessage、AssistantActions、ThinkBlock 和 Markdown 配置。继续使用原有图标、尺寸和布局。
- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)：参考 `packages/client/ui-settings-agent-loop/src/client/AgentLoopCard.tsx` 的工具批量调用配置、`ui-chat/src/client/settings/TranscriptViewRow.tsx` 的展示偏好和 `packages/api/settings-controller/src/types.ts` 的配置冲突语义。仅参考交互和边界，没有导入其运行时或供应商实现。
- Icarus 的 `ConfigModel`、`ToolExecutionSettings`、`SessionApi`、Gateway methods、TUI GatewayClient 与 ResourceImporter 是接入依据。TUI 本身没有另一套模型配置服务；现有客户端共用 Agent 设置文件。

## 逐项处理

| 用户项 | 本轮处理 | 边界 |
| --- | --- | --- |
| 1 会话菜单 | 重命名、置顶/取消置顶、导出 Markdown、删除 | 置顶是按工作区保存的浏览器偏好，尚不跨设备同步；导出真实用户和 AI 正文，不伪造公共分享链接 |
| 2 标题 | 列表内编辑与点击顶部标题编辑；Enter/失焦保存、Escape 取消；最多 80 字；列表两行、完整标题提示 | 标题经 session.rename 写入原 SQLite 标题表，AI 自动生成不会覆盖手动标题 |
| 3 AI 消息 | 普通换行、GFM、公式、表格独立横向滚动；原版六个图标的布局 | 复制、再次提问、赞/踩、浏览器朗读、分享。再次提问填入输入框，用户发送后追加一轮，不伪装为截断历史的重新生成；评价只在当前页面标记 |
| 4 思考工具 | 移除 Semi Reasoning 混用，原版字号/颜色/时间线，思考 Markdown，工具明细折叠 | 保留多步骤/工具真实顺序和滚动跟随；历史思考不重播 |
| 5 用户消息 | 复制、编辑为新消息 | 编辑填入输入框，不覆写服务端已保存历史，也不自动发请求 |
| 6 切换卡顿 | 最近 12 段会话的内存历史缓存，先展示再按 sequence 增量同步；不重建 WebSocket；去掉历史消息入场动画；Markdown memo | 缓存不替代服务端，不跨刷新；再次进入仍订阅并同步新增内容 |
| 7 管理入口 | 左上角知识库/记忆入口；右侧面板切换；保持单一会话侧栏 | 保留 #/memory/*、#/knowledge/*、详情参数与原页面组件；知识库分类置于右侧顶部。模块保持挂载和筛选状态，旧链接可直接访问 |
| 8 常用配置 | 通用外观、Enter 方式、字号、宽版；最大步数、工具超时/输出预算/批量数量、Skill 生成/演化 | 坚持原设置对话框样式；服务端校验原模型，不添加空权限/联网开关 |
| 9 模型与多模态 | 已接共享模型列表/模型 ID/窗口/最大输出/温度/思考强度和四种角色分配 | 下文列出图片、凭据、多端刷新和历史分支的待实施部分 |
| 10 配色 | Icarus 原粉灰色为主色，适配浅/深色及选中态、气泡、发送按钮 | 业务管理页继续使用原主题，DSWUI 样式限定在对话子树 |
| 11 验证 | 聚焦会话/设置/路由后，执行应用套件、类型/构建/diff | 不调用付费模型，不把 mock 测试称为真实模型验证 |

## 模型与 TUI 的共用方式

新增 `settings.get / settings.update`，复用 `ICARUS_SETTINGS_PATH`（缺省为 Agent 现有 settings.json）。仅传 agent、skill、model_settings、model_roles，API Key 不返回浏览器；保留原文档其他字段。保存前验证完整 ConfigModel，使用文件 revision 检测外部修改，临时文件原子替换。冲突保留页面草稿，用户重新载入后重填。

新建或重新加载会话读取新设置，执行中的任务不热改。浏览器不另存一份服务端模型配置，不直接连接模型。新方法需要重启旧 Gateway 进程；`-32601` 显示中文重启提示。设置页面未填写 Key，也没有直接配置供应商协议的分支。

## 后续实施顺序

### A 图片上传（下一项）

2026-10-24 更新：图片输入已按 [独立实施计划](../2026-10-24-chat-image-input/plan.md) 接入，
Gateway 使用原始二进制 body 接收单张图片，无需新增 multipart 依赖。下文保留最初方案作为背景，
当前接口与验证以独立计划及 Gateway 图片资源架构为准。

目前 session.submit/steer 已有 resources，但浏览器不能把本地绝对路径作为服务端文件路径。TUI 当前通过 incoming 资源目录交接给 ResourceImporter；WebUI 应复用后半段导入逻辑。

1. Gateway 增加 `POST /resources`，接 multipart 文件，按实际图片类型和字节上限校验，生成不含用户路径的资源 ID 并写入现有 incoming 目录；返回既有 ResourceRef 字段。禁止把浏览器给出的绝对路径直接交给 importer。
2. WebUI 生产入口与 Vite 都代理该路径，沿用现有同源认证。输入框接文件选择、粘贴和拖入，显示缩略图、上传进度、移除、失败重试。
3. 上传成功后才把资源引用放入 submit/steer；队列条目携带各自的引用；停止、重试和未知提交结果沿用 submission_id。纯图片输入的 prompt 校验与 TUI 保持一致。
4. 历史展示读取 user.message.resources；若需要缩略图，由 Gateway 增加受同源认证的读取接口。临时资源回收由 runtime 管理，前端不自行删除执行中资源。
5. 聚焦测试：图片与文字、纯图片、粘贴、上传失败保留草稿、排队与断线重试、刷新历史。供应商图片支持及转换仍由 model_provider 处理，上层不判断供应商。

首版仅做现有 Agent 已支持的图片；PDF/Office 的解析复用知识库上传，不在对话上传器中新增临时 OCR 框架。

### B 连接与凭据管理

在共享设置中补专门的连接表单：协议、endpoint、凭据“已配置/未配置”状态与只写替换接口，浏览器不读回已有 Key。表单字段由后端声明；供应商能力映射仍留在 model_provider。先支持现有全局连接方式，不提前设计多租户账户。

### C 多端配置刷新

TUI 增加设置入口，复用同一 Gateway 方法；可增加 settings.changed 通知，让客户端知道 revision 变更。界面明确“下次会话生效”，需要立即应用时显式 unload 后重开，不中断正在执行的任务。

### D 真正的编辑重发与重新生成

新增显式的历史分支/回溯能力后，再接原版原地编辑和重生成。必须同时定义父消息位置、工具副作用是否重跑、活跃任务处理、历史 cursor 与旧分支保留方式；不能用重复 session.submit 伪装为原地重生成。多选删除与跨端置顶可独立补，不阻塞图片链路。

## 验证记录

浏览器预览使用独立端口 5186；没有停止 5173/5174 的既有前端服务。所有浏览器功能数据由 fixture 提供。真实服务探测只核对方法存在，不保存生产配置、不发送模型请求。

- `test_settings_service.py`、`test_session_store.py`、Gateway `test_methods.py`：26 项通过。另定向验证了运行时手动改名的持久化与原消息历史不变。
- WebUI `test_refinements.py` 与 shell：30 项通过；欢迎页 5 项通过。覆盖改名、置顶、导出、消息按钮、公式、增量缓存、配置保存和保留路由参数。
- 既有会话文件分段检查：一次运行 59 项通过后，发现输入框高度、空会话欢迎页和 Windows 剪贴板换行等 5 处旧断言；已修正并定向复跑。后续会话、补齐功能、记忆侧栏布局检查 21 项通过，另发现手机侧栏 Escape 后焦点未恢复的真实问题，修复后单项通过。不把这些有重叠的数字相加作为总通过数。
- 人工查看 fixture 截图：粉色欢迎页、消息正文/表格/公式与六个按钮、Agent 设置、思考过程。修正顶部短标题被挤成两行，以及首次深链接加载列表时没有滚动到选中项。
- `pnpm typecheck`、`pnpm lint`、`pnpm build`、相关 Python compileall、`git diff --check` 通过。构建仍提示较大 chunk；diff 检查仅有原工作副本 CRLF 归一化提示。

完整套件尚未全部验收：Windows 后端应用级运行结果为 1040 通过、54 失败、36 跳过，失败涉及 POSIX 目录 fd/文件与 shell 行为、默认编码及环境配置断言；未扩大范围修复。Linux Gateway 虚拟环境缺少 pytest，未完成 Linux 全套。WebUI 最初全量检查遇到大量旧布局断言后中止，后续仅迁移并检查受影响文件；不能视为全量通过。

本机旧 Gateway 对 `settings.get` 返回 `-32601 Method not found`。确认对应 WSL 进程与工作目录后重启同一服务，实际 WebSocket 再次请求已返回配置快照。未修改生产设置，也未为验证发送模型请求。

提交前补验：后端标题单项 1 通过，配置/存储/Gateway 26 通过；shell、欢迎页与补齐功能 35 通过；知识库窄屏分类与图谱 5 通过。类型检查和 lint 通过。修正了测试导航辅助函数误为知识库分类链接打开手机侧栏的问题；未扩展业务功能。
