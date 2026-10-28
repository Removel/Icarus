# Icarus 提示词插件技术方案

## 文档信息

| 项目 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 日期 | 2026-10-17 |
| 所属应用 | Agent |
| 代码分支 | `feature` |
| 状态 | 已实现并完成真实 socket/真实远端模型验证；已提交，未推送 |
| 相关提交 | `71d9564`、`9bea8d9`、`5266bb4`、`5aa000b` |
| 适用读者 | Agent 开发、插件开发、Gateway 开发、测试与运维 |

---

## 1. 摘要

本次开发新增第 12 个内置插件 `prompt`，把提示词从应用层和业务插件中的硬编码，收敛为**可组合、可约束、可观测的提示词能力**。

方案包含三部分：

1. **Prompt 插件**：统一发现、接收、排序、校验和渲染静态与动态提示词。
2. **业务插件接入**：Memory、Skill、Process、Knowledge、MCP、Builtin-tools 各自维护使用指导，通过 Event 贡献给 Prompt。
3. **基础 System Prompt 精简**：只保留通用行为约束，将插件专属规则移到对应插件，基础段减少约 57.5%。

设计重点：

- 静态 system 在一个运行时内保持字节稳定，减少服务商前缀缓存失效。
- 动态内容只进入当前 User Prompt；稳定插件指导进入 `session` 静态 System 层。
- 创建时外部指令只接受一次，私有持久化，不出现在公共历史。
- required/optional 来源有明确失败边界，避免无限等待或静默丢弃。
- 不新增数据库表、不修改框架 core 的业务语义。

---

## 2. 背景与问题

在改造前存在以下问题：

1. **提示词分散**：应用 `SessionRuntime` 维护一份较长的 `DEFAULT_SYSTEM_PROMPT`，Memory、Skill、工具使用规则等业务约束混在其中。
2. **业务知识无法独立演化**：修改记忆表达方式需要改应用层常量；新增插件无法声明自己的提示词职责。
3. **上下文路径不统一**：Memory 使用 `TaskContextInputEvent`，Skill 有独立生成 Agent，工具使用规则写在 ToolDefinition，缺少统一的提示词入口。
4. **缺少来源与预算治理**：没有优先级、单节上限、总预算、required 失败门禁和可选来源降级。
5. **外部指令无入口**：调用方无法在创建会话时传入一份静态指令，也无法安全恢复。
6. **前缀稳定性无保证**：任何业务变化都可能修改 system，影响模型服务商的 prefix cache。

---

## 3. 目标与非目标

### 3.1 目标

- 提供统一的静态/动态提示词注入能力。
- 让现有业务插件可以独立贡献使用指导。
- 保持 Blackboard 对历史、任务就绪、compaction 的既有职责。
- 保证静态段在明确边界之间字节稳定。
- 支持创建时外部指令，并保证恢复不丢、客户端不可见。
- 对来源、大小、失败和并发行为建立可测试的约束。
- 为后续 Persona、主动观测等能力提供依赖契约。

### 3.2 非目标

- 不实现 Persona/主动观测产品插件，只用测试插件验证接入。
- 不做用户级/托管级指令文件、嵌套目录或 `@import`。
- 不做显式 reset/reload API。
- 不探测模型服务商 cache 状态。
- 不新增数据库表或迁移。
- 不把 Hook 作为提示词出口。
- 不强制所有插件都成为贡献者。
- 不迁移 Memory/Skill 已稳定的业务数据路径。

---

## 4. 总体架构

```text
调用方 / Gateway
  │ session.create(instructions)
  ▼
AgentRuntime / SessionApi
  ├─ 校验外部指令
  ├─ SessionStore 同事务写 session + 私有指令记录(sequence=0)
  └─ SessionRegistry 每次 load 读取指令
        ▼
SessionRuntime
  ├─ PluginRuntimeHost 装配 12 个插件
  ├─ before_ready 采样静态贡献者 / 发现动态来源
  └─ EventBus + Prompt 插件
        │
        ├─ 静态: base → workspace → session → persona → external
        │     └─ prompt.read → Blackboard → system_prompt
        │
        └─ 动态: mode → contributor → runtime
              └─ ContextContributionEvent → User Prompt
```

