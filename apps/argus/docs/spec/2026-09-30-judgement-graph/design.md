# Argus 批次与决策图设计

> 状态：已完成本轮代码与合成测试，执行器和批次当前实现见 [arch.md](arch.md)。真实用户库尚未迁移，服务未重启；外部端到端验证不属于本轮已完成证据。
> 配套实施计划：[plan.md](plan.md)。命名整理先按 [模块命名计划](../2026-09-30-module-naming/plan.md) 执行；本文的文件路径统一采用整理后的 snake_case。

## 1. 目标、范围和修订

Argus 根据一组消息和所需的只读材料，决定是否介入及将内容交到哪里，不生成回复。service 负责收件、组装、调度、保存和执行动作；strategy 节点负责判断，包括代码判断与 Jev 判断；材料节点负责只读获取；执行器负责依赖、条件路由、失败与运行记录。

本期实现：按会话成批、类型化 BasePolicy、代码内手动组装图、条件分支/扇入/有界重复、Jev 多种答案类型、批次持久化与恢复、submit/steer 动作选择。只接现有 QQ；不做记忆/知识 SDK、跨应用共享包、图编辑器、自动策略演化、并行调度或逐节点 checkpoint，不修改 Agent/Gateway。

替代先前几项不准确的表述：

- 不再将每条消息直接对应一次 Jev 请求；私聊即时，群聊按批判断。
- 删除“超过 300 秒就 silence”的自动过期规则；已收存消息正常参与判断，原始时间供策略参考。
- 不设群级最短判断间隔。5 秒静默/20 条/60 秒控制批次形成，不是调用限频；活跃群或频繁直接提及仍可能形成多个批次。
- “代码优先、Jev 兜底”表现为图的分支，不是把所有 if 藏回 service。
- 不再承诺“百来行复刻 LangGraph”。暂不引入 LangGraph 是当前范围选择，不代表等价能力或固定工作量；不用 checkpointer 的 LangGraph 也不必然产生第二个事实源。
- 不再声明“已有数据库无需迁移”。SQLite `create_all` 不会升级旧表，必须有显式、可验证的 schema 升级。

## 2. 命名和代码归属

文件/目录/function 采用 snake_case，类采用 PascalCase，常量 UPPER_SNAKE_CASE。保留平台和职责：`qq_service.py` / `QQService`、`base_policy.py` / `BasePolicy`。不重复创建 PascalCase 模块兼容壳，不改变现有类名和协议字段来迁就文件名。

```text
src/
  entity/
    observation.py, qq_observation.py       # 现有观察事实
    decision.py                            # 批次、节点输入输出、最终路由
  strategy/
    base_policy.py                         # BasePolicy[InputT, OutputT]
    qq/
      qq_batch_policy.py                   # 是否关闭候选批次
      qq_self_message_policy.py            # 纯自消息过滤
      qq_batch_facts_policy.py              # QQ 事实提取
      qq_intervention_policy.py            # 代码判 yes/no/undetermined
      qq_jev_intervention_policy.py         # 有歧义时调用 Jev
      qq_task_relation_policy.py           # 补充/新话题判定
      qq_action_policy.py                  # 最终路由
  decision_graph/
    graph.py                               # 节点绑定、入口、依赖、条件边、重复块
    runner.py                              # 无业务语义的执行器
  service/
    qq_service.py                          # 手动构造图；批次生命周期与执行副作用
    batch_scheduler.py                     # 计时/缓冲等机制，不作语义判断
  clients/
    jev_client.py, icarus_client.py         # 网络协议与资源生命周期
  dao/
    entities.py, observation_store.py       # 事实、关联、只读历史
    batch_store.py, schema.py               # 批次事务、schema 升级
```

材料步骤遵循相同异步调用接口，但不是“模型策略”。例如 `service/qq_materials.py` 内的 TaskStatusReader 接收现有 IcarusClient，只查询状态/历史，不提交或 steer。BasePolicy 只统一判断能力，不强迫材料步骤继承它；NodeBinding 接受二者的 `evaluate` callable。`utils` 不存图引擎、DAO 或连接客户端。

## 3. 正常消息路径与批次形成

正常路径保持：`connector → handler → DAO 持久化 → queue<Observation> → worker → QQService`。返回 HTTP 204 前 DB 必须提交。worker 从队列取对象，不改成 DB 任务轮询。QQService 的接收阶段只把消息交给 batch_scheduler，不等待 Jev 整个网络调用；否则唯一 worker 被模型阻塞，无法及时接收后续消息来成批。

