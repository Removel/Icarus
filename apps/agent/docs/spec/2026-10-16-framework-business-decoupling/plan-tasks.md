# Agent 框架与业务解耦 Implementation Plan

> **执行状态：** T01–T26主体已实施，验收结果和明确偏差见[plan-validation.md](plan-validation.md)。下列接口为原计划；现状以arch.md与源码为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Agent 拆成可独立导入的 `icarus` 框架与 `agent_app` 应用，在保留现有存储语义和外部协议的条件下完成插件契约、应用职责与 Hook 的重构。

**Architecture:** `agent_app → icarus.runtime → icarus.core`，框架不反向 import 应用。先闭合打包/资源/类型身份并连续迁移，再修注入、能力调用和应用拆分，最后实现 Hook 改值与独立记录器。Blackboard 的 Message 状态与 SessionStore 的公共 RuntimeUpdate 历史继续各自承担原职责。

**Tech Stack:** Python >=3.11、现有 asyncio/dataclasses/pydantic/packaging、SQLAlchemy 2.x Async + aiosqlite、pytest/native assert；新增 setuptools editable 打包、mypy。厂商 SDK、FastAPI 和客户端技术栈不升级。

**Spec:** [当前实现](arch.md)、[目标与 D1–D42](plan-target-architecture.md)、[阶段与依赖](plan.md)。实施者必须一起阅读，不能只按文件移动表操作。

## Global Constraints

- 本文是已执行任务清单；实现与验收证据见 [plan-validation.md](plan-validation.md)。复选框按交付切片完成，不把未执行的历史示例命令冒充已运行。
- 已确认：保留现有存储语义；Hook 只改值，Event 承担业务流程，记录与日志脱离 Hook。
- app-owned 依赖/环境/构建元数据留 `apps/agent`；不引入根共享 Python distribution，不向索引发布，不合并 Agent/Gateway venv。
- 框架不 import `agent_app` / `apps.agent.src` / 厂商 SDK；core 不 import runtime；运行时不得 import 领域插件。
- ReActAgent 保持无状态，不依赖 Plugin Runtime、Blackboard、UI、TTS 或领域插件。
- 不实现嵌套子插件、热插拔、第二个总线、隐式重试、通用 object 注册表或没有真实消费者的能力。
- 动态上下文仍放当前 User Prompt，不能用本次 Hook 改造把它搬入稳定 System Prompt。
- 保留 Message/RuntimeUpdate/状态文件格式、SQLite 表与原子取号、软删除、资产相对路径、checkpoint、失败锁存、thinking 隔离与压缩语义；不新增统一 ConversationStore。
- 保留 Gateway wire、异常类身份、返回 dataclass、同步/异步形态、订阅关闭/溢出；现有公开入口均保留，分页另加兼容入口。
- 测试跟随对应应用且镜像目标源码。验证顺序：最小测试 → 受影响应用套件 → compileall → diff check。不修无关失败，不用 skip 掩盖既有失败。
- 每项列出的新名称、默认值、tier/ID、接口细节为**计划作者提案**；只在整份计划获批后实施，不能称用户已经逐个确认。
- **禁止自动建分支、commit、amend、rebase、push。** 每任务收尾仅划定逻辑提交边界；以后用户另行授权 commit 时再执行，并带仓库规定的 attribution。

## Review Focus

1. handler 内等待能力响应、超时/取消/停机与伪造响应：不能自锁、误配对或遗留 Future；T12 用真实串行 worker 固定。
2. 安装后非仓库 cwd、缺资源、双导入身份与 Gateway 独立 venv：不能只在 checkout 下假绿；T02–T04、T25 验证。
3. 插件失败/降级与 Region freeze：不能留下已禁用 Memory 的 required Region，使普通输入永久等待；T10、T13 验证。
4. 改值 Hook 修改参数影响并行性、扩大输出、原地修改后抛错：不能绕过工具策略/预算或污染回退值；T18–T20 验证。
5. 分页跨越旧 delta 合并、进程重启与状态文件恢复：游标不能回退，UI 历史不能代替模型消息或意外暴露内部内容；T01、T15、T23、T25 验证。

---

## 0. 执行约定与完整文件地图

### 0.1 路径和命令

下文所有路径相对仓库根。文件表中的 `A/` **精确展开为** `apps/agent/src/`，`AT/` 展开为 `apps/agent/test/`；
`G/` 为 `apps/gateway/src/`，`GT/` 为 `apps/gateway/test/`。这些缩写不是新的目录。

所有 pytest 命令从仓库根运行，使用拥有该测试的解释器：

```bash
apps/agent/.venv/bin/python -m pytest <AT中的具体路径> -q
apps/gateway/.venv/bin/python -m pytest <GT中的具体路径> -q
```

T02 前先使用原有环境获取基线；没有环境则报告，不把安装当成本轮已授权动作。
任务实施时，最小测试先 RED（针对新行为失败）再 GREEN；纯移动以旧测试行为基线不变 + 新导入守卫先失败来验证，
不要求故意破坏原业务测试。下文“Expected PASS”是未来验收要求，不是本轮执行结果。

### 0.2 源码迁移矩阵