### 4.1 分层职责

| 层 | 职责 |
|---|---|
| Gateway | JSON-RPC 参数校验、错误映射、WebSocket 分发 |
| Agent 应用层 | 会话创建、加载、持久化、更新投影、Blackboard 装配 |
| Prompt 插件 | 来源聚合、排序、预算、静态快照、动态贡献汇聚 |
| 业务插件 | 维护自己的指导文本与工具能力 |
| Blackboard | 任务 readiness、历史、compaction、最终 prompt 组装 |
| Agent/ReAct | 消费最终 system/input/history，不感知插件语义 |

### 4.2 关键设计原则

- Prompt 只负责提示词，不接管 EventBus、历史、任务调度。
- 插件之间运行期通信必须使用 Event 或定向能力，不直接调用兄弟插件实现。
- 静态贡献在恢复完成后采样，避免读取未恢复状态。
- 稳定业务指导进入 session 静态 System 层；只有真正随任务变化的内容才进入当前 User Prompt。
- 所有来源身份由运行时提供，贡献者不能自报可信身份。
- 不允许任意文本直接插入 system，只接受类型化 `PromptSection`。

---

## 5. 核心契约

### 5.1 位置

`apps/agent/src/agent_app/contracts/prompt.py`

应用级共享契约，不属于 `icarus.core`，也不修改 `PluginRegistration`。

### 5.2 数据结构

```python
@dataclass(frozen=True)
class PromptSection:
    section_id: str
    layer: Literal[
        "base", "workspace", "session", "persona", "external",
        "mode", "contributor", "runtime",
    ]
    content: str
```

约束：

- 来源由运行时提供，不放在 section 中。
- static 层为 `base/workspace/session/persona/external`。
- dynamic 层为 `mode/contributor/runtime`；稳定业务规则不放在动态层。
- `section_id` 使用稳定标识，层内排序；v1 不引入数值 priority。
- 正文不截断、不 strip、不做语义规范化。

### 5.3 静态贡献 Protocol

```python
@runtime_checkable
class PromptContributor(Protocol):
    def static_prompt_sections(self) -> tuple[PromptSection, ...]: ...
```

- 应用在 `before_ready` 采样。
- 当前对其他插件允许 `session` 与 `persona` 层：`session` 放稳定业务指导，`persona` 放身份/表达。
- 静态 Protocol 不是运行期查询接口，不能从其他 handler 直接调用兄弟插件。

### 5.4 动态贡献 Event

```python
@dataclass(frozen=True, kw_only=True)
class PromptContributionEvent(Event):
    status: Literal["completed", "failed"]
    sections: tuple[PromptSection, ...] = ()
    error: str | None = None
```

- 必须带 `task_id`。
- 只用于该次任务准备阶段。
- 每个来源只接受首个终态结果。
- 没有内容也要发 `completed + 空 sections`，避免 readiness 等待到超时。
- 失败发安全 `failed`，不携带原始异常或用户正文。

### 5.5 运行期更新 Event

```python
@dataclass(frozen=True, kw_only=True)
class PromptUpdateEvent(Event):
    sections: tuple[PromptSection, ...] = ()
```

- 无 `task_id`：只允许 persona 静态快照，暂存到下一次 refresh。
- 有 `task_id`：只允许动态层，且任务必须已成功准备、TaskChannel 为 RUNNING、有 run_id。
- 通过既有 `TaskContextInputEvent` 注入下一模型安全边界。
- 不会启动新任务，也不会把空闲期更新带到下一任务。

### 5.6 静态查询能力