- 分组键是 `(platform, account_id, subject)`，不能只用群号，也不按发送人拆分。
- 由 `QQBatchPolicy` 读取候选消息、时钟和参数，返回 `wait(deadline)` 或 `close(reason)`。service 持有计时器/执行关闭；策略不持有后台任务。
- 群聊：人类消息直接 at 或文本命中已配置称呼时立即关闭；否则静默 5 秒、20 条上限、开批后 60 秒上限先到先关闭。名字命中只改变判断时机，不必然判介入。
- 私聊：新的人类消息即时关闭判断，不等静默窗口；每次可带近期历史材料，因此即时不等于只看当前一条。
- 自消息保留为观察和上下文，可纳入候选批次；不触发私聊/名字即时通道，不推迟人类消息的静默 deadline。纯自消息批次按既有数量/最长窗口关闭，再由纯自消息节点结束，不调用 Jev。
- 消息不能无限延长开批时间；单次关闭至多取最早 20 条，剩余消息留给后续批次。关闭和后续新消息到达有确定的事务边界，不把新消息补进已关闭批次。
- 决策运行与收件解耦；同一 subject 的批次按接收顺序执行，不同时操作同一任务。不同 subject 可分别收件与计时；图执行并发用应用级有界队列/限额，默认一个批次执行槽位，不承诺跨群零延迟，也不另加静默冷却限频。

### 接收顺序与固定成员

新增 DB 单调递增 `ingest_seq`，表示收存顺序，不拿来源时间或随机 UUID 当游标。迟到消息仍有新的 ingest_seq，正常参与未关闭批次。

一个事务创建稳定 batch_id，并把明确选中的 observation ID 归属该批次；同一观察只归属一个批次。关闭操作只选择同 subject、尚未归属的最早有界消息，且条件更新成员时核实行数，失败则整个事务回滚。queue 到达顺序乱序时，以 DAO 的收存前缀重建候选，不丢掉更早记录。

**首版不另存 judged_through 水位表**：`batch_id IS NULL` 表示尚未固定到批次；已有 batch_id 则按原批次恢复。这是此前水位概念的无空洞实现，避免“已判断”水位混淆批次归属与提交完成。今后需要扫描优化时才增加派生游标，不能让游标成为另一套权威。

未关闭候选仅在内存；恢复时从未归属记录重建。已关闭批次持久化成员，重启必须复用同一 batch_id、相同成员和 submission_id，不能重新切批。

## 4. 节点契约和运行结果

```python
class BasePolicy(ABC, Generic[InputT, OutputT]):
    name: str
    version: str

    @abstractmethod
    async def evaluate(self, value: InputT) -> OutputT:
        ...
```

- InputT/OutputT 使用明确 dataclass 或已有公共类型；不把所有节点输入统一成任意可写 dict。
- 判断结果与运行状态分离。例如 InterventionResult(verdict=yes/no/undetermined, reason) 中 `undetermined` 是成功判断，不是异常。
- 执行器记录 `succeeded/failed/skipped/blocked`。条件未选中是 skipped；必需输入失败是 blocked；不得把缺失输入当作 false。
- 普通节点产出中间结果，不认识“下一节点 ID”；边和输入映射由 service 构图时配置。
- 只有最终路由节点产出 `RouteDecision(action=silence/submit/steer, target_task_id?, reason)`。需要介入的 yes 是中间事实，不使图提前结束；纯自消息过滤也应路由到最终 silence 节点。
- `continue` 作为概念保留：完成非终态节点且存在被选中的后继节点就是继续。它不是 wake，也不与 bool/Choice/Score 混进同一个字符串输出字段。
- 构造参数注入只读 reader、JevClient、时钟与阈值；不要把 DAO 写接口、Gateway submit 能力交给 Policy。

## 5. 图定义、执行器和失败语义

Graph 定义节点 ID、`evaluate` callable、输入构造器、选中的前驱入口、必需依赖、条件路由函数及唯一最终输出。GraphRunner 不 import QQPolicy 或分析聊天内容。

第一版顺序调度，无并行节点执行；仍实现下列图语义：

