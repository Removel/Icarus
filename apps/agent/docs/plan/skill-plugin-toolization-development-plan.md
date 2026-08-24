# SkillPlugin Toolization Development Plan｜SkillPlugin 工具化重构开发计划

## 目标

基于 `apps/agent/docs/arch/skill-plugin-design.md`，把当前自动检索、Blackboard 注入和轮后自动
维护的 `SkillPlugin`，重构为由主 Agent 主动调用的 Skill 管理插件。完成后：

- Runtime 启动时向共享 `ToolRegistry` 注册五个 Skill Tool；
- 主 Agent 按需 list/search，再通过通用 `read` 阅读完整 `SKILL.md`；
- produce/evolve 是显式、受权限控制的后台 Job；
- 搜索只使用确定性的简单关键词模糊匹配；
- Producer/Evolver 获得此前 Session 历史和当前 Task 截至 ToolCall 的完整脱敏证据；
- Job 结果通过现有 `TaskContextInputEvent` 通知仍活跃的主 Agent；
- 不再保留 Embedding、使用次数、生命周期排名、会话累计注入和自动维护链路。

## 实施边界

- 不修改无状态 `ReActAgent` 的领域逻辑；
- 不给 Plugin Runtime 或 EventBus 增加 Skill/Job 专用分支；
- 不增加 `skill_read`，完整正文继续由通用 `read` Tool 提供；
- `SKILL.md` 只描述如何组合当前 Run 已有 Tool，不在 Run 中途动态注册新 Tool；
- 单次 Agent Run 继续使用启动时取得的稳定 Tool 快照；
- Hook 只观测，不参与权限、调度或提交；
- 不实现 Skill 删除、合并、自动召回、自动演化和跨进程 Job 恢复；
- 不修改或清理当前工作树中的 TUI Logo 相关变更。

## 目标运行链路

```text
Skill manifest provided_tools
→ Skill factory 返回五个 BaseTool
→ PluginRuntimeHost 校验并注册到共享 ToolRegistry
→ ReActAgent 为当前 Run 创建 ToolExecutor 快照
→ 模型看到五个 Skill Tool 定义

skill_search
→ SkillCatalog 扫描 global + workspace
→ 关键词匹配轻量元信息
→ 返回 name / description / scope / path
→ 主 Agent 使用通用 read 获取 SKILL.md

skill_produce / skill_evolve
→ 同步完成权限、参数和目标前置检查
→ 保存 queued Job 并立即返回 job_id
→ Producer/Evolver 后台生成单个 SKILL.md
→ 脱敏、严格解析、Repository 提交
→ Job 进入终态
→ TaskContextInputEvent 通知活跃主 Agent
```

## 迁移策略

采用“新领域组件先落地，最后切换 Plugin 入口并删除旧链路”的顺序。前几个任务允许新旧组件
短暂并存，但不能让两套行为同时接入 Runtime。切换 Manifest 与 Factory 后，立即删除旧实现和测试，
避免保留无调用方的兼容层。

## 任务一：收敛 Skill 模型、扫描与 Catalog

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/skill/models.py`
- `apps/agent/src/agent_orchestration/plugins/skill/scanner.py`
- `apps/agent/src/agent_orchestration/plugins/skill/__init__.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_scanner.py`

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/skill/catalog.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_catalog.py`

**实现内容**

- 将 `SkillDefinition` 收敛为发现和搜索真正需要的字段：
  - `name`；
  - `description`；
  - `keywords: tuple[str, ...]`；
  - `scope`；
  - 规范化绝对 `path`。
- 删除 `SkillUsage`、`RankedSkill`、`SessionSkillUpdate`、生命周期和注入模式等旧模型。
- 保留 `normalize_skill_name()` 作为覆盖、冲突和稳定排序的统一身份规则。
- Scanner 增加显式的物理作用域扫描接口，使 Catalog 能取得 Workspace 覆盖全局后的可见目录。
- 解析 YAML 可选 `keywords`：仅接受 1 至 8 个非空字符串；字段整体无效时记录 warning 并按空列表处理。
- 每个候选路径 `resolve()` 后必须仍位于对应 Skill 根目录；越界符号链接跳过。
- 新增 `SkillCatalog`，提供扁平方法：
  - `list_skills(scope="all")`；
  - `search(keywords)`；
  - `find_visible(name)`。
- 搜索归一化使用 Unicode `casefold()`，把连续空白、`-`、`_` 合并为单个空格。每个关键词
  经 `re.escape()` 后只做包含匹配，不执行用户正则。
- 排序依次比较：命中关键词数量、name 命中、YAML keywords 命中、description 命中、
  规范化名称；最多返回 10 项。

