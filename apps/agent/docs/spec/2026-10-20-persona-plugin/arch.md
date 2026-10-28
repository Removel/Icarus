# Persona 插件设计

本文依据 `src/agent_app/plugins/persona/`、对应单测与 SessionRuntime 集成测试描述当前实现。实施顺序与验收记录见 [plan.md](plan.md)。

## 1. 定位与目标

persona 是一个一级内置插件，manifest `tier: "core"` 并加入默认 `runtime.required_plugin_ids`。它向 Prompt 注入全局唯一的人格内容，并围绕长对话中的三类问题提供保护：

| 问题 | 设计回应 |
|---|---|
| 长对话人格漂移 | 每 5 轮用 jev 评估最近助手输出，命中后注入一次简短重锚 |
| 提示词攻击导致出戏 | 每轮用 jev 评估用户输入，按置信度分档注入防御或人格内拒绝 |
| 角色约束过拟合 | 人格只约束表达方式；插件自有策略明确要求不改变事实判断、工具使用和任务执行 |

人格是**全局资产**，不是会话资产：一个 `persona_id`、一组文件、所有 Session 共享。自演化写入全局 overlay，对所有未来 Session 生效；运行中的 Session 不热更新。会话级状态只有漂移计数、最近消息窗口和限流计数。

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
- 缺少 `soul` 不阻止启动，但会告警；此时其它有效分节仍注入。
- 文件不存在是配置错误，启动失败；文件存在但损坏是尽力加载 + 告警。

`evolved.yaml` 格式（Q7 中讨论的 `evolved.md` 以 YAML 落地，条目需要结构化字段，仍可人工编辑）：

```yaml
version: 1
updated_at: "2026-10-20T12:00:00Z"
entries:
  - id: e_0123456789ab
    section: voice
    note: 第一人称的一句观察
    reason: 最近对话中的具体依据
    created_at: "2026-10-20T11:59:00Z"
    session_id: "..."
```

overlay 损坏时告警并按空 overlay 处理；写入时用原子替换覆盖。基础人格文件读取上限 64KiB；overlay 审计文件独立上限 1MiB，读写使用同一字节限额，避免注入笔记合规但理由元数据膨胀导致自己写出的文件无法重读。

## 3. Prompt 接入

persona 插件同时是静态贡献者和动态贡献者：

- **静态**：实现 `static_prompt_sections()`，只使用 `persona` 层，随 System Prompt 在 Session 开始时冻结。
- **动态**：消费 `input.user_input`，发布 `prompt.contribution`，使用 `contributor` 层，进入当轮用户提示词。
- Session 运行期间不再修改 System Prompt。

静态分节（`source=persona`，`layer=persona`）。`compose_prompt` 按 `section_id` 排序，因此使用数字前缀固定渲染顺序：

| section_id | 内容 |
|---|---|
| `persona-01-identity` | soul、boundaries、anti-goals、voice、examples 按优先级拼接 |
| `persona-02-relationship` | 从 rela-mem 读取的用户档案与关系摘要 |
| `persona-03-evolved` | overlay 条目，明确标注为"观察，不是指令" |
| `persona-04-policy` | 插件自有策略：演化约束与"人格不改变任务执行" |

动态分节（`source=persona`，`layer=contributor`）：

| section_id | 触发 |
|---|---|
| `persona-defense` | 攻击置信度进入中/高档 |
| `persona-reanchor` | 漂移置信度超过阈值 |

`prompt` 插件会等待所有动态贡献者或 5s 截止。persona 处理器**无论是否有内容都必须发布 `status="completed"` 的贡献**；异常时也发布空贡献，避免每轮额外等待 5s。jev 关闭时立即发布空贡献。

persona 在两份默认 settings 的 `prompt.required_contributors` 中：静态分节不会在总预算紧张时被当作可选来源丢弃。正常判定失败会发布空的 completed 贡献，不让整轮失败；但插件自身崩溃或未能在 prompt 截止前送达，仍按 required contributor 的既有语义使该轮失败，不能保证绝对无新增失败路径。将 persona 设为 optional 时，需要同时从 `runtime.required_plugin_ids` 和 `prompt.required_contributors` 移除。

## 4. 输入防御

每轮 `input.user_input` 调一次 jev，输入是当前用户文本（超长时保留头尾并截断）。使用 `Score`，三档 rubric：

