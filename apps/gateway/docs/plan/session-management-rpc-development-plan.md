# Session Management RPC Development Plan｜Session 管理 RPC 实施计划

## 目标

为 TUI Session 管理提供稳定的摘要列表和空 Session 清理 RPC，同时复用现有 Session 查询、订阅和
历史恢复协议。

设计依据：

- `spec/session-management.md`；
- `apps/gateway/docs/arch/session-management-rpc-design.md`。

## 实施顺序

### 阶段一：共享协议模型

更新 `packages/gateway_protocol/models.py` 和导出：

- `SessionSummaryModel`；
- `SessionListModel`；
- `DiscardEmptySessionResultModel`；
- 为 Agent 领域类型增加显式转换测试；
- 摘要协议不暴露内部排序时间和 Runtime 状态。

### 阶段二：Session 列表 RPC

更新 `GatewayMethods._session_list`：

- 调用 `AgentRuntime.list_session_summaries()`；
- 使用共享 Wire Model 返回；
- 不保留 Gateway 内部排序或 journal 解析；
- 保持 `session.get` 作为单 Session Runtime 状态查询。

同步 Gateway 方法测试中的 Runtime Stub。

### 阶段三：空 Session 清理 RPC

增加：

```text
session.discard_empty
```

实施内容：

- 复用严格 `SessionParams`；
- 调用 `AgentRuntime.discard_empty_session()`；
- 将预期结果作为成功响应返回；
- Runtime 停止和未分类异常继续走现有错误映射；
- 不增加 `force` 或任意路径参数。

### 阶段四：候选连接回归

扩展 WebSocket 测试：

- 两个连接分别订阅旧 Session 和目标 Session；
- 目标 Update 只进入候选连接；
- 关闭旧连接不影响目标 Session；
- 连接发送队列和 RuntimeUpdate overflow 行为不变。

### 阶段五：文档同步

- 更新 `apps/gateway/README.md` 的 Session RPC 能力；
- 更新 `agent-gateway-positioning-design.md` 和既有 Gateway 实施计划中的方法清单；
- 不引入 `session.switch`、远程认证和 Backend 逻辑。

## 验证命令

```bash
apps/gateway/.venv/bin/python -m pytest \
  apps/gateway/test/test_methods.py \
  apps/gateway/test/test_app.py -q

make test-gateway
git diff --check
```

跨应用完成后执行：

```bash
make test
```

## 完成标准

- Session 列表与空 Session 清理具有稳定共享模型；
- `/resume` 不存在目标时不会被 Gateway 隐式创建；
- Gateway 继续只承担传输与路由职责；
- Gateway 全量测试通过。
