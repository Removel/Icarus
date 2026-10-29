# Tool Call Quality Implementation Plan｜工具调用质量迁移计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 选择性适配 Argus 分支中的六组工具调用质量改进，保持现有分层、公共事件身份及用户配置边界。

**Architecture:** 沿用 ToolRegistry、ToolExecutor、ToolResultStore 和 `tool_search → tool_execute` 双桥。MCP 转换/诊断与 builtin Read/Bash 改动留在各自插件；core 只负责通用结果预算、分发记录和最终搜索定义完整性。完整性保证止于 Agent 最终 Tool Result，不扩展到 MCP Run 内授权或下游 LLM Hook。

**Tech Stack:** Python、pytest functional tests/native assert、现有 dataclass/Pydantic/jsonschema；不新增依赖。

**Spec:** [design.md](design.md) 是目标设计的唯一入口；本文只记录实施任务、接口、测试与执行证据。既有基线参考[工具面延迟加载](../2026-10-21-tool-surface-lazy-loading/arch.md)、[工具执行保护](../2026-09-20-tool-execution-guard/arch.md)、[MCP](../2026-09-05-mcp-client-plugin/arch.md)。

> 状态：Native隔离执行，feat/agent已rebase同步origin/feature=a736cda；六组实现及全部审查发现已修复。提交前重验Agent1274 passed（74.29s）、typecheck/package/compile/diff通过；用户已授权逻辑提交、推送和PR，等待MR确认后才处理TUI。真实模型smoke未执行。
> 本目录保持 `2026-10-29-tool-call-quality`；路径除 Markdown 相对链接外均相对于仓库根目录。

## Global Constraints

- 仅修改 `apps/agent/` 所有的实现、测试和文档；不改 Gateway/TUI/Argus、数据库、manifest 或 model_provider，不新增依赖。
- ReActAgent 保持无状态，Run 名单只放 `_RunState`；默认常驻 9 工具不变，加载名单保序去重、只增不减、跨 Run 清空。
- Agent 循环复用单次生成的工具定义；不收紧 LLM before Hook 权限，不宣称覆盖其后请求或模型送达。
- MCP 与 core Catalog 分离，不增加 MCP 每 Run 发现授权、连接池、自动重放或业务收据/成功推断。
- Hook 不改变调用身份、执行事实或 metadata；不新增 Hook 路由。动态 Plugin 上下文不写入稳定 System Prompt。
- `invoke / ainvoke / stream / astream` 对应行为一致；沿用公共类型，不增加通用控制信号框架、执行 tracker 或子 Plugin。
- `default_output_tokens=25000`、`max_output_tokens=25000`、`batch_output_tokens=100000`；max/batch 同时是配置绝对上限。
- 保持单次 `context_window × 0.08`、整组 `context_window × 0.15`，min=512、max_calls=8、preview_ratio=0.9、head_ratio=0.5、timeout 默认120s/上限600s、文件16 MiB。
- 保留较低显式预算；配置不兼容明确报错，不静默重写。只精确编辑无用户冲突的默认预算键。
- 不修改已有 `icarus.toml`、`.claude/` 工作区内容，不输出凭据，不覆盖或 stash 用户改动。
- 仅选择性重做 `96c65e0 / 8569e79 / 1fc7e1c` 的获准行为，不整颗 cherry-pick，不迁 Argus/handoff/Memory 自动暴露策略。
- 工具失败、超时或裁剪不等于副作用回滚；通用渲染器不指导原调用重放，只有明确只读工具可说明安全重读。
- 原 ToolCall ID、历史消息、公共 started/completed 外层身份不变；内部目标参数不新增日志/公共序列化。
- 未明确授权不创建/切换/同步分支、提交、合并、推送或 revert；建议提交仅是逻辑拆分，不是执行指令。

## Review Focus