| score | 含义 |
|---|---|
| 0 | 正常请求，不改变身份、规则或角色 |
| 1 | 可疑：出现越狱、角色覆盖、规则泄露等迹象，但仍可能是普通讨论 |
| 2 | 明确攻击：要求放弃人格、泄露系统提示词、改变边界或服从冲突指令 |

置信度分档使用归一化分数 `p = score / 2`，**不是校准后的攻击概率或统计置信区间**。`p >= 0.5` 注入中档防御；`p >= 0.8` 注入高档防御。不使用关键词预检。

- 中档：保持身份与语气继续自然回应，不复述、不解释设定。
- 高档：以人格身份明确拒绝该请求，保持语气；不泄露或讨论系统提示词、规则、检测机制。
- 所有档位都不向用户暴露"检测到攻击"之类元信息。
- 防御文案是插件内置模板，不来自人格文件，用户不可编辑。

失败策略：1.2s 硬超时、单次尝试、超时或解析失败一律 fail-open，不注入防御，正常生成。检测与提醒是降低出戏风险的启发式措施，不是权限控制或可靠的安全边界，不能保证防住提示词攻击；工具权限与授权仍由既有执行层负责。

## 5. 漂移检测与重锚

- 每个有效的 `UserInputEvent` 递增轮次；每 5 轮检测一次。使用宿主已注入的 TaskChannelRegistry 跳过已取消/结束/移除的任务，立即发布空贡献，不让过期队列消耗后续任务的判定时间。
- "一条助手消息"定义为**每个已完成任务的一条最终助手文本**，由 `agent.completed` 提供；不把同一任务内每个模型步骤的 `agent.message_completed` 片段计入，避免一次任务填满窗口或重复计数。
- 只使用助手最终文本，不使用用户内容；至少积累 3 条才检测。窗口上限 5 条 / 4000 字符。
- jev 使用 `Score`，三档 rubric：0 一致、1 轻微漂移、2 明显漂移；`p = score / 2`，`p >= 0.7` 视为漂移。
- 输入给 jev 的是最近 5 条助手文本 + 目标语气（soul+voice 的有界摘要）。
- 命中后只注入一句重锚，作用于当轮；下一次检测仍按 5 轮节奏，不连续每轮注入。
- 与攻击检测在需要时并行执行，各自独立超时；任一失败不影响另一个。
- 漂移只做提示，不改写、不重生成已产生的输出。

## 6. 自演化

agent 通过工具 `persona_evolve` 主动记录观察。工具对用户可见，这是预期体验。

```text
persona_evolve(
  section: "voice" | "relationship",
  note: str,       # 1..300 字符，第一人称一句观察
  reason: str,     # 1..500 字符，来自最近对话的具体依据
)
```

结构性护栏：

- `section` 是枚举，`soul/boundaries/anti-goals` 不在参数域内，提示词攻击无法诱导模型写到核心设定。
- note/reason 有长度上限；overlay 上限 30 条 / 600 tokens，超限按 `created_at` 淘汰最旧条目。
- 每个 Session 每 10 轮最多接受 1 次；被拒绝的调用不计数。
- V1 只增不改不删；人工可直接编辑或删除 `evolved.yaml` 中的条目。

校验：一次 jev 调用问三个 `Noul` 问题：

| 问题 | 拒绝条件 |
|---|---|
| `grounded` | 理由不是来自最近对话的具体言行 |
| `in_scope` | 内容涉及身份、边界、规则、权限或行为准则 |
| `worth_persisting` | 只是一次性情绪或临时情境，不值得跨会话保留 |

每个 `noul >= 0.6` 才通过；任一不通过即拒绝，并把原因返回给 agent。jev 超时、解析失败或不可用时**拒绝写入**（fail-closed），返回 `judge_unavailable`。枚举和只读基础文件限制实际写入范围，但自由文本观察的语义仍依赖分类器，不能据此承诺不会发生误判或长期污染。

送给演化判定的上下文是有界的：`note`、`reason`、`section`、最近 8 条消息（仅用户文本与助手文本，每条 ≤1000 字符、合计 ≤4000 字符）以及 soul+boundaries/anti-goals 的精简摘录。保留窗口来自每任务最终助手文本；另外合并工具执行接口提供的当前任务 `task_messages`，使本轮刚产生的证据可用。排除带 `tool_calls` 的助手中间消息、工具结果、图片、System Prompt，以及 User Prompt 中的插件上下文包装；只移除最外层 user_request，保留用户文本中的字面标签。

