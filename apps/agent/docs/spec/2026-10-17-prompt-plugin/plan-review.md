# 提示词插件｜设计审查

> 审查对象：原 `plan.md`；源码基线 `b1cc666`；审查日期 2026-10-17。
> 以下是未实现方案的缺陷，不是已引入的运行故障。功能目标保留，修正不一致的实现推论。

## 结论

功能范围可实现，但初稿不适合直接执行。已修订拥有者设计，并在 `plan-implementation.md` 分解任务。
不新增表、字段迁移、缓存探测、reset/reload API、bundle version、persona/主动观测实现。

## 阻断实施的问题

| 优先级 | 原稿问题与失败场景 | 当前代码证据 | 修正 |
|---|---|---|---|
| P1 | async PromptContributor 直接调用其他插件，绕过 Event；等待动态方法会把串行 worker 卡住 | `AGENTS.md` 的 Event 红线；`runtime/worker.py:258-280` | Protocol 只取初始化静态数据；动态使用 app-owned Event，等待用受管后台任务 |
| P1 | before_restore 读取 persona，得到未恢复状态，且可能保留随后被禁用的来源 | `runtime/host.py:140-164,185-196` | before_restore 仅登记，before_ready 从有效插件采样；运行期只存快照不持 sibling 对象 |
| P1 | compaction 后等下一 task 刷新，压缩后第一个模型请求仍使用旧 system | `plugins/blackboard/plugin.py:369-424` | compaction 完成后同一准备流程 await refresh，再发 context.ready |
| P1 | 私有指令占公共 sequence；仅隐藏记录会破坏完整性/客户端实时连续性，公开空历史游标也可能不推进 | `repositories/session_store.py:303-358,458-482`；`apps/tui/src/app.py:1204-1227` | 标记记录使用现表保留 sequence=0，不构造公共 RuntimeUpdate，不改 last_sequence；公共查询和完整性统计都只看正数 |
| P1 | session 先提交、指令后写；失败留下缺指令的 session；lazy create 绕过插件校验 | `controllers/session_api.py:172-201`；`repositories/session_store.py:105-146,254-301` | 创建前独立校验，同事务写 session+私有记录；不强行加载 lazy runtime |
| P1 | 只在 create 内存传指令，重启/恢复丢失；坏记录变 None 则静默放弃用户约束 | `services/session_registry.py:49-77` | 每次 single-flight load 通过内部 getter 读取；坏记录/读取异常 fail closed |
| P1 | 外部指令默认为 optional，完整 bundle 超限时可能被丢弃 | 原稿 PromptSection.required 默认 False | base/external 永远不可丢；optional 整节丢弃，不截断 |
| P1 | `EVENTS` 未导出导致 contracts 加载失败；必填 system_prompt 导致框架合成错误响应时 TypeError | `runtime/contracts.py:116-120`；`core/capabilities/requester.py:38-43` | 同时导出 EVENTS/CAPABILITIES；system_prompt 默认空字符串 |
| P2 | 将 persona-owned ContextBlock 直接转发会被黑板拒绝；多个 block 会被 composer 重排 | `blackboard/plugin.py:249-261`；`prompt_composer.py:36-48` | 先由 Prompt 排序渲染，再发一个 source=prompt 的 ContextBlock |
| P2 | “超时迟到准备响应”和“运行中新提醒”未区分，旧结果可能误注入 | TaskChannel 接受有效 task 上下文；普通 readiness 只发一次 | `prompt.contribution` 是一次性准备终态；`prompt.update` 才是显式更新 |

## 简化与明确化

- `PromptSection` 只保留 section_id/layer/content；来源来自 publisher，required 来源来自配置，slot 由 layer 推出。
- 不新增公共 RuntimeUpdateType。`session.instructions` 是 DB 内部标记，避免 Gateway/客户端认识私有类型。
- 不用 subscriber 监听 compaction 后竞速刷新；由 Blackboard 在现有同步准备流程直接请求。
- 声明式顺序是**渲染顺序**，不是“后面的内容能绕过更高等级限制”。
- 缓存自然过期不可观测，不保证重启或 compaction 等价于服务商实际过期。
- 8192 是插件总预算，4096 是单节预算；Gateway 和应用入口共享单节限额，完整 bundle 另验。
- 不复制产品自我身份、供应商分支或不存在的工具。基础提示词已在 `plan.md` 给出 Icarus 草稿。

## sequence=0 方案核实

现表 `_ConversationUpdateRow` 主键为 workspace_key/session_id/sequence，Integer 没有正数 CHECK
（`session_entities.py:78-96`），所以无需 DDL。公共 RuntimeUpdate.sequence 的正数约束仍保持。
`read_updates` 的数据查询已经 `sequence > after_sequence`，默认 after=0；但完整性统计未排除 0，必须一起修。

私有记录与 session 原子创建，不更新 public activity，不阻止 discard_empty。只有读取指令的内部方法能访问该记录；异常记录不经 wire 转换。隐私是协议不展示，不是承诺对 DB 所有者或模型自行复述进行加密/阻断。

## 当前基线证据

本轮主会话实际执行：

- `make test-agent`：**833 passed in 41.81s**。
- `make test-gateway`：**20 passed in 5.21s**，1 条既有 Starlette/AnyIO DeprecationWarning。
- `git diff --check`（文档编辑前）：通过。

独立审查另执行现有 capability/contracts/Blackboard 聚焦测试：35 passed。这里只作审查佐证，
不把已有测试通过冒称新功能通过。

最终文档 builder check（只读文档、未重复扫仓库）认可主要修正，另外指出最终预算检查的注入接口和
Gateway validator 的公开对象接缝应明确。实施计划 T2/T6/T7 已指定纯计数/验证回调，T4 已明确
AgentRuntime 与 SessionApi 的 canonical alias，并要求生产对象而不只测试桩验证。

TUI 全套、真实模型、新功能测试、实现后的类型/打包测试均尚未执行；见实施验收任务。

## 实施前需要审阅的技术选择

1. 私有记录保留 sequence=0，替代初稿的“占公共 sequence 并过滤”。
2. 仅用于隔离语义的两类 Event：一次性贡献与显式更新；不增加调用注册服务。
3. 静态数据在恢复后采样；compaction 刷新立即影响同一 task。
4. 基础提示词文案为设计草稿，落地时按本稿测试与范围验证，不宣称已实现。
