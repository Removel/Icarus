# Argus QQ 观测闭环实施计划

> 执行记录：本计划保留实施时的任务拆分；后续按用户确认的职责边界，将 Jev/Gateway 通信放在 `src/clients/{JevClient,IcarusClient}.py`，将 QQ 业务编排与 Jev 判断归入 `src/service/QQService.py`。下文旧文件地图不代表当前架构；以同目录 `arch.md` 和代码、测试为准。

> **供执行者：**按任务逐项执行；开始实施前调用 `superpowers:executing-plans`（原生执行）或 `superpowers:subagent-driven-development`。每一步先写可失败的功能测试，看到预期失败，再写最小实现。本文是实施计划，不表示已完成。

**目标：**在现有 Argus 骨架上打通 SnowLuma QQ HTTP 上报 → 类型化观察 → 持久收件箱 → 内存队列/worker → Jev wake/silence → Icarus Gateway session 提交的可恢复最小闭环。

**架构：**业务 `QQObservation(Observation)` 与私有 SQLAlchemy 映射分离；一张通用 observations 表保存事实、处理状态及带版本的特化 JSON，一张 session_links 表保存 QQ subject 与 Icarus session 的关联。正常消息是对象入 queue、worker 从 queue 取对象；DAO 落库后才向 SnowLuma 确认，DAO 扫描仅用于启动和运行期补入。Jev 策略定义在 strategy，service 组装上下文和执行 Gateway 操作；提交结果未知时停住不重发。

**技术栈：**Python ≥3.11、FastAPI、SQLAlchemy[asyncio] 2.x、aiosqlite、Pydantic、websockets、TypeSafe SDK / OpenRouter Jev、pytest；无 Docker、QQ 或付费模型依赖的自动测试。

**设计：**`apps/argus/docs/spec/2026-09-29-qq-observation-flow/design.md`。执行前先读该文件、`AGENTS.md` 及现有 `apps/argus/src/`。

## 全局约束

- 当前 `feat/argus` 上的骨架代码仍未提交；不要重置/覆盖已有文件。保留预先存在的 `apps/agent/settings.json`、`.env.bak-3010`、`apps/agent/settings.json.bak-argus`。不创建分支、不提交、不推送，除非用户之后明确要求。
- 本期不修改 Agent/Gateway、`packages/`、SnowLuma 源码；不接 mem0/openkb，不直接发送 QQ，不建立策略 DSL/注册中心，不声称跨进程 exactly-once。
- QQ connector 默认禁用；启用时必须配置单个 bot ID、非空上报签名密钥、Gateway 地址和绝对 workspace。服务只绑定 loopback。根 `.env` 加 **空值模板**，密钥绝不写入 `settings.json`、日志或测试输出。
- 平台特定实现的**文件名与类名一致**，用平台名+职责名：`connector/QQConnector.py` 定义 `QQConnector`、`handler/QQHandler.py` 定义 `QQHandler`、`entity/QQObservation.py` 定义 `QQObservation`；对应测试镜像命名。通用模块仍采用现有 Python snake_case 命名。不创建 `qq.py` / `qq_reply.py` 等并行平台入口。
- `POST /onebot/events` 只在启用 QQ 时挂载；原始字节 HMAC-SHA1 签名验证与请求大小限制先于 JSON 解析；成功必须返回无 JSON 体 204，仅表示 DB 已提交。
- 入站保留一个有界内存 queue 和单 worker；worker 从 queue 获取对象，不以数据库轮询替代主路径；启动/运行期从 DB 补入待处理对象。DAO 负责读写而不判断是否唤醒。
- `Observation` 业务对象不继承 ORM；通用事实为列，`content` 为有序 JSON，平台差异为 `subtype/detail_version/detail`；SQLAlchemy 独立 `$ICARUS_DATA_DIR/argus.db`，不读 `icarus.db`。
- Jev 调用首次失败后重试两次，仍失败记录原因并兜底 silence；Gateway `session.submit` 结果未知不自动重发；现有 update/history 无 submission_id，不能用文本相似性确认。
- 自动测试必须使用合成 OneBot 事件、临时 DB、假 Jev 与假 Gateway；不连接真实 QQ、不发送消息、不付费调用 Jev。真实冒烟另获确认。