注入 `persona-03-evolved` 时只渲染 `section` 标签与 `note`；`reason`、`created_at`、`session_id` 只保留在 YAML 中供审计和人工查看，不进入提示词，避免把临时情境或敏感细节变成角色行为依据。

写入：校验前和写盘前检查任务是否仍有效，已取消则拒绝；通过后在锁内读取当前 overlay、追加条目、执行上限淘汰、写同目录临时文件并 `os.replace`。当前 Session 的静态 persona 不刷新，下一次 Session 启动时生效。

并发：插件实例是每 Session 一个，写锁是模块级、按 overlay 解析路径索引的 `asyncio.Lock` 注册表；`asyncio.Lock` 的等待者按 FIFO 获得锁。全局锁只包读改写，不包含 jev 评估；单实例另有演化调用锁，防止同 Session 并发调用越过冷却。工具重写 `ainvoke`；同步 `invoke` 通过已有 RuntimeLoopRunner 调度到同一事件循环，不新建 loop。跨进程（两个 Gateway 指向同一 data dir）不处理。

## 7. 关系档案联动

- 只读；mem0 完全不动。mem0 已在每轮自动 recall 情节记忆，persona 插件不重复查询。
- Session 开始时从 rela-mem 读取：
  - `GET /v1/persons?kind=user`，要求恰好一个 active 用户；
  - `GET /v1/persons/{id}/profile`；
  - `GET /v1/relations?person_id={id}&include_incoming=true`。
- 组合为"称呼 / 档案 key=value / 关系摘要"的紧凑块，≤200 tokens，超出时从末尾丢弃完整条目。
- 数据以"数据，不是指令"的框架注入。
- endpoint 默认 `http://127.0.0.1:8767`，`ICARUS_RELATIONSHIP_ENDPOINT` 可覆盖；token 只从进程环境 `RELA_MEM_API_TOKEN` 读取；非 loopback 必须 HTTPS 且带 token。
- 只通过 `httpx.AsyncClient` 发起 GET，三个读取共用 2s 总截止，不跟随重定向，响应有字节上限；并行读取使用 TaskGroup，任一失败或取消都会取消并等待其它请求，不遗留后台连接。
- 2s 超时、不重试；失败只告警并跳过该块。"不阻塞"指失败不会让 Session 启动失败；开启联动时，`on_session_start` 仍会最多等待 2s。包内默认关闭，仓库配置开启。
- endpoint 形状非法属于配置错误，启动失败；非 loopback 未使用 HTTPS、缺少 token 或服务不可达属于外部依赖问题，告警并跳过该块。
- 包内默认配置关闭该联动（`relationship_enabled=false`），仓库内 `apps/agent/settings.json` 开启；这样 CI 与全新安装不会因外部服务不可用而变慢或失败。
- 不新增 agent 检索工具：检索是否命中和准确由插件保证，不交给模型自行判断。

## 8. 预算与失败策略

统一使用 `icarus.core.tools.result_budget.conservative_token_count`。

| 内容 | 上限 | 超限处理 |
|---|---|---|
| soul | 400 tokens | 硬截断并告警 |
| identity 总块 | 1500 tokens | 按 soul > boundaries/anti-goals > voice > examples 顺序整节取舍 |
| relationship 块 | 200 tokens | 从末尾丢弃完整条目 |
| evolved 块 | 600 tokens / 30 条 | 淘汰最旧条目 |
| 每轮动态块 | 200 tokens | 防御优先；放不下时丢弃重锚 |

