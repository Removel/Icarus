# 对话排序、记忆可见性与实时思考实施计划

## 目标与文档入口

- 日期：2026-11-06。
- 状态：代码与聚焦回归已完成，应用级验收记录见文末；2026-11-07 经用户授权整理本地提交，尚未推送或部署。
- 用户目标：在 PR #17 的代码基础上，以独立 PR 修复会话排序、记忆页面可见性、实时思考展开和 TUI 引用显示。
- 本文件拥有 WebUI 的三个改动；其他应用的实施细节由下表文档拥有。需求可以按应用拆开，因此不新增仓库根跨应用 spec，也不创建占位 arch.md。

| 工作项 | 实施文档 | 完成条件 |
| --- | --- | --- |
| 会话排序、实时思考、记忆刷新 | 本文件 | 浏览器行为回归通过 |
| 历史记忆数据核对 | [数据审计计划](../../../../../docs/spec/2026-10-29-local-environment-reset/plan-memory-data-audit.md) | 明确数据源、缺失记录与重置历史的关系 |
| Agent 记忆写入反馈 | [写入结果计划](../../../../agent/docs/spec/2026-09-15-memory-knowledge-plugin/plan-write-result.md) | 空写入结果不再支持错误的成功表述 |
| Mem0 管理分页 | [完整分页计划](../../../../mem0/docs/spec/2026-10-05-memory-browser/plan-complete-pagination.md) | 超过 1000 条也可按条件访问全部记录 |
| TUI 联网引用显示 | [终端引用计划](../../../../tui/docs/spec/2026-11-06-web-search-citations/plan.md) | 来源编号只在终端展示时隐藏，原始记录保留 |

## 开发基线与 PR 边界

- 依赖 PR：Removel/Icarus #17，标题为 feat(agent): enable autonomous web research and on-demand prompt guidance；2026-11-06 通过 GitHub 查询确认状态为 OPEN。
- PR #17 的源：xilele777/Icarus 的 feat/agent；目标：Removel/Icarus 的 feature。
- 开发起点：284bcc5ec4decb74a95d6d5a1aa13b09b48c82be，与查询时 PR #17 的 headRefOid 相同。
- 新分支：feat/webui-chat-memory-fixes。该分支基于 PR #17 最新提交，而非旧 feat/webui 或旧 feature。
- 上游集成基线：upstream/feature 为 c019e9a7b7f12e175e59c4928c1678aba448cc70；GitHub API 与本地引用一致，且它已经是开发起点的祖先。origin/feature 为 2edab90410fdfce78de14e82b3afeadeefd7c0cf，不能混用。
- 网络记录：普通 fetch 因本机代理不可用失败；临时禁用代理后 origin fetch 成功、upstream fetch 连接重置。上游基线通过 GitHub API 单独核实，没有把 fetch 失败记成同步成功。
- 新 PR 最终面向 upstream 的 feature。原 PR 合并前，如先创建面向 feature 的草稿 PR，它会包含原 PR 的差异；实施期间以开发起点到修复分支的差异审阅本次修复。
- 原 PR 合并后重新核对 feature；普通合并、squash 或 rebase 合并对提交历史的影响分别处理，保证最终 PR 只剩本次修复，再做组合回归。后续提交、历史整理、推送和创建 PR 按用户授权执行。
- 已有 .gitignore 修改及未跟踪 pelican-bicycle.html 保留；它们不属于本次修复。

## 已验证事实与限制

1. SessionStore.list_session_summaries 已按 last_public_activity_at 降序；append_update 会更新活动时间。服务端不是按创建时间排序。
2. DswSidebar.groupSessions 使用更新时间分桶但不在桶内排序；useChatSession 发送成功后只 map 更新 updated_at，实时订阅也只更新 transcript。
3. ChatProcess 外层 details 没有默认 open；ChatReasoning 内层对 live 且 running 的新条目已默认展开。初始占位已存在，无须再添加另一套占位动画。
4. useMemories 在 active、version、query 改变时重新查询；离开再进入页面已有刷新。停留页面及浏览器焦点恢复没有外部写入同步，不能宣称所有切页都会使用旧缓存。
5. 当前运行环境 Agent 与 WebUI 的 Mem0 endpoint、鉴权配置一致，直连与代理都返回相同的 2 条记忆；历史数据不一致另见数据审计计划。1000 条截断是独立的容量缺陷，不能解释当前 2 条记录的现象。
6. 上轮只读调研已运行 SessionStore 测试 12 passed、TUI AgentProjector/Dispatcher 测试 8 passed；这不是本计划实施后的验收，也不是整个应用测试通过。