- 组预算二次裁剪、结果 Hook 改写或业务 Tool 伪造 `tools_loaded`：只对可信搜索最终完整条目新增授权，见 Task 6。
- MCP JSON 去重后诊断丢失、外层 operation 拼回敏感 tool_ref、未知动态键泄漏：实际错误有界且安全，见 Task 2/4。
- Read 二次裁剪、超大 next_cursor 或已产生副作用的分页结果：不跳过未展示正文、不输出半截游标、不建议盲目重放，见 Task 3/6。
- 512预算/小窗口与无完整落盘叠加，或图片信封大于1000：保持实际硬预算和真实保存状态，见 Task 1/4。
- 分发 metadata 畸形、ID不配对、参数被 Hook 改写：安全回退，分发请求不冒充实际执行参数/副作用证据，见 Task 5。

---

## 1. 文件责任与实施次序

下表路径以 `apps/agent/` 为前缀；测试目录镜像源码，不拆执行器、不新建通用插件或平行 DTO。

| 单元 | 修改实现 | 修改/补充测试 |
|---|---|---|
| Task 1 预算 | `src/icarus/core/tools/settings.py`、`src/agent_app/resources/default_settings.json`、`settings.json` 中安全可改预算键 | `test/icarus/core/tools/test_execution_policy.py`、`test/agent_app/config/test_config_loader.py`、必要时 `test/test_packaging.py` |
| Task 2 MCP | `src/agent_app/plugins/mcp/{result_converter,client_manager,tools,plugin}.py` | `test/agent_app/plugins/mcp/test_{result_converter,client_manager,tools,plugin,integration}.py` |
| Task 3 Read/Bash/标记 | `src/agent_app/plugins/builtin_tools/{read_tool,bash_tool,prompts}.py`、`src/icarus/core/tools/result_budget.py` | `test/agent_app/plugins/builtin_tools/{test_tools,test_prompt_contribution}.py`、`test/icarus/core/tools/test_result_budget.py` |
| Task 4 失败证据 | `src/icarus/core/tools/{tool_executor,result_budget}.py` | `test/icarus/core/tools/{test_tools,test_result_budget}.py`、MCP `test_integration.py` |
| Task 5 分发身份 | `src/icarus/core/tools/tool_execute.py`、`src/icarus/core/agents/types.py` | `test/icarus/core/tools/test_tool_execute.py`、`test/icarus/core/agents/test_react_agent_stream.py`、`test/agent_app/plugins/runtime_update/test_plugin.py` |
| Task 6 最终发现/恢复 | `src/icarus/core/tools/{tool_search,tool_executor,result_budget}.py`、`src/agent_app/plugins/prompt/base_prompt.py` | `test/icarus/core/tools/{test_tool_search,test_tool_execute,test_tools,test_result_budget}.py`、`test/icarus/core/agents/{test_react_agent,test_react_agent_stream}.py`、`test/agent_app/application/test_session_runtime.py`、builtin `test_tools.py` |
| Task 7 验收/arch | 本 feature `arch.md`，既有工具延迟加载/执行保护/MCP `arch.md`，本目录 design/plan 状态 | 不新增产品行为；文档根据最终源码和测试更新 |

Task 1→6 顺序执行，共享文件按任务逐步修改；Task 4使用Task 2诊断和Task 3标记，Task 6使用前三项的结果链。逻辑提交保持实现与测试一起，文档单列；未经授权均不提交。

## 2. 实施准备与现有证据

设计阶段只读核查：本地 `feature=a736cda`、`feat/argus=3186c01`、共同祖先 `885a431`，当时 origin/feature 与 feature 相同，未 fetch；本地 feat/agent 落后 feature 111 个提交。实施前重新核对，不能视作远端实时状态。

