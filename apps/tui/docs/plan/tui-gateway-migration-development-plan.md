# TUI Gateway Migration Development Plan｜TUI Gateway 迁移实施计划

## 目标

将当前 TUI 进程内直连 AgentRuntimeService 的路径迁移为：

```text
TUI
→ Agent Gateway WebSocket / JSON-RPC 2.0
→ AgentRuntime
→ SessionRuntime
```

迁移后 TUI 不导入 AgentRuntime、SessionRuntime、Plugin Event 或具体 Plugin 类型；只依赖 Gateway
协议和公共 RuntimeUpdate。保留当前本地草稿、发送队列、取消、流式文本、Tool 展示和剪贴板图片
体验。

## 前置条件

- AgentRuntime、SessionRuntime、RuntimeUpdate 和 ResourceRef 已实现；
- Gateway 的 Session/Task RPC、连接订阅和 RuntimeUpdate Notification 已稳定；
- Gateway 默认本机监听方式和启动发现方式可供 TUI 使用。

## 实施原则

- 不保留进程内 Agent fallback，不维护两条生产调用链；
- 客户端队列继续负责草稿、未确认请求、重试和 UI 恢复；
- Runtime 返回 task_id 后，TUI 使用 Runtime 状态作为执行事实；
- Textual UiAction、Widget 和展示文案继续属于 TUI；
- Replay 与真实连接都从 RuntimeUpdate 投影，避免测试一条旧协议、生产跑另一条协议；
- 图片二进制不经过 WebSocket，不向 Gateway 发送绝对路径。

## 目标文件结构

```text
apps/tui/src/gateway_client/
├── __init__.py
├── client.py
├── models.py
└── subscription.py
```

TUI 直接复用 `apps.gateway.src.protocol.models` 中不依赖 FastAPI 的网络协议模型，不复制同名结构；
但不导入 Gateway 服务实现、FastAPI App 或 Agent 内部对象。

## 阶段一：Gateway Client

### 新增文件

- `apps/tui/src/gateway_client/`
- `apps/tui/test/gateway_client/`

### 开发内容

1. 默认连接 `ws://127.0.0.1:8765/rpc`，允许通过 `--gateway-url` 覆盖；实现 JSON-RPC request id、
   pending response、RuntimeUpdate notification 和连接关闭。
2. 单独维护读循环和写串行入口；Response 与 Notification 可以交错，Response 必须按 id 唤醒正确
   请求。
3. 提供 TUI 所需方法：runtime status、session create/get/list/submit/cancel/unload/subscribe/
   unsubscribe、task status。
4. 将 JSON-RPC 错误映射为少量客户端领域错误；不让 Textual 直接解析原始 error dict。
5. 连接关闭时唤醒所有 pending request 和 Update waiter，不留下后台 Task。

### 定向测试

- Response 乱序仍按 request id 关联；
- Notification 与 Response 交错不互相消费；
- 连接断开唤醒所有等待者；
- 服务端业务错误保留安全 code，不暴露内部异常；
- 并发 request 不发生 WebSocket 并发写。

## 阶段二：RuntimeUpdate 到 UiAction 的投影

### 更新文件

- `apps/tui/src/event_pipeline/dispatcher.py`
- `apps/tui/src/event_pipeline/projectors/`
- `apps/tui/src/event_pipeline/actions.py`
- 对应 Projector 测试

### 开发内容

1. 删除按 source_plugin_id 和 Python Event 类型分发，改为按 RuntimeUpdate.type 显式注册投影。
2. 映射第一阶段 Update：
   - task.accepted / started / finished；
   - task.error / task.usage；
   - assistant.text_delta；
   - tool.started / completed；
   - context.compacted；
   - session.lifecycle。
3. Tool arguments 在 TUI 投影时格式化为展示字符串；Agent/Gateway 保持 JSON 对象。
4. tool.completed(success=false) 已显示 Tool 错误时，不再重复显示隐藏的 tool_execution_failed。
5. 不在 TUI 恢复 source_plugin_id 或内部 Event 类依赖。

### 定向测试

- 每种 RuntimeUpdate 产生确定 UiAction；
- 不相关 Session/Task Update 被正确过滤；
- Usage、Compact 和 Session 生命周期可识别；
- Tool 失败不重复展示；
- 未知 Update 只记录诊断，不导致 UI 崩溃。

## 阶段三：TUI 控制器接入 Gateway

### 更新文件

- `apps/tui/src/main.py`
- `apps/tui/src/app.py`
- `apps/tui/src/chat_state.py`
- `apps/tui/test/test_app.py`
- `apps/tui/test/test_app_snapshots.py`

### 开发内容

1. main 不再延迟导入 AgentRuntimeService，改为构造 GatewayClient。
2. 启动后检查 Gateway、获取或创建目标 Session，并在订阅成功后开放输入。
3. TUI 本地队列中的每条 PendingMessage 生成并保留 submission_id，直到收到 task_id。
4. submit 失败或响应丢失时不丢队首；重连后使用相同 submission_id 重试。
5. 收到 task.accepted 后从客户端未确认状态切换为 Runtime 状态；后续 started/finished 更新当前任务。
6. cancel 使用 session.cancel；已结束、未找到和未加载状态使用现有清晰反馈。
7. TUI 退出只关闭 Gateway 连接，不调用 AgentRuntime.stop 或 Session unload。
8. Gateway 不可用时显示连接错误，保留草稿与未确认队列，不在本地启动隐藏 Runtime。

### 定向测试

