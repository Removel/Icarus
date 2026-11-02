# Argus Plugin Refactor Implementation Plan

> 2026-11-01：用户已批准开始实现，目标分支 `feat/argus-plugin-refactor`；协调者报告当前基线包含已 fetch 的 `origin/feature`。此处记录执行事实而非重新要求授权，也不声称本次文档任务创建/同步了分支。本次只改文档，代码与测试完成状态见实际执行证据。

**Goal:** 协议入库→Icarus 总线→文件规则/能力→Bridge→Agent 处理→确认→可选激活更新。

**入口：**[技术方案](plan-technical-design.md)、[契约](plan-contracts.md)、[执行台账](plan-execution.md)、[当前源码架构](arch.md)、[跨应用约定](../../../../../spec/2026-11-01-channel-observation.md)、[Agent](../../../../agent/docs/spec/2026-11-01-channel-observation/plan.md)、[Gateway](../../../../gateway/docs/spec/2026-11-01-channel-observation/plan.md)。

## 1. 不可变约束

- Argus 恰好 observed_items、channels 两张表；没有 channel_observations/wake_requests 或其他支撑表。当前 pending/round/送达/确认/有限回执仅在 channels.runtime；控制结果在 observed_items.detail 严格有界保存。不迁移旧数据。
- 复用 Mem0 现有 PG 容器，独立 Argus DB/受限账号；生产不新增专用 DB 容器，不自动清库/建角色。
- main.py 进程入口，application.py 组合公共 PluginRuntimeHost；不复制 runtime。plugins/protocols 与 plugins/rule_capabilities 仅分组，workflow_rules、icarus_bridge 为同级 leaf；所有具体 leaf 平级注册。
- 必须补最小通用嵌套 builtin discovery；不能借 external plugin_dirs 绕过。内部 helper 不注册子插件。
- 冻结本轮全部确认后才能改进展/生命周期/订阅/生效规则；确认前可 check。默认复用有效 Session，同 Channel 一个在途执行。
- Python 受限子进程 + AST + OS limits + 超时/JSON IPC；不宣称恶意多租户安全沙箱，不强制 Docker launcher，不降级宿主 exec。
- ReActAgent 无状态、动态背景进 User Prompt、Hooks 不决定流程，供应商差异留 model_provider/客户端。未经授权不做付费 API、平台发送、生产数据/Git操作。

## 2. 任务与依赖

每任务先最小行为测试 red，再最小实现 green；缺依赖/缺 DB/收集失败不是行为 red。测试镜像 `apps/argus/src/argus_app`，应用自有依赖和环境不移到仓库根。旧业务代码可复用底层算法/可靠性，不做兼容迁移。

| 任务 | 文件/交付与验收 | 依赖 | 原估算人日 |
|---|---|---|---:|
| P0 | 记录实际基线失败身份、依赖与隔离 PG/规则目录权限；不等待架构重复确认 | 无 | 0.5–1 |
| T1 | `apps/agent/src/icarus/runtime/discovery.py` 最小 builtin 分组发现；Argus pyproject、main.py/application.py/config.py、manifest/contracts 与真实 Host 生命周期 | P0 | 1–1.5 |
| T2 | Argus `contracts/models.py`、Store/repositories、`db/schema.sql`；严格两表、同来源修订、cursor/pending 原子与容量背压 | 冻结接口 | 1.5–2 |
| T3 | `plugins/protocols/{qq_protocol,feishu_protocol,agent_protocol}`；提交先于通知、认证决定控制 kind、幂等与重放 | T1/T2 | 2.5–4 |
| T4 | `plugins/rule_capabilities/{observation_query,mechanical_rules,semantic_judgement,memory_evidence,relationship_evidence,knowledge_evidence}`；有界授权查询、typed 语义、真实后端/明确 unavailable | T1/T2 | 2.5–4 |
| T5 | `plugins/workflow_rules`、RuleFiles/Engine、rule_sdk；hash 文件、check、子进程限制、版本重判、公平扫漏 | T2/T4 | 3–4 |
| T6 | workflow_rules 固定控制 handler + Store 事务；当前轮确认/激活/finish、稳定 processing 结果 | T3/T5 | 2–3 |
| T7 | `plugins/icarus_bridge`；runtime.round 冻结、Session 复用、HTTP 控制面、丢响应对账 | T5/A1/G1 | 1.5–2.5 |
| A1–A3 | Agent 身份/正常投递、动态提示/工具、实际送达确认/outbox/授权恢复 | T6/T7 接口 | 2.5–3.5 |
| G1 | Gateway 鉴权、严格公开模型、薄 HTTP 转发 | A1 接口 | 0.5–1 |
| T8 | 真实 Host + 隔离 PG + 确定性 HTTP fixture 的闭环/容量/恢复验收 | 以上 | 1.5–2.5 |

