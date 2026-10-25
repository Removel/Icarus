# PR 前 WebUI 之外改动复核

核对日期：2026-10-25（Asia/Shanghai）。此文件用于提交范围和职责复核，不表示其他应用负责人已批准。
以下提交数和未提交文件列表保留首次范围复核时的快照；发布准备阶段的清理见末尾说明。

## 基线与范围

- 目标仓库：`upstream = https://github.com/Removel/Icarus.git`；目标分支 `feature`。
- 远端基线：`04b203feda13cc52567609e07729c7629258ca5c`，已通过 `git ls-remote` 核对，且为本地 HEAD 的祖先。
- 本地：`feat/webui@620989c`，相对基线共 60 个提交，另有工作区未提交改动。
- 其中 16 个提交涉及 WebUI 之外；按最终文件路径计，外部已跟踪改动为 46 个文件：Agent 33、Gateway 10、根目录 3。
- WebUI 之外另有 6 个未提交文件，全部属于 Agent 按需搜索修正，已包含在上述 46 个最终路径内。
- `origin/feature@2633c14` 与目标基线不同；不能用它代替 `upstream/feature` 做此次复核。

## 提交清单

| 提交 | 范围 | 实际改动及用途 |
| --- | --- | --- |
| `2553135` | 根目录 `.gitignore` | 忽略 `/resume/`，与 WebUI 功能无关；当前提交历史会将其带入 PR，建议明确决定是否保留。 |
| `a4dae15` | Agent | 增加手动改名入口，标题存储支持显式覆盖；与自动生成共用 `title_lock`，不重写消息历史。 |
| `81b562e` | Agent | 新增共享配置文件读写服务，可编辑 agent、skill、model_settings、model_roles；摘要 revision 冲突检查及同目录临时文件原子替换。 |
| `24469fa` | Gateway | 暴露 `session.rename` RPC，并校验标题参数。 |
| `48baeac` | Gateway | 暴露 `settings.get`、`settings.update` RPC，映射配置冲突和校验失败。 |
| `9592e39` | Agent 文档、根目录 spec | 记录共享配置和会话标题的跨应用契约及 Agent 实施范围。 |
| `3aca371` | Agent | 收紧自动生成标题的提示与结果裁剪：含宽字符最多 12 字符，否则最多 6 词及 40 字符；不改变手动及已持久化标题。 |
| `ed180a4` | Agent | `TaskSteerAppliedEvent` 投影为 `user.correction` 时补充 `submission_id`，让前端替换本地引导消息，避免重复显示。 |
| `67bba11` | 根目录 spec，兼有 WebUI 文档 | 修改共享契约中的自动标题说明及 WebUI 规划链接；提交标题提到关系工作台，但外部 diff 没有 Rela 服务实现。 |
| `7787209` | Agent，含 `icarus` 核心契约 | 新增完整 `web-search` 插件及 Search/Fetch 链路，输入选项透传、幂等指纹、上下文等待及进度事件；插件打包数量由 14 调整为 15。是外部范围最大的实现提交。 |
| `b75130b` | Gateway | `session.submit` 接受严格校验的 request_options：web_search、deep_search、local_time、time_zone；旧请求可不提供。 |
| `44d4e51` | Agent/Gateway 文档，兼有 WebUI 文档 | 新增搜索架构说明；其他内容在 WebUI 内。 |
| `4c50e22` | Gateway | 新增图片 `POST /resources`、`GET /resources`，接入既有 incoming 暂存与会话 assets，另加资源及本地模拟模型端到端测试。 |
| `8f82be5` | Gateway 文档，兼有 WebUI 文档 | 更新 Gateway README，增加图片资源架构说明。 |
| `7646162` | Agent | 搜索服务 401/402/403/429 等失败映射为可读提示，失败后继续普通回答，避免公开响应体和凭据。 |
| `620989c` | 根目录 `.example.env`、Agent 文档 | 增加 `ICARUS_WEB_SEARCH_API_KEY` 空值示例及 DeepSeek Search/Fetch 配置说明，不改变聊天模型配置。 |

逐笔查看：`git show <commit> -- . ':!apps/webui'`。

## 需要负责人重点复核的设计影响

### 共享设置：是服务端配置写入能力

代码：`apps/agent/src/agent_app/config/settings_service.py`，Gateway 入口位于
`apps/gateway/src/protocol/methods.py`。

- 写入现有服务端配置文件，不是浏览器本地设置；会影响后续新建或重新加载的会话及其他客户端。
- 运行中任务继续使用已加载配置；没有跨端变更通知或运行中模型热切换。
- 可编辑投影不含顶层 API Key；未编辑的配置字段保留，复用 ConfigModel 校验。
- revision 先检查再原子替换，不等同于跨进程锁或完整并发事务；需确认单实例配置写入边界符合预期。

### 标题及引导：共享会话数据和公开事件

