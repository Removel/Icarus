# relationship 插件实施计划

| 项目 | 内容 |
|---|---|
| 日期 | 2026-10-18 |
| 状态 | 已实施；最终测试与真实 E2E 记录见文末 |
| 依据 | [design.md](design.md) |
| 目标 | 在 Agent 中增加 tool-only relationship 插件，不自动召回 |
| 前置 | rela-mem 搜索候选返回有限 aliases |

> 本文件保留任务顺序并在文末记录实施证据；2026-10-18 已完成审查及用户决策确认，源码实现不自动提交/推送。

## 0. 约束

- 只改 `apps/agent` 和必要的 `apps/rela-mem` 搜索返回；不改 Gateway/TUI/WebUI。
- 插件是普通一级 Plugin，不注册子 Plugin。
- 不订阅 Event，不注册 Region，不加入 `required_plugin_ids`。
- Tool 参数只保留意图和数据；来源、时间、方向、默认值、组合查询由代码处理。
- 工具失败不阻塞 Session；服务不可用只影响当前 Tool。
- 不放 Secret 到配置、日志或 ToolResult。
- 每个任务先写行为测试，再实现。

先加载 `superpowers:test-driven-development` 再执行任务。

## 1. 目标文件

```text
apps/rela-mem/
├── src/repositories/person_repo.py          # 搜索返回 aliases
├── src/domain/services/person_service.py    # 如需要，透传
└── test/integration/test_persons.py          # 候选别名回归

apps/agent/
├── src/agent_app/plugins/relationship/
│   ├── manifest.json
│   ├── factory.py
│   ├── plugin.py
│   ├── config.py
│   ├── backend.py
│   ├── rela_mem_http_adapter.py
│   ├── models.py
│   ├── tools.py
│   └── prompts.py
├── src/agent_app/resources/default_settings.json
└── test/agent_app/plugins/relationship/
    ├── test_adapter.py
    ├── test_factory.py
    ├── test_plugin.py
    ├── test_tools.py
    └── test_prompt_contribution.py
```

## 2. Task 0：rela-mem 搜索候选别名

**Files**

- Modify: `apps/rela-mem/src/repositories/person_repo.py`
- Test: `apps/rela-mem/test/integration/test_persons.py`

**Interfaces**

- `GET /v1/persons?query=` 每项增加 `aliases`：
  ```json
  [{"alias_id": "a_...", "namespace": "qq", "value": "123456", "display_value": "小张"}]
  ```
- 每项最多 10 个别名；不返回 `normalized_value` / `identity_key`。
- 分页、过滤、权限不变。

**Steps**

- [ ] 写失败测试 `test_search_candidates_include_bounded_aliases`：两个同名人物各自返回自己的别名，未命中为空。
- [ ] 运行测试，确认失败原因是缺少 `aliases`。
- [ ] 修改列表 Cypher：`OPTIONAL MATCH (p)-[:HAS_ALIAS]->(a:Alias)`，聚合后截断 10 个，返回安全字段。
- [ ] 运行该测试及 `make test-rela-mem-integration`。
- [ ] 更新 `apps/rela-mem` 设计/计划中的搜索响应说明。

## 3. Task 1：插件骨架、配置、注册

**Files**

- Create: `manifest.json`, `config.py`, `factory.py`, `plugin.py`
- Modify: `default_settings.json`
- Test: `test_factory.py`, `test_plugin.py`

**Interfaces**

- `create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger) -> PluginRegistration`
- `RelationshipPlugin(plugin_id, backend)`；`start/stop` 只管理 client 生命周期。
- `RelationshipConfig`：`endpoint: str = "http://127.0.0.1:8767"`，`timeout_seconds: int = 10`，`extra="forbid"`。
- Token：`os.environ["RELA_MEM_API_TOKEN"]`；非 loopback 且为空时失败。
- Manifest 不发布/消费事件，`required_config: []`，`tier: "core"`。
- `default_settings.json` 增加：
  ```json
  "relationship": {"endpoint": "http://127.0.0.1:8767", "timeout_seconds": 10}
  ```
- 不加入 `required_plugin_ids`。

**Tests**

- `test_factory_accepts_defaults_without_health_check`：Factory 不发起 HTTP。
- `test_factory_rejects_unknown_config_and_missing_remote_token`。
- `test_plugin_start_stop_closes_backend`。
- `test_manifest_declares_exactly_five_tools_and_no_events`。

## 4. Task 2：Backend Protocol 与 HTTP Adapter

**Files**

- Create: `backend.py`, `models.py`, `rela_mem_http_adapter.py`
- Test: `test_adapter.py`

**Interfaces**

