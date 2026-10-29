# Tool Call Quality Design｜工具调用质量设计

| 项目 | 内容 |
|---|---|
| 日期 | 2026-10-29 |
| 所属应用 | `apps/agent` |
| 状态 | 六组目标及审查发现均已修复；提交前重验Agent1274项及typecheck/package/compile/diff通过 |
| 实施计划 | [plan.md](plan.md)，记录Native隔离执行和实际验证；feat/agent已rebase同步origin/feature |
| 当前架构 | [arch.md](arch.md)，描述当前源码/测试行为；本文保留设计意图 |

> 本文从既有迁移计划中提取目标设计，并对照当前实现重新审查。不能把文档编写顺序追溯成“先设计后计划”：已有 plan 早于本文；后续遵循设计审阅 → 校准计划 → 实现/测试 → 更新 arch。
> 除 Markdown 相对链接外，代码路径均相对于仓库根目录。本文不授权分支切换、合并、提交、推送或付费真实模型调用。

## 1. 意图与成功标准

将 `feat/argus` 中与业务无关的工具调用质量改进选择性适配到 Agent，而不是迁入 Argus 功能。模型应能看懂工具的真实失败原因、可靠续读长内容。对 core 的 `tool_search → tool_execute` 延迟入口，仅当当前 Run 的最终 Tool Result 消息保留完整定义时才新增授权。此项不扩展到 MCP 自有发现/执行协议，也不宣称验证了 LLM before Hook 后的最终厂商请求或模型实际接收。

提高结果预算是已记录需求，但不是正确性的替代手段。用户的小预算、小模型窗口和工具组二次分配仍须正确工作。结果裁剪必须可辨识，可恢复的信息不能伪造；完整证据无法保存时不能声称已完整保存。

成功标准：

1. 六组范围均有聚焦功能测试：预算、MCP 转换/诊断、Read/Bash/截断标记、失败证据/预览、分发身份、动态发现一致性。
2. `invoke / ainvoke / stream / astream` 对应行为一致；模型工具定义单 Run 冻结、加载名单跨 Run 清空。
3. 不引入 Argus 业务、不改变公共客户端事件协议、不覆盖用户配置、不增加依赖。
4. 每个阶段先运行最小受影响测试，再执行 Agent 全量、编译和 diff 检查；验证结果按实际命令记录，不能沿用设计前的通过数作为实现验收。

## 2. 现状依据与迁移前审查

### 2.1 核查基线

本地 `feature=a736cda`、`feat/argus=3186c01`，共同祖先 `885a431`。未 fetch，不能视为远端实时状态。当前工作区已有 `icarus.toml` 修改、`.claude/` 和本 feature 的未跟踪文档；不得覆盖或混入本次实现。

本轮独立执行 core 聚焦测试：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/icarus/core/tools/test_tool_search.py \
  apps/agent/test/icarus/core/tools/test_tool_execute.py \
  apps/agent/test/icarus/core/tools/test_result_budget.py \
  apps/agent/test/icarus/core/tools/test_execution_policy.py -q
