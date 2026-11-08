# Persona 插件设计

2026-11-08：依据 `src/agent_app/plugins/persona/`、应用层整理服务、对应单测与 SessionRuntime 集成测试更新。当前实施与验证见[新建对话时整理人格](plan-conversation-end.md)，原实施记录见 [plan.md](plan.md)，Prompt 分层见 [上下文交付](../2026-11-04-prompt-guidance/plan.md)。

## 1. 定位与目标

persona 是一个一级内置插件，manifest `tier: "core"` 并加入默认 `runtime.required_plugin_ids`。它向 Prompt 注入全局唯一的人格内容，并围绕长对话中的三类问题提供保护：

| 问题 | 设计回应 |
|---|---|
| 长对话人格漂移 | 每 5 轮用 jev 评估最近助手输出，命中后注入一次简短重锚 |
| 提示词攻击导致出戏 | 每轮用 jev 评估用户输入，按置信度分档注入防御或人格内拒绝 |
| 角色约束过拟合 | 人格只约束身份与表达；base 统一声明人格不改变事实、工具、授权和任务要求，persona 正文保留边界 |

人格是**全局资产**：一个 `persona_id`、一组文件、所有 Session 共享。演化写入全局 overlay，成功提交后启动的 runtime 加载新结果；运行中的 runtime 不热更新。会话级状态只有漂移计数和最近助手消息窗口。卸载后重载会重新读取当前文件，不持久化会话人格版本。

## 2. 文件与加载

```text
$ICARUS_DATA_DIR/personas/<persona_id>/
├── persona.md       # 作者维护，插件只读
└── evolved.yaml     # 自演化 overlay，插件写入
```

查找顺序：数据目录的 `persona.md` → 包内只读样例 `agent_app/resources/personas/<persona_id>/persona.md`。两者都不存在时，required 插件启动失败。包内样例只为让默认安装和测试可用，数据目录文件存在时永远优先。overlay 始终写入数据目录，即使基础文件来自包内样例。

`persona.md` 格式：

```markdown
---
id: default
name: Icarus
description: 默认人格
version: 1
---

## soul
我是谁、核心动机、不变的身份事实。

## voice
语气、用词、句长、口头习惯。

## boundaries
不可跨越的行为边界。

## anti-goals
明确不做什么，尤其是为了扮演而拒绝正常工作。

## examples
少量正例；不是逐字台词。
```

- 分节标题为二级标题；未知分节忽略并告警，重复分节按出现顺序拼接。
- frontmatter 只使用 `id/name/description/version`；`id` 与目录不一致时以目录为准并告警。
- 缺少 frontmatter 时正文仍可解析；解析失败按"文件损坏"处理。
- 缺少 `soul` 不阻止启动，但会告警；此时其它有效分节仍注入。`soul/boundaries/anti-goals` 任一缺失会汇总告警，提醒 JEV 的 invariants 输入缺失对应段；不按长度猜测语义完整性。
- 文件不存在是配置错误，启动失败；文件存在但损坏是尽力加载 + 告警。

`evolved.yaml` 格式（Q7 中讨论的 `evolved.md` 以 YAML 落地，条目需要结构化字段，仍可人工编辑）：

```yaml
version: 2
updated_at: "2026-11-08T12:00:00Z"
entries:
  - id: e_0123456789ab
    section: voice
    note: 第一人称的一句观察
    reason: 最近对话中的具体依据
    created_at: "2026-10-20T11:59:00Z"
    session_id: "..."
    workspace_key: "..."
    evidence_ids: [m_1]
sources:
  '["workspace-key","session-id"]':
    cycle_id: pc_...
    start_sequence: 0
    processed_through: 3
    status: applied
    accepted_count: 1
    skipped_count: 0
    already_processed: false
    processed_at: "2026-11-08T12:00:00Z"
```

兼容读取 v1，首次写入升级 v2，保留旧条目。加载时损坏 overlay 告警并降级为空；整理和写入严格拒绝损坏文件，不覆盖旧数据。条目与来源进度一起原子替换；空结果也保存游标。每个来源只保留最近收据，来源进度不随条目删除。基础人格读取上限 64KiB；overlay 上限 1MiB。整理输入超限显式失败，不静默截断证据。

## 3. Prompt 接入

persona 插件同时是静态贡献者和动态贡献者：

- **静态**：实现 `static_prompt_sections()`，只使用 `persona` 层，随 System Prompt 在 Session 开始时冻结。
- **动态**：消费 `input.user_input`，发布 `prompt.contribution`，使用 `contributor` 层，进入当轮用户提示词。
- Session 运行期间不再修改 System Prompt。