1. 编译/验证阶段拒绝重复 ID、悬空边、不可达终点、未声明依赖，以及普通图中的环。
2. 节点必须“路径已被选中 + 必需依赖均成功”才执行。节点输出按 ID 存在只读结果视图中，不把所有 facts 随意合并为一个字典；节点间同名字段不会覆盖。
3. 分支：route 函数明确返回一个或多个目标；多目标可顺序执行。扇入使用 `all` 或 `any` 明确等待规则；any 等候相关候选路径的状态确定后，恰好一个成功来源才继续，多个冲突成功来源报失败。all 必需依赖未选中则该扇入 skipped，失败则 blocked。不能把条件分支汇合写成无条件等待全部兄弟分支。
4. 最终只有一个一致的 RouteDecision；不同分支产生冲突最终决定时标 graph_failed 并保守 silence，不按“最后写入者胜”。
5. 每个节点有超时，整图有总 deadline 与最大执行次数（建议默认 30 秒、32 次，可配正数）。取消用于停机，应继续向外传播，不伪装成模型 silence。
6. 节点失败后 warn，依赖其结果的路径 blocked；没有依赖的分支可完成并留下记录，但不能使用失败路径的结果进行外部动作。若无法得到完整的有效最终决定，图结果为 action=silence、status=failed，而非正常业务 silence。第一版不存在忽略必需材料失败并继续动作的隐式降级。
7. 有界重复使用显式 `RepeatBlock(body_graph, next_input, until, max_iterations)`，而非任意回边。每轮使用新的输出空间和轮次标识；跨轮只有显式 next_input 传递的值能使用。循环计入总执行预算；达到上限仍不能完成则失败收工。QQ 首图无环，执行器用合成节点验证此能力，不添加虚构的检索业务节点。
8. 材料结果仅在同一次运行、相同查询参数与读取快照内复用；扩展查询范围或开始新轮次不得盲目复用旧结果。首版依靠共同材料节点的输出共享，不提供跨运行全局缓存。

本期不使用 LangGraph；不仿照整个框架实现 DSL。策略节点与 execution context 解耦方便未来适配，但更换引擎仍需要输入、路由、错误语义的适配和回归测试，不能承诺节点绝对零修改。

## 6. Jev 能力与代码优先的 QQ 首图

已核对安装的 TypeSafe SDK 0.7.2：

- `Noul` / `NoulAnswer` 是是/否原语，返回 noul 数值；不是专门的“无内容检测”，也没有 Choice/Score 那样的独立 confidence/probabilities 字段。
- `Choice` 返回 choice、confidence、probabilities。
- `Score` 按有序 rubric 给 score、confidence、legend、probabilities；排名是对若干可比评分结果由调用方排序，不是任意列表排序工具。

JevClient 增加 `ask(state, questions) -> SystemOneResponse`，保留 SDK 的三种答案和 usage，校验请求的问题均有对应类型答案。初次 + 两次重试，SDK 内部 retry=0；最终失败抛出带安全类型的 JevCallError，由节点/runner 记录故障。当前代码统一使用 typed ask；旧 `decide` 单选兼容入口在迁移调用方后删除，避免保留两套判断接口。

独立问题可在一次 ask 中询问，后续投影节点各取对应类型答案；投影仍是逻辑上的不同判断。首版由 service 显式组装一次多问题请求，不自动合并任意节点。后一问依赖前一答案或新增材料时必须新调用。

QQService 构图的具体路径：

1. `QQSelfMessagePolicy`：批次是否含人类消息；纯自消息走最终 silence。
2. `QQBatchFactsPolicy`：提取私聊、at、称呼命中、各发言人、当前/近期转录等事实。
3. `QQInterventionPolicy` 代码判定：人类私聊且有文本/图片，或人类消息明确 at 机器人且有文本/图片 → yes；其他非纯自消息情况 → undetermined。文字提到 icarus、无 at、只有表情都不能凭代码等同“无关”；交 Jev 理解。上述具体规则是本轮待审的首版默认值，可在策略文件替换，不写死 service。
4. undetermined → `QQJevInterventionPolicy`：按最小 Choice 标准判 yes/no（解释群/私聊、称呼只作信号）。yes 分支与代码 yes 汇合，no 走最终 silence。
5. 只读 TaskStatusReader 查询 session.get / task.get_status，已有 task ID 只是候选；`not_found` 和 `unloaded` 有明确语义，网络失败不是“无任务”。
6. 无活动任务 → `submit`。有活动任务：`QQTaskRelationPolicy` 有可靠结构化关联才代码判补充；否则 Jev Choice 判 supplement/new_topic。不能因“同一群存在活动任务”就直接 steer。最小首版没有可靠引用映射时统一走模型关系判断，不伪造规则。
7. `QQActionPolicy` 汇总成功结果：无介入 → silence；有介入+无活动任务/新话题 → submit；补充+已核实活动任务 → steer。图内不实际发送消息。

QQ 首图示例体现三种路：纯代码 silence、纯代码介入后 submit、需要 Jev 的条件分支；用 fake client 计数证明简单情况没有模型调用。有 at 的立即关闭与是否介入是独立规则，不混淆。

## 7. 持久化、schema 升级与恢复

`observations` 保留不可变事实并新增 `ingest_seq`（单调接收序）、`batch_id`（归属）。旧处理列的数据先完整移入批次的 route/trace/error/operation 字段，升级事务成功时删除旧列。新版不保留单消息处理兼容路径（按实现中用户补充的清理要求）。

