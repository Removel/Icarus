# persona 插件实施计划

| 项目 | 内容 |
|---|---|
| 日期 | 2026-10-20 |
| 状态 | 已实施；测试证据与在线验证限制见文末 |
| 依据 | [arch.md](arch.md) |
| 目标 | 全局人格注入、逐轮攻击防御、5 轮漂移重锚、agent 自演化、rela-mem 只读联动 |
| 前置 | `typesafe-sdk==0.7.2` 可安装；`ICARUS_JEV_API_KEY` 或 `ARGUS_JEV_API_KEY` 已在 `.env` |

> 本文只覆盖 `apps/agent` 与对应文档。不自动创建分支、commit、rebase 或 push。

## 0. 约束

- 只改 `apps/agent`；不改 Gateway、TUI、WebUI、ReActAgent、Plugin Runtime。
- persona 是一级 Plugin，不注册子 Plugin，不导入其它业务插件。
- 人格是全局资产：一个 `persona_id`、一组文件、所有 Session 共享；自演化只对新 Session 生效。
- 不改稳定 System Prompt 的 `base/workspace/external` 层；静态人格只使用 `persona` 层，动态内容只使用 `contributor` 层。
- 配置保持扁平、显式；不引入新的共享包或抽象层。
- 凭证只从环境读取，不写入配置、日志、测试快照或工具结果。
- 每个任务先写行为测试，再实现；先加载 `superpowers:test-driven-development`。

## 1. 目标文件

```text
apps/agent/
├── requirements.txt                                   # + typesafe-sdk==0.7.2
├── pyproject.toml                                     # package-data + personas/*/*.md
├── settings.json                                      # persona 配置；jev/relationship 均开启
├── src/agent_app/
│   ├── config/config_model.py                         # required_plugin_ids + persona
│   ├── resources/default_settings.json                # persona 配置；jev/relationship 均关闭
│   ├── resources/personas/default/persona.md          # 包内只读样例
│   ├── application/session_runtime.py                 # persona 注入 config_model
│   └── plugins/persona/
│       ├── manifest.json
│       ├── factory.py
│       ├── plugin.py
│       ├── config.py
│       ├── store.py
│       ├── judge.py
│       ├── relationship.py
│       ├── prompts.py
│       └── tools.py
├── docs/spec/2026-10-20-persona-plugin/
│   ├── arch.md
│   └── plan.md
└── test/agent_app/plugins/persona/
    ├── test_config.py
    ├── test_factory.py
    ├── test_store.py
    ├── test_judge.py
    ├── test_relationship.py
    ├── test_prompt_contribution.py
    ├── test_tools.py
    ├── test_plugin.py
    └── test_integration.py
```

同时更新 `test/test_packaging.py` 和 `scripts/check-package.sh` 的内置插件数量断言（13 → 14），检查默认人格在 wheel 与隔离安装后可读。

## 2. Task 0：依赖与打包资源

**Files**

- Modify: `requirements.txt`、`pyproject.toml`
- Create: `src/agent_app/resources/personas/default/persona.md`

**Interfaces**

- `requirements.txt` 增加 `typesafe-sdk==0.7.2`。
- `pyproject.toml` 的 `"agent_app.resources"` 增加 `"personas/*/*.md"`。
- 样例人格只含 `soul/voice/boundaries/anti-goals`，总量控制在 800 tokens 内，不包含插件策略文案。

**Tests**

- `test_installed_package_exposes_default_persona_resource`：安装后 `files("agent_app.resources").joinpath("personas/default/persona.md")` 可读。
- `test_typesafe_sdk_importable`：`import typesafe_sdk` 成功，版本落在 `>=0.7.2,<0.8`。

**Steps**

- [x] 加依赖与 package-data。
- [x] 写样例人格。
- [x] 重新安装 `apps/agent`，确认资源与 SDK 可见。
- [x] 运行 `make check-package-agent`（环境允许时）。

## 3. Task 1：插件骨架、配置、Manifest、装配

**Files**

- Create: `manifest.json`、`config.py`、`factory.py`、`plugin.py`（最小骨架）
- Modify: `config_model.py`、`default_settings.json`、`settings.json`、`session_runtime.py`
- Test: `test_config.py`、`test_factory.py`、`test_packaging.py`