## Task W1：按最近活动稳定排列会话

### 文件与接口

- 修改 packages/chat-app/src/DswSidebar.tsx、useChatSession.ts。
- 必要时在同目录新增小型纯排序函数；复用现有 Session 和 RuntimeUpdate，不新增另一套会话模型。
- 扩展 test/packages/chat_app/test_sidebar_controls.py；需要隔离排序行为时新增 test_session_order.py。
- 服务端参照 apps/agent/src/agent_app/application/repositories/session_store.py 及对应 test_session_store.py；本任务不改变服务端会话存储语义。

### 目标行为

- [x] 首次加载、旧会话再次发送、当前会话收到有效活动、任务完成及重连回读后，列表按最近活动降序；置顶组优先，组内也按最近活动排序。
- [x] 先更新会话事实，再由同一排序规则生成日期组；更新时不原地修改 React 输入数组。
- [x] 优先使用服务端活动/事件时间，发送被接受时才作本地即时更新；重复或迟到事件不能把时间回退。
- [x] 相同时间使用 session_id 等稳定次序；无效 updated_at 可回退有效 created_at，均无效时保留稳定顺序，不产生 NaN 排序。
- [x] 拒绝发送不提升会话位置；发送结果不确定时依靠原有重连核对流程，不伪造已接受活动。
- [x] 排序不改变当前选中会话、搜索、置顶、批量操作及侧栏阅读位置；不为每个 token 请求 session.list。
- [x] 只处理已订阅的活动；未订阅会话在重新列举时校正，不引入未经需求支持的全局订阅协议。

### 验证

- [x] 先补能复现同日旧会话不置顶的浏览器测试，记录修复前失败。
- [x] 新增回归覆盖旧会话发送、实时/迟到活动、置顶、时间相同/无效和拒绝发送；历史恢复、重连等沿用应用既有回归，未对每个组合新增专用测试。
- [x] 跑聚焦浏览器回归，再执行本文件的应用级检查。

## Task W2：实时过程可见，保留手动折叠与阅读位置

### 文件与接口

- 修改 packages/chat-app/src/ChatProcess.tsx、ChatReasoning.tsx；必要时调整 ChatTranscript.tsx 和既有样式。
- 扩展 test/packages/chat_app/test_chat.py、test_presentation.py、test_refinements.py；使用现有 Gateway 离线事件夹具。

### 目标行为

- [x] 新的实时执行默认展开外层过程和最新思考块；历史回放默认折叠，重连恢复的活动任务仍按实时状态处理。
- [x] 用户手动收起后，后续 token、工具事件、页面激活和任务结束不强行展开；手动状态按任务/条目标识保存，虚拟列表卸载重挂不应清除用户选择。
- [x] 本轮保持当前实时任务的思考展开，不自动归档前一阶段，避免突然收起正在阅读的内容；任务结束保留展开状态，重新加载的历史默认折叠。
- [x] 沿用已有首响应占位；收到真实思考、搜索或工具状态后显示对应阶段。模型不返回思考内容时，展示等待/执行状态，不模拟思考文本。
- [x] 保留逐步揭示效果与已到达文本，折叠再展开不从头播放；外层和内层 toggle 事件不能互相误触发。
- [x] 保留当前自动跟随边界，用户向上阅读时不抢滚动；桌面及 390px 窄屏均可操作键盘折叠，正常动画和减少动态模式均可用。

### 验证

- [x] 新增实时默认展开、手动收起、任务结束保留、历史折叠与虚拟列表重挂回归；活动任务恢复和无思考执行沿用既有夹具。
- [x] 更新现有“实时外层必定折叠”的断言及依赖首次点击展开的用例；继续验证这些用例原本保护的复制、滚动、工具和来源行为。
- [x] 用离线浏览器夹具检查实际可见文本和滚动位置，并查看桌面/窄屏截图，不能只检查 open 属性。

