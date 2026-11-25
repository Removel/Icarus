# WebSearch 默认由 Agent 按需使用：调查与改造建议

调查日期：2026-11-05。状态：调查完成，以下方案尚未实现。

代码基线：`13743b0`，分支 `feat/agent`。已 fetch 分支跟踪的 `upstream/feature`，
远端基线为 `c019e9a`，当前 HEAD 包含该基线并领先 2 个提交；`origin/feature`
与它不同，本调查不将两者混用。未切换分支、提交、合并或推送。

本次只新增调查文档。复用现有联网功能目录，保留首次进入 Git 的目录日期。
当前行为见 [arch.md](arch.md)，此前工具化实施记录见 [plan.md](plan.md) 和
[plan-validation.md](plan-validation.md)。本文中的建议不替代当前架构描述。
后续任务、应用间依赖及验收步骤见 [执行方案](plan-agent-autonomy.md)。

## 1. 调查结论

用户感受到的“只能从 Web 端使用”有代码依据，但限制来自请求开关，而不是搜索在浏览器执行。

1. `web_search`、`web_fetch` 已经是 Agent 应用内独立 `web-search` 插件提供的工具。
   主 Agent 已经可以决定查询词、直接读页、连续补搜；不需要重新实现一次工具化。
2. 插件仅在 `request_options.web_search is True` 时允许调用。字段缺失也会关闭，
   同时向当前 User Prompt 注入“未启用联网工具，调用会失败”的提示。
3. WebUI 有联网按钮，默认关闭，每条提交显式携带布尔值。TUI 的提交接口不传
   `request_options`，所以从 TUI 发起的普通任务默认无法使用这两个工具。
4. Gateway 会给已提供的 `request_options` 补默认值：即使客户端仅传时间、时区，
   透传时也会产生 `web_search=false`。仅修改 Agent 的字段缺省值仍会漏掉这些请求。
5. 建议把“客户端逐轮开启才可用”改为“默认允许 Agent 按需调用，显式关闭仍生效”。
   搜索执行、任务状态和预算继续归属 Agent 插件，WebUI/TUI 不负责决定是否需要检索。

这里的“自主”指主 Agent 在有效任务及服务端预算内选择是否调用工具，不表示输入一到达
就自动搜索，也不表示把搜索业务下移到无状态的 `ReActAgent` 或供应商适配层。

## 2. 代码证据与现有调用链

以下路径以所在应用为归属，均已检查当前源码；旧设计稿只用于历史背景。

| 位置 | 当前行为及影响 |
| --- | --- |
| Agent [web_search/manifest.json](../../../src/agent_app/plugins/web_search/manifest.json) | 声明 `web_search`、`web_fetch`，入口为 Agent 侧 factory；与浏览器生命周期无关 |
| Agent [web_search/factory.py](../../../src/agent_app/plugins/web_search/factory.py) | 装配插件和 HTTP backend；从服务进程环境读取独立搜索 Key，无独立查询规划模型 |
| Agent [web_search/plugin.py](../../../src/agent_app/plugins/web_search/plugin.py) 的 `on_input` | 保存任务快照；以 `get("web_search") is True` 判定启用；立即贡献时间及使用指引，不预取搜索资料 |
| 同文件 `_check`、`execute` | 除开关外，要求任务身份有效、TaskChannel 正在运行、插件可接纳工作、Key 存在，并检查剩余额度 |
| Agent [web_search/tools.py](../../../src/agent_app/plugins/web_search/tools.py) | 两个工具的 schema、同步/异步入口已具备；description 仍写着“仅本轮启用联网时可用” |
| Agent [session_runtime.py](../../../src/agent_app/application/session_runtime.py) | 装配搜索配置和任务设施；绑定工具目录；搜索插件活跃时，等待其初始 context contribution 后再启动 Agent |
| Agent [assembly.py](../../../src/icarus/runtime/assembly.py) | 通用装配通过 `build_tools()` 取得插件工具，验证与 manifest 一致后注册 |
| Agent [tool_search.py](../../../src/icarus/core/tools/tool_search.py)、[tool_execute.py](../../../src/icarus/core/tools/tool_execute.py) | 发现完整工具定义后才能在当前 Run 分发；传递可信任务身份；发现不等于业务执行许可 |
| Gateway [methods.py](../../../../gateway/src/protocol/methods.py) | `RequestOptions.web_search` 为严格布尔，默认 false；`_session_submit` 使用普通 `model_dump()`，将缺省值一起传给 Agent |
| WebUI [useChatSession.ts](../../../../webui/packages/chat-app/src/useChatSession.ts) | 偏好 `icarus.chat.webSearch` 默认 false；提交/排队保存开关快照；发送前还有 false 的兜底值 |
| WebUI [DswComposer.tsx](../../../../webui/packages/chat-app/src/DswComposer.tsx)、[webSearch.ts](../../../../webui/packages/chat-app/src/webSearch.ts) | 联网按钮控制该偏好；`SearchPreference` 要求 `web_search` 为必填 boolean |
| TUI [client.py](../../../../tui/src/client/gateway/client.py) 的 `submit` | 发送 prompt、resources、submission_id 等，不携带 `request_options`；没有联网启用入口 |

