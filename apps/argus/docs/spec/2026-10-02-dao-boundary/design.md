# DAO 边界整理设计

> 状态：待审阅设计，尚未实施；不是当前源码架构。实施计划在设计确认后编写。本文覆盖 B：表记录类型、映射与持久化边界。A 见 [图基座设计](../2026-10-02-decision-graph-substrate/design.md)，C 见 [职责归位设计](../2026-10-02-layer-organization/design.md)。

## 1. 目的与范围

目标是整理当前代码：类对象有明确归属，DAO 只执行持久化操作，业务裁决和证据组装不散落在存储方法里。

约定：

- `entity` 定义业务对象和基于表的记录对象；两类对象分文件，不让业务对象继承 ORM。
- `dao` 接收表记录对象或明确的查询/更新参数，返回表记录对象或查询结果，执行增删改查、事务及并发条件检查。
- 业务对象自行提供 `to_row()` / `from_row()` 映射；映射不执行数据库操作。
- DAO 可以依赖 `entity` 中的表记录类型，但不得导入业务判断、证据对象或策略实现。
- `entity` 不依赖 DAO。不能通过把 ORM 类留在 DAO、让 entity 反向 import DAO 来实现本次整理。

不变项：三张表、列/索引/约束、schema v3、已有事实的存储表示、去重依据、接收顺序、批次成员、等待恢复及投递未知安全语义。不做迁移、不访问真实用户库。

## 2. 已核对的现状

- `dao/entities.py` 定义 `_Base`、`_ObservationRow`、`_SessionLinkRow`、`_DecisionBatchRow`。
- `ObservationStore.insert()` 接收业务 `Observation`，内部按 `QQObservation` 分支并调用 codec；重复事实比较也通过恢复业务对象完成。
- `dao/qq_observation_codec.py` 处理 QQ 业务对象与表记录映射。
- `SessionLink` 定义在 `dao/observation_store.py`。
- `conversation()` 在 DAO 内决定上下文窗口与截断，并构造 `ConversationEvidence`；`recent_activity()` 构造 `RecentActivity`。
- `save_decision()`、`defer()`、`mark_rejected()` 在 DAO 内决定业务结果/原因对应的存储状态。
- `BatchStore` 通过 `ObservationStore._require_sessions()`、`._codec` 取得数据库和映射能力，两个 store 未共享独立的数据库组件。

## 3. 目标文件归属

| 文件 | 内容 |
|---|---|
| `entity/records.py` | ORM Base、ObservationRow、SessionLinkRow、DecisionBatchRow；从现有 dao/entities.py 移入，表定义保持不变 |
| `entity/qq_observation.py` | QQObservation 及其 to_row/from_row 映射、存储格式校验 |
| `entity/session_link.py` | SessionLink 业务对象及映射 |
| `entity/decision.py` | 保留 ObservationBatch/业务决定；提供需要的批次记录与成员转换，不在这里查库 |
| `dao/database.py` | SQLite/engine/sessionmaker 生命周期、公开会话/事务入口、健康检查；供两个 store 共用 |
| `dao/observation_store.py` | observations 的写入与查询、session_links 的持久化操作 |
| `dao/batch_store.py` | decision_batches 的写入与查询、原子成员绑定及条件更新 |
| `dao/schema.py` | schema 版本守卫，保持拒绝旧试验库 |
| `utils/datetime_utils.py` | SQLite 读出时间的 UTC 归一化纯函数 |

不另建 repository、通用 mapper、CRUD 基类或 Unit of Work 框架。Database 是现有 engine/sessionmaker 能力的明确归属，不是新的一套持久化抽象。

## 4. 对象边界

### 4.1 表记录与业务对象

业务对象仍为不可变业务数据，不继承 ORM。表记录是 SQLAlchemy 映射对象，只负责描述表结构。业务对象与对应记录对象都在 entity，映射是同层依赖。

观察记录字段保持原样，包括 subtype/detail_version/detail、content、is_strong_signal、ingest_seq 与 batch_id。由数据库赋值的接收序号、绑定关系不靠业务映射重新生成。

