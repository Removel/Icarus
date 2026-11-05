# Argus 来源与安全投递实施计划

> 实施时按 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development` 逐项先测试、后实现。核心实现已写入工作区；[执行记录](plan-execution-2026-11-05.md)区分本地已验证与PostgreSQL待验证任务，不能将本计划整体标为完成。

**Goal:** 让来源/消息事实准确，投递失败能按证据恢复，不重复启动可能已经执行的任务。

**Architecture:** 协议适配器规范化事实，WorkflowRules 只消费明确语义；Bridge 使用 Agent 给出的接收状态，Store 保存当前 round 元数据。保留现有来源游标、Channel 规则、冻结与原子 commit。

**Tech Stack:** 现有 Python、Pydantic、SQLAlchemy async PostgreSQL、httpx、pytest；不新增依赖或外部发送框架。

**Spec:** [design](plan-design.md) · [Agent 计划](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan.md) · [Gateway 计划](../../../../gateway/docs/spec/2026-11-03-response-oriented-channels/plan.md)。更新：2026-11-05；调查基线 `e2ca65c`。下一轮以此清单为入口，不重做已经实现的回应规则与 Session 关联；历史结果保留在 [execution.md](execution.md)。

## Global Constraints

- 一个 Icarus 实例是独立对象；Source 是外部区域，平台账号是接入身份。不同实例不合并观察、记忆和进度。
- 不再等待“多机器人同群如何合并”的选择；不设计单实例多账号假想需求。
- 保留旧 key/消息/Channel，不自动重分区、合并或重放 ignored/exhausted。
- 正式 Agent 用普通 MCP/CLI 反馈；规则仍只读；Argus 不代发平台消息，不解释模型供应商协议。
- 不用缺少接受日志证明没执行；未知不重放，旧 attempt 不覆盖新状态。
- 不改生产库、拓扑、凭据，不启动真实模型/外发/服务；Git 操作另行授权。

## Review Focus

1. 飞书 text 是内容而非区域类型：Task 1。
2. root/parent 仅是引用事实，不能自动改变来源归属：Task 1。
3. 纯图片/表情不是空消息，也不等于已经可视理解：Task 2。
4. Session 已选但 wake 失败、接收回执丢失、乱序 attempt：Task 3。
5. 同一群进入两个实例仍各自推进；共享外部服务不能共用未隔离状态：Task 4。

### Task 1：来源描述与消息事实

**Files:** 修改 `apps/argus/src/argus_app/plugins/protocols/qq_protocol/plugin.py`、`feishu_protocol/plugin.py`、`plugins/workflow_rules/discovery.py`、必要的 `contracts/models.py`；新增 `contracts/source.py`。修改对应 protocols 测试，新增 `test_source_context.py`，扩展 `test_semantic_routing.py`。

**Interfaces:** 普通 `SourceDescriptor` 复用 Fact 验证，字段 platform/kind/region_id、可选 parent_id/display_name；接入 account_id/tenant_id 与区域身份分开。`semantic_context(current, history)` 的 conversation 使用规范化事实，kind 指区域，content_type 指内容。

JSON 交接：`detail.source` 为规范化来源描述；`detail.sent_at` 为可选显式 UTC ISO；`content.sender_name`；`detail.reply_to/root_id/parent_id/thread_id`；`content.media` 有界数组（type、可选 id/name/size、availability）。不复制原始任意嵌套对象，不把外来同名 detail 当可信身份。

- [ ] 写 QQ 群名片/昵称、平台时间、回复/@ 保留测试；非法时间不捏造，observed_at 仍是入库时间。
- [ ] 写飞书 group+text 得到 kind=group/content_type=text；缺少 chat_type 明确未知，不由内容类型猜。
- [ ] 写 root-only 不改旧 chat key、未知协议不按 QQ 位置拆 key；旧 QQ/飞书 key 有限兼容。
- [ ] 写账号仅为接入身份、旧 key 编码保持的测试；保留私聊 target_id/临时会话的既有正确行为。
- [ ] RED：`bash apps/argus/scripts/test.sh apps/argus/test/argus_app/plugins/protocols apps/argus/test/argus_app/test_source_context.py apps/argus/test/argus_app/test_semantic_routing.py -q`；缺测试库不是缺陷 RED。
- [ ] 实现协议 helper 与公共层规范化事实消费；不新增 Source 数据库、注册中心或层级订阅。
- [ ] GREEN：同命令通过，并由 Gateway 联合测试证明字段真实传到 Agent 模型视图；不跨应用导入内部模型。

### Task 2：媒体不是空消息

**Files:** 修改 `plugins/workflow_rules/discovery.py` 的默认发现/回应规则和 protocols 的有界媒体元信息；扩展 `test_discovery.py`、`test_response_rules.py`、protocols 测试。

**Interfaces:** 原始 text 与媒体事实分开；适配器提供真实类型描述供规则判内容存在。不可理解时允许语义判断或 defer，不伪造 OCR/图片语义。

- [ ] 写纯图片、纯表情、引用媒体、真正空消息测试；仅没有文本不得直接等于空消息。
- [ ] RED：运行上述最小规则/协议测试，确认旧 text-only 分支导致失败。
- [ ] 最小修改默认规则和规范化事实；不自动下载 URL、不内联媒体、不替换已有自定义规则。
- [ ] GREEN：重跑最小集，保留规则能力预算和历史边界测试。

### Task 3：投递选择、接收状态与有界恢复

**Files:** 修改 `plugins/icarus_bridge/client.py`、`plugin.py`、`infrastructure/store.py`、`application.py`；新增 `plugins/icarus_bridge/test_admission_recovery.py`、`infrastructure/test_round_submission.py`；扩展 `test_plugin.py`、`plugins/test_recovery_regressions.py`。

**Consumes:** Agent/Gateway 返回的 admission_state、attempt、retryable、error_code，及原 status/commit_state/replay_safe。状态语义以 [Agent 设计](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan-design.md#6-明确没接收才重试) 为准；缺字段不是安全证明。

**Produces:** Store 新增：

```python
async def set_round_session(channel_id: str, wake_id: str, session_id: str) -> None: ...
async def set_round_submission(
    channel_id: str, wake_id: str, admission_state: str, *,
    attempt: int, retryable: bool = False, error_code: str | None = None,
) -> None: ...
```

只扩展 runtime.round JSON。set_round_delivery 仅表示 accepted；未知的 known_task_id 供查询，不伪装接收成功。保存发生在原 Channel 行锁内，低 attempt 不覆盖高 attempt；同 attempt 迟到准备阶段不覆盖终态。任一次 accepted/未排除 unknown 不被其他失败重新放行。

- [ ] 写 ensure 成功/wake 前失败，Session 选择立即保存，重试原 key 不创建第二 Session。
- [ ] 写明确 not_admitted 修复后恢复，沿原 wake/binding/Session/task/prompt；相同 wake 排除自身安全失败记录，不绕过其他未解决执行。
- [ ] 写 accepted 响应丢失、入队后失败、接收后日志故障、旧状态缺字段：只查询、不二次执行。
- [ ] 写旧 not_admitted 晚于新 submitting/accepted、冲突状态归 unknown，以及一 Channel 失败不阻塞其他 Channel。
- [ ] 写最多 3 次总尝试（含首次）、永久错误停止、耗尽保留 pending/error。建议两次重试退避 1/2 秒，由扫描周期调度，不 busy-loop；阈值是待审工程参数。
- [ ] RED：`bash apps/argus/scripts/test.sh apps/argus/test/argus_app/plugins/icarus_bridge apps/argus/test/argus_app/infrastructure/test_round_submission.py apps/argus/test/argus_app/plugins/test_recovery_regressions.py -q`。
- [ ] 实现：尽量先校验快照/载荷，再选择并保存 Session，再发 wake。网络超时/500/无效 JSON 先查原 wake；未曾获得远端 claim 时允许完全相同的幂等请求，不换业务身份。404 不推翻已知 accepted/unknown。
- [ ] 不把 admission 重试当作 reconcile_round 重新启动业务任务；不消费确认、不丢待处理项、不换 Session 绕过未知外发。
- [ ] GREEN：最小测试及本地 Gateway/Agent/stdio MCP E2E；平台发送成功 commit 失联仍只恢复回执。

### Task 4：实例隔离与最终验证

**Files:** 扩展 `test_config.py`、`infrastructure/test_store.py`、`test_host_integration.py`；Agent 外部记忆范围测试见 Agent Task 5。

- [ ] 两独立测试数据库/数据根接入同群同消息，分别产生观察和独立进度；一方确认不推进另一方游标。不能用同群自动去重合并两个实例。
- [ ] 验证既有 database_url/data_dir 和外部 memory_user_id/agent_id/workspace_key 的实际隔离要求；共享数据库服务不等于共享业务范围。配置不支持时报告缺口，不擅改生产库或新增协调服务。
- [ ] 运行最小测试后 `bash apps/argus/scripts/test.sh`，使用隔离 PostgreSQL，绝不以生产库作夹具。
- [ ] 联合 `make test-agent`、`make test-gateway`；Agent 通用输入影响 TUI 时比较相同环境基线，不掩盖无关失败。
- [ ] Argus 脚本已包含 compileall；再 `git diff --check`。记录失败身份、跳过和未做的真实测试，不引用历史通过数量证明本轮通过。

### Task 5：新建 Channel 先总结命名（用户追加）

**Files:** 新增 `apps/argus/src/argus_app/model_provider/channel_naming.py`；修改 `config.py`、`application.py`、`plugins/workflow_rules/{plugin.py,factory.py}`、`infrastructure/store.py`；新增 model_provider 与 workflow_rules 的 `test_channel_naming.py`，扩展 `infrastructure/test_discovery.py`、`test_discovery_e2e.py`。

**Interfaces:** `ChannelNamer(llm).__call__(text, *, reason) -> str`；Store `claim_channel_name` / `finish_channel_name` / `channel_name`，结果存原 Observation.detail；`record_discovery(..., title=None)` 仅替代标题，goal保持原消息。默认配置perception，10秒/256输出tokens/retries0，title1–60字符。

- [x] 命名后再创建、失败回退、完成结果复用、非法/超长/多行/工具输出拒绝，先8项RED后GREEN。
- [x] 配置工厂复用已命名模型、预算固定的离线测试通过；新建主路径释放发现锁后命名，不在事务里调模型。
- [ ] 真实PG验证一次claim/重启/到期结果、title持久化与并发候选重新核验；已新增测试但隔离PG环境不可用，不能标完成。
- [ ] 实际业务样本的命名质量/延迟/成本烟测另获授权。普通测试必须注入替身或显式关闭，不能因为命名默认开启而走真实模型。

## 切换、限制与交付

Agent 可读新旧状态 → Gateway 薄转发 → Argus 消费；部署前暂停新投递并对账。旧失败无接收证明时保持保守，旧 key/消息/历史不改写。重复外发/引用错绑/未知被重放立即停止新投递；回滚代码不等于撤销平台动作。

文本 CQ 转义与跨主机上传需对应平台后端源码/权限，尚不具备闭环条件，不向通用 MCP 硬编码 QQ 特例。本次不补完整视觉、GitHub/会议连接器、不建立跨实例共享机制。

设计、工作区实现和实际验证分别记录；arch按已检查代码更新，execution历史保留。本轮未commit/push，PostgreSQL与真实部署门禁尚未完成。
