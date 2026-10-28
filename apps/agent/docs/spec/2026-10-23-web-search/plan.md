# Agent 自主联网检索实施计划

记录日期：2026-10-26。状态：工具化代码已实现；本地验证及外部待验事项见 [验证记录](plan-validation.md)。
审阅修订：2026-10-26，对照源码补充 DeepSeek 文本审阅提出的边界与验收项。

## 目标与设计依据

将联网检索从“回答前自动准备资料”改为“Agent 在推理过程中按需调用工具”。
Agent 决定是否搜索、查询词、阅读页面和后续查证；搜索插件负责请求执行、来源编号、
请求预算、进度与生命周期。支持搜索后继续搜索，也支持直接阅读用户提供的 URL。

当前实现见 [arch.md](arch.md)，实际检查见 [验证记录](plan-validation.md)。下文保留
实现前的设计依据与验收要求；已实现范围由任务勾选和验证记录共同说明。沿用既有功能目录。
计划结构遵循仓库 [实施计划模板](../../../../../docs/templates/spec/plan.md)。

关联边界：

- [Gateway 请求选项](../../../../gateway/docs/spec/2026-10-23-web-search/arch.md)。
- [WebUI 联网搜索计划](../../../../webui/docs/spec/2026-10-23-web-search/plan.md)。

本次实现归属 Agent 应用，复用现有请求选项和进度事件，不新增根目录跨应用 spec。
若实施发现必须修改 Gateway 或 WebUI，各应用在自己的功能文档中记录其改动。

## 基线与约束

调研代码基线：`0cb13f8`。调研时当前分支为 `feat/webui`；本计划不授权切换分支、
提交、合并或推送。开始实现前须重新核对工作树、目标 remote 和对应 `feature`
基线，遵循仓库 Git 规则，不覆盖现有未跟踪文件。

2026-10-26 在现有 Windows Agent 环境执行：

```powershell
& apps/agent/.venv/windows/Scripts/python.exe -m pytest apps/agent/test/agent_app/plugins/web_search -q
```

结果为 `29 passed in 46.64s`。这是现有实现的专项基线，不是本计划的验收结果；
当时未运行应用全套、真实模型或真实搜索服务。改造前集成测试替换了 Agent，不能证明
真实 ReAct 工具循环已经支持搜索。

已检查的主要依据：

| 文件（相对 `apps/agent/`） | 已确认行为 |
| --- | --- |
| `src/agent_app/plugins/web_search/{plugin,factory,pipeline}.py` | 收到输入后规划、搜索与抓取，再贡献上下文 |
| `src/icarus/core/plugin.py`、`src/icarus/runtime/assembly.py` | `build_tools()` 注册工具，实际工具名须与 manifest 一致 |
| `src/icarus/core/agents/react_agent.py` | 每次执行取得工具快照，支持连续多轮工具调用 |
| `src/icarus/core/tools/{base_tool,tool_executor}.py` | 工具执行获得 `task_id` 等执行上下文，复用超时和输出预算 |
| `src/icarus/core/tools/execution_policy.py` | `_execution` 是允许模型提交的预算请求，由框架解析并与服务端限制合成 |
| `src/icarus/runtime/loop_runner.py` | 同步适配提交到既有 Runtime 循环，等待上限为 35 秒 |
| `src/agent_app/contracts/web_search.py` | 已包含 status、total、queries、sources、pages、browsing、notice |
| `src/agent_app/plugins/runtime_update/plugin.py` | 仅接受 `web-search` 来源的搜索进度事件 |
| `test/agent_app/plugins/web_search/` | HTTP 契约、自动检索、容错、取消与上下文注入的现有测试 |

边界要求：

- 不修改 ReActAgent 的无状态设计，不让其依赖搜索插件、Blackboard 或 UI。
- 通用 Plugin Runtime 不解释搜索事件，不新增供应商协议分支。
- 工具是插件内普通组件，不注册子插件；插件之间仍通过 Event 通信。
- 工具结果通过现有 `ToolExecutionResult` 返回 Agent；搜索进度通过插件 Event 发布。
- 本轮时间、开关和深度指引进入当前 User Prompt，不改写稳定 System Prompt。
- 不新增依赖、数据库表、持久化 state scope、搜索代理服务或浏览器直连接口。

## 接口与行为