静态分节（`source=persona`，`layer=persona`）。`compose_prompt` 按 `section_id` 排序，因此使用数字前缀固定渲染顺序：

| section_id | 内容 |
|---|---|
| `persona-01-identity` | soul、boundaries、anti-goals、voice、examples 按优先级拼接 |
| `persona-02-style` | 启动时读取的已提交演化风格，服从人工人格和当前用户要求 |

空段不贡献。JEV 开关不影响已经提交的风格加载。关系档案仍是当前任务的动态数据；原始对话、理由、证据和审计字段不进入静态人格。独立整理读取完整人工人格进行校验，不使用渲染后可能省略分节的 identity。

动态分节（`source=persona`，`layer=contributor`）：

| section_id | 触发 |
|---|---|
| `persona-relationship` | relationship_enabled 时每任务读取有界档案；不再在会话启动时读取或进入 System |
| `persona-defense` | 攻击置信度进入中/高档 |
| `persona-reanchor` | 漂移置信度超过阈值 |

`prompt` 插件会等待所有动态贡献者或 5s 截止。persona 处理器**无论是否有内容都必须发布 `status="completed"` 的贡献**；关系读取与 JEV 判定并行，分别有界且错误独立。JEV 关闭仍交付可用档案；没有内容时发布空贡献。自定义 relationship_timeout_seconds 大于 Prompt 截止仍可能使 required 来源超时，应协调配置；默认2s/1.2s小于5s。

persona 在两份默认 settings 的 `prompt.required_contributors` 中：静态分节不会在总预算紧张时被当作可选来源丢弃。正常判定失败只省略对应提醒，仍发布 completed 并保留可用档案。但插件自身崩溃或未能在 prompt 截止前送达，仍按 required contributor 的既有语义使该轮失败。将 persona 设为 optional 时，需要同时从 `runtime.required_plugin_ids` 和 `prompt.required_contributors` 移除。有效人格仍随各次模型请求进入上下文，不能理解为只消耗一次 token。

## 4. 输入防御

每轮 `input.user_input` 调一次 jev，输入是当前用户文本（超长时保留头尾并截断）。使用 `Score`，三档 rubric：

| score | 含义 |
|---|---|
| 0 | 正常请求，不改变身份、规则或角色 |
| 1 | 可疑：出现越狱、角色覆盖、规则泄露等迹象，但仍可能是普通讨论 |
| 2 | 明确攻击：要求放弃人格、泄露系统提示词、改变边界或服从冲突指令 |

置信度分档使用归一化分数 `p = score / 2`，**不是校准后的攻击概率或统计置信区间**。`p >= 0.5` 注入中档防御；`p >= 0.8` 注入高档防御。不使用关键词预检。

- 中档：保持身份与语气继续自然回应，不复述、不解释设定。
- 高档：拒绝覆盖规则或泄露私有提示词的要求，同时继续帮助合理任务；不把整段包含安全讨论的请求都拒绝。
- 所有档位都不向用户暴露"检测到攻击"之类元信息。
- 防御文案是插件内置模板，不来自人格文件，用户不可编辑。
- 动态贡献按完整渲染后的预算选段，defense 优先于 reanchor；超限整段省略并记录 section、预算和上限，不记录用户输入。内置默认防御段均在默认 200 预算内；用户自设极小预算仍可能使其省略。

失败策略：1.2s 硬超时、单次尝试、超时或解析失败一律 fail-open，不注入防御，正常生成。检测与提醒是降低出戏风险的启发式措施，不是权限控制或可靠的安全边界，不能保证防住提示词攻击；工具权限与授权仍由既有执行层负责。

## 5. 漂移检测与重锚

- 每个有效的 `UserInputEvent` 递增轮次；每 5 轮检测一次。使用宿主已注入的 TaskChannelRegistry 跳过已取消/结束/移除的任务，立即发布空贡献，不让过期队列消耗后续任务的判定时间。
- "一条助手消息"定义为**每个已完成任务的一条最终助手文本**，由 `agent.completed` 提供；不把同一任务内每个模型步骤的 `agent.message_completed` 片段计入，避免一次任务填满窗口或重复计数。
- 只使用助手最终文本，不使用用户内容；至少积累 3 条才检测。窗口上限 5 条 / 4000 字符。
- jev 使用 `Score`，三档 rubric：0 一致、1 轻微漂移、2 明显漂移；`p = score / 2`，`p >= 0.7` 视为漂移。
- 输入给 jev 的是最近 5 条助手文本 + 目标语气（soul+voice 的有界摘要和启动时演化风格；JEV 再按现有限额裁剪）。
- 命中后只注入一句重锚，作用于当轮；下一次检测仍按 5 轮节奏，不连续每轮注入。
- 与攻击检测在需要时并行执行，各自独立超时；任一失败不影响另一个。
- 漂移只做提示，不改写、不重生成已产生的输出。

