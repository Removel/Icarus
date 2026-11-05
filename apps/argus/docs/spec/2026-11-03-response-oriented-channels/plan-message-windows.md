# 消息窗口恢复：设计与实施计划

> 执行使用 `superpowers:executing-plans`，先失败测试，再最小实现；不重启生产服务、不重放真实消息。2026-11-05 用户要求：先commit当前代码，再写文档，再开始开发。

**Goal:** 恢复 feat/argus 的来源消息窗口；窗口消息先一起理解，同一Channel整批判完再交付，不能逐个聊天气泡启动回答。

**Architecture:** Observation仍为原始消息，窗口只是按来源和时间组织输入的稳定范围，不等于Channel或Session。窗口成员存入既有Observation.detail，规则仍逐条决定业务归属，但每次看完整窗口；Channel以一个事务保存同窗口的决定，投递必须等窗口在该Channel内判断完。Agent永远收到本轮涉及窗口的完整原文，不靠历史工具补缺句。

**Tech Stack:** 现有Python、SQLAlchemy/PostgreSQL、RuleEngine、Event、pytest；无新依赖、新表或通用调度框架。

**Spec:** 用户确认的消息窗口机制；参考 feat/argus `common/tasks/observation_scheduler.py`（quiet5s/max30s，但不复用私聊强信号绕过等待）；配套[Agent交付计划](../../../../agent/docs/spec/2026-11-03-response-oriented-channels/plan-observation-delivery.md)、[Gateway计划](../../../../gateway/docs/spec/2026-11-03-response-oriented-channels/plan-observation-delivery.md)。

## Global Constraints

- 一个Icarus实例是一个独立对象；Source是外部区域；窗口不是任务路由身份。
- 不以“不到一秒六句话”规定产品行为；5秒安静窗口、30秒最长等待可配置；私聊/@不绕过聚合。
- 当前窗口内原话都是正文。是否需要确认某条工作项，不得决定这条原话是否进入Agent输入。以前已处理对话才是history。
- 保留来源/发送者/时间/引用；不合并成丢失原消息身份的一段字符串。
- 保留现有规则入口、原子commit、持久Session关联与未知外发不重放；不修改ReActAgent或在Hook里调度。
- 不重新评估全部历史终态消息，不迁移生产库，不改变旧轮次的请求/指纹。

## Review Focus

1. 一组窗口尚未判断完就被begin_round抢先冻结：Task3。
2. 解释句不单独触发回应却从正文消失：Task2/4。
3. 连续不断消息导致永远等待、满预算丢最后一条：Task1。
4. 进展version更新使已接受但未交付消息被重新过滤：Task3，原窗口正文不丢。
5. 命名释放锁期间同事件重复分类：Task0。

## 设计决定

### 窗口

生产默认quiet=5秒、max_wait=30秒，max_items=20（沿用一次交付上限）；quiet=0为显式兼容模式。窗口从已持久消息的observed_at及position计算，不依赖单个进程的计时器。按同一Source有序取尚未组织的消息；遇到quiet间隔或max_wait边界切段。安静已到、最长等待已到、或达到批次数量上限才封存，不在HTTP入口sleep。超出条数留下一窗口，不丢弃。

`detail.message_window`只保存稳定窗口ID（首条Observation ID）；同一窗口在短事务里原子分配。协议不能注入此保留字段。已封存成员和原始消息不重排。新修订按最新消息规则处理，不默默改已经投递的冻结内容。

### 判断与投递

`process_channel`读取unread后按窗口处理，不每条passed就通知。每条规则仍接收自己的event，但broker向semantic上下文注入完整`current_window`，与不晚于当前消息的recent_history分开，不伪装历史截止。全部决定通过`decide_window`同一事务落库，CAS失败整批回滚，最后只发布一次RulePassed。

新建发现同样等待窗口封存、获得窗口上下文。首条new依旧只seed自身；`begin_round`必须检查该来源窗口末尾已被当前Channel判断（来源游标达到窗口末尾），避免命名后立即投递首条而其他句尚未判完。这样新话题仍能按现有规则另行发现，不强行把同窗口全部归一个Channel。

round保持原`observation_ids`作为确认范围，新增`window_observation_ids`保存本轮涉及的完整当前窗口；不是可选附件，也不是history。这些正文即使不单独触发回应，也总是随轮次交给Agent。工作项确认范围与正文范围分开是事务约束，不是内容重要性分类。

### 兼容与第二阶段

