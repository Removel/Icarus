# QQ 观测闭环现状

> 本文依据 `apps/argus/src` 与对应测试描述当前实现；实施前目标见 `design.md`，未完成/未验证的目标以此处为准。

## 启动与连接

`main.create_app()` 从 `apps/argus/settings.json` 读取配置；`qq.enabled=false` 默认无 QQ 路由。启用时读取仓库根 `.env` 中的 `ARGUS_QQ_REPORT_SECRET`、`ARGUS_JEV_API_KEY`，创建独立 `ObservationStore` (`$ICARUS_DATA_DIR/argus.db`)、`InboundQueue`、`QueueWorker`、`QQService` 和 `clients/` 内的 Icarus/Jev 客户端。lifespan 初始化 DB 并重置安全中断状态，连接 Gateway，启动 worker 与周期补入任务；`GET /health` 检查 worker 与补入任务是否仍运行。

`connector/QQConnector.py` 验证 HTTP 上报体的 HMAC-SHA1、`X-Self-ID`，限制体积，再交 `handler/QQHandler.py` 转成不可变 `QQObservation(Observation)`。handler 支持群/私聊文本、图片引用、直接 at 与自发消息；不能解释的消息段（如 `face`、`reply`）保存为显式的 `other` 片段并保留段名，不冒充文本，也不整条拒绝；仅对结构本身错误的载荷（缺段类型、文本/图片字段类型不对）与 CQ 字符串报错。SnowLuma `message_id` 是可能为负的非零有符号整数。时间须来自事件，以便来源重投具有一致事实。DAO 唯一约束来源平台、账号和消息 ID；入库成功才返回无体的 204。重复相同事实不再入队，冲突事实返回 409。SnowLuma 不保证非 2xx 自动重试。

## 观察数据与恢复

SQLAlchemy ORM 行仅存于 `dao/entities.py`，业务对象不继承 ORM。`dao/QQObservationCodec.py` 将通用字段存为列、内容片段存 JSON、QQ 字段存 `subtype/detail_version/detail`。`ObservationStore` 持久化 observations 和 session_links；SQLite 设置 WAL、外键和 busy timeout。

worker 正常从内存队列取对象交 `QQService`。`dao.claim()` 是原子 pending→processing，阻止重复队列项再次处理。`recover_pending()` 扫描 DB pending 补入队列，按进程内 scheduled ID 避免重复补入；重启时 `reset_interrupted()` 将未提交的 processing 还原为 pending，已留提交意图的处理中记录改为 submission_unknown。启用 QQ 时启动与定时 tick 都补入；禁用时保留待处理记录，不将其投给空 worker。

## 判断与会话操作

`strategy/QQStrategy.py` 固定了最小 wake/silence Jev Choice（当前版本 `qq-wake-v3`），标准区分“私聊中对方单独发来的内容”和“群聊中提及/叫名字/延续对话”。`service/QQService.py` 负责组装 state（当前消息、按接收时间排序的最近同 subject 消息、sender、是否通过 `at` 提及、`chat_type`、`is_direct`、`bot_names`、`name_mentioned`），再由 `clients/JevClient.py` 的 `JevClient.decide()` 按调用方给出的问题名、可选答案与兜底答案询问 Jev：默认最多 3 次、单次超时 2s，全部失败返回兜底答案并标记 `judge_fallback`。`JevClient` 同时持有 TypeSafe SDK（`RetryPolicy(max_retries=0)`，禁用 SDK 内部重试）与判断编排；`answer=` 注入点让判断逻辑可在测试中替换。`name_mentioned` 由 `QQService.name_mentioned()` 对纯文本做 NFKC + casefold 归一化后匹配 `qq.bot_names`（只搜索文本片段，不看图片引用），是判断信号而非硬规则。`QQService` 对超过年龄窗口的消息或自发消息直接 silence，避免旧消息唤醒和自回复回环；策略版本由 service 侧记录。`name_mentioned` 由 `QQService.name_mentioned()` 对纯文本做 NFKC + casefold 归一化后匹配 `qq.bot_names`（只搜索文本片段，不看图片引用），是判断信号而非硬规则。`QQService` 对超过年龄窗口的消息或自发消息直接 silence，避免旧消息唤醒和自回复回环；策略版本由 service 侧记录。

`QQService` 查 `session_links` 并通过 Gateway `session.get` 核实 workspace_key 与当前配置的 workspace_path；缺关联时先存目标 session ID，再 `session.create`，最后预存提交意图并 `session.submit`。明确 task_id 记为 submitted；RPC 传输结果未知或服务器内部错误记为 submission_unknown，不自动重发；明确的提交前业务拒绝记为 failed。`GET /observations/{id}/status` 只给身份和状态，不给聊天正文。Argus 不发送 QQ，prompt 中的回复目标提示由 `QQHandler.reply_hint()` 生成。

## 验证边界

`apps/argus/test/` 使用合成 OneBot 消息和假 Jev/Gateway 覆盖字段解析、数据库双向映射、签名/去重、队列、策略退化与端到端状态。尚未在真实 SnowLuma、真实付费 Jev 或真实 Gateway 的 QQ 任务上做冒烟。当前 Gateway 历史不带 submission_id，不能可靠自动核对丢失的 submit 响应；submission_unknown 仍须人工确认。mem0/openkb SDK 与跨平台扩展不在本期实现。