```

结果：35 passed。另运行 `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/hooks/wrappers/test_value_rewrite.py apps/agent/test/agent_app/plugins/runtime_update -q`，18 passed；`apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/mcp/test_result_converter.py apps/agent/test/agent_app/plugins/mcp/test_tools.py apps/agent/test/agent_app/plugins/mcp/test_plugin.py -q`，20 passed。以上仅确认实施前基线，不证明新增行为已实现。既有 plan 中的 133 项是其先前调查记录，本轮没有把它重新验算或计入验收。

本轮另运行 `make test-agent`：1201 passed in 86.85s，退出码 0，包含仓库脚本的 compile 检查。`git diff --check`、新文档空白与相对链接检查通过。未运行 typecheck/package 或真实模型 smoke。所有结果均为尚未修改产品代码的迁移前基线。

### 2.2 当前源码事实

| 位置 | 已确认行为 | 设计影响 |
|---|---|---|
| `src/icarus/core/tools/tool_search.py` | 返回拼接字符串，并直接将命中名称放入 `tools_loaded` | 不能证明裁剪后完整 Schema 仍可见 |
| `src/icarus/core/tools/tool_executor.py:finalize_group` | 先按整组分配预算，再保存超预算原结果和渲染预览 | 完整性检查必须位于最终渲染后，而非搜索工具刚返回时 |
| `src/icarus/core/agents/react_agent.py:_collect_loaded_tools` | 消费所有结果的 `tools_loaded` 并与可执行全集求交 | 求交不是来源校验；非搜索工具不得借此新增加载权限 |
| `src/icarus/core/tools/tool_execute.py` | 门控、Schema 校验后使用目标名称和原 ID 进入完整执行器 | 不再另造一条目标执行链；保留消息层外部身份 |
| `src/icarus/core/hooks/wrappers/observable_tool_executor.py` | 目标单次解包；Hook 不得改 identity、success 或 metadata，可在声明点改参数/输出 | 分发参数不等于 Hook 改写后的最终参数；最终可见定义还须在 Hook 后验证 |
| `src/icarus/core/tools/result_budget.py` | 通用 head/tail，未针对恢复信封保留字段 | 通用裁剪会丢游标；仅放大预算无法修复 |
| `src/icarus/core/tools/result_store.py` | 有大小上限、源捕获完整性和 `StoredToolResult.complete` | 文件存在不代表证据完整 |
| `src/icarus/core/tools/settings.py` | 类型默认 4000，max/batch 默认 16000 | 需同时核对其他实际配置入口 |
| `src/agent_app/config/config_loader.py` | 仓库 settings 与打包资源是择一读取，不是自动合并 | 只改类型默认不会改变仓库运行默认 |

表中 `src/` 均以 `apps/agent/` 为前缀。插件细节以迁移前源码/测试审查为准，不以源分支文档代替目标分支事实。

### 2.3 源补丁筛选

| 来源 | 允许提取的意图 | 不能直接照搬的部分 |
|---|---|---|
| `96c65e0` | 结果携带分发目标，完成事件提供显式访问属性 | Argus 消费者；将分发记录解释为工具实际副作用或 Hook 后实参的证据 |
| `8569e79` | MCP 结果质量、参数诊断、Read 字符分片、Bash 错误、失败证据/短预览、裁剪标记、预算上限 | Argus/handoff/语义会话/报告/Memory 自动暴露；JSON 去重导致实际错误原因消失的退化 |
| `1fc7e1c` | 默认输出预算 25000 的意图 | 整份 settings、用户环境值、目标分支没有的 example 配置 |
| 目标分支新增适配 | 最终可见定义与加载权限一致、分页恢复字段保留 | 不宣称这些已被 Argus 源补丁解决 |

不迁入执行退出 tracker、SessionContext、后台会话接线、MCP server instructions 或业务提示词。迁移采用小范围重做，不整颗 cherry-pick 包含大业务功能的提交。

## 3. 所有权、约束与明确不做

### 3.1 层次归属

- `icarus.core.tools`：预算策略、结果落盘/渲染、固定搜索/分发桥及其完整性规则。
- `icarus.core.agents`：完成事件目标访问属性；Run 内加载名单仍只属于 `_RunState`。
- `agent_app.plugins.mcp`：MCP 协议内容转换、脱敏、远端调用前参数校验。
- `agent_app.plugins.builtin_tools`：Read 字符分页、Bash 退出诊断与相应工具提示。
- `agent_app` 配置装配：统一受控的预算默认；既有配置来源优先级不变。

core 不导入 Plugin Runtime、Blackboard 或业务插件；Runtime 不解释具体业务 Event。ReActAgent 不依赖 MCP 等插件。厂商差异只留在 `model_provider`。不增加 Plugin、子 Plugin、Hook 路由、执行器框架或通用控制元数据注册机制。

### 3.2 保持的不变量

- 默认常驻 9 工具不变，MCP 动态 Catalog 与统一工具面 Catalog 分离。
- ReActAgent 产出的模型工具定义每 Run 只生成一次，循环不重建、不增删、不重排；不把此项夸大为所有下游 Hook 后请求的字节冻结。
- `loaded_tools` 保序去重、只增不减、跨 Run 重置，不存在共享工具实例中。
- 原 ToolCall ID、模型消息、历史存储和 started/completed 的公共配对不变。
- 工具失败、预算拒绝或超时均不等于副作用回滚；本期不提供执行退出的权威追踪。
- 使用现有 `ToolExecutionResult`、`ToolCall` 和存储类型，不增加相似 DTO 或依赖。

### 3.3 非目标与反目标

不改 Gateway/TUI/Argus、跨应用协议、数据库、manifest、模型 provider；不做远端自动重放、连接池改造、模糊工具搜索、跨 Run 授权缓存或读文件自动授权。

以下即使“跑通”也属于失败：提高预算掩盖门控错误；保存不完整结果却强制短预览；输出半截游标；把失败改成成功；在稳定 System Prompt 注入动态信息；公共 payload 泄漏内部目标参数；为迁移便利越层或整份覆盖配置。

## 4. 预算设计

| 设置 | 目标值/边界 |
|---|---|
| `default_output_tokens` | 默认 25000 |
| `max_output_tokens` | 默认及配置绝对上限 25000 |
| `batch_output_tokens` | 默认及配置绝对上限 100000 |
| `min_output_tokens / max_calls_per_batch` | 保持 512 / 8 |
| `preview_target_ratio / head_ratio` | 保持 0.9 / 0.5 |
| timeout、结果文件大小 | 保持默认 120s / 上限 600s / 16 MiB |

沿用现有 policy：有效单次不超过 `context_window × 0.08`，整组不超过 `context_window × 0.15`；没有窗口时使用配置。默认在 128000 窗口下为 10240/19200，在 20000 窗口下为 1600/3000。无窗口时默认预览目标为 22500，但该数不是所有模型的实际预览保证。

显式较低值不提高，25001 的单次申请非法，配置 max/batch 超过绝对上限非法。保留严格数值类型、有限超时和最小组信封校验。若旧配置仅写低 max 而省略 default，与新默认不兼容时明确报配置错误，不静默修正用户意图。

同时更新类型、打包 `default_settings.json` 和受版本管理的 `apps/agent/settings.json` 的目标预算键。修改前核对该文件 diff；有用户冲突就保留并记录此入口仍采用用户值，不整体替换、不新增虚构的配置入口。独立测试仓库和打包资源两条读取路径。

## 5. MCP 内容与诊断

### 5.1 转换规则

仅尝试解析完整文本中的 JSON object/list。普通文本、无效 JSON、标量和 JSON 内部的字符串不改变业务表示/类型；标量也不参与本次去重。保留现有 Redactor 对嵌套字符串的安全处理，不以“不递归解析”为由停用脱敏。

- 无 structured content 时，JSON object/list 文本转为 `{type: "text", data: <JSON>}`。
- 有 structured content 时，只去掉与原始 structured content JSON 语义相同的文本副本；比较先于脱敏，避免不同敏感值被替换后误认为相同。
- JSON 比较区分布尔与数字；object 键序不影响等价，数组顺序有意义。不同内容、补充文本、图片与资源均保留。
- `is_error=True` 的原始文本无论后来转 data 还是去重，都参与现有脱敏诊断生成；不能只留下通用 MCP 失败。避免扩大到新的错误语义推断器。

success 仍只依据 MCP 的 is_error，不读取 JSON 中的 code/retcode/success/receipt 来猜业务成败，不将 text JSON 提升为 structured_content。只有结构化错误而无文本时，保留结构化证据并允许通用 error；不硬编码服务端字段来推导“实际原因”。源测试中要求 JSON 错误原因不出现在 error 的反向断言不可照搬。

### 5.2 参数校验

继续在远端调用前校验，失败时不调用远端、不自动重放。诊断包含字段路径、缺失/未知字段、期望类型、anyOf/oneOf 分支，但不包含实际参数或 enum 值。最多 8 条，递归最多 4 层，每条最多 240 字符，tool_ref 最多 128 字符；异常链不得绕过脱敏。上述是诊断输出展开上限，不声称整个 Schema 验证 CPU/内存有界。

最终面向模型的错误出口也须脱敏/有界，不能只处理 validator 后又由 `mcp/plugin.py` 的 operation 拼回原始 tool_ref；将该包装层及 sync/async 回归测试纳入 MCP 单元，不改实际执行标识。路径仅展示经安全处理、限长的 Schema 已声明字段；对 additionalProperties 等未知动态键使用占位而非回显用户提供的键名，避免把“不回显值”误称为“不泄漏任何参数数据”。不新建全项目脱敏框架。

沿用 MCP 本地校验器，不为与 core 分发校验器形式相似就跨层合并。保持参数原样透传、禁止外部 Schema 引用、取消透传与既有失败后重连语义。list Schema 的 page_size 默认与实例 default_page_size 一致；search 的通常默认仍为 5，对于合法 max_page_size<5 的实例，统一 Schema 与实际缺省为 `min(DEFAULT_SEARCH_LIMIT, max_page_size)`，不改变显式 limit 校验。该小上限适配是本设计为保证默认自洽作出的选择，不是用户额外要求。sync/async 契约相同。

## 6. Read、Bash 与恢复信息

### 6.1 两层 Read 进度

保留 `offset/limit` 行分页，在选定行页的正文上增加：

- `text_offset`：默认 0，非负整数，拒绝 bool。
- `max_chars`：默认 2000，范围 1–20000，拒绝 bool。

按 Python Unicode 字符切片，不按 UTF-8 字节。输出保留 path/content/offset/limit/has_more/next_offset，增加 text_offset/max_chars/truncated/next_text_offset。

`has_more/next_offset` 表示后续行页；`truncated/next_text_offset` 表示当前行页尚有字符片段。先用相同 offset/limit 加 next_text_offset 读完当前页，之后才按 next_offset 翻行页。text_offset 等于页长返回空 content、truncated=false、next_text_offset=null，超过页长返回可读参数错误。

仍先读取行页再按字符切片，不声称解决超长单行的内存占用。不改 UTF-8、现有换行/文件错误语义；Unicode 字符不是用户感知字素簇。旧调用省略新参数也会从整行页正文变为最多 2000 字符，`has_more=false` 仍可能 `truncated=true`，须同步说明。续读仅针对同一 path/offset/limit 的当前文件行页；期间文件被编辑则不承诺快照一致性，调用者应重新读取，不新增锁、版本号或常驻读取状态。

### 6.2 结果预算不是第三套游标

通用预算裁剪用 `metadata.result_truncated` 和模型可见省略标记表示；mapping/list 标记含 `truncated: true`，字符串标记含 `truncated`。避免与业务字段冲突，保留现有省略 key 自动避让。

通用 mapping 预览只在当前映射层识别有限的恢复字段，不按插件名分支、不递归搜寻业务协议。预留完整标量字段：`offset/limit/has_more/next_offset/page/page_size/total/next_page/text_offset/max_chars/truncated/next_text_offset/next_cursor`，再用剩余预算预览正文。不将一般 mapping 升级成分页对象，也不改写字段值。

游标必须完整保留或整体省略，不能 head/tail 裁成可误用的值。超大恢复信封放不下时，退到明确标记裁剪/恢复信息不可用的最小结果，有完整存储就指向证据文件；连最小信封也放不下则使用既有预算错误，不突破预算。通用渲染器不能根据分页字段推断调用只读或可安全重放，不指示重新执行原调用；失败或裁剪不代表副作用回滚。仅具体只读工具说明（例如 read）可指导使用原参数重读，其他工具应先检查已有证据/实际状态，不能盲目重放。

可见原游标只证明工具生成了这些进度，不证明正文已展示。工具说明和静态基础提示应要求：发现预算裁剪，使用原 offset/text_offset 减小 max_chars 重读或合理申请预算，不能直接按 next_text_offset 跳过隐藏正文。该提示不新增动态 System Prompt 内容。

### 6.3 Bash

普通非零退出且 stderr 为空或全空白时，error 至少提供 `exit_code=<code>, no stderr`；非空 stderr 仍作为诊断。保留 stdout/stderr/exit_code 与失败事实，不增加命令重试或副作用推断。超时、取消、spawn 失败、输出采集超限保留其既有优先诊断与 metadata，不被该 fallback 覆盖；不改变已有后台进程组清理或新增后台会话能力。

## 7. 失败证据与有界预览

在最终组分配之后、渲染之前，满足以下任一条件就沿用现有 ToolResultStore 尝试保存完整原结果：超出实际分配预算；失败且序列化大于 1000 token。小失败不增加无意义落盘。

保存的是进入预算渲染前的结果，仍经过已有工具转换/脱敏和声明的结果 Hook，不是远端未脱敏数据，也不是被预览过的结果。文件不写入内部控制 metadata。

只有 `stored.complete=True` 才将失败预览主动收缩至 `min(1000, 实际分配预算)`。优先保留最终诊断、exit_code、有效 stdout；仅去重与 error 完全相同的 stderr。使用共享省略/文件引用，不为每个字段重复长路径。非 shell 输出同样必须保住真实失败原因，不能因 shell 字段排序被挤掉。

图片和结果信封保持原样；装不进 1000 时回到实际分配预算，再放不下则明确报现有预算错误。不改 success，不吞图片，不伪造执行结果。

无 store、缺 task_id、写入失败、16 MiB 截断或原捕获不完整时，不强制 1000，回到实际分配预算的一般渲染，明确完整证据不可用或文件不完整。保留 original_tokens/visible_tokens/result_truncated/result_file/result_file_complete/original_bytes/saved_bytes/result_file_error/context_budget_exhausted 及既有控制 metadata。不能仅凭路径存在判断完整性。

## 8. 分发身份的语义

`tool_execute` 通过门控及参数校验、目标执行器返回结果后，合并 `metadata.dispatched_tool={id,name,arguments}`。参数深拷贝并去掉顶层 `_execution`，其余原 metadata、success/output/error/images 保留。前置拒绝或执行器抛异常未返回结果时不伪造目标完成记录。

该字段记录**进入目标执行器的分发请求**，不承诺业务 Tool 已被调用、成功或产生副作用；目标执行器还可能因预算等规则返回失败。arguments 为分发参数，不冒充 before Hook 改写后的实际参数。本期不引入 tracker 来补足这类证据。

`AgentToolCompletedEvent.target_tool_call` 提供显式、安全的内部访问：仅在外层 tool_call.name 为 `tool_execute`、metadata 为 mapping、目标 name/ID 为非空字符串、ID 等于外层 ID 且 arguments 为 dict 时解包；缺失或非法时回退原 tool_call。返回的分发参数须与 metadata 深拷贝隔离，不允许消费者修改事件原数据。实现采用何种内部 helper 由构建者决定，但需验证正常分发、目标失败、畸形 metadata 和深拷贝隔离。

原 tool_call、消息历史、调用 ID、公共 started/completed 均保留外层 `tool_execute` 身份。新增 metadata 不经 as_dict 进入模型消息，也不进入 runtime-update 公共投影。目标参数可能敏感，不能因为“内部 metadata”就新增日志或公共序列化。Hook 保持只观察一次目标，不观察重复的桥入口。

## 9. 最终可见定义与加载门控

### 9.1 为什么必须适配

当前搜索结果文本被裁剪后，metadata 的候选加载名仍完整；ReActAgent 会允许调用模型实际没有拿到完整说明/Schema 的目标。提高预算、仅检查搜索原始结果或仅与 Registry 求交都不足以修复。

本轮只读进程内复现：注册带长 description 的临时 StubTool，通过真实 `ToolExecutor.execute_many` 搜索并申请 512 token，得到 `result_truncated=True`、`visible_tokens=412`、完整 description 不再可见，但 `tools_loaded=['design_probe']` 仍保留。未写入源码/测试文件、未调用模型或远端服务；这是当前缺陷证据，不是修复验证。

### 9.2 数据流

```text
固定 tool_search Schema（Run 创建前）
  → 精确 scope 查询，可选精确 name
  → 结构化完整定义 + 候选 tools_loaded
  → 既有结果 Hook
  → ToolExecutor 最终组预算分配、落盘、渲染
  → 对最终可见条目逐项完整性检查，收紧 tools_loaded
  → ReActAgent 与可执行全集求交、保序加入本 Run 名单
