# Argus 第一阶段源码架构

> 依据 2026-10-21 新链路源码和隔离 PG 测试整理。旧 SQLite/submit/steer 链路已删除，
> decision_graph 仅保留独立基座，不参与运行。功能取舍见 [design.md](design.md)，任务见 [plan.md](plan.md)。

## 入口与分层

`src/main.py` 提供 FastAPI create_app 和 CLI。保留 `python -m apps.argus.src.main`，
`icarus.toml` 将 host/port 传入现有 start.sh，/health 验证 ready 响应。

- core：配置、PG 生命周期和 schema 版本。
- common：规范化 schema、ORM、两表 repository、规则/判断/叫醒 services、客户端和调度 task。
- surfaces/qq：OneBot HTTP、输入 schema、解析/事实/接收 services，无独立业务表。
- common 不依赖具体来源；入口显式注入 QQFactService。ORM 行不进入 HTTP handler 或外部客户端。

路由在 create_app 注册一次。每次 lifespan 产生新 boot_id、repository 和来源对象，通过
app.state 交给路由，避免重复启动复用旧 boot 的闭包。

## 接收与持久化

HTTP 对原始 body 流式限长、验 HMAC、核对 header/body 账号，随后 parser 规范化，repository
提交后204。模型判断和处理端调用不在接收请求内。

PG 的 observed_items 保存全部内容 revision，wake_requests 保存冻结递交包、尝试及结果，
schema_version 当前为1。DDL 在 db/schema.sql；ORM 为显式映射，不另行 create_all。
同消息使用事务级 advisory lock，历史 fingerprint 去重；撤回为新版本墓碑，不被迟到消息复活。

repository.commit_judgement 在事务中重取消息身份锁，校验版本/boot/chat/状态后再忽略或保留
消息。活跃唯一索引为本 boot+chat 的 waiting/sent/accepted，历史 accepted 不占新启动名额。

## 判断与调度

普通群聊安静5秒或最长30秒，私聊和强信号尽快处理；窗口计算覆盖全部待处理消息，选取预算
只处理前20条。判断先排除自消息和敏感全文，再构造短摘录，Jev 只见有限且过滤后的文本。
YAML机械规则优先，语义候选才请求 typed Jev；语义判断使用按时间排序的窗口（sender/text/
sent_at/current），Icarus 自消息只作上下文；Jev 先回答 can_speak（是否适合说话），再回答
speak_now（现在就说吗）；两次都是是才唤醒，失败默认沉默。窗口优先来自本地观测；配置
qq.history 时用 SnowLuma history 回填缺失消息，失败回退。

scheduler 为每 chat 最多创建一个 worker，总体有限并发并轮转。已活跃 chat 只推进其请求，
新消息继续入库；终态后重新判断积累消息，不逐条打断，也不默认把闲聊递交。无启动恢复。

## mock 与结果

WakeService 每轮推进一个步骤：waiting→sent→accepted→resolved/no_action/failed。
重试使用相同 wake_id/冻结 payload；接收超时与处理超时分别有界。显式完整匹配的结果才能把
观测 delivered 推进到 processed；失败为 failed，永不由 mock 写 verified。

MockAgentClient 无平台行为、会话、工具、网络或记忆写入，结果 simulated=true。
Gateway 传输代码迁到 common/clients/gateway_client.py，但第一阶段未装配，不注册 argus.wake。

## 生命周期与验证边界

退出时先停止接收，再取消/等待 task/worker，关闭入口拥有的客户端和数据库；注入对象由调用方
关闭。QQ禁用不挂来源路由、不创建来源 worker，只读和健康仍需PG。

测试使用独立 pgvector/pgvector:pg17；HTTP端到端为ASGI+真实PG+mock，还验证真实start.sh→
命名host/port参数→loopback TCP健康接口→优雅停机，使用单独的测试数据库并已清理。未访问正式库、
未调用真实QQ/Jev/Gateway/agent，模拟解决不代表真实动作已核实。