### 1. 两个基础工具

| 工具 | 输入 | 输出与副作用 |
| --- | --- | --- |
| `web_search` | 必填 `query: str`，去除首尾空白后为 1–128 个 Unicode 字符 | 执行一次 Search，返回本次结果及稳定引用编号；不自动 Fetch |
| `web_fetch` | 必填 `url: str`，最长 8192 字符，通过下述公网 URL 安全策略 | 执行一次 Fetch，返回非空正文节选及稳定引用编号；成功后加入已浏览页面 |

两个业务 schema 都设 `additionalProperties=false`，拒绝 `task_id`、Key、endpoint、
深度、原问题和业务预算等额外参数。任务身份来自执行器，原问题和选项来自插件
保存的输入快照。框架执行预算与业务权限采用以下明确顺序：

1. `ToolExecutor.definitions()` 在业务 schema 的副本中加入保留字段 `_execution`；
   不是把未知字段交给模型随意扩展。
2. 模型可以按框架契约提交 `_execution.timeout_seconds`、`max_output_tokens`。
   `ToolExecutionPolicy.prepare()` 从参数副本取出 `_execution`，校验其类型、字段
   和范围，产生有效预算与剥离后的业务参数；非法值或嵌套 `task_id` 等未知字段
   拒绝且不调用后端。
3. 工具仅校验剥离后的业务参数；框架从调用上下文以独立关键字参数传入 `task_id`、
   `run_id`、`step`、`timeout_seconds` 等，模型同名业务字段不能覆盖它们。
4. 模型合法的预算请求仍不能增加插件剩余次数、正文额度或工作时间；插件按 §5
   取更小超时。直接绕过执行器调用工具时，业务参数中的 `_execution` 同样拒绝。

因此不是一律禁止模型传 `_execution`，也不修改其已有框架语义；拒绝的是非法控制
内容、未知业务参数以及将模型参数当作可信任务身份或服务端限制。

Search 沿用 `count=10` 和当前后端参数契约。超长 query 明确返回参数错误，不静默
截断后执行另一条查询。Fetch 可用于通过安全策略的公网 URL，无须先搜索；只向
配置的搜索服务提交 URL，不在 Agent 进程中新增直接访问目标页面的实现。

#### Fetch URL 安全边界与上线前置条件

当前 `valid_url()` 只验证基本格式，HTTP client 的 `follow_redirects=False` 只约束
Agent 到搜索 API 的请求，不约束搜索服务抓取目标网页的重定向。现有源码及已存
接口资料不足以证明外部服务具备完整 SSRF 防护，不能把服务的 Fetch 403 错误当作
安全保证，也不能用本机 DNS 解析结果代表远端解析结果。

本地入口先按规范解析 URL，拒绝非 HTTP(S)、带凭据、非法或歧义 host、非标准端口
（HTTP 仅 80，HTTPS 仅 443）、环回/私有/链路本地/组播/未指定及其它非公网 IP，
包括 IPv4-mapped IPv6 和非常规数值 IP 写法；拒绝 localhost、单标签主机名及
明确的本地保留域名。云元数据地址和已知元数据主机名必须拒绝。所有进入 Fetch
的 URL 都适用该策略，无论来自用户直接输入还是 Search，不能跳过检查。

域名通过格式检查不表示其远端解析地址安全。实际执行 Fetch 的服务必须具备并提供
可核验的防护依据：对初始目标和每一跳重定向重新校验协议、端口与所有 A/AAAA
地址，拒绝非公网目标；连接实际使用已校验地址，防止 DNS rebinding/解析与连接
之间的竞态；对重定向链设上限，不把服务凭据转发给目标站点。配置 endpoint 由
服务端管理员控制，沿用当前连接约束，不向模型开放。

T1 增加本地拒绝测试；T4 用受控 Fetch 服务验证“公网跳转到私网”和 DNS 地址
变化时没有访问禁用地址。受控服务须运行实际部署的 Fetch 实现或与供应商约定的
验证环境，不能用自行编造“拒绝访问”响应的假服务证明供应商安全。MockTransport
只能验证本地拒绝与 API 错误处理，不能
证明远端的逐跳校验或连接地址绑定。上线前记录服务防护契约或受控服务测试证据；
证据缺失时不得上线面向任意公网域名的 Fetch，可继续实现和验证 Search 与本地
fixture。不得以新增一个默认信任的配置开关替代该前置条件。