上述原粗估合计19–29人日（114–174有效小时），不是交付承诺；新增明确的 discovery 与两表有界协调要求需在首批测试后重新估算，不能假称原预算已逐项验证。可选能力并发 C1 仅在实测串行不足后加0.5–1人日，默认 Runtime 行为不变；不在首期先造并发框架。

## 3. 聚焦验收清单

- [ ] T1：flat/grouped builtin 都发现；到 manifest 即停；无组注册、重复/override 校验保留；不导入实现扫描；Host 在工厂前解析契约，停止能 drain。
- [ ] T2：实际 schema 恰好两张表；A→B→A、控制相同 ID 不同 payload 拒绝；两 Channel 独立；ignore 无逐项日志；pending 满不推进 cursor；JSON 大小/字段强校验；真实隔离 PG 并发事务。
- [ ] T3：commit 失败不发通知，commit 后丢 Bus 可扫漏；平台文本不能造控制身份；协议超时返回 pending 而不谎称取消。
- [ ] T4：QQ 先到、飞书后到可读历史组合；查询范围受订阅/账号/source_bounds 限制；查询不推进 cursor/确认历史；服务失败与空结果区分。
- [ ] T5：缺文件/hash 错独立 error；旧版本晚到重判；路径/AST/死循环/内存/超时/IPC 权限限制失败有界；check 不激活，不在宿主 exec；缺必要 OS 支持则明确不可执行。
- [ ] T6：未送达/部分确认/本地 outbox 未同步均拒绝激活；全部确认后一次更新或 unchanged；同 request/report 重放幂等；文件先发布、PG 失败旧指针有效；确认不因发布失败倒退。
- [ ] T7：默认复用/失效重建、同 Channel 单在途；相同 wake 指纹不改；remote accepted 不等 delivered；未知状态保占用；新事件不扩当前冻结集合。
- [ ] T8：各提交后通知/回执丢失均恢复；发布失败不重做已处理动作；paused 积压保留；completed 无 active actions 才清理；容量、保留保护与停止排空实测。

## 4. 执行顺序与验证

最先落 discovery/组合根和两表 Store 的聚焦测试与实现，再接协议/规则/能力，再联合 Bridge/Agent/Gateway。冻结接口可让 Store、插件、应用适配按明确所有权并行，但本计划不自动授权额外 agent 或 Git 操作。

按最小文件/目录→受影响应用套件→compile/typecheck/diff 顺序；例如 Argus `scripts/test.sh` 必须确实收集 `test/argus_app`，再跑 `make test-agent`、`make test-gateway` 及受影响 compile/typecheck、`git diff --check`。隔离 PG 必需；不把测试脚本自动起容器误当生产拓扑。记录真实命令、退出码、失败身份，不修无关失败。真实平台/付费模型 smoke 仍按权限，不以 fixture 冒充联通。

## 5. 首期范围

单机、单调度实例、有界任务；QQ/飞书/Agent 协议、查询/机械/语义必需，记忆/关系/知识插件按已配置后端启用。不含自动发现新 Channel、多 Agent Bridge、多机制品分发、管理 WebUI、历史迁移。保留原有可靠交接不变量，不保留旧表与旧协议路径。

## 7. 执行裁决（不再重复询问）

Python 规则文件/受限子进程；本轮全部确认门禁；确认前 check；有效 Session 默认复用；pending=256、每轮≤20、每 Channel≤16来源、规则≤32KiB、控制 processing≤8KiB；默认30天留存但保护活跃积压；自动发现不进首期。新增 runtime 总量/回执上限见技术方案，作为明确可配置工程默认，不阻塞开工。

实际缺 DB 权限、平台凭据或 OS limits 只阻塞对应部署/真实 smoke/规则执行，需记录而非重新提问架构。用户批准实现不等于批准清库、付费调用或 Git 提交。本次 docs 修订不宣布任何实现任务完成。