```python
class RelationshipBackend(Protocol):
    def search_people(self, name: str) -> FindResult: ...
    def get_person(self, person_id: str) -> PersonSummary: ...
    def resolve_identity(self, identity: str, *, include_archived: bool = False) -> PersonSummary | None: ...
    def find(self, query: str) -> FindResult: ...
    def context(self, person_id: str, query: str | None = None) -> ContextResult: ...
    def create_person(self, display_name: str, identities: list[str]) -> PersonSummary: ...
    def add_alias(self, person_id: str, identity: str) -> None: ...
    def write_profile(self, person_id: str, key: str, value, source_ref: str) -> None: ...
    def forget_profile(self, person_id: str, key: str, source_ref: str) -> None: ...
    def upsert_relation(self, **fields) -> RelationResult: ...
    def invalidate_relation(self, relation_id: str) -> None: ...
    def close(self) -> None: ...
```

- `RelaMemHttpAdapter` 使用 `httpx.Client`；同步方法，供 Tool 调用。
- 认证头 `Authorization: Bearer <token>`。
- 错误映射为 `RelationshipBackendError`，安全文本。
- 不改 rela-mem 的 Python 代码。

**Adapter 测试**

- `test_find_name_calls_search_with_limit_10`。
- `test_find_prefixed_identity_calls_identity_resolve`。
- `test_context_composes_four_reads_and_bounds`。
- `test_remember_prefixed_identity`。
- `test_write_profile_sends_source_agent_and_ref`。
- `test_error_mapping_does_not_leak_token_or_body`。
- `test_close_closes_client`。

## 5. Task 3：find/context Tools

**Files**

- Create: `tools.py`（先实现两个只读 Tool）
- Test: `test_tools.py`

**Interfaces**

- `relationship_find(query: str)`：唯一参数；前缀解析在 Adapter/Tool 内完成；结果带 `ambiguous`。
- `relationship_context(person_id: str, query?: str)`：固定组合四个读请求，返回有界上下文和 `truncated`。
- 参数校验用现有 Tool 参数风格；错误返回 `ToolExecutionResult(False, error=...)`。

**Tests**

- `test_find_name_returns_candidates_with_aliases`。
- `test_find_prefixed_identity_returns_single_or_empty`。
- `test_context_returns_bounded_person_profile_circles_relations`。
- `test_context_rejects_unknown_person`。
- `test_tools_reject_unknown_arguments`。

## 6. Task 4：remember/forget Tools

**Files**

- Modify: `tools.py`
- Test: `test_tools.py`

**Interfaces**

```text
relationship_remember(
  person_id?: str,
  display_name?: str,
  identities?: str[],
  profile?: object,
  confirm_create?: bool = false,
)
relationship_forget(person_id: str, keys: str[])
```

- `source` 固定 `agent`；`source_ref = f"agent:{task_id}:{run_id}"`，没有执行 ID 时用 UUID。
- 只有精确身份无匹配且没有姓名候选时自动创建；存在候选先确认；名字无匹配且 `confirm_create=false` 返回 `confirmation_required`。
- 多个同名候选返回 `confirmation_required`，不写。
- 全部 identities 预解析并核对目标，409 只在复查归属同一 active person_id 时忽略；其余停止，不继续写档案。
- 名字仅精确同名且唯一且候选完整时直写；新身份无命中但有姓名候选先确认（2026-10-18 用户已确认）。
- name-only 创建不保证跨 Session exactly-once；不自动重试写操作，失败可部分完成。
- 详见批准执行计划；补全 `__init__.py`、get_person、严格配置/HTTPS、Memory 条件指导、打包13 manifests/33 tools、真实 SessionRuntime 集成和隔离真实模型 E2E。
- `forget` 只追加墓碑。

**Tests**

- `test_remember_exact_identity_creates_missing_person`。
- `test_remember_name_without_match_requires_confirmation`。
- `test_remember_name_with_confirmation_creates_once`。
- `test_remember_ambiguous_name_does_not_write`。
- `test_remember_writes_profile_with_agent_source`。
- `test_remember_alias_409_requires_same_active_owner`（同人继续、异人/归档停止，不写档案）。
- `test_forget_appends_tombstones_with_agent_source`。
- `test_remember_rejects_person_id_and_display_name_together`。

## 7. Task 5：relate Tool

**Files**

- Modify: `tools.py`
- Test: `test_tools.py`

**Interfaces**

```text
relationship_relate(
  relation_id?: str,
  from_person_id?: str,
  to_person_id?: str,
  kind?: str,
  forward_detail?: str,
  reverse_detail?: str,
  scope_circle_id?: str,
)
```

- `relation_id` 存在 → invalidate；其他写字段必须为空。
- 否则 upsert；`direction = two_way if reverse_detail else one_way`。
- upsert 为 `source=agent` 且 `source_ref` 自动；失效 API 不记录此次来源，返回独立 invalidated 结果。
- 双向必须同时有正向；缺失返回参数错误。

**Tests**

- `test_relate_infers_two_way_from_reverse_detail`。
- `test_relate_infers_one_way_without_reverse`。
- `test_relate_with_relation_id_invalidates_only`。
- `test_relate_rejects_mixed_invalidate_and_upsert_fields`。
- `test_relate_rejects_missing_required_upsert_fields`。

## 8. Task 6：静态 Prompt 与注册

**Files**

- Create: `prompts.py`
- Modify: `manifest.json`, `default_settings.json`
- Test: `test_prompt_contribution.py`, `test_factory.py`

