    # Prompt Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现应用自有的静态/动态提示词插件，并支持创建时私有、不可变、可恢复的外部 session 指令。

**执行状态（2026-10-17）：** T1–T9完成，独立审查5项已修复；具体RED/GREEN、取舍、未执行项与整套结果见[plan-validation.md](plan-validation.md)。以下复选项保留原计划步骤，不把未逐项单独执行的命令伪标为完成。

**Architecture:** Prompt 负责来源与预算，Blackboard 保留历史/readiness/压缩职责。初始化由应用回调收集静态快照，运行期通过业务 Event 和 `prompt.read` 通信；外部指令在现有 conversation_updates 表保留序号 0，完全不进入公共更新序列。

**Tech Stack:** Python 3.11+、dataclass/Protocol、asyncio、Pydantic、现有 SQLAlchemy/SQLite、pytest，Gateway 复用 FastAPI/JSON-RPC；不新增依赖。

**Spec:** [功能设计](plan.md)；实施前必须先读 [审查记录](plan-review.md)。Gateway 所有者任务见 [RPC 计划](../../../../gateway/docs/spec/2026-10-17-session-instructions/plan.md)。

## Global Constraints

- 只改 Agent 与 Gateway 对应层；`agent_app → icarus.runtime → icarus.core`，不让框架导入 Prompt/Persona。
- 不新增表/列/迁移，不新增公共 RuntimeUpdateType；公共 sequence 仍为正数且连续。
- 不新增 reset/reload、缓存状态探测、文件监听、version/hash/epoch 响应字段、数值 priority。
- Prompt 无持久化 state；persona/主动观测只用测试插件，不实现这些产品插件。
- 静态渲染 base→workspace→persona→external；动态 mode→contributor→runtime；同层 source+section_id 排序。
- 总预算默认 8192，单节 4096 token 保守估算，来源 deadline 默认 5 秒；超限不截断，optional 整节丢弃，required/base/external 失败。
- 所有来源文本视为数据；不复制外部产品的身份、厂商特殊逻辑或不存在的工具能力。
- Event 发布身份取运行时，不能信任贡献者自报 source/required；动态 sibling 方法调用禁止。
- 测试先 RED→GREEN，先最小文件再套件再 compile/diff；不以 ImportError/收集错误当行为 RED。
- 当前阶段只写计划。实施前重新检查工作树、目标 remote/feature baseline，未经授权不 fetch 后 merge、不建分支/提交/推送；本计划的任务结束点不是自动 commit 授权。

## Review Focus

1. 指令-only + lazy create + 进程重启：指令必须原样恢复而 public history/cursor 均为空/0（T3、T4、T8）。
2. persona restore、optional start 失败、compaction 同 task：只能采样有效恢复状态，压缩后的第一个请求就应使用新 system（T6、T7）。
3. 贡献先到/缺席/迟到/重复与取消：不会永等、重复启动 Agent、把旧任务内容注入新任务（T5、T6）。
4. Unicode 边界、渲染包裹开销、可选与必需混合：两层限额一致、不截断外部原文、不静默丢失 required（T2、T4、T5、T8）。
5. 私有标记误走普通 update、错误异常链、摘要/标题：所有 wire 出口都不带私有正文，正常 public sequence 连续（T3、T4、T8、T9）。

---

## 0. 当前基线与文件分工

源码基线 `b1cc666`。本轮已运行 Agent 833 passed、Gateway 20 passed（1 条既有 AnyIO 弃用警告）；这不是新功能验收。实施开始重新记录基线，保留 `.claude/` 等用户工作，不触及真实 DB/.env。

### 新建文件