- [x] 核对源补丁与目标源码的架构/业务边界，完成独立构建者检查；见 design 第2、12节。
- [x] 设计阶段 `make test-agent`：1201 passed，exit 0；额外 core/hook/public/MCP 聚焦基线见 design 第2节，均非新增行为验收。
- [x] 复现512预算下搜索输出412 token、说明不完整但加载metadata仍存在；未修改产品代码。
- [x] 用户选择 Native，并授权隔离工作区/分支同步，随后明确使用 rebase 而非 merge。
- [x] 使用 using-git-worktrees 建立隔离工作区，fetch origin，先 ff-only 更新 feat/agent 至 origin/feat/agent，再 rebase origin/feature=a736cda；无冲突，基线包含检查通过，未推送。源工作区用户改动保留原处。
- [x] 已将获准 design/plan 复制进实施工作区，使用 apps/agent/scripts/install.sh --dev 新建 app 自有环境；不复用/修改源工作区 editable 环境。隔离环境已补无凭据.env并通过全量1201项迁移前基线。

### Task 1: 统一预算默认与上限

**Files:** 第1节 Task 1。

**Interfaces:** `ToolExecutionSettings` 字段不增删；`ToolExecutionPolicy.prepare(arguments, *, batch_remaining_tokens=None)` 和 `batch_output_tokens` 接口不变；`get_config` 配置优先级不变。

- [x] 在 `test_execution_policy.py` 更新默认断言并增加参数化窗口用例：

```python
@pytest.mark.parametrize("window,single,batch", [
    (None, 25000, 100000), (128000, 10240, 19200), (20000, 1600, 3000),
])
def test_execution_policy默认预算与窗口限制一致(window, single, batch):
    policy = ToolExecutionPolicy(context_window=window)
    assert policy.prepare({}).budget.output_tokens == single
    assert policy.batch_output_tokens == batch
    if window is None:
        assert policy.prepare({}).budget.preview_tokens == 22500
```

- [x] 添加 `test_execution_policy新上限及低预算兼容`：25000合法、25001非法、batch100001配置非法；显式512保持512；完整显式4000/16000旧配置可加载；仅配置max16000而省略default明确报错。将旧非法值16001更新为25001，保留bool/NaN/最小组信封断言。
- [x] 在 `test_config_loader.py` 分别验证实际仓库配置和 monkeypatch `_settings_resource` 选择的打包资源 default=25000；显式路径/环境优先级及无关值不变。
- [x] 运行红灯：`apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/tools/test_execution_policy.py apps/agent/test/agent_app/config/test_config_loader.py -q`；应出现新默认/上限断言失败，不接受collection error。
- [x] 精确修改 `ToolExecutionSettings` default/max/batch 为25000/25000/100000，max/batch增加le上限；核对 `git diff -- apps/agent/settings.json` 后，只改无冲突预算键及打包资源相同键。不改读取优先级。
- [x] 同命令绿灯；必要时更新实际依赖默认值的既有测试，使其显式指定原用例所需预算，不能弱化行为断言。记录配置冲突例外。
- [x] 记录逻辑单元 `feat(agent): raise tool result budgets consistently`，不执行提交。

### Task 2: MCP表示、诊断与安全出口

**Files:** 第1节 Task 2，特别包含 `plugin.py` 最终错误包装层。

**Interfaces:** 保持 `MCPResultConverter.convert(MCPCallResult) -> ToolExecutionResult`、`MCPClientManager.call_tool(...)`、现有 `_validate_arguments(descriptor, arguments) -> None`。list缺省=self.default_page_size；search Schema/运行缺省均=min(5,self.max_page_size)。内部诊断helper不公开为core API。

