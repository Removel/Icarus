# Session Management RPC Design｜Session 管理 RPC 设计

## 文档定位

本文定义 `apps/gateway` 为 Session 列表、恢复和空 Session 清理提供的 JSON-RPC 契约。产品行为见：

`spec/session-management.md`。

Gateway 继续只负责参数校验、序列化、调用路由和实时更新分发，不读取 Session 目录、不解释
conversation journal，也不持有切换状态。

## 当前基础

Gateway 当前通过 FastAPI WebSocket `/rpc` 暴露 JSON-RPC 2.0，并已有：

- `session.create`；
- `session.list`；
- `session.get`；
- `session.subscribe` / `session.unsubscribe`；
- `session.get_history`；
- `session.submit`、`session.cancel`、`session.unload`；
- `task.get_status`；
- `runtime.update` Notification。

本期不增加 `session.switch`。选择和切换是 TUI 当前投影的变化，服务端只提供目标 Session 的事实、
历史和订阅。

## 共享 Wire Model

在 `packages.gateway_protocol` 增加：

```python
class SessionSummaryModel(StrictWireModel):
    session_id: str
    first_user_input: str

class SessionListModel(StrictWireModel):
    sessions: tuple[SessionSummaryModel, ...]

class DiscardEmptySessionResultModel(StrictWireModel):
    workspace_key: str
    session_id: str
    status: Literal["discarded", "not_empty", "busy", "not_found"]
```

共享模型只表达稳定协议，不导入 Agent dataclass。Gateway 使用显式 `from_domain` 或现有 `_wire` 边界
转换。排序由 AgentRuntime 完成，Gateway 保持返回顺序；排序时间不跨越应用边界。

## `session.list`

请求：

```json
{
  "workspace_path": "/absolute/workspace"
}
```

响应：

```json
{
  "sessions": [
    {
      "session_id": "...",
      "first_user_input": "分析 Agent Runtime 架构"
    }
  ]
}
```

Gateway 调用 `AgentRuntime.list_session_summaries(workspace_path)`，不调用 Runtime 加载接口。结果已由
Agent 层排好序，Gateway 不重新解释时间或会话内容。

当前 `session.list` 尚未被 TUI 使用，本期将其从 Runtime 状态列表收敛为产品 Session 摘要列表。
需要同步更新 Gateway 测试与架构文档；单 Session 状态继续通过 `session.get` 查询。

## `session.discard_empty`

请求：

```json
{
  "workspace_path": "/absolute/workspace",
  "session_id": "session-id"
}
```

响应使用结构化状态：

```json
{
  "workspace_key": "...",
  "session_id": "session-id",
  "status": "discarded"
}
```

`not_empty`、`busy` 和 `not_found` 是可预期操作结果，不映射为 JSON-RPC Error。参数非法、Runtime 正在
停止、持久化损坏和未分类异常继续使用现有错误响应机制。

Gateway 不接收 `force`，不提供删除非空 Session 的旁路。

## Session 激活所用现有 RPC

TUI 准备候选 Session 时继续使用：

```text
session.get
→ session.subscribe
→ session.get_history(after_sequence=0)
→ session.get 再次确认无工作
```

入口语义：

- 启动 `icarus`：不存在时由客户端调用 `session.create`；
- 启动 `icarus --session-id <id>`：保持当前不存在时创建行为；
- `/resume`：不存在时直接失败，禁止调用 `session.create`；
- `/clear`：使用新 ID 明确调用 `session.create`。

`/resume` 的目标 Busy 判定基于 `session.get` 返回的现有 lifecycle 与工作计数。状态不进入 Picker
展示。最终 `session.get` 是激活前的时点检查；检查后仍可能有另一个客户端并发提交，本期不引入
Session Lease 或多客户端所有权协议。命令行指定 Session 的启动行为保持现状，不额外增加 Busy 拒绝。

## 订阅与连接

每个 `GatewayConnection` 继续持有自己的 Session 订阅集合。候选 GatewayClient 在切换准备期间使用
独立 WebSocket：

```text
旧连接继续接收当前 Session
候选连接订阅目标 Session 并缓冲 Update
→ 候选历史和状态验证成功
→ TUI 提交切换
→ 关闭旧连接
```

Gateway 不需要知道哪个连接是“当前 TUI”。连接断开不取消 Task、不卸载 Session。慢连接继续使用现有
有界发送队列和关闭策略。

## 错误映射

沿用现有安全错误：

- `session_not_found`；
- `history_corrupt`；
- `runtime_stopping`；
- `invalid_params`；
- `internal_error`。

本期不把 Agent 内部路径、Plugin 错误、Trace 或 Python 异常文本写入 RPC 响应。

## 兼容性

- 不修改 `session.create`、`session.get`、`session.subscribe`、`session.get_history` 和实时
  `runtime.update` 结构；
- `session.list` 的列表项从运行状态转为产品摘要，需要同步当前测试；
- 运行诊断仍使用 `session.get`，未来如需设备级 Runtime 管理列表应定义独立诊断 RPC；
- Gateway 与 AgentRuntime 继续同进程运行，但协议不依赖该部署方式；
- 不新增 Backend、远程认证或文件上传。

## 测试范围

- `session.list` 参数校验、顺序保持和严格 Wire Model；
- 列表只返回 AgentRuntime 提供的非空摘要；
- `session.discard_empty` 四种业务状态；
- `/resume` 所需 `session.get`、subscribe、history 调用仍兼容；
- notification 失败不返回 Error Response；
- 候选连接和旧连接按各自订阅过滤 RuntimeUpdate；
- 业务错误不泄漏内部路径和异常文本。

## 完成标准

- TUI 不访问 Agent 内部类型即可列出和识别 Session；
- TUI 可以通过正式 RPC 请求安全丢弃空 Session；
- Gateway 不承担 Session 切换状态机或持久化逻辑；
- 现有任务、历史和订阅 RPC 行为保持一致。