复用 `ToolDefinition`、`ToolExecutionResult` 和现有来源字段，不另建重复公共模型。
成功结果约定：

- Search 的 `output` 包含 `query`、本次实际返回行数 `total`、本次去重的 `sources`
  和阅读提示。来源包含 `citeIndex`、`url`、`title`，以及可用的摘要、站点与日期。
- Fetch 的 `output` 包含 `citeIndex`、`url`、`title`、正文节选 `content`、完整正文
  字符数 `contentLength` 和 `truncated`。正文仍受框架工具输出预算约束。
- 合法空搜索是 `success=True`、`sources=[]`；接口失败不是空结果。空正文的 Fetch
  是失败，不增加已浏览记录。失败返回 `success=False` 和可读、安全的 `error`。
- 搜索摘要和正文均标明是外部不可信资料，不能当作新的指令或授权；只有 Fetch
  返回非空正文才能声称读过正文。依据资料作答时使用实际返回的 `[n]` 编号。
  Search 摘要也允许引用，但指引须明确“引用搜索摘要不等于已阅读网页正文”。

### 2. 去掉回答前的自动研究

`UserInputEvent` 到达后先建立任务上下文，随即发布终态
`ContextContributionEvent`，贡献当前时间与本轮工具使用指引。保留 SessionRuntime
对 `web-search` 上下文来源的等待关系，保证工具调用前已保存本轮选项。

这里的“终态”仅表示本轮初始上下文准备完成，不是搜索任务或 Agent 任务完成。
顺序必须为：保存选项快照 → 发布完成态贡献 → Blackboard 汇齐上下文并发出
`context.ready` → Agent 启动 → 工具调用。测试检查该顺序，不依赖插件处理输入的
偶然调度次序，也不等首次工具调用时再回填权限状态。

此阶段不访问搜索服务，不调用规划模型。开启搜索表示允许按需调用，不表示每轮
必须搜索；闲聊或已有资料足够时可直接回答。深度开启时提示 Agent 在预算内优先
多来源交叉验证、阅读原文并补查缺口，不保证固定次数或检索质量。

移除 factory 中搜索专用的 `LLMFactory`、`ObservableLLM` 和规划 prompt。移除
`SearchPipeline.run` 的自动查询规划、默认选页与自动抓取编排，保留仍有工具调用者
的解析、URL 规范化、去重、排序与资料格式化函数，不保留无调用者的旧流程。
应用装配删除仅服务于该规划模型的设施注入。

### 3. 每轮权限与状态

插件按 `task_id` 保存原问题、选项快照、来源表、引用计数、累计进度、剩余预算和
在途操作。状态只存在于当前任务内，不依赖上一轮的联网选项，不持久化网页正文。

所有工具入口都检查：插件正在接受调用、任务上下文存在、TaskChannel 为运行态、
`web_search is True`、Key 已配置且预算充足。身份缺失、未知任务、已结束或取消任务
一律拒绝，不借用会话中的“最近任务”。模型参数不能覆盖这些事实。

工具可在插件加载时常驻注册，本阶段不新增按任务动态修改工具注册表的机制。
关闭搜索时即使模型构造调用也不得发起 HTTP；仅写 prompt 提示不构成权限控制。
本轮 User Prompt 明确说明“本轮未启用联网工具，调用会失败，请勿调用或反复重试”；
指引用于减少误调用，执行拒绝仍是最终约束。
此开关控制这两个工具，不限制 Bash 或其它工具的网络访问。

### 4. 稳定来源与编号

使用现有 `normalize_url` 作为任务内去重键。来源第一次对 Agent 可见时分配从 1
开始的编号，同一规范化 URL 后续搜索或 Fetch 复用编号，新来源追加，旧编号不重排。
每批结果可先排序再为新增来源分配编号；不能对累计来源重新按排名编号。

重复结果可补充更完整的元数据，不改变编号或让编号指向另一 URL。直接 Fetch
成功且正文非空后可创建来源；失败 Fetch 不凭空增加来源。Search 中包含 `content`
字段仍不代表 Fetch 成功，不能据此增加已浏览记录。