- [x] `test_result_converter.py` 增加 `test_converter仅object_list转换及原语义去重`：object键序无关、数组顺序有意义、True与1不同、标量文本保留、不同值但脱敏后相同不能误删；补充文本/图片/资源不丢，嵌套JSON字符串仍为字符串且敏感值被清除。
- [x] 增加 `test_converterJSON失败诊断不因去重消失`：JSON文本转data及与structured去重两条路径，error仍有合成原因、无合成密钥。`is_error=False`但业务retcode失败仍success=True；仅structured错误允许通用error且证据保留。不照搬源补丁要求原因消失的测试。
- [x] `test_client_manager.py` 增加 `test_manager诊断有界且隐藏动态键和值`：required/type/enum/数组/anyOf/oneOf、未知动态键占位、非法schema/外部ref；8条、4层、每条240字符，tool_ref128字符；敏感参数值/enum值/未知键名不得出现在诊断或暴露异常链。同步桥/异步非法调用远端次数为0，合法参数不被改写。
- [x] `test_plugin.py` 增加 `test_plugin最终错误不拼回原始tool_ref`：sync/async分别传含合成token及长文本的标识，让manager失败；最终error没有敏感标识，展示标识不超过128，传给manager的实际标识未变。
- [x] `test_tools.py` 增加 `test_catalog_schema默认与实际调用一致`：list默认17/50；search通常5；max_page_size=1时Schema/缺省实际limit均1；显式越界仍拒绝。
- [x] 红灯运行 `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/mcp -q`，确认新增语义断言失败。
- [x] 在converter先收集脱敏错误文本，再做object/list表示转换/原语义去重；success只取is_error。就地改善manager有界安全诊断、plugin错误标识及tools默认值，不迁业务解释器、不停用Redactor、不改变取消/连接/重放策略。
- [x] 同命令绿灯；保留图片限制、外部ref禁止、取消透传、不重放和后续重连现有断言。Task 4另验证诊断在短预览中存活。
- [x] 记录逻辑单元 `feat(agent): improve MCP result and validation quality`，不执行提交。

### Task 3: Read字符续读、Bash与裁剪标记

**Files:** 第1节 Task 3。

**Interfaces:** `ReadTool.invoke/ainvoke` 现有签名不变，arguments新增text_offset默认0非负整数/max_chars默认2000范围1–20000；输出新增同名参数、truncated、next_text_offset。Bash调用签名、UTF-8/换行语义不变；省略marker API不变。

- [x] `test_tools.py` 增加 `test_readUnicode字符分片可完整重组`：用中文/emoji长行及第二行，固定path/offset/limit串联next_text_offset，拼接等于原行页无重复遗漏；默认正文最多2000字符，has_more=false仍可truncated=true；sync/async相同。
- [x] 增加 `test_read字符边界与编辑后无快照承诺`：0/页长/超页长、负值/bool、max_chars=0/20001，页尾为空且next_text_offset=None；原offset/limit行分页保留。编辑后用原路径重新读取反映新内容，不引入版本状态。
- [x] 增加 `test_bash普通非零空stderr提供退出原因`：stderr空或空白时含`exit_code=7, no stderr`；非空stderr保留；超时/采集不完整原诊断metadata优先，不新增命令重放。
- [x] `test_result_budget.py` 增加 `test_result_budget省略标记可识别且避让业务key`：mapping/list嵌套省略标记含truncated=True、文本含truncated，业务truncated与_icarus_omitted不被覆盖。
- [x] 红灯运行 `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/builtin_tools apps/agent/test/icarus/core/tools/test_result_budget.py -q`。
- [x] 以现有行页读取结果做Python字符切片，新增严格参数验证和进度字段；仅普通Bash空stderr分支补fallback；就地补marker和builtin静态提示。Read提示明确先字符后行、预算裁剪用原游标减小max_chars重读、文件修改后重新读取，不声称内存有界或副作用回滚。
- [x] 同命令绿灯，保留Windows Bash和现有进程组清理用例。不因Unicode要求引入编码探测/字素库。
- [x] 记录逻辑单元 `feat(agent): add resumable text reads and clear truncation hints`，不执行提交。

### Task 4: 完整失败证据与短预览

**Files:** 第1节 Task 4。

**Interfaces:** `ToolExecutor.finalize_group(...)`、`render_tool_result(...) -> ToolExecutionResult` 保持签名；`result_budget.py`新增 `FAILED_RESULT_PREVIEW_TOKENS=1000`，失败预览用私有helper。复用 `StoredToolResult.complete`，不增加存储类型或源数据脱敏旁路。