**Interfaces**

- Manifest：
  - `plugin_id="persona"`、`plugin_version="1.0.0"`、`tier="core"`、`required_config=[]`。
  - `provided_tools=[]`（Task 6 追加 `persona_evolve` 并更新本测试）。
  - `published_events=["prompt.contribution"]`。
  - `consumed_events=["input.user_input","agent.completed"]`。
  - `state_scopes=[]`；`python_requires=["typesafe-sdk>=0.7.2,<0.8"]`。
- `PersonaConfig`：扁平字段与默认值见 arch.md §9；`extra="forbid"`；`persona_id` 只允许 `[a-z0-9][a-z0-9._-]{0,63}`；时间/阈值字段做范围校验；endpoint 校验 scheme/host/端口。
- `create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger) -> PluginRegistration`：
  - 从 `config["config_model"].icarus_data_dir` 取数据目录；缺失时抛错。
  - 构造 `PersonaStore`、`RelationshipReader`、`JevJudge` 或 `NullJudge`（Task 1 可先用 stub；Task 2/3/4 逐步接入，最终以本节接口为准）。
  - 不发起网络请求。
- `SessionRuntime._with_facilities` 为 `persona` 注入 `config_model`。
- `default_settings.json` 与 `settings.json` 增加 persona 配置块和 `required_plugin_ids` 条目；前者 `jev_enabled=false`、`relationship_enabled=false`，后者均为 `true`。代码默认同样关闭两项外部依赖，避免 `ConfigModel` 直接构造的测试发起网络请求。
- 两处 `prompt` 配置增加 `required_contributors: ["persona"]`，保护静态人格不被总预算当作可选来源丢弃。
- `plugin.py` 初始只实现 `start/stop`、`on_session_start`（Task 1 为 stub，Task 2/4 接入 store 与关系读取）、`static_prompt_sections`（Task 1 返回空 tuple）、带 `@handles(UserInputEvent, AgentCompletedEvent)` 的最小处理器（输入事件立即发布空 completed 贡献，避免 prompt 等待 5s）、空 `build_tools`。

**Tests**

- `test_config_rejects_unknown_fields_and_invalid_persona_id`。
- `test_config_validates_threshold_ranges_and_endpoint`。
- `test_factory_constructs_without_network_and_defers_file_load`。
- `test_manifest_declares_exact_events_and_no_tools_until_task6`。
- `test_minimal_input_handler_publishes_empty_completed_contribution`。
- `test_default_required_plugins_include_persona`。
- `test_persona_is_listed_in_prompt_required_contributors`。
- `test_packaging_counts_fourteen_builtin_plugins`。

**Steps**

- [x] 先写配置/清单/工厂测试并确认 RED。
- [x] 实现骨架与装配，更新 settings 与 required 列表。
- [x] 运行 persona 单测与 `test/agent_app/application/test_session_runtime.py`。
- [x] 确认所有 SessionRuntime 测试在无 jev key、无 rela-mem 的环境下仍能启动。

## 4. Task 2：Store（加载、预算、overlay、并发写）

**Files**

- Create: `store.py`
- Test: `test_store.py`

**Interfaces**

```python
class PersonaFileMissing(FileNotFoundError): ...

@dataclass(frozen=True)
class PersonaDocument:
    persona_id: str
    name: str
    identity_text: str          # 已按预算裁剪的 soul/boundaries/voice/examples
    drift_reference_text: str   # soul+voice 的有界摘要，供漂移判定
    invariants_text: str        # soul+boundaries/anti-goals 的有界摘要，供演化校验
    evolved_text: str

@dataclass(frozen=True)
class EvolvedEntry:
    id: str
    section: Literal["voice", "relationship"]
    note: str
    reason: str
    created_at: str
    session_id: str

class PersonaStore:
    def __init__(self, data_dir: Path, persona_id: str, config: PersonaConfig,
                 package_root: Traversable | None = None) -> None: ...
    def load(self) -> PersonaDocument: ...                 # 文件缺失抛 PersonaFileMissing
    async def append(self, entry: EvolvedEntry) -> str:    # 锁内读改写，返回 id
    def read_entries(self) -> tuple[EvolvedEntry, ...]: ...
```