| 现路径（A/下） | canonical 目标（A/下） | 执行任务 / 说明 |
|---|---|---|
| agent_orchestration/plugin_runtime/base_plugin.py | icarus/core/plugin.py | T03移动；T11改声明分发 |
| plugin_runtime/manifest.py、registration.py、types.py | icarus/core/同名文件 | T03；后续T09/10调整契约 |
| plugin_runtime/graph_builder.py | icarus/runtime/assembly.py | T03；类名改PluginAssembly，T10能力解析 |
| plugin_runtime/plugin_registry.py | icarus/runtime/registry.py | T03，PluginRegistry行为不变 |
| plugin_runtime/plugin_manager.py | icarus/runtime/manager.py | T03，保留PluginManager类名 |
| plugin_runtime/plugin_runtime.py | icarus/runtime/worker.py | T03，保留PluginRuntime类名 |
| plugin_runtime/{discovery,resolver,event_bus,host,state_coordinator,diagnostics}.py | icarus/runtime/同名文件 | T03；host默认不再指向业务包 |
| plugin_runtime/wrappers/* | icarus/runtime/wrappers/* | T03；T21/22改记录/挂点 |
| agent_orchestration/events/* | icarus/core/events/* | T03 |
| agent_orchestration/hooks/* | icarus/core/hooks/* | T03；T18–22改行为 |
| agent_orchestration/tools/*（不含builtin） | icarus/core/tools/* | T03；新增settings.py承接通用执行设置 |
| agent_orchestration/tools/builtin/* | agent_app/tools/builtin/* | T04，Windows helper一起迁 |
| agent_orchestration/capability/* | icarus/core/agents/* | T03；不建同名capability.py |
| agent_orchestration/run_control/* | icarus/core/run_control/* | T03；T08明确每会话拥有者 |
| model_provider/types.py | icarus/core/models/messages.py | T03，含ImagePart/错误/Definition/Response/Chunk全部类型 |
| model_provider/base_llm.py | icarus/core/llm.py | T03；T05增加注入式工厂 |
| agent_orchestration/agent_factory.py | agent_app/assembly.py → icarus/core/assembly.py | T04先留业务，T05去配置/SDK/内置工具后下沉 |
| model_provider/llm_factory.py | agent_app/model_provider/factory.py | T04；T05改ModelProviderFactory，协议分支留此 |
| model_provider/impl/* | agent_app/model_provider/impl/* | T04，不改变厂商协议 |
| model_config/* | agent_app/config/* | T04；ToolExecutionSettings提前移core并同对象re-export |
| application/* | agent_app/application/* | T04纯移；T15–17再拆职责 |
| runtime_update.py | icarus/core/runtime_update.py | T03，产品事件字面量不变 |
| plugins/*（含persistence暂时全部） | agent_app/plugins/* | T04；父__init__为空，不全量re-export |
| plugins/persistence/session_identity.py | icarus/core/identity.py | T07 |
| plugins/persistence/path_resolver.py | icarus/runtime/paths.py | T07 |
| plugins/persistence/runtime.py | icarus/runtime/assets.py（仅设施） | T07：不把后端实现一起拖进框架 |
| plugins/persistence/redactor.py | agent_app/utils/redactor.py | T07：含规则但无运行态，非能力 |
| application/resource_ref.py | icarus/core/resource_ref.py | T07，异常同对象，签名不变 |
| application/runtime_status.py | core/session.py 的通用Snapshot + app/models/status.py | T08/T15；外部状态DTO仍app |
| application/session_entities.py | agent_app/application/repositories/session_entities.py | T15；ORM不是业务Model目录 |
| application/session_store.py | agent_app/application/repositories/session_store.py | T15；通用engine生命周期抽runtime/storage.py |
| plugins/user_input/events.py + InputAccepted | icarus/core/contracts/input.py | T11/T13；私有PendingInput留插件 |
| plugins/blackboard/events.py + Region公开值类型 | icarus/core/contracts/context.py | T11/T13；RegionRegistry/Store实现留插件 |
| plugins/process/events.py的跨插件事件 | icarus/core/contracts/process.py | T11；不迁ProcessManager实现 |

未列出的插件内部文件都**整体保留在所属插件**。不借共享类型迁移把所有 DTO/HTTP backend/工具 schema 都放进core。
新增 `core/capabilities/{base,catalog,conversation,requester}.py`、`core/ports.py`、`runtime/{capability_registry,session,storage,fanout,recording}.py` 由对应任务定义。

### 0.3 测试移动规则（全部现有测试必须有去处）

- `AT/agent_orchestration/capability/*` → `AT/icarus/core/agents/*`。
- events、hooks、run_control、tools（除builtin）→ `AT/icarus/core/` 对应目录。
- plugin_runtime 的 base_plugin/manifest/types 测试→`AT/icarus/core/`；其余测试、support、host_fixtures、empty_plugins、conftest → `AT/icarus/runtime/`，同步所有 fixture import/反射字符串。
- `test_agent_factory.py` → `AT/icarus/core/test_assembly.py`（T05前可暂放 `AT/agent_app/test_assembly.py`）。
- 所有 plugins 测试→`AT/agent_app/plugins/<plugin>/`；persistence 设施测试再随T07移到 core/runtime；后端测试仍插件。
- builtin 测试→`AT/agent_app/tools/builtin/`；model_config→`AT/agent_app/config/`；provider impl/factory→`AT/agent_app/model_provider/`。
- application 测试先整体→`AT/agent_app/application/`，再随T15–17拆分镜像目录；保留公开入口集成测试 `test_agent_runtime.py`。
- 测试helper只供测试使用，不打进发行包；不把 `apps.agent.test.*` 当生产依赖。

### 0.4 外部与工程文件（不遗漏）

修改 `apps/agent/{pyproject.toml,requirements-dev.txt,scripts/install.sh,scripts/test.sh}`、三个 `scripts/smoke_*.py`；
`apps/gateway/scripts/{install,test}.sh`、`src/{app,connection}.py`、`src/protocol/{methods,models}.py` 与对应测试；
`.github/workflows/{apps,backend-contracts}.yml`；`packages/gateway_protocol/models.py` 的 TYPE_CHECKING 路径；
`apps/tui/test/test_cli.py` 的禁止导入清单。TUI启动器与Gateway start.sh的仓库根bootstrap不删除。
Makefile现有转调目标保留，只增加确有调用者的typecheck/check-package入口。README、CONTRIBUTING与AGENTS修订在T26。

## 0.5 关键测试的断言契约

这些是测试步骤必须落实的断言，不是可直接运行的完整测试文件；依赖fixture由所属任务在同一镜像测试目录定义。
fixture只包装本文已定义Interface，不增加生产抽象。旧测试能够直接提供相同断言时应迁移复用。

| 所属 | 测试名 | 必须出现的断言（native assert / pytest.raises） |
|---|---|---|
| T01 | test_model_and_public_history_remain_distinct | `assert records[0].payload["text"] == "visible prompt"`；`assert "model prompt" in persisted_blackboard`；`assert known_thinking_text not in serialized_model_messages`；工具call/result的id和原顺序相等 |
| T02 | test_wheel_contains_declared_resources | `assert len(manifest_paths) == 11`（T04后）；`assert "agent_app/resources/default_settings.json" in wheel_paths`；`assert not any(".env" in p.split("/") for p in wheel_paths)` |
| T03 | test_moved_event_identity_is_singleton | `assert facade.Event is Event`（仅存在临时shim时）；`assert Event.__module__ == "icarus.core.events.base_event"`；旧路径删后以`find_spec`不可达代替shim断言 |
| T04 | test_all_builtin_manifests_discover_without_eager_plugin_import | `assert {p.manifest.plugin_id for p in result.plugins} == EXPECTED_11_PLUGIN_IDS`；`assert not any(n.endswith(".factory") for n in newly_imported)` |
| T05 | test_registry_is_only_tool_source | `assert registry.names() == names_before_assembly`；`assert assembly.get_agent("worker") is assembly.get_agent("worker")` |
| T06 | test_roles_are_looked_up_by_name | `assert config.get_model("worker").name == "worker"`；重复name用`pytest.raises(pydantic.ValidationError)`；未知角色在加载时失败 |
| T07 | test_identity_and_paths_are_unchanged | `assert identity.workspace_key == sha256(str(workspace.resolve()).encode()).hexdigest()[:16]`；`assert imported.source == imported_again.source`；逃逸用`pytest.raises(ImageAssetError)` |
| T08 | test_task_channels_are_session_owned | `assert host.task_channels is agent.task_channels is user_input.task_channels`；不同session的Registry `is not` |
| T09 | test_capability_events_are_not_business_declarations | manifest含能力请求ID用`pytest.raises(pydantic.ValidationError)`；`assert manifest.schema_version == 2`；`assert "plugin_id" not in requirement.model_dump()` |
| T10 | test_replacement_provider_restores_state | provider用测试ID`alternate-store`；`assert restored_state == saved_state`；`assert registry.provider_for("state.store", ">=1,<2") == "alternate-store"` |
| T11 | test_handler_manifest_agreement | 缺少/多余handler、未声明工具用`pytest.raises(PluginContractError)`；为此定义core/plugin.py的`PluginContractError(ValueError)`并门面导出 |
| T12 | test_handler_can_await_response_with_real_serial_workers | `assert response.status is CapabilityStatus.OK`；`assert requester.pending_count == 0`；task在`asyncio.wait_for(..., 1)`内完成，使用Event同步不sleep猜时间 |
| T13 | test_disabled_memory_does_not_leave_required_region | `assert "memory" not in {d.region for d in registry.definitions()}`；无Memory降级图普通submit仍得到正常终态；session Region恢复前后 `assert restored_snapshot == saved_snapshot` |
| T14 | test_conversation_failure_creates_no_job | `assert jobs == ()`；`assert observed_source == "skill"`；成功证据`assert evidence == (*committed, *current_task_messages)` |
| T15 | test_concurrent_sequence_is_atomic | `assert sorted(u.sequence for u in saved) == list(range(1, 11))`；`assert cursor == 10`；注入事务失败后cursor和rows同时不变 |
| T16 | test_cancelled_waiter_keeps_shared_load | `assert factory.call_count == 1`；`assert second_accepted.task_id`；`assert status.lifecycle == "loading"`且查询不等待写锁 |
| T17 | test_persistence_failure_latches_session | `assert entry.persistence_failed is True`；`assert entry.lifecycle == "failed"`；后续submit用`pytest.raises(RuntimeError, match="persistence is unavailable")` |
| T18 | test_hook_mutation_then_failure_rolls_back | `assert result == value_before_bad_hook`；`assert original == original_before_dispatch`；`assert call_order == ["first", "second"]`，async测试另断言第二Hook看到第一输出 |
| T19 | test_prompt_rewrite_reaches_llm | `assert fake_llm.received_messages == expected_messages`；`assert yielded_chunks == original_chunks`（仅stream.after观察时） |
| T20 | test_result_rewrite_still_obeys_budget | `assert final_token_count <= effective_budget`；`assert argument_hook_count == 1`；`assert observed_parallel_arguments == rewritten_arguments`；权威success/metadata不被Hook改写 |
| T21 | test_recording_counts_are_conserved | `assert sum(r.data["count"] for r in summaries) == emitted_count`；`assert backend.pending_count == 0`（停机后）；`assert known_full_text not in trace_text` |
| T22 | test_runtime_rewrite_preserves_routing | `assert consumed.payload == rewritten_payload`；`assert consumed.event_id == original.event_id`；能力响应不出现于旁听插件收件列表 |
| T23 | test_legacy_history_page_cursor_advances | `assert page.next_after_sequence == page.records[-1].sequence`；`assert page.has_more is expected_has_more`；`assert set(wire) == {"records", "history_cursor", "next_after_sequence", "has_more"}` |
| T24 | test_overflow_closes_only_slow_subscriber | `assert slow.closed is True`；`assert fast.closed is False`；`assert received == expected_order`；Gateway慢连接`assert close_code == 1013` |
| T25 | test_packaged_real_gateway_contract | `assert health["status"] == "ready"`；重启history `assert cursor_after >= cursor_before`；`assert violations == []`（AST边界检查） |
| T26 | test_document_links_and_old_entry_cleanup | 文档校验脚本`assert missing_links == []`；生产旧引用`assert old_imports == []`；不可把测试脚本退出0当应用已通过 |

`CapabilityRequester.pending_count`是只读诊断属性，用于确认清理而不是让调用方操纵Future。
T11的PluginContractError、T12的pending_count是此处新增的明确Interface，需在对应任务一并实现。

## 1. 必须先锁住的 Interface

### 1.1 外部入口

`agent_app.application` 导出 `AgentRuntime`；最终 `AgentRuntime is SessionApi`（同一个类的别名，不是新包装层）。
沿用现构造的关键字参数：config_loader、idle_timeout=6h、cleanup_interval=2h、update_queue_capacity、
submission_history_limit=1024、task_history_limit=1024、data_dir、clock、session_factory、session_store、logger。
完整公开签名目标如下；导入路径更新不是改变参数的理由，默认get_config/SessionRuntime指向迁移后的同职责对象：

```python
class SessionApi:
    def __init__(self, *, config_loader: Callable[[], ConfigModel] = get_config,
                 idle_timeout: timedelta = timedelta(hours=6),
                 cleanup_interval: timedelta = timedelta(hours=2),
                 update_queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
                 submission_history_limit: int = 1024,
                 task_history_limit: int = 1024,
                 data_dir: str | Path | None = None,
                 clock: Callable[[], datetime] | None = None,
                 session_factory=SessionRuntime,
                 session_store: SessionStore | None = None,
                 logger: logging.Logger | None = None) -> None: ...
    @property
    def is_running(self) -> bool: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def subscribe_updates(self) -> RuntimeUpdateSubscription: ...
    def get_memory_context(self, workspace_path: str | Path | None = None) -> dict: ...
    async def create_session(self, workspace_path: str | Path,
        session_id: str | None = None, *, load_runtime: bool = True) -> str: ...
    async def submit(self, workspace_path: str | Path, session_id: str, prompt: str, *,
        submission_id: str, resources: tuple[ResourceRef, ...] = (),
        display_text: str | None = None) -> InputAccepted: ...
    async def cancel_task(self, workspace_path: str | Path, session_id: str,
        task_id: str, reason: str | None = None) -> TaskOperationResult: ...
    async def steer_task(self, workspace_path: str | Path, session_id: str,
        task_id: str, content: str, *, resources: tuple[ResourceRef, ...] = (),
        display_text: str | None = None,
        submission_id: str | None = None) -> TaskOperationResult: ...
    async def unload_session(self, workspace_path: str | Path,
        session_id: str) -> UnloadResult: ...
    async def get_session_status(self, workspace_path: str | Path,
        session_id: str) -> SessionStatus: ...
    async def list_session_statuses(self, workspace_path: str | Path) -> tuple[SessionStatus, ...]: ...
    async def list_session_summaries(self, workspace_path: str | Path) -> tuple[SessionSummary, ...]: ...
    async def generate_session_title(self, workspace_path: str | Path,
        session_id: str) -> str | None: ...
    async def discard_empty_session(self, workspace_path: str | Path,
        session_id: str) -> DiscardSessionResult: ...
    async def delete_session(self, workspace_path: str | Path,
        session_id: str) -> DiscardSessionResult: ...
    def get_task_status(self, workspace_path: str | Path, session_id: str,
        task_id: str) -> TaskStatus: ...
    async def get_session_history(self, workspace_path: str | Path, session_id: str, *,
        after_sequence: int = 0) -> tuple[tuple[RuntimeUpdate, ...], int]: ...
    async def cleanup_idle_sessions(self) -> None: ...
```

完整19个公开入口的property与async形态由T01记录源码基线并做兼容测试；不得仅手抄17个名称。
异常仍从此门面导出同一canonical类。

分页兼容扩展：

```python
@dataclass(frozen=True)
class HistoryPage:
    records: tuple[RuntimeUpdate, ...]
    history_cursor: int
    next_after_sequence: int
    has_more: bool

async def get_session_history_page(
    self, workspace_path: str | Path, session_id: str, *,
    after_sequence: int = 0, limit: int = 200,
) -> HistoryPage: ...
```

原 `get_session_history(..., after_sequence=0) -> tuple[tuple[RuntimeUpdate, ...], int]` 保留。
分页limit范围1..500；对旧delta先完整投影再分页，不把“逻辑跳号”当数据库损坏。

### 1.2 状态与能力完整映射

| 旧provide | 新契约/去处 | 真实消费者与通路 |
|---|---|---|
| persistence.runtime | 从能力表移除，runtime.assets | 应用/插件构造注入；同步图片导入保持同步 |
| persistence.session | 移除，core.identity + runtime.assets.PersistenceSession | 构造注入 |
| persistence.redactor | 移除，agent_app.utils.Redactor | 直接叶子工具，无Plugin生命周期 |
| persistence.state_store | `state.store` v1.0.0 | Host/StateCoordinator直连PluginStateStore，启动恢复前可用 |
| agent.task_control | `task.control` v1.0.0 | SessionHost直连TaskControl端口，不做插件间对象调用 |
| agent.task_channels | 移除，SessionHost拥有唯一TaskChannelRegistry | Agent/UserInput/Host共享注入；不能各自fallback新建 |
| blackboard.conversation | `conversation.read` v1.0.0 | Skill经请求/响应读Blackboard已提交Message |
| blackboard.region_registry | 移除 | 应用装配器在READY前连线，不走未启动总线 |
| user-input.input | `input.submit` v1.0.0 | SessionHost直连InputSubmission端口 |
| skill.skill_management | 移除未被消费的声明 | 五个工具保留，JobManager仍插件内部helper |

全部 require 版本沿用 `>=1,<2`。state_scopes声明仍必须require `state.store`；Skill需要它和conversation.read。
其他插件删设施require；不因为删Region能力而漏掉Memory装配回滚（T13）。

能力定义类采用显式ClassVar，不用无类型object万能表：`CapabilityContract`声明
`capability_id: str`、`version: str`、`access_mode: Literal["event", "host"]`；
事件能力子类额外声明`request_type`、`response_type`和`failure_response(request, status, *, code, message)`，
host能力子类声明`port_type`（runtime_checkable Protocol）。内置类名为`ConversationRead`、`StateStoreAccess`、`TaskControlAccess`、`InputSubmitAccess`。
Catalog.register接收能力类，插件类只声明提供哪些类，不重复保存运行对象；runtime registry保存provider绑定。

默认用户配置必填表：memory=`("user_id", "agent_id")`，knowledge=`("knowledge_base",)`，其余9插件为空。
远端服务凭据继续从既有环境变量读且仅对应远端条件下必需，不能写成settings中的必填明文密码。
每插件新增`config.py`的Pydantic schema（已有Process/MCP配置复用），保留现默认值/extra拒绝/条件校验。
装配器先对用户plugin_config做required_config与插件schema校验，再合并显式的应用注入字段（config_model、logger等）；
这些字段不是用户schema的一部分，不能把现Memory/Knowledge的unknown-field守卫关掉来接纳注入对象。

不额外引入FactoryContext或通用service-locator。工厂继续接收现有扁平参数
`create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger)`（可async）；
T07/T08把设施作为已知config键显式注入，T10令required_capabilities只由Host持有、对插件工厂不再传兄弟对象，
T14最后一个消费者迁出后删除此参数。工厂内部直接读取已知typed依赖并校验；不扩展成任意动态查服务入口。

core目录清单内的能力分两种**访问模式，不新增第三条通信通道**：
`event` 仅 conversation.read；`host` 为 state.store/task.control/input.submit。
host模式是D8已规定的“基座直接访问”例外，**不能注入给其他插件，也不能通过self.request伪装调用**。
能力注册表仍一份，契约模式用于装配校验；不新增assembly-only能力或Region RPC。

### 1.3 新能力请求的最小协议

```python
# core/capabilities/base.py；全部Event已有实例event_id，不能拿它当类型ID。
@dataclass(frozen=True, kw_only=True)
class CapabilityRequest(Event):
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    timeout_seconds: float | None = None

@dataclass(frozen=True, kw_only=True)
class CapabilityResponse(Event):
    correlation_id: str
    status: CapabilityStatus  # OK / NOT_FOUND / UNAVAILABLE / TIMEOUT / FAILED
    error_code: str | None = None
    error_message: str | None = None
    def unwrap(self) -> Self: ...

@dataclass(frozen=True, kw_only=True)
class ConversationReadRequest(CapabilityRequest): ...

@dataclass(frozen=True, kw_only=True)
class ConversationReadResponse(CapabilityResponse):
    messages: tuple[Message, ...] = ()
```

请求task_id沿用Event已有字段。默认timeout=30秒，显式值必须有限且>0；不重试、不广播；
空历史是OK；CancelledError传播；失败合成同类型响应，且messages为空。
core定义`CapabilityCallError(RuntimeError)`，unwrap遇非OK抛此类并保留status/error_code属性，OK返回self；
Skill在工具错误适配层转为原ToolExecutionResult失败，不把技术错误误写成空历史。
返回快照深拷贝Message内的可变content/tool_calls/arguments，不能以frozen外壳冒充深度不可变。

```python
class CapabilityRequester:
    async def request(self, event: CapabilityRequest) -> CapabilityResponse: ...
    def accept_response(self, source_plugin_id: str, event: CapabilityResponse) -> bool: ...
    def close(self) -> None: ...  # 唤醒所有pending为UNAVAILABLE

class BasePlugin:
    async def request(self, event: CapabilityRequest) -> CapabilityResponse: ...
```

core.Requester仅接收注入callable做查询/发送，不import总线或runtime registry。

### 1.4 Tier 提案与兼容 required

| tier | 插件 | 失败处理 |
|---|---|---|
| base | persistence、builtin-tools、user-input、blackboard、agent、runtime-update | 启动失败、回滚 |
| core | skill、memory、knowledge、process | 无显式required时禁用并警告 |
| external | mcp | 无显式required时禁用并通知 |

**现有默认 required_plugin_ids 不在本次静默缩减**；因此默认配置下这些required插件仍是失败阻断。
只有调用方主动移出required清单后才体现tier的降级差异。后端不可达与插件自身不能加载不是一回事，保留各插件已有延迟连接/工具错误语义。

## 2. 任务索引与依赖

| ID | 阶段 | 交付 | 前置 |
|---|---|---|---|
| T01 | P0 | 行为/外部/存储基线守卫 | 无 |
| T02 | P0 | 本地distribution、双venv/CI安装、资源策略 | T01 |
| T03 | P1 | 通用core/runtime机械迁移 | T02 |
| T04 | P2 | 业务/测试/字符串路径迁移闭环 | T03；紧接执行 |
| T05 | P3 | 模型创建与AgentAssembly解耦 | T04 |
| T06 | P3 | 配置角色数组化与显式角色查找 | T05 |
| T07 | P3 | 身份/路径/资产/脱敏与后端切开 | T04 |
| T08 | P3 | SessionHost与唯一TaskChannelRegistry | T05、T07 |
| T09 | P5a | manifest schema、契约目录与事件ID | T04 |
| T10 | P5a | provider解析/tier/失败级联/无业务ID内核 | T08、T09 |
| T11 | P5b | 声明handler与build_tools | T09、T10 |
| T12 | P5b | 单总线能力请求/响应闭环 | T10、T11 |
| T13 | P5b | Agent/Input/Blackboard/Memory装配与契约 | T08、T11、T12 |
| T14 | P5b | Skill异步历史查询与插件内工具 | T12、T13 |
| T15 | P4 | 业务Repository与通用SQLite设施 | T04、T07 |
| T16 | P4 | SessionRegistry/SubmissionGuard/ResourceImporter | T08、T15 |
| T17 | P4 | UpdatePipeline/HistoryProjector/Reaper/SessionApi | T13、T16 |
| T18 | P6 | Hook串行值通路与回退 | T11 |
| T19 | P6 | Agent/LLM真实值挂点 | T05、T18 |
| T20 | P6 | Tool挂点与策略/预算一致性 | T18、T19 |
| T21 | P6 | 独立记录器与persistence后端 | T07、T18 |
| T22 | P6 | runtime Hook/记录接线与事件链诊断 | T12、T19–T21 |
| T23 | P7 | 应用分页与显式Gateway wire | T17 |
| T24 | P7 | 有界mailbox/fanout与统一容量 | T17 |
| T25 | P7 | 全插件封边界、类型检查与跨应用总验收 | T06、T13–T14、T22–T24 |
| T26 | P7 | 文档与旧入口清理 | T25 |

每任务收尾只划定逻辑变更边界，不等于可以把尚未闭合的应用状态提交为可发布版本。
**T09–T14是一个连续的契约切换验收批次**：基础任务可在独立测试图上验证自己的Interface，但最终11份manifest、factory、handler与消费者
必须同批贯通，批次结束才运行并要求全Agent/Gateway基线不退化；不承诺每个破坏性中间态都能启动旧应用。
若需要拆成多个可合并提交，先在T09/T10仅新增契约模块和独立fixture，保持生产旧schema，
最后一批原子切换生产manifest/工厂/消费者；不能为省事长期支持两套capability语义或引入feature flag。

推荐串行主线：T01→T04→T05→T06→T07→T08→T09→T14→T15→T17→T18→T26。
可以在不同文件上并行设计/实现T15与P5，但不能在同一插件的handler/Hook两种改造中并行写入。
能力基础前置于插件移除直连，P3不再提前使用尚不存在的P5a registry。

---

## T01 · 固定现有行为、外部 Interface 与存储语义

**Files:** 新建 `AT/application/test_refactor_contract.py`（T04移动后为`AT/agent_app/application/test_refactor_contract.py`）、
`GT/test_runtime_contract.py`；保留现有application、Blackboard、Gateway、TUI测试。

**Interfaces:** 消费当前AgentRuntime与application门面；产出真实类型身份、19入口/构造/property基线与离线fixture；后续任务沿用。

- [x] **RED：新增守卫** `test_facade_exports_canonical_objects`：门面异常/RuntimeUpdate与定义处`is`一致；
  `test_runtime_interface_matches_baseline`：检查19个入口、is_running为property、同步get_task_status/subscribe_updates与其他async方法、参数默认值。
  不是只断言方法存在，也不新增过度严格的私有方法快照。canonical迁移后必须保留这样的直接身份断言：

  ```python
  def test_facade_exports_canonical_objects():
      from agent_app import application
      from icarus.core.runtime_update import RuntimeUpdate
      from agent_app.application.errors import SubmissionConflictError
      assert application.RuntimeUpdate is RuntimeUpdate
      assert application.SubmissionConflictError is SubmissionConflictError
  ```
- [x] **锁存储行为测试** `test_model_and_public_history_remain_distinct`：prompt/display_text不同、Message保留工具配对与上下文、公共thinking不入模型、compact不清UI历史；
  `test_restart_keeps_state_and_public_cursor`：重启恢复、旧state version、软删除与历史sequence；复用现有测试而非重写同一套逻辑。
- [x] **建立基线** 运行 `apps/agent/.venv/bin/python -m pytest apps/agent/test/application apps/agent/test/agent_orchestration/plugins/blackboard -q`，
  再跑 `make test-agent` / `make test-gateway` / `make test-tui`，记录失败**测试身份**与环境，不声称未知基线全绿。
- [x] **GREEN/交付** 新守卫在旧实现上应PASS；若新增canonical包守卫尚无包，明确它只在T02后启用，不对业务基线造假。
  新增Gateway真实runtime fixture使用临时data_dir和stub模型/后端，不连接真实服务。逻辑提交边界：基线测试；不执行commit。

## T02 · 打包、安装环境与资源闭环

**Files:** 新建 `apps/agent/pyproject.toml`、`A/icarus/__init__.py`、`A/agent_app/__init__.py`、
`A/agent_app/resources/{__init__.py,default_settings.json}`、`AT/test_packaging.py`、`apps/agent/scripts/check-package.sh`；
修改agent/gateway安装器、CI两份workflow、`apps/agent/requirements-dev.txt`。

**Interfaces:** distribution建议名`icarus-agent`（仅元数据，不发布），import包为icarus/agent_app；
setuptools src发现只include这两个根及子包，exclude测试和旧apps命名空间；Python>=3.11。

- [x] **RED：** `test_editable_import_outside_checkout` 用临时cwd和移除PYTHONPATH的子进程import两个根；
  `test_wheel_contains_declared_resources` 检查wheel内默认settings及迁移后的11 manifest；T02时先验证默认资源，manifest断言在T04迁入后启用。
- [x] **实现元数据与依赖唯一来源：** build backend=`setuptools.build_meta`，构建依赖声明`setuptools>=69`、`wheel`；
  project dependencies动态读取既有requirements.txt，不再抄第二份依赖表；dev文件增加mypy和build。
  package-data显式覆盖`agent_app.plugins: */manifest.json`与resources JSON；不要误打包用户.env/临时文件。
- [x] **接线安装：** 两个app安装器各在原venv中安装requirements后执行`python -m pip install --no-deps -e <apps/agent>`。
  CI所有Agent/Gateway uv临时运行追加本地editable项目（`uv run --no-project --with-editable ./apps/agent ...`），保留原requirements输入。
  不向TUI venv安装Agent，不删除Gateway start.sh的PYTHONPATH。
- [x] **配置资源策略：** 保留`apps/agent/settings.json`用户入口；迁移后ConfigLoader通过显式settings_path或`ICARUS_SETTINGS_PATH`（显式路径无效即报错）优先读取，
  checkout中从模块位置定位已有apps/agent/settings.json，普通安装fallback随包default_settings.json。
  两份默认配置一致性测试约束版本库默认值，不读取/发布用户私有配置；配置schema此时不改。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/test_packaging.py -q`；
  `check-package.sh`构建到临时目录、检查wheel内容并在临时venv安装，退出清理临时目录，不改仓库用户环境。
  本任务仅检查空壳包能import，不宣称业务已迁入；mypy只对实际纳入文件运行。逻辑提交边界：打包闭环。

