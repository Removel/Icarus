# Argus 当前实现架构

核验日期：2026-11-02。对应本工作区 `feat/argus-plugin-refactor` 的未提交实现。本文依据实际源码与测试；设计提案保留在[技术方案](plan-technical-design.md)，具体测试证据见[验收记录](plan-validation.md)。不表示已部署、已合并或已接真实平台账号。

## 1. 启动与基础设施

`bin/icarus start argus` → 应用 `scripts/start.sh` → 已安装 `argus_app.main` → `application.create_app`。

- main加载环境配置并启动Uvicorn；application协调PG Store、HTTP客户端、规则文件目录和现有Icarus PluginRuntimeHost。
- Store启动创建全新部署所需两张表，不迁移/删除已有业务数据，不创建PG角色或容器。
- Host使用 `builtin_package='argus_app.plugins'`，发现11个具体叶插件；新增通用builtin分组遍历，遇manifest即停，external扫描语义不变。
- ready在Store/Host就绪后置真；随后启动应用恢复扫描。退出先拒绝新业务、取消并等待扫描、停止Host，再关闭客户端/连接池。
- 生产连接已有共享PG容器中的Argus独立库和受限账号。新集群init可按可选ARGUS_PG_PASSWORD创建角色/库，现有集群不自动变更。Argus不启动或停止共享PG。

## 2. 实际源码职责

```text
argus_app/
├── main.py / application.py / config.py
├── contracts/
│   ├── models.py                  # Observation、Channel、Decision、DomainError
│   ├── events.py                  # ObservationRecorded、RulePassed
│   └── capabilities.py            # 定向能力契约与函数映射
├── infrastructure/store.py        # SQLAlchemy asyncpg，两表事务
├── model_provider/semantic.py      # typed Jev客户端适配
├── rule_sdk/
│   ├── engine.py                  # AST校验、子进程IPC、预算/结果校验
│   ├── worker.py                  # -I独立Python与OS资源限制
│   └── files.py                   # 内容寻址的不可变规则文件
└── plugins/
    ├── protocols/
    │   ├── qq_protocol/
    │   ├── feishu_protocol/
    │   └── agent_protocol/
    ├── rule_capabilities/
    │   ├── observation_query/
    │   ├── mechanical_rules/
    │   ├── semantic_judgement/
    │   ├── memory_evidence/
    │   ├── relationship_evidence/
    │   └── knowledge_evidence/
    ├── workflow_rules/
    └── icarus_bridge/
```

具体插件各自manifest/factory/plugin，平级注册；分组不是父插件，Repository/RuleFiles/Engine不是插件。能力调用直接走Icarus request/response和现有correlation/timeout，不另造EventBus或Future路由。

## 3. 恰好两张表

### observed_items

`id, position, source_key, message_id, kind, revision, observed_at, content, detail`。

- position是数据库分配的入库位置，用于来源消费cursor；observed_at是UTC接收时间，不再把它当抗时钟回退的消费序号。
- 同source_key的advisory事务锁持续到提交，避免较晚提交的旧位置被已推进cursor越过。
- latest比较只针对该消息最新内容，A→B→A产生三次修订；相邻相同内容重投返回原id。
- 控制事件以source_key=`agent:<binding_id>`、message_id=request_id固定输入hash；重复同内容返回同记录，不同内容冲突。
- detail保存有限来源信息、控制binding/payload和固定result，不保留每次模型/能力调用日志。

### channels

`id, title, goal, progress, lifecycle, version, subscriptions, rule_path, rule_hash, runtime`。

- subscriptions为source_key→位置cursor/null，非独立订阅表；version只在业务更新时递增，不因消费/回执递增。
- runtime.pending最多256项，保passed/deferred/error、判定版本和有限重试；ignore仅推进cursor。
- runtime.round至多一个当前轮，含wake_id/binding_id、冻结事件、送达/确认、远端Session/task和本轮更新标识。不累计历史轮次。
- 不存在channel_observations、wake_requests、规则/版本/确认/命令表。
- 过期非冻结修订/被移除订阅的pending事务内退休，冻结输入直到结束/对账保持稳定。

## 4. 事件与执行主链

QQ接收签名OneBot消息，来源为 `qq:<self_id>:group:<group_id>` 或private；飞书接收官方 `im.message.receive_v1`，来源为 `feishu:<tenant_key>:chat:<chat_id>`。缺账号/租户拒绝，不把多个账号的数据合并。

协议receive先Store.ingest提交，再publish ObservationRecorded；workflow_rules处理候选Channel、读取文件、查询已存多来源历史，经typed能力判断，把结果/cursor写Channel后publish RulePassed。