实际执行路径：

```text
WebUI / TUI / 其他调用者
  -> Gateway session.submit（校验并透传，或进程内直接调用 Runtime）
  -> AgentRuntime / SessionRuntime
  -> UserInputEvent
  -> WebSearchPlugin 保存开关、预算模式及任务快照，贡献动态上下文
  -> context.ready -> 主 Agent 推理
  -> tool_search(query="web-search")
  -> tool_execute(name="web_search", arguments={"query": "..."})
     或 tool_execute(name="web_fetch", arguments={"url": "..."})
  -> WebSearchPlugin 校验 -> SearchHttpBackend -> 远端 Search/Fetch 服务
  -> ToolExecutionResult 回到 Agent -> 决定补搜、读页或回答
```

默认模型工具面仍是常驻九工具，联网工具属于延迟加载目录。每个新 Run 重新发现，
不需要改为常驻工具才能实现自主使用。工具返回资料经既有执行器进入后续模型消息，
进度经 `web_search.updated` 投影、持久化，再由客户端消费。即使客户端没有专用搜索卡片，
也不应影响 Agent 获取工具结果或完成回答。

## 3. 当前可复现的启用矩阵

在配置测试 Key、有效 RUNNING 任务、其余额度充足的条件下：

| 提交情况 | 插件看到的启用值 | 当前结果 |
| --- | --- | --- |
| 进程内提交不带 options / TUI 普通提交 | 缺失 | 拒绝，零搜索请求 |
| 进程内只带时间 | 缺失 | 拒绝，零搜索请求 |
| 经 Gateway 传 `request_options={}` | 默认补为 false | 拒绝 |
| 经 Gateway 只传时间、时区 | 默认补为 false | 拒绝 |
| WebUI 默认提交 | 显式 false | 拒绝 |
| WebUI 开启按钮 / 任意合法调用者显式 true | true | 允许 Agent 调用；不调用工具仍零搜索请求 |
| 任意调用者显式 false | false | 拒绝，加载工具也不能绕过 |

因此不存在“服务端检查请求是否来自 Web”的判断。任何支持传入 true 的客户端都能启用，
当前 TUI 恰好没有这个参数。真实部署还可能受 Key 或远端接口不可用影响；本调查没有
检查或输出本机凭据，也没有据此判断当前部署已具备真实联网条件。

## 4. 建议方案：缺省自动，保留显式禁用

### 4.1 任务级语义

继续复用 `request_options.web_search`，用字段是否存在表达默认选择，避免引入重复的
`auto_search` 参数、第二套工具或新的搜索代理。

| 输入 | 建议行为 |
| --- | --- |
| 缺省整个 options / 缺省 `web_search` | 默认允许 Agent 在需要时搜索或读页 |
| `web_search=true` | 允许按需使用；兼容现有客户端，不强制预搜索 |
| `web_search=false` | 显式禁用这两个工具；保持零 HTTP 的执行边界 |
| `web_search=null`、字符串、数字等 | 参数错误，不把非法值当默认允许 |
| 缺省 `deep_search` / false | 维持普通模式预算 |
| `deep_search=true` | 沿用深度查证指导及较高次数预算；不能覆盖显式禁用 |

“默认允许”仍需满足搜索插件成功加载、任务有效、独立 Key 存在和额度充足。
不新增全局联网权限系统，也不把该开关扩展为 Bash/MCP 等其它工具的网络权限。

Agent 插件在接收输入时解析并保存有效策略，之后在本任务内保持稳定；不能改用“最近任务”，
不能由模型通过业务参数覆盖 task_id、模式、endpoint 或 Key。Gateway 保持严格输入校验，
进程内入口也须验证显式开关类型。不要用 `value is not False` 这类写法放行非法值。
实现时需要让非法输入的失败及时可见，避免 handler 异常使 required context 永久等待。

动态 User Prompt 改为说明“可按需使用”，给出当前 scope、调用方法、来源与预算规则。
推荐在要求最新事实、显式查证、核对链接或知识不足时检索；稳定知识和闲聊可直接回答。
缺 Key 时明确不可用及失败原因，避免鼓励徒劳调用；执行入口仍保留 Key 校验。
能力与任务策略进入当前 User Prompt，不修改稳定 System Prompt。

### 4.2 Gateway 必须保留字段缺失