- 解析 `persona.md`：YAML frontmatter + 二级标题分节；未知分节忽略并告警；重复分节拼接；无 frontmatter 时仍解析正文。
- 预算：`conservative_token_count`；soul 超 400 硬截断；identity 按 soul > boundaries/anti-goals > voice > examples 整节取舍；总 1500。
- overlay：`evolved.yaml`；`version/updated_at/entries`；读取失败告警并按空处理；写入时创建目录、临时文件 + `os.replace`。
- 并发：模块级 `{resolved_overlay_path: asyncio.Lock}` 注册表；临界区包含读取、追加、淘汰、写盘；`asyncio.Lock` 的 FIFO 等待满足互斥需求。
- 淘汰：超过 `evolution_max_entries` 或 `evolution_max_tokens` 时按 `created_at` 丢弃最旧条目。

**Tests**

- `test_load_parses_frontmatter_and_sections`。
- `test_load_ignores_unknown_sections_with_warning`。
- `test_load_without_frontmatter_still_parses_body`。
- `test_load_corrupt_frontmatter_warns_and_keeps_valid_sections`。
- `test_data_dir_file_wins_over_packaged_fallback`。
- `test_missing_everywhere_raises_persona_file_missing`。
- `test_soul_over_budget_is_truncated`。
- `test_identity_sections_dropped_in_priority_order`。
- `test_append_merges_entries_and_updates_updated_at`。
- `test_append_evicts_oldest_when_entry_or_token_cap_reached`。
- `test_corrupt_overlay_is_treated_as_empty_with_warning`。
- `test_concurrent_appends_keep_all_entries_within_cap`：`asyncio.gather` 两次 append，断言不丢、不超限。
- `test_write_uses_atomic_replace`：写入过程中目标文件始终是完整 YAML（可通过 monkeypatch 观察临时文件与替换调用）。

## 5. Task 3：Judge（jev 客户端与结构化判定）

**Files**

- Create: `judge.py`
- Test: `test_judge.py`

**Interfaces**

```python
class JudgeUnavailable(RuntimeError): ...

class Judge(Protocol):
    async def attack_confidence(self, text: str) -> float: ...
    async def drift_confidence(self, recent_assistant: Sequence[str],
                               expected_voice: str) -> float: ...
    async def evolution_verdict(self, *, note: str, reason: str, section: str,
                                recent: Sequence[tuple[str, str]],
                                invariants: str) -> EvolutionVerdict: ...
```

- `EvolutionVerdict(grounded: float, in_scope: float, worth_persisting: float)`。
- `JevJudge`：`AsyncTypeSafeClient(api_key=..., base_url=..., model=..., timeout=..., retry=RetryPolicy(max_retries=0))`；每次调用外层 `asyncio.wait_for(..., timeout)`。
- `NullJudge`：`jev_enabled=false` 或缺少 key 时注入；所有 `ask_*` 抛 `JudgeUnavailable`。插件用 `config.jev_enabled` 区分 `judge_disabled`（显式关闭）与 `judge_unavailable`（启用但不可用）。
- 攻击：`Score` 三档（safe/suspicious/attack），`p = score / 2`。
- 漂移：`Score` 三档（consistent/mild/clear），`p = score / 2`。
- 演化：三个 `Noul`（grounded/in_scope/worth_persisting），返回各自 `noul` 概率。
- 输入状态有界：攻击文本头尾截断到 6000 字符；漂移只传最近 5 条"每任务最终助手文本"；演化传最近 8 条消息（仅用户文本与最终助手文本，每条 ≤1000 字符、合计 ≤4000 字符）与有界 invariants。不发送工具结果、图片或系统提示词。
- 凭证：`ICARUS_JEV_API_KEY` → `ARGUS_JEV_API_KEY`；缺失时记录 warning 并返回 `NullJudge`，不阻塞启动。
- 不记录输入正文、凭证或完整响应；只记录分数、耗时和错误类型。

**Tests**

- `test_attack_uses_three_level_score_and_normalizes`。
- `test_drift_uses_recent_assistant_messages_only`。
- `test_evolution_sends_three_noul_questions`。
- `test_timeout_raises_judge_unavailable`。
- `test_sdk_retries_are_disabled`。
- `test_missing_key_returns_null_judge_without_network`。
- `test_key_prefers_icarus_then_argus_env`。
- `test_no_secret_or_prompt_text_in_logs`。