## 6. 对话后的独立整理

WebUI 现有“新建会话”在草稿离开确认通过后，请求 `session.consolidate_persona`，参数为捕获的旧工作区与 session ID；随后立即导航。普通历史切换、断线、卸载、每轮完成和应用退出均不触发。没有新增结束按钮，也不关闭旧会话。

应用层 `PersonaConsolidationService` 提供快速请求，返回 `accepted/already_running/skipped`。最多持有两个任务，同来源合并；忙碌、持久化失败、关闭开关或容量不足时跳过，不自动排队补跑。任务先等待更新队列屏障，在会话锁内复查忙碌、读取已持久化历史和成功游标，再固定范围释放锁。模型调用不阻止旧会话继续输入；快照后的新消息留待下次整理。

历史所有者以不可变 `PersonaSnapshot` 契约提供 `(start_sequence, end_sequence]` 范围和稳定周期 ID。证据保留 `user.message/user.correction`，以及成功任务的最终助手文本；失败任务的用户纠正仍保留。排除工具输出、思考、System 和插件包装；未终结任务跳过，不把断线当作结束。Persona 普通组件不访问数据库或 Blackboard。

`PersonaExtractor` 通过已有 `LLMFactory` 进行一次无工具调用，默认 perception 模型，输入完整人工人格、旧观察和本周期证据。输出允许 `[]`，候选仅含 `voice/relationship`、note、reason、evidence_ids；引用必须在本周期且包含用户文本。默认最多 5 条、note 300 字符、reason 500 字符、30s 超时和 12000 保守 token 输入预算，同时受模型上下文限制。超限、无效 JSON、工具调用或证据错误均不写入。

`PersonaConsolidator` 先做 NFKC、大小写与空白规范化去重，再由 JEV 审核。JEV 接收完整周期上下文、人工人格及已有和本批通过的观察，避免只看最近 8 条丢失早期纠正。一次调用问三个 `Noul` 问题：

| 问题 | 拒绝条件 |
|---|---|
| `grounded` | 理由不是来自最近对话的具体言行 |
| `in_scope` | 涉及人物事实、工具行为、身份、边界、规则、权限、义务或与旧人格冲突 |
| `worth_persisting` | 一次性情绪、临时格式、敏感细节或旧项的语义重复，不值得长期保留 |

每个 `noul >= 0.6` 才通过；不合格候选跳过，服务超时、解析失败或不可用则整个批次失败，不保存部分结果。语义范围仍依赖模型，离线测试不证明真实提炼质量或免于污染。内置指令为英文，证据及候选保留原语言。

写入按 overlay 路径持有进程内锁，核对来源游标及人工人格/overlay 版本；任一文件变化返回 `persona_changed`，不使用过期审核结果。只增加通过的条目，不自动改删旧项；30 条 / 600 token 容量不足时跳过新项，保留旧内容。结果和游标用同目录临时文件加 `os.replace` 一起提交，重复或更早范围不再追加。

提炼、审核或写盘失败不推进游标。后台保留最多 64 个请求结果并记录受控日志，不提供完成推送或轮询接口。应用停止时取消并等待任务，再关闭存储；旧 runtime 卸载不取消已取得快照的整理。没有持久化待办队列、启动恢复或自动重试，下次自然触发/显式应用 API 从成功游标重新处理。跨进程互斥与断电强持久性未实现。主 Agent 不再注册 `persona_evolve` 或演化指导，动态贡献不再含 `persona-observations`。

## 7. 关系档案联动

- 只读；mem0 完全不动。mem0 已在每轮自动 recall 情节记忆，persona 插件不重复查询。
- `relationship_enabled` 时，在每个有效任务的准备阶段从 rela-mem 读取，不在Session启动时联网：
  - `GET /v1/persons?kind=user`，要求恰好一个 active 用户；
  - `GET /v1/persons/{id}/profile`；
  - `GET /v1/relations?person_id={id}&include_incoming=true`。