| 路径（相对仓库根） | 唯一职责 |
|---|---|
| `apps/agent/src/agent_app/contracts/__init__.py` | 轻量应用契约包，不导入插件实现 |
| `apps/agent/src/agent_app/contracts/prompt.py` | section、静态 Protocol、两类 Event、prompt.read 和安全错误类型 |
| `apps/agent/src/agent_app/plugins/prompt/{__init__,config,factory,plugin}.py` | 标准插件、配置、注入设施与生命周期 |
| `apps/agent/src/agent_app/plugins/prompt/manifest.json` | base/required、contracts、published/consumed/能力声明 |
| `apps/agent/src/agent_app/plugins/prompt/base_prompt.py` | 单一 DEFAULT_SYSTEM_PROMPT 常量 |
| `apps/agent/src/agent_app/plugins/prompt/composer.py` | 排序/渲染/整节丢弃与纯预算检查 |
| `apps/agent/src/agent_app/plugins/prompt/sources.py` | 根目录工作区文件安全有界读取 |
| `apps/agent/test/agent_app/contracts/test_prompt_contract.py` | 契约与失败响应 |
| `apps/agent/test/agent_app/plugins/prompt/test_{composition,workspace_sources,budget,plugin,integration}.py` | 镜像功能测试 |
| `apps/agent/test/agent_app/application/test_session_instructions.py` | 私有记录、load/restore、API/隐私集成 |
| `apps/gateway/test/test_session_instructions.py` | Gateway 参数、错误、安全与 wire 集成 |

需要的测试目录 `__init__.py` 随对应任务创建，不额外建包壳/空占位文档。

### 修改文件组

- Store：`application/repositories/session_store.py`；**不改** `session_entities.py` 的 schema。
- 恢复入口：`application/controllers/session_api.py`、`services/session_registry.py`、`session_runtime.py`；同步更新注入 session_factory 的测试。
- 隐私出口：`services/history_projector.py`、`services/update_pipeline.py`、`runtime_update_stream.py`。
- Blackboard：`plugins/blackboard/{config,factory,plugin,manifest}.py/json`；现有 readiness/state/prompt_composer 类型不重复造模型。
- 默认/打包：`config/config_model.py`、`resources/default_settings.json`、`test/test_packaging.py`、`scripts/check-package.sh`、相应配置/装配测试。
- Gateway：`src/protocol/methods.py`；只有确需装配注入 validator 时改 `app.py`，不改 ConnectionHub/发送协议。
- 文档：Agent 所有者计划、Gateway 所有者计划、最后按实现核实更新既有现状文档与 CONTRIBUTING 的 manifest 数。

## 依赖与任务顺序

`T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9`。
T3 只依赖 T1/T2，可与 T5 的独立开发分支并行，但本计划默认顺序执行，避免多处装配接口竞改。

## T1：最小契约可被现有 Runtime 正确加载

**Files:** Create `contracts/{__init__,prompt}.py`；Test `test/agent_app/contracts/test_prompt_contract.py`；引用 `icarus/runtime/contracts.py` 和 `core/capabilities/{base,catalog,requester}.py`，不改框架目录。

**Interfaces**
- Consumes：`Event`、`CapabilityRequest/Response/Status`、`CapabilityContract`。
- Produces：功能设计 §3 的 `PromptSection`、`PromptContributor`、`PromptContributionEvent`、`PromptUpdateEvent`、`PromptRead{Request,Response}`、`PromptRead`。
- `PromptError(code: str, message: str)` 仅保存安全信息；`SESSION_INSTRUCTIONS_TYPE = "session.instructions"` 为私有记录标记，**不是** RuntimeUpdateType。

- [ ] **RED：写行为测试。** 用动态测试导入/hasattr 后 assert 缺少契约（不要让收集失败）；随后验证：
  ```python
  response = CapabilityRequester.failure(
      PromptReadRequest(reason="start"), CapabilityStatus.TIMEOUT
  )
  assert response.system_prompt == ""
  assert response.status is CapabilityStatus.TIMEOUT
  assert set(EVENTS) == {"prompt.contribution", "prompt.update"}
  assert CAPABILITIES == (PromptRead,)
  ```
  额外 assert：非法 layer/空 section_id/非字符串 content 拒绝；frozen dataclass；初始失败响应无需提供 system_prompt；Protocol 只有静态方法。
