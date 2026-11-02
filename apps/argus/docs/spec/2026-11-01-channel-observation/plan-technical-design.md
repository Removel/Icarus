# Argus 插件化重构技术方案

> 2026-11-01设计提案；2026-11-02已有首期实现。当前事实以[源码架构](arch.md)与[验收记录](plan-validation.md)为准；本文件保留目标约束，不把未实现的GC、统一JSON上限或早期异步SDK宣称为现有能力。用户已批准在 `feat/argus-plugin-refactor` 实施。

入口：[计划](plan.md) · [契约](plan-contracts.md) · [执行台账](plan-execution.md) · [跨应用约定](../../../../../spec/2026-11-01-channel-observation.md)。

## 1. 分层与复用

协议插件认证、规范化、保存统一 Observation，提交后 publish ObservationRecorded。workflow_rules 匹配 Channel 订阅，从文件加载规则，经受限查询/机械/语义/外挂能力判断，持久化结果后 publish RulePassed。icarus_bridge 冻结本轮，通过 Gateway/Agent 公开应用入口找/建 Session 并投递到 Argus 适配插件。Agent 正常执行后回传控制事件，经第一层落库、规则层固定 handler 确认，才可选择更新或不更新。

直接使用公开 `icarus.PluginRuntimeHost`、BasePlugin、manifest/factory、ContractLoader、EventBus、Capability、生命周期与 RuntimeRecorder。不得复制框架、反向 import Agent 具体插件或直接调用 ReActAgent。来源身份路由属于 Runtime；source_key/Channel 订阅属于业务插件。Hooks 不路由、不记录、不改变控制流。

```text
src/argus_app/
├── main.py                         # 进程入口
├── application.py                  # create_app、依赖与公共 Host 装配
├── config.py
├── contracts/                      # models.py、events.py、capabilities.py
├── infrastructure/                 # Store、连接和事务普通对象
├── plugins/
│   ├── protocols/                  # 分组命名空间，无 manifest
│   │   ├── qq_protocol/
│   │   ├── feishu_protocol/
│   │   └── agent_protocol/
│   ├── rule_capabilities/          # 分组命名空间，无 manifest
│   │   ├── observation_query/
│   │   ├── mechanical_rules/
│   │   ├── semantic_judgement/
│   │   ├── memory_evidence/
│   │   ├── relationship_evidence/
│   │   └── knowledge_evidence/
│   ├── workflow_rules/
│   └── icarus_bridge/
└── rule_sdk/                       # 受限执行与薄 JSON IPC
```

每个 leaf 有自己的 manifest/factory/plugin，ID 按 leaf 目录下划线转连字符；所有 leaf 平级注册。分组不是父插件，Repository/Engine/RuleFiles 是普通对象。测试镜像源目录。

现有 discovery 只扫描直接子目录，必须补最小通用 **builtin** 分组遍历：遇 manifest 停止下钻，保留 leaf ID 校验、重复诊断、builtin 优先和启动前契约校验；不改变 external 扫描，不把分组塞入外部 plugin_dirs 绕过 builtin 身份，不导入实现来发现 manifest。此为必须任务，不是可选性能扩展。

## 2. 严格两表与部署

Argus 数据库恰好两张应用表：`observed_items`、`channels`。不创建或复用 `channel_observations`、`wake_requests` 或任何关联、规则、执行、版本、确认、command/outbox/schema_version 表；不能把“两张核心表”解释为允许额外支撑表。

- Observation 最小公共字段：id、source_key、message_id、kind、revision、observed_at（UTC）、content、detail。id 指具体修订，正文在 content.text。新增平台字段只有实际需要才引入；不复制无界原始 payload。
- Channel：id、title、goal、progress、lifecycle、version、subscriptions、rule_path、rule_hash、runtime。subscriptions 为 source_key→cursor/null；source_keys 从其导出，不另存一份。源码只存受管文件。
- `channels.runtime`：严格有界 pending/errors + 至多一个当前 round（包含稳定 wake/binding/session/task、冻结 observation_ids、送达、确认、有限回执及更新结果）。不得积累历史轮、执行日志或每次 ignore 记录。
- pending 上限默认 **256**，round 至多20条；pending 保留至本轮结束，冻结后不扩集合。当前轮回执摘要至多40条；runtime 总体≤1MiB，单项诊断/证据有界。容量满时拒绝写入并背压，不推进该项 cursor，不丢待办，不截断身份。上限是执行默认配置，不是吞吐承诺。
- 控制 payload/身份/输入 hash 与 `processing` 结果在 `observed_items.detail` 的严格按 kind schema 中；detail≤48KiB，processing≤8KiB，诊断/引用各≤20，源码≤32KiB。拒绝无界响应、堆栈和任意嵌套对象。控制请求保留记录不是执行日志表。