| 场景 | 行为 |
|---|---|
| persona 文件不存在（required 插件） | 启动失败 |
| persona 文件损坏 / 分节缺失 | 尽力加载有效部分 + 告警 |
| overlay 损坏 | 告警并按空 overlay 处理 |
| jev 超时 / 失败（防御、漂移） | fail-open，正常生成 |
| jev 超时 / 失败（演化） | fail-closed，拒绝写入 |
| jev 未配置且 `jev_enabled=true` | 告警并禁用 jev；人格继续注入，不阻塞启动 |
| `jev_enabled=false` | 跳过判定；`persona_evolve` 仍注册但返回 `judge_disabled`；policy 省略演化引导 |
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
├── prompts.py         # 防御、重锚、policy、关系与 overlay 的框架文案
└── tools.py           # persona_evolve
```

Manifest 关键字段：

- `plugin_id: "persona"`，`plugin_version: "1.0.0"`，`tier: "core"`。
- `provided_tools: ["persona_evolve"]`。
- `published_events: ["prompt.contribution"]`。
- `consumed_events: ["input.user_input", "agent.completed"]`。
- `state_scopes: []`；不持久化会话状态。
- `python_requires: ["typesafe-sdk>=0.7.2,<0.8"]`。

生命周期：

- `start`：创建 jev 客户端（不发起网络请求）。
- `on_session_start`：加载 persona/overlay、读取关系档案、构建静态分节；文件缺失时抛错使 required 插件启动失败。
- `static_prompt_sections`：返回已构建的四个静态分节。
- `on_event`：处理 `input.user_input` 与 `agent.completed`；输入事件始终发布动态贡献。
- `build_tools`：返回 `persona_evolve`。
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
evolution_min_turns_between_accepts: 10
relationship_enabled: false
relationship_endpoint: "http://127.0.0.1:8767"
relationship_timeout_seconds: 2.0
max_identity_tokens: 1500
max_soul_tokens: 400
max_relationship_tokens: 200
max_dynamic_tokens: 200
```

凭证只从环境读取：jev 优先 `ICARUS_JEV_API_KEY`，回退 `ARGUS_JEV_API_KEY`；rela-mem 使用 `RELA_MEM_API_TOKEN`。不写入配置、日志或工具结果。

需要同步修改的装配点：

- `agent_app/config/config_model.py` 默认 `required_plugin_ids` 增加 `persona`。
- `agent_app/resources/default_settings.json` 与仓库内 `apps/agent/settings.json` 增加 persona 配置和 required 条目；前者 `jev_enabled=false`、`relationship_enabled=false`，后者均为 `true`。包内默认关闭外部依赖，保证测试与全新安装离线可用。两处 `prompt` 配置都增加 `required_contributors: ["persona"]`。
- `agent_app/application/session_runtime.py` 为 persona 注入 `config_model`，用于解析 `ICARUS_DATA_DIR`。
- `agent_app/config/config_loader.py` 不新增映射；persona 的 rela-mem endpoint 在工厂内读取 `ICARUS_RELATIONSHIP_ENDPOINT` 覆盖。
- `apps/agent/requirements.txt` 增加 `typesafe-sdk==0.7.2`。
- `pyproject.toml` 的 package-data 增加 `personas/*/*.md`。

## 10. 非目标与已知限制

- 不支持多人格、运行中切换或 per-session 覆盖。
- 不做对话中自动检索，不新增关系查询工具。
- 不扫描记忆、工具输出、文件或网页里的间接提示词注入；V1 只检查用户输入。
- 不检查 steer/context input，只检查每轮 `input.user_input`。
- 不向 mem0 / rela-mem 回写任何内容。
- 不做跨进程写锁、条目修改/删除工具、UI 改造、动态预算加载器、关键词预检。
- 不持久化轮次计数与最近消息；Gateway 重启后从零开始。
- 演化条目是观察，不是指令；它不能覆盖核心人格、边界或用户当前明确要求。

## 11. 验收

- 新 Session 的 System Prompt 含 `persona` 层内容；persona 是 required source，不会在总预算内被当作可选来源丢弃；运行中不刷新。
- `jev_enabled=false` 时工具仍可见但返回 `judge_disabled`，policy 不包含演化引导。
- 两个 Session 共享同一人格文件与 overlay；自演化写入后只有新 Session 看到。
- 正常输入不注入动态内容；中/高档攻击分别注入对应防御；jev 超时不影响生成。
- 第 5、10、15… 轮检测漂移；命中只重锚一次；jev 失败不影响生成。
- `persona_evolve` 只能写 `voice`/`relationship`；三问任一不过即拒绝；jev 失败拒绝写入；限流与上限生效。
- 并发两次演化写入都落盘且不丢条目，计数不超过上限。
- persona 文件缺失时 required 插件启动失败；损坏时部分加载并告警；rela-mem 离线不阻塞启动。
- `make test-agent`、`make typecheck-agent`、`compileall`、`git diff --check` 通过。