- [ ] **验证 RED。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/contracts/test_prompt_contract.py -q`，确认预期断言失败。
- [ ] **GREEN：实现定义。** capability_id/response_type 使用 ClassVar；response.system_prompt 默认 `""`，请求 reason 默认 start。EVENTS 和 CAPABILITIES 都导出。字符串标识验证遵循当前 `_ID_PATTERN` 风格，不新增公用验证框架。
- [ ] **验证 GREEN。** 同命令；加跑 `apps/agent/test/icarus/runtime/test_contract_exports.py` 与 `test_contract_bundles.py`，确保加载仍通用。
- [ ] **检查点。** 仅记录结果/偏差；不提交。

## T2：基础文案、文件发现和不会截断的预算

**Files:** Create `plugins/prompt/{__init__,base_prompt,config,composer,sources}.py`；Test `test_{composition,workspace_sources,budget}.py`。`__init__` 不提前导入插件实现或 contracts。

**Interfaces**
- `PluginConfig`：`max_prompt_tokens: StrictInt=8192`、`max_section_tokens: StrictInt=4096`、`contributor_timeout_seconds: float=5.0`、`required_contributors: list[str]=[]`；extra=forbid，数字正值/有限且单节≤总量，来源唯一。
- `validate_instructions(instructions: str | None, *, max_tokens: int) -> None`：纯函数，非字符串/坏编码 prompt_invalid，超限 prompt_too_large；None/空字符串合法，不 strip。
- `load_workspace_section(workspace_path: Path, *, max_tokens: int, logger: logging.Logger) -> PromptSection | None`。
- `compose_prompt(sections_by_source: Mapping[str, tuple[PromptSection, ...]], *, layers: tuple[str, ...], required_sources: frozenset[str], max_tokens: int, max_section_tokens: int) -> str`：参数决定单次静态/动态渲染，固定稳定顺序。
- 总量复核复用 `conservative_token_count`；不要复制 token 算法或引入 tokenizer 依赖。
- `prompt_context_block(content: str) -> ContextBlock` 在 composer.py 定义：构造归属 prompt 的单块。`compose_prompt` 另接可选 `cost: Callable[[str], int]=conservative_token_count`，动态路径传入实际 JSON 包裹计数，不复制 Blackboard serializer。
- 组合根提供 `measure_dynamic(system_prompt: str, content: str) -> int`（用实际 board.prompt_composer.serialize_context([prompt_context_block(content)]) 与保守计数），传给 Prompt.prepare；同一组合根向 Blackboard 注入 `validate_prompt_context(system_prompt: str, content: str) -> None`，超限抛安全 PromptError。两者闭包捕获同一配置/计数规则，具体插件不互相 import/调用。

- [ ] **RED：预算与来源测试。** 默认值 assert 8192/4096/5；T=4 时 `"abcd"` 合法、`"abcde"` 失败、`"中"*4` 合法、5 个失败、4 个 emoji 因 UTF-8 为16 bytes 失败。正文前后空白完全保留。超限 optional 整节不在结果中；external 不能被挤掉；用非常小预算证明标题/JSON wrapper 也计费，不能正文刚好等于上限而总渲染越界。
- [ ] **RED：发现与稳定顺序测试。** 临时目录两文件并存仅 AGENTS；不存在回退 CLAUDE；均不存在空；存在但空/坏编码/目录不改读 fallback；工作区外 symlink 拒绝。改变输入 map 顺序输出相同；分层顺序不被文本内容影响。
- [ ] **验证 RED。** 聚焦运行上述三文件，失败必须对应缺行为而非 import 收集。
- [ ] **GREEN：实现纯函数/读取。** 读取前检查路径/普通文件，最多 `3*T+1` bytes，UTF-8 解码；异常消息不带原文。base/external 为保留 owner，其他来源不得冒充。optional 用反向 layer/source/section 顺序逐节丢弃；基础文案严格采用设计 §7，文案不包含第三方品牌身份/不可用工具承诺。
- [ ] **验证 GREEN。** 三文件通过；将原 `test_session_runtime.py` 里的两项基础文案行为断言迁入本插件测试，不删除记忆/简短表达约束。原应用导入常量处稍后 T7 连线更新。
- [ ] **检查点。** 输出文案审阅及行为验证，不以几项子串断言宣称模型效果已验证；不调用远端模型。

## T3：现有 DB 原子保存私有创建指令

**Files:** Modify `application/repositories/session_store.py`；Test `test_session_store.py`、`test_session_instructions.py`、`test_history_page.py`。若现有 history/pipeline 的错误路径可接收伪造私有 update，同任务改 `history_projector.py`、`update_pipeline.py`、`runtime_update_stream.py` 并加镜像测试；不改 schema/wire/core RuntimeUpdateType。

**Interfaces**
- Extend `SessionStore.create_session(identity: SessionIdentity, *, instructions: str | None = None) -> None`。
- Add `SessionStore.get_session_instructions(identity: SessionIdentity) -> str | None`，只读活跃 session 的 raw 序号0行。
- 私有行固定 sequence=0/task_id=None/update_type=session.instructions/payload={content: original}；公共 last_sequence 不递增。

- [ ] **RED：原子与恢复测试。** 创建 instructions="\nPRIVATE-哨兵😀\n"，重新打开 store 后 getter 原样返回；raw DB 只有一条 seq0；public history 为空 cursor=0。mock 私有 insert flush 失败，同事务的 session 不存在；取消同样 rollback。重复 create/删除后重用同 ID 不覆盖旧内容。
- [ ] **RED：公开隔离测试。** 在私有行之后 append 两个普通更新，返回 sequence=[1,2]，分页 limit=1 完整取得它们，完整性校验不报损坏；指令-only session 无 first_user_input/public activity，可 discard_empty，不产生摘要/标题。
- [ ] **RED：坏记录/误路由测试。** seq0 标记错、非字符串 content、task_id 非空、正数位置的私有标记均不能公开或静默忽略；普通 append/stream 明确拒绝该标记；已有 legacy session 没 seq0 则 getter None。检验表名集合前后相同。
- [ ] **验证 RED。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/test_session_instructions.py apps/agent/test/agent_app/application/test_session_store.py apps/agent/test/agent_app/application/test_history_page.py -q`。
- [ ] **GREEN：同事务插入。** 扩展现有 create 的 transaction，不调用另起事务的 append_update；无 setter/upsert。read_updates 数据与 count/min/max 统计加 `sequence>0`；校验公共序列不放松。检测错误位置的私有标记不将正文放进异常。公共流入口/历史投影作为纵深防护拒绝私有类型。
- [ ] **验证 GREEN。** 上述文件与 `test_runtime_update_stream.py`；检查 public cursor 没有新定义，客户端无需适应隐藏序号。
- [ ] **检查点。** 在临时 DB 实验，不改真实数据；记录无 schema 迁移证据。

