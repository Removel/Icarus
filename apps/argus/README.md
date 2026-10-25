# Argus

Argus 是常驻观测入口。第一阶段持续接收 QQ 消息，经规则和必要的 Jev 选择后递交给本地 mock，
记录接收与显式解决结果。**mock 不调用真实 agent、不创建会话、不发 QQ 消息；resolved 只代表
模拟流程结束，不代表外部问题真的解决。** 新链路不使用决策图，不提供历史回放。

## 本地安装与数据库

```bash
./bin/icarus install argus --dev
export ARGUS_DEV_POSTGRES_PASSWORD='your-local-development-password'
docker compose -f apps/argus/compose.dev.yaml up -d
```

开发 Compose 使用 `pgvector/pgvector:pg17`，独立项目 `argus-dev`、数据库/账号 `argus`，默认仅
绑定 `127.0.0.1:55433`。不复用或修改 mem0、rela-mem 的真实数据库；不要删除它们的数据卷。
正式 PG 的建库、账号、授权和部署在本地功能确认后由用户手动完成，不由 Argus 自动执行。

在仓库根 `.env` 或进程环境提供 `ARGUS_DATABASE_URL`，格式为：

```text
postgresql+asyncpg://argus:<URL编码后的密码>@127.0.0.1:55433/argus
```

连接串必填，QQ 关闭时也需要数据库；密码含特殊字符时必须 URL 编码，不把真实连接串提交到 Git。
SQLite 不再使用，不迁移、不自动删除旧数据库文件。未知 schema 版本拒绝启动，不自动清库。

## 应用级生命周期

```bash
./bin/icarus start argus
./bin/icarus status argus
./bin/icarus stop argus
```

继续使用 `apps.argus.src.main` 和现有 Icarus 应用管理器。地址来自应用配置，管理器可通过
host/port 参数覆盖；只允许 `127.0.0.1`。健康探针为 `/health`，统一接口别名为
`/api/argus/health`，共同检查数据库和已启用的后台任务。

## QQ 与规则

`apps/argus/settings.json` 设置 `qq.enabled=true`、合法 `bot_id` 和机器人别名。
提供 `ARGUS_QQ_REPORT_SECRET`，OneBot 上报到：

```text
http://127.0.0.1:8766/api/argus/qq/onebot/events
```

上报使用原始 body 的 HMAC-SHA1，header 为 `X-Signature: sha1=<40位hex>`、
`X-Self-ID: <bot_id>`。建议 messageFormat=array，也接受没有 CQ 的普通文本。
签名不合法401、账号不匹配403、超长413、结构非法422、停止接收503。成功204只表示已保存观测，
不表示 agent 已处理。心跳等非聊天事件确认后204但不落库；消息撤回会保存新的删除版本。

- 普通私聊直接触发，群聊 @/别名强信号尽快判断；自消息、敏感内容等门槛仍生效。
- 普通群聊安静5秒后判断，持续来消息最长等30秒。
- 同一个群正在处理时继续保存新消息，不打断、不追加当前请求；结束后重新判断积累内容。
- 不同聊天对象独立，默认最多8个并发工作。
- 默认每批20条、20000字符；单条过长使用短摘录，完整原文保留。预算外消息仍待处理。
- 判断规则在 `rules/qq.yaml`。只有机械规则无法决定时才使用已启用的 Jev；Jev 先回答 `can_speak`（是否适合说话），再回答 `speak_now`（现在就说吗），两个都是 yes 才唤醒，没有 `none`。
- 语义窗口优先使用已入库观测；`qq.history` 开启时从 SnowLuma 的 `get_group_msg_history` / `get_friend_msg_history` 回填缺失消息（尤其是机器人自身消息），按 `message_id` 去重，失败时回退到本地观测。
- 推荐在 SnowLuma 适配器开启 `reportSelfMessage`，让自消息实时入库；历史回填用于补启动前或漏掉的消息。
- Jev 默认关闭，开启需配置 `jev.enabled=true` 与 `ARGUS_JEV_API_KEY`。失败、非法结果或任一问题为否默认沉默。
- `sensitive_keywords` 默认为空，需要操作者配置关键词；不是完整的语义内容审核机制。
- QQ 关闭时不挂上报路由、不扫描来源、不调用 Jev/mock，但只读接口和健康检查仍可用。

## 规则配置与生效

规则文件为 [`rules/qq.yaml`](rules/qq.yaml)，完整字段、可用 facts、条件操作符、语义规则与
离线校验方法见 [规则配置指南](docs/rules.md)。

- 默认机械规则：私聊叫醒（优先级200）；群 @/叫名字叫醒（优先级100），不调用 Jev。
- 机械规则按优先级从高到低，第一条命中即结束；同优先级按文件顺序。
- 只有机械规则未命中时，才把满足 `when` 的语义规则交给已启用的 Jev；Jev 先回答 `can_speak`，再回答 `speak_now`，两个都是 yes 才唤醒。
- `wake` 表示递交请求，`ignore` 表示忽略；尽快判断不等于保证叫醒，基本检查不能被规则绕过。

例如，向 `rules` 列表加入下面一条，可让某个群只记录、不叫醒，即使 @ 也不递交：

```yaml
- id: mute_specific_group
  description: "这个群只观测，不递交"
  priority: 1000
  when:
    chat_id: "qq:123:group:456"
  decision: ignore
  reason: "该群配置为只观测"
```