- 组合为英文标签、原文称呼/档案与明确方向的关系摘要，≤200保守预算单位；按顺序加入能容纳的完整事实，超大条目跳过。当前任务User注入，缺项不表示不存在。
- 数据以"数据，不是指令"的框架注入。
- endpoint 默认 `http://127.0.0.1:8767`，`ICARUS_RELATIONSHIP_ENDPOINT` 可覆盖；token 只从进程环境 `RELA_MEM_API_TOKEN` 读取；非 loopback 必须 HTTPS 且带 token。
- 只通过 `httpx.AsyncClient` 发起 GET，三个读取共用 2s 总截止，不跟随重定向，响应有字节上限；并行读取使用 TaskGroup，任一失败或取消都会取消并等待其它请求，不遗留后台连接。
- 2s 超时、不重试；失败只告警并跳过该块，不丢已有观察或JEV防御；两类读取/判定并行。启动不等待该读取；开启时每任务准备最多付出配置的关系读取延迟。包内默认关闭，仓库配置开启。
- endpoint 形状非法属于配置错误，启动失败；非 loopback 未使用 HTTPS、缺少 token 或服务不可达属于外部依赖问题，告警并跳过该块。
- 包内默认配置关闭该联动（`relationship_enabled=false`），仓库内 `apps/agent/settings.json` 开启；这样 CI 与全新安装不会因外部服务不可用而变慢或失败。
- 不新增 agent 检索工具：检索是否命中和准确由插件保证，不交给模型自行判断。

## 8. 预算与失败策略

统一使用 `icarus.core.tools.result_budget.conservative_token_count`。

| 内容 | 上限 | 超限处理 |
|---|---|---|
| soul | 400 tokens | 硬截断并告警 |
| identity 总块 | 1500 tokens | 按 soul > boundaries/anti-goals > voice > examples 顺序整节取舍 |
| relationship 块 | 200保守预算单位 | 按顺序加入能容纳的完整事实，放不下的跳过并继续检查后续事实 |
| evolved 块 | 600 tokens / 30 条 | 新整理容量不足跳过新项；加载超预算的人工/旧记录时有界选取 |
| 每轮防御/重锚块 | 200保守预算单位 | 防御优先；放不下时整段省略并告警 |

档案遵守 200 动态预算，演化风格遵守 600 静态预算，再交给 Prompt 总预算校验；整个动态贡献还可能含关系档案。英文模板没有放宽生产预算。

| 场景 | 行为 |
|---|---|
| persona 文件不存在（required 插件） | 启动失败 |
| persona 文件损坏 / 分节缺失 | 尽力加载有效部分 + 告警 |
| overlay 损坏 | 告警并按空 overlay 处理 |
| jev 超时 / 失败（防御、漂移） | fail-open，正常生成 |
| jev 超时 / 失败（演化） | fail-closed，拒绝写入 |
| jev 未配置且 `jev_enabled=true` | 告警并禁用 jev；人格继续注入，不阻塞启动 |
| `jev_enabled=false` | 跳过防御/漂移；整理候选无法通过审核，既有静态风格仍加载 |
| `evolution_enabled=false` | 跳过整理，不推进游标；新聊天照常继续 |
| rela-mem 离线 / 超时 | 告警，跳过关系块，Session 正常启动 |

## 9. 插件形态、事件与配置

```text
apps/agent/src/agent_app/plugins/persona/
├── manifest.json
├── factory.py
├── plugin.py
├── config.py
├── store.py           # persona.md / evolved.yaml 加载、预算、原子写入
├── judge.py           # jev 客户端与 Judge Protocol
├── relationship.py    # 只读 rela-mem 档案读取
├── prompts.py         # 防御、重锚、独立提炼指令
├── extractor.py       # 无工具候选提炼
└── consolidator.py    # 校验、去重、保守提交
```

Manifest 关键字段：

- `plugin_id: "persona"`，`plugin_version: "1.0.0"`，`tier: "core"`。
- `provided_tools: []`。
- `published_events: ["prompt.contribution"]`。
- `consumed_events: ["input.user_input", "agent.completed"]`。
- `state_scopes: []`；不持久化会话状态。
- `python_requires: ["typesafe-sdk>=0.7.2,<0.8"]`。

生命周期：

- `start`：创建 jev 客户端（不发起网络请求）。
- `on_session_start`：加载 persona/overlay，冻结身份与风格；不读取关系档案。文件缺失时抛错使 required 插件启动失败。
- `static_prompt_sections`：返回非空身份与演化风格段，最多两段。
- `on_input` / `on_completed`：分别处理 `input.user_input` 与 `agent.completed`；有效任务输入时并行读取关系档案和执行 JEV 判定，将可用背景及提醒作为动态贡献发布；完成事件更新助手消息窗口。
- `build_tools`：沿用基类空实现。
- `quiesce`：停止接受新判定；`stop`：关闭 jev 与 HTTP 客户端。