## 文件地图及接口锁定

- `apps/argus/src/entity/observation.py` 与 `apps/argus/src/entity/QQObservation.py`：前者定义 `SourceRef`、`SubjectRef`、`ActorRef`、`ContentPart`、不可变 `Observation`；后者定义 QQ 特化的 `QQObservation`；`SubjectRef.key` 稳定编码 `qq:group:<id>` / `qq:private:<id>`。
- `apps/argus/src/handler/QQHandler.py`：`QQHandler(bot_id: str).parse(raw: dict[str, object]) -> QQObservation` 校验并转换入站消息；`QQHandler.reply_hint(subject: str) -> str` 提供 QQ 回复目标提示。两者均不依赖 HTTP、DB、queue、Jev；通用 service 经注入的 callable 使用提示。
- `apps/argus/src/dao/{entities,observation_store}.py`：私有 SQLAlchemy row 与 `ObservationStore(data_dir)`，`start/close`、`insert(obs) -> bool`（true=新写入）、`get(id)`、`recent(platform, account_id, subject, limit)`、`claim(id)`、`transition(...)`、`recover()`、`current_link(...)`、`save_link(...)`。使用单次事务进行状态更新和来源去重。
- `apps/argus/src/dao/QQObservationCodec.py`：QQ 专有的编解码器 `QQObservationCodec`，提供 `to_row_fields(obs) -> dict[str, object]` / `from_row(row) -> QQObservation`；不使用 pickle 或动态 import 未知类。只有第二个平台真正接入时才引入通用注册表。
- `apps/argus/src/connector/QQConnector.py`：`QQConnector` 接收 `config, secret, store, queue` 并暴露 `router: APIRouter`；验请求字节与 headers，交 `QQHandler`，落库入队，响应 204；不执行业务策略。
- `apps/argus/src/strategy/QQStrategy.py`：`QQStrategy` 提供固定策略版本和 Jev `Choice` 的 wake/silence 标准；不放 Gateway 操作。
- `apps/argus/src/service/{judge,gateway,observation}.py`：可替换的异步 Jev/Gateway 客户端、state 组装和观察处理；QQ 回复提示由 `QQHandler.reply_hint(subject)` 提供，通用 service 通过传入的 callable 使用，不 import QQ 类型进行条件分叉。
- `apps/argus/src/{queue,worker,main}.py`：保留现有类；为 queue 增加容量/满队列行为，为 worker 注入处理器并维持 `task_done()` 语义，由 main 持有周期补入任务；健康检查覆盖 DB、worker 与补入循环的活性。
- `apps/argus/src/config/{config_model,config_loader}.py`、`settings.json`、`requirements*.txt`、根 `.example.env`：明确非机密配置及带验证的密钥加载，使用 TypeSafe SDK 的现成调用形状（对照 `D:\Projects\argus\scripts\try_jev.py`）；先核实 Python 包名和版本再写依赖，不猜 SDK API。
- `apps/argus/test/{entity,handler,dao,connector,service,strategy,application}/`：镜像相应实现；根 `apps/argus/test/test_main.py` 保持旧默认禁用 QQ 的健康测试。
- `apps/argus/README.md`、根 README 中 Argus 能力说明、`apps/argus/docs/spec/2026-09-29-qq-observation-flow/arch.md`：只在实现与测试完成后按实际行为更新。

## 重点审查的失效输入

1. **相同来源键、不同消息内容：**不是幂等重投，应拒绝并留下无正文日志；任务 2 和 3 测试。
2. **入站 DB 已提交但 queue 满或进程退出：**204 后仍能由运行期/启动恢复补入，不能丢或重复执行；任务 3、4 测试。
3. **Jev 正常 silence 与故障兜底 silence：**均不提交 Icarus，但结果原因必须可区分；任务 5 测试。
4. **Gateway 接受提交而响应丢失：**恢复后标为 submission_unknown 且无第二次 submit；任务 6、7 测试。
5. **已有 session 是 unloaded 或 belongs-to-different-workspace：**前者可继续，后者不得提交；任务 6 测试。

---

### Task 1: QQ 业务实体与 OneBot handler