## T4：所有创建/恢复路径可靠携带指令

**Files:** Modify `controllers/session_api.py`、`services/session_registry.py`、`session_runtime.py`、`application/__init__.py`/现有异常导出按需要；Test `test_agent_runtime.py`、`test_session_instructions.py`。同步修改所有显式 session_factory 测试桩签名，不引入 inspect.signature 兼容分支。

**Interfaces**
- `SessionApi.create_session(workspace_path, session_id=None, *, load_runtime=True, instructions: str | None=None) -> str`，旧调用行为不变。
- `SessionApi.validate_session_instructions(instructions: str | None) -> None`：同步读取实际 prompt PluginConfig 并调用 T2 validator；供 Gateway 提前校验，create_session 入口仍须独立调用。
- 公开对象无需新 facade：当前 `controllers/session_api.py` 末尾 `AgentRuntime = SessionApi`，`application/agent_runtime.py`、`application/__init__.py` 重导出同一对象。T4 在 `test_refactor_contract.py` 验证 canonical identity，并直接 `AgentRuntime(...).validate_session_instructions(...)`；T8 真实对象测试不能仅依赖 RuntimeStub。
- `SessionRegistry.__init__(..., load_instructions: Callable[[SessionIdentity], Awaitable[str | None]], ...)`。
- `SessionRuntime(..., instructions: str | None=None, ...)` session-scoped 注入，仅进入 prompt 配置。
- 安全 `PromptError` 在应用公开入口导出，Gateway 可识别，不返回源文本。