**定向测试**

- Workspace 同名覆盖全局，但物理扫描仍能看到两个来源；
- list 的三个 scope、稳定排序和返回字段；
- 大小写、空格、横线和下划线差异可以命中；
- 多关键词任一命中、命中数量和字段优先级排序；
- 正则元字符只按普通字符处理；
- 空结果、无效参数、无效 YAML、重复名和越界符号链接。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_scanner.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_catalog.py -q
```

## 任务二：把 Repository 收窄为单目标 Produce/Evolve

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/skill/repository.py`
- `apps/agent/src/agent_orchestration/plugins/skill/coordinator.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_repository.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_coordinator.py`

**实现内容**

- 保留现有安全目录句柄、根目录约束、文件权限、同目录临时文件、`fsync`、原子替换和内容 Hash。
- 删除批量 `create/update/merge/delete/no_op` 计划执行接口，增加两个单目标入口：
  - `produce(name, scope, content)`；
  - `evolve(snapshot, content)`。
- Produce 支持写入 `workspace` 或 `global`：
  - 调用前由 Repository 的 `find_conflicts(name)` 检查两个物理作用域的目录占用，包括无法解析为有效 Skill 的同名目录；
  - Repository 锁内再次检查两个作用域和目标路径占用；
  - 任一同规范化名称或目标路径冲突时失败；
  - 不覆盖已有文件。
- Evolve 使用明确的原始 `SkillSnapshot`：
  - Workspace Skill 原路径原子更新；
  - 全局 Skill保持只读，结果写入当前 Workspace 的同名覆盖；
  - 提交时原始 Hash 变化或预期为空的 Workspace 覆盖已经出现，均视为冲突。
- 两个入口都只接受一个完整 UTF-8 `SKILL.md`；YAML `name` 必须与目标规范化名称一致，
  `description` 必须非空。
- 将 `WorkspaceMaintenanceCoordinator` 改为面向 Skill 名称的写协调器。同进程内对会影响同一
  global/Workspace 名称的写入串行化，不让两个 Job 绕过最终冲突检查。
- 明确不承诺跨进程 CAS；外部进程竞态仍依赖最终检查和安全原子写，后续有真实部署需求再引入
  lock-file 或外部协调服务。

**定向测试**

- Workspace/global Produce 成功；
- 任一作用域已存在同名 Skill 时 Produce 失败且零副作用；
- 前置检查后新增同名 Skill时，提交前检查阻止覆盖；
- Workspace Evolve 更新原文件；
- Global Evolve 创建 Workspace 覆盖且不修改全局文件；
- Hash 变化、覆盖抢占、名称不一致、非法 YAML、路径逃逸和符号链接全部失败；
- 同进程同名 Job 串行，不同名称可以独立执行。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_repository.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_coordinator.py -q
```

## 任务三：实现 Producer/Evolver 的安全生成边界

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/skill/generation_models.py`
- `apps/agent/src/agent_orchestration/plugins/skill/generation_prompt.py`
- `apps/agent/src/agent_orchestration/plugins/skill/generation_parser.py`
- `apps/agent/src/agent_orchestration/plugins/skill/producer.py`
- `apps/agent/src/agent_orchestration/plugins/skill/evolver.py`
- 对应的 `test_generation_models.py`、`test_generation_prompt.py`、`test_generation_parser.py`、
  `test_producer.py` 和 `test_evolver.py`

**实现内容**

- 从旧 `maintenance_prompt.py` 提取并保留结构化脱敏、URL 凭据移除和强凭据 fail-closed 能力。
- 定义单目标严格输出模型，模型只能返回：

```json
{
  "content": "完整 SKILL.md 文本"
}
```

- Parser 接受一个纯 JSON 对象或一个完整 fenced JSON；拒绝额外文本、未知字段、空内容和多文档。
- Prompt Builder 输入保持扁平明确：
  - `operation`；
  - `name`；
  - Produce 的 `scope`；
  - `instructions`；
  - `conversation`；
  - Evolve 的原始 Skill 快照。
- `conversation` 在进入内部 Agent 前按消息角色、文本、图片元数据、ToolCall 和 ToolResult
  序列化并统一脱敏。图片不传原始二进制；URL 移除 query/fragment 中的凭据。
- `context = tuple(conversation.get_messages()) + task_messages` 只在 SkillPlugin 内组合并按只读
  数据使用，不增加第二次深拷贝。
- 不能把 `context` 原样传作内部 Agent 的 `history_messages`：当前 `task_messages` 末尾可能是尚未
  配对 ToolResult 的当前 Skill ToolCall。序列化后的完整证据放入内部 Agent 的 `input_prompt`，
  `history_messages=[]`，保证模型协议合法。