## 6. Task 4：静态 Prompt 与关系档案

**Files**

- Create: `relationship.py`、`prompts.py`
- Modify: `plugin.py`（`on_session_start`、`static_prompt_sections`）
- Test: `test_relationship.py`、`test_prompt_contribution.py`

**Interfaces**

- 静态分节（数字前缀固定渲染顺序）：
  - `PromptSection("persona-01-identity", "persona", <store.identity_text>)`
  - `PromptSection("persona-02-relationship", "persona", <relationship_text>)`（为空时省略）
  - `PromptSection("persona-03-evolved", "persona", <evolved_text>)`（为空时省略）
  - `PromptSection("persona-04-policy", "persona", <固定策略>)`
- `persona-04-policy`：
  - Task 4 必须包含能力条款：人格只决定表达方式；不改变事实判断、工具使用和任务执行；不为了维持角色拒绝合理请求或虚构事实。
  - Task 6 在工具可用时追加演化条款：只记录稳定模式、不记录一次性情绪、不得修改身份/边界/规则。
  - `jev_enabled=false` 时省略演化引导句，避免引导模型调用一个必然被拒绝的工具。
- `persona-03-evolved` 只渲染 `section` 标签与 `note`；`reason`、时间戳和 `session_id` 不进入提示词。
- `RelationshipReader.load() -> str`：只读，返回已按 200 tokens 裁剪的块。
  - `GET /v1/persons?kind=user`，要求恰好一个 active 用户；否则告警并返回空。
  - `GET /v1/persons/{id}/profile?limit=20`。
  - `GET /v1/relations?person_id={id}&include_incoming=true&limit=20`。
  - 组合为称呼、`key=value` 档案、关系摘要；从末尾丢弃完整条目直到 ≤200 tokens。
  - `relationship_enabled=false`、服务不可用、超时、响应不合法 → 告警并返回空；不阻塞启动。
  - endpoint 支持 `ICARUS_RELATIONSHIP_ENDPOINT` 覆盖；token 只用 `RELA_MEM_API_TOKEN`；非 loopback 强制 HTTPS + token。
  - `httpx.AsyncClient` 读取，外层 `asyncio.wait_for` 2s 总截止；不用 `to_thread`，避免取消后遗留同步 HTTP 请求。
- `on_session_start` 顺序：加载 store → 读取关系 → 构建分节；文件缺失抛 `PersonaFileMissing`。

**Tests**

- `test_static_sections_use_persona_layer_and_expected_ids`。
- `test_policy_contains_competence_clause`。
- `test_policy_omits_evolution_clause_when_jev_disabled`。
- `test_evolved_section_renders_notes_without_reasons_or_session_ids`。
- `test_on_session_start_raises_when_persona_missing_everywhere`。
- `test_relationship_disabled_makes_no_request`。
- `test_relationship_selects_single_user_and_bounds_to_200_tokens`。
- `test_relationship_zero_or_multiple_users_returns_empty_with_warning`。
- `test_relationship_offline_returns_empty_and_session_starts`。
- `test_relationship_non_loopback_requires_https_and_token`。
- `test_static_sections_update_next_session_but_not_current`：写入 overlay 后当前实例内容不变，新 store 读到新条目。

## 7. Task 5：输入防御与漂移重锚

**Files**

- Modify: `plugin.py`（`on_event`、轮次与窗口状态、动态贡献）
- Test: `test_plugin.py`、`test_prompt_contribution.py`

**Interfaces**

- `@handles(UserInputEvent, AgentCompletedEvent)`。
- `UserInputEvent`：
  1. 递增 `turn_count`，把用户文本加入最近消息窗口。
  2. 若 `jev_enabled`：攻击检测总是执行；`turn_count % drift_every_turns == 0` 且助手消息 ≥3 时并行执行漂移检测。
  3. 按阈值生成 `persona-defense` / `persona-reanchor` 分节，动态总量 ≤200 tokens；防御优先。
  4. 始终发布 `PromptContributionEvent(task_id=..., status="completed", sections=...)`；异常时发布空 completed。