**文件：**创建 `apps/argus/src/entity/{observation.py,QQObservation.py}`、`apps/argus/src/handler/QQHandler.py` 和 `apps/argus/test/{entity/test_QQObservation.py,handler/test_QQHandler.py}`。

**接口：**`Observation(id, source: SourceRef, subject: SubjectRef, actor: ActorRef, content: tuple[ContentPart,...], received_at)` 为 frozen keyword-only dataclass；`QQObservation(Observation, message_type, group_id, is_self, mentions_bot)`。内容片段 `ContentPart(kind: Literal['text','image'], value: str)`。`QQHandler(bot_id).parse(raw)` 为纯函数式转换入口；非法消息抛 `ValueError`，无 IO。

- [ ] **Step 1 RED：**写一个群消息测试：`self_id=10000,message_id=301,message_type=group,group_id=42,user_id=7,message=[at(10000),text('你好'),image('a.png')]`；断言 subject `qq:group:42`、来源 ID `301`、有序 text/image、`mentions_bot=True`、`is_self=False`。再写自发私聊（`message_sent,user_id=10000,target_id=7` → `qq:private:7`）及临时群私聊（仍 `private`）。
- [ ] **Step 2 RED：**运行 `apps/argus/.venv/bin/python -m pytest apps/argus/test/handler/test_QQHandler.py -q`；预期因 handler/entity 尚不存在而失败。不要把 demo 的 `QQNormalizer` 源码复制进来。
- [ ] **Step 3 GREEN：**实现 QQ 身份正整数或正整数字符串校验、OneBot `message_id` 非零有符号整数并存为稳定字符串（SnowLuma 32 位哈希可能为负）；区分 `post_type=message/message_sent`，校验 `self_id`，构造 subject 和 UTC 时间；数组仅识别 text/image/at，未知段明确拒绝而不当文本，`@all` 与纯文本“@机器人”不算提及；纯文本中的 CQ 码拒绝。图片段只保存远程引用，不下载。所有 dataclass 使用 `frozen=True` 并把片段顺序保留为 tuple。
- [ ] **Step 4 GREEN：**运行两个新测试目录；追加参数化错误测试：缺 message_id、错 self_id、群缺 group_id、非聊天事件、非法时间、CQ 字符串、错误段 data、未知段；看到预期异常而不是任何静默转换。运行 `./apps/argus/scripts/test.sh`。

### Task 2: 独立数据库与跨平台观察映射

**文件：**创建 `apps/argus/src/dao/{entities.py,QQObservationCodec.py,observation_store.py}`、`apps/argus/test/dao/{test_QQObservationCodec.py,test_observation_store.py}`；修改 `apps/argus/requirements.txt` 加 `SQLAlchemy[asyncio]>=2,<3` 与 `aiosqlite>=0.20,<1`。不动 Agent 数据库。

**接口：**`ObservationStore(data_dir: Path)` 在该目录建立 `argus.db`；`async start()/close()`；`async insert(obs: Observation) -> bool`（首次 true，完全相同来源事件重复 false）；`async get(id: str) -> Observation`；`async recent(platform, account_id, subject, limit=20) -> tuple[Observation,...]`。`QQObservationCodec` 保存普通列和 subtype/version/detail JSON；QQ 行必须可还原 QQ 子类。