## T03 · 通用框架机械迁移，保持类型身份

**Files:** 按§0.2所有T03行移动；新增`A/icarus/core/{model_types.py,tools/settings.py}`；
修改对应`AT/icarus/`镜像测试、旧ConfigModel re-export、业务import与manifest类路径、CI显式测试路径。

**Interfaces:** 通用ToolExecutionSettings整类移动后由旧model_config同对象re-export；LLMRole先保留原Literal的canonical别名，T06再改str。
PluginAssembly只是原PluginGraphBuilder改名。Host/Discovery接收`builtin_package: str | None = None`，None表示不扫描内置业务包；现应用显式传原插件包名。

- [x] **RED：** `AT/icarus/test_import_boundaries.py::test_core_and_runtime_do_not_import_application` 验证AST依赖与隔离子进程导入；
  `test_moved_event_identity_is_singleton` 验证Event/Message/错误/Registration单一对象；准备好全部迁移路径后再去旧定义。
- [x] **移动与改路径：** 按表机械迁移，修普通import、factory/事件字符串、monkeypatch和测试helper路径；
  外层plugins仍旧包时不提前禁止插件间import。原AgentFactory、厂商LLMFactory、PersistenceRuntime留业务一侧，不能为“搬完”把反向依赖带进core。
- [x] **默认与门面：** 应用显式选择builtin包；icarus门面只re-export BasePlugin/Event/BaseTool/BaseAgent，不拉业务；
  迁移窗口如确需旧入口，文件仅re-export canonical对象，不能复制实现、加载同文件两次或创建异常子类冒充同类型。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus -q`，随后全Agent和Gateway；
  旧类型引用与序列化测试行为应保持基线。此任务不改tier、capability路由、Hook或model_settings。
  与T04连续执行，不能把半迁移状态作为长期交付。逻辑提交边界：core/runtime机械迁移。

## T04 · 业务、插件、外部消费者与所有字符串迁移

**Files:** §0.2所有T04行；11份`A/agent_app/plugins/*/manifest.json`；`A/agent_app/config/config_loader.py`、
三个smoke脚本；Gateway四个生产引用与测试、`packages/gateway_protocol/models.py` TYPE_CHECKING、TUI禁止清单；所有§0.3测试。

**Interfaces:** `agent_app.application`先re-export原行为的AgentRuntime及原公共类型；
`agent_app.plugins.__init__`为空；每插件内部__init__仅导出自身类型，不从兄弟插件拉取。

- [x] **RED：** `test_all_builtin_manifests_discover_without_eager_plugin_import`：11个ID齐全、发现时不执行factory；
  `test_new_facade_works_in_gateway_environment`；TUI`test_cli`禁止新`agent_app`和`icarus`重依赖被首帧加载。
- [x] **机械搬迁：** 业务目录按表整体移动，AgentFactory暂在agent_app/assembly.py；
  runtime中所有默认业务包路径已去除，由app配置传`agent_app.plugins`。11工厂entrypoint与暂用事件类路径全部换canonical路径。
  CI/help fixtures/smoke import同步，不提前引入稳定事件ID（T09负责）。
- [x] **配置bootstrap：** 落实T02路径选择；Agent包本身移除对根packages.runtime_environment的运行期import，
  在`agent_app/config/environment.py`保留相同行为：checkout固定仓库.env，`override=False`；普通安装只有显式env文件或进程环境，不搜索任意祖先猜repo。
  根packages模块留给其余应用，不把它打包进agent distribution；现有加载测试移后适配。
- [x] **外部与资源：** Gateway从`agent_app.application`取RuntimeUpdate等同一门面符号；wire包仍仅TYPE_CHECKING导入。
  删除Agent三个smoke的sys.path插入，说明用已安装Agent解释器运行；Gateway/TUI自身bootstrap保留。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test -q`、`make test-gateway`、`make test-tui`；
  T02 wheel资源守卫此时必须覆盖11 manifests；在两venv从非repo cwd导入Agent门面成功。
  `rg 'apps\.agent\.src' apps/agent/src apps/gateway/src packages/gateway_protocol`应无生产依赖；测试历史文档不全局误替换。
  逻辑提交边界：业务/import/resources机械迁移，不混后续行为。

## T05 · 模型创建注入与 AgentAssembly

**Files:** 新建`A/icarus/core/assembly.py`；修改`core/llm.py`、`agent_app/model_provider/factory.py`、
`agent_app/plugins/{agent,skill,blackboard}/factory.py`、`application/session_title.py`；删暂存`agent_app/assembly.py`；
测试`AT/icarus/core/test_assembly.py`、`AT/agent_app/model_provider/test_factory.py`。

**Interfaces:**

```python
class LLMFactory:
    def __init__(self, create: Callable[[str], BaseLLM]) -> None: ...
    def create_llm(self, role: str) -> BaseLLM: ...

class AgentAssembly:
    def __init__(self, *, llm_factory: LLMFactory, tool_registry: ToolRegistry,
                 hook_registry: HookRegistry,
                 policy_for_role: Callable[[str], ToolExecutionPolicy],
                 tool_result_store: ToolResultStore | None = None) -> None: ...
    def get_agent(self, model_role: str) -> BaseAgent: ...
    def close(self) -> None: ...
    async def aclose(self) -> None: ...
```

应用的ModelProviderFactory保留现create_llm可选protocol/model_name/max_tokens/temperature/think/budget参数，
负责ConfigModel、SDK选择与image_resolver；只通过其绑定方法适配core.LLMFactory。

- [x] **RED：** `test_assembly_accepts_only_core_dependencies`：构造时不读配置/SDK；
  `test_registry_is_only_tool_source`：默认不自动注册内置工具；`test_role_cache_and_close_sync_async`：角色缓存和资源释放与原行为一致。
- [x] **实现注入：** 把AgentFactory的缓存、Observable包装和close/aclose移动为AgentAssembly；
  删除register_builtin_tools及默认创建厂商factory分支，不新增重复Agent类型。policy_for_role由app按角色配置生成。
- [x] **更新真实消费者：** Agent、Skill私有生成Agent、Blackboard压缩和标题创建均走app provider；
  Skill私有ToolRegistry保持独立且冻结，不强迫它使用主Agent全部工具；无子插件。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/test_assembly.py apps/agent/test/agent_app/model_provider -q`；
  隔离进程屏蔽openai/anthropic模块后`import icarus`仍成功；Agent/Skill既有测试不退化。逻辑提交边界：模型接入依赖切分。

## T06 · model_settings 数组化与显式角色配置

**Files:** `A/agent_app/config/{config_model,config_loader}.py`、`A/icarus/core/model_types.py`、
`agent_app/model_provider/factory.py`、Blackboard/Skill工厂、session_title、应用assembly；
`apps/agent/settings.json`、随包default_settings.json；`AT/agent_app/config/test_config_loader.py`及所有构造ConfigModel的fixture。

**Interfaces:** `model_settings: list[NamedLLMConfig]`，NamedLLMConfig在现LLMConfig上增加`name: str`；
`ConfigModel.get_model(name: str) -> NamedLLMConfig`，空白/重复/未知名字显式报错；LLMRole为str。
新增`ModelRoleSettings(agent="thinking", compaction="thinking", skill_generation="thinking", title="perception")`，配置字段`model_roles`。

- [x] **RED：** 参数化`test_unique_named_models`（空、空白、重复name失败）；
  `test_roles_are_looked_up_by_name`用非thinking/perception名字完整装配，未知引用在加载时失败，不延迟到SDK请求。
- [x] **实现schema：** 保留现LLMConfig数值约束与协议字段；默认JSON改数组，不创建框架配置类。
  兼容既有固定thinking/perception字典输入：加载期转换为同一数组并给弃用诊断，不双写配置；测试明确既有用户文件仍可加载。
- [x] **接线调用点：** 去所有getattr(model_settings, role)；标题不再写死perception，压缩/生成不写死thinking，
  Blackboard的主角色通过应用注入。默认角色行为保持不变。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/config apps/agent/test/agent_app/model_provider apps/agent/test/icarus/core/test_assembly.py -q`，再跑受影响插件/标题测试。
  不修改Provider协议、不迁移历史文件。逻辑提交边界：配置角色契约。

## T07 · 分离会话设施与持久化后端

**Files:** 移动identity/path_resolver/resource_ref/Redactor到§0.2目标；新增`A/icarus/runtime/assets.py`；
修改`agent_app/plugins/persistence/{runtime,plugin,factory,trace_writer,log_handler}.py`及7类消费者；
新增`AT/icarus/runtime/test_assets.py`、`AT/icarus/core/test_identity.py`，移动原images/path/identity测试。

**Interfaces:** 保留`SessionIdentity.create`、DataPathResolver现方法、`PersistenceSession.import_image/import_image_bytes/resolve_image`签名；
新的core身份和runtime资产类不创建后端；应用创建并注入。
原PersistenceRuntime只保留resolver、workspace_identity、会话scope、资产方法和设施运行状态；
旧trace/log start/stop由业务persistence backend adapter暂接管，T21再转runtime记录端口。

- [x] **RED：** `test_assets_import_without_plugin_backends`屏蔽agent_app导入后同步导入/解析图片；
  `test_identity_and_paths_are_unchanged`：workspace哈希、assets SHA256命名、20MiB限制、相对路径/符号链接逃逸保护、缺文件异常同一语义。
- [x] **切开实现：** JsonStateStore、FileTraceWriter/TraceRecord、log handler仍在插件；Redactor所有跨插件引用转utils。
  不保留`runtime.state_store/redactor/trace_writer`式反向聚合；PersistencePlugin显式持有后端与resolver/identity。
  移除资产设施对具体HookRegistry/TraceHook的构造依赖，context函数仍可直接使用core。
- [x] **更新消费者：** SessionRuntime、Agent、Blackboard、UserInput、MCP、Skill、Process factories通过注入取设施；
  MCP.result_converter.import_image_bytes仍同步；无每图片一次事件往返，不升级整条MCP同步链。
  所有错误/类型兼容导出指向同一对象。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/runtime/test_assets.py apps/agent/test/agent_app/plugins/persistence apps/agent/test/agent_app/plugins/mcp/test_result_converter.py -q`，再受影响应用。
  `ConfigModel`之外的设施依赖从插件schema分离；插件不得在start/stop中管理共享资产设施。
  SessionHost在插件factory之前启动assets，失败/正常stop时在所有插件与记录后端结束后关闭assets；
  base设施失败即启动失败，不能借tier吞掉。保留资产导入线程安全与session_scope身份传播。
  不改state envelope/trace路径/日志隔离。逻辑提交边界：设施与后端归属。

## T08 · SessionHost 与唯一任务通道

**Files:** 新建`A/icarus/{core/session.py,core/ports.py,runtime/session.py}`、`A/agent_app/application/assembly.py`；
修改session_runtime.py、runtime/host.py、Agent/UserInput factories；测试`AT/icarus/runtime/test_session.py`及原session_runtime/UserInput/Agent测试。

**Interfaces:** core.ports声明结构性接缝：

```python
class InputSubmission(Protocol):
    @property
    def queued_count(self) -> int: ...
    async def submit(self, prompt: str,
                     input_images: list[ImagePart | str | Path] | None = None, *,
                     task_id: str | None = None) -> InputAccepted: ...

class TaskControl(Protocol):
    def handle_task_operation(self, source_id: str,
        event: TaskContextInputEvent | TaskCancelRequestedEvent | TaskSteerRequestedEvent
    ) -> TaskOperationResult: ...
```

InputAccepted随公开输入类型进core；`SessionRuntimeSnapshot`进core/session.py。
`SessionHost(identity, *, runtime_host, assets, task_channels)`维护start/stop/checkpoint/snapshot/submit/cancel/steer/import_resources；
这些签名沿用旧SessionRuntime，去掉ConfigModel/default prompt依赖。
`PluginRuntimeHost(..., before_restore: Callable[[PluginRuntimeHost], Awaitable[None]] | None = None,
 before_ready: Callable[[PluginRuntimeHost], Awaitable[None]] | None = None)`提供两个通用装配接缝：
before_restore在所有factory成功/构建级联后、任何插件start/restore前注册Region定义；before_ready在最终启动失败级联后、registry冻结/总线启动前移除失效owner并freeze；失败走统一回滚。
这两个callback由应用实现，runtime不识别Region。不能只在restore后注册，否则现有session Region快照会因找不到定义被丢弃。

- [x] **RED：** `test_task_channels_are_session_owned`断言host、agent、user-input拿到相同Registry；
  `test_channel_cancel_signal_survives_queue_and_prepare`覆盖queued/preparing/running取消；
  `test_session_host_has_no_product_config`用两个简单测试插件启动，不导入agent_app。
- [x] **实现：** SessionHost在装配入口创建/拥有唯一TaskChannelRegistry（max_steps由应用参数传），Agent/UserInput不再fallback新建。
  Host状态直接读该设施，不再查`("agent","task_channels")`；先保留旧input/task_control获取适配，T10换契约ID。
- [x] **应用适配：** 原SessionRuntime变薄适配器，保持`(identity, *, config, publish_update, system_prompt, tools, initial_messages, logger)`接缝及可替换session_factory；
  默认prompt放`agent_app/resources/prompts.py`，11插件配置组装放application/assembly.py。
  runtime接收参数，不按插件ID生成业务配置。
- [x] **生命周期：** 保留shielded stop、取消start的完整清理、关闭后不可重启、checkpoint先drain。
  新before_restore/before_ready只允许装配连接，不在其中发业务请求；T13接入Region注册、恢复与finalization。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/runtime/test_session.py apps/agent/test/agent_app/application/test_session_runtime.py apps/agent/test/agent_app/plugins/user_input/test_plugin.py -q`。
  逻辑提交边界：会话设施拥有权。

## T09 · manifest v2、稳定类型ID与能力目录

**Files:** 修改`A/icarus/core/manifest.py`；新增`core/events/catalog.py`、`core/capabilities/{base,catalog,conversation}.py`；
修改runtime discovery/resolver及全部11 manifest；测试`AT/icarus/core/test_manifest.py`、`test_event_catalog.py`、`capabilities/test_catalog.py`。

**Interfaces:** `PluginTier`枚举；manifest schema_version=2，新增`tier`、`required_config: tuple[str,...]`，
RequiredCapability删除plugin_id。事件ID建议由core目录显式登记`<namespace>.<type_name>`，例如`input.user_input`、`agent.completed`、`context.region_updated`；
版本保持在能力独立字段，事件不把UUID当类型ID。内置事件ID完整映射（左为现类名，canonical类型由T11移动）：

| 类型 | 稳定ID | 类型 | 稳定ID |
|---|---|---|---|
| AgentCancelledEvent | agent.cancelled | AgentCompletedEvent | agent.completed |
| AgentMessageCompletedEvent | agent.message_completed | AgentTextDeltaEvent | agent.text_delta |
| AgentThinkingCompletedEvent | agent.thinking_completed | AgentThinkingDeltaEvent | agent.thinking_delta |
| AgentToolCompletedEvent | agent.tool_completed | AgentToolStartedEvent | agent.tool_started |
| TaskErrorEvent | task.error | BlackboardCompactedEvent | context.compacted |
| BlackboardContextReadyEvent | context.ready | BlackboardRegionUpdatedEvent | context.region_updated |
| ProcessUpdatedEvent | process.updated | InputFinishedEvent | input.finished |
| InputQueuedEvent | input.queued | InputStartedEvent | input.started |
| UserInputEvent | input.user_input | TaskCancelRequestedEvent | task.cancel_requested |
| TaskCancelResultEvent | task.cancel_result | TaskContextInputEvent | task.context_input |
| TaskContextInputResultEvent | task.context_input_result | TaskSteerAppliedEvent | task.steer_applied |

TaskSteerRequestedEvent虽未出现在当前22种manifest引用中，仍在core目录登记`task.steer_requested`，供Host端口/类型验证；
不擅自新增其广播订阅。CapabilityRequest/Response各自通过CapabilityCatalog登记，不列此业务事件表。
`EventCatalog.register(event_id: str, event_type: type[Event])` / `resolve(event_id)->type[Event]`，重复ID或一类型多ID失败；
`CapabilityCatalog`按§1.2提供完整定义、访问模式、请求/响应或host Protocol。

- [x] **RED：** `test_unknown_or_duplicate_contract_is_rejected`；`test_capability_events_are_not_business_declarations`；
  `test_required_config_missing_before_factory_import`；`test_state_scopes_require_state_store_by_contract_not_provider`。
- [x] **实现目录与schema：** 事件注册清单从现有11manifest全部类路径逐一生成并人工核对；类型不落业务模块的先在T11迁移，
  本任务可用显式传入catalog适配类路径实现，但最终manifest只能稳定ID，不保留importlib event反射fallback。
  第三方自有事件由调用方装配前传EventCatalog扩展，discovery不执行其实现；未知引用不能静默跳过。
- [x] **配置声明：** 工厂注入的runtime/identity/logger/config_model/tool_registry等不列入用户required_config；
  Memory的user_id/agent_id及各插件实际必填用户字段按现验证提取；有默认值的字段不强制写settings。
  `python_requires`语义保留为包依赖声明，不误当requires-python。
- [x] **11 manifest：** tier采用§1.4，provide/require采用§1.2；工具名和状态scope/version不变；
  新ID/版本Schema属于行为改造；本任务与T10作为同一原子可验收切片实施，中间RED不可交付。
  在此切片保留临时event类路径适配，由T11一次删完，不能宣称T09结束已无importlib。
  schema v1外部插件清楚报“需迁移”，不静默猜测旧provider；这次不提供双schema长期兼容层。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/test_manifest.py apps/agent/test/icarus/core/test_event_catalog.py apps/agent/test/icarus/core/capabilities/test_catalog.py -q`。
  schema/ID必须与T10紧接推进，不能停在runtime读不懂manifest的中间态。逻辑提交边界：静态契约基础。

## T10 · 能力注册、依赖解析、tier 与失败级联

**Files:** 新建`A/icarus/runtime/capability_registry.py`；修改runtime/{assembly,resolver,host,state_coordinator,diagnostics}.py、core/registration.py；
测试`AT/icarus/runtime/{test_capability_registry,test_discovery_resolver,test_host,test_state_coordinator}.py`。

**Interfaces:** `CapabilityRegistry.bind(capability_id, version, provider_plugin_id, value=None)`；
`provider_for(capability_id: str, version_spec: str) -> str`、`get_host_port(capability_id: str) -> object`；
`PluginRuntimeHost.get_capability(capability_id: str)`只给host模式返回端口，event模式不给插件对象。
所有能力require版本解析基于提供契约，不是具体plugin_id。

- [x] **RED：** 替换提供者插件ID后state恢复/提交/控制仍工作；重复提供者冲突、缺失/版本不兼容/依赖环；
  工厂失败、start失败、restore失败、自检失败均级联；base/显式required阻断，core/external按级别禁用。
- [x] **实现resolver和assembly：** 不能只改resolver；graph构造所需对象、依赖边、构建/运行失败级联、诊断快照全部改为契约解析。
  重复provider全部标冲突，不以发现顺序择优；受影响required/base导致启动失败；剩余按tier降级。
  只向Host提供host端口，普通插件factory不再获取任何其他插件实例。
- [x] **状态存储：** manifest只检查require state.store；state coordinator按registry找PluginStateStore；
  存储提供者启动后且恢复消费者前refresh，不再if plugin_id==persistence。保留同步load/save，不经尚未启动的worker。
- [x] **自检与required：** `BasePlugin.on_session_start(self) -> None` async，默认no-op，异常表示失败；顺序为start→restore→自检，
  最终级联/装配finalize成功后才READY。required清单默认不缩减；损坏manifest按缺失必需项处理，不信任其中未经验证的tier。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/runtime/test_capability_registry.py apps/agent/test/icarus/runtime/test_discovery_resolver.py apps/agent/test/icarus/runtime/test_host.py apps/agent/test/icarus/runtime/test_state_coordinator.py -q`。
  AST守卫runtime无具体插件ID判断、无领域import；不以全局禁用普通词"agent"的文本grep替代依赖检查。
  逻辑提交边界：provider/tier/状态启动契约。

## T11 · 声明式 handler、共享事件与工具导出

**Files:** `A/icarus/core/{plugin,registration}.py`、新`core/contracts/{input,context,process}.py`、runtime/{assembly,manager,worker}.py；
全部`agent_app/plugins/*/{plugin,factory}.py`及类型import；对应base_plugin/runtime/插件测试。

**Interfaces:** `@handles(EventType)`绑定`async def method(self, source_plugin_id: str, event: E) -> Sequence[Event] | None`；
BasePlugin内部`dispatch(source_plugin_id,event)`唯一分发入口；`build_tools(self)->tuple[BaseTool,...]`默认空；
`provided_capabilities: tuple[type[CapabilityContract], ...]`为类级绑定。

- [x] **RED：** handler重复、类型错误、类与manifest不一致、非法返回事件失败；
  业务handler集合==业务consumed，能力handler集合==provide请求集合，两集合分开验证；能力Response禁止注册handler。
- [x] **迁移公开类型闭包：** InputAccepted/UserInput/Input*事件、Blackboard Context/Region事件、ProcessUpdatedEvent进入core/contracts；
  RegionInput/Output/State/Definition及事件字段引用的必要值类型一起迁移；Registry/Store/compactor/prompt实现留业务。
  Agent*Event留core.agents.types，TaskError/Event/run_control沿用现归属；删除graph `_import_event`反射。
- [x] **替换consume：** 原条件分支拆成具名@handles方法，保留task/source校验与原异步后台工作；
  “是否需要这个来源的此事件”仍可显式校验，但不留catch-all consume绕过声明。
  返回事件由dispatcher统一校验发送，延迟/后台产出仍publish；现manager发布守卫扩展而非移除。
- [x] **工具：** factory不再返回重复tools列表，PluginRegistration只返回plugin和state_provider/host端口；
  build_tools调用一次，注册后冻结，manifest名称严格相等。依赖具体内部helper的工具在插件内创建，不把helper登记子插件。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/test_base_plugin.py apps/agent/test/icarus/runtime/test_host.py apps/agent/test/agent_app/plugins -q`。
  此任务不同时删除Skill直接历史调用；T14在能力通路可用后做。逻辑提交边界：声明分发/工具注册。