- [ ] **RED：lazy 创建校验。** 直接调用应用（绕过 Gateway）且 load_runtime=False，超单节限制则 raise PromptError.code==prompt_too_large，store 不存在新 session，session_factory 未调用。None/空/Unicode 原文语义一致。
- [ ] **RED：重载路径。** lazy create→销毁整个 AgentRuntime→新对象 submit、unload→submit、并发 resume：只有一次加载、传入完全相同指令。两个 session 互不污染共享 ConfigModel。getter 异常/坏记录时不构造缺指令 runtime；无指令旧 session 正常。
- [ ] **RED：启动失败保留契约。** store 已提交但无关插件启动失败时记录仍在，后续 retry 能恢复；不得为“失败无记录”破坏现有 test_agent_runtime_create失败保留目录且后续可重试。
- [ ] **验证 RED。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/test_session_instructions.py apps/agent/test/agent_app/application/test_agent_runtime.py -q`。
- [ ] **GREEN：创建前调用共享纯 validator；把指令与 session 一起交 Store。** 每次 `_load_session` 在现有 try/single-flight 锁语义内先读 DB 再构造，复用失败清理。不把 instructions 放入 user.message、title prompt、status、日志或全局配置。
- [ ] **验证 GREEN。** 上述 tests + session_registry/service_composition/refactor_contract 有关测试；检查公开对象身份不复制。
- [ ] **检查点。** 本任务暂不让 prompt 对外 READY；T7 接入配置后做真实运行时验证。

## T5：Prompt 插件提供静态查询与非阻塞动态聚合

**Files:** Create `plugins/prompt/{factory,plugin}.py`、`manifest.json`；Test `test_plugin.py`、`test_integration.py`；必要时给本插件加 `state.py` 普通 dataclass 以缩小 plugin.py，不登记子插件。

**Interfaces**
- `PromptPlugin(BasePlugin)`：`provided_capabilities=(PromptRead,)`，config_schema=PluginConfig。
- 构造：`PromptPlugin(plugin_id: str, *, workspace_path: Path, config: PluginConfig, instructions: str | None, task_channels: TaskChannelRegistry, logger: logging.Logger, base_prompt: str=DEFAULT_SYSTEM_PROMPT)`；factory 复用 identity/task_channels 注入设施，配置用户字段与资源分开。
- 装配方法 `prepare(static_sections: Mapping[str, tuple[PromptSection,...]], *, dynamic_sources: frozenset[str], update_sources: frozenset[str], required_sources: frozenset[str], measure_dynamic: Callable[[str, str], int]) -> None`：只接收复制数据和组合根提供的纯计数函数，无 sibling 对象；重复准备拒绝。
- manifest：tier base；contracts=agent_app.contracts.prompt；provide prompt.read；publish context.contribution/task.context_input；consume input.user_input/input.finished/prompt.contribution/prompt.update/task.context_input_result。后两类 prompt Event 可 optional_consumed_events，允许无贡献者；无 tools/state_scopes。
- `@handles(PromptReadRequest)` 返回恰好一个响应；start 读取缓存，refresh 原子地重读 workspace+应用待生效 static，失败保留旧缓存但返回失败。

- [ ] **RED：真实 manager 查询。** 工厂、manifest、contracts 校验；真实 EventBus 中响应匹配、超时/UNAVAILABLE/FAILED 合成不 TypeError。等待慢贡献时 prompt.read 仍可响应。prepare 只接受 persona 静态来源，不允许伪造保留 layer。
- [ ] **RED：聚合。** 假来源作为 BasePlugin 消费 UserInput 并发布真实 PromptContributionEvent。测试并发不同完成顺序得到同样输出；一次 readiness contribution；只有一个 source=prompt 的 ContextBlock；无来源/空成功也释放；required_source 没回复超时失败，optional_source 超时完成但无正文。使用非默认512总预算证明动态包裹计数和 T6 compaction 后检查一致（base override 使用短文本以隔离预算测试）。
- [ ] **RED：乱序/终态。** 贡献早于 user input、重复、旧 task、过期 input、stop 取消、128 个 pending 上限；迟到 prompt.contribution 丢弃，不注入 run、不阻塞下一个任务。invalid layer/重复 section 按来源 requiredness 判定，不容忍空 required 配置对象冒充来源。
- [ ] **RED：更新与预算。** prompt.update 无 task 只能 staged persona；refresh 前 system 一致，refresh 后采用新快照。带 task 的更新只能动态层：实际发 TaskContextInputEvent，result 非 accepted 不宣称已注入；InputFinished 后不发。累计同 task 已注入动态内容计入预算，重复 event_id 不重复注入。
- [ ] **验证 RED。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/prompt/test_plugin.py apps/agent/test/agent_app/plugins/prompt/test_integration.py -q`；使用 asyncio.Event 控制完成而非固定 sleep，deadline 可用短配置测试。
- [ ] **GREEN：实现单 worker + 受管后台 deadline。** 普通 handler 不等待普通 Event；每来源终态完成或 timer 截止后一次 finalize；所有 buffer/future 在 InputFinished/stop 清理。准备期 dynamic 用单一渲染字符串包入 ContextBlock，身份=prompt。最终 XML/JSON wrapper 的实际表示纳入总预算；对 run-time 发出的增量做累计预算，不承诺修改已发送历史。
- [ ] **验证 GREEN。** 同命令 + framework capability transport/quiesce suites；确保取消不被 except Exception 吞掉、没有残留后台任务。
- [ ] **检查点。** 测试 producer 在测试目录，不把 persona/观测产品实现混入此任务。

