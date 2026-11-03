# Discovery Confidence and Single-Call LLM Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 修正 Argus 发现阶段对 JEV 置信度的使用，并仅对语义低置信度的发现结果增加同一消息修订最多一次的无工具 LLM 兜底。

**Architecture:** JEV 保持一次 typed 请求，直接读取所选 `Choice` 答案的原生 `confidence`，不再用另一个指代不明的 `Noul` 问题代替路由置信度。固定 WorkflowRules handler 在规则执行结束后按需调用分类后端；协议和模型配置留在 `model_provider`，决策仍经现有 Store 事务校验和 Channel 创建路径落库。兜底意图与结果保存在现有 Observation 的 `detail.discovery.fallback`，不增加表、Plugin 或完整 Agent。

**Tech Stack:** Python / pytest / pytest-asyncio / SQLAlchemy async PostgreSQL / typesafe-sdk / 现有 Icarus model_provider。

**Spec:** [现有设计 §6](plan-design.md#6-持续发现与channel生命周期)以及本节“已确认设计”。本计划是用户于 2026-11-02 批准的后续修订：替换旧设计中“独立 Noul 给出分类置信度”的部分，并新增单次 LLM 分类兜底；其余来源权限、候选版本、原子提交和生命周期约束不变。

**Dependency:** [Agent 公共模型重试控制计划](../../../../agent/docs/spec/2026-11-02-llm-request-retry-control/plan.md)，须在 Task 2 的真实后端构造前完成。当前公共 `BaseLLM.ainvoke` 可以无工具调用，但 factory/适配器没有关闭 SDK 自动重试的公开参数；不能把一次 Python 调用误称为一次实际 HTTP 请求。

**Status:** 用户已批准实施；代码、离线验证和独立审阅修复已完成。最终Argus257、Agent1320、Gateway52通过；再次授权后原始六条JEV发现6/6、独立LLM后端1/1通过，但未自然触发自动兜底，未验证完整业务闭环；详见[实施记录](plan-discovery-fallback-execution.md)。

## 已确认设计与新证据

- 输入为消息与宿主注入的可信候选，输出复用 `DiscoveryDecision(action, reason, channel_id?)`；不让 LLM 生成 Channel 身份、规则或工具调用。
- JEV 高置信度正常走原路径；仅合法返回且 `Choice.confidence < 0.7` 的 discovery 语义判断触发兜底。
- LLM 可以返回 `new` / `existing` / `ignore` / `defer`，不再追加一个模型自报分数门槛。`existing` 只能引用给定候选；失败或不确定不能解释为 `ignore`。
- “一次”定义为：同一 Observation 修订最多启动一次上游 LLM 请求，SDK 自动重试关闭。崩溃或超时结果不明时不自动补发；不承诺外部模型服务的 exactly-once 执行或一定得到结果。
- WorkflowRules 的业务编排与模型协议分离；不修改通用 Plugin Runtime、Hooks、ReActAgent，也不扩大规则脚本权限或调用预算。
- 已测的 5 条完成条件对比为内网 `/score` 4/5、OpenRouter JEV 5/5；现有 24 条完成条件矩阵为 24/24。它们不证明初始发现或完整业务闭环通过。
- 当前初始发现 6/6 为 `defer`。进一步检查一条未改变问题的原始返回：`route=new`、原生 `Choice.confidence=0.64`、`probabilities={ignore:0.18,new:0.82}`、独立 `Noul=0.26`。原生 confidence 与选中项概率不是同一个字段，不能为了跨过门槛而换用较高的值。
- 已安装 SDK 的公开 `ChoiceAnswer` 暴露 `choice/confidence/probabilities`。保留 `0.7` 门槛，使用原生 `confidence`；这条样本仍可能需要兜底，不能提前声称修正后所有请求都会走快路径。

## Global Constraints

- 应用代码和测试留在 `apps/argus/`。唯一先行改动是 Agent 自有 `model_provider` 的可选重试控制，见[独立计划](../../../../agent/docs/spec/2026-11-02-llm-request-retry-control/plan.md)；不建立共享包或重复模型。
- 每次规则评估仍最多 3 次能力调用、其中 semantic 最多 1 次；LLM 兜底不在隔离规则进程或 semantic capability 的请求等待中执行。
- JEV 置信度门槛固定 `0.7`；普通 semantic match 门槛固定 `0.5`。不通过调整阈值掩盖失败。
- 候选最多 20 个，来源限制、active/paused 生命周期与候选版本复核保持不变；容量不足不触发模型兜底。
- LLM 请求不持有 PostgreSQL 事务锁或 WorkflowRules 全局 discovery 锁；超时 20 秒，最大输出 2048 tokens，上游自动重试为 0。
- 每个 Observation 修订最多一次兜底请求；新消息修订拥有独立身份。重启、重复通知和恢复扫描不能再次消耗该修订的兜底预算。
- 凭据只来自既有秘密配置，日志和报告不得含请求头、密钥或原始异常 HTTP body；公开地址不写入凭据文件。
- 不新增生产数据库表，不使用生产 DB 测试，不默认开启付费测试，不调用真实平台写操作。
- 当前工作区有大量用户未提交改动；在其基础上做最小修改，不覆盖、暂存、提交其他改动。未获授权不创建分支/worktree、提交、rebase 或 push。
- 正式实施前核对 intended remote 的 `feature` 基线；若需要合并/rebase 等 Git 变更，先征求授权，不自动同步或改写当前分支。

## Review Focus

1. 两个并发处理者、重复通知或服务重启只能产生一个持久兜底意图；覆盖实际 PostgreSQL 并发测试。
2. 外部调用完成后、应用业务结果前崩溃：恢复应复用已保存结果；调用已开始但结果未知：不得再次调用。
3. 候选或消息修订在 LLM 等待期间变化：旧答案不能创建新 Channel 或关联越权/过时候选。
4. 规则主动 defer、候选超限、JEV 超时/鉴权/非法输出：不能误触发付费兜底；兜底异常也不能变成 ignore。
5. 模型超时、取消、非法 JSON、伪造候选与消息内提示注入：必须保留失败/不确定事实，关闭隐式重试，不暴露凭据。

---

## 文件职责

- `model_provider/semantic.py`：JEV 原生路由置信度、低置信度标记及正确的默认模型标识。
- 新增 `model_provider/discovery_fallback.py`：一次无工具模型调用、严格结果解析与候选校验。
- `infrastructure/store.py`：现有 Observation JSON 中的兜底意图、结果缓存、并发去重与恢复。
- `plugins/workflow_rules/plugin.py`：规则结束后的兜底编排和统一结果提交；不解释具体模型协议。
- `plugins/workflow_rules/factory.py` / `application.py` / `config.py`：注入和资源生命周期、显式启用与配置验证。
- 对应镜像路径的测试、现有付费验收入口和 README：确定性验证、预算与可观察结果。

### Task 1: 使用 JEV 原生 Choice 置信度

**Files:**
- Modify: `apps/argus/src/argus_app/model_provider/semantic.py`
- Modify: `apps/argus/test/argus_app/test_semantic_discovery.py`
- Modify: `apps/argus/test/argus_app/test_semantic_backend.py`
- Modify: `apps/argus/test/argus_app/test_semantic_routing.py`

**Interfaces:**
- Consumes: 现有 `client.ask(state=..., questions=...)` 和公开 `ChoiceAnswer.confidence`。
- Produces: 保持 `SemanticBackend.__call__(arguments)` 返回结构；合法低置信度 discovery 结果另带顶层 `defer_reason="low_confidence"`，不把该标记加入 `DiscoveryDecision`。
- `score` 在 discovery 模式表示原生 `Choice.confidence`；普通模式仍表示 Noul 的 match 分数。

- [x] **Step 1: 写失败测试。** `test_discovery_uses_choice_confidence_in_one_request` 断言只发送 `route` 问题，原生 confidence `.9` 返回 new/existing；`test_choice_probability_does_not_override_confidence` 使用 confidence `.64`、new probability `.82`，断言 score 仍为 `.64`、返回 defer 且低置信度标记存在。补充 ignore、恰好 `.7`、低于 `.7`、未知候选、布尔/NaN/越界 confidence；普通 match 结果不增加 discovery 字段。
- [x] **Step 2: 验证失败。** Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/argus_app/test_semantic_discovery.py -q`；确认失败来自旧的独立 Noul 逻辑，而非环境缺失。
- [x] **Step 3: 最小实现。** 修改 `SemanticBackend.discover(self, arguments)`：只保留一次 Choice 请求，验证 choice 和原生 confidence 后按 `.7` 决策。完善 Choice instructions，明确“提出需要等待后续结果的跟进请求”即可为新事项，不要求事项已经完成；保留不因同群合并等现有约束。低置信度增加可信顶层标记，其他 defer 来源不伪装成该标记。
- [x] **Step 4: 修正已确认的路由标识。** 默认模型改为 `~typesafe/jev-latest`；保留 `https://openrouter.ai/api/alpha/decisions` 的端点适配与显式覆盖能力。更新离线默认值和 wire 测试，保持自托管原生端点的显式配置兼容，不接入另一套 `/score` API。
- [x] **Step 5: 验证。** Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/argus_app/test_semantic_discovery.py apps/argus/test/argus_app/test_semantic_backend.py apps/argus/test/argus_app/test_semantic_routing.py -q`；全部通过。仅检查本任务 diff，不自动提交。

### Task 2: 实现单次无工具 LLM 分类后端

**Files:**
- Create: `apps/argus/src/argus_app/model_provider/discovery_fallback.py`
- Create: `apps/argus/test/argus_app/model_provider/test_discovery_fallback.py`

**Interfaces:**
- `DiscoveryFallback.__init__(self, llm: BaseLLM) -> None`，复用 `icarus.core.llm.BaseLLM`；`async def __call__(self, text: str, candidates: list[dict]) -> DiscoveryDecision`，不注册 Plugin。
- `configured_discovery_fallback(model_name: str, settings_path: str | Path | None = None) -> tuple[DiscoveryFallback, BaseLLM]`：在本文件内调用公共 `get_config(settings_path)`、`agent_app.model_provider.factory.LLMFactory(config).create_llm(role=model_name, max_tokens=2048, max_retries=0)`。`model_name` 是配置里的实际命名模型键，不是线上的 model ID；credentials/base_url 复用已有 ConfigModel，不复制供应商配置字段。
- 一次 `await llm.ainvoke(messages)`，输入复用 `Message` / `TextPart`，输出只读取 `LLMResponse.message.content` 中的文本。不假设存在 `generate`、`response_format` 或原生结构化输出 API；用 JSON 解析加现有领域类型校验。
- App 拥有 helper 返回的 `BaseLLM` 并在 shutdown `await owner.aclose()`；外部注入的 callable 不归 App 关闭。接口错误、finish reason 表示截断/拒绝、意外 tool_calls 或非文本结果均不触发第二次模型请求。
- 输入候选与 JEV 使用同一可信快照；text 上限 16000 字符、候选上限 20、reason 上限 512 字符。候选外的 existing ID 直接拒绝。2048 tokens 是当前 provider thinking 预算下的有界请求上限，不代表实际 JSON 需要这么长；不为本特性迁移模型代际或重构 thinking 策略。

- [x] **Step 1: 写失败测试。** 使用记录实际调用次数的模型替身，覆盖四种 action、existing 合法/非法身份、new 携带 channel_id、缺 reason、未知字段、无效 JSON、reason 超限、超时/取消与 API 错误。断言正常路径恰好一次模型调用、没有工具定义/执行、没有模型自报 confidence 门槛、失败不转换成 ignore。
- [x] **Step 2: 验证失败。** Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/argus_app/model_provider/test_discovery_fallback.py -q`；新增后端缺失导致失败。
- [x] **Step 3: 实现。** 使用现有公共 model_provider 的一次 completion 接口，不创建 ReActAgent/AgentRuntime。System 只描述稳定分类规则；消息和可信候选作为 User 数据输入。要求单个 JSON 对象并用现有 `DiscoveryDecision` 校验，额外验证候选 ID 与输入/输出长度；解析失败不进行修复性第二次模型调用。
- [x] **Step 4: 固定调用限制。** 上游请求最大输出 2048 tokens、超时 20 秒、自动重试 0，任何生成循环/工具循环都不允许。测试 mock transport 的实际上游 HTTP 请求数，不仅统计 Python 方法调用数。
- [x] **Step 5: 验证。** 聚焦测试全部通过，并确认具体协议代码未泄漏到 WorkflowRules 或 Runtime。配置与生命周期接线在 Task 4 完成，不提前默认启用付费后端。

### Task 3: 持久兜底意图与最多一次预算

**Files:**
- Modify: `apps/argus/src/argus_app/infrastructure/store.py`
- Create: `apps/argus/test/argus_app/infrastructure/test_discovery_fallback.py`
- Modify: `apps/argus/test/argus_app/infrastructure/test_discovery.py`

**Interfaces:**
- `async def claim_discovery_fallback(self, observation_id: str, candidate_versions: dict[str, int]) -> str | None`：事务内校验最新修订、终态和候选版本，唯一认领成功返回随机 token；其他处理者不获得调用权限。
- `async def finish_discovery_fallback(self, observation_id: str, token: str, *, decision: dict | None = None, error_code: str = "") -> None`：只接受对应意图的一份完成结果，持久化原有 `DiscoveryDecision` 或受控错误码。decision 与 error_code 恰好提供一个；错误码限定 timeout/cancelled/invalid_output/unavailable/outcome_unknown。相同 token/相同结果重复提交幂等，不同 token 或冲突结果抛 `DomainError('discovery_fallback_conflict')`。timeout/cancelled/outcome_unknown 记 unknown，其他错误记 failed；取消时 shield 持久收尾后继续传播取消，不吞掉取消。
- 扩展 `record_discovery(..., fallback_token: str | None = None)`：只有匹配已完成意图的缓存答案能经 token 提交；复核现有候选版本与 latest revision，并复用原有 Channel 创建逻辑。

- [x] **Step 1: 写失败测试。** PostgreSQL 下两个 Store 实例并发认领只获得一个 token；同修订重复认领为 None；新修订拥有独立预算；旧修订/候选变化/终态均不能认领。断言数据库仍只有 `observed_items`、`channels` 两张表。
- [x] **Step 2: 验证失败。** Run `bash apps/argus/scripts/test.sh apps/argus/test/argus_app/infrastructure/test_discovery_fallback.py -q`，使用 runner 创建的隔离 PostgreSQL，不绕过真实锁测试。
- [x] **Step 3: 实现持久状态。** 在 `detail.discovery.fallback` 保存 token、candidate_versions、started_at、deadline_at、status 和受校验结果。状态限定 started/completed/failed/unknown。deadline 为认领后 25 秒，覆盖模型 20 秒等待及本地收尾；认领后外层发现状态为可到期恢复的 `fallback_pending`。所有事务维持现有锁顺序，提交后才允许发模型请求。
- [x] **Step 4: 实现恢复与受控提交。** `discovery_candidates` 增加已到期 `fallback_pending` 的有界扫描；未到期 started 不重发，到期结果未知标 unknown，不重新认领。completed 但未应用的答案可以恢复提交，不再调用模型；finish 保存完成结果后将 pending 的下一次扫描时间推进为当前时间，以便崩溃恢复。`record_discovery` 的普通结果更新保留 fallback 元数据，匹配完成 token 可越过为在途请求设置的等待时间，但不得越过候选/修订/身份校验。任意 token、不同答案或过时上下文不能提交；上下文变化仍允许后续 JEV 重新分类，但不能再次消耗该修订的 LLM 预算。
- [x] **Step 5: 补充故障测试。** 覆盖认领后未发请求即崩溃、调用后未存结果、存结果后未创建 Channel、重复 finish、finish 超过期限后才返回、调用中消息被修订、候选被暂停/完成/变更、普通 record 更新不能删除兜底记录。对超时结果采用保守策略，不迟到激活被判 unknown 的请求。
- [x] **Step 6: 验证。** Run `bash apps/argus/scripts/test.sh apps/argus/test/argus_app/infrastructure/test_discovery.py apps/argus/test/argus_app/infrastructure/test_discovery_fallback.py apps/argus/test/argus_app/infrastructure/test_continuous_discovery.py -q`；全部通过。不做生产数据迁移或回填。

### Task 4: WorkflowRules 编排、配置与资源接线

**Files:**
- Modify: `apps/argus/src/argus_app/plugins/workflow_rules/plugin.py`
- Modify: `apps/argus/src/argus_app/plugins/workflow_rules/factory.py`
- Modify: `apps/argus/src/argus_app/application.py`
- Modify: `apps/argus/src/argus_app/config.py`
- Modify: `apps/argus/config.example.toml`
- Modify: `apps/argus/test/argus_app/plugins/workflow_rules/test_discovery.py`
- Create: `apps/argus/test/argus_app/plugins/workflow_rules/test_discovery_fallback.py`
- Modify: `apps/argus/test/argus_app/test_config.py`
- Modify: `apps/argus/test/argus_app/test_host_integration.py`

**Interfaces:**
- WorkflowRules 增加可选 `discovery_fallback` 组件注入，接口为 Task 2 的异步 callable；关闭时行为兼容。
- `ArgusConfig` 增加三个平铺字段：`discovery_fallback_enabled: bool = False`、`discovery_fallback_model: str = ""`、`discovery_fallback_settings_path: Path | None = None`，分别读取 `ARGUS_DISCOVERY_FALLBACK_ENABLED` / `ARGUS_DISCOVERY_FALLBACK_MODEL` / `ARGUS_DISCOVERY_FALLBACK_SETTINGS_PATH`。enabled 为 true 时要求非空命名模型键；settings_path 未指定时沿用公共 get_config 的现有路径选择。开启但缺少命名模型/所选协议凭据时启动失败，不静默降级；vendor 校验和选择只在 model_provider helper 内，不在 WorkflowRules/Application 分支。
- 使用 Task 3 的 claim/finish/record 接口；从 `Observation.detail` 读取兜底恢复状态，不维护仅进程内的已调用集合。

- [x] **Step 1: 写失败测试。** 高置信度 new/existing/ignore 调用兜底 0 次；由宿主收到的 semantic 返回带 `defer_reason=low_confidence` 且最终 decision 为 defer 时调用一次。纯规则 defer、capacity defer、semantic unavailable/timeout/非法结果均为 0 次。fallback new/existing/ignore/defer 和异常分别经过正常校验或保留未决状态。
- [x] **Step 2: 验证失败。** Run `apps/argus/.venv/bin/python -m pytest apps/argus/test/argus_app/plugins/workflow_rules/test_discovery_fallback.py -q`；失败指向缺失的编排行为。
- [x] **Step 3: 实现两阶段编排。** 在可信 `call` 包装器中记录 semantic 后端的低置信度标记，而不是解析自由文本 reason 或接受脚本伪造标记。规则结束、校验 DiscoveryDecision 后认领意图；退出 `_discovery_lock` 和规则执行环境再等待 LLM。兜底完成先持久化，再经共享的规则制品准备及 record_discovery 路径提交；只由固定 handler 发布 RulePassed。
- [x] **Step 4: 接入恢复。** 对 started 未到期请求不做重复分类；完成缓存优先恢复提交；过期请求标 unknown；failed/unknown/已消费预算不会因下一轮 5/30 秒扫描再次调用 LLM。候选变化后缓存不能用于新上下文，保留现有最多 3 次发现尝试的边界。
- [x] **Step 5: 配置与生命周期。** Application 创建后端并通过现有 factory 注入，shutdown 关闭所拥有的客户端；注入的外部客户端不重复关闭。README/config example 写清显式开关、模型配置、凭据来源、20 秒上限及最多一次的崩溃权衡。不要增大 RuleEngine 的墙钟/能力等待上限来容纳 LLM。
- [x] **Step 6: 补充回归测试。** 覆盖重启重新构造 Plugin 仍只调用一次、并发发现两个处理者、回调超时和取消、LLM 期间候选/修订变化、非法 token、来源越权、提示注入、恢复提交只创建一个 Channel，以及一个慢兜底不阻塞其他已确定发现。配置关闭/开启缺参数、成功/异常 shutdown 也分别断言。
- [x] **Step 7: 验证。** 先聚焦 plugin/config 测试，再用 `bash apps/argus/scripts/test.sh` 运行完整 Argus 套件；错误身份与基线逐项比较，不修不相关失败。

### Task 5: 预算化验收与文档同步

**Files:**
- Modify: `apps/argus/test/real_scenarios.py`
- Modify: `apps/argus/test/manual_real_scenarios.py`
- Modify: `apps/argus/test/model_budget.py`
- Modify: `apps/argus/test/argus_app/test_real_scenario_harness.py`
- Modify: `apps/argus/test/argus_app/test_manual_budget.py`
- Modify: `apps/argus/README.md`
- Modify: `apps/argus/docs/spec/2026-11-02-decision-script-atomic-commit/plan-design.md`
- Update after verified implementation only: `apps/argus/docs/spec/2026-11-02-decision-script-atomic-commit/arch.md`

**Interfaces:**
- 真实模型报告分别记录 JEV route/native confidence/最终 action、fallback 是否触发/实际请求数/结果、规则与数据库是否真正应用，不再把 route 与独立 Noul 混为一项分数。
- 新增 discovery 验收选择；保留既有 semantic/full 模式。付费执行仍需 `--authorize-real-model`，兜底有独立 request budget，不计入深度 Agent 或安全审查预算。

- [x] **Step 1: 写失败测试。** 离线替身覆盖发现样本标签、报告原生 confidence、fallback 单次预算/失败即停、没有触发兜底时不能声称真实兜底已验证，以及 full 模式业务未完成不能报告成功。
- [x] **Step 2: 最小实现与离线验证。** 六个原始初始请求保持不变，并增加已有候选续接、同来源不同事项、引用/否定/无关和真正歧义样本；标签先固定再运行。统计实际尝试，报告不得带凭据。运行对应 harness/budget 聚焦测试通过。
- [x] **Step 3: 小规模真实验收（用户再次授权后六条JEV通过、自动fallback0次；独立后端另测1次通过，未声称真实自动触发或完整业务闭环通过）。** 获得执行授权且模型配置可用时，通过已验证代理运行六条初始发现，JEV 最多 6 次、fallback 最多 6 次，报告每条原始 route、native confidence、最终 decision 和延迟。未触发真实 fallback 时如实说明，由确定性集成测试证明触发和去重，不调整门槛强行制造通过。鉴权/地区/限流等错误立即停止，不自动换 key/代理。更大矩阵或完整付费业务链路另获授权。
- [x] **Step 4: 全面验证。** 顺序：最小受影响测试 → `bash apps/argus/scripts/test.sh` → `make test-agent` → `make test-gateway` → `apps/argus/.venv/bin/python -m compileall -q apps/argus/src apps/argus/test` → `git diff --check`。缺依赖/数据库/隔离能力逐项报告，不以 skip 冒充通过。仅在公共 Agent provider 发生真实修改时增加其 sync/async 对称测试及 `make typecheck-agent`。
- [x] **Step 5: 更新文档。** 先按实现与测试修订旧设计 §6，再同步 arch/README；保留原有失败记录，另加本次结果与剩余限制。区分 JEV 快路径、一次分类兜底和完整 Agent 业务闭环，不提前宣称全链路完成。不自动提交或推送。

## 实施前基线

- 当前分支：`feat/argus-plugin-refactor`，工作区存在大量未提交跨应用改动。
- 本次新鲜运行：semantic discovery/backend/routing 最小集 **8 passed**；`git diff --check` 通过。未在编写本计划时运行全应用套件。
- 付费观察报告为私有本地 `/tmp/icarus-openrouter-jev-comparison-lf2v8371/results.json` 与 `/tmp/icarus-openrouter-jev-discovery-ouvk14ls/results.json`；这些路径是本次会话证据，不是长期部署配置或可用性承诺。
- 本计划不授权 Git 变更、服务重启、生产数据迁移、修改密钥或新增外部发布。