- `AgentCompletedEvent`：每个已完成任务只追加一条最终助手文本（拼接文本 part，忽略图片和纯工具调用消息）；不使用 `agent.message_completed` 的中间片段，避免一次任务填满窗口或重复计数。窗口上限 5 条 / 4000 字符。
- 维护两个窗口：漂移窗口（最近 5 条每任务最终助手文本）与演化对话窗口（最近 8 条用户+最终助手文本，合计 ≤4000 字符）。
- 漂移状态只使用每任务最终助手文本；`expected_voice` 来自 soul+voice 的有界摘要。
- 攻击防御模板与重锚模板来自 `prompts.py`，不读取人格文件。
- 所有判定失败 fail-open；`NullJudge` 时立即发布空贡献。

**Tests**

- `test_minimal_input_handler_publishes_empty_completed_contribution`（Task 1 已有，扩展为完整判定测试）。
- `test_normal_input_publishes_empty_completed_contribution`。
- `test_mid_attack_injects_defense` / `test_high_attack_injects_strong_defense` / `test_low_attack_injects_nothing`。
- `test_threshold_boundaries_are_inclusive`。
- `test_judge_timeout_still_publishes_completed`。
- `test_drift_runs_every_five_turns_and_uses_completed_turn_final_text_only`。
- `test_partial_message_events_do_not_fill_drift_window`。
- `test_drift_below_threshold_injects_nothing`。
- `test_drift_hit_injects_one_reanchor_not_every_turn`。
- `test_attack_and_drift_run_concurrently`：fake judge 用事件/计数器证明两路并行。
- `test_dynamic_budget_drops_reanchor_first`。
- `test_unexpected_handler_error_publishes_empty_completed`。
- `test_prompt_plugin_does_not_wait_extra_timeout`：用真实 PromptPlugin 与空贡献，断言快速 finalize。

## 8. Task 6：`persona_evolve` 工具

**Files**

- Create: `tools.py`
- Modify: `plugin.py`（`build_tools`）、`store.py`（append 已有）
- Test: `test_tools.py`

**Interfaces**

```text
persona_evolve(
  section: "voice" | "relationship",
  note: str,     # 1..300
  reason: str,   # 1..500
)
```

- `PersonaEvolveTool(BaseTool)` 重写 `async def ainvoke(...)`；`can_run_parallel=False`。
- 参数校验：未知字段拒绝、枚举、长度、blank。
- 限流：每个 Session 每 10 轮最多接受 1 次；被拒绝不计数。
- 校验：`judge.evolution_verdict` 三问，每个 `noul >= evolution_min_probability`；任一不过返回 `{"status":"rejected","reason":...}`。
- `JudgeUnavailable`（已启用但超时/解析失败/缺 key）→ `{"status":"rejected","reason":"judge_unavailable"}`（fail-closed）。
- `NullJudge`（`jev_enabled=false`）→ `{"status":"rejected","reason":"judge_disabled"}`；工具仍保持注册，因为 manifest 的 `provided_tools` 必须与 `build_tools()` 一致。
- 通过后 `store.append`；返回 `{"status":"accepted","id":...,"section":...}`。
- 工具结果不包含完整 overlay、不包含 jev 原始响应。
- `build_tools()` 返回单个工具，与 manifest 一致；同时把 manifest 的 `provided_tools` 更新为 `["persona_evolve"]`，并更新 Task 1 的 manifest 测试。
- 工具可用时，在 `persona-04-policy` 追加演化引导句；`jev_enabled=false` 时保持省略。

**Tests**

- `test_tool_schema_rejects_unknown_fields_and_bad_section`。
- `test_tool_rejects_missing_or_blank_note_and_reason`。
- `test_accepted_note_is_written_with_reason_and_session`。
- `test_each_failed_question_rejects_with_specific_reason`。
- `test_judge_unavailable_rejects_without_writing`。
- `test_jev_disabled_tool_returns_judge_disabled`。
- `test_rate_limit_allows_one_accept_per_ten_turns`。
- `test_rejected_call_does_not_consume_rate_limit`。
- `test_entry_and_token_caps_evict_oldest`。
- `test_tool_is_not_parallel`。
- `test_manifest_tools_match_build_tools`。
- `test_policy_adds_evolution_clause_when_judge_available`。

## 9. Task 7：集成、打包与真实冒烟

**Files**