```python
@dataclass(frozen=True, kw_only=True)
class PromptReadResponse(CapabilityResponse):
    system_prompt: str = ""

@dataclass(frozen=True, kw_only=True)
class PromptReadRequest(CapabilityRequest):
    reason: Literal["start", "refresh"] = "start"
    capability_id: ClassVar[str] = "prompt.read"
    response_type: ClassVar[type[CapabilityResponse]] = PromptReadResponse
```

- Event 能力，provider 为 Prompt，consumer 为 Blackboard。
- 响应只包含渲染后的 `system_prompt`。
- 错误通过 `CapabilityResponse.status/error_code/error_message` 返回。
- `system_prompt` 默认空字符串，使框架能合成 TIMEOUT/UNAVAILABLE/FAILED 响应。
- 不新增 version/hash/epoch 等字段。

### 5.7 contracts 导出

```python
EVENTS = {
    "prompt.contribution": PromptContributionEvent,
    "prompt.update": PromptUpdateEvent,
}
CAPABILITIES = (PromptRead,)
```

通过 manifest `contracts: "agent_app.contracts.prompt"` 注册，不修改 core 内置能力目录。

---

## 6. 静态提示词生命周期

### 6.1 层次

```text
base       内置通用约束
workspace  工作区 AGENTS.md → CLAUDE.md（首个存在者）
session    业务插件稳定使用指导
persona    其他插件静态人格贡献
external   session.create.instructions
```

同层按 `(source_plugin_id, section_id)` 排序。

### 6.2 构建边界

- 新建 session / 从存储恢复：构建一次。
- compaction 成功后、**当前 task 发布 context.ready 之前**：刷新一次。
- 进程/运行时重启：新运行时重新构建。
- 普通 task、客户端切换、WebSocket 重连：不刷新。
- 不探测服务商 KV 状态，不承诺 cache hit。

### 6.3 字节稳定

在一个运行时内，system 字符串保持逐字节一致，除非命中明确刷新边界。

注意：现有 Hook 仍可能在模型调用前改写参数；本方案只保证 Prompt/Blackboard 产出的静态字符串稳定，不覆盖 Hook 既有权限。

### 6.4 工作区文件

- 只读工作区根目录，不递归。
- `AGENTS.md` 存在则使用；不存在才回退 `CLAUDE.md`。
- 不回退读取越界 symlink、目录、FIFO 等非普通文件。
- 有界读取，最多 `3 * max_section_tokens + 1` bytes。
- 普通任务不重读；初始化/刷新时读取。
- 读取失败默认 optional，省略并记录诊断。

---

## 7. 动态提示词与运行期更新

### 7.1 任务准备

1. Prompt 消费 `input.user_input`。
2. 已配置动态来源独立并发工作，发布 `PromptContributionEvent`。
3. Prompt 在每个来源终态后聚合，或到达绝对截止时间后完成。
4. 生成一个 source=`prompt` 的 `ContextContributionEvent`。
5. Blackboard 将其与 Region、用户请求一起组装成当前 User Prompt。

### 7.2 截止时间

- 默认 `contributor_timeout_seconds = 5.0`。
- 以 `UserInputEvent.occurred_at + timeout` 为绝对 cutoff。
- 入队和提前缓存的贡献都检查接收时间与事件时间。
- 不能依赖零延时 timer 竞态延长 deadline。

### 7.3 required 与 optional

| 类型 | 缺失/失败/超时 | 超预算 |
|---|---|---|
| optional | 省略并告警，任务继续 | 整节丢弃 |
| required | 聚合 failed，Blackboard 终止任务 | 失败，不截断 |

- `required_contributors` 由 Prompt 接收方配置，不由生产者自报。
- Blackboard 的 `fatal_context_sources` 必须包含 `prompt`。
- 普通 Memory/Skill 既有 optional 上下文路径保持原行为。

### 7.4 运行期更新

- 只在成功准备、Agent 已 RUNNING、有 run_id 时接受。
- 每个更新单独计费，复用 `format_runtime_context` 的实际包裹格式。
- 确认必须来自可信的 `task.control` provider，且 task/run/request 匹配。
- 拒绝确认会释放预留；接受后才计入已用预算。
- `InputFinished`/stop 清理任务、预留和计时器。