不能仅把 `RequestOptions.web_search` 默认值改成 true，因为这会在传输层替 Agent
做业务默认决策，也继续丢失字段是否显式提供的信息。

建议校验仍沿用严格布尔模型，透传时使用 `model_dump(exclude_unset=True)`，保留客户端
实际提供的字段。这样仅传时间不会被补上 false；显式 false 则原样保留。无需允许 null，
也无需为区分缺省而新增公开模型。同步核对省略对象、空对象和明确布尔值的测试断言。

提交幂等要单独验收：[session_api.py](../../../src/agent_app/application/controllers/session_api.py)
及 [common.py](../../../src/agent_app/application/services/common.py) 会将 options 纳入指纹。
同一个 submission_id 重试必须保留首次请求的 options，不能在重试时重新生成时间或
重算模式。保留显式 false 与缺省的差异，不为了指纹相等而统一补默认值。
Gateway 新旧版本的补值方式不同，滚动升级时未完成提交的重试可能遇到指纹冲突；上线时应
先排空提交或对这类重试明确恢复流程，不能宣称线协议字段不变就完全没有行为兼容成本。

### 4.3 WebUI 需要停止默认发送 false

只完成 Agent/Gateway 改动，TUI 和省略字段的调用者即可按需使用，但旧 WebUI 的默认
提交仍明确禁用。这只能算第一阶段，不能宣称所有入口都已交给 Agent 自主判断。

建议 WebUI 默认选项改为“自动”，提交时省略 `web_search`。显式“关闭”才发 false；
旧 true 可作为允许的兼容值。可以将手动禁用留在设置中，取消主输入区每轮必须开启的门槛。
深度查证偏好继续保留；搜索进度和引用展示复用原事件，不需要前端调用搜索接口。

需要同时调整 `SearchPreference` 类型、偏好读取、普通提交、排队快照、重试和发送前兜底。
只将初始 false 改为 true 不够：[useChatPreference.ts](../../../../webui/packages/chat-app/src/useChatPreference.ts)
会优先恢复 localStorage 中的旧 false；旧版自动写入与用户明确禁用的 false 又无法区分。
2026-11-05 根据用户明确的开发阶段约束，执行方案取消旧偏好兼容：改用
`icarus.chat.autoWebSearch`，默认自动，不读取或迁移旧 key；只保存改版后的主动选择。
无需实现旧 false 恢复入口或偏好迁移逻辑。

### 4.4 TUI 无须先补一个联网开关

现有 TUI 提交不带搜索参数，服务端缺省变为允许后即可使用。首轮验收应覆盖这种真实
请求形状，而不是在测试里额外传 true。TUI 专用搜索进度/来源展示属于后续体验改进，
不应成为工具执行的前置条件；本次未验证它对搜索专用事件的完整呈现能力。

## 5. 保留的实现边界与已知限制

- 保留 `web-search` 为独立业务插件。`ReActAgent` 无状态，不依赖 UI、搜索插件或
  Blackboard；通用 Runtime、Hooks、model_provider 不加入搜索专用判断。
- 复用两个工具、延迟加载、同步入口回到 Runtime loop、可信执行身份及现有输出处理。
  工具执行产生结果，插件通过 Event 发布进度；不新增子插件或后台自动检索流程。
- 保留普通 Search 2 次 / Fetch 3 次、深度 5 / 6 次，单页 6000、累计 24000 字符，
  默认活跃工作时间 45 秒及既有请求间隔。自主判断不意味着无限搜索或自动提高预算。
- 保留取消、终态任务拒绝、迟到结果失效、稳定来源编号、快照及历史顺序。外部资料是不可信
  数据，搜索摘要不代表阅读正文；不因客户端不展示搜索卡片而省略执行记录。
- 当前 backend 使用服务端配置的 endpoint，默认 `https://api.deepseek.com/v1`，请求
  `/tools/search` 和 `/tools/fetch`，不是模型供应商内置 web search。
  连接参数、凭据和远端契约继续封装在插件 backend；不向上层增加厂商协议分支。
- 当前本地 Fetch URL 策略不能证明远端抓取服务逐跳校验 DNS/IP/重定向。既有
  [外部待验事项](plan-validation.md) 继续有效；离线测试不能证明真实服务可用或远端安全。
  默认允许会扩大实际调用机会，这一部署前置条件需要在后续上线验收中继续跟踪。
- 缺少本地时间选项时，插件回退到运行进程所在时区，不能由 TUI 请求推断用户时区。
  不把“服务端当前时间”描述成“已获得客户端本地时间”。

## 6. 后续实施拆分与验收要求