元数据补充只写入任务内部来源表，影响后续结果和事件；已经交付的 Tool Result
和已发布事件不回写。每次输出对嵌套来源、页面容器深拷贝成独立快照，不能只拷贝
外层 list/tuple；冻结 dataclass 本身不等于冻结内部 dict。测试保存先前结果和
事件，补充元数据后再次比对其值与对象隔离。

首版来源身份基于请求 URL 的规范化值。现有 Fetch 响应契约没有可验证的最终 URL，
不推断重定向目标，也不从正文声明的 canonical URL 重编号。因此不同请求 URL
即使落到同一页面，也可能有不同编号，这是已知去重边界。将来服务若提供可信的
最终 URL，可另行设计别名合并，但不得改变已经交付的编号。安全校验逐跳进行的
要求与这里的引用身份规则分别成立。

Tool Result 和进度事件复用同一份来源事实。已交付 Agent 的编号在任务结束前保持
不变；下一任务重新从 1 开始，历史使用该任务已保存的进度快照恢复。

### 5. 预算、错误与同步适配

以下为实现采用的默认策略，复用原先浅检索/深检索上限，不表示必须消耗完预算：

| 限额 | 普通 | 深度 |
| --- | --- | --- |
| 每任务 Search HTTP 尝试次数 | 2 | 5 |
| 每任务 Fetch HTTP 尝试次数 | 3 | 6 |
| 单页返回正文节选 | 最多 6000 字符 | 最多 6000 字符 |
| 每任务累计返回正文节选 | 最多 24000 字符 | 最多 24000 字符 |

参数或权限校验失败不消耗请求次数；请求实际发起即计次，失败、重复请求和重试也
占用次数。本阶段不添加隐式重试或缓存；Agent 可在剩余预算内选择其它查询和页面。
工具输出提示剩余次数及正文预算，耗尽后返回非致命错误，仍允许 Agent 回答。

`deadline_seconds` 保留配置名和默认 45 秒，语义从一次自动研究的墙钟上限改为
本任务累计搜索/抓取工作时间预算。输入到达及首次调用前不计时；操作通过本地参数、
权限与 URL 格式检查、进入执行/排队后开始计时，包含任务锁等待、DNS 安全检查
（如有）、限流等待、HTTP 和结果处理，不包含两次工具操作之间的 Agent 思考或
其它工具时间。失败、超时和取消的操作已经花费的工作时间也扣减。

采用单调时钟，定义：

```text
D = 本任务 deadline_seconds
W(t) = 截至 t 本任务所有已接纳操作活跃区间的并集长度（等待也属活跃）
R(t) = max(0, D - W(t))
P = Search 15s，Fetch 20s
F = 执行器传入的有效 timeout_seconds，未提供时视为 +∞
T = min(P, F, R(t0))                  # t0 为本次进入执行/排队的时刻
E = t0 + T                           # 本次绝对截止时刻，不在取得锁后重置
H(t) = max(0, min(E - t, R(t)))       # 取得锁、通过限流后，HTTP 可用的剩余时间
```

`T<=0`、`H<=0` 时不发起 HTTP。整个操作使用同一截止时刻，不能让 HTTP 的读写
阶段、锁或限流各自重新取得完整的 15/20 秒。正常 ReAct 按任务串行；即便直接
并发调用同任务工具，工作时间按区间并集扣减，不能因重叠等待被重复计算，也不能
因等待不计时绕过任务上限。测试使用可控时钟验证公式与边界。

限流沿用 `_gates` 的实际作用域：同一 Python 进程、同一事件循环内所有后端实例的
Search/Fetch 共用一把发起锁，相邻发起间隔至少 210ms，跨任务也共用；不同进程或
不同循环不共享，不宣称是跨部署的全局限流。任务状态和次数预算仍按 task 隔离。
同步桥提交回同一 Runtime 循环，不另建循环以规避限流。等待其它 task 的限流
占用时长也计入本任务活跃工作时间。

实际发起边界定义为“最后一次取消/预算校验之后，紧接 HTTP client 调用之前”，
计次、追加查询及 Search 开始事件在同一临界流程完成。无法知道网络对端是否真正
收到请求，因此计量的是已提交的本地 HTTP 尝试，不把它当作外部成功计费次数。
一旦越过边界即不退回计次；即使发布开始事件时被取消、尚未建立连接，也属于
已提交尝试。开始快照入队后才允许发布本次 stopped；计数不是成功检索的证据。