2026-11-02已补无人订阅来源发现：默认受信机械脚本识别明确请求（可配置ARGUS_DISCOVERY_RULE_PATH或关闭ARGUS_DISCOVERY_ENABLED），走同引擎；Store.record_discovery用同订阅协调锁原子检查active/paused归属、幂等创建Channel、seed首条passed并写事件detail.discovery。无虚拟Channel、无新表。发现history授权只当前事件来源；重复通知遵守5/30秒退避与最多3次，已有Channel正常处理过的记录将非终结发现状态标ignored，避免完成/退订后重新建事。

Bridge.begin_round事务冻结通过项，再通过Gateway受保护HTTP接口ensure_session/wake。批次最多20项且完整序列化items总量不超过384KiB，为Gateway512KiB包络留余量；单条超限保明确delivery_item_too_large耗尽状态，不截断后假称送达。

总线丢通知由Store水位/pending/控制结果恢复。恢复扫描独立隔离各Channel错误和Bridge失败，暂停/完成但仍有当前轮的Channel也继续状态对账，不新建inactive轮次。

## 5. 当前规则SDK（与早期异步草案不同）

规则文件只包含一个同步函数，输入普通JSON字典：

```python
def decide(event, channel):
    history = capability("history", {"limit": 20})
    return {
        "passed": any("review" in row["content"]["text"]
                      for row in history["observations"]),
        "reason": "review evidence",
    }
```

函数名称为history/mechanical/semantic/memory/relationships/knowledge；由宿主映射到typed Icarus Capability。不支持早期草案的async decide、声明头或任意import/helper函数定义。RuleEngine.evaluate是宿主异步Interface，规则代码本身同步，通过IPC等待受控能力结果。

源码≤32KiB；子进程输入显式≤2MiB且去掉Channel.runtime/事件detail，避免向规则泄漏binding；输出/能力返回≤128KiB；最多3次能力调用、1次semantic；应用默认总墙钟10秒。严格AST限制、-I、OS CPU/内存/文件限制和kill/wait提供受信单机受限运行，**不是恶意多租户安全沙箱**。

历史只看订阅范围、捕获的source_bounds内最新修订；查询不推进cursor，不确认历史。实际query可取1..100条，由输出字节预算进一步约束；Agent历史HTTP入口最多50条。

## 6. Agent、控制回流与确认

Gateway使用受保护 `/argus/session`、`/argus/wake`、`/argus/status`；普通RPC不能创建/提交/steer保留的argus-* Session。Agent应用复用正常SessionRuntime/AgentPlugin/ReActAgent执行链，不建立第二runtime。

Agent适配插件提供7个工具和当前User Prompt指导。应用既有tools参数变为实际注册表allowlist：在lazy catalog/executor绑定前去掉非允许工具，保留tool_search/tool_execute，不能间接调用bash/MCP。默认非受限Session不变。

只有本轮完整prompt出现在真实工具执行最终视图后，才回传agent.delivered。Agent显式confirm后的远端applied receipt才登记handled/no_action；HTTP接受和本地outbox不是确认。

确认/check/update/finish经 `/agent/events` 到协议插件先存事件，再由规则固定handler处理；更新前Store事务检查全轮确认、binding、expected_version和单次更新资格。候选hash由规则层验证为本Channel文件。省略更新字段保留原值，确认后允许unchanged结束。

控制结果统一 `{applied: bool, code?: str, ...}`，不是外层Capability OK。失败或丢回执按原request_id恢复；Agent journal回执按task/binding隔离，复用Session下一轮可重新使用同request_id而不串回执。正常finish撤销绑定，远端终态状态回收内存；未知提交不盲建新执行。

## 7. 明确边界

- 初期单调度实例、规则/控制顺序锁，没有多副本租约或吞吐公平性保证；局部故障不阻断其他Channel恢复。
- 已进行少量真实模型+合成平台HTTP验证，结果见[真实测试记录](plan-real-model-validation.md)；工作闭环通过，生活场景确认后写回前触及预算未完成，不能据此宣布无人值守可靠。
- 飞书加密请求体当前明确拒绝；编辑/撤回额外官方事件、管理WebUI、多机规则制品和自动留存GC尚未实现。自动发现目前只覆盖无人active/paused订阅的来源，不会在已有群订阅中独立发现另一件事。
- 已确认事件与后续状态/规则发布是两步事务；执行在两步间中断时，当前没有“仅补发布”的恢复阶段。按用户要求停止架构扩展，保留待决策问题。
- pending计数、决策/进程/包络等有明确上限；早期提案中的统一runtime≤1MiB、processing≤8KiB、完整30天GC并非当前全部已实现约束，不能据提案宣称已经存在。
- 动态规则无法执行任意平台动作；首期Agent仅开放Argus适配工具，新增真实外部动作需独立授权与具体工具接入。
- 没有生产数据库/角色创建、旧数据迁移、部署、提交或推送操作；共享基础设施测试是配置和隔离PG测试，不是对生产容器的变更。