## T12 · 单总线请求/响应，不阻塞自己的 inbox

**Files:** 新`A/icarus/core/capabilities/requester.py`；修改base/catalog、plugin.py、runtime/{event_bus,manager,worker,capability_registry}.py；
测试`AT/icarus/core/capabilities/test_requester.py`、`AT/icarus/runtime/test_capability_transport.py`。

**Interfaces:** §1.3。`EventBus.publish_by_capability(source_plugin_id, capability_id, event)`只按registry查地址投递；
响应经同一ingress的定向信封投递原调用者；普通订阅、链诊断、publish门禁不接收能力事件。
worker.enqueue先调用绑定的response_receiver，不增加第二个worker，不在总线按领域类型分支。

- [x] **RED：** `test_handler_can_await_response_with_real_serial_workers`：A handler await B，B返回，A在超时前完成；
  必须用真实EventBus+PluginRuntime队列，不用publisher同步回调consume作弊。
- [x] **实现pending：** 发送前登记Future，绑定correlation、caller、能力、选定provider、response类型、task_id；
  源身份来自绑定信封而非payload；request必须是caller require的event契约。response在目标普通入队前由helper完成Future。
  Provider handler异常由dispatcher生成FAILED；trace不得带异常敏感详情给客户端。
- [x] **测试失败通路：** 有限正timeout（0/NaN/inf失败）、默认30s、caller取消、提供者禁用、重复correlation、
  伪造source/错误类型/task、重复/迟到/未知响应；不得误完成pending或泄漏。超时TIMEOUT，未就绪/缺provider UNAVAILABLE。
