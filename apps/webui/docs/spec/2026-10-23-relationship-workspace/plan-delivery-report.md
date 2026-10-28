# 人物工作台实施与验证记录

日期：2026-10-27。对应 [计划](plan.md)、[分阶段任务](plan-layout-implementation.md) 与 [当前架构](arch.md)。

**2026-10-28 PR #12 提交复验**：本轮后续提交准备已在 WSL Ubuntu 原生文件系统中的拟提交副本执行 WebUI 完整套件，**349 passed、13 skipped（1038.08s），退出 0**；根控制面 **369 passed（44.08s）**。WSL 的 Lint、格式、类型与默认配置生产构建均通过。13 项跳过为显式启用的真实服务/模型检查，没有真实业务写入或付费模型调用。环境与范围见 [PR 验证记录](../2026-10-23-dswui-chat-integration/plan-pr-validation.md)；下方“未重跑完整套件”等表述保留各轮当时状态。

**2026-10-28 圈子卡片与阅读弹窗统一调整**：圈子卡片改用与人物一致的 36px 图标/正文结构，名称与类型同排，可选负责人最多两行，紧凑 ID 紧随其后；移除箭头、空负责人占位及多余高度。人物/圈子详情统一为桌面宽 860px、手机全屏；标题、身份、左对齐分类与正文共用 28px 水平内边距（手机 20px），移除内部居中窄容器和重复的档案标题。头部使用主题浅底色，完整 ID 可整体点击复制，编辑采用带铅笔图标的次级按钮；搜索与实心添加按钮分列。空状态增加浅底虚线容器和操作提示，历史查询使用折叠面板。首屏空集合/单页不显示分页，翻页后为空仍保留上一页；阅读框稳定高度、延迟加载与校验期间的写入禁用行为保持有效。

新增四组桌面/手机人物与圈子阅读用例，以及翻页后空页返回用例。人物包与八项稳定性回归共 **38 passed（101.54s）**；部署后实际 8080 上重点阅读与稳定性回归 **13 passed（18.57s）**。随后根据超长 ID 手机截图，修正复制胶囊中 ID 标签被压缩换行，限制圈子卡片负责人行数，最终布局回归 **12 passed（29.98s）**。类型、人物包 Lint/格式、生产构建及差异检查通过；静态稿脚本语法和浏览器运行检查通过。架构、计划与静态稿已同步，静态稿仍仅演示人物阅读和圈子卡片，圈子详情以生产组件及测试为准。

已检查空/有数据的档案与成员、长姓名/别名/ID、文本放大、桌面/手机/暗色及嵌套表单。最终生产构建已重启受管 WSL WebUI，浏览器强制重载后实际入口为 `index-yulwI_qT.js`。真实人物只读截图为 `test-results/reading-person-live.png`；真实圈子卡片/详情为 `test-results/reading-circle-cards-real.png`、`test-results/reading-circle-real.png`；其他 `reading-circle-*` 与长 ID 截图为隔离样例。验证不写入真实业务数据、不调用模型，未重跑完整 WebUI 套件。下方各轮截图、尺寸与验证为历史记录，当前布局以本段和架构文档为准。

**2026-10-28 身份卡片定稿调整**：根据后续反馈，继续去掉首轮紧凑卡片的水平分割线、底部固定分栏与阅读箭头。头像改为 36px，姓名和类型小标签同排，别名、ID 在右侧正文中顺序排列；无别名的 Icarus/用户卡片自然高度为 88px，不再设置 148px 最小高度。ID 标签简化为 ID，并显式使用与正文一致的公共无衬线字体，避免 code 默认字体回退。类型标签与头像使用同一低对比色调，归档独立展示；悬停只改变背景和边框颜色，不增加位移或缩放。下段保留首轮调整记录，当前布局以本段及架构、实施计划为准。