| 边界 | 请求次数 / `queries` | 工作时间 / 进度 |
| --- | --- | --- |
| 参数、权限、本地 URL 校验失败 | 不计次、不追加 | 不计工作时间，不新建搜索节点 |
| 已接纳，任务锁/限流等待中取消或超时 | 不计次、不追加 | 已耗等待扣减；若此前无进度则无 stopped 节点 |
| 跨过本地发起边界后立即取消、连接失败或超时 | 计一次；Search 追加实际 query | 扣减工作时间，已有开始事件后发对应终态 |
| 收到合法空搜索响应 | 计一次，追加 query；total 增量为 0 | empty，保留累计来源 |
| 收到错误或非法响应 | 已计次不退回，Search query 保留；total 不增加 | error，不伪装空搜索 |
| Fetch 发起/成功/失败 | 已发起即计次；从不追加 Search query | 仅非空正文成功增加页面 |
| Agent 显式重复请求或重试 | 每次越过发起边界均计次 | 按实际时间扣减，无隐式重试 |

该配置语义变更须写入实施后的架构说明，不能继续声称整个 Agent 任务只耗时 45 秒。

首版工具 `can_run_parallel=False`，并在插件内保证同任务操作串行、状态更新原子，
避免累计事件倒序和编号竞态。不为尚不存在的需求引入并行调度抽象。

异步入口直接在 Runtime 所属循环执行；同步入口复用注入的 `_loop_runner` 提交
同一操作，不能用 `asyncio.run()` 为共享 HTTP client、锁和发布器新建事件循环。
单次 15/20 秒操作上限低于现有同步桥 35 秒等待上限；测试还须覆盖串行等待也被
计入超时的情况。Runtime 循环线程内使用异步入口；未绑定或关闭时返回明确失败。

缺少 Key、鉴权失败、限流、接口不支持、网络错误和超时均返回安全提示，沿用
HTTP 后端对响应与密钥的隔离。一次失败不删除之前成功的来源和页面，不终止
整个 Agent 任务，不把失败伪装成成功、空搜索或已浏览。

### 6. 累计进度、取消和历史

保留 `WebSearchUpdatedEvent` 及其现有字段，发布者仍是 `web-search` 插件。普通工具
开始/结束事件沿用 Agent 原始执行流；搜索插件不伪造 Agent 事件，也不用 Hook
触发业务处理。搜索事件字段约定为当前任务的完整累计快照：

已对照 `contracts/web_search.py` 及 `runtime_update/plugin.py:on_web_search`：下列
字段全部已存在且已投影到 RuntimeUpdate，本计划不新增字段或事件类型。新增的
预算信息仅进入工具结果/本轮指引，不暗中扩展公开进度协议。T3 先断言当前字段集
与投影一致；如果实施需要扩展，先修订契约与各应用兼容计划。

| 字段 | 约定 |
| --- | --- |
| `queries` | 越过本地发起边界的 Search 查询，按提交顺序追加，真实重复调用也保留 |
| `total` | 成功 Search 响应的原始结果行数之和，不用去重来源数或请求 count 代替 |
| `sources` | 全部已分配编号的累计来源，不携带正文；直接 Fetch 的来源也包含在内 |
| `pages` | 成功取得非空正文的页面，按规范化 URL 去重，不记录失败项 |
| `browsing` | 当前是否存在未完成的 Fetch；终止后必须为 false |
| `status`、`notice` | 表达当前操作结果或停止原因，不能抹去既有成功事实 |

事件转换表如下。每个事件均携带累计快照；表中的状态是当前操作状态，初始“无”
表示未发布过搜索事件，不能用一个虚假初始节点填补。