- [x] `test_tools.py` 增加 `test_failed_result先保存再按实际预算短预览`：失败正文约1500token、普通预算25000，完整文件含全部正文和诊断，visible<=1000；实际512时visible<=512，小失败不落盘。文件不包含内部metadata。
- [x] 参数化 `test_failed_result无完整存储不强制1000`：无store/缺task_id/写失败/文件字节上限/源output_capture_incomplete；不伪称完整保存，在实际预算25000且原结果可容纳时不强制缩为1000。16MiB场景可复用小字节上限桩并保留真实默认值断言。
- [x] `test_result_budget.py` 增加 `test_failed_preview保留诊断退出码图片及metadata`：宽mapping仍保exit_code、有效stdout及error尾部原因，完全重复stderr才去重；图片/envelope大于1000但小于实际预算时回实际预算，仍超实际预算则明确预算错误。
- [x] MCP `test_integration.py` 增加 `test_MCP_JSON失败短预览仍有实际原因`：沿Task 2转换后过真实executor/finalize，最终error保原因、无密钥，完整文件可用。
- [x] 红灯运行 `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/tools/test_result_budget.py apps/agent/test/icarus/core/tools/test_tools.py apps/agent/test/agent_app/plugins/mcp/test_integration.py -q`。
- [x] finalize对超实际预算结果或>1000token失败尝试保存预算前结果；只在complete=True时主动缩短失败。渲染保留success/images/原metadata与保存统计，优先诊断、退出码、stdout，按真实分配预算最终断言。不得将路径存在当成complete。
- [x] 同命令绿灯；成功结果策略不变，原始/可见token统计和文件降级状态正确。控制metadata在渲染阶段保留，Task 6再按来源收紧授权。
- [x] 记录逻辑单元 `feat(agent): preserve full failure evidence with bounded previews`，不执行提交。

### Task 5: 分发请求身份与完成事件访问属性

**Files:** 第1节 Task 5。runtime-update只新增测试，不改公共payload。

**Interfaces:** `tool_execute.py`新增 `DISPATCHED_TOOL_METADATA="dispatched_tool"`；`AgentToolCompletedEvent.target_tool_call: ToolCall`只读属性；invoke/ainvoke接口不变。metadata表示进入目标执行器的分发请求，不是实际副作用或before Hook后参数的证明。

- [x] `test_tool_execute.py` 增加 `test_tool_execute分发请求深拷贝并保留结果`：sync/async目标成功/失败均保原result字段和metadata；分发id=原id，name=目标，arguments去顶层_execution且嵌套隔离；前置门控/Schema失败和执行器抛异常无伪造完成记录。
- [x] 增加 `test_tool_execute记录分发参数而非冒充Hook后实参`：before Hook改写目标参数，目标收到改写值；metadata仍为原分发请求；Hook只观察一次目标，不重复桥入口。
- [x] `test_react_agent_stream.py` 增加 `test_completed目标属性安全回退且深拷贝`：仅外层name=tool_execute、记录name/id非空字符串、id匹配、arguments=dict时解包；其余回退原ToolCall。修改返回参数不改metadata。stream/astream原事件tool_call仍为外层。
- [x] runtime-update `test_plugin.py` 增加 `test_public分发事件仍外层配对且不泄漏内部参数`：started/completed同id同name；内部合成敏感值不在新增公共字段；as_dict/模型消息不含dispatched_tool。
- [x] 红灯运行 `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/tools/test_tool_execute.py apps/agent/test/icarus/core/agents/test_react_agent_stream.py apps/agent/test/agent_app/plugins/runtime_update -q`。
- [x] 目标执行器返回结果后合并深拷贝分发metadata；属性按上述条件解包并深拷贝，缺失/非法安全回退，不改public投影或历史消息，不新增tracker。
- [x] 同命令绿灯；保留现有单次目标Hook观测断言，不将目标executor返回失败解释为业务Tool已执行。
- [x] 记录逻辑单元 `feat(agent): expose dispatched tool identity on completion`，不执行提交。

### Task 6: 最终发现授权与恢复信封

**Files:** 第1节 Task 6。`react_agent.py`原则上不必修改；如确需修改，四路径对称，不引入插件依赖。