- Test: `test_integration.py`、`test/test_packaging.py`
- Optional: `apps/agent/scripts/smoke_persona.py`

**Steps**

- [x] 构造临时 `ICARUS_DATA_DIR`、样例人格与 fake judge，启动真实 `SessionRuntime`。
- [x] 断言 System Prompt 含 `persona/persona/persona-01-identity` 等分节。
- [x] 用户输入触发中档攻击时，用户提示词含 `persona/persona/persona-defense`。
- [x] 第 5 轮漂移命中时，只出现一次 `persona-reanchor`。
- [x] 调用 `persona_evolve` 后，新 Session 的 System Prompt 含新条目；旧 Session 不含。
- [x] 删除数据目录文件、只保留包内样例时，required 插件仍能启动。
- [x] 更新 `test_packaging.py` 的插件数量断言为 14。
- [x] 可选真实 jev 冒烟：正常输入、攻击输入、漂移样本、演化校验各一次；只记录分数与判定，不记录输入正文或 key。
- [x] 运行 `make test-agent`。
- [x] 如 Gateway 的 prompt e2e 断言受静态人格影响，运行 `make test-gateway` 并只调整测试期望，不改 Gateway 产品代码。
- [x] 运行 `make typecheck-agent`。
- [x] 运行 `python -m compileall -q apps/agent/src`。
- [x] 运行 `git diff --check`。

## 10. 验证顺序

1. `apps/agent/test/agent_app/plugins/persona/` 最小测试文件；
2. `apps/agent/test/agent_app/plugins/persona/` 全目录；
3. `apps/agent/test/agent_app/application/test_session_runtime.py`；
4. `make test-agent`；
5. `make typecheck-agent`、`compileall`、`git diff --check`。

## 11. 验收

- 全局人格文件被加载并进入 `persona` 静态层；Session 运行中不刷新。
- 正常输入不注入动态内容；中/高档攻击分别注入防御；jev 超时 fail-open。
- 每 5 轮检测漂移，只看助手输出；命中只重锚一次。
- `persona_evolve` 只能写 `voice`/`relationship`；三问任一不过即拒；jev 失败 fail-closed；限流与上限生效。
- 并发演化写入不丢条目、不越上限。
- 文件缺失启动失败；文件损坏部分加载；rela-mem 离线不阻塞；mem0 不受影响。
- 无 jev key 时插件仍可为所有既有测试启动，仅禁用判定与演化。
- `make test-agent`、`make typecheck-agent`、`compileall`、`git diff --check` 通过。

## 12. 实施决策记录

1. `evolved.md` 以 `evolved.yaml` 落地：条目需要结构化字段，YAML 仍可人工编辑，避免自造 Markdown 解析器。
2. 包内提供只读 `default/persona.md` 样例：required 插件不能让全新安装或 CI 因缺少数据目录文件而无法启动；数据目录文件始终优先。
3. 包内默认关闭 jev 与 rela-mem 联动，仓库 `settings.json` 开启：既避免 CI 和全新安装访问外部服务，也让本仓库的运行时获得完整能力。显式开启但缺少 jev key 时告警并禁用判定，而不是启动失败；`jev_enabled=false` 时 `persona_evolve` 仍注册，但一律返回 `judge_disabled`。
4. persona 加入 `prompt.required_contributors`：静态人格获得预算保护；正常判定失败发布空 completed，不会因为 jev 故障使整轮失败。插件本身崩溃或消息未在 prompt 截止前送达仍可使该轮失败，不宣称消除了全部 required-source 失败路径。optional 模式必须同时从两处 required 列表移除。
5. 演化工具重写 `ainvoke` 并在事件循环内完成校验与写盘；同步 `invoke` 复用 RuntimeLoopRunner。写锁使用模块级 `asyncio.Lock` 注册表，满足 FIFO 与跨 Session 互斥；全局写锁之外评估，单实例调用锁保护同 Session 冷却。
6. V1 只增不改不删演化条目；人工可直接编辑或删除 `evolved.yaml`。
7. 校验上下文合并保留的历史与 Tool 执行接口已有的 `task_messages`，让当前轮证据参与 grounded 判断；排除工具输出、图片和用户提示词的插件上下文。
8. 动态预算按完整 PromptSection 渲染（包含标题）计费；防御优先，避免正文满足预算但实际注入超限。