```

命中 output 改为 JSON-compatible mapping，核心字段为 `scope`、`tools: [{name,description,input_schema}]`，附简短分发/恢复说明。使用 Catalog 原始完整定义，不注入 `_execution`。未知 scope/空 query 沿用可读目录、不授权的语义。

最终收紧由注册的 `ToolSearchTool` 实例提供无状态方法（建议 `finalize_loaded_tools(result) -> ToolExecutionResult`），由 ToolExecutor 在最后一次渲染后调用。它不是 Hook，不读取/修改某个 Run 的名单，只依据原候选名称、绑定的不可变 Catalog 和最终 result 过滤 metadata，不改输出、success、图片或预算统计。

判定要求：成功结果中最终可见的工具条目，其 name、description 和完整 input_schema 与对应候选 Catalog 定义完全一致；JSON 类型也须一致，不能把 bool 与 number 的 Python 宽松相等当作完整相等。残缺条目不加载，完整条目可部分加载，零个完整定义则无新增权限。无需整个 scope 都完整才准许部分条目。

**来源约束**：只允许实际注册的 ToolSearchTool 返回候选授权，不以模型传来的工具名称字符串、scope 文本或任意业务结果作为授权来源。其他工具结果中的 `tools_loaded` 在最终消费前必须移除/忽略；特别覆盖普通 Tool 和经 tool_execute 分发的业务 Tool 伪造 metadata 的情况。普通结果的其它 metadata 不受影响。不引入通用授权插件/控制信号框架。

该检查在四条 ReAct 路径共用的 finalize 契约内完成，结果 Hook 不能绕过最终检查。已经在本 Run 完整加载的工具不因后续搜索被裁剪而撤销；本次检查只约束新增权限。

**保证终点**：完整性以预算渲染后的 Tool Result/Agent 历史为准，不延伸到 `ObservableLLM` 的后续 before Hook。现有 LLM Hook 可改写请求消息，本期不收紧其权限、不增加真实请求级授权确认或网络送达证明；受信任 Hook 若主动移除发现内容，不在本期保证内。既有架构中“冻结”的准确含义是 Agent 循环不变更工具数组，不能声称下游所有改写点均不可变。

**MCP 边界**：常驻 `mcp_tool_execute` 继续按自有 Catalog 查找、验证并执行，不增加每 Run 发现授权或裁剪确认。MCP 转换/诊断的改进不等于 MCP 获得 core 延迟工具的完整性门控；如需相同强保证，应单独设计，不借本期迁移越界。

### 9.3 精确恢复

固定搜索 Schema 增加可选 `name: str`，只在精确 scope 内筛选单个工具；省略时仍整组召回，不做评分、模糊匹配、分页或新工具。该 Schema 在 Run 创建前已固定，绝不运行中改定义。

单个极大定义仍超预算时，不授权、不声称已加载；完整结果若已保存，可用常驻 read 辅助检查，并调整预算重新搜索。读文件本身不授权；完整文件不可用就明确说明。硬预算无法容纳完整定义时允许明确无法加载，不以静默放行换取表面成功。

## 10. 迁移审查结论与实现准备

本设计选择的最小路线是沿用执行链、结果存储和双桥，选择性适配六组行为；不迁入分支整体业务。相比直接搬源补丁，新增的必要适配是：MCP 去重前保住错误诊断、最终可见定义校验、授权来源约束、恢复信封保留。这些解决当前架构的具体正确性问题，而不是可选的框架扩展。

不选择：

- 直接 cherry-pick 大提交：包含明确排除的 Argus 业务和配置。
- 只放大预算：用户小预算/窗口和组分配下问题仍在。
- Run 内动态添加模型定义：破坏现有冻结契约并引入 provider 复杂度。
- 新建通用元数据策略/分页协议/执行 tracker：没有本期必要调用者，扩大实现与验证范围。

实施前先批准本设计，再使用 writing-plans 校准既有 plan：需求与决策以本文为入口，plan 保留任务顺序、文件、TDD 步骤、命令、验收和执行记录，不继续维护第二份独立目标设计。尤其补充非搜索结果伪造授权测试、分发语义测试与 JSON 类型严格完整性测试。

当前处于 `feature`，本地 `feat/agent` 落后。文档准备不需切分支；开始应用源码工作前重新核对 intended remote 的 feature 基线，并依用户明确授权处理应用分支。没有授权不自动切换、创建、同步、合并、提交或推送，也不把工作区修改 stash/reset 掉。

## 11. 验证与交付

聚焦测试覆盖正常路径、显式 512/旧默认 3000/新默认 25000、128000/20000 窗口、整组二次裁剪、无完整落盘、sync/async 与四条 Agent 路径。保留已有 tools=None、空白名单、显式名单、单次目标 Hook、跨 Run 门控、脱敏和图片限制回归。

最终按仓库顺序执行 Agent 全量，再执行 typecheck/package/compile/diff；命令见 plan。公共投影不变时不强制扩大到其它 app；若实现影响公共行为，先停止并重新确认范围，不能悄悄修改 Gateway/TUI。

实现且验证后补本 feature `arch.md`，只描述实际源码/测试确认的结构、数据流、语义及已知限制；同步修订受影响的工具延迟加载、执行保护、MCP 等既有 arch，明确新的设计状态和验证结果。不提前写“已实现”的架构，不创建占位 arch。

真实模型 smoke 仅在凭据可用且另有执行/成本授权时使用隔离数据；未跑明确记录。回滚保持原调用身份、历史消息和文件，不清数据库、不覆盖用户随后配置、不使用 reset --hard；任何 revert 仍需授权。

## 12. 设计审阅记录

本轮已完成独立源补丁边界审查和独立构建者检查。构建者指出以下三处会实质改变实施范围的歧义，已按本期最小改动边界澄清：

| 审阅问题 | 本文采用的处理 | 对计划的要求 |
|---|---|---|
| 完整定义授权是否覆盖 MCP | 仅覆盖 core 双桥；MCP 自有协议维持原样 | 不新增 MCP Run 内凭据/授权架构，不将本期成果描述为全体远端工具强门控 |
| 完整性是否保证到 LLM Hook 后请求 | 保证终点为最终 Tool Result/Agent 历史，不收紧 LLM before Hook 权限 | 测试标准 executor/finalize 链；明确受信任下游改写的限制，不夸大网络送达/模型实际接收保证 |
| 通用恢复提示是否能要求重放 | 通用渲染器不得建议重放；只读 read 自己说明原游标重读 | 超大游标 fallback 验证无盲目重放提示，失败/裁剪不等于回滚 |

源补丁审查另外促成本设计补充：MCP 最终错误包装层的脱敏、未知动态键隐藏、JSON 转换不推断业务成功、保留嵌套字符串脱敏、Read 默认返回量改变及文件修改后的游标限制、Bash 特殊错误优先级。

这些澄清、search 小上限时 `min(5,max_page_size)` 和非搜索结果授权来源约束，均为依据现有架构选择的设计决定，随整份设计经用户“开始吧”确认；不冒称用户逐项单独提出。plan 已校准并执行六组实现，覆盖 search 小上限、通用提示禁止盲目重放、MCP 最终错误出口和分发请求身份语义。当前实现与最新验证见 arch/plan；独立整体审查两项Important及原延后Minor均已修复。