复用 Mem0 所用的**现有 PostgreSQL 容器/实例**，使用独立 Argus 数据库和受限应用账号，只能访问自己的 DB/schema。不得使用 Mem0 凭据/超级用户或读写其表；生产不增加 Argus 专用 PG 容器。管理员创建 DB/角色属于单独部署权限，不是应用启动自动授权。无旧数据迁移、双轨兼容、清库或自动重建。隔离 PG 测试必须与生产/Mem0 业务隔离。

## 3. 存取、容量和恢复

Store 的方法及 DTO 以冻结接口为准：ingest/observation(s)/create_channel/channel(s)/unread/decide/begin_round/set_round_delivery/bound_channel/apply_control/complete_control/pending_controls。SQLAlchemy async + asyncpg 是生产路径；SQLite 若用于局部测试不得宣传为生产后端，真实隔离 PG 测试必需。

同来源按最新规范化内容判断重复，编辑 A→B→A 不应被历史 fingerprint 吞掉。控制 request_id 重用但 payload 不同必须拒绝，不能产生新修订。来源事务锁保证单调提交水位；无可靠源版本时披露按接收顺序的限制。

cursor 与判定/pending 在同一短事务更新；旧 Channel version/cursor 的结果返回 stale，不覆盖新值。ignore 只推进 cursor。pending 满则该来源停在当前项，观测仍可入库，公平处理其他来源/Channel。不要在模型/外部能力等待期间持 DB 锁。

Bridge 先写 runtime.round 再外部投递；重复 begin_round 返回同一身份。未知远端结果保留占用，不得覆盖 round 或修改已提交指纹。新事件留 pending 或 cursor 之后。正常 finish 删除已确认 pending，再清 active round（或保留至多一个 closed 摘要）；未确认失败仍保留，不能因发布失败重做已确认动作。paused 保留积压；completed 只能在无 active actions 后清订阅/pending。

总线仅通知：按 cursor、runtime.pending/round 和 detail.processing=pending 扫漏。控制 effect 与 processing 结果同一 PG 事务提交；丢响应用原 request_id 查询/重试。当前轮摘要可清，规范幂等结果由控制 Observation 承担。默认留存30天，未释放轮/未消费积压/必要证据受保护；积压超龄告警并背压，不承诺无限存储。过期已释放 binding 的迟到请求拒绝；不承诺删除控制记录后的无限历史幂等。

## 4. 规则与能力

规则为受管不可变 `rules/<channel_id>/<sha256>.py`，Channel 仅存 path/hash。服务端计算 hash；候选 TTL 清理，当前/上一有效及在用制品受保护。禁止路径穿越、symlink、URL、任意安装依赖。写文件并 fsync/rename 后，才在 PG 事务切换指针；失败保持旧有效指针，不声称文件系统与 PG 原子提交。

`async decide(event, channel, capabilities) -> Decision`；Decision 为 passed/reason/evidence_refs/defer，passed+defer 非法。查询只在授权订阅来源、本次捕获 source_bounds 内看最新有效修订，limit≤50；历史查询不推进 cursor、不扩大待确认集合。机械简单比较可用普通 Python；语义失败/能力 unavailable 不伪装 False 或空结果。

observation_query、mechanical_rules、semantic_judgement 必需；memory/relationship/knowledge 按配置提供真实只读后端，缺失明确 unavailable，不强制部署全部外部服务。能力经既有 request/response，不造第二套 RPC/Future registry；供应商差异留底层客户端/model_provider。

首期规则执行为 **Python 受限子进程**：`-I`、严格 AST、OS CPU/内存等 limits、墙钟超时与可终止进程、JSON IPC 能力 broker；禁止 import、私有/dunder、open/eval/exec、宿主凭据及任意网络/SQL。lint 不等于隔离；这**不是恶意多租户安全沙箱**。无需另建 Docker launcher 架构；平台缺少必要 OS limits 时明确失败，绝不降级宿主 exec。初始预算10秒/3次外部能力/1次语义，可配置并测量。

## 5. 确认与激活

Session 默认复用有效绑定，缺失/关闭才新建；同 Channel 单在途，Session/task 不混用旧 binding。送达是最终模型可见视图完整后的可信 delivered 回传，不是 HTTP 接受响应。确认是 agent.result_submitted 被服务端持久接受 handled/no_action。

**本轮冻结项全部已送达且确认后**，才允许一次最终 progress/lifecycle/subscriptions/rule 激活更新；check/拟稿/试跑可在确认前做，但不得激活。确认、更新、结束均经 agent_protocol 落控制 Observation，再由 workflow_rules 固定 handler 处理；用户规则不能裁定授权。更新验证 binding、expected_version、候选 hash/路径、所有确认与取消状态；Agent 不直接给任意 rule_path，规则层解析已验证 hash 后供 Store 使用。

确认成功不因后续发布失败撤回；可更新，也可全部确认后 finish(unchanged)。历史证据不是本轮确认，Agent 本地 outbox 不是远端确认，外部动作不承诺 exactly-once。动态目标/规则/指导进入当前 User Prompt，稳定 System Prompt 不变，ReActAgent 保持无状态。
