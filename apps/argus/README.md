# Argus

Argus 是 Icarus 的 QQ 观察、判断与唤醒桥接应用。SnowLuma 通过 OneBot v11 HTTP 上报消息；Argus 验签并落库，worker 从有界队列消费、按最小 Jev 策略判断是否调用 Icarus Gateway。Argus 自身**不向 QQ 发送消息**。

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

Jev 使用 `typesafe/jev-1.13`；调用失败最多尝试三次，仍失败会记录 `judge_fallback` 并以 silence 结束。`session.submit` 若在发送后失去响应，状态为 `submission_unknown`，Argus 不自动再次提交。通过本机 `GET /observations/<observation_id>/status` 查看状态、session/submission/task ID 与错误类型；**不能仅凭 Gateway 历史中相似文本认定提交结果**。该端点不回显聊天内容，但尚无单独认证，只能保持 loopback 监听。

自动测试使用合成 OneBot 事件、临时数据库及假 Jev/Gateway，不发送 QQ、不产生付费模型请求。真实 SnowLuma/付费 Jev 冒烟尚未执行，需事先确认账号、目标与费用。设计、实施计划与当前实现架构见 `docs/spec/2026-09-29-qq-observation-flow/`。