- [x] **停机：** request阶段不是READY即立即UNAVAILABLE；quiesce拒绝新请求，close requester唤醒pending后再drain，避免stop等满超时。
  caller取消传播CancelledError，不暗示远端工作撤销，不加自动retry/cancel协议；请求环靠超时终止。
- [x] **同步宿主接缝：** 在core/ports.py定义`RuntimeLoopRunner`最小结构性接口，runtime/session.py实现绑定现有宿主loop的run；
  仅供T14同步工具适配，用run_coroutine_threadsafe，不增加总线/loop/线程。
  测试同loop线程立即拒绝、其他线程可往返、宿主未启动/已关闭失败、取消清理，不能使同步调用默默降级成不创建Job。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/capabilities/test_requester.py apps/agent/test/icarus/runtime/test_capability_transport.py -q`；
  检查pending最终为0、无额外队列/worker、无能力响应旁听者。逻辑提交边界：能力事件运输闭环。

## T13 · Agent / UserInput / Blackboard / Memory 的真实装配

**Files:** `A/agent_app/application/assembly.py`、`plugins/{agent,user_input,blackboard,memory}/{factory,plugin}.py`、
blackboard/regions.py、memory相关配置；上述四插件测试与`AT/agent_app/application/test_assembly.py`。

**Interfaces:** 输入/控制host端口采用§1.2/1.4；
Blackboard提供`@handles(ConversationReadRequest)`返回深拷贝ConversationReadResponse；
Memory新增`bind_region_handle(handle)`，返回/持有本插件RegionDefinition而不import Blackboard实现；
应用`async register_plugin_links(host: PluginRuntimeHost) -> None`在before_restore注册Region；
`async finalize_plugin_links(host: PluginRuntimeHost) -> None`在before_ready撤销失效owner并freeze。
跨插件的可撤销handle只用core中的`ReleaseHandle(Protocol).release()->None`，具体RegionRegistration仍留Blackboard插件。

- [x] **RED：** 无Agent提供者时Host明确失败；input与agent共享channel，取消与FIFO不变；
  conversation请求空/有历史/旧state恢复后值正确，不能改写Blackboard原Message。
- [x] **移除直连：** Agent/UserInput factories用基座设施，host端口登记task.control/input.submit；
  Blackboard不再把自身或RegionRegistry传给Skill/Memory。Memory内部上下文请求/结果仍为原普通业务Event，不强改成新能力。
- [x] **Region装配顺序：** 工厂/构建级联完成后，在before_restore注册定义并给owner绑定handle，**先有定义再恢复session_regions**；
  Blackboard.start不再过早freeze；所有start/restore/selfcheck+级联结束后，应用before_ready撤销已禁用owner的定义/快照，
  对最终集合initialize并freeze，再让Host启动bus/READY。会话Region恢复仍通过原state_provider，不推迟或绕过整个状态恢复流程。
  runtime只执行通用callback，不import或分支memory/blackboard；装配期调用不算插件运行期互调。
  新增`test_session_region_restore_survives_finalization`，确保持久化session Region不是因为定义注册晚了被静默跳过。
- [x] **回滚：** factory/start/restore/selfcheck失败、被级联禁用及finalize失败时撤销已注册handle并关闭已创建backend；
  base Blackboard缺失本就阻断。停机撤销handle须在对应freeze规则允许的位置或由应用清理整个注册图，不能遗留幽灵required Region。
  用`test_disabled_memory_does_not_leave_required_region`验证降级后普通输入仍完成。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/test_assembly.py apps/agent/test/agent_app/plugins/agent apps/agent/test/agent_app/plugins/user_input apps/agent/test/agent_app/plugins/blackboard apps/agent/test/agent_app/plugins/memory -q`。
  逻辑提交边界：四插件契约/装配切换。