---

## 8. 外部会话指令

### 8.1 API

```json
{
  "method": "session.create",
  "params": {
    "workspace_path": "/absolute/workspace",
    "session_id": "example",
    "load_runtime": false,
    "instructions": "当前会话的静态调用方指令"
  }
}
```

- 只在创建时接受。
- `None` 不存记录；空字符串也可原样存储。
- `StrictStr` 拒绝数字/数组等强转。
- submit/steer/get 等接口通过 `extra="forbid"` 拒绝该字段。

### 8.2 双层校验

1. **Gateway 层**：参数形状与字符/字节上限，超限返回 `-32602`。
2. **Agent 应用入口**：独立校验，绕过 Gateway 也不能写入超限指令。

预算来源为实际 Prompt 配置：

- 默认 `max_section_tokens = 8192`。
- 字符与 UTF-8 bytes 双约束。
- 超限直接失败，不截断。
- 错误码：`prompt_too_large`、`prompt_invalid`、`prompt_source_failed`。
- 错误文本不包含指令正文。

### 8.3 持久化

不新增表，复用 `conversation_updates`：

| 字段 | 值 |
|---|---|
| sequence | `0`（保留内部序号） |
| task_id | `None` |
| update_type | `session.instructions` |
| payload | `{"content": 原文}` |

规则：

- 与 `sessions` 行在同一事务写入。
- 不改变 `last_sequence`。
- 不更新 `first_user_input`、`last_public_activity_at`。
- 不构造公共 `RuntimeUpdate`。
- 公共读和完整性统计只查询 `sequence > 0`。
- 公开序号仍从 1 连续，客户端无需变更。

### 8.4 恢复

- 每次 single-flight load 通过内部 getter 读取。
- lazy create、unload/reload、进程重启走同一路径。
- 无记录兼容旧 session。
- 坏记录、读取失败不能降级为空，必须禁止加载成丢失指令的 Agent。
- 只有私有记录的 session 仍属于公开空会话，可 discard_empty。

### 8.5 隐私边界

- 指令不进入公共历史、通知、状态、标题或错误文本。
- 这条记录不是加密：拥有数据库文件的本地主体仍可能读取。
- 模型可以复述 system 内容；协议只保证不自动暴露。
- SQL 异常可能包含 bound parameters，Store 在私有写入边界替换为不带原始异常链的安全错误，避免日志泄漏。

---

## 9. Blackboard 集成

Blackboard 保持原有职责：

- 等待 required regions 和 required context sources。
- 管理完整 Message 历史。
- 在阈值时压缩历史。
- 最终组装 `BlackboardContextReadyEvent`。

新增行为：

1. 生产配置要求 `prompt.read`。
2. `required_context_sources` 包含 `prompt`。
3. `fatal_context_sources` 包含 `prompt`；失败直接 `_fail_before_agent`，不发布 `context.ready`。
4. 上下文完整后，先完成可能的 compaction，再请求 `prompt.read(reason=start/refresh)`。
5. 用最新 system + 实际 Prompt contribution 复核总预算。
6. 刷新失败当前任务不启动，后续任务重试。
7. 动态 Prompt 只产生一个 publisher=`prompt` 的 ContextBlock，避免 Blackboard 按来源重排。

---

## 10. 内置插件贡献

### 10.1 接入矩阵

| 插件 | 注入指导 | 保留的原有路径 |
|---|---|---|
| Memory | 记忆自然使用、空召回措辞、自主维护与隐私 | 召回正文走 TaskContextInput + Region + ACK |
| Skill | 先发现再读 SKILL.md、写入权限、轻量目录 | 生成/演化内部 Agent、Job 通知不变 |
| Process | 前台/后台分工、start≠ready、logs 跟进 | 不自动启动/查询进程，不回灌状态事件 |
| Knowledge | 聚焦查询、原文核对、真实引用、上传边界 | 不自动 RAG，不调用后端 |
| MCP | 工具发现、schema、tool_ref、失败如实报告 | 无配置空贡献；不主动联网/枚举 |
| Builtin-tools | 先读、分页、覆盖写、行前插入、退出码 | ToolDefinition 和参数约束不变 |