请替换机器人账号和群号；ID 保持字符串。规则作用于本轮预算内的一批消息，不是逐条独立执行。
可组合 `all` / `any` / `not`，比较支持 `eq`、`ne`、`in`、`contains_any`、`contains_all`、
`gte`、`lte`、`exists`；不支持正则或任意 Python 表达式。

**当前没有热重载。** 修改后建议递增 `version`，按指南离线检查，再等当前工作结束后使用
`./bin/icarus stop argus`、`./bin/icarus start argus` 生效。重启不恢复旧待办，改规则也不会重新
处理旧的 ignored 记录；操作前请留意尚未处理的积压。

## 重启、状态与模拟结果

每次启动有新 boot_id。**重启后不恢复旧待办、不重投旧请求、不以旧消息作判断上文**；旧记录
仍可查询，但不占本次启动的处理名额。跨启动重复上报仍按全库消息身份去重，不会重新处理。
这意味着停机前尚未完成的工作不会自动补做。

请求活跃状态：waiting→sent→accepted；终态：resolved/no_action/failed。
只有接收确认才将观测记为 delivered，显式结果才记为 processed；mock 不会写 verified。
发送不确定时保留相同 wake_id，默认最多3次尝试，间隔1秒、2秒；accepted 最多等60秒。
最终失败释放聊天对象，但该失败批次不无限自动重做。

## 仅本机读接口

| 路由 | 内容 |
|---|---|
| `GET /api/argus/observed-items/{id}` | 完整观测与版本身份 |
| `GET /api/argus/wake-requests/{id}` | 请求、关联消息、模拟解决结果 |
| `GET /api/argus/wake-requests?status=&chat_id=&limit=50&offset=0` | 有限分页，active区分当前启动与历史 |
| `GET /api/argus/chats/{chat_id}/unhandled?limit=50` | 本次启动尚未关联请求的最新消息 |

limit 范围1–100。读接口不鉴权，因此不能开放外网；其中包含聊天原文，不用于公开页面。

## 真实 Agent 接线（显式配置启用）

复用真实Agent和既有会话/插件机制，同群read/history/wait与逐项report已接线；退出、工具存活、
报告封口与scope释放分开，读过但未报告的途中消息仍new。默认仍mock，不因代码存在启用。
2026-10-22按用户简化决议，后台直接使用Agent已配置的MCP工具（包括SnowLuma），不新增动作
白名单、群范围校验或发送记账；是否回复由模型选择并实际调用工具，最终正文不会自动发QQ。

启用需要显式配置：Argus `execution.mode="gateway"`，Gateway URL使用`/argus/rpc`；环境提供
`ARGUS_GATEWAY_TOKEN`和独立`ARGUS_AGENT_API_TOKEN`，不复用QQ上报secret。Agent配置
`argus.enabled=true`、已存在的绝对后台workspace、Argus内部base_url；同data_dir只能一个宿主。
后台沿用全局MCP配置，普通交互不变；没有当前群强制路由保证。执行默认最多8个、总期限15分钟；wait单次≤30秒。

数据库启动显式迁移v1→v2，原wake/observations加handoff/exposed_wake_id，不删旧记录。
正式迁移前备份并另获授权；只在隔离PG验证过。rollback保留新schema，不清库、不切mock假装
旧真实执行完成，保持status/cancel/报告同步通路直到确认退出。

普通host保持loopback；独立部署使用显式service_host/service_port，只挂Bearer内部prefix，
不挂公开GET/QQ上报。Gateway也有专用listener，仅四个认证方法；ACL必须，私网/VPN无TLS，
不支持公网，不信任forwarded header。本机真实模型/QQ工具调用已验证，但不代表正式部署或
全面生产验收；本机运行配置和原始测试记录不随代码提交。


- [跨应用共同契约](../../spec/2026-10-21-argus-agent-handoff.md)：身份、scope、RPC/HTTP、报告和取消语义。
- Argus：[目标设计](docs/spec/2026-10-21-argus-agent-handoff/design.md)、[当前架构差距](docs/spec/2026-10-21-argus-agent-handoff/arch.md)、[逐文件实施计划](docs/spec/2026-10-21-argus-agent-handoff/plan.md)。
- Agent 与 Gateway 的自有设计/架构/计划由共同契约入口链接，不在 Argus 文档重复实现细节。

## 测试

```bash
make test-argus
# 或只跑指定路径
apps/argus/scripts/test.sh apps/argus/test/common -q
```

没有 `ARGUS_TEST_DATABASE_URL` 时脚本创建自己的临时 PG17 容器，随机本机端口、独立 `argus_test`
库，退出只清理该容器。也可显式提供专用测试 DSN；库名必须以 `_test` 结尾，fixture 只创建和
删除自身唯一 schema。**绝不能将生产数据库当测试库。** Docker不可用时明确失败，不以skip假绿。
测试屏蔽真实配置和密钥，使用 typed Jev fixture、本地 mock 和隔离 PG，不启动真实 QQ/Gateway。

设计与逐文件实施任务见 [第一阶段设计](docs/spec/2026-10-21-argus-positioning/design.md) 和
[实施计划](docs/spec/2026-10-21-argus-positioning/plan.md)。旧 feature 目录记录历史方案，
不再作为新链路的当前架构说明。