## T14 · Skill 历史查询异步化，保持 Job 语义

**Files:** `A/agent_app/plugins/skill/{factory,plugin,tools,generation_context,generation_prompt}.py`、
`AT/agent_app/plugins/skill/{test_plugin,test_tools,test_generation_context,test_generation_integration,test_job_manager}.py`。

**Interfaces:** 移除constructor的`conversation: object`与get_messages探测；
`produce/evolve`在原参数上改`async def`，返回原dict，不新增Job DTO；请求ConversationReadRequest并unwrap，再拼接当前task_messages。
精确签名：

```python
async def produce(self, *, name: str, scope: SkillScope, instructions: str,
                  task_id: str | None, run_id: str | None, step: int | None,
                  task_messages: tuple[Message, ...]) -> dict[str, object]: ...
async def evolve(self, *, name: str, instructions: str,
                 task_id: str | None, run_id: str | None, step: int | None,
                 task_messages: tuple[Message, ...]) -> dict[str, object]: ...
async def _conversation_evidence(self, task_messages: tuple[Message, ...]
                                 ) -> tuple[Message, ...]: ...
```

五个公开工具名/参数/权限默认值不变。

- [x] **RED：** `test_produce_uses_committed_history_then_current_task`断言证据顺序、工具消息完整配对、脱敏不丢；
  `test_conversation_failure_creates_no_job`覆盖TIMEOUT/UNAVAILABLE/FAILED；空历史仍可按原规则执行。
- [x] **实现：** 权限/输入/目标冲突预检保持在查询前；异步查询后创建Job，停止前再检查不接受新工作。
- [x] **业务能力身份与同步桥：** caller身份绑定在Skill实例的requester上，不能从调用它的Agent执行器ambient上下文推导。
  `produce/evolve`工具显式覆写`ainvoke`并await，list/search/job_status仍本地操作。
  **现BaseTool只有ainvoke→to_thread(invoke)，并没有反向async→sync桥，不能假设已有。**
  T12新增`RuntimeLoopRunner.run(factory: Callable[[], Awaitable[T]]) -> T`，绑定宿主已运行loop；同步Skill工具invoke在其他线程通过
  `asyncio.run_coroutine_threadsafe`把同一ainvoke提交回宿主loop并等待；同宿主loop线程调用则明确拒绝并提示用ainvoke，loop关闭则UNAVAILABLE。
  不创建新loop/线程/worker，超时取消提交Future并清理pending；同步与异步路径复用相同参数校验与Job逻辑，不能在Skill里asyncio.run。
  新增测试：Agent忙时查询仍完成、来源确为Skill、线程中同步invoke等价于ainvoke、同loop误用不自锁、取消/loop关闭无Job泄漏。
- [x] **通知与私有资源：** TaskContextInputEvent/ResultEvent原Job通知仍普通事件；Skill生成Agent仍私有registry、受控Draft边界、配置权限、关闭资源。
  移除无消费者skill_management声明，不影响工具注册。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/skill -q`；
  增真实bus集成证明读取的是Blackboard而非SessionStore，不新增存储源。逻辑提交边界：Skill能力消费。

## T15 · Session Repository 与通用连接设施

**Files:** 新`A/icarus/runtime/storage.py`；移动`agent_app/application/{session_store,session_entities}.py`至`repositories/`；
新`repositories/session_records.py`、`models/status.py`、`errors.py`；测试`AT/icarus/runtime/test_storage.py`、`AT/agent_app/application/repositories/test_session_store.py`。

**Interfaces:** `SqliteDatabase(path: Path)`提供`async start()/close()`和`session_factory`；
仅管理engine、连接PRAGMA、WAL、async_sessionmaker，不知道业务表/Message/RuntimeUpdate。
`SessionStore`名称与原业务方法签名保留，拥有metadata.create_all、schema、原子取号SQL、连续性校验和软删除规则。

- [x] **RED：** 重启/并发同session追加sequence=1..N、不同session独立游标、回滚不留半条、旧标题表初始化、软删除后不能复用；
  新通用设施测试只用测试表，不import应用schema。
- [x] **抽取：** 仅连接生命周期和PRAGMA进入runtime；不要自建ORM/事务框架/通用BaseRepository。
  **原子sequence更新与Conversation插入必须仍在同一次sessions.begin里**，不能拆成两个所谓存储原语后丢原子性。
  SessionRecord移repository，SessionStatus等只读DTO移models，门面兼容导出同类。
- [x] **存储约束：** DB名icarus.db、表/主键/时间UTC/first_user_input/title/删除原因规则不变；
  旧文件目录拒绝规则不变，不做统一Message表/公共投影双写/ConversationStore Protocol。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/runtime/test_storage.py apps/agent/test/agent_app/application/repositories/test_session_store.py -q`。
  逻辑提交边界：存储设施与业务Repository。

## T16 · 会话注册、幂等和资源导入

**Files:** 新`A/agent_app/application/services/{session_registry,submission_guard,resource_importer}.py`、`models/session_entry.py`；
修改现agent_runtime.py，新增镜像services测试，保留test_agent_runtime集成测试。

**Interfaces:** SessionEntry保留identity、mutation_lock、title_lock、load/unload task、lifecycle、persistence_failed、tasks和幂等记录。
`SessionRegistry.entry(identity)->SessionEntry` async；`ensure_loaded(entry)->SessionRuntime` async single-flight；
`unload(entry, reason)->None` async；状态查询不等待mutation_lock。
`SubmissionGuard.fingerprint(prompt, resources, display_text)->str`、`find/remember`在调用方持锁时使用；
`ResourceImporter.import_resources(runtime, resources)->list[ImagePart] | None` async。

- [x] **RED：** 移用现有single-flight/等待者取消不取消共享load/共享失败/submit与unload竞争/状态查询不阻塞；
  同submission不同内容冲突、同内容返回同accepted，未接受steer不缓存；资源在accept前导入，重试不再读已删临时文件。
- [x] **提取：** 生命周期状态与锁拥有者只在SessionRegistry一处；SubmissionGuard不再自己申请第二把session锁；
  hash序列化与1024容量/淘汰规则不变；ResourceImporter沿用asyncio.to_thread及core.ResourceRef逃逸检查。
  不让services互相import SessionApi，跨service依赖用构造注入实际对象/少量回调。