配置（`runtime.plugin_config.persona`，扁平、`extra="forbid"`）：

```text
persona_id: "default"
jev_enabled: false
jev_base_url: "https://openrouter.ai/api"
jev_model: "typesafe/jev-1.13"
jev_timeout_seconds: 1.2
attack_defend_threshold: 0.5
attack_refuse_threshold: 0.8
drift_every_turns: 5
drift_threshold: 0.7
evolution_min_probability: 0.6
evolution_max_entries: 30
evolution_max_tokens: 600
evolution_max_note_chars: 300
evolution_max_reason_chars: 500
evolution_enabled: false
evolution_model_role: "perception"
evolution_timeout_seconds: 30
evolution_max_input_tokens: 12000
evolution_max_candidates: 5
relationship_enabled: false
relationship_endpoint: "http://127.0.0.1:8767"
relationship_timeout_seconds: 2.0
max_identity_tokens: 1500
max_soul_tokens: 400
max_relationship_tokens: 200
max_dynamic_tokens: 200
```

凭证只从环境读取：jev 优先 `ICARUS_JEV_API_KEY`，回退 `ARGUS_JEV_API_KEY`；rela-mem 使用 `RELA_MEM_API_TOKEN`。不写入配置、日志或工具结果。
旧 `evolution_min_turns_between_accepts` 配置读取时丢弃并告警，其他未知字段仍拒绝。包内 evolution 开关默认关闭，仓库配置开启。

需要同步修改的装配点：

- `agent_app/config/config_model.py` 默认 `required_plugin_ids` 增加 `persona`。
- `agent_app/resources/default_settings.json` 与仓库内 `apps/agent/settings.json` 增加 persona 配置和 required 条目；前者 `jev_enabled=false`、`relationship_enabled=false`，后者均为 `true`。包内默认关闭外部依赖，保证测试与全新安装离线可用。两处 `prompt` 配置都增加 `required_contributors: ["persona"]`。
- `agent_app/application/session_runtime.py` 为 persona 注入 `config_model`，用于解析 `ICARUS_DATA_DIR`。
- `agent_app/config/config_loader.py` 不新增映射；persona 的 rela-mem endpoint 在工厂内读取 `ICARUS_RELATIONSHIP_ENDPOINT` 覆盖。
- `apps/agent/requirements.txt` 增加 `typesafe-sdk==0.7.2`。
- `pyproject.toml` 的 package-data 增加 `personas/*/*.md`。

## 10. 非目标与已知限制

- 不支持多人格、运行中切换或 per-session 覆盖。
- 不让模型为人格联动自主选择检索；开启联动后由插件按任务后台读取关系档案，不新增关系查询工具。
- 不扫描记忆、工具输出、文件或网页里的间接提示词注入；V1 只检查用户输入。
- 不检查 steer/context input，只检查每轮 `input.user_input`。
- 不向 mem0 / rela-mem 回写任何内容。
- 不做跨进程写锁、条目修改/删除工具、管理界面、动态预算加载器、关键词预检。
- 不持久化轮次计数与最近消息；Gateway 重启后从零开始。
- 演化条目是观察，不是指令；它不能覆盖核心人格、边界或用户当前明确要求。

## 11. 验收

- 新 Session 的 System Prompt 含 `persona` 层内容；persona 是 required source，不会在总预算内被当作可选来源丢弃；运行中不刷新。
- 无 `persona_evolve`、逐轮观察或演化指导；静态风格加载不依赖 JEV，可用关系档案仍进入当轮 User。
- 两个 Session 共享同一人格文件与 overlay；自演化写入后只有新 Session 看到。
- 正常输入只注入可用背景；中/高档攻击另注入对应防御；JEV 超时不丢已有背景。
- 第 5、10、15… 轮检测漂移；命中只重锚一次；jev 失败不影响生成。
- 只有独立整理可提交 `voice/relationship`；无变化保存游标，失败保留旧数据，后续触发可重试。
- 同来源合并、全局容量有界；竞争写入遇到版本变化拒绝，避免丢条目或提交过期判断。
- persona 文件缺失时 required 插件启动失败；损坏时部分加载并告警；rela-mem 离线不阻塞启动。
- 实际测试命令与结果以[本轮验证记录](plan-conversation-end.md#7-实施验证记录)为准，不把验收要求视为已通过证据。