DAO 查询必须显式取出需要的字段，不向上层暴露需要活跃 session 才能访问的 lazy relationship。JSON 内容在转换为业务对象时复制，避免后续修改意外改变 ORM 记录。

### 4.2 写入和去重

调用方先把 QQObservation 转为 ObservationRow，再交 DAO。DAO 按现有来源唯一约束执行插入；来源重复时比较记录中的原始事实字段，而不构造 QQObservation。

比较仍排除本地 id、received_at、接收顺序和后来处理状态；机器人配置变化不能改写已保存的首次强信号。事实相同为重复，事实冲突仍报冲突。这里是存储一致性校验，不新增业务回应规则。

### 4.3 查询和证据组装

DAO 保留有界 SELECT、排序、计数和同一事务内的组合读取，但不决定业务窗口规则、不返回 ConversationEvidence/RecentActivity。

- QQConversationReader 提供前序/后到数量及截断策略，DAO 执行对应范围查询。
- 水位、当前成员、前序与后到读取保持同一个本地事务快照；不能把原来一次事务拆成互相不一致的多次查询。
- RecentActivityReader 解释判断/交付记录：窗口、强信号重置、连续不回应与提交统计仍沿用现有规则。
- 状态查询由 service 组合 observation/batch 记录并分页安全 trace；DAO 不组装 HTTP 返回视图。
- SQL 的 where/order_by/limit 属于 DAO 实现；“什么记录构成业务证据”由调用方明确给出，不把每个查询都退化成读取整表后在 Python 中过滤。

## 5. 状态转换与事务

业务状态目标、route/reason、判断失败映射和恢复策略由 service 调用 strategy 得出；DAO 只执行调用方给定的条件更新。

必须保留：

- 固定前缀成员绑定全有或全无；DAO 在事务内验证预期记录仍未被绑定。
- 状态更新带预期状态条件，避免重复领取/重复发送。
- 发送前完整参数、operation_id 与尝试记录同事务落库。
- 明确拒绝的回执、尝试结果和等待状态同事务写入，不能在重启后把已知拒绝误归未知。
- accepted 回执和 delivered_at 同事务保存。
- judging/submitting 重启恢复由 service 指定转换，但仍由 DAO 原子执行。

DAO 中不保留“某业务 action 应转哪个 status”的自动推导。持久化状态名字可以作为字段值传入；开放状态集合由 service/strategy 提供给查询，不因为它存成字符串就把业务含义放回 DAO。

## 6. 两个 store 解耦

main 创建一个 Database，并分别注入 ObservationStore 与 BatchStore。两个 store 不能通过对方的私有 sessionmaker 或 codec 获取能力。

同一业务操作的跨表事务通过 Database 的公开事务上下文完成。需要同一事务的 store 方法可明确接收该事务 session；不泄漏 ORM session 到图执行器，不新增跨层事务管理器。

Database 对外提供启动、关闭、运行状态和可用性检查，替代 main 对 `store._sessions` 的访问。

## 7. 与 A/C 的衔接

B 不依赖新的图 API，可以独立验证。为让 B 完成后应用仍可运行，本阶段同步调整调用侧的对象转换，并把 DAO 内证据组装迁到现有 evidence reader；这些 reader 暂时仍可按旧 QQ 图节点调用。C 再将它们绑定到边，并完成接入链归位。

不保留旧路径转发壳，不同时维护两套 store 业务接口。所有源码与测试调用点一并迁移。

## 8. 验收

按最小受影响测试 → Argus 全量 → 编译/diff 检查执行；不运行真实 QQ/Gateway/Jev，不读取密钥。

- entity 映射往返覆盖原有 QQ content/detail/timestamps 和异常格式；ORM schema 与现有 v3 一致。
- DAO 测试只以表记录为主要输入输出，覆盖重复/冲突、排序、事务回滚和条件更新。
- evidence 测试证明窗口/截断/统计不变，固定事务水位不丢失。
- 状态/投递/恢复集成测试验证发送未知不自动重发、明确拒绝保持原子性。
- import 检查：entity 不依赖 dao；dao 仅依赖 entity.records 等表记录模块，不依赖业务实体、evidence、strategy、service。
- `make test-argus` 与 `git diff --check`；编译检查遵循应用测试脚本。