- [x] **保持公开入口：** AgentRuntime暂委托新实现，现测试factory和clock注入仍能工作，不改返回模型。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/services/test_session_registry.py apps/agent/test/agent_app/application/services/test_submission_guard.py apps/agent/test/agent_app/application/services/test_resource_importer.py apps/agent/test/agent_app/application/test_agent_runtime.py -q`。
  逻辑提交边界：注册/提交依赖提取。

## T17 · 更新、历史、回收与对外 SessionApi

**Files:** 新`A/agent_app/application/{controllers/session_api.py,services/update_pipeline.py,services/history_projector.py,services/session_reaper.py,services/session_title.py}`；
修改application/__init__.py、现agent_runtime.py；对应镜像测试。

**Interfaces:** `UpdatePipeline.accept(update)->None` async只入队，`start()/stop()`生命周期，
`drain()`等待队列处理；`HistoryProjector.read(identity, *, after_sequence=0)->(records,cursor)`；
`SessionReaper.unload/delete/cleanup_idle`承接现行为；`SessionApi`完整保留§1.1入口，`AgentRuntime=SessionApi`门面alias。

- [x] **RED：** 用户消息持久化先于task.accepted处理；发布前落库；终态checkpoint失败先记录task.error再failed终态；
  落库失败取消任务/锁存failed/禁止后续提交；shutdown等更新队列处理完成；不因拆类发生重入锁。
- [x] **更新提取：** 将原_update_loop整体迁入UpdatePipeline后再做局部简化，保留task状态投影、transient集合和未知会话处理；
  使用Registry同一entry/锁，不把_update_loop改成回调同步执行造成submit锁重入。
- [x] **历史提取：** interrupted补终态、legacy delta合并与cursor保持原语义；查询历史不加载runtime、不刷新idle时间。
  公共thinking完整持久化但不进Message；HistoryProjector不去修改Blackboard消息。
- [x] **回收与标题：** 手动unload允许仅剩插件后台工作，idle cleanup仍尊重全部busy；删除要求无工作且保持soft-delete再移entry的防复活规则。
  保留title_lock、最多一次生成、模型失败回退、已有标题不再调用模型。不要为了Controller薄把标题锁移出共享entry。
- [x] **门面与GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application -q`；
  `make test-gateway`。19入口契约守卫继续PASS；删旧大类实现，只留必要同对象兼容导出，不能留下两套流程。
  逻辑提交边界：应用职责拆分，实施可按update/history/reaper分三个测试独立的子提交。

## T18 · Hook 的值通路、稳定顺序与异常回退

**Files:** `A/icarus/core/hooks/{base_hook,hook_dispatcher,hook_registry,hook_event}.py`；新`points.py`、`values.py`；
测试`AT/icarus/core/hooks/test_hooks.py`、`test_value_pipeline.py`。

**Interfaces:** `BaseHook.handle(event: HookEvent, value: T)->T`；`async ahandle(...) -> T`默认委托handle；
`HookRegistry.register(name, hook, *, priority: int)->bool`；
`HookDispatcher.trigger(name, phase, value, *, data=None)->T`与async atrigger同签名/语义。
`HookPoint`登记名称、phase、位置、值类型、复制和不变量校验函数；未知点不能隐式生效。

- [x] **RED：** 同优先级注册顺序、通配和具名合并排序、async串行而不是gather；同步/异步相同值链；
  None/错误类型/修改受保护字段失败，原地修改副本后抛错仍保持前一值；HookEvent frozen快照不受后续修改。
- [x] **实现：** 每Hook先隔离复制→调用→校验→提交；不改返回输入；None不是“不改”的暗号。
  捕获Exception记录并继续，CancelledError/BaseException传播；一次调用固定注册快照，运行中注册不插入已执行链。
  只接受可安全复制的值，不传锁/回调/run_control活对象；同步点禁止只提供async实现，不补asyncio.run桥。
- [x] **T18–T22原子接线批次：** 首先新增/测试Hook值通路与RecordSink契约，不立即删除旧trigger签名；
  T19–T22先迁移所有真实值与纯观测调用，最后同批删除旧trigger/atrigger和FileTraceHook。
  过渡实现只为有限迁移窗口，业务发布前必须仅剩新接口；不把纯观测误当value来兼容，不声称旧trace在中间态已经脱Hook。
- [x] **规则更新边界：** 实施本任务时同步修改AGENTS相关Hook红线，内容采用目标D10/D37/D38；
  本轮文档编写不直接改AGENTS，不把未批准的规则当当前约束。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/hooks/test_hooks.py apps/agent/test/icarus/core/hooks/test_value_pipeline.py -q`。
  逻辑提交边界：Hook核心契约与规则同步。

## T19 · Agent / LLM 真实值挂点

**Files:** core/hooks/wrappers/{observable_agent,observable_llm}.py、hooks/{points,values}.py；
测试`AT/icarus/core/hooks/wrappers/test_observable_wrappers.py`及agents stream测试。

**Interfaces:** 首批有效值点：

| 点 | 可改值 | 不可改控制量 |
|---|---|---|
| agent.invoke/stream.before | AgentCallValue：system_prompt/input_prompt/history_messages/input_images | tools白名单、role、run_control、task/run身份 |
| llm.invoke/stream.before | LLMCallValue：messages/tools内容 | tools集合/名称不扩大，调用数量不变 |
| agent.invoke.after | AgentResponse内容字段 | steps/usage/finish/task边界、工具调用身份/数量 |
| llm.invoke.after | LLMResponse消息内容 | usage/finish_reason/工具调用身份/数量 |

所有value用具名冻结dataclass包裹可复制数据，受保护字段由validator验证。
stream.after只记完成摘要，不新增可改值点；首批不提供逐chunk rewrite。

- [x] **RED：** Hook改prompt实际抵达stub LLM/Agent，返回内容实际返回调用方；
  None与空工具列表语义不混；Hook不可增加工具、改变finish_reason让循环提前结束；already-yielded流不被after回写。
- [x] **接线：** wrapper使用dispatcher返回值，不只发布一个观测快照；invocation和stream的before一致。
  metadata仍通过独立HookEvent.snapshot，不能从快照逆序列化真实参数；动态插件上下文原注入位置不变。
- [x] **历史一致性：** invoke.after若改内容必须同步返回message与对应messages尾项，校验task_message_start合法；
  stream只在请求前改值，不能在最终AgentCompleted事件重写已展示文本形成流/历史不一致。
  工具调用身份变化视为不合法Hook结果并回退。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/hooks/wrappers/test_observable_wrappers.py apps/agent/test/icarus/core/agents -q`。
  逻辑提交边界：Agent/LLM挂点。

## T20 · Tool 改值不得绕过策略与预算

**Files:** `A/icarus/core/tools/{tool_executor,execution_policy,result_budget}.py`、hooks/wrappers/observable_tool_executor.py、hooks/{points,values}.py、
core/agents/react_agent.py调度；测试`AT/icarus/core/tools/{test_execution_policy,test_tools,test_result_budget}.py`及wrapper测试。

**Interfaces:** tool.execute.before值为ToolCall（仅arguments可改，id/name固定）；
after值为ToolExecutionResult（output/error/images可改，success和权威执行metadata固定）。
`prepare_group`保持sync，新增`aprepare_group`供async链；prepared调用显式传递，不用call_id隐式缓存“Hook跑过”。

- [x] **RED：** 改后的参数影响并行资格；同call参数Hook仅一次；_execution预算请求经策略再次封顶；
  Hook放大结果后单调用/整组预算仍生效；不能把timeout/拒绝改成功；direct/group、sync/async、executor.snapshot行为一致。
- [x] **调整前置顺序：** 参数Hook→原group数量限制/参数prepare→根据最终参数判并行→执行。
  当前wrapper.execute之前已分批，不能只在该wrapper加before；ReactAgent组准备调用同步/异步分别接新入口。
- [x] **调整后置顺序：** 归一化真实结果→结果Hook→附加权威execution metadata→最终group预算/完整结果外置/截断。
  策略合成的拒绝/超时/取消结果只记录，Hook不能篡改执行事实；禁止after绕过finalize_group。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/tools apps/agent/test/icarus/core/hooks/wrappers apps/agent/test/icarus/core/agents -q`。
  逻辑提交边界：工具执行次序与预算不变量，不改工具业务实现。

## T21 · 记录器独立于 Hook，后端仍可替换

**Files:** 新`A/icarus/core/recording.py`、`A/icarus/runtime/recording.py`；
修改persistence/{plugin,factory,trace_record,trace_writer,log_handler}.py；删除trace_hook.py及旧runtime聚合残余；
core wrappers注入RecordSink；测试`AT/icarus/runtime/test_recording.py`、persistence trace/log测试。

**Interfaces:** `RecordSink.record(point: str, phase: str, *, data: Mapping[str, object]) -> None`同步轻量入口；
`RecordingBackend.start()/offer(record)->bool/drain()/stop(drain=True)`协议在core；
runtime Recorder接上下文快照与已注册backend，persistence提供JSONL adapter/脱敏/log绑定。
backend以PluginRegistration.backends独立字段登记并校验，不能绕开注册表传私有writer给core。

- [x] **RED：** 无Hook仍记录，Hook注册不额外增加全链记录；后端错误不阻断业务、不递归记录；
  溢出/关闭offer按既有writer行为统计，不顺手发明丢弃策略；flush_every>1停机最后flush/close。
- [x] **记录点与聚合固定：**
  - task.operation、task/run终态、plugin.lifecycle及错误保留明细；LLM每次完成记耗时/usage/finish/字符数，不含全文/chunks。
  - tool完成在最终预算后记耗时、disposition、截断/文件可用性；group记预算汇总，不复制结果正文。
  - event.publish/route、plugin.consume、background成功按(session,run,plugin,event_type,point)计数；错误立即记。
  - 尊重trace_event_flow=False；thinking与assistant全文不重复进trace。计数桶达1024或run终态/卸载flush，限制桶数1024，淘汰最旧桶先输出，非静默丢计数。
- [x] **后端适配：** 保留trace.jsonl路径与现schema_version=2外壳字段；新record_type=`runtime_record`，name/phase/context/data稳定，
  不再伪称hook_event；保留旧行可解析，trace不是模型恢复格式。Redactor在后端，路由身份不混入普通context。
  不新增文件轮转、压缩、fsync或落盘原子性承诺。
- [x] **停机屏障：** 后端由Host先启动、最后停止；插件反向stop不能提前关闭writer。
  quiesce/drain→快照/插件stop尾记录→flush聚合→关闭接收→backend.stop(drain=True)→日志解绑。
  core只知RecordSink，不能import runtime Recorder；记录不经过EventBus/Hook，不包装backend本身。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/runtime/test_recording.py apps/agent/test/agent_app/plugins/persistence -q`。
  逻辑提交边界：记录机制/插件后端分离。

## T22 · runtime 值挂点、观测接线与事件链推导

**Files:** runtime/wrappers/{observable_event_bus,observable_plugin_runtime}.py、worker.py、assembly.py、diagnostics.py；
core/hooks/points.py、core/recording.py；Skill/Memory/Process/MCP中仅观测用_hook调用改RecordSink；
测试`AT/icarus/runtime/wrappers/test_runtime_observability.py`、`test_diagnostics.py`。

**Interfaces:** plugin.consume.before只可替换同类Event的业务payload，不改source/destination/event_id/task_id/correlation/能力类型；
消费后的“after”、route/publish/lifecycle/background/task.operation不伪造可改值，全部走记录器。
额外挂点需显式登记类型/值位置，无“未知字符串也运行”的后门。

- [x] **RED：** inbox前改值实际被handler消费，不能换事件类型/路由身份/关联；
  响应配对仍在普通inbox前，不能被Hook伪造pending；记录路径不会再次产生业务事件或自我Hook。
- [x] **接线：** 普通事件的before值挂点放既定投递/消费位置，过滤和安全身份校验仍生效；
  对能力的可写内容严格受请求/响应契约约束，相关身份全部只读；记录能力流量不自动订阅响应。
  将所有纯观测_hook/trigger调用迁到RecordSink，不只处理最早统计出的12个名称；禁止未知Hook点运行后悄悄失去Skill观测。