- [ ] **Step 1 RED：**临时目录中写入 QQ 群消息（含文本和图片引用），关闭重开 store 后读回 `QQObservation`，断言父类字段和 QQ 字段均相等；另一条相同来源键且相同事实 `insert` 返回 false，查库只有一行。运行 `apps/argus/.venv/bin/python -m pytest apps/argus/test/dao -q` 确认失败。
- [ ] **Step 2 GREEN：**私有 `_ObservationRow` 包含事实列、`content` JSON、`subtype='qq_message'`、`detail_version=1`、`detail` JSON 和处理字段；唯一约束 `(platform,account_id,source_message_id)`，复合 `(platform,account_id,subject,occurred_at)` 与状态索引。`_SessionLinkRow` 保存关联历史且同一 subject 仅一个当前候选（SQLite 部分唯一索引或事务内结束旧关联）。使用 SQLAlchemy async sqlite engine、`async_sessionmaker`、PRAGMA `foreign_keys=ON/busy_timeout=5000/WAL`，DB 权限 0600，数据目录 0700，`create_all` 不覆盖已有数据库。ORM 类仅供 DAO 使用。
- [ ] **Step 3 GREEN：**显式 encode/decode datetime、`ContentPart`、`QQObservation`；未知 `subtype/detail_version` 抛专用 `UnsupportedObservationError` 并保留数据库原行。对来自 SQLite 的无时区 datetime 恢复 UTC。重新运行新 DAO 测试。
- [ ] **Step 4 RED→GREEN：**写同来源键但内容不同的冲突测试；`insert` 必须抛 `ObservationConflictError`，不修改旧记录（不能只靠 ON CONFLICT DO NOTHING）。写 `recent` 只返回同平台/账号/subject，按时间稳定排序的测试；跑 Argus 全套。

### Task 3: 待处理状态、原子领取与 DB→queue 补入

**文件：**扩展 `apps/argus/src/dao/{entities,observation_store}.py`，修改 `apps/argus/src/{queue,worker}.py`，新增 `apps/argus/test/{dao/test_recovery.py,test_worker.py,test_queue.py}`。只保留一个 worker；这里不接 Jev。

**接口：**`store.claim(id) -> bool` 只将 pending 改 processing；`store.mark_silence(id, reason, strategy_version)` 终结；`store.recover() -> tuple[Observation,...]` 按小页列出需恢复的记录（实际以分页/游标遍历）；`InboundQueue.put_nowait(obs)` / `get() -> Observation`。`QueueWorker(queue, process: Callable[[Observation], Awaitable[None]])` 在处理后 `task_done`；`recover_pending(store,queue)` 扫描补入，队列满则留 DB pending，下一轮继续，不把 DB 用作 worker 的消费接口。

- [ ] **Step 1 RED：**队列只传入 `QQObservation`：新增真实 `QueueWorker` 处理器收集 observation ID 的测试，验证消费顺序、`queue.task_done`/`join()`；同时添加 `claim` 连续两次只有一次成功的 DAO 测试。运行 `test_worker.py` 与 `dao/test_recovery.py`，观察失败。
- [ ] **Step 2 GREEN：**将骨架 worker 的“读取即丢弃”替换为注入处理器；处理器异常记录且由 DAO 标记可恢复的失败，不吞异常后假报健康。`claim` 使用条件 UPDATE + rowcount 的事务级原子转换；已领取的重复队列项跳过。现有 `start()/stop()/is_running` 合约保持有效，新增处理器可以按构造参数注入，测试不依赖 HTTP。
- [ ] **Step 3 RED→GREEN：**测试容量为 1 时，DB 中两个 pending，补入只使一个进入 queue；worker 消费后下一轮补入第二个；并模拟关闭 store、重开后补入尚未处理的 observation，结果仍是同一个 QQ 子类。补入不能把同一 pending 持续填满 queue：以本进程的 scheduled-id 集合去重，worker 完成后移除；进程重启清空该集合，由 DAO 状态判断可恢复性。
- [ ] **Step 4 RED→GREEN：**测试 processing 在进程重启时按阶段重置为 pending；若已持久化提交意图则不得重置（留任务 7 的 submission_unknown 处理）。用注入的短 tick 或显式 `recover_pending` 验证运行期补入，不靠 sleep。运行 Argus 全套。DAO 事务失败必须传播，不能把恢复失败解释为无 pending。

### Task 4: 可选、安全的 SnowLuma HTTP connector

**文件：**创建 `apps/argus/src/connector/QQConnector.py`、`apps/argus/test/connector/test_QQConnector.py`；修改 `apps/argus/src/config/{config_model,config_loader}.py`、`apps/argus/settings.json`、`apps/argus/src/main.py`、根 `.example.env`。沿用任务 1 的 handler、任务 2/3 的 store 与 queue。