代码：`application/controllers/session_api.py`、`repositories/session_store.py`、
`application/session_title.py`、`plugins/runtime_update/plugin.py`（均位于 Agent）。

- 手动标题覆盖持久化值，与自动生成串行；没有新增数据库表或迁移脚本。
- 自动标题变短会影响任何调用同一标题生成接口的客户端，不限于 WebUI。
- `user.correction` 新增 submission_id 是公开 RuntimeUpdate 的增量字段。

### 搜索：扩展核心输入契约并加入上下文准备

核心路径：`apps/agent/src/icarus/core/contracts/input.py`、`core/ports.py`、
`core/runtime_update.py`、`runtime/session.py`。

应用路径：`apps/agent/src/agent_app/plugins/web_search/`、`contracts/web_search.py`、
`plugins/user_input/plugin.py`、`plugins/runtime_update/`、`application/session_runtime.py`、
`application/services/common.py`、`application/controllers/session_api.py`。

- 输入新增可选 request_options；JSON 校验和 4096 字符限制位于 UserInputEvent，旧调用保持空选项。
- 非空选项纳入 submission_id 的指纹校验，同一 ID 修改搜索选项不再被当作相同请求。
- 新插件 manifest 为 core；插件启用时 SessionRuntime 将 web-search 加入 Blackboard 必需上下文来源。
  即使开关关闭也会贡献本轮时间，关闭时不调用规划模型或 Search/Fetch。需复核这对其他客户端和任务时序的影响。
- 搜索开关开启且密钥存在时，调用 title 角色模型作决策和规划；不是主模型在 ReAct 中自行选择工具。
  需要搜索时普通模式最多 2 条查询、抓取 3 页；深度模式最多 5 条查询、抓取 6 页。
- 规划上限 8 秒、链路默认上限 45 秒；上下文单页最多 6000 字符、合计最多 24000 字符。
  Search/Fetch 经可配置端点发送，默认 DeepSeek；密钥由服务端环境变量读取。
- 资料作为本轮 ContextContributionEvent 进入动态 User Prompt，附不可信资料及引用编号说明；
  未修改 ReActAgent、稳定 System Prompt 或模型供应商适配层。
- 新增公开 `web_search.updated` 事件；来源和页面通过既有脱敏器投影，供 WebUI 呈现进度和引用。

### 图片：Gateway 新增 HTTP 文件传输入口

代码：`apps/gateway/src/resources.py`、`src/app.py`。

- 接受 PNG/JPEG/GIF/WebP，声明长度及实际读取均受 20 MiB 限制；校验文件签名与 MIME。
- 暂存为 `incoming/webui/<uuid>.<ext>`，提交时复用已有 ResourceRef 和 Agent 图片导入流程。
- 读取会话 assets 需要 workspace/session，并通过 runtime 查询会话状态；有路径和符号链接边界检查。
- 本接口沿用 Gateway 本机单用户边界，浏览器经 WebUI 同源鉴权代理；没有新增多租户授权层。
- 暂存文件不自动回收，前端移除附件不删除服务端文件。需由 Gateway 负责人确认资源保留策略。
- 没有修改 Agent 图片导入器或模型图片协议。

## 尚未提交的外部修正

以下 6 个文件均为按需搜索修正，不能只推当前 HEAD 而遗漏它们：

| 文件（`apps/agent/` 下） | 工作区新增行为 |
| --- | --- |
| `src/agent_app/plugins/web_search/factory.py` | 规划提示区分是否需要外部资料；闲聊、翻译、稳定知识等可返回 `[]`，明确要求查证/链接或最新事实时检索。 |
| `src/agent_app/plugins/web_search/pipeline.py` | 显式空数组跳过 Search/Fetch 与进度发布；畸形输出或规划失败仍回退查询，因此降级情况下仍可能搜索。 |
| `src/agent_app/plugins/web_search/plugin.py` | 尚未发布搜索进度时取消，不虚构“搜索已停止”节点；无检索仍释放时间上下文。 |
| `test/agent_app/plugins/web_search/test_pipeline.py` | 覆盖空计划跳过网络、时间上下文释放及决策阶段取消。 |
| `test/agent_app/plugins/web_search/test_integration.py` | 真 Runtime 事件链加入 skip 场景。 |
| `docs/spec/2026-10-23-web-search/arch.md` | 同步记录按需搜索机制。 |

重要边界：开启搜索后，即使最后判定不搜索，决策模型调用仍有延迟和成本；缺少搜索密钥时会先报告未配置。

## 建议的职责划分与验证状态