- 启动、连接失败、订阅失败和恢复成功状态；
- 客户端草稿、pending、dispatch_in_progress 和 active_task_id 不混淆；
- 响应丢失重试不会重复创建 Task；
- TUI 关闭后远端 Task 继续运行；
- Cancel、错误、完成和下一条本地消息派发不回归；
- 现有布局与快照只发生预期变化。

## 阶段四：剪贴板图片与 ResourceRef

### 更新文件

- `apps/tui/src/clipboard.py`
- `apps/tui/src/submission.py`
- `apps/tui/src/chat_state.py`
- `apps/tui/src/app.py`
- 对应 Clipboard、ChatState、App 和视觉快照测试

### 开发内容

1. 继续复用现有 ClipboardImage 读取、格式转换和草稿附件 UI。
2. 将图片写入现有 `ICARUS_DATA_DIR/incoming/` 受控本地暂存根目录，并在 PendingMessage 中保存相对
   resource_id，不向 Gateway 发送绝对路径。
3. session.submit 发送 ResourceRef；成功 Response 表示图片已复制到 Session assets，此时才能清理
   对应暂存文件。
4. 请求失败、连接中断或响应未确认时保留暂存文件和原 submission_id，以便安全重试。
5. 清除草稿、移除待发送消息或 TUI 退出时，只清理当前 TUI 自己仍拥有的未提交暂存文件。
6. 不在 JSON-RPC、Replay 或日志中内联图片二进制。

### 定向测试

- 粘贴图片生成受控 ResourceRef；
- 成功确认后删除暂存文件，Session Asset 仍可用；
- 失败和断线后保留暂存文件并可重试；
- 清草稿只删除对应资源，不影响其他消息；
- 路径和错误文案不泄露敏感绝对路径；
- 文本与图片混合提交、纯图片提交、队列恢复和 Ctrl+C 不回归。

## 阶段五：Replay、Transcript 与状态重建

### 更新文件

- `apps/tui/src/replay.py`
- `apps/tui/src/transcript.py`
- Replay Fixture 和对应测试

### 开发内容

1. Replay Schema 迁移为 RuntimeUpdate 序列，不再序列化 source_plugin_id 和内部 Event 类型。
2. ReplaySubscription 与 GatewayClient 暴露相同 Update 消费接口。
3. Transcript 通过公共 Projector 生成，不导入 Agent Event。
4. 重连时重新订阅当前 Session，再调用 session.get 和 task.get_status 恢复当前投影。
5. 第一阶段不请求 sequence、游标或 Update 历史补发；无法从内存 Task 状态恢复时显示明确的状态
   不可用，而不是猜测终态。

### 定向测试

- Replay 和真实 Gateway Update 得到相同 UiAction；
- 重连后 ready/running/unloaded/failed 状态正确；
- Session 自动 unload 后再次提交会自动 resume；
- 无法恢复近期 Task 状态时 UI 不显示伪终态。

## 阶段六：删除旧直连入口

### 删除或更新

- 删除 TUI RuntimeService/RuntimeSubscription 旧进程内协议；
- 删除 main 中 AgentRuntimeService 导入和本地构造；
- 删除 AgentRuntimeService 与旧 OutputBridge 的生产代码和公开导出；
- 将仍有价值的旧测试迁移到 SessionRuntime、AgentRuntime、RuntimeUpdate 或 GatewayClient；
- 删除旧 `(source_plugin_id, Event)` Replay 和 Projector Fixture；
- 更新 TUI 架构文档、TODO、CLI 帮助和 package-data。

### 收口要求

- TUI 只通过 Gateway 使用 Agent；
- Gateway 不可用时没有隐式本地 Runtime fallback；
- 仓库生产代码不存在旧 AgentRuntimeService 和 OutputBridge；
- TUI 不导入具体 Agent Plugin Event；
- GatewayClient 不包含 Textual Widget 或 ChatState 逻辑。

## 验证顺序

每阶段运行最小测试：

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/gateway_client -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/event_pipeline -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_app.py -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_replay.py -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_clipboard.py -q
```

完成迁移和旧入口删除后：

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m pytest apps/gateway/test -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src apps/agent/test \
  apps/gateway/src apps/gateway/test \
  apps/tui/src apps/tui/test
git diff --check
```

## 完成标准

- TUI 只依赖 Gateway 协议和 RuntimeUpdate；
- 本地双队列的责任切换和 submission_id 重试成立；
- 文本、Tool、错误、Usage、Compact 和终态展示不回归；
- 图片通过 ResourceRef 提交，成功确认后 Runtime 已拥有 Session Asset；
- 断线、重连、Session unload/resume 和 TUI 退出不错误终止 Runtime Task；
- Replay 与真实连接使用同一公共投影；
- 旧 AgentRuntimeService、OutputBridge 和内部 Event UI 依赖全部删除；
- Agent、Gateway、TUI 全量测试、编译和 diff 检查通过。

## 实施结果

- TUI 已切换为 GatewayClient，不再在进程内导入或创建 AgentRuntime；
- 已迁移 RuntimeUpdate Projector、Replay Schema v4、Transcript 和断线后的 Session/Task 状态对账；
- PendingMessage 持有稳定 submission_id，未确认提交可安全重试；
- 剪贴板图片写入 `$ICARUS_DATA_DIR/incoming/`，只传 ResourceRef，确认后清理暂存文件；
- TUI 全量测试和 9 个视觉快照通过，生产代码不再依赖 Agent Plugin Event。
