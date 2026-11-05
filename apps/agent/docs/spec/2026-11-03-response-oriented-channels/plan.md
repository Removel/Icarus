# Agent 语义输入与安全接收实施计划

> 执行者使用 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development`，按任务先写失败测试，再实现和回归。核心实现已写入工作区，任务是否完成以[执行记录](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-execution-2026-11-05.md)的逐项证据为准，不把未勾选任务当作全已验收。

**Goal:** 让 Agent 直接理解消息，按需了解 Argus 工具，并区分“明确没接收”和“可能已经执行”。

**Architecture:** 复用 PromptContributor、tool_search/tool_execute、SessionStore 与原子 commit。语义投影、短引用与接收状态属于 Agent 应用层；不把 Argus 协议识别塞进 Memory/ReActAgent。

**Tech Stack:** 现有 Python、Pydantic、dataclass、SQLAlchemy/SQLite、httpx、pytest；不引入新依赖。

**Spec:** [design](plan-design.md) · [Argus 计划](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan.md) · [Gateway 计划](../../../../gateway/docs/spec/2026-11-03-response-oriented-channels/plan.md)。更新：2026-11-05；调查基线 `e2ca65c`。本计划替代旧实施清单作为下一轮入口；既有实现/验证仍以 arch 和历史执行记录为准，不重做已经完成的能力继承与 Session 关联。

## Global Constraints

- 一个 Icarus 实例是一个独立对象；来源区域、平台账号、实例身份分开，不默认共享状态。
- 动态事实只进 User Prompt，短而稳定的原则进系统提示；不把教程常驻化。
- 普通工具策略不变，读说明不扩权；同步/异步一致。
- ReActAgent 无 Argus 依赖；不新增平台代发器、Hook 回执处理或通用工作流框架。
- 可能入队的异常不等于明确未接收；不删除 Session、换身份或重放外部动作来恢复。
- 不改生产配置/数据库，不真实外发或调用计费模型；不创建分支、commit 或 push，除非明确授权。

## Review Focus

1. 入队后异常/接收后写日志失败不能安全重试：Task 1。
2. 旧 attempt 的失败不能覆盖新尝试，重启不能靠缺日志推断未执行：Task 1。
3. 引用跨轮/重启不重绑，历史不能确认为当前工作：Task 2。
4. 主题字段缺省时旧请求指纹与普通输入行为不变，安全检测不缩小：Task 4。
5. 两实例看同一来源不共享记忆和执行进度；同账号不同实例也不能据此合并：Task 5。

## 顺序

先 Task 1 与 Gateway/Argus 接收契约联调；再 Task 2 → 3 → 4；最后 Task 5。Task 2 消费 Argus 新增来源事实。所有接口是提案，未实现；新测试路径在任务中创建后才能运行。

### Task 1：明确记录任务有没有被接收

**Files:** 修改 `apps/agent/src/agent_app/application/errors.py`、`controllers/session_api.py`、`services/argus_bridge.py`、`repositories/session_store.py`；新增同 services 下 `argus_delivery.py`；新增 `apps/agent/test/agent_app/application/test_argus_admission.py`，扩展 `test_session_runtime.py`。

**Interfaces:**

- `SubmissionNotAdmitted(code: str, *, retryable: bool)`：仅 SessionAPI 在 `entry.runtime.submit()` 之前可证明的失败使用，bridge 路径分类，不改变普通调用者已有异常语义。
- `submission_state(records: tuple[RuntimeUpdate, ...], *, task_id: str) -> SubmissionState`：phase/attempt/retryable/error_code；不读模型文字猜状态。
- 会话日志 `argus.submission`：prepared/submitting/not_admitted/accepted/unknown；先持久 submitting，再进入提交端口。InputAccepted 后存 accepted；日志落盘失败也可能已经执行，必须 unknown。
- 同 wake 在原身份内安全继续；不同 wake 不绕过未解决 Channel。选择 Session 的 ready 不是任务 accepted。

- [ ] 写 `test_pre_enqueue_load_failure_is_retryable`：加载失败未调用输入提交端口；持久 not_admitted 后修复再试，原 Session/wake/task 不变。
- [ ] 写 `test_enqueue_then_raise_is_unknown` 和 `test_admitted_then_journal_failure_is_unknown`：即使无 user.message，也不二次执行；外部动作替身计数最多一次。
- [ ] 写 `test_stale_attempt_cannot_reopen_retry`：旧 not_admitted 晚到，不覆盖新的 submitting/accepted；任何未排除的 unknown 都阻止重放。
- [ ] 写 prepared 落盘失败不执行、取消传播、同 wake 并发、重启留下 submitting、旧 argus.failed 缺证明保守处理测试。
- [ ] RED：`apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/application/test_argus_admission.py -q`，失败原因须为缺失行为而非环境错误。
- [ ] 实现类型化边界、持久阶段、按 attempt/sequence 归约。跨入端口后所有不确定异常保持 unknown；取消不直接变 retryable。
- [ ] GREEN：重跑上述测试与已有 `plugins/argus/test_reconciliation.py`、`test_cancel_commit.py`；不回退现有 commit 失联对账。

### Task 2：统一语义视图与引用

**Files:** 新增 `apps/agent/src/agent_app/plugins/argus/presentation.py`、`references.py`；修改 `tools.py`、`application/services/argus_bridge.py`、`application/repositories/session_store.py`；新增 `plugins/argus/test_presentation.py`、`test_references.py`，扩展 tools/commit/reconciliation 测试。

**Interfaces:**

- `project_observation(item: Observation, *, ref: str, role: Literal['work','context']) -> dict`，使用 `agent_app.argus_protocol.Observation`。
- `render_round(wake: WakeRequest, *, message_refs: dict[str,str], source_refs: dict[str,str]) -> str`：只给当前必要语义；不每轮贴完整业务状态。
- `semantic_subject(items: list[Observation], *, max_chars: int = 8192) -> str`：真实业务主题，不额外调用模型。
- `references.ensure(identity, *, observation_ids: tuple[str,...], source_keys: tuple[str,...])`：返回内部 ID/key→mN/sN 映射；先持久化、后展示，日志复用 `argus.references`。

- [ ] 写实际模型视图断言：当前正文/必要来源可见，无内部 UUID、游标、SDK 教程；发送所需平台目标仍可取得。
- [ ] 写两轮、重启、legacy→新视图、引用日志丢失/冲突测试；已暴露别名不可按当前列表重新编号。旧数字引用只解析原 binding 集合。
- [ ] 写历史仅作参考、未知/越界 ref 拒绝、最终模型视图截断后不能全轮 commit、取消后不能提交测试。
- [ ] RED：运行新增 presentation/references 与现有 commit 测试，确认旧行为失败。
- [ ] 实现纯投影、引用持久化和工具响应过滤；原始绑定/版本/回执留在代码侧。channel_read 默认只读业务状态，规则按需；history 使用 before_ref；来源提交使用获授权 source_refs。
- [ ] 评估 `argus_read` 的真实调用需求：当前消息完整、history 已满足时不新增重复入口；移除新表面入口前证明调用者与兼容，旧绑定仍能用旧契约。
- [ ] 更新 delivered 完整可见校验，检查实际最终模型视图，不能用“格式化过”替代可见。老 wake 保留原 prompt，不重渲染改变指纹。
- [ ] GREEN：`apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/plugins/argus -q`，覆盖 sync/async 返回一致。

### Task 3：短系统引导与只读工具指南

**Files:** 新增 `plugins/argus/prompts.py`、`guide.py`；修改 `plugin.py`、`tools.py`、`manifest.json`、`application/services/argus_bridge.py`；新增 `test_prompt_contribution.py`、`test_guide.py`。前缀均为 Agent 对应 src/test 路径。

**Interfaces:** `ArgusPlugin.static_prompt_sections() -> tuple[PromptSection,...]`；拟增工具 `argus_guide(topic='overview')`，仅 overview/rules/sources/commit；复用现有发现/执行，不改 ToolSearchTool。

- [ ] 写稳定系统与动态上下文分离测试：消息、来源、配置变动不改系统原则，普通轮次无规则教程。
- [ ] 写 rules 主题返回当前规则元数据/限制/示例，未知主题拒绝；配置不等于健康，不激活规则、不调模型。
- [ ] 写已知工具可直接调用、发现/读指南不扩权、manifest/打包工具声明一致测试。
- [ ] RED：运行新增 prompt/guide 测试。
- [ ] 实现短静态贡献和主题指南；规则元数据来自 Argus 绑定的 `rule_guide()`，不重复维护引擎常量；GUIDANCE 不再拼入新轮次 User Prompt。
- [ ] GREEN：插件测试及 `application/test_argus_session_capabilities.py`；Gateway 旧的“教程必须在 User Prompt”断言由 Gateway 计划替换。

### Task 4：干净主题正常召回

**Files:** 修改 `apps/agent/src/icarus/core/contracts/input.py`、`core/ports.py`、`runtime/session.py`；`agent_app/plugins/user_input/plugin.py`、`plugins/memory/plugin.py`、必要的 `plugins/persona/plugin.py`；`application/controllers/session_api.py`、`services/common.py`、`services/argus_bridge.py`。新增 `application/test_semantic_input.py`，扩展 memory/user_input/persona 与 `test_memory_context.py`。

**Interfaces:** `UserInputEvent`、`PendingInput`、`submit` 增加可选 `semantic_text: str | None = None`；提供时非空且最多 8192 字符。Memory 查询、缓存指纹、结果 query 使用同一主题，缺省使用旧 prompt。Blackboard 给模型的仍是完整必要语义视图。

- [ ] 写跨层透传、排队、缺省、超长拒绝、普通会话不变测试。
- [ ] 写 recall 查询不含教程/UUID/源码，真实业务仍参与召回；它不是禁用召回，也不自动写长期记忆。
- [ ] 写旧 submission_id 重试兼容；None 不往旧 hash 加 null；有值的新指纹可识别，主题改变构成冲突。
- [ ] 写 Persona 完整输入安全检查仍保留；不得因主题简化而漏查原始输入。
- [ ] RED → 实现上述输入链，不把 Argus 私有数据藏在 request_options，不让 Memory 导入 Argus。
- [ ] GREEN：新增 semantic_input、memory_context，以及 memory/persona/user_input 相关测试。

### Task 5：实例隔离与联合验收

**Files:** 扩展 `application/test_argus_sessions.py`、`test_memory_context.py`、`plugins/memory/test_factory.py`；Gateway fixture 与测试由 Gateway 计划拥有。

- [ ] 两个独立临时数据根、相同外部群/消息输入，断言各自 Session/回执/任务进度独立；不做跨实例自动去重。
- [ ] 对现有 memory user_id/agent_id/workspace_key 的后端约束做真实客户端契约级测试，证明一方写入不被另一方默认召回；不能只测两个无关内存字典。
- [ ] 记录实际可用隔离条件；共享存储无隔离能力时报告阻塞，不凭空加实例协调系统或擅改生产配置。
- [ ] 新旧绑定、按需指南、提交前失败、入队后异常、commit 回执丢失一起跑本地 E2E；不使用真实平台或计费模型。
- [ ] 最小集 → `make test-agent` → `make test-gateway` → 隔离 Argus 套件；通用输入影响 TUI 时跑 `make test-tui`，对比同环境旧失败身份，不修无关失败。
- [ ] `apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test`，最后 `git diff --check`。

## 切换和交付

暂停新投递并对账 → Agent 支持新旧接收状态 → Gateway 透传 → Argus 消费。旧未知轮次不能凭升级转成可重试；旧历史/原 prompt 不改写。重复外发、引用错绑或未知自动重试时停止新投递，回滚不撤销已发生外部动作。

本次撤销多机器人同群的待决问题；短引用、指南命名、主题字段、重试上限仍是具体工程提案。只更新 design/plan 不等于实施、部署或测试通过；实现后再据代码更新 arch，历史执行记录不抹去。