**接口：**`QQConfig(enabled: bool=False, bot_id: str|None=None, max_body_bytes: int=1048576, workspace_path: str|None=None, gateway_url: str|None=None)` 为非机密设置；签名密钥 `ARGUS_QQ_REPORT_SECRET` 只从根 `.env` 加载并在启用时校验非空，API 不通过 HTTP 暴露配置。`QQConnector(config, secret, store, queue).router: APIRouter` 注册 `/onebot/events`，成功为 `Response(status_code=204)`。未启用时根本不 include router。

- [ ] **Step 1 RED：**使用 `TestClient` + 临时 DB + 固定字节原文，通过 `hmac.new(secret, body, hashlib.sha1).hexdigest()` 计算 `X-Signature`，带 `X-Self-ID`，POST 有效群消息，断言 204、响应空体、DB 恰好一条 pending、queue 有 QQObservation；第二次同样 POST 仍 204 但记录/队列不增加。未启用时 404。运行 connector 测试预期失败。
- [ ] **Step 2 GREEN：**实现每次最多读 `max_body_bytes+1`，严格拒绝超限；原始 bytes 做 HMAC 常量时间验证，`X-Signature` 必须 `sha1=<40 hex>`；验证 header/事件账号，JSON 解析、handler、store.insert，然后 `put_nowait`；队列满不改已经提交的 pending，交给任务 3 的运行期补入；对相同来源 ID 但不同事实的冲突报非 2xx。启动顺序必须先 DB/worker/补入，后接受 HTTP。保持旧 `GET /health` 精确 `{'status':'ready'}`，但 DB/worker/补入任务退出时返回 503。
- [ ] **Step 3 RED→GREEN：**参数化缺签名、签名不符、非 hex、错 `X-Self-ID`、JSON 错、缺来源 ID、非聊天事件、超限输入；断言均没有新 DB 行也没有入 queue。对配置启用但缺密钥/无效 bot ID/无绝对 workspace/错误 Gateway URL，启动前报清晰错误；默认禁用仍能 `icarus start argus` 且无需 Jev 凭据。测试 `204` 不带 `application/json` 以防 SnowLuma 快捷操作。运行全 Argus 套件和控制命令测试。

### Task 5: 简单 Jev 策略、有限上下文与可测判断器

**文件：**创建 `apps/argus/src/strategy/QQStrategy.py`、`apps/argus/src/service/{judge,observation}.py`、`apps/argus/test/{strategy/test_QQStrategy.py,service/test_judge.py,service/test_observation.py}`；按验证过的包名在 `apps/argus/requirements.txt` 加 TypeSafe SDK。Jev 失败与有效 silence 要有不同 `reason`。

**接口：**`judge(obs: Observation, recent: tuple[Observation,...], current_session: bool) -> Decision(outcome: Literal['wake','silence'], reason: str|None, strategy_version: str)`；适配器 `JevJudge` 注入可替换异步调用函数；只在单个 strategy 文件定义 `Choice` 和版本。`ObservationService.process(obs: Observation)` 负责 claim、同 subject/账号有限 `recent()`、调用 judge、DAO 记录结果；当 outcome 为 wake 调用下一任务的 `SessionDispatcher`。调用方提供 platform-specific `reply_hint(subject)`，通用 service 不 import QQ 实体做 `isinstance`。

- [ ] **Step 1 RED：**构造已持久化的 QQObservation 及同 subject 两条近期记录，假 Jev 调用捕获 state，断言含当前消息、来源上下文、`current_session`，但不含其他群/账号的消息；文本/数组有长度和数量上限。假 Jev 返回合法 wake/silence，断言 DAO 结果准确。运行两个 service 测试文件观察失败。
- [ ] **Step 2 GREEN：**策略模块定义一次 `should_wake: Choice(instructions=...,criteria={'wake':...,'silence':...})` 与策略版本；参考 `/mnt/d/Projects/argus/scripts/try_jev.py` 的 `TypeSafeClient.system_one(model='typesafe/jev-1.13',state=...,questions=...)` 形状，实装时先在已安装 SDK 上验证异步调用支持；若 SDK 仅同步，使用 `asyncio.to_thread` 并对整体调用设置有限超时，避免阻塞主 event loop。客户端每次从独立配置读取 OpenRouter endpoint/model/key，不运行真实调用。输出只允许明确 `answers['should_wake'].choice` 为 wake/silence，异常/缺失视为失败，不从正文猜测。
- [ ] **Step 3 RED→GREEN：**假调用连续失败、超时、返回未知 choice，断言最多 3 次（第一次+两次重试）、次数和退避可测试（注入 sleep）、最终 DAO 裁决 `silence` 且 `reason='judge_fallback'`；有效 silence 原因不是该值、不提交 Gateway。`is_self` 必须在调用 Jev 前确定性 silence 以防回环，写专门测试。处理过期消息（注入时钟、有限年龄）留明确 `expired` 状态，不突然唤醒。运行 Argus 套件。