### 10.2 接入方式

静态：

```python
def static_prompt_sections(self):
    return (PromptSection("guidance", "session", "稳定的业务使用指导"),)
```

人格使用 `layer="persona"`；稳定工具/业务规则使用 `layer="session"`。

动态（只放真正随任务变化的内容）：

```python
@handles(UserInputEvent)
async def on_prompt_input(self, source_plugin_id, event):
    return (PromptContributionEvent(
        task_id=event.task_id,
        status="completed",
        sections=(PromptSection("task-mode", "mode", "当前任务模式"),),
    ),)
```

只有动态贡献才需要在 manifest 声明：

```json
{
  "contracts": "agent_app.contracts.prompt",
  "published_events": ["prompt.contribution"],
  "consumed_events": ["input.user_input"]
}
```

### 10.3 Skill 目录边界

- 最多 8 项。
- 单项 JSON > 256 字符整项省略。
- 总 JSON <= 1200 字符。
- 只读取最多 16 KiB YAML frontmatter，不读取 SKILL.md 正文。
- 元数据明确标为不可信数据，正文按需 `read`。
- 超过头部上限的定义省略并记录 warning，不截断后伪装完整。

### 10.4 不接入的插件

- `agent`：执行消费端，不硬编码业务提示词。
- `blackboard`：上下文装配端，不贡献重复内容。
- `persistence`：状态/日志后端，不回灌日志。
- `runtime-update`：客户端更新投影端，避免输出→提示词回路。
- `user-input`：原始输入来源，不篡改用户原文。

---

## 11. System Prompt 精简

### 11.1 结果

| 指标 | 改造前 | 改造后 | 变化 |
|---|---:|---:|---:|
| 字符数 | 1110 | 472 | -57.5% |
| UTF-8 bytes | 3208 | 1374 | -57.2% |
| 保守 token 估算 | 1110 | 472 | -57.5% |

> 这里减少的是**稳定基础段**。六个插件的稳定指导进入 `session` System 层，只保留一份；只有真正随任务变化的内容才进入 User Prompt 并随历史保存。

### 11.2 保留内容

- 身份与目标表达。
- 事实优先、不虚构。
- 实际执行、修改前理解、并行/顺序约束。
- 证据式报告、验证、阻塞说明。
- 授权、安全、凭据保护。
- 普通聊天简短、复杂任务完整展开。
- 不暴露内部插件/传输机制。

### 11.3 下放内容

- 记忆使用、维护、隐私、空召回措辞 → Memory。
- Skill 发现与读取流程 → Skill。
- 文件/命令操作注意事项 → Builtin-tools。
- 知识库查询/引用 → Knowledge。
- 后台任务与 MCP 使用 → Process/MCP。

---

## 12. 预算、失败与降级

### 12.1 预算

- 总预算 `max_prompt_tokens = 12288`。
- 单节 `max_section_tokens = 8192`。
- 使用 `conservative_token_count(text) = max(len(text), ceil(utf8_bytes / 3))`。
- 这是保守估算，不是精确 tokenizer。
- 渲染文本包含分隔标题、JSON 包裹和动态上下文开销，不能只计算正文。
- 静态刷新后重新复核整包预算。

### 12.2 失败策略

- optional section 超限：整节丢弃，不截断。
- optional 来源失败/超时：省略并告警。
- required 来源失败/超时/超限：任务失败。
- base/external 不可丢弃。
- prompt.read 失败/超时：当前任务失败，不使用旧 system 继续。
- compaction 刷新失败：当前任务失败，后续任务重试。