本轮布局检查 **7 passed（54.08s）**，随后人物包与稳定性回归共 **33 passed（92.37s）**；类型、Lint、格式、生产构建与差异检查通过，静态稿脚本语法检查通过。已检查桌面、手机、暗色、长姓名/别名及文本放大场景。受管 WSL WebUI 已重启，实际 8080 页面入口 `index-jn98z-G0.js`；两张真实人物卡片约 280×88，悬停前后坐标和尺寸一致，点击仍打开阅读档案并可关闭。真实截图为 `test-results/person-identity-live.png`，未修改人物数据。

**2026-10-28 卡片视觉调整**：用户截图显示方形卡片在身份字段较少时留下大块空白，本轮取消固定方形比例。人物卡片列宽仍上限 280px，最小高度改为 148px；44px 头像与姓名/类型横向组合，助手与用户使用对应类型图标，普通人物使用姓名首字。无别名不渲染“暂无别名”，有别名最多两行；底部以细分隔线和单行 ID/阅读箭头收尾。悬停只改变边框/阴影，不改变位置或尺寸。已同步静态稿、实施计划和当前架构。

本轮人物包与八项稳定性回归共 **33 passed（95.05s）**，类型、Lint、格式、生产构建通过。已检查桌面、390px 手机、暗色和长姓名样例；重启受管 WSL WebUI 后，实际 8080 页面两张人物卡片均为 280×148。真实卡片截图位于 `test-results/person-card-redesign-live.png`；其他 `person-card-redesign-*.png` 为隔离样例。未改动真实人物数据。

**2026-10-28 用户复测补充**：上轮外框/按钮尺寸回归未覆盖人物卡片打开时的内部闪动。本轮逐帧确认空白身份区、人物读取、档案读取的串行呈现；改用当前卡片记录即时展示、并行读取并延迟加载图标，预留滚动条空间。真实 8080 页面已更新并逐帧复核，八项稳定性测试通过，扩展回归 105 项通过；详见[补充排查记录](../2026-10-27-interaction-stability/plan.md)。

**同日密度与交互修正**：按用户实际使用反馈，人物卡片列宽限制为 280px、最小高度 240px，收紧头像/内边距；圈子卡片列宽限制为 360px，使用内容高度。表单此前叠加 Field 默认 20px 下边距与 fieldset 18px gap；现移除 Field 外边距，统一使用 16px gap。真实圈子列表返回 200、0 项，未写演示记录到用户库，空状态增加新建入口说明。

首次进入列表出现两侧红线已复现：`restore()` 在没有返回目标时对整个 `.relationship-page` 调用了 focus，触发全局焦点轮廓。现只恢复真实卡片焦点，保留键盘打开/关闭详情的焦点返回。人物档案重排头像、姓名/类型/别名/ID 与编辑操作；工具栏将搜索与添加组合，手机详情铺满视口，四个分类等分适配。

按钮点击跳动与弹窗加载闪动的公共修正见 [交互稳定性记录](../2026-10-27-interaction-stability/plan.md)：修复 light 按钮交互时丢失边框造成的 2px 位移；刷新提示改为延迟显示的覆盖层；人物/圈子阅读框使用稳定高度。扩展回归 125 项通过，最新生产构建已更新到 WSL WebUI，8080 服务上的四项隔离稳定性回归再次通过。

本次人物包 **25 passed（75.30s）**；Shell 与 Chat Shell **25 passed（63.00s）**；类型、Lint、格式、构建通过。检查了桌面/手机、长身份和 200% 文本，生产构建后重新启动 WSL WebUI。Windows 浏览器重新加载实际 8080 页面，确认卡片 280×280、首次进入不抢焦点、详情头部为 grid、搜索与添加同排、无横向溢出。隔离圈子卡片/表单截图保存在 `test-results/relationship-compact-*.png`。本次未重跑已有环境失败的完整 WebUI 套件，未执行真实业务写入或模型调用。

**同日生产回归修正**：用户在 WSL 重启后，WebUI 对话显示 Gateway 不可用。实际 Gateway 健康正常，但 WebUI `/rpc` 返回 502。新增 rela 代理改变了 `routes` 顺序，WebSocket 升级仍使用 `routes[2]`，误转发到 OpenKB。本次改为按 `/rpc` 前缀选择目标。原测试将所有服务配置到同一假上游，掩盖了错误；现将 Gateway 分配独立端口，其他后端拒绝 WebSocket。修改测试先复现 502，修复后生产测试 **14 passed（21.69s）**，Lint、类型、格式及差异检查通过。