| 触发 | 前置条件 | `status` | `browsing` | 发布与后续 |
| --- | --- | --- | --- | --- |
| Search 跨过发起边界 | 任务运行且允许调用 | searching | false | 追加 query，再发布开始快照 |
| Search 成功且本次有有效来源 | Search 在途 | complete | false | 合并来源，发布成功快照 |
| Search 成功但本次无有效来源 | Search 在途 | empty | false | 保留历史来源与本次真实 total 增量 |
| Search 失败或操作超时 | 已有进度 | error | false | 保留历史成果，允许后续调用 |
| Fetch 跨过发起边界 | 可从无进度直接进入 | complete | true | 立即创建浏览进度；不伪造 Search query |
| Fetch 成功且正文非空 | Fetch 在途 | complete | false | 添加/更新来源与成功页面 |
| Fetch 失败、空正文或超时 | Fetch 已发起 | error | false | 不增加页面，保留历史成果 |
| 任一在途操作因任务取消 | 已发过开始或浏览进度 | stopped | false | 任务不可再调用；终态只发布一次 |
| 接纳后、发起前失败/取消 | 本次未发开始事件 | error 或 stopped（仅已有节点时） | false | 没有历史节点则仅返回失败，不创建节点 |
| 已完成操作后 Agent 继续思考 | 无在途搜索/抓取 | 保持最近快照 | false | 无需为思考创建进度事件 |
| InputFinishedEvent 到达 | 成功/失败/取消均适用 | 不再发布搜索事件 | false（已有最终快照） | 清理状态，拒绝迟到结果与新调用 |

`complete` 表示当前操作结束，后续工具调用可以再次进入工作状态；只有
`input.finished` 表示任务结束。直接 Fetch 开始就属于已有进度，即使没有 Search，
取消也须先有 `browsing=true` 快照再有 `stopped,browsing=false`；Search 在途取消
则先有 `searching` 再有 `stopped`。没有在途操作且最近快照已经结束时，任务取消
无须把已完成成果重新标记为 stopped。
直接 Fetch 时 `total=0` 是合法事实，前端应能显示来源和已浏览页面，不能补造搜索数。

目前 WebUI 的 `updates.ts` 按 `search:<task_id>` 覆盖状态，因此每次都发完整快照；
不扩展事件协议也可继续使用来源面板、引用角标和历史回放。验收需确认重复操作和
直接 Fetch 的展示；若需调整文案，由 WebUI 应用另记计划，不把其实现塞进 Agent。

`TaskCancelRequestedEvent` 标记任务不可再调用并取消在途操作。无论成功、失败还是
取消，`InputFinishedEvent` 都清理该任务状态和句柄。清理后到达的 HTTP 结果不得
重新建状态。以捕获的任务状态对象仍是当前映射值且处于有效状态作为写入条件；
清理/reload 后的旧引用失效，不为此新增公共 generation/epoch 协议。

取消顺序是验收约束，不是独立 inbox 自动提供的保证：在途工具拥有本次终态，
捕获取消后先失效化操作，再完成有界的 `stopped` 发布，最后传播 CancelledError。
搜索插件的取消 handler 只标记/取消并等待同一个终态结果，不能竞争发布第二份。
必须在真实 Runtime 的公开更新流观察到：

```text
searching 或 browsing=true → stopped,browsing=false → task.finished
```

不能在收到 `InputFinishedEvent` 后才补 stopped；`runtime_update` 的发布顺序与
历史 sequence 都要检验，而不只断言调用 `publish()` 的顺序。框架 ToolExecutor
当前取消宽限为 1 秒，正常取消的终态入队必须在该范围内完成。若独立 inbox 或
发布阻塞导致此顺序无法满足，T4 应失败并记录具体应用编排缺口，不能用 sleep 或
让通用 Runtime 解释搜索事件掩盖问题；异常超时不得标记为正常验收通过。

插件 `stop()` 先停止接受调用并使所有任务状态失效，再取消在途操作。使用插件内
总计最多 5 秒的清理期限，`asyncio.wait` 等有界等待与 HTTP client 关闭共享同一
期限，不为每个操作重新获得 5 秒；不得无限 gather 一个吞掉取消的协程。超时记录
清理失败和未确认取消事实，剩余句柄只负责回收异常且永远不能写状态/发进度。
上层传入更短停止期限时服从上层取消，不吞掉取消。停止可能发生在 EventBus 关闭
后，此时不尝试补公开事件；正常任务取消的先后顺序必须在 quiesce 阶段完成。
用吞取消/迟到返回的 fixture 测试有界停止与失效状态防护，不能把超时写成清理成功。

## 实施任务

任务勾选反映本地实现与验证；未完成的外部服务验收保留待办。行为失败
测试、实际结果与文件合并调整见 [验证记录](plan-validation.md)。用户已在人工试用后授权本地提交。