- `SkillProducer` 和 `SkillEvolver` 共用 Parser/脱敏基础设施，但各自使用明确的稳定 System Prompt
  和操作约束；两者都通过独立 `AgentFactory(register_builtin_tools=False)` 获取无工具 Agent。
- 内部 Agent 使用 `thinking` 角色、`tools=[]` 和独立超时；失败只向 Job 层抛出安全异常。

**定向测试**

- 完整历史和当前任务轨迹均进入脱敏证据；
- 当前未配对 Skill ToolCall 不会作为协议 history 传给模型；
- `name/scope/instructions` 不从历史猜测，而是显式进入任务指令；
- Token、Cookie、私钥、高熵凭据和 URL 凭据不会进入模型 Prompt；
- 强凭据、非法输出、超时、非文本结果和空内容 fail closed；
- Producer/Evolver 没有 read/write/bash 等文件 Tool。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_generation_models.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_generation_prompt.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_generation_parser.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_producer.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_evolver.py -q
```

## 任务四：实现 Skill Job、通知与持久状态

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/skill/jobs.py`
- `apps/agent/src/agent_orchestration/plugins/skill/job_manager.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_jobs.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_job_manager.py`

**实现内容**

- 定义 JSON 可序列化 `SkillJob`：
  - `job_id`、`operation`、`status`、`target_name`、Produce `scope`；
  - `task_id`、`run_id`、`step`；
  - `path` 或安全错误摘要；
  - `created_at`、`started_at`、`finished_at`；
  - 通知请求 Event ID 和投递状态。
- 状态机固定为 `queued -> running -> succeeded|failed|interrupted`，禁止终态反向迁移。
- `SkillJobManager` 使用锁保护 Job 字典；插件 `start()` 记录所属 asyncio loop。Tool 从执行线程
  提交 Job 时，先同步保存 queued，再通过 `loop.call_soon_threadsafe()` 调度后台 Task，因此同步和
  异步 Tool 都进入同一领域实现且 Tool 调用可以立即返回。
- Job 执行顺序：生成、严格解析、Repository 提交、保存终态、请求通知。所有异常转换为安全
  `failed`，不留下部分文件。
- Job 到达终态后由 SkillPlugin 发布 `TaskContextInputEvent`。JobManager 用 `request_event_id`
  关联后续 `TaskContextInputResultEvent`，记录六种既有状态：`accepted`、`not_found`、
  `not_running`、`already_cancelling`、`already_finished`、`invalid_content`。
- Workspace 状态保存有界终态记录；Session 状态保存当前 Session 的 Job ID 和通知状态。恢复时
  `queued/running` 统一转为 `interrupted`，不恢复 asyncio Task。
- 历史使用固定上限，确定性淘汰最旧终态；未知 `job_id` 返回明确失败。
- 生命周期：
  - `quiesce()` 拒绝新写 Job；
  - `drain()` 允许已进入提交临界区的 Job 完成，取消仍在模型生成阶段的 Task并标记 interrupted；
  - `drain()` 返回前所有 Job 必须稳定；
  - `stop()` 幂等关闭 AgentFactory 和残余资源。

**定向测试**

- 合法状态迁移、非法迁移、并发查询和有界历史；
- Tool 提交立即返回 queued，后台成功/失败不阻塞调用；
- 生成取消与提交阶段收束行为；
- Event 通知内容最小化、request_event_id 关联和六种结果状态；
- 通知失败不改变 Job 业务终态；
- Workspace/Session snapshot、restore 和未完成 Job interrupted 转换；
- 重复 quiesce/drain/stop 幂等。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_jobs.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_job_manager.py -q
```

## 任务五：实现五个 Tool 并重写 SkillPlugin

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/skill/tools.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_tools.py`

**重写文件**

- `apps/agent/src/agent_orchestration/plugins/skill/plugin.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_plugin.py`

**实现内容**

- `SkillPlugin` 对外提供与 `skill_management@1.0.0` 一致的五个扁平领域入口：
  - list；
  - search；
  - produce；
  - evolve；
  - job status。
- 五个 `BaseTool` 只负责 JSON 参数校验、调用领域入口并返回 `ToolExecutionResult`。
- Tool Schema 固定为：
  - `skills_list(scope?)`；
  - `skill_search(keywords)`；
  - `skill_produce(name, scope, instructions)`；
  - `skill_evolve(name, instructions)`；
  - `skill_job_status(job_id)`。