修复后已重启用户 WSL 中的受管 WebUI，并从 Windows Edge 通过实际 `8080/rpc` 验证 `runtime.get_status`、`session.list`、`memory.get_context` 均成功；不发送模型消息，不改动会话内容。直接浏览 `8765/` 的 404 只表示 API 服务没有网页首页，不能用于判断 WebUI 功能正常。实际连通记录比早先仅健康接口的判断更充分。

**同日真实只读联调补充**：rela 当时没有容器运行，其 PostgreSQL、FalkorDB 也未启动。Gateway/WebUI 对 rela 的依赖是可选，单独启动两者不会拉起 rela。已执行 `./bin/icarus start rela-mem`，自动启动独立数据库并首次构建服务镜像；镜像/依赖下载使首次启动耗时较长。最终 rela 就绪，通过实际 WebUI `/api/rela/v1/persons?limit=1` 返回 200 且分页形状有效；Mem0 与 OpenKB 同源读取也均为 200。下文“真实服务未连接”保留为首次交付时的历史结果，已由本段只读验证补充；真实业务写入和模型 smoke 仍未执行。

## 1. 交付状态

P0～P6 的 WebUI 代码已落地，隔离功能测试与生产构建完成。真实 rela 只读探测未能连接；真实数据库持久化、真实写入与模型 smoke 尚未验收。本记录不表示已部署、提交或合并。

| 阶段 | 实际成果 |
| --- | --- |
| P0 | 核对 REST/工具契约、Git 与运行基线；静态人物卡片同步 |
| P1 | 独立 relationship workspace 包、Shell 懒加载、账户菜单统一入口、开发/生产代理、可选依赖与启动白名单 |
| P2 | 三/两/一列近方形人物卡片；真实接口搜索、类型/归档包含、每页 20 项游标与错误恢复 |
| P3 | 阅读弹窗四分类、任意 JSON/null、单字段历史、精确身份核对、关系跳人及列表返回恢复 |
| P4 | 创建/编辑人物、别名增删、档案单项修正/忘记、关系称谓与失效；冲突、校验与未知写结果保护 |
| P5 | 圈子两/一列紧凑卡片；跨页定位、圈子编辑/负责人清空、成员加入/编辑/退出与筛选 |
| P6 | 完整结构化 relationship 工具结果中的稳定 ID 打开人物，返回保留会话；裁剪/其他工具不猜身份 |

没有新增成员总数、关系数、完整度、活跃度、人物合并、精确身份全列表或关系图。未改变 Agent、rela 服务业务与数据库。

## 2. 文件与实现调整

新增 `packages/relationship-app/`，包含请求与类型、通用只读状态、卡片、阅读详情、写入表单、人物候选选择和响应式样式。Shell、ChatSettings/ChatApp、DswSidebar 接入统一管理目标；旧知识库/记忆上方入口迁入账户菜单，已有路由保留。

`/api/rela` 使用独立有边界的代理前缀，不占用 OpenKB `/api/v1`。Vite/Node 附加服务端 Token、清理浏览器私有凭据；原生启动器读取 endpoint/Token 白名单。`icarus.toml` 只增加 WebUI 对 rela-mem 的可选依赖。锁文件只增加新 workspace 包及 Shell 的本地引用。

下列细节在复查时补齐：

- 游标栈按条件保存在历史条目，翻页后刷新、切换分类和关闭详情仍可上一页。
- 已忘记字段的历史独立显示，刷新后可按已知 key 再读墓碑，不随当前值条目消失。
- 圈子与人物关闭后恢复触发条目焦点；归档成员/关联人物使用详情专用读取标记。
- 写入对话框独立处理 Escape，避免嵌套确认同时关闭底层阅读详情。
- 每个表单只执行一项 REST 操作，超时/中断不自动重放，普通失败保留输入。

与初始任务拆分存在以下明确偏离：