### 12.3 并发与顺序

- 来源并发，不由 Prompt 顺序调用。
- 等待放在 runtime-managed background work。
- 串行 handler 不阻塞 prompt.read。
- 同任务只发一次 readiness contribution。
- 重复/迟到准备结果不转成运行期更新。

---

## 13. 安全与隐私

1. 动态内容只进入 User Prompt，不修改 system。
2. 工作区文件、Skill 目录、知识/工具结果按数据对待，不当作授权。
3. 外部指令在协议侧不进入公共历史/通知/错误。
4. 诊断只记录来源、section ID、大小、错误码，不记录正文。
5. SQL 私有写入异常替换为安全错误，保留 rollback。
6. 来源身份由运行时提供；运行期确认必须来自可信 task.control provider。
7. 不新增任意文本注入 system 的通道。
8. 回退旧版本必须保留 sequence=0 兼容 reader，否则旧完整性统计会把历史判为损坏。

---

## 14. 测试与验证

### 14.1 自动化测试

| 范围 | 结果 |
|---|---|
| Agent 全套 | 903 passed |
| Gateway 全套 | 34 passed |
| 类型检查 | 238 个源文件通过 |
| wheel 构建/隔离安装/12 manifest | 通过 |
| compileall / diff 检查 | 通过 |
| TUI 全套 | 253 passed，13 个既有 snapshot 失败身份与基线完全一致 |

### 14.2 真实网络端到端

- 实际 Uvicorn/TCP/WebSocket。
- 生产 AgentRuntime、SessionRuntime、12 插件、EventBus、Blackboard、ReAct、LLM 适配器、SQLite。
- 独立磁盘外部插件，经 manifest 发现接入。
- 验证 session/persona 静态层、动态 mode、六个内置 session 指导进入模型请求，且六段 session 指导不出现在 User Prompt。
- 验证 Skill 目录元数据可见、正文不被自动注入。
- 验证 MCP 有配置但未发生网络访问。
- 停止并重启 Gateway 后恢复同一 session，外部指令不丢。
- 私有指令不出现在公共历史/通知，公共 sequence 连续。

### 14.3 真实远端模型烟测

在 session 静态迁移完成后再次执行一次受限调用：

- 模型：现有 `deepseek-flash`。
- 临时 workspace/data，不访问真实用户数据。
- 工具关闭，max_steps=1，max_output_tokens=128，SDK 自动重试关闭。
- 外部测试插件提供 `session` 静态码 `S23` 与任务动态码 `D47`。
- 结果：任务 completed，两个码均匹配，私有指令未出现在公开历史，`history_cursor=7`。

该结果证明真实模型可以同时使用 session 静态指导与真正动态的任务贡献，不代表所有模型遵循度或 Persona 产品能力已验收。

### 14.4 独立审查

- 首轮独立审查确认 5 项缺陷，均已按 RED→GREEN 修复：
  - SQL 私有参数日志泄漏。
  - 多次运行期包裹计费偏差。
  - 未运行/失败准备仍可注入。
  - 确认来源未校验。
  - deadline 竞态。
- 内置插件增量审查未发现需修复的确定缺陷。
- 未执行第二次重复审查。

---

## 15. 上线、迁移与回滚

### 15.1 上线内容

- 新增第 12 个 manifest：`prompt`。
- 默认 `required_plugin_ids` 增加 `prompt`。
- 默认插件配置新增 prompt 预算/超时/required 贡献者。
- Gateway 仅新增 create 参数和错误映射。
- 不新增客户端更新类型或 UI 控件。

### 15.2 数据库兼容

- 无 DDL、无新表。
- 旧 session 无 sequence=0 记录时按无外部指令处理。
- 新代码公共查询和完整性统计只读取 sequence>0。
- 回退旧代码前必须确认其完整性统计已排除 sequence=0，否则会误报历史损坏。
- 不删除真实私有记录来规避兼容问题。

### 15.3 回滚