### T1：工具注册与按需执行

文件：新增 `src/agent_app/plugins/web_search/tools.py`；修改同目录
`plugin.py`、`factory.py`、`manifest.json`、`pipeline.py`；新增镜像
`test/agent_app/plugins/web_search/test_tools.py`，调整 `test_pipeline.py`。

- [x] 定义两个工具的 schema、参数验证和 `ToolExecutionResult`，拒绝未知参数。
- [x] 经真实执行器验证合法 `_execution` 被剥离并合成预算；非法 `_execution`、
  伪造 task_id、业务预算字段及直接调用工具携带 `_execution` 均失败且零 HTTP。
- [x] `build_tools()` 返回两个工具，manifest `provided_tools` 与实际名称严格一致。
- [x] Search 不自动 Fetch，Fetch 可直接接受用户 URL，空结果与错误可区分。
- [x] 测试内网/环回/元数据/映射 IPv6/歧义 IP、非法协议、凭据 URL、非标准端口
  本地拒绝；搜索结果 URL 不绕过策略。远端 SSRF 防护证据保持为独立上线前置项。
- [x] 删除独立规划与自动编排，保留有调用者的处理函数及 HTTP 契约测试。
- [x] 验证 Runtime 真正加载这两个工具，既有事件、能力和 state 声明仍一致。

### T2：任务上下文、权限和生命周期

文件：修改插件 `plugin.py`、`tools.py`、`config.py`（仅在实现需要时）及
`src/agent_app/application/session_runtime.py`；新增镜像 `test_lifecycle.py`，
补充 `test_tools.py` 与应用 `test_search_submission.py`。

- [x] 输入先建立任务快照，再贡献时间与本轮指引；未调用工具时零搜索/规划请求。
- [x] 明确断言选项快照先于完成态贡献和 context.ready，工具不懒建任务身份。
- [x] 工具从执行上下文取得任务身份，验证关闭、未知、结束、取消任务均零 HTTP。
- [x] 实现请求次数、正文与累计工作时间预算，耗尽、超时和缺 Key 不阻止正常回答。
- [x] 按公式覆盖首次调用前/操作间思考不扣减、锁和跨 task 限流等待扣减、超时
  不重置、同任务意外并发区间不双扣；按计次边界表逐项断言次数与 queries。
- [x] 同步调用通过 RuntimeLoopRunner，与异步调用共享校验、预算和结果语义。
- [x] 所有任务终态和插件停止均清理；取消中、预算等待中及迟到响应不泄漏状态。
- [x] 吞掉取消、5 秒清理期限、上层更短取消、停止重建后旧响应都被测试；不允许
  迟到操作重新建状态或写入新任务，无界等待不算清理成功。

### T3：稳定编号与累计进度

文件：修改插件 `pipeline.py`、`plugin.py`，确有需要时在插件内新增
`sources.py` 承载来源表；来源与进度测试集中在镜像 `test_tools.py`、`test_lifecycle.py`。
继续复用 `src/agent_app/contracts/web_search.py` 和 RuntimeUpdate 投影契约。

- [x] 先核对并断言现有事件字段与投影，无未记录的协议扩展。
- [x] 连续搜索、重复 URL、排序变化和直接 Fetch 都保持已交付编号稳定。
- [x] 区分本次 Tool Result 与累计事件；正文只进 Tool Result，来源编号两边一致。
- [x] 部分失败、空正文、取消和预算耗尽保留既有成果，不生成假页面或假搜索数。
- [x] 每次发布完整、独立的快照，不因后续可变对象修改而改变历史事件内容。
- [x] 元数据补充不改变旧 Tool Result 的嵌套字典与旧事件；请求 URL 身份不因
  服务重定向或正文 canonical 提示改变。显式记录不同 URL 的去重边界。
- [x] 按状态转换表覆盖 Search/Fetch 各种终态、直接 Fetch 取消、发起前取消与
  已结束后的拒绝；零调用不得创建虚假进度节点。
- [x] 验证取消事件在任务结束前可观察，结束后不再发布迟到搜索结果。

### T4：真实工具循环与兼容验收

文件：改造 `test/agent_app/plugins/web_search/test_integration.py`；按失败身份
补充已有 RuntimeUpdate、打包与应用测试。Agent 内核和通用 Runtime 仅作为被测
依赖，不因测试便利添加搜索专用分支。