1. 只读请求统一为 `useRead.ts`；人物/档案/身份表单按调用关系合并，未创建计划中的所有独立文件。测试也按实际组件合并到 `test_detail.py`、`test_circles.py` 等文件。
2. 当前 UI 不展示 key 目录，未添加没有消费者的 `/profile/keys` 请求；单 key 历史直接支持已知 key，未加载字段不被视为不存在。
3. 圈子无单条 GET。现实现按每页 200 项遍历列表定位，测试目标在第二页；不添加不存在的详情接口。大规模圈子定位成本仍是服务能力限制。
4. 圈子静态稿在生产布局完成后补齐，未遵循“先静态稿再编码”的原计划顺序；生产布局、长名称/成员功能与桌面/手机检查已独立完成。
5. 写入均为单项请求，没有引入批量事务或多请求成功承诺；P6 的候选链接只负责阅读，消歧后的业务操作仍通过既有对话工具，不自动续写。

## 3. 基线与环境

| 项目 | 记录 |
| --- | --- |
| 分支 / HEAD | `feat/webui` / `9b715f2` |
| origin | `https://github.com/xilele777/Icarus.git`；feature `2633c14c69744cce6c8fa19b7f65e672e2380046` |
| upstream | `https://github.com/Removel/Icarus.git`；feature `04b203feda13cc52567609e07729c7629258ca5c` |
| 远端基线 | `ls-remote` 已核对，两条 feature 均为 HEAD 祖先；未创建分支、merge/rebase、commit 或 push |
| 系统 / 浏览器 | Windows，Playwright Chromium 使用已安装 Edge（`WEBUI_BROWSER_CHANNEL=msedge`） |
| Node / pnpm | 24.14.0 / 10.32.1 |
| WebUI Python | 应用 `.venv/Scripts/python.exe`，Python 3.12.13 |
| 控制面 Python | 既有 `apps/agent/.venv/windows/Scripts/python.exe` |

开始时已有关系工作台计划改动及未跟踪设计文档；另有 `.workbuddy/`、`pelican-bicycle.html` 和对话交付文档等本地文件。保留这些既有内容，未纳入清理或 Git 操作。

首次开发服务因缺少 Windows Rollup 可选依赖无法启动，固定锁文件安装后恢复；未升级依赖。服务启动前的浏览器 setup 失败不计为功能通过。最小 Shell 基线 2 项通过；开发过程中新增功能缺失用例先失败，完成后再验证。

## 4. 验证结果

以下命令除控制面外在 `apps/webui` 执行。浏览器验证需要 `pnpm dev`，本次使用 `http://127.0.0.1:5173`。Python 使用应用私有环境，避免重新解析测试依赖；与 `pnpm test` 运行同一 pytest 配置和测试。

| 验证 | 命令 / 范围 | 结果 |
| --- | --- | --- |
| 最终人物及关联回归 | `.venv/Scripts/python.exe -m pytest test/packages/relationship_app test/packages/chat_app/test_relationship_links.py test/server/test_rela_lifecycle.py test/server/test_rela_development.py -q --tb=short` | 30 passed，68.60s，退出 0 |
| 生产入口 | `.venv/Scripts/python.exe -m pytest test/server/test_server.py -q --tb=short` | 14 passed，退出 0；使用真实构建、Node 入口与隔离 HTTP/WebSocket 上游 |
| 关系规范化补充 | `.venv/Scripts/python.exe -m pytest test/packages/relationship_app/test_relation_writes.py -q --tb=short` | 2 passed，6.76s，退出 0；覆盖双向称谓更新、端点和圈子范围保留 |
| Shell/侧栏/布局回归 | Shell、sidebar controls 与人物布局组合 | 46 passed、2 failed 后修复首次菜单焦点；受影响 5 项重跑通过，完整套件也通过对应功能用例 |
| WebUI 完整套件 | `.venv/Scripts/python.exe -m pytest -q --tb=short` | 310 passed、13 skipped、7 failed、1 error；990.92s，退出 1；详见下节 |
| 控制面 | 根目录：`apps/agent/.venv/windows/Scripts/python.exe -m pytest scripts/tests/test_registry.py scripts/tests/test_injection.py -q --tb=short` | 200 passed，2.56s，退出 0 |
| 静态/生产布局 | Edge；1440、1024、768、390px；内容宽度断点、长中英文姓名/多别名、浅深色、侧栏收起、键盘与 200% 文本 | 已通过；200% 用测得字体像素加倍检查，不等同整页浏览器缩放 |
| 编译与规范 | `pnpm typecheck`、`pnpm lint`、`pnpm format:check`、`pnpm build` | 全部退出 0 |
| 差异 | 根目录 `git diff --check`；新增文件单独检查空白与文档链接 | 通过 |
| 真实 rela 只读 | 使用启动器配置，仅 GET `/v1/persons?limit=1`，只打印 HTTP/分页形状 | 未连接（URLError），退出 1；未打印凭据或人物数据 |