### Task 6: Icarus Gateway 会话核实与一次安全提交

**文件：**创建 `apps/argus/src/service/gateway.py`、`apps/argus/test/service/{test_gateway.py,test_dispatch.py}`；扩展 `apps/argus/src/handler/QQHandler.py` 中的 QQ 回复提示、`apps/argus/src/dao/{entities,observation_store}.py`、`apps/argus/test/dao/test_session_links.py`。只使用 Gateway WebSocket JSON-RPC 2.0 `/rpc`，不 import Agent 内部。

**接口：**`GatewayClient.connect()/close()/call(method,params)` 加 `session_get/create/submit/get_history/task_get_status/subscribe` 的窄方法；`SessionDispatcher.dispatch(obs: Observation, prompt: str, reply_hint: str) -> str|None` 保存并校验 session，提交成功返 task_id。`store.current_link(platform,account_id,subject)`、`store.save_link(...)`、`store.reserve_submission(observation_id,workspace_path,workspace_key,session_id,submission_id)`、`store.mark_submitted(id,task_id)`、`store.mark_submission_unknown(id,error_type)`；提交预留状态在网络调用前提交事务。Gateway 返回 workspace_key 必须来自服务端，不能自己计算。同步调用不用于业务流程。

- [ ] **Step 1 RED：**用小型本地假 Gateway（WebSocket 或隔离的 call 传输替身）验证：已有 link 经 `session.get` 确认同 workspace_key 后提交；`lifecycle='unloaded'` 仍复用；不存在则用提前持久化的目标 ID 调 `session.create`，保存返回的真实 workspace_key，再提交；当 Gateway 返回其他 workspace_key 时拒绝提交。运行 `test_gateway.py` / `test_dispatch.py` 观察失败。
- [ ] **Step 2 GREEN：**以现有 `apps/tui/src/gateway_client/client.py` 为 JSON-RPC 帧/错误/订阅参考，但 Argus 客户端留在 `apps/argus`；按单 socket 并发请求 ID 分流响应与 runtime.update，连接中断让在途调用获明确的 transport-unknown，最大帧限制、有限 RPC 超时与关闭释放。不要把 `session.list` 摘要解释为 QQ→session 路由。subject 级锁串行化同群的 link 和提交；锁放 service 而非 DAO。
- [ ] **Step 3 RED→GREEN：**提交前写稳定 submission ID 与目标；成功响应 task_id 后更新 observation；断线/超时标记 submission_unknown 并禁止自动重发；明确的 `session_not_found` / `runtime_stopping` 等业务拒绝分别记录可诊断类型，不能统称 unknown。创建 session 响应丢失时用提前保存的 ID 走 `session.get` 检查，不新建随机 ID。QQ 回复提示由 `QQHandler.reply_hint(subject)` 以 subject 构造 group/private 工具目标，通过依赖注入 append 到每次 prompt；Argus 不直接发送 QQ。补充测试相同 subject 并发输入不建两条当前 link。
- [ ] **Step 4：**运行 `apps/argus/.venv/bin/python -m pytest apps/argus/test/service apps/argus/test/dao -q`，再运行 Argus 全套；只用假 Gateway，绝不在测试中启动 Icarus/发送 QQ。

### Task 7: 完整运行接线、恢复安全与状态可见性

**文件：**修改 `apps/argus/src/{main,queue,worker}.py`、`apps/argus/src/config/{config_model,config_loader}.py`、`apps/argus/settings.json`、`apps/argus/test/{test_main.py,application/test_qq_flow.py}`；按已有身份匿名展示有限状态，不输出消息正文。

