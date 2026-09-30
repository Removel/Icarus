# Argus

Argus 是 Icarus 的 QQ 观察与触发应用。签名上报先落库，Observation 经队列交给 worker，按会话成批后运行由 QQService 编排的决策图。代码能确定时不调用模型，有歧义时才通过 Jev 判断；最终由 service 执行 submit 或 steer。Argus 自身**不向 QQ 发送消息**。

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

SnowLuma 上报签名为 `X-Signature: sha1=<原始 body 的 HMAC-SHA1>`，同时提供与 bot ID 一致的 `X-Self-ID`。有效聊天消息必须包含非零 `message_id` 与 Unix 秒 `time`；缺少这些字段或收到非聊天事件时会拒绝。成功返回无 JSON 响应体的 **204**：仅表示观察已提交 Argus 数据库，不表示 Jev 已判断或 Icarus 已回复。来源 ID 重投不会生成第二条记录。消息队列满时已提交记录会在运行期补入；重启也会恢复可安全重试的记录。

## 升级已有数据库

新版使用 schema v2（三张业务表）。已有旧库不能直接启动，需在确认停止 Argus 后执行：

```bash
./bin/icarus stop argus
./apps/argus/scripts/migrate.sh
./bin/icarus start argus
```

维护脚本检测仍在运行的 Argus 会拒绝迁移；如果 stop 报 external，不要继续迁移，先确认进程已停止。SQLite 需 3.35+。升级先生成 `argus.db.backup-<时间>`（包含 WAL 数据），事务内将旧裁决、错误、session/task/submission 身份移至批次，再删除旧处理列；历史已提交消息不重放。本轮开发没有执行用户真实数据库迁移，部署前应保留备份；升级后不要用旧版本进程打开新版库。

## 批次与判断

群聊默认静默 5 秒、20 条或开批 60 秒先到先关闭；私聊即时，at/称呼会提前关闭群批次。配置字段为 `batch_quiet_seconds`、`batch_max_messages`、`batch_max_seconds`，默认值来自配置模型；这些是批次窗口，不是每分钟调用上限，也没有额外冷却。原 300 秒自动丢弃规则已移除。

JevClient 支持一次多问题 Noul/Choice/Score，最多尝试三次，最终故障记录为失败 silence，并打 warn；不混同于正常判断 silence。批次默认 graph 预算为 30 秒 / 32 步，可用 `graph_timeout_seconds`、`graph_max_steps` 配置。`submit` 或 `steer` 发送结果未知时为 `submission_unknown`，不自动重发；该 subject 后续批次会等待人工核实。通过本机 `GET /observations/<observation_id>/status` 查看 `batch_id`、批次状态和目标身份；不能用历史相似文本猜测提交是否成功。端点无单独认证，只能保持 loopback。

自动测试使用临时配置、临时库及假 Jev/Gateway，标准 `make test-argus` 也不读取用户密钥或真实库。上一期单消息 QQ 流程已做真实验证；本次批次/图流程仅经合成测试，尚未进行真实外部冒烟。当前架构和计划见 `docs/spec/2026-09-30-judgement-graph/`；命名规则见 `docs/spec/2026-09-30-module-naming/`。