最终 30 项聚焦检查在后续档案历史、圈子恢复及 fixture 校正之后运行；完整套件只运行一次，随后根据新增修改运行对应最小回归。生产检查验证编码查询/路径、错误透传、来源校验、代理边界、客户端凭据清理及构建 Token 哨兵不进入 JS。启动诊断分别验证成功与 401，不输出记录或 Token。

测试产物仅在忽略目录 `apps/webui/test-results/`：

- `relationship-focused-final.log`、`relationship-production-final.log`：最终聚焦与生产测试。
- `relationship-full-suite.log`、`relationship-lifecycle-baseline.log`：全套及原启动器基线复现。
- `relationship-cards-{1440,1024,768,390}.png`：生产卡片列数。
- `relationship-detail-{light-1440,dark-390}.png`、`relationship-circle-{light-1440,dark-390}.png`：长文本阅读与圈子。
- `relationship-static-{people,circles}-{1440,1024,768,390}.png`：已同步的静态稿。

## 5. 完整套件异常及基线复现

`test/test_lifecycle_scripts.py` 的下列 7 项失败：

- `test_install_requires_node`
- `test_install_requires_corepack`
- `test_install_rejects_unknown_arguments`
- `test_install_builds_with_corepack_for_empty_and_dev_arguments`
- `test_launcher_execs_node_with_dotenv_and_process_override`
- `test_install_keeps_nested_pnpm_on_the_pinned_version`
- `test_install_accepts_a_system_pnpm_matching_the_pin`

其中 6 项安装测试依赖 POSIX 命令/符号链接，当前 Windows PATH 无所需工具；启动器用例创建的是 Bash `node` 假脚本，Windows 无法作为 Node 可执行程序调用。将 `HEAD:apps/webui/scripts/icarus_start.py` 导出到测试产物，通过测试模块的 `START_SCRIPT` 指向原版再次运行，得到同身份 **7 failed、3 passed**。原版与修改版失败一致，未为本任务修改这些环境相关测试。

`test/packages/chat_app/test_chat.py::test_new_session_and_switching_clears_previous_transcript` 在公共 page fixture 等待记忆页面时超时，尚未进入用例；同环境单独重跑 **1 passed，3.55s**。未将这次全套运行描述为全绿，也未改动该用例来掩盖超时。

13 项真实服务/模型测试按默认策略跳过，不能视为实际服务通过。

## 6. 交接与剩余验收

可在账户菜单进入“人物”，或直接访问 `/#/people`、`/#/people/circles`。服务端配置 `ICARUS_RELATIONSHIP_ENDPOINT` 与 `RELA_MEM_API_TOKEN` 后重启入口。运行与排障见 [部署说明](../../deployment.md)。

剩余验收：rela 服务恢复后先做鉴权只读 smoke，再在明确授权的隔离数据上验证真实写入、刷新持久化、墓碑与失效语义；如需真实模型对话验证，单独记录模型配置与结果。不得使用生产人物作为测试数据。

继续保留的边界：圈子定位依赖列表遍历；工具预览被裁剪时不提供人物链接；精确身份全列表、人物合并/重绑/归档专用流程、关系图与最短路径尚未实现。UI 回退不会撤销已经成功的业务写入。