## Task W3：让记忆页面及时回读外部写入

### 文件与接口

- 修改 packages/memory-app/src/useMemories.ts；必要时调整 MemoryApp.tsx 的刷新入口和提示。
- 扩展 test/packages/memory_app/test_memory.py 及 test/mem0_service.py。
- 沿用 GET /api/mem0/memories/page 及当前 page_size、筛选、排序和详情接口；服务端完整分页由 Mem0 计划负责。

### 目标行为

- [x] 保留进入页面时查询，在页面激活且文档可见时进行低频后台回读，间隔暂定 15 秒；恢复窗口焦点或标签可见后立即回读，并合并同时发生的刷新触发。
- [x] 离开记忆页或浏览器文档隐藏时停止周期回读；不新增全局跨应用事件总线，不把刷新绑定到某个具体工具名称。
- [x] 提供手动刷新入口；后台回读保留列表和滚动位置，不关闭详情或覆盖编辑草稿，不让页面布局跳动。
- [x] 使用已有 AbortController 和过期响应隔离；同时最多一个列表读取，切筛选/翻页后旧响应不能覆盖新条件。
- [x] 后台同步失败保留可读旧数据并提供错误与重试，不用“没有记忆”的空状态掩盖服务故障。
- [x] 既有筛选继续明确生效；不自动清空用户筛选来假装所有写入都已可见。完整分页后 counts/total 的语义按 Mem0 文档统一。

### 验证

- [x] 新增离线夹具验证定时、focus、隐藏页面暂停及手动刷新；离开再进入沿用原有 active 查询逻辑。
- [x] 新增回归覆盖隐藏页面无轮询、请求合并、刷新失败和保留编辑草稿；过期响应隔离及筛选沿用既有请求取消和服务查询契约。
- [x] 记忆的浏览器回归只写夹具，不把当前生产记忆作为测试数据。

## 实施顺序、验收与提交拆分

1. 完成数据审计，明确历史缺口是否与已记录重置有关；环境数据处理不阻塞独立的前端回归准备。
2. 分别完成 W1、W2；再配合 Agent/Mem0 完成 W3 和记忆写入反馈；TUI 引用按其应用计划实现。
3. 每项先跑最小受影响测试，随后跑受影响应用套件。提交按排序、展开、记忆各层、TUI 分开；文档在可行时独立提交。

~~~bash
# 从仓库根目录运行；脚本启动隔离 dev server，并隔离真实后端。
python3 apps/webui/scripts/test.py test/packages/chat_app/test_sidebar_controls.py test/packages/chat_app/test_chat.py test/packages/memory_app/test_memory.py -q
make test-webui
cd apps/webui
pnpm typecheck
pnpm lint
pnpm build
cd ../..
git diff --check
~~~

- [x] 记录每次实际运行的命令、退出码、失败测试标识与未执行项；不把计划命令写成验证完成。
- [ ] PR #17 合并后，核对最终 feature 基线及修复专属 diff，再运行跨应用组合回归。
- [x] 回滚以逻辑提交为单位，不覆盖用户原有文件，不对业务数据库执行重建、清空或降级。


## 2026-11-06 实施与验收记录