**Interfaces:** `ToolSearchTool.definition`固定Schema增加可选`name: str`；命中output为`{scope, tools:[{name,description,input_schema}], ...}`；新增 `ToolSearchTool.finalize_loaded_tools(result: ToolExecutionResult) -> ToolExecutionResult`。`ToolSurfaceCatalog.search(scope)`不变，name在搜索工具内精确筛选。finalize只改metadata，不改输出/success/images/token统计。

- [x] `test_tool_search.py` 更新旧字符串断言，增加 `test_search精确scope及name返回结构化原定义`：正常scope整组、name只在scope内筛选、无命中无授权、目录/常驻排除不变、Schema无_execution、sync/async相同。
- [x] 增加 `test_search最终完整性逐项校验`：候选名单保序去重；仅完整name/description/input_schema通过；部分条目完整可授权；bool/number替换不通过；失败结果、整体tools被替换或零完整条目不授权。不得通过裁坏scope文本扩大到其它Catalog定义。
- [x] `test_tools.py` 增加 `test_finalize二次裁剪后再过滤加载名单`：长description/嵌套Schema与同组结果争预算，逐个核对最终可见完整条目等于tools_loaded；保留visible_tokens及metadata，其它字段不被finalizer改动。
- [x] 增加 `test_finalize非搜索及桥内目标不能伪造授权`：普通工具、命名为tool_search的非ToolSearchTool、经tool_execute分发的业务结果携带伪造tools_loaded，均不能增加授权；其它metadata保留。结果Hook裁坏定义后同样不得授权。
- [x] `test_result_budget.py` 增加 `test_preview分页恢复标量先于正文保留`：Read行/字符字段、MCP page/next_page和next_cursor，原值完整保留；正文仍有truncated标记；不按plugin身份分支、不搜索嵌套业务协议。超长游标整体省略而非半截，fallback不要求重放，有完整文件才称可读完整证据。
- [x] 在 `test_react_agent.py` 与 `test_react_agent_stream.py` 覆盖四路径 `test_裁剪搜索不授权而精确恢复后可执行`：脚本模型先大scope搜索、调用未加载目标失败、精确name搜索得到完整定义后可执行；每step工具定义相等、跨Run重新门控；已完整加载者不因后续截断搜索被撤销。
- [x] builtin `test_tools.py` 增加 `test_read预算裁剪后原游标缩片可恢复`：真实executor输出保原next_text_offset但明确正文被裁；原offset/text_offset减小max_chars重读不漏正文，不把工具游标改成“展示进度”。
- [x] 红灯运行 `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/tools apps/agent/test/icarus/core/agents apps/agent/test/agent_app/application/test_session_runtime.py apps/agent/test/agent_app/plugins/builtin_tools -q`。
- [x] 实现结构化搜索/精确name和无状态finalizer；ToolExecutor最终渲染后仅按实际注册ToolSearchTool实例过滤授权，其他结果移除/忽略tools_loaded。不按工具名字符串信任元数据，不创建通用策略注册框架。
- [x] 在result_budget当前mapping层为指定恢复标量预留预算，超大信封最小降级并保持最终预算断言；更新搜索说明和base_prompt静态流程提示。明确读文件不授权、单项极大定义可无法加载、通用结果不得指导重放。
- [x] 同命令绿灯；在3000/25000默认、128000/20000窗口与显式512预算测试裁剪/完整性。Session装配仍9工具；tools=None/空名单/白名单、Catalog独立、单次Hook和跨Run重置现有测试保持通过。
- [x] 确认不增加MCP每Run加载凭据、不收紧LLM before Hook；本期保证止于最终Tool Result，而非厂商请求送达。记录逻辑单元 `fix(agent): align lazy tool authorization with visible definitions`，不执行提交。

### Task 7: 全量验收、实现审查与arch

**Files:** 新建 `apps/agent/docs/spec/2026-10-29-tool-call-quality/arch.md`；更新本目录design/plan状态及第1节列出的既有arch。仅依据最终源码和测试，不预写已实现架构。

- [x] 各Task记录红/绿命令及结果；完成最小目录后运行Agent全量，再compile/diff。保留实际失败身份，区分环境/既有失败/实现回归，不修无关失败。
- [x] 顺序执行最终命令：