- 功能未提交时可撤销本功能改动。
- 已写私有记录的数据库不能直接退回不识别 sequence=0 的旧 reader。
- 回滚应用版本需保留兼容 reader，或在隔离环境中迁移/清理数据。
- 测试和上线使用隔离数据，不触碰真实用户会话。

### 15.4 配置建议

```json
{
  "prompt": {
    "max_prompt_tokens": 12288,
    "max_section_tokens": 8192,
    "contributor_timeout_seconds": 5.0,
    "required_contributors": ["memory", "skill", "process", "knowledge", "mcp", "builtin-tools"]
  }
}
```

- `required_contributors` 应按部署实际启用的插件配置。
- 不要要求未启用插件，否则启动或任务准备会失败。
- MCP 无 server 配置时仍可安全完成，不会产生指导正文。

---

## 16. 风险与后续工作

### 16.1 已知风险

1. **Token 成本**：稳定插件指导进入 session System，每次请求都会携带；真正动态指导才进入 User Prompt 和历史。基础段减少不等于总成本同幅下降。
2. **模型遵循度**：不同模型对同样的指导遵循程度不同。
3. **缓存收益不可观测**：不接入服务商 cache 指标，无法量化前缀缓存命中。
4. **Skill 元数据为不可信内容**：目录描述可能包含提示注入；已按数据对待并限制大小，但不能完全消除风险。
5. **MCP 配置不等于连接**：指导只说明已配置，不保证工具可用。
6. **数据库回退兼容**：sequence=0 记录需要兼容 reader。
7. **外部指令隐私边界**：协议不展示，但本地数据库所有者和模型仍可能看到内容。

### 16.2 后续方向

- Persona 插件：作为独立静态贡献者，持久化自己的选择。
- 主动观测插件：作为动态贡献者或受控运行期更新来源。
- 显式 reload/refresh 能力：在明确授权与缓存边界下引入。
- 用户级/托管级指令文件与嵌套目录。
- MCP prompts 的获取、选择与信任边界。
- Knowledge 自动检索/RAG（需单独设计）。
- 更精确的 tokenizer 与按贡献者预算。
- 服务商 cache 指标接入与可观测性面板。

---

## 17. 代码地图

```text
apps/agent/src/agent_app/
├── contracts/prompt.py
├── plugins/prompt/
│   ├── manifest.json
│   ├── base_prompt.py
│   ├── composer.py
│   ├── config.py
│   ├── factory.py
│   ├── plugin.py
│   ├── sources.py
│   └── state.py
├── plugins/{memory,skill,process,knowledge,mcp,builtin_tools}/prompts.py
├── plugins/blackboard/{config,factory,plugin}.py
├── application/
│   ├── controllers/session_api.py
│   ├── services/session_registry.py
│   ├── repositories/session_store.py
│   ├── runtime_update_stream.py
│   └── session_runtime.py
└── config/config_model.py

apps/gateway/src/protocol/methods.py

apps/agent/test/agent_app/plugins/prompt/
apps/agent/test/agent_app/plugins/{memory,skill,process,knowledge,mcp,builtin_tools}/test_prompt_contribution.py
apps/agent/test/agent_app/plugins/blackboard/test_prompt_context.py
apps/agent/test/agent_app/application/test_session_instructions.py
apps/gateway/test/test_prompt_e2e.py
apps/gateway/test/test_session_instructions.py
```

---

## 18. 结论

本次改造把提示词从“应用层一段长文本”升级为“可声明、可组合、可治理的插件能力”：

- 静态 system 稳定，稳定插件指导在 session 层只保留一份，真正动态内容按任务注入。
- 业务插件拥有自己的指导文本，应用层只保留通用约束。
- 外部指令私有、不可变、可恢复。
- required/optional、预算、超时、来源身份和运行期边界均有测试覆盖。
- 真实网络与真实远端模型验证通过。

方案可作为后续 Persona、主动观测和更多提示词来源接入的基础。