本节是建议工作项，尚未修改代码或增加测试。所有应用拥有自己的实现和验收文档；
这项工作可按归属拆分，不新增根目录跨应用 spec。Gateway 变更记录放入其
[既有功能目录](../../../../gateway/docs/spec/2026-10-23-web-search/arch.md)，WebUI 变更记录放入其
[既有功能目录](../../../../webui/docs/spec/2026-10-23-web-search/plan.md)。

| 阶段 | 主要文件与测试 | 可观察结果 |
| --- | --- | --- |
| 1. Agent 缺省策略及指引 | `web_search/{plugin,tools}.py`；`test_tools.py`、`test_integration.py`；提交幂等专项 | 省略开关即可通过真实发现/分发链执行；不调用工具时零 HTTP；false、缺 Key、非法类型、终态任务不越过执行边界 |
| 2. Gateway 保留字段缺失 | `apps/gateway/src/protocol/methods.py`；`test_web_search_options.py`；Gateway 生产运行链集成测试 | 省略、空对象、只传时间均不合成 false；true/false 原样传递；类型及未知字段仍严格校验；TUI 形状请求能够搜索 |
| 3. WebUI 默认自动 | `useChatSession.ts`、`webSearch.ts`、`DswComposer.tsx`，必要时设置组件；现有浏览器专项 | 统一默认自动，不迁移旧开关偏好；主动关闭可保存；排队、重试不改变已捕获策略；来源与历史正常 |
| 4. 文档与验证记录 | 各应用现有功能目录 | 按实现结果更新 arch/plan-validation，区分离线验收与真实服务/模型待验 |

建议先写出能失败的目标行为用例，再实现相应最小改动。至少补充这些此前未覆盖的场景：

1. 真实 SessionRuntime 不传 options，假 LLM 走 `tool_search -> web_search -> web_fetch ->
   补搜 -> 回答`；再以同样默认策略直接 Fetch。冻结工具面及来源编号保持稳定。
2. 不带 options 但模型直接回答，搜索请求为零；显式 false 即使模型强行调用也被拒绝。
3. 经 Gateway 仅传 local_time/time_zone，实际传到插件的开关仍为缺省；不是只断言
   Pydantic 模型解析成功。
4. 缺 Key、非法开关类型、普通/深度预算、取消、历史来源及新 Run 重新发现回归；
   `deep_search=true + web_search=false` 仍禁用。
5. 同一 submission_id 的重试保留模式和时间；改变显式禁用等语义继续返回冲突。
6. WebUI 默认自动、主动关闭及刷新持久化、排队期间切换偏好、失败重试和直接抓取的浏览器测试。

按仓库顺序执行最小专项、受影响应用全套、编译及 diff 检查。最小专项命令为：

```powershell
& apps/agent/.venv/windows/Scripts/python.exe -m pytest apps/agent/test/agent_app/plugins/web_search -q
& apps/agent/.venv/windows/Scripts/python.exe -m pytest apps/agent/test/agent_app/application/test_search_submission.py -q
& apps/gateway/.venv/windows/Scripts/python.exe -m pytest apps/gateway/test/test_web_search_options.py -q
```

应用全套使用仓库 `make test-agent`、`make test-gateway`、`make test-webui` 入口。
真实服务与模型验收需另行按仓库规则授权执行，不能用预置 LLM 工具调用序列声称模型已学会
自主选择搜索。回退时恢复旧缺省禁用策略、Gateway 透传和 WebUI 偏好行为；不涉及数据库迁移，
不删除会话历史或用户存储偏好。

## 7. 本次调查验证记录

2026-11-05，在现有 Windows 应用环境、当前 HEAD 执行：

| 检查 | 实际结果 | 证据范围 |
| --- | --- | --- |
| Agent `test/agent_app/plugins/web_search` | **75 passed in 41.61s** | 现有插件、真实 ReAct/Runtime 代码配合假 LLM 与 HTTP fixture 的行为；不是缺省自动方案验收 |
| Gateway `test/test_web_search_options.py` | **1 passed in 36.68s** | 现有选项严格校验与透传 |
| 临时进程内探针，测试 backend，不写文件 | 缺省 / 仅时间 / false：enabled=false、失败、0 请求；true：成功、1 次 fixture 搜索 | 直接复现插件门槛；未访问远端服务 |
| `RequestOptions` 解析及普通 `model_dump()` 探针 | `{}` 和仅时间对象均补出 `web_search=false`；`model_fields_set` 仍能区分显式字段 | 复现 Gateway 补值问题及保留字段缺失的可行依据 |
| 文档检查 | 22 个相对链接均存在，代码围栏配对、无行尾空白；`git diff --check` 通过 | 文档结构及工作树差异检查 |

本次没有运行应用全套、WebUI 浏览器测试、真实模型或真实搜索服务；没有修改运行时代码、
配置或凭据。工作树原有 `.gitignore` 修改及无关未跟踪文件不属于本调查变更。