**接口：**`create_app(config=..., store=..., judge=..., gateway=...) -> FastAPI` 可测试依赖注入；lifespan 安装 connector、打开 DB/客户端、启动 worker 与周期补入，退出先拒绝新入站再停止 worker/扫描并关闭资源。`GET /health` 在 DB/worker/recovery 已就绪时才返回 `{'status':'ready'}`；本地只读 `GET /observations/{id}/status` 返回 observation ID、处理状态、session ID、submission ID、task ID、错误类型（不含原始聊天内容或密钥）。

- [ ] **Step 1 RED：**端到端功能测试：签名合成 QQ 事件返回 204，入队实际 `QQObservation` 被 worker 消费，fake Jev wake 后 fake Gateway 收到一次 `session.submit`，DB 再读能得到同一来源、裁决、link 和 task ID；另一条正常 silence 不调用 Gateway；重投同 ID 不重复处理。运行应用集成测试观察失败。
- [ ] **Step 2 GREEN：**接线 `main` 的 store/queue/worker/recover/connector/service/gateway，启用 QQ 配置的情况下启动先完成连接和 DB 初始化再开始接收请求；默认禁用 QQ 仍保留原空骨架健康能力，不要求 Gateway/模型凭据。`queue.join()` 的测试同步点替代固定 sleep；健康检查发现 worker、补入任务或 DB 不可用时返回 503。
- [ ] **Step 3 RED→GREEN：**模拟在 DAO insert 后 queue 满、随后 worker 腾出容量，补入能在同一进程再次入队；模拟 kill/restart，DB 中 pending 与安全的 processing 能重新进入 queue；持久化 `submission_intent/submitting` 且没有 task_id 的行启动时成为 submission_unknown，fake Gateway 绝不收到第二次 submit。状态端点可查但不回显文本；unknown 保留人工作业线索与 attempt/unknown 计数日志。关闭时取消/等待任务，正确 `task_done`，不删除未完成记录。
- [ ] **Step 4：**Argus 完整测试、根控制测试、受影响应用测试、compileall 与 diff 检查；在本机只跑无真实 QQ/模型请求的启动健康冒烟。若既有 TUI 13 快照失败，比较具体失败集合并单独报告，不修改 TUI。

### Task 8: 配置/文档与可选真实冒烟说明

**文件：**修改 `apps/argus/README.md`、根 `README.md` 的 Argus 能力描述、根 `.example.env` 中的空凭据说明；实现和测试全部落地后从代码和测试撰写 `apps/argus/docs/spec/2026-09-29-qq-observation-flow/arch.md`。不写尚未实现的能力为已上线。

- [ ] **Step 1：**文档写出 SnowLuma `httpClients` 的本机上报地址、`messageFormat=array`、需要匹配的 HMAC secret、默认禁用启用方式、为什么 204 表示已落库但不保证回复、Jev/ Gateway 配置与故障兜底、`submission_unknown` 如何查询和人工确认；密钥例子必须是假值，不能复制本地 `.env` 或 demo 的真实密钥。
- [ ] **Step 2：**运行 `./apps/argus/scripts/test.sh`、`python3 scripts/tests/test_icarus_control.py`、`make test-agent`、`make test-gateway`、`make test-tui`、`python3 -m compileall -q apps/argus/src apps/argus/test`、`git diff --check`；逐项记录实际结果。实体/DAO/HTTP/worker/策略/会话各最小测试必须已 RED→GREEN。
- [ ] **Step 3：**真实 SnowLuma/付费 Jev 冒烟仅在用户确认目标账号/群、数据来源和费用后单独进行；若未批准，报告“未执行真实冒烟”，不得通过合成测试暗示真实 QQ 链路已验证。`git status --short` 标明既存未改动的三个文件；不要执行 git add、commit、push。

## 执行时要保留的证据

每项任务完成前，记录该项最小测试的预期失败与通过结果。重点检查：DB 已收存但 queue 补入失败时仍能恢复；未知 submission 不重发；SnowLuma 无签名、错账号和坏消息不能落库。真实 OpenRouter、SnowLuma 与 Icarus Gateway 的外部行为只能由后来明确授权的冒烟验证，不能由 fake 的通过推断。