## 13. 2026-10-20 实施与验证记录

- 新增插件、配置、默认资源、manifest、typed Jev 判定、只读 rela-mem 快照和可见 `persona_evolve`；未改 ReActAgent、通用 Runtime、Gateway/TUI/WebUI 产品代码。
- 基线 `make test-agent` 为 983 passed；实现后全量为 **1085 passed**，其中 persona 目录 **100 passed**。
- `make test-gateway`：**34 passed**，保留 1 条现有 Starlette/AnyIO deprecated alias warning。
- `make typecheck-agent`：259 个源文件无问题；compileall 与 `git diff --check` 通过。
- `make check-package-agent`：标准隔离构建、安装 wheel 成功；验证 14 个 lazy manifests、默认 settings、默认人格 Markdown 和隔离导入。
- 真实 SessionRuntime 集成验证：静态人格冻结、逐轮动态防御、第 5 轮一次性重锚、演化工具、旧 Session 不刷新、新 Session 看到全局 overlay、optional 人格缺失不阻塞其它插件。
- **真实主模型冒烟通过**：临时 data dir 的独立样例人格要求特定开头，模型输出命中该风格标记（42 字符）；不读取或写入生产 persona、overlay、记忆或人物档案。
- **Jev 在线验证未通过**：默认 1.2s 下四次 synthetic 判定均超时；额外一次 4s 连通性诊断在约 2.78s 返回 `TypeSafePermissionDeniedError`，未取得有效分数。故只确认 HTTP 边界 fixture 下的结构化判定和故障策略，不能声称当前 key/服务的在线判定可用；需上游权限恢复后再次冒烟。
- 失败过程如实保留：首轮 Agent 全量 1025 passed/1 failed（新配置缺 `PluginConfig` 别名），补别名后通过；首轮 Gateway 31 passed/3 failed（其虚拟环境缺新增的 Agent SDK 依赖），同步 SDK 和 transitive tenacity 后通过；类型检查的注解问题已修复。
- 实现与验证阶段未创建分支、worktree、commit、merge、rebase 或 push；提交与推送另需用户授权。`origin/feature` 开始时已核验与 HEAD `07ae95c` 相同。

- 独立整体审查复现并修复：Unicode 理由导致 overlay 超出自身读取上限、取消任务判定积压、助手工具中间文本挤掉真实依据、字面 user_request 标签截断、零阈值与失败分数混用、演化校验前/后取消仍写入、rela-mem 并行读取失败后遗留请求。全部先跑失败回归再修复，persona 测试目前 **108 passed**；审查无遗留 Critical/Important，原 Minor 的实际行为问题也已在本轮修复，无待处理小项。
- 审查后的首轮全量：1091 passed / 1 failed，失败为未改动的 `skill/test_job_manager.py::test_submit_returns_queued_then_background_job_succeeds_and_notifies`，任务 succeeded 但通知 events 尚为空；不修改 Skill 无关代码。
- 最终修复树：**persona 108 passed，Agent 全量 1093 passed（49.89s）**；该 Skill 测试单独复跑通过。Gateway 再次 **34 passed**（1 条已有 warning）；类型检查仍 259 files clean，compileall、diff check 通过；最终 wheel 在结构化 HTTP 清理修复后重新构建/安装验证通过。

### 使用与自定义

- 全局文件：`$ICARUS_DATA_DIR/personas/default/persona.md`。不存在时读取包内 `src/agent_app/resources/personas/default/persona.md`，可复制该样例到数据目录后自行编辑。
- 演化记录：`$ICARUS_DATA_DIR/personas/default/evolved.yaml`，只在第一次接受 `persona_evolve` 后创建；删除它可以清除演化观察，不影响作者维护的基础人格。
- 修改任何人格文件后，新 Session 加载最新内容；已经运行的 Session 不热刷新。
- 仓库 `apps/agent/settings.json` 已开启 jev 和关系联动；离线包默认关闭，可在 `runtime.plugin_config.persona` 显式开启。
- 关闭强制 persona 时，同时从 `runtime.required_plugin_ids` 与 `runtime.plugin_config.prompt.required_contributors` 删除 `persona`；这使加载故障可降级，不会自动禁止 manifest 发现。