**Interfaces**

- `RelationshipPlugin.static_prompt_sections()` 返回一个 `PromptSection(section_id="relationship.guidance", layer="session", content=...)`。
- 内容包含：事件/档案区分、人物查询、确认创建、删除边界、关系方向。
- Manifest 的 `provided_tools` 与 Factory 注册的 Tool 完全一致。
- 不在 `required_plugin_ids` 中。

**Tests**

- `test_static_prompt_contains_event_profile_split_and_confirmation_rule`。
- `test_manifest_tools_match_factory_registration`。
- `test_plugin_has_no_event_bindings_or_regions`。

## 9. Task 7：集成与真实冒烟

**Files**

- Test: `test_adapter.py` / `test_tools.py` / optional Gateway e2e
- Scripts: optional `apps/agent/scripts/smoke_relationship.py`

**Steps**

- [ ] 用 `httpx.MockTransport` 跑完整 Tool 流程：find → context → remember → relate → forget。
- [ ] 用真实本地 rela-mem 做可选 smoke：启动服务，调用 find/context/remember/relate/forget，检查数据。
- [ ] 用 Agent/Gateway 跑一次真实对话：用户提到人物，Agent 调用 `relationship_find`；用户要求记档案，Agent 调用 `relationship_remember`。
- [ ] 服务停止时，Session 仍启动，其他 Tool 正常，relationship Tool 返回安全错误。
- [ ] 运行 `make test-agent`、`make test-rela-mem-integration`、`python -m compileall`、`git diff --check`。

## 10. 验证顺序

1. 最小测试文件；
2. `apps/agent` 全量测试；
3. `apps/rela-mem` 全量测试；
4. 编译检查；
5. `git diff --check`。

## 11. 验收

- 5 个工具可通过 Agent 调用；
- 事件/档案不混写；
- 名字歧义不自动创建；
- 精确身份可自动建档；
- 关系方向正确；
- 服务离线不阻塞 Session；
- 无自动召回、Region、启动门闩；
- Agent 静态工具从 28 增至 33；
- 不修改 ReActAgent、Plugin Runtime、Gateway、TUI、WebUI。

## 12. 确认与实施记录

1. 2026-10-18 文档审查后，用户确认：仅精确同名且唯一直写；新身份遇已有姓名候选先确认。别名409需复查同一active owner。
2. 已一次实现5工具，不拆为只读阶段；无自动提交/推送。
3. 用户批准完整实施计划及隔离真实验收，并两次批准有上限的额外模型请求；未使用生产数据。

## 13. 2026-10-19 验证结果

- 插件测试68项通过；完整 Agent `make test-agent` 为971 passed，Gateway为34 passed，rela-mem真实隔离数据库全量为49 passed。
- `make typecheck-agent`：248个源文件无问题；compileall与git diff --check通过。
- 标准 `make check-package-agent` 因PyPI TLS EOF下载构建依赖失败，不标为通过。使用已有依赖 `build --no-isolation --wheel` 成功，离线安装至临时环境，验证13 manifests、插件模块、默认资源和5工具注册。
- 独立审查发现含冒号姓名误走身份解析、GET人物响应ID不一致、姓名证据缺失仍直写；均先写失败回归再修复，3项RED→GREEN，最终全量971项通过。
- 真实REST验收覆盖5工具、精确身份、姓名确认、唯一精确同名、模糊不写、已有姓名新身份先确认、第11个姓名别名证据、身份冲突不写、圈子、单/双向关系、墓碑、关系失效和24000字符上下文预算。
- 真实链路为WebSocket RPC→生产Gateway→AgentRuntime→真实deepseek-flash→5工具→真实rela-mem/FalkorDB/PostgreSQL。最终单任务4次模型请求，5工具全部成功、task.finished=completed，真实档案历史/墓碑和关系invalid状态核验通过。
- 服务停止后另建Session，真实模型2次请求完成relationship_find安全失败、独立read成功、正常task终态。
- 保留失败记录：初轮在线两turn通过后，离线task失败，因未保留完整轨迹不能推定根因；第二轮在线5工具全部成功但6次预算在总结前耗尽，保护器使task失败；最终合并单任务后4次完成。没有隐去或伪称此前失败轮次通过。
- 汇总证据见 [acceptance.json](acceptance.json)。本次仅使用专用容器、临时Token、随机测试图/schema和独立Gateway数据目录；不修改生产配置、不访问用户库。

### 已知边界与暂缓项

- 姓名唯一是本次观测，不保证跨Session exactly-once；HTTP timeout为单请求，不承诺取消回滚或批量原子性。
- 搜索aliases为最多10条展示摘要，精确截断元数据暂缓；写入前独立读取完整证据，不据该摘要判断姓名唯一。
- 圈子上下文保留circle.name/role/ID，membership alias/identity_label尚未暴露，后续按需补齐。
- 重复远端查询因Git TLS错误失败，不能声称已同步最新feature；已知origin/feature包含于HEAD。本次未创建分支、commit、merge、rebase或push。