- [x] **推导诊断：** 按业务published/consumed ID交集构建边，输出无人消费、环/分支与链视图；
  环仅诊断不当成resolver能力依赖环；能力事件排除，不引入新链声明或执行顺序。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/runtime/wrappers apps/agent/test/icarus/runtime/test_diagnostics.py apps/agent/test/icarus/runtime/test_capability_transport.py -q`，再全部插件。
  逻辑提交边界：runtime接线与诊断。

## T23 · 历史分页下沉，显式 wire 模型

**Files:** controllers/session_api.py、services/history_projector.py、models/history.py；
`G/protocol/{methods,models}.py`、`packages/gateway_protocol/models.py`（只改TYPE_CHECKING必要路径）；
`GT/test_methods.py`、`AT/agent_app/application/services/test_history_projector.py`。

**Interfaces:** §1.1 HistoryPage/get_session_history_page；Gateway本地明确SessionStatusModel/TaskStatusModel/UnloadResultModel，
采用from_domain方法转换，wire字段与旧_wire输出完全相同；既有共享SessionHistoryModel用于真实响应校验。

- [x] **RED：** 默认200、limit1/500、0/501拒绝；空页next_after_sequence不回退；
  legacy合并导致逻辑sequence跳号仍正确has_more/next；重复查询不再追加重复interrupted；旧二元组方法继续可用。
- [x] **实现：** 应用先做恢复/投影再切片，返回HistoryPage；旧get_session_history调用相同未分页投影；
  Gateway只传参数/转换，不再records[:limit]或计算has_more，不为了性能顺手改变历史恢复算法/一致性。
- [x] **wire：** History构造SessionHistoryModel，不绕过它手写不受校验dict；状态/任务/卸载显式模型不从domain泄露未来字段。
  不把domain对象改成Pydantic破坏原调用方；共享wire包保持不在运行期import Agent。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/services/test_history_projector.py -q`、
  `apps/gateway/.venv/bin/python -m pytest apps/gateway/test/test_methods.py -q`、`make test-tui`。
  WebUI无需代码改动，现history客户端协议仍相同。逻辑提交边界：分页/wire兼容。

## T24 · 统一有界 mailbox/fanout 与容量来源

**Files:** 新`A/icarus/runtime/fanout.py`；修改app runtime_update_stream.py、config_model.py、SessionApi；
`G/{connection,app}.py`；测试`AT/icarus/runtime/test_fanout.py`、app流测试与Gateway连接测试。

**Interfaces:** `DEFAULT_QUEUE_CAPACITY=4096`唯一默认常量；
`BoundedMailbox[T](capacity)`提供`offer(item)->bool`、`async next()->T`、`close(error=None)`；
`BoundedFanout[T].subscribe()/publish()/close()`管理独立mailbox，不阻塞快消费者。
`RuntimeSettings.update_queue_capacity`默认取该常量；保留AgentRuntime/update和create_app/connection显式capacity覆盖兼容。

- [x] **RED：** capacity<=0失败；慢订阅溢出只关闭自身，快订阅顺序不变；close唤醒等待者；
  WebSocket响应与通知仍共用同一发送队列，满时1013关闭，不因两种消息复用原语而双重发送。
- [x] **实现：** app RuntimeUpdateStream封装通用Fanout并保留RuntimeUpdateSubscription/OverflowError原门面；
  GatewayConnection发送队列复用mailbox，ConnectionHub继续负责(workspace_key,session_id)过滤，不把业务过滤塞进原语。
  一个配置值为默认路径供应用与Gateway使用，调用方显式覆盖可不同；不把旧两个override静默捆绑成一项。
- [x] **容量接线：** 一个配置值为默认路径供应用与Gateway使用，调用方显式覆盖可不同；
  保留原update_queue_capacity/connection_queue_capacity构造参数，不把旧两个override静默捆绑成一项；
  分别测试无参默认与两个显式不同容量，确认不会误读全局可变配置。
- [x] **GREEN：** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/runtime/test_fanout.py apps/agent/test/agent_app/application/test_runtime_update_stream.py -q`；
  `make test-gateway`。生产代码不再四处出现4096独立默认字面量。逻辑提交边界：扇出原语与配置。

## T25 · 全插件封边界、mypy 与真实集成验收

**Files:** 11插件factory/plugin/manifest、Agent pyproject与scripts/typecheck.sh、CI、打包/导入守卫、
`GT/test_runtime_contract.py`；补齐MCP/Knowledge/Process/Builtin相关测试与声明。

**Interfaces:** 全部canonical路径与注册契约冻结；mypy先core/runtime再agent_app，配置逐包纳入，不使用全局ignore_errors冒充通过。

- [x] **插件逐项闭环：**
  - persistence：仅状态后端和记录/log后端；assets/identity/Redactor不再作为能力；trace停止顺序满足T21。
  - builtin-tools：build_tools提供read/write/insert/bash，Windows helper随包，AgentAssembly不二次注册。
  - agent/user-input/blackboard/memory：T13端口/共享channel/Region/请求/声明集完整，无具体兄弟类import。
  - skill：T14异步历史查询、私有工具与Job关闭、五工具名称/参数/权限一致，无skill_management死声明。
  - mcp：factory/backend/client_manager/result_converter依赖core设施/utils；同步图片导入、稳定三工具、schema/catalog/断连错误语义不变。
  - knowledge：HTTP backend/只读分页/工具schema不变；仅handler/build_tools/config声明适配，不引入新能力。
  - process：factory/manager依赖新设施；会话隔离、cursor、日志限额、quiesce/stop取消子进程保持原行为，不顺手修改进程策略。
  - runtime-update：所有普通事件来自core契约，payload/脱敏/预览/thinking隔离不变，不参与能力响应旁听。
- [x] **边界与类型RED/GREEN：** AST检查无插件兄弟实现import、utils不importplugins/application、core不importruntime/app、框架不import SDK；
  唯一允许跨插件具体类型连线在agent_app/application/assembly.py，不在runtime。
  mypy优先收core跨包接口、端口、请求/响应、BaseTool；第三方无stub只对对应外部模块设置ignore_missing_imports，不能屏蔽内部模块类型错误。
- [x] **真实离线集成：** 两session隔离、提交/steer/cancel、流/完整消息、工具、checkpoint/unload/resume、删除、标题、overflow；
  Gateway用真实SessionApi+stub模型/服务而不只RuntimeStub。追加伪造响应、降级Memory无幽灵Region、stop尾记录用例。
- [x] **安装验收：** Agent/Gateway两私有环境、CI临时环境、构建wheel安装各自验证非repo cwd导入/资源/11manifest；
  框架导入屏蔽厂商SDK仍成功；TUI首帧不加载新框架/app。检查旧字符串/TYPE_CHECKING/monkeypatch无遗漏。
- [x] **全量GREEN：** `make test-agent` → `make test-gateway` → `make test-tui`；
  `apps/agent/.venv/bin/python -m mypy apps/agent/src/icarus apps/agent/src/agent_app`；
  `apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test apps/gateway/src packages`；`git diff --check`。
  WebUI按现有应用测试命令跑协议消费回归（从apps/webui/package.json确认现有脚本，不猜命令）。
  无真实模型调用，除非另获费用/服务授权。失败按测试身份与T01基线比较，不修无关项。逻辑提交边界：逐插件收口/最终守卫，可按插件分组提交。

## T26 · 文档、兼容入口和最终交接

**Files:** 本feature四文档、README.md、CONTRIBUTING.md、AGENTS.md、相关旧feature现状架构的引用/过时段落；
删除无消费者旧shim，更新脚本帮助/测试路径。

**Interfaces:** 当前arch只描述已通过源码/测试核实的新实现；目标决策仍保留历史与偏差说明，task完成勾选须附实际证据。

- [x] **清理：** 生产/测试已统一canonical后删除临时apps.agent.src shim；如有外部用户依赖只保留公开门面同对象re-export并写弃用范围，不保留两份类型实现。
  不保留旧graph_builder/_import_event/consume兜底/TraceHook双通路。
- [x] **文档：** 解释双venv安装、settings位置/覆盖规则、模型角色迁移、manifest v2/事件ID/能力访问模式、Hook点和值类型、记录点与容量配置；
  显式说明没有统一历史源、没有热插拔/第二总线、何时考虑框架上提；修改旧当前架构中“Hook观测”等过时描述，不重写历史计划为已执行。
- [x] **核对D1–D42：** 使用§3覆盖表，所有偏差都有用户确认或计划作者提案获批后的记录；
  runtime-snapshot.json继续作为诊断快照保存，不接模型恢复、无用户授权不删除旧文件。
- [x] **GREEN/交付验证：** 文档相对链接存在、任务中的文件与命令可定位、无虚假PASS；`git diff --check`；
  源码有改动则仍按T25真实验收，不能以文档检查替代。交接完成后等待用户决定Git集成，不自动commit/merge/push。

## 3. 决策覆盖与已明确不做的事

| 决策 | Tasks | 覆盖说明 |
|---|---|---|
| D1 | T02–T04、T26 | 两包、canonical身份、上提条件 |
| D2 | T03、T05 | 类型/接口与实际厂商创建拆开 |
| D3 | T06 | 数组、唯一名字、全部角色调用点 |
| D4 / D40 | T08、T13、T16–T17 | Host执行、应用触发/装配 |
| D5 | T15–T17 | Controller/Service/Repository；不是重写 |
| D6（修订） | T01、T07、T15、T17 | 设施/后端切分，保留存储语义，不新增ConversationStore |
| D7 | T03–T04、T25 | DTO进core、生产者留插件、wire不变 |
| D8 / D42 | T07–T08、T12–T14、T25 | 基座设施/叶子utils/运行期通信/装配例外 |
| D9 | T09–T10、T13 | tier+required、失败/降级/回滚 |
| D10 / D34–D37 | T18–T20、T22、T26 | 值通路、顺序、隔离回退、位置约束、规则同步 |
| D11 / D13 | T09、T11–T12、T22 | 稳定类型ID、业务链视图、能力不进订阅 |
| D12 | T23 | 新分页兼容入口，Gateway协议转换 |
| D14 | T24 | mailbox/fanout，单一默认4096 |
| D15–D17 / D19 / D23 | T09–T12、T21 | 独立注册、权威目录与六类manifest |
| D18 / D29–D33 | T09–T10、T12–T14 | event能力与host直连接缝，唯一实际RPC消费者 |
| D20–D22 / D24–D27 | T05、T09、T11、T25 | 工具/handler/能力/config一致性 |
| D28 | T08、T10、T13 | 生命周期、自检、降级、最终装配/回滚 |
| D38 | T21–T22 | 记录机制/插件后端、高频聚合、停机flush |
| D39（修订） | T05、T08、T15–T17、T25 | 真接缝才立接口，mypy覆盖 |
| D41 | T02、T04、T25 | 双venv/CI/资源/外置配置与wheel |

**本次明确不做：** 统一模型/UI历史源、新Message数据库表、数据迁移、旧历史伪造补齐、工具权限系统、
运行中热插拔、插件RPC重试/广播、多worker并发消费、全仓库打包、Provider协议升级、自动上游同步、自动真实模型调用、自动Git变更。

## 4. 完成定义与任务记录

每项完成时记录：变更文件、最小测试命令/退出码、应用回归、compile/diff结果、与T01基线差异、尚存风险。
不得仅把复选框勾完而没有执行证据；任务可因真实代码变化拆小，但不能省略Interface/测试或越过依赖。

执行状态以plan-validation.md为准：官方Agent/Gateway、安装/wheel、mypy和真实远端模型端到端已运行；TUI既有13项快照失败保持不变。
原计划中的接口细节与最终裁决有差异时，以arch.md和验证记录列出的明确偏差为准。未提交、未推送。