新增 `decision_batches`：batch_id、platform/account/subject、成员范围与数量、关闭原因、状态、graph_version、route、reason/error、运行路径 JSON、workspace_path/key/session_id、candidate_task_id、target_task_id、submission_id、最终调用方法/参数快照、创建/更新时间。成员由 observations.batch_id 明确绑定；无需另建成员表。session_links 中的 task ID 只是缓存，实际状态从 Gateway 核实。首版共三张业务表，无额外水位表；schema 版本用 SQLite PRAGMA user_version。

状态：`ready → judging → decided → submitting → submitted`；正常无动作是 `finished`；节点失败 `failed`；发送边界不确定 `submission_unknown`。persisted `decided` 保存完整 route、目标和 prompt，不因传输重试重复调用模型；因任务状态变化必须重算路由时只重跑路由阶段并记录轮次。

原子边界：

- close_batch：写批次和成员归属同事务；crash 后全有或全无。
- save_decision：写判断结果与路径；此时还没有 Gateway 写操作。
- begin_submission：持久化具体方法、参数快照、目标、稳定 submission_id，事务成功后才网络调用。
- receive_receipt：记录可靠回执，更新关联候选 task。steer 的 `accepted` 表示追加已接收，不等于任务完成。

恢复：未归属观察重新成候选；ready/judging 批次复用原成员和 ID重新判断；decided 复用已存决定并重验实际目标；submitting 无可靠回执改 unknown，不自动重发；submitted/finished/failed/unknown 均不作为新批次再跑。

旧库升级必须在 Argus 停止、数据库独占维护时进行，使用 SQLite backup API（包含 WAL 数据）创建私有备份后事务化升级。依据旧 received_at/id 回填 ingest_seq；每个旧 observation 创建一个兼容单条批次，保留既有 session/task/submission_id 与结果：pending/未发送 processing → ready；带 submission_id 且未确认的 processing/submitting → unknown；submitted/finished/failed/unknown 保持终结或不确定语义。旧 submission_id 不改成新值，不重放历史已提交数据。保存当前 session_links。成功后再允许新版服务启动；重复执行升级不重复创建批次。正常启动发现旧 schema 必须明确提示维护命令，不能清库或自动丢历史。无需引入 Alembic 全套，但不能假定 create_all 能迁移。

## 8. 动作执行与竞态

service 执行图结果前再次查询目标是否仍可用。submit 使用原 session 校验/稳定建会话逻辑；steer 使用现有 RPC 并显式检查返回 status，不能只看有 task_id。

- `accepted`：记录已交付目标任务。
- `not_running/already_finished` 明确未追加：允许一次重新核实路由，保存“steer 被拒绝”证据后改为 submit，使用方法区分的稳定 ID（例如 `batch_id:submit` 与 `batch_id:steer:task_id`），避免不同 payload 重用幂等键。批次 ID 是这些操作 ID 的根，不强制所有方法复用同一个字符串。
- 连接中断、超时、内部错误、不可识别状态：unknown，不转成第二次 submit。
- 无活动 task、task_status_unavailable、网络失败必须分清；有缺失状态时先读 session 快照/有界历史，只能用明确 task ID 关联，不能凭文本相似性核实提交。

外部发布、工具授权属于 Agent/平台现有边界；判断 yes 不代表授权任意工具。需要真实模型/QQ 验证时另行确认范围与费用。

## 9. 可观测性、测试与边界

运行记录写批次：run_id、graph_version、节点 ID/版本/轮次、执行状态、耗时、分支结果、错误类型、材料来源与截断标志；不默认复制全部聊天正文或密钥。完整的有类型节点输出在内存供后继使用，DB 路径记录保存安全摘要。故障 silence 与正常 silence 有不同 batch status/reason。Warn 不因 swallowed exception 消失。

测试覆盖：静默/条数/时间关闭、私聊即时、称呼/at 提前关闭但不等于最终动作、自消息不延长静默、跨账号隔离、迟到不丢、批次成员原子绑定与恢复、旧库所有状态迁移、条件路径/扇入/局部阻断/冲突终态/重复块预算、代码判断零 Jev 调用、一次 ask 多类型答案、多步答案依赖、Jev 三次失败、steer 执行竞态与 unknown 不重发。配置默认值为 5 秒/20 条/60 秒、Jev 每次 2 秒总共 3 次，图默认 30 秒/32 次执行；图预算是上限而非保证完成时间。过长转录不得无提示截断：节点输入显示截断标志，原始 batch 成员仍保留并对最终发送施加明确大小限制。

本设计仍待用户评审；命名计划可独立执行并测试，随后图计划分步骤落地。在任何阶段不把尚未实现的能力写进 arch.md，不提前修改用户实际数据库或重启服务。