- list/search/job-status 标记为 `parallel_safe`；produce/evolve 保持顺序屏障。
- Produce/Evolve 在创建 Job 前完成权限、任务身份、当前消息、目标存在性和双作用域冲突检查。
  前置失败返回失败结果且没有 `job_id`。
- `allow_produce` 与 `allow_evolve` 独立，默认关闭；权限关闭不影响 Tool 注册。
- Plugin 只消费 `TaskContextInputResultEvent`；不再消费 UserInput、InputFinished 或
  AgentCompleted，也不再发布 `ContextContributionEvent`。
- Plugin 的 `consume()`、状态 Provider 和生命周期方法委托给 JobManager；Hook 只记录 list/search、
  Job 创建/迁移和 status 查询的摘要，不记录用户消息或 Skill 正文。

**Tool 注入集成测试**

- Skill Factory 返回的五个 Tool 与 Manifest 完全一致；
- Runtime Host 把五个 Tool 注册进共享 `ToolRegistry`；
- AgentFactory 使用同一 Registry；ReActAgent 在 Run 开始时能取得五个 ToolDefinition；
- `tools=None` 时五个 Tool 默认可见；显式 allowlist 排除时不可见；
- Run 启动后 Registry 冻结，Skill 文件新增不会改变当前 Run Tool 集合。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_tools.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_plugin.py \
  apps/agent/test/agent_orchestration/tools \
  apps/agent/test/agent_orchestration/capability -q
```

## 任务六：切换 Factory、Manifest、Blackboard 与 Runtime 装配

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/skill/factory.py`
- `apps/agent/src/agent_orchestration/plugins/skill/manifest.json`
- `apps/agent/src/agent_orchestration/plugins/blackboard/factory.py`
- `apps/agent/src/application/agent_runtime_service.py`
- `apps/agent/test/agent_orchestration/plugin_runtime/test_runtime_integration.py`
- `apps/agent/test/application/test_agent_runtime_service.py`
- 相关 Manifest/Factory 测试

**实现内容**

- Skill Manifest：
  - 依赖 `persistence/runtime`、`persistence/session`、`persistence/state_store`、
    `persistence/redactor`、`blackboard/conversation`；
  - 提供 `skill_management@1.0.0`；
  - 提供五个 Skill Tool；
  - 只发布 `TaskContextInputEvent`；
  - 只消费 `TaskContextInputResultEvent`；
  - 声明 Workspace/Session state version 1；
  - Python 依赖只保留 `PyYAML`。
- Factory 从 required capabilities 取得路径、身份、状态存储所需能力、Redactor 和 Blackboard
  conversation；创建 Catalog、Repository、Producer、Evolver、JobManager 和 SkillPlugin。
- Factory 返回一个原子 `PluginRegistration`：Plugin、`skill_management` Capability、五个 Tool 和
  State Provider 必须与 Manifest 严格一致。
- Blackboard 默认 `required_context_sources` 改为空集合；RuntimeService 不再强制传入 `["skill"]`。
- 保留 Blackboard 对其他未来 ContextContribution 来源的通用支持，不删除其事件模型或汇聚逻辑。
- 验证新增的 `skill -> blackboard` Capability 依赖不会形成反向依赖环；Blackboard 收到 UserInput 后
  不再等待 Skill，直接发布 Context Ready。

**定向测试**

- Manifest 解析、Capability 版本、五 Tool 注册和两个 state scope；
- Factory 缺少任一 required capability 时失败且不留下半注册资源；
- Blackboard 默认不等待 Skill Context；
- 完整链路 `skill_search -> read`；
- Produce/Evolve 的 queued、通知或 job_status 查询链路；
- Runtime shutdown 在 snapshot 前收束 Job。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugin_runtime \
  apps/agent/test/agent_orchestration/plugins/blackboard \
  apps/agent/test/application/test_agent_runtime_service.py -q