- [x] 使用真实 ReActAgent、ToolExecutor、插件注册图和本地 HTTP fixture；只替换
  模型响应为可重复的 Tool Call 脚本，不替换 Agent 本身。
- [x] 验证“搜索 → 抓取 → 补搜 → 回答”与“直接抓取 → 回答”，检查每步真实
  Tool Result、编号、HTTP 次数、公开事件和最终历史。
- [x] 验证模型不开工具、关闭开关后尝试调用、缺 Key、空结果、抓取失败、超时和取消。
- [x] 真实 Runtime 下分别取消在途 Search 和直接 Fetch，公开更新与历史 sequence
  均满足“开始 → stopped 且 browsing=false → task.finished”，finished 后无搜索更新。
- [ ] 用受控 Fetch 服务验证重定向私网、混合公网/私网解析与 DNS 地址变化均不能
  访问禁用地址；明确哪些是本地 fixture，哪些仍需服务方防护契约/部署验收证据。
- [x] 验证前一任务的开关、来源、预算不影响下一任务，稳定 System Prompt 不变。
- [x] 验证 Gateway 既有选项与提交幂等、WebUI 累计快照及引用回放；若没有修改对应
  应用代码，保留现有协议并运行与本改造相关的检查，不宣称已完成浏览器人工验收。

### T5：按实现更新文档

文件：本计划、同目录 `arch.md`；若跨应用确有变动，再更新对应应用功能文档。

- [x] 将已实现工具、开关、预算语义、事件与清理行为写回架构，删除失效描述。
- [x] 逐项记录实际结果、原有失败身份和未执行项；不提前勾选计划或填入预测结果。
- [x] 检查相对链接，保持原功能目录及既有历史文档稳定。

## 审阅落实与验收前置项

| 审阅点 | 处理与验收位置 |
| --- | --- |
| Fetch URL 安全 | §1 分开本地拒绝与远端逐跳/DNS 防护；T1/T4 验证，服务证据未核实前不开放任意公网 Fetch |
| 事件状态与取消顺序 | §6 状态表、单一终态所有者、发布宽限；T3/T4 真实 Runtime 顺序验收 |
| 预算与限流 | §5 时钟公式、循环内跨任务限流、计次边界表；T2 验证 |
| `_execution` | §1 根据源码保留合法模型预算请求，校验剥离后才检查业务参数；T1 验证 |
| 元数据与历史不可变 | §4 深拷贝嵌套快照、仅未来输出补充；T3 验证 |
| 事件字段兼容性 | 已核对现有契约和投影，pages/browsing 已存在；T3 防止无意扩展 |
| 其它澄清 | §2/3 关闭提示与贡献时序；§4 重定向身份；§6 有界 stop 和迟到结果失效 |

本地真实 Runtime 工具循环与取消顺序已有验证；外部 Fetch 服务 SSRF 契约和
真实付费服务验证尚未通过。会话重建和跨任务组合测试已通过，详见 [验证记录](plan-validation.md)。

## 验收与回退

Linux/WSL 使用应用私有环境，按以下顺序执行，保留原始退出码：

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/web_search -q
make test-agent
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test
make typecheck-agent
make check-package-agent
git diff --check
```

Windows 使用应用已有 `.venv/windows/Scripts/python.exe` 执行等价 pytest/compile
命令；不使用根 PYTHONPATH 替代安装。打包与类型检查使用仓库支持的对应脚本环境。
若修改 Gateway/WebUI，再执行其原生相关测试及应用检查。测试前重新记录实现基线，
无关失败按测试身份对照，不扩大修复范围。

验收必须观察到：工具可被真实 Agent 调用、未调用零请求、关闭时不可绕过工具校验、
多次查询编号稳定、直接抓取有真实来源、进度不丢失、取消后无请求残留、历史可回放。
mock 验证不等于真实服务验收；真实模型与搜索调用须按 CONTRIBUTING 的执行和费用
授权规则单独安排，不读取或暴露生产凭据。

不涉及数据迁移。需要回退时，在单独授权的变更中恢复本次工具化相关代码和文档，
重启或重新加载会话以重建工具注册表；保留既有会话数据和公开进度历史，不删除
用户文件，不通过重置工作树或批量回退覆盖其它人的修改。