旧轮次不增加字段、不重建wake。新WakeRequest增加可选window_items/history_items，既有items仍是需要确认的工作项。Bridge发送前照常持久化完整原请求。第二阶段available/read/wait及领取后续不可变批次另行设计；本轮执行中新消息保留下一窗口，不修改本轮确认集合，不新增永久轮询。

## Task0：修复检查点已有的命名并发失败

**Files:** `plugins/workflow_rules/plugin.py`；`test_channel_naming.py`、`test_discovery_e2e.py`。

- [ ] 检查点Argus349通过、2失败已真实运行。用同一个Observation在命名等待中再次discover的用例继续锁定RED。
- [ ] 增加事件级在途门禁，命名期间同观察不重复分类；finally释放，不用持有全局锁等待模型。
- [ ] 重跑2个失败文件并GREEN，不放宽断言。

## Task1：稳定来源窗口

**Files:** 新增`apps/argus/src/argus_app/plugins/workflow_rules/message_windows.py`；修改`config.py`、`application.py`、`plugins/workflow_rules/factory.py`、`infrastructure/store.py`。

**Interfaces:** `Store.message_window(observation_id, *, quiet_seconds, max_wait_seconds, limit=20) -> list[Observation]`，未到窗口边界返回空；`Store.window_members(observation_id) -> list[Observation]`只读已封存成员。纯helper负责时间切段，DB负责原子封存和position顺序。生产配置`message_quiet_seconds=5`/`message_max_wait_seconds=30`，环境变量`ARGUS_MESSAGE_QUIET_SECONDS`/`ARGUS_MESSAGE_MAX_WAIT_SECONDS`。

- [ ] 测试消息间隔2秒持续补充会延长quiet；到30秒必须放行；不同Source不互相拖延；20条上限余项不丢；重启复用封存成员；旧终态不全量重放。
- [ ] RED后实现，使用可控时钟而非sleep。
- [ ] `bash apps/argus/scripts/test.sh apps/argus/test/argus_app/infrastructure/test_message_windows.py -q`必须实际通过。

## Task2：窗口正文进入规则语义

**Files:** `plugins/workflow_rules/plugin.py`、`discovery.py`；新增`test_message_windows.py`。

- [ ] 为response/discovery broker注入`current_window`有界有序原文，覆盖脚本伪造字段但保留其他业务context；历史截止不变。
- [ ] 窗口未就绪不判，不因私聊或@跳过等待；quiet=0保持旧接口兼容便于历史用例。
- [ ] 测试6条、任意条数和不同间隔；每次语义调用看到完整窗口。解释句不靠主动history查询才能看到。
- [ ] RED→GREEN；模型替身只证明契约，不宣称真实语义质量。

## Task3：整批落库与一次投递

**Files:** `infrastructure/store.py`、`plugins/workflow_rules/plugin.py`；新增`infrastructure/test_window_decisions.py`。

**Interfaces:** `decide_window(channel_id, decisions: list[tuple[str,dict]], expected_version: int) -> bool`；复用单条decide的验证，在一个Channel事务内全部落库；失败全回滚。`begin_round`补充窗口完成门禁与完整正文ID集合。

- [ ] 测试落库中途/CAS失败无部分游标推进；并发begin_round不能捡走半窗口；新Channel首条seed后要等剩余成员判完。
- [ ] 相同Channel涉及不同窗口/Source仍能独立判断；不把窗口变成独占Channel。
- [ ] round工作集合有界，窗口正文不能偷偷被截断；超预算显式阻塞或保持后续工作，不能标已处理。

## Task4：跨应用交付与验收

**Files:** `plugins/icarus_bridge/plugin.py`；消费Agent新增window_items/history_items契约；扩展真实TCP discovery闭环及Gateway合成平台测试。

- [ ] 发送round涉及窗口的完整原文和必要近期历史；保留items确认边界、512KiB原请求限制和不可变重试。
- [ ] 回归：6句全部出现在首次模型输入中，同一事项一轮提交；解释句完整可见；不逐句发送五个问答。
- [ ] 最小文件→完整Argus隔离PG→Agent/Gateway→compile/diff。真实模型、真实QQ发送和服务重启另获授权。

## 交付状态

当前是实施前文档，不能据此称窗口已恢复。运行中后续工作读取是第二阶段，不在此阶段扩展冻结集合。对生产真实六条消息只读取证，本轮使用合成复现，不自动重放或替用户补发回复。