```

## 任务七：删除旧自动检索、自动维护和 Embedding 链路

**删除源码**

- `apps/agent/src/agent_orchestration/plugins/skill/ranker.py`
- `apps/agent/src/agent_orchestration/plugins/skill/usage_store.py`
- `apps/agent/src/agent_orchestration/plugins/skill/session_state.py`
- `apps/agent/src/agent_orchestration/plugins/skill/turn_state.py`
- `apps/agent/src/agent_orchestration/plugins/skill/maintainer.py`
- `apps/agent/src/agent_orchestration/plugins/skill/maintenance_models.py`
- `apps/agent/src/agent_orchestration/plugins/skill/maintenance_parser.py`
- `apps/agent/src/agent_orchestration/plugins/skill/maintenance_prompt.py`
- `apps/agent/src/model_provider/base_embedding.py`
- `apps/agent/src/model_provider/embedding_factory.py`
- `apps/agent/src/model_provider/impl/fastembed_embedding.py`

删除对应旧测试，并清理 `skill/__init__.py` 导出。安全脱敏能力必须已经由任务三的新模块承接后，
才能删除 `maintenance_prompt.py`。

**更新配置与依赖**

- `apps/agent/src/model_config/config_model.py`：
  - 删除 `EmbeddingSettings` 和必填 `embedding`；
  - 删除 `minimum_content_score`；
  - `SkillSettings` 改为严格布尔 `allow_produce=False`、`allow_evolve=False`。
- `apps/agent/settings.json`：删除 embedding 段和旧匹配阈值，写入两个默认关闭的权限。
- `apps/agent/src/agent_orchestration/plugins/persistence/path_resolver.py`：删除仅服务旧实现的
  `fastembed_cache_dir` 和 `skill_state_database`。
- `pyproject.toml`、`apps/agent/requirements.txt`：删除 `fastembed` 和 `numpy`，保留 `PyYAML`。
- 更新 model_config、model_provider、persistence 路径和应用测试中的旧构造参数及断言。

**残留检查**

```bash
rg -n "BaseEmbedding|EmbeddingFactory|FastEmbed|fastembed|minimum_content_score|SkillRanker|SkillUsageStore|SessionSkillState|SkillTurnState|SkillMaintainer|SkillMaintenance" \
  apps/agent/src apps/agent/test apps/agent/settings.json pyproject.toml \
  apps/agent/requirements.txt
```

预期只允许历史设计/计划文档中出现，不允许生产代码、当前测试或运行配置继续引用。

## 任务八：更新当前状态文档与完成验证

**更新文件**

- `apps/agent/docs/arch/plugin-event-flow-current-state.md`
- `apps/agent/docs/todo/agent-core.md`
- `apps/agent/docs/todo/development-roadmap.md`

**文档内容**

- 当前状态图改为 Tool 主动发现和 Job 通知链路；
- 删除 Skill 对 UserInput、AgentCompleted 和 Blackboard ContextContribution 的旧订阅；
- 增加 Skill 依赖 Blackboard conversation Capability；
- 明确五 Tool 通过 Manifest、PluginRegistration、共享 ToolRegistry 和 Run 快照进入主 Agent；
- 标记 Embedding、usage SQLite、自动检索和自动维护已移除；
- Roadmap 的发现、召回、生产和演化验收项分别记录测试结果，不用一个集成结果代替。

**最终验证顺序**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/blackboard \
  apps/agent/test/agent_orchestration/plugin_runtime \
  apps/agent/test/agent_orchestration/tools \
  apps/agent/test/agent_orchestration/capability \
  apps/agent/test/model_config \
  apps/agent/test/model_provider \
  apps/agent/test/application -q

apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src apps/agent/test apps/tui/src apps/tui/test
git diff --check
```

如果环境中存在可用模型凭据，再补一个最小真实 Smoke Test：

1. 主 Agent 能看到并调用 `skill_search`；
2. 主 Agent 用通用 `read` 阅读命中的 Skill；
3. 临时开启 `allow_produce` 后创建一个 Workspace Skill Job；
4. Job 成功后主 Agent收到 Context 通知，或可通过 `skill_job_status` 查询；
5. Smoke Test 使用临时 `ICARUS_DATA_DIR` 和临时 Workspace，不写入用户真实全局 Skill。

## 完成标准

- Runtime 中只存在五个 Skill Tool，且能按现有 Tool 快照机制提供给主 Agent；
- 普通 UserInput 不触发 Skill 扫描、Embedding 或 Context 注入；
- 搜索结果由简单、稳定、可测试的关键词规则产生；
- Produce 必须显式选择 workspace/global，并在创建 Job 前及提交前检查两个作用域冲突；
- Evolve 更新 Workspace Skill，或为全局 Skill 创建 Workspace 覆盖；
- Producer/Evolver 获得完整脱敏对话证据，但不接收协议不完整的原始 history；
- 两个写权限严格布尔、相互独立且默认关闭；
- Job 可查询、可通知、可持久化终态，并在 Runtime 退出时确定性收束；
- Blackboard 不再等待 Skill Context，Capability 图无环；
- Embedding、Ranker、UsageStore、自动检索与自动维护生产代码全部删除；
- Skill、Runtime、Agent 和 TUI 全量测试、compileall 与 diff check 全部通过；
- 当前未提交的 TUI Logo 改动保持原样，不混入 SkillPlugin 实现范围。