- W1：新增 `sessionActivity.ts`，Sidebar 在日期分组前按活动时间排序；实时通知使用 `occurred_at`，被接受的发送即时提升会话，迟到时间不回退。服务端无需修改。
- W2：实时外层过程和思考默认展开。手动状态由 `ChatTranscript` 按工作区、会话、任务和条目保存，虚拟列表重挂仍保留；完成后不突然收起，历史重新加载折叠。只展示服务实际提供的思考和执行状态。
- W3：可见且激活时每 15 秒回读，focus/visibility 恢复和手动按钮立即触发；合并并发刷新。后台刷新独立于详情刷新，不覆盖编辑草稿，失败保留旧列表。
- 新增排序/展开与记忆刷新浏览器回归共 13 项通过。受影响聊天与记忆回归为 217 passed、1 failed；失败为 `test_chat.py::test_only_messages_scroll_and_reading_history_does_not_jump[no-preference-390]`，恢复 HEAD 原始源码后亦复现相同失败，重跑又通过，属于既有滚动时序不稳定。本次不修改无关滚动行为。
- `pnpm typecheck`、`pnpm lint`、`pnpm build` 均退出 0；390 px 与 1440 px 实时思考截图已检查。
- Agent 全量 1363 passed；Mem0 聚焦 190 passed，包含隔离 PostgreSQL 17 的 1005 条管理分页。更多边界与完整套件限制见各应用计划。
- TUI 最终 `make test-tui` 为 **535 passed**，退出 0，入口包含 compileall；Agent 入口同样包含 compileall。Mem0 在只读源码挂载的隔离镜像中对 mem0/server/scripts/tests 执行 compileall，退出 0；`git diff --check` 退出 0。
- WebUI 最终全量命令：Windows 下 `UV_OFFLINE=true python apps/webui/scripts/test.py -q`，结果 **9 failed / 377 passed / 13 skipped**，退出 1。除了上述滚动用例，还包括 `test_circle_card_supplies_header_before_resolution_finishes` 及 7 个 lifecycle 脚本用例。7 个脚本用例分别为 install_requires_node、install_requires_corepack、install_rejects_unknown_arguments、install_builds_with_corepack_for_empty_and_dev_arguments、launcher_execs_node_with_dotenv_and_process_override、install_keeps_nested_pnpm_on_the_pinned_version、install_accepts_a_system_pnpm_matching_the_pin；Windows 缺 Unix 命令与无扩展名假 node 解析造成失败，原始 packages 源码下相同 7 项仍失败。圈子用例在原始源码的聚焦重跑通过；未改关系模块源码。
- WebUI `pnpm format:check` 退出 0。全量后重跑 UI loading 全文件与新增 13 项，**21 passed**，退出 0，包含此前失败的圈子加载用例。
- 未调用真实模型或搜索，未写入/恢复业务记忆；未运行 Gateway 套件，因为 Gateway 服务端源码与协议未修改，仅前端类型补充已有时间字段。

2026-11-07 用户反馈正文混入工具前的阶段性说明，补充修复及本轮验证见 [阶段性说明分离](plan-progress-messages.md)。上面的统计是 11 月 6 日四项修复的记录，不代表后续改动已验证。

## 2026-11-07 本地提交与运行更新

用户授权 commit 后，按 Mem0 存储、Agent 记忆反馈、TUI 引用、WebUI 记忆刷新、聊天显示与事件标记整理代码提交，文档另行提交。各项实现与对应测试放在一起；聊天排序、展开和阶段性说明共享 Transcript 及浏览器回归，保留在一个聊天修复提交中。`.gitignore` 和 `pelican-bicycle.html` 为原有本地改动，未纳入。未推送、创建 PR、合并或重启运行服务。

提交前补验：Agent RuntimeUpdate 目录与应用 checkpoint 联合回归 **20 passed / 30 deselected**，包含全量中旧断言修正后的持久化用例；WebUI 圈子管理既有回归 **3 passed**。圈子回归用于核对现有网页写入能力，未修改关系插件或圈子功能。

AgentRuntime 在 Gateway 进程中运行，本地安装使用 editable Agent distribution；本轮没有依赖变动，Gateway 停止后重新启动即可加载源码。生产 WebUI 必须先构建，再停止并启动 Node 入口。在实际运行服务的同一 WSL 环境、仓库、配置和数据根下执行以下命令；若原服务使用 `--config` 或环境覆盖，继续使用原值，不切换到另一套数据目录。

```bash
cd /mnt/f/project/Icarus
./bin/icarus install webui
./bin/icarus stop gateway
./bin/icarus start gateway
./bin/icarus stop webui
./bin/icarus start webui
./bin/icarus status gateway
./bin/icarus status webui
```

以上是更新步骤说明，本轮未执行。待当前任务结束后再重启 Gateway；浏览器随后强制刷新。新安装环境应先 `./bin/icarus install gateway`，安装 Gateway 依赖及 editable Agent。Mem0 容器中的持久化与完整分页修复还需要单独重建服务镜像并重启 Mem0，单独更新 Gateway/WebUI 不会更新容器源码；继续沿用既有数据库和挂载，不执行清库或环境重置。