```bash
make test-agent
make typecheck-agent
make check-package-agent
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test
git diff --check
```

- [x] 运行 requesting-code-review：独立审查本次diff是否符合design六组范围、架构、业务语义、脱敏、预算和授权来源约束；先验证反馈再修复，修复后重跑受影响测试及必要全量。
- [x] 创建本feature arch：记录真实文件职责、预算/落盘/预览/授权顺序、分发语义、同步异步、公共投影及MCP/LLM Hook保证终点。更新受影响旧arch的默认值/发现输出/门控，区分旧基线与当前实现；用实际源码和测试校正，不只复制design。
- [x] 回填plan实际通过数/退出码、未执行项和用户配置例外；更新design状态为与实际相符。文档改完检查相对链接及未跟踪文件空白（`git diff --check`不会覆盖未跟踪文件）。
- [x] 公共投影不变则不扩大app改动；若发现公共行为需要改变，停止并重审范围，必要时再跑Gateway/TUI，而非静默扩展协议。
- [x] 真实模型smoke只有另获执行/成本授权且凭据可用才运行；否则明确未执行。未授权不提交/推送/合并，不清用户数据/结果文件，不reset --hard。

## 10. 自审与执行记录

计划校准自审：design第1–3/10节→准备、Global Constraints和最终审查；第4→Task1；第5→Task2/4；第6→Task3/6；第7→Task4；第8→Task5；第9→Task6；第11→Task7；第12全部审查澄清已映射到约束与测试。接口沿用现有类型，finalizer只改metadata，MCP search小上限缺省与运行一致，不再保留原plan的通用重放提示或“分发即执行完成”语义。

| 项目 | 状态 |
|---|---|
| 设计审查与用户确认 | 已完成；用户回复“开始吧” |
| 计划校准及自审 | 已完成；Native隔离执行 |
| 迁移前Agent全量 | 源工作区1201 passed；隔离环境补无凭据.env后1201 passed，exit 0 |
| Task 1–6 | 已实施；任务末聚焦验证依次44/77/47/33/47/194项通过（目录有重叠，不相加） |
| 实现后全量/typecheck/package/compile/diff | 提交前重验Agent1274 passed（74.29s）；273源码typecheck通过；wheel资源/安装/发现、compile/diff通过 |
| 实现后独立审查与arch | arch已补；两项Important及原延后Minor均经RED→GREEN修复，最新全量通过 |
| 真实模型smoke | 未执行 |
| 分支操作/commit/push | feat/agent已rebase同步origin/feature；按用户授权拆分为预算、核心结果/发现执行链、MCP、builtin、文档五组提交；推送/PR状态以Git托管端为准 |

## 11. 独立整体审查与修复记录

- 无Critical。两项Important已复现并修复：完整存储失败短预览的cursor可能被裁成头尾字符串；MCP真实manager缺失目标异常会把长tool_ref重新拼回。新增512/25000预算、两种字段顺序、短/长cursor及sync/async真实manager回归，修前6 failed/22 passed，修后28 passed；受影响core工具+MCP180 passed。
- 修复：失败分页结果复用原子恢复信封处理；manager未知工具标识限128且脱敏，Plugin最终异常正文另限2200字符，保留原校验诊断。
- 原延后Minor已按用户要求修复：target_tool_call读取前检查外层Mapping；新增metadata=None/list/string × tool_execute/ordinary共6项回归，修前6项均因AttributeError失败，修后安全回退原ToolCall并保持metadata不变。完整应用及静态检查结果回填于上表。
- 保持设计明确非目标：MCP Run内发现授权、LLM before Hook后的送达、core既有参数校验回显、验证CPU界限/退出追踪、Read内存/字素/文件快照与真实付费smoke。

回滚只按逻辑单元、在用户授权后处理，不移除历史消息/结果文件，不覆盖随后修改的配置。文档与实现建议拆分，提交需另外授权并遵循仓库署名规则。