## T6：Blackboard 接入 prompt.read、失败门禁与同 task 刷新

**Files:** Modify `plugins/blackboard/{config,factory,plugin}.py`、`manifest.json`；Test existing `test_plugin.py` 和新 `test_prompt_context.py`。复用 state.is_context_ready，不改含义。

**Interfaces**
- 构造增加 `prompt_provider_enabled: bool=False`（仅支持独立测试/低级直接调用）、`fatal_context_sources: set[str] | frozenset[str]=frozenset()` 与 `validate_prompt_context: Callable[[str, str], None] | None=None`（组合根注入纯预算检查，生产不能省略）。
- 生产 factory 开启 provider；manifest require prompt.read >=1,<2，consume context.contribution 不再仅为未声明的兼容 handler。
- `_read_system_prompt(reason: Literal["start","refresh"], *, task_id: str) -> str`：await request，unwrap 后才接受；错误不覆盖旧 system，转既有 TaskError。
- 保留 `required_context_sources` 等待完成语义，`fatal_context_sources ⊆ required_context_sources`。

- [ ] **RED：失败区分。** required_context_sources={optional,prompt}，optional failed 仍启动；prompt failed 只发致命 TaskError 不发 context.ready；事件提前到达不会丢失 failed。prompt.read 失败/超时不让任务永等、不发旧 system。
- [ ] **RED：同 task compaction。** 捕获模型请求：第1次使用 system A，修改 workspace 后普通 task 仍 A；强制 _should_compact 真且成功，**该 task 第1次请求使用 B**。refresh 失败该 task 不启动；后续 task 重试 refresh，而不是使用压缩前缓存。compaction 本身失败保留现有历史行为。
- [ ] **验证 RED。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/blackboard/test_prompt_context.py apps/agent/test/agent_app/plugins/blackboard/test_plugin.py -q`。
- [ ] **GREEN：在 _publish_if_ready 现有顺序内加入查询。** readiness完整→可能 compact→读 system(start/refresh)→以最新 system+实际 prompt contribution 复核总预算→compose→context.ready。避免 refresh 增大 system 后突破原动态分配；超限当前任务失败，不重排已发出的贡献。刷新 flag 只在查询成功后清除。运行时上报错误使用安全 code，不把 PromptReadResponse 错误直接塞入模型上下文。不要异步订阅 context.compacted 实现刷新。
- [ ] **验证 GREEN。** 同命令 + Blackboard 全目录，特别保留 optional 来源失败、完整 tool group、安全取消/致命历史提交测试。
- [ ] **检查点。** `ReActAgent`、LLM SDK 和 ToolExecutor 不改；Hook 不新增路由/控制逻辑。

## T7：应用生命周期连线、默认开启及打包闭环

**Files:** Modify `application/session_runtime.py`、`config/config_model.py`、`resources/default_settings.json`、`test/test_packaging.py`、`scripts/check-package.sh`；Test `test_session_runtime.py`、`test_assembly.py`、`plugins/test_config_contracts.py`、`application/test_optional_plugins.py`。文档数字留 T9 随实际落地更新。

**Interfaces**
- before_restore 登记候选 PromptContributor；before_ready 删除禁用来源、采样恢复后静态内容并调用 PromptPlugin.prepare。组合根在本文件定义 `measure_dynamic(system_prompt, content)` / `validate_prompt_context(system_prompt, content)` 两个纯闭包，捕获实际 board serializer 与同一 PromptConfig.max_prompt_tokens；分别注入 Prompt.prepare 与 board.validate_prompt_context。不得在 plugin 内 import sibling implementation；添加非默认预算（例如512）跨聚合/compaction一致性测试。
- 动态/更新来源从成功 registrations 对应 manifest 发现；required_contributors 缺失立即安全失败，不能无限等待未启用插件。
- SessionRuntime 将 instructions/system_prompt override 只注入 Prompt；Blackboard 从能力读取，不再由应用直接拼 system。
- 原 `system_prompt: str=DEFAULT_SYSTEM_PROMPT` 构造接缝可保留作为显式 base override，常量 canonical 定义在 prompt/base_prompt.py；默认文本不再复制在应用层。

- [ ] **RED：恢复与禁用。** 测试 persona stub 的 state restore 后值才被采样；故意 optional start 失败则从 static/dynamic 来源均移除；required 缺失启动失败。无 persona/观测配置的默认会话也能启动。
- [ ] **RED：默认与工厂。** 默认 required 含 prompt；插件 discovery=12，仍不 import 实现；旧 RuntimeFactory fixture 补 instructions 参数；test-only tiny context_window 不为此默认发起真实模型调用。
- [ ] **RED：完整上下文。** 真实 SessionRuntime 捕获最终 system 的 base/workspace/persona/external 次序和输入动态层，Memory/Skill 路径未被替换；两个 task system 一样；新的 SessionRuntime 读最新文件与原外部指令。
- [ ] **验证 RED。** 聚焦 `test_session_runtime.py`、`test_optional_plugins.py`、`test_packaging.py`。
- [ ] **GREEN：只改应用回调，不在 core host 加 Prompt 字段。** 初始化 static 源异常按 requiredness 转化；body 日志保密。freeze/readiness 都在有效插件集合确定后进行。打包脚本两处 11→12，并 assert prompt manifest/base_prompt/contract 模块在 wheel 中，不添加 md 资源。
- [ ] **验证 GREEN。** `make test-agent` → `make typecheck-agent` → `make check-package-agent`，记录实际输出。wheel 从非 repo cwd 导入和 lazy discovery 必须仍通过。
- [ ] **检查点。** 默认 prompt tier=base 不可随意降级；不减少原 required 插件清单。

## T8：Gateway create-only 参数、两层错误与真实 socket 恢复

**Files/接口细节：** [Gateway owning plan](../../../../gateway/docs/spec/2026-10-17-session-instructions/plan.md)。仅改 `apps/gateway/src/protocol/methods.py` 及 Gateway 测试。Agent 公开验证/错误来自 T2/T4，不能直接操作 DB。

- [ ] **RED：参数与两层校验。** instructions 只在 CreateSessionParams；None/空/有效 Unicode 原样转发；非字符串、非法 surrogate、T+1 字符、3T+1 bytes 拒绝且 runtime.create_session 未调用；绕过 Gateway 的失败由 T4 测试证明。submit/steer 传 instructions（包括 None）应 strict extra 拒绝。
- [ ] **RED：错误隐私。** Gateway -32602 与业务 prompt_too_large 均不带源正文，包装的已知 PromptError 能映射，未知异常仍 Internal error。duplicate create 不覆写。
- [ ] **RED：真实 socket。** 扩展现有本地 fixture E2E，lazy create 私有指令→进程/runtime 重建→submit；provider 捕获 system 有完整哨兵而所有通知、get_history/page/status/list/title 无标记/正文；公共 sequence 从1连续。不新增真实远端模型费用。
- [ ] **验证 RED。** Gateway 私有 venv 运行新 test_session_instructions.py，再指定现有 `test_runtime_e2e.py` 的新场景。
- [ ] **GREEN：按 owning plan 实现。** 优先通过 runtime 提供的同步 validate_session_instructions 方法读实际配置；Gateway 参数层调用同一纯预算规则，不复制 4096。不要把私有参数扩展到 shared SessionParams 或公共 RuntimeUpdateModel。
- [ ] **验证 GREEN。** `make test-gateway`；Agent 聚焦应用入口回归。JSON-RPC notification 请求无 id 时仍遵守原协议不返回错误帧。
- [ ] **检查点。** 不改 WebUI/TUI 正常订阅与更新投影。新 UI 输入控件不在范围内，外部程序可用现有 RPC 传值。

## T9：回归、文档及实施证据

**Files:** 功能对应所有测试/文档；`CONTRIBUTING.md` 的 11 manifests 文案；既有 `2026-10-16-framework-business-decoupling/arch.md` 在实现核实后更新12插件与职责。实施完成后可新增本目录 `plan-validation.md` 记录输出；未实现时不创建 arch.md 占位。

- [ ] **兼容测试。** Agent 完整套件、Gateway 完整套件；TUI 聚焦历史分页/sequence 的既有测试（路径由 `rg -n '分批读取|sequence跳号|buffered' apps/tui/test` 定位）增加 instructed-session fixtures，公共序列不能放松。若执行 TUI 全套，按身份对比已知快照失败，不顺手修复。
- [ ] **类型/打包/编译。**
  ```bash
  make test-agent
  make test-gateway
  make typecheck-agent
  make check-package-agent
  apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test apps/gateway/src apps/gateway/test
  git diff --check
  ```
  必须保留退出码，不能 `| tail` 隐藏失败；用后台任务时完成后 Read 输出。
- [ ] **文档核对。** 把计划的接口名/默认值对照实际源码；所有 checked task 都有 RED/GREEN/回归证据。排除真实用户数据/下载代码/实验产物。
- [ ] **失败与回滚。** 功能未提交时只撤销本功能拥有的改动；DB 无 DDL，旧代码公共查询不认识 seq0 的完整性统计，因此已写入私有记录的数据不能直接退回旧 reader。回滚应用版本需同时保留 seq0兼容 reader；不删除真实指令记录以掩盖不兼容。测试/上线使用隔离数据，部署与数据回退另获授权。
- [ ] **最终交接。** 给出通过/失败/未执行清单；实际代码修改与用户工作分开；只有再次明确授权才分层提交，不自动 push。

## 自审覆盖

| 功能设计 | 对应任务 |
|---|---|
| 层次/工作区/基础文案/无截断预算 | T1、T2 |
| 私有 DB 标记/原子性/不可变/公开连续性 | T3 |
| eager/lazy/所有恢复路径与两层防护 Agent 端 | T4 |
| 静态缓存/动态 Event/readiness/deadline/即时覆盖 | T5 |
| required/optional 错误、首个压缩后请求刷新 | T6 |
| 初始化恢复后采样、独立 persona 接口、默认打包 | T7 |
| session.create/Gateway 错误/隐私端到端 | T8 |
| 兼容/类型/打包/编译/证据/回滚 | T9 |

所有接口默认值以 T1/T2 定义为唯一来源。后续任务不得添加 bundle_version、DB 表、回调式运行期 contributor 或扩大 persona/观测交付范围。
