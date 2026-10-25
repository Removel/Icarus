# Agent 工具结果与上下文质量修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development after user approval. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 复用现有工具预算/落盘/读取/Prompt 设施，减少模型拆 JSON、误读工具状态和空记忆噪声；不新增业务流程、数据库表或并行的工具发现体系。

**Architecture:** core tools 只处理通用预算与可见结果；MCP 插件处理协议形状和参数诊断；builtin-tools 处理本地失败和文本分段；Memory 插件不自动曝光内部空状态。业务成功解释和图片能力仍留在领域适配边界。

**Tech Stack:** 现有 Python、Pydantic、jsonschema、pytest；不增加依赖。

**Spec:** 本文是单应用修复的需求与实施依据；与 [跨应用语义会话约定](../../../../../spec/2026-10-25-argus-semantic-sessions.md) 独立可测。来源为 [飞书正文及 29 条批注](https://tcnyvm7vds2s.feishu.cn/docx/ZbuBdm7qvoWQMUxAqvbcCw9Ynih) 与 2026-10-25 用户确认。

**实施记录（2026-10-25）：** Tasks1–6本地实现、独立审阅及最终修正复审完成；MCP联合schema和失败视图宽映射/图片开销已修，预算绝对上限含配置层校验。当前代码见 [arch](arch.md)。最终完整Agent1288通过（含语义会话其他批次）、mypy276通过，安装包构建/安装导入和发现验证通过。真实模型/QQ验证未执行；以下保留原任务步骤，不把文档预期当测试证据。

## Global Constraints

- 用户已通过 /goal 授权预审、代码实现和本地隔离测试；不修改本机 settings，不自动 branch/commit/push，不真实模型/MCP/QQ/正式数据。
- 单工具可请求上限 25000、并发工具结果总上限 100000；应用默认输出预算保留 3000。框架默认 4000 不为本轮另改，模型 context_window 的既有 8%/15% 收紧保留。
- 参数 `_execution.max_output_tokens` 约束可见结果，不保证远端服务器只获取相同量的数据；不向任意 MCP schema 强塞 limit/filter。
- 不新增绕过预算的 read_result、QQ 专用一等工具、动态工具加载系统、通用字段投影框架或数据库表。
- 同步/异步行为一致；原始结果和已知完整性保留，未保存就不能提示“完整结果可读”。
- ReActAgent 不理解 QQ、MCP 业务码或 memory；插件动态内容不进入稳定 System Prompt。

## Review Focus

- 128k 模型下 25k/100k 仍被安全收紧，不能只改上限就宣称每次生效（Task 1）。
- metadata 当前不进入 ToolExecutionResult.as_dict；仅写 result_truncated 元数据不能当作模型已收到提醒（Task 2）。
- 单行超长文本、Unicode/转义和 EOF 不能通过行号续读，也不能增预算后仍无解（Task 2）。
- MCP structured/text 混合、重复 JSON、图片和 secret-bearing ValidationError 不可因“解包”而丢失或泄密（Task 3）。
- 非空失败 stdout 可能有诊断或部分执行证据；摘要不能把失败副作用伪装回滚，空记忆 gate 不能被展示关闭破坏（Tasks 4/5）。

## 当前核验结论

- 应用配置 default_output_tokens=3000，框架 max/batch=16000；_execution 的解析/渲染/组分配实际存在。原始结果在 renderer 前保存，不是保存最终省略视图。
- 单工具实际有效上限为 min(requested,max,context_window*0.08)；batch 为 min(configured,context_window*0.15)。1M 窗口可用 25k/100k，128k 窗口分别为 10240/19200。
- read 只有行 offset/limit，单行可出现 has_more=false 但模型可见正文再次截断。metadata 不进 as_dict，见 `icarus/core/tools/types.py`。
- MCP list schema default=5/实际=50，search schema default=50/实际=5。converter 同时返回 content.text JSON 和 structured_content。validation 把完整 jsonschema 错误降为 `$: failed ... validation`。
- bash 非零退出且 stderr 为空时 error=''，traceback 同时出现在 output.stderr/error。Blackboard 已有会话级 85% 阈值压缩，但当前长任务内失败输出仍累加。
- memory 自动召回为空不发 TaskContextInputEvent，却通过 auto_expose 的 Region 状态进入最终 Prompt。当前测试只检查内容事件，不足以证明最终 Prompt 无噪声。

以下路径均相对 apps/agent/；不创建占位 arch.md，实际实现后更新各原 feature 的架构。

## Task 1：预算上限与 schema 一致性

**Files:** `src/icarus/core/tools/settings.py`、`src/agent_app/resources/default_settings.json`、`settings.json.example`；测试 `test/icarus/core/tools/test_execution_policy.py`、`test/agent_app/config/test_config_loader.py`。

**Interfaces:** 复用 ToolExecutionSettings 现有 max_output_tokens/batch_output_tokens，不新增“会话总预算”等字段。control_schema.maximum 与运行校验共同来自 settings。

- [ ] RED：max_output_tokens=25000、batch_output_tokens=100000；应用 default=3000；schema 允许请求25000、拒绝25001；未知_execution字段仍拒绝。
- [ ] RED：context_window=128000 下 single=10240/batch=19200；1M 下可达25000/100000；多工具分配不超组预算，小结果不为了占满而填充。
- [ ] Run：`apps/agent/.venv/bin/python -m pytest -q apps/agent/test/icarus/core/tools/test_execution_policy.py apps/agent/test/agent_app/config/test_config_loader.py`。
- [ ] 修改现有上限/示例，保留小默认和 preview_ratio；不修改本机 settings.json。说明 source token 是保守计量，不与 provider 实际 usage 混称。
- [ ] GREEN；文档明确上游全量接收、工具采集上限、文件保存上限与模型展示预算是不同层，不新增全局业务过滤。

## Task 2：现有 read 的有界分段与可见截断说明

**Files:** `src/agent_app/plugins/builtin_tools/read_tool.py`、prompts.py；`src/icarus/core/tools/result_budget.py`；测试 `test/agent_app/plugins/builtin_tools/test_tools.py`、`test/icarus/core/tools/test_result_budget.py`。

**Interfaces:**

- 继续使用 `read(path, offset=1, limit=200)`；可选增加 `text_offset=0,max_chars=2000` 两个平坦参数，用于在选定行页中读取字符片段，max_chars 上限20000。
- 返回 next_text_offset/truncated 表达当前页正文分段；原 has_more/next_offset 仍只代表后续行页。先完成当前页的文本片段再推进到下一页，互不覆盖两种游标。
- 通用 renderer 仅在实际截断时在可见结果中加一个短、明确的截断/原始结果位置说明；用已有 omission marker 机制，不把全部内部 metadata 序列化给模型。

- [ ] RED：一条五万字符单行使用文本分段完整重组，含中文/emoji/引号/换行；EOF 空片段、越界、非法布尔 offset 拒绝；原行分页调用保持兼容。
- [ ] RED：以3000可见预算读取默认2000字符片段能前进；结果被并发预算进一步裁剪时，模型可见截断说明且可以调小max_chars重读，不静默宣称正文完整；不得因 renderer 裁剪伪造 next_text_offset 已消费。
- [ ] RED：原始 ToolResultStore 内容不包含后加的省略串；文件被16MiB上限截掉时不称完整。未保存结果不提供虚假路径。
- [ ] Run：`apps/agent/.venv/bin/python -m pytest -q apps/agent/test/agent_app/plugins/builtin_tools/test_tools.py apps/agent/test/icarus/core/tools/test_result_budget.py`。
- [ ] 实现单一 read 的片段行为与简短指导，指导调小范围/请求较大已有预算，不让 bash cat 成为默认续读；不取消预算、不另建read_result。
- [ ] GREEN，同步/异步默认调用与原有路径行为保持；不把 has_more=false 等同“从未发生展示截断”。

## Task 3：MCP 默认值、结果归一化和可自纠错误

**Files:** `src/agent_app/plugins/mcp/tools.py`、result_converter.py、client_manager.py、plugin.py；测试对应 test_tools.py/test_result_converter.py/test_client_manager.py/test_integration.py。

**Interfaces:**

- list 的 schema/runtime 同取 default_page_size，search 同取 DEFAULT_SEARCH_LIMIT=5；本轮不重新决定全局默认页大小。
- converter 继续返回 ToolExecutionResult。structured_content 存在时保留一个结构化主体；只有语义相等的 JSON 文本副本才去重，普通补充文本/多块/image保留。无structured_content时，完整JSON对象/数组文本仅解码一层，不递归猜data/messages/retcode。
- validation 保留最多8条有界诊断：字段path、缺失/未知键、期望类型/enum等；不回显完整instance/arguments或任意secret，不直接无界拼jsonschema.message。

- [ ] RED：schema默认=实际默认，包括自定义default_page_size；list不再宣称5实际50，search不再宣称50实际5。
- [ ] RED：仅text JSON、structured+重复JSON、structured+额外非JSON、混合image/text、无效JSON和普通文本；成功/错误内容均脱敏，图片仍通过已有ImagePart路径。
- [ ] RED：required/additionalProperties/type/enum精确字段诊断，合成secret不在异常/日志中；isError=False但业务retcode失败时不擅自宣称业务成功或在core硬编码。
- [ ] Run：`apps/agent/.venv/bin/python -m pytest -q apps/agent/test/agent_app/plugins/mcp/test_tools.py apps/agent/test/agent_app/plugins/mcp/test_result_converter.py apps/agent/test/agent_app/plugins/mcp/test_client_manager.py apps/agent/test/agent_app/plugins/mcp/test_integration.py`。
- [ ] 最小修复上述三处，外层success保留MCP协议含义；schema目录缓存沿用当前实现，不缓存有副作用执行，不另封QQ action工具。若服务端返回本身无分页，明确列为下游缺口。
- [ ] GREEN，调用同步/异步共用结果/验证；原始MCP数据可追溯，模型视图不重复付费。

## Task 4：bash 空错误与失败结果渐进披露

**Files:** `src/agent_app/plugins/builtin_tools/bash_tool.py`、`src/icarus/core/tools/tool_executor.py`、result_budget.py；测试 builtin_tools/test_tools.py、icarus/core/tools/test_tools.py/test_result_budget.py。

**Interfaces:** 失败非空error保留简短有用诊断，空stderr兜底 `exit_code=<n>, no stderr`。core按success=False做通用有界失败视图，不解析Python traceback或业务码；完整原始失败结果先保存，然后向模型提供短error、必要exit_code/部分stdout证据和可读路径。

- [ ] RED：sync/async `exit 1` 且无stderr有稳定error；超时/输出采集上限的既有错误不被覆盖。
- [ ] RED：长traceback不在error与stderr重复完整出现；失败摘要仍保留最重要的一行、退出码和完整诊断位置，原始文件可读；部分stdout不能被静默抹掉，失败不意味着副作用回滚。
- [ ] RED：结果存储失败时不能声称“完整诊断已保存”；采用原有有界预览并明确不可用，不能为缩短错误而不可恢复丢弃证据。
- [ ] Run：`apps/agent/.venv/bin/python -m pytest -q apps/agent/test/agent_app/plugins/builtin_tools/test_tools.py apps/agent/test/icarus/core/tools/test_tools.py apps/agent/test/icarus/core/tools/test_result_budget.py`。
- [ ] 复用现有save/render流程，为失败诊断设短预览目标但仍服从单项/组预算；不在ReActAgent添加失败业务处理，不新增错误分类框架或配置树。
- [ ] GREEN，重复读/短错误不强行落盘制造无用文件；本轮不实现通用上下文垃圾回收和读结果缓存。

## Task 5：空 Memory 状态不自动进入 Prompt

**Files:** `src/agent_app/plugins/memory/factory.py`、必要 plugin.py；测试 memory/test_factory.py/test_plugin.py/test_prompt_contribution.py、blackboard/test_regions.py、application/test_memory_context.py。

**Interfaces:** memory Region 的 required_for_start 保留，auto_expose=False；有内容仍通过现有TaskContextInputEvent注入，显式memory_recall和blackboard_read/list保留空/失败语义。不对所有插件硬编码“refs为空必省略”。

- [ ] RED：真实Blackboard compose后，空自动召回不包含“我记不起来了”、refs=[]或内部idle状态；gate仍及时完成，后端超时不阻塞Agent。
- [ ] RED：非空召回仍有原事实上下文，显式memory_recall空结果仍可见；Blackboard工具仍能检查当前Region；不把诊断状态删除当成功。
- [ ] Run：`apps/agent/.venv/bin/python -m pytest -q apps/agent/test/agent_app/plugins/memory apps/agent/test/agent_app/plugins/blackboard/test_regions.py apps/agent/test/agent_app/application/test_memory_context.py`。
- [ ] 使用已有auto_expose开关完成最小修复，不新造未检索/空/超时字段，不改整个记忆人格说明。Argus自动召回query的语义改进归语义会话计划，不能用本任务掩盖其固定调度句问题。
- [ ] GREEN；Prompt/Memory现有测试如只断言没有TaskContextInputEvent，不视为最终Prompt已验证，必须覆盖完整compose。

## Task 6：回归、文档与边界

- [ ] 依次跑受影响小文件、`make test-agent`、`make typecheck-agent`、`make check-package-agent`，再 compile/diff。
- [ ] 更新工具预算/结果行为的实现后架构，不改旧飞书实测数字；把原始量、可见量、provider usage区别写清。
- [ ] 如需新实测，只先提供离线fixture结果；真实模型/QQ需要另获授权，不声称节省到某个未测token目标。
- [ ] 延后项单列：动态工具延迟加载、下游QQ action分页/描述、真正视觉能力、全局事件因果重构。task.cancel_result告警仅核对声明/消费者，未有真实消费者需求不加dummy插件或顺手重做EventBus。

## 验收与回滚

任务独立可测，不依赖新的Gateway协议。回滚配置不删除已保存结果；失败摘要/分段结果仅影响后续调用，不重写旧对话。保留原有接口与 sync/async 一致性，新增字段明确校验。

实施按任务逐项记录实际 RED/GREEN 和回归结果，不把文档预期当测试通过证据。没有新增表、绕预算通路、领域化 core、全局 JSON 投影或新的工具发现系统。
