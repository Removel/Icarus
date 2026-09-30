# 批次与决策图现状

> 本文依据本目录设计对应的源码和自动测试描述实现；未迁移实际用户数据库，未重启服务。旧单消息路径的历史说明见前一期文档。

## 代码与职责

所有 Argus Python 模块采用 snake_case，类仍为 QQService、QQConnector、JevClient 等 PascalCase。判断节点基类在 `strategy/base_policy.py`；QQ 节点位于 `strategy/qq/`。service 内 `_build_graph()` 显式组装节点、条件边和最终路由，main 不编排业务策略。

`decision_graph/graph.py` 校验节点、依赖、可达性和普通环；`runner.py` 顺序执行，不并行。`requires` 是不可省略的数据依赖；all/any/selected 描述控制路径汇合，selected 不能过滤掉 requires。中间 yes 不短路；最终只有 RouteDecision(silence/submit/steer)。失败依赖标 blocked，条件未选中标 skipped；超时/错误打 warn，返回失败 silence；停机取消继续传播。RepeatBlock 使用独立轮次输出空间和共享步数预算。路径记录包含 run_id、节点/版本/轮次、状态、耗时、错误类型与安全的判断结果，不记录原始输入正文。

QQ 图当前节点顺序为 human → facts → session → code → context → model → intervene → silent/relation → action → end。`session`（`TaskStatusReader`）在**介入判断之前**读取会话状态，`context`（`ContextReader`）把持续会话事实（`current_session`、`session_lifecycle`、`active_task_count`）与最近 10 条前序消息一起并入 state，因此"延续已经开始的对话"对 Jev 是**可见事实**而非猜测；同一份会话快照随后被关系判断复用，不重复查询。转录会给机器人自己的消息加 `[机器人]` 前缀，使上下文能区分谁说过什么。

## 收件与成批

QQConnector 验签、QQHandler 转换并落库后返回 204。Observation 仍进入原队列，worker 调 BatchScheduler.offer，模型判断不阻塞收件。scheduler 按平台/账号/subject 的未归属接收序前缀成批；QQBatchPolicy 决定立即/静默/数量/最长时间关闭。默认群静默 5 秒、20 条、最长 60 秒，私聊人类内容即时；at/称呼触发及时判断但不意味着图内一定提交。自消息不延长人类静默期限。旧来源时间不被 300 秒规则丢弃。

另一个有界 ready-batch 队列驱动批次执行。数据库固定成员与 batch_id；关闭事务失败不产生部分归属。恢复只选同 scope 最早可执行批次，前一批 ready/judging/decided/submitting/unknown 不允许后批越过；不同 scope 不受此约束。unknown 需人工核实，可能阻塞该 subject 后续处理。补入使用容量上限查询，满队列不扫描全部 ready IDs。

## 判断与发送

纯自消息代码 silence；私聊人类文本/图片或明确 at 且有文本/图片代码 yes；其余让 Jev Choice 判断（判定标准 `qq-jev-intervention@2`：`current_session` 为真且上下文显示在等机器人接话时倾向介入，但不因出现名字或存在历史会话就介入）。JevClient.ask 返回原生 SystemOneResponse，支持 Noul/Choice/Score，不再保留旧 decide 单选字符串兼容接口。最多三次请求，SDK retry=0；失败抛安全 JevCallError，由图记录故障。

TaskStatusReader 查询现有 session 状态；有活动任务时 QQTaskRelationPolicy 读取与 task_id 对应的有界历史并问 supplement/new_topic，无活动任务直接 submit。多活动目标或材料不足时失败收工，不猜测 task。GraphRunner 输出后，QQService 保存决定并重验目标；steer 明确 not_running/already_finished 才可改为 submit。未知回执/断线/内部错误记 unknown；不能自动转为另一次写请求。提交意图同时保存完整 workspace/session 身份、method、params、operation_id。原操作 ID 为批次 ID 加动作/目标后缀。

## 数据与维护

schema v2 有 observations（不可变事实、ingest_seq、batch_id）、decision_batches（状态、路由、trace、发送意图与回执）、session_links。旧 observation 处理接口与列已从新版运行路径移除，不双写旧状态。一次性 schema 维护工具会先通过 SQLite backup API 备份，再迁移旧记录为单条历史批次，保留既有提交 ID、task ID、策略版本、裁决原因与错误；迁移成功后删除旧处理列。历史信息保存到 batch route/trace/error 等位置，不因去兼容而删除。

旧库正常启动会抛 SchemaUpgradeRequired。维护入口：`apps/argus/scripts/migrate.sh`，要求停止所有 Argus 进程；不依赖 create_all 升级旧列。测试仅在临时旧库验证升级幂等与结果保留；真实数据升级需用户明确安排。无节点级 checkpoint，重启复用固定批次；judging 回 ready，submitting 回 unknown，不重放已交付批次。

## 验证与限制

自动测试覆盖命名导入、图分支/依赖/重复预算、typed Jev、多消息成批、单条私聊、历史状态迁移、批次保序、队列满、空回执和 steer 竞态。真实 SnowLuma/付费模型验证属于上一期单消息流程，本次批次图未做真实外部冒烟。

当前无跨节点并行、节点中断恢复、自动话题拆批、记忆/知识客户端。缺失 task 状态会核对 session 快照；可确认空闲时转 submit，仍无法确认时明确 failed 而不无限自动重试。迁移前需升级支持 SQLite DROP COLUMN 的 SQLite（3.35+）；维护工具只用于停服期间，不支持在线多进程迁移。