- 标题、设置、图片传输和搜索协议均直接支撑此次 WebUI 功能，但分别需要 Agent/Gateway 负责人复核。
- 优先复核共享配置写入、核心输入契约、core 插件的上下文等待及文件传输边界；不应将其视作纯样式改动。
- `/resume/` 忽略规则没有功能依赖。若需从 PR 去除，须显式整理提交历史或以反向提交抵消；本轮未自行改写历史。
- 未修改 Mem0、OpenKB、TUI 的应用源码；没有 Rela 后端接入。WebUI 内的 memory-app/knowledge-app 是前端包。
- 此前聚焦验证为 Agent 65 passed、Gateway 27 passed，不代表 Agent/Gateway 完整应用套件全部通过。
  WebUI 全量与受影响组复测去重后为 296 passed、13 skipped，环境和报告见
  [PR 修复复验](../2026-10-24-webui-performance/plan-validation.md#2026-10-25-pr-修复复验)。
- 首次范围复核阶段没有新增提交、推送或 PR；随后经用户要求进入发布准备。

## 研究文档归属

已将根目录未跟踪文件 `docs/research/2026-10-24-deepseek-web-image-upload.md`
移至 [WebUI 图片上传研究](../../research/2026-10-24-deepseek-web-image-upload.md)，移动前后 SHA-256 一致：
`E08E54F88F60AF0F187241BEF65F5BC5573EDE8E580BB2AC83F970B283BFC179`。

该文档不是本轮设计约束，不要求创建根目录跨应用 spec；没有将其他个人或实验文件纳入 PR。

## 发布前范围清理

- 取消 `.gitignore` 中与本功能无关的 `/resume/` 改动，本地忽略写入 `.git/info/exclude`；
  保留原提交历史，以新提交抵消，因此最终 PR 文件差异不再包含 `.gitignore`。
  外部最终差异相应为 45 个文件：Agent 33、Gateway 10、根目录 2。
- 性能逆向调研、架构与安全评估也归入 WebUI 的 `docs/research/`，并修正相对链接；
  应用设计和验证文档继续放在现有 `docs/spec/<feature>/`，根目录只保留跨应用契约。
- 个人目录 `.workbuddy/`、根目录实验页 `pelican-bicycle.html`、环境文件、构建产物、
  截图和测试 XML 均不纳入提交。应用使用的 SVG、字体和动画是运行资源，保留在相应包内。
- 输入框隐藏尚未接入控制接口的“深度思考”按钮；服务端思考能力与搜索设置保持原行为。
- PR 描述见 [提交说明](plan-pull-request.md)。

## 外部文件完整清单（46 个）

```text
.example.env
.gitignore
apps/agent/docs/spec/2026-10-23-client-settings/plan.md
apps/agent/docs/spec/2026-10-23-web-search/arch.md
apps/agent/scripts/check-package.sh
apps/agent/src/agent_app/application/controllers/session_api.py
apps/agent/src/agent_app/application/repositories/session_store.py
apps/agent/src/agent_app/application/services/common.py
apps/agent/src/agent_app/application/session_runtime.py
apps/agent/src/agent_app/application/session_title.py
apps/agent/src/agent_app/config/settings_service.py
apps/agent/src/agent_app/contracts/web_search.py
apps/agent/src/agent_app/plugins/runtime_update/manifest.json
apps/agent/src/agent_app/plugins/runtime_update/plugin.py
apps/agent/src/agent_app/plugins/user_input/plugin.py
apps/agent/src/agent_app/plugins/web_search/__init__.py
apps/agent/src/agent_app/plugins/web_search/config.py
apps/agent/src/agent_app/plugins/web_search/factory.py
apps/agent/src/agent_app/plugins/web_search/http_backend.py
apps/agent/src/agent_app/plugins/web_search/manifest.json
apps/agent/src/agent_app/plugins/web_search/pipeline.py
apps/agent/src/agent_app/plugins/web_search/plugin.py
apps/agent/src/icarus/core/contracts/input.py
apps/agent/src/icarus/core/ports.py
apps/agent/src/icarus/core/runtime_update.py
apps/agent/src/icarus/runtime/session.py
apps/agent/test/agent_app/application/test_agent_runtime.py
apps/agent/test/agent_app/application/test_search_submission.py
apps/agent/test/agent_app/application/test_session_title.py
apps/agent/test/agent_app/config/test_settings_service.py
apps/agent/test/agent_app/plugins/runtime_update/test_plugin.py
apps/agent/test/agent_app/plugins/web_search/__init__.py
apps/agent/test/agent_app/plugins/web_search/test_integration.py
apps/agent/test/agent_app/plugins/web_search/test_pipeline.py
apps/agent/test/test_packaging.py
apps/gateway/README.md
apps/gateway/docs/spec/2026-10-23-web-search/arch.md
apps/gateway/docs/spec/2026-10-24-image-resources/arch.md
apps/gateway/src/app.py
apps/gateway/src/protocol/methods.py
apps/gateway/src/resources.py
apps/gateway/test/test_image_e2e.py
apps/gateway/test/test_methods.py
apps/gateway/test/test_resources.py
apps/gateway/test/test_web_search_options.py
spec/2026-10-23-client-settings-and-session-titles.md
```
