# Argus

Argus 是 Icarus 的 QQ 观察与触发应用。签名上报通过 `connector → handler → service` 直接接收，service 持久化成功后返回 204，不经过入站消息队列。按会话成批后，由 service 内部的后台消费者运行决策图：节点判断、选中的边收集证据。代码能确定时不调用模型，有歧义时才通过 Jev 判断；最终由 service 执行 submit 或 steer。Argus 自身**不向 QQ 发送消息**。

## 安装与生命周期

```bash
./bin/icarus install argus --dev
./bin/icarus start argus
./bin/icarus status argus
./bin/icarus stop argus
make test-argus
```

默认健康端点 `http://127.0.0.1:8766/health`，仅绑定本机。默认 QQ connector **禁用**：此时不提供 `/onebot/events`，也不需要 SnowLuma、Jev 或 Gateway。进程使用自己的 `.venv`；`ICARUS_DATA_DIR/argus.db` 保存观察记录和 session 关联，`ICARUS_DATA_DIR/runtime/argus.json` 与 `logs/argus.log` 分别是进程记录和日志。

## 启用 QQ

在 `apps/argus/settings.json` 设置 `qq.enabled=true`、机器人的正整数 `bot_id`、绝对 `workspace_path` 与 `gateway_url`（默认本机 `ws://127.0.0.1:8765/rpc`）；在仓库根 `.env` 配置 `ARGUS_QQ_REPORT_SECRET` 与 `ARGUS_JEV_API_KEY`。SnowLuma `httpClients` 的上报 URL 指向 `http://127.0.0.1:8766/onebot/events`，`accessToken` 必须与 `ARGUS_QQ_REPORT_SECRET` 相同，`messageFormat` 使用 `array`。凭据勿写入 JSON、日志或版本库。

SnowLuma 上报签名为 `X-Signature: sha1=<原始 body 的 HMAC-SHA1>`，同时提供与 bot ID 一致的 `X-Self-ID`。有效聊天消息必须包含非零 `message_id` 与 Unix 秒 `time`；聊天消息缺少这些字段或格式错误时返回 422。验签和账号核对通过的 `meta_event`（心跳/生命周期）、`notice`（通知）、`request`（好友/群请求）返回空的 204，表示已收到但本应用不处理，不落库、不调用模型，也不自动同意请求。正常聊天消息成功返回无 JSON 响应体的 **204**，表示观察已提交数据库；之后判定不回应或等待仍属于正常业务，不影响这个成功回执。204 不表示 Icarus 已回复。来源 ID 重投不会生成第二条记录。后台批次队列满时已提交记录会在运行期补入；重启也会恢复可安全重试的记录。

## 测试数据库版本

当前使用 schema v3，仍为 observations、decision_batches、session_links 三张表。新增等待、判断完成时间、交付时间、固定接收顺序和强信号依据。测试阶段不兼容旧试验库，也不提供旧迁移入口；版本不符会明确拒绝启动，不自动改库或删除数据。需要重建时应先停止 Argus、确认目标与保留范围，再单独执行；本轮实现未操作实际用户数据库。

## 收集、退避与判断

普通群消息和私聊默认从最后一条人类消息起等待 6 秒，收集连续发言；期间有新消息就重新计算安静期限。单批最多 20 条、连续收集最长 60 秒，达到上限仍可关批。私聊不再因私聊身份、称呼或积压 6 条而跳过收集；普通群聊的明确 @、称呼命中与积压 6 条人类消息仍可提前判断。普通群聊的收集上限不能绕过退避。提前判断不保证一定回应，机器人自己的消息只作上下文，不单独唤醒模型。

连续两次正常判断“不需要回应”后，从 15 秒开始指数退避，最多 300 秒。近期 300 秒内每次成功新提交，还会增加 15 秒的普通判断间隔；追加到同一任务不当作新启动。两项取较晚期限，不相乘。新的普通消息不会使已算出的退避期限不断延后。没有待处理消息或明确等待的请求时，不主动找话。

配置默认值由 `config/config_model.py` 定义；用户 settings 中显式设置的值继续生效：

| 配置 | 默认 |
|---|---|
| `batch_quiet_seconds` / `batch_max_messages` / `batch_max_seconds` | 6 秒 / 20 条 / 60 秒 |
| `no_action_backoff_base_seconds` / `no_action_backoff_start_count` / `no_action_backoff_cap_seconds` | 15 秒 / 2 次 / 300 秒 |
| `submission_window_seconds` / `submission_spacing_seconds` | 300 秒 / 15 秒 |
| `pacing_bypass_messages` / `waiting_check_seconds` | 6 条 / 5 秒 |

证据包读取聊天前文、后到背景、Icarus 的实际任务状态与近期交付记录。策略判断是否需要回应，以及是否适合追加当前任务。运行器只执行图，service 才实际提交或追加。任务 ID 列表可能包含排队任务，不能直接当作“同时运行”的任务。

## 等待与发送安全

需要回应但不适合打断当前任务时，批次进入 `waiting`，不当作“已沉默结束”。默认每 5 秒检查工作证据；同一原因下证据不变就不反复问模型，新人类消息可以提前检查。任务结束后即使用户没有再说话，也会重新判断旧请求是否仍需处理。后来收到的消息只作背景，不冒充本批已经处理的成员。

同会话最早未结束批次优先，后续批次不能越过 waiting 或发送未知状态。发送前重新核实任务和消息是否变化；追加目标已结束或明确拒绝时回到等待，不自动变成另一份提交。网络中断、超时、不可信回执记为 `submission_unknown`，不自动重发，该会话后续等待人工核实。

请求被 Icarus 接收、任务完成、QQ 中观察到发言是不同事实，不能互相代替。不能用历史相似文本猜测提交是否成功。

## 查看判断过程

通过仅绑定本机的 `GET /observations/<observation_id>/status?after_run=0&limit=20` 查看批次状态、最终决定、等待原因和下次检查时间，以及逐轮安全执行摘要。`limit` 范围 1–100，`next_after_run` 和 `has_more` 用于继续读取。摘要包含节点状态、选边、耗时、错误类型和证据完整性，不包含聊天全文、完整模型输入或工具输出。端点无单独认证，只能保持 loopback。

Jev 支持 typed Noul/Choice/Score，一次可以带多个问题；最多三次请求，故障记录为 failed，与正常“不需要回应”区分。图默认总预算 30 秒 / 32 步，不在图内跨时间等待。

## 验证范围

`make test-argus` 使用临时配置、临时库、可控时钟和假 Jev/Gateway，不读取真实密钥或发送 QQ 消息。新等待/退避/证据流程已用隔离测试验证；本轮未启动真实服务、未做付费模型或真实 QQ 冒烟。测试能证明流程按规则执行，不能替代真实模型的判断质量评价。

当前源码架构见 [证据层与触发节奏](docs/spec/2026-10-01-evidence-and-pacing/arch.md)，设计和实施计划位于同目录；命名规则见 `docs/spec/2026-09-30-module-naming/`。
