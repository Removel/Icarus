# Gateway Thinking and Tool Output Protocol Implementation Plan｜Gateway 思考与工具输出协议实施计划

## 目标

基于以下设计，通过现有 JSON-RPC RuntimeUpdate、订阅和 Session History 通路公开 Agent thinking 与
Tool 安全预览：

- [跨应用规格](../../../../../spec/2026-09-21-agent-thinking-experience.md)；
- [Gateway 设计](arch.md)；
- [Agent 设计](../../../../agent/docs/spec/2026-09-21-agent-thinking-experience/arch.md)。

本计划不新增 RPC 方法，不增加 Gateway 聚合状态，也不读取 Agent 本地 Tool Result 文件。

## 前置门禁

开始本计划前，Agent 应已固定以下 RuntimeUpdate：

```text
assistant.thinking_delta(step, text), sequence=None
assistant.thinking(step, text, partial), sequence>0
tool.completed(existing fields + safe preview fields)
```

Gateway 计划可以与 Agent 测试编写并行准备，但最终协议测试必须使用 Agent 的实际 Domain
`RuntimeUpdate` 构造，不能仅依赖手写 dict。

## 阶段一：固定开放 RuntimeUpdate Wire 契约

### 更新文件

- `apps/gateway/test/test_app.py`
- `apps/gateway/test/test_methods.py`

### 开发内容

1. 保持 `RuntimeUpdateModel.type: str`、`payload: dict[str, Any]` 和现有 `from_domain()` 原样转换，
   不引入按 type 分支的严格 payload 模型或 discriminated union。
2. 使用 Agent 的真实 Domain `RuntimeUpdate` 固定下列 Wire 形状：
   - `assistant.thinking_delta(step, text)`；
   - `assistant.thinking(step, text, partial)`；
   - `tool.completed` 的既有字段加 `output_preview`、`preview_truncated`、
     `full_result_available`、`preview_error`。
3. 断言 output preview 的对象、数组、字符串、数值、布尔和 null 在 Domain/Wire 转换后不变。
4. 断言公共 payload 不出现 Agent 本地结果路径或任意 Tool metadata。
5. 已知字段的业务约束由 Agent 生产端和 TUI Projector 消费端分别测试；Gateway 只验证通用 Envelope，
   避免一个未来可选字段导致广播泵拒绝整个开放协议。

### 定向测试

- 两种 thinking payload 与 sequence 在转换后保持不变。
- Tool preview 的所有 JSON 值形态保持不变。
- 旧版 `tool.completed` 缺少新增字段时仍能透传。
- 未知 update type 继续透传，不被新增功能封闭。
- 非 JSON payload 仍由现有 Domain `RuntimeUpdate` 边界拒绝。

### 验证命令

```bash
apps/gateway/.venv/bin/python -m pytest \
  apps/gateway/test/test_app.py \
  apps/gateway/test/test_methods.py -q
```

## 阶段二：验证实时与历史透传

### 核对文件（预计无需生产改动）

- `apps/gateway/src/connection.py`
- `apps/gateway/src/protocol/methods.py`

当前通用 Domain/Wire 转换已经覆盖新增类型；若测试证明无需改动，不为本阶段制造空洞的生产 diff。

### 更新文件

- `apps/gateway/test/test_app.py`
- `apps/gateway/test/test_methods.py`

### 开发内容

1. Connection 的实时通知和 Methods 的历史响应继续统一调用
   `RuntimeUpdateModel.from_domain()`，不复制两套转换。
2. 使用真实 Domain update 覆盖 thinking delta、完整 thinking 和扩展 Tool completion。
3. 断言实时 thinking delta 的 `sequence=None`，持久化 thinking 使用正整数 sequence；该事实由 Agent
   生产测试保证，Gateway 只证明不改写。
4. 如果现有测试直接比较 Tool payload，更新预期以包含新增字段；不要删除既有字段或补造旧历史字段。
5. 保留 Gateway 广播泵的既有连接隔离和开放类型行为，不为 thinking 创建新缓存。

### 定向测试

- 实时 connection 将 thinking delta 发送为 `runtime.update`，sequence 为 null。
- 完整 thinking 的 Notification 和 `session.get_history` record 完全同形。
- History 返回 Tool preview 与完整结果可用性，不丢失 JSON 类型。
- delta 不进入 History 的事实由 Agent 测试负责；Gateway 测试只断言给定 History 不自行合成 delta。
- 未知 type 继续通过现有 RuntimeUpdate Wire 转换。

### 验证命令

```bash
apps/gateway/.venv/bin/python -m pytest \
  apps/gateway/test/test_app.py \
  apps/gateway/test/test_methods.py -q
```

## 阶段三：验证订阅顺序与重连历史

### 更新文件

- `apps/gateway/test/test_app.py`
- `apps/gateway/test/test_methods.py`

### 开发内容

1. 扩展 WebSocket 订阅测试，依次发布：

   ```text
   assistant.thinking_delta(step=1, sequence=None)
   assistant.thinking(step=1, sequence=N)
   assistant.message(step=1, sequence=N+1)
   tool.started(step=1, sequence=N+2)
   tool.completed(step=1, sequence=N+3)
   assistant.thinking_delta(step=2, sequence=None)
   assistant.thinking(step=2, sequence=N+4)
   assistant.message(step=2, sequence=N+5)
   ```

2. 断言 Gateway 保持 Agent 发布顺序：每个模型 step 的完整 thinking 先于该 step 的完整 Assistant
   Message，Tool step 的 Message 先于 Tool started/completed；Gateway 不按 sequence 把实时 delta 移到
   其他位置。
3. 断言未订阅 Session 和其他 Workspace 的 thinking 仍被连接过滤。
4. 扩展 `session.get_history` 分页测试：
   - 完整 thinking 跨页时 cursor/next_after_sequence 正确；
   - Tool preview 在分页和 JSON 序列化后保持一致；
   - Gateway 不增加 delta 补发记录。
5. 验证慢连接/关闭路径不因新 update 类型产生独立缓存或泄漏。

### 定向测试

```bash
apps/gateway/.venv/bin/python -m pytest \
  apps/gateway/test/test_app.py \
  apps/gateway/test/test_methods.py -q
```

## 阶段四：透传 Steer 幂等键并保持单一 RPC 语义

### 更新文件

- `apps/gateway/src/protocol/methods.py`
- `apps/gateway/test/test_methods.py`

### 开发内容

1. 保持 `session.submit` 请求、资源上传、display text 和 accepted response 不变。
2. `SteerParams` 增加可选、非空 `submission_id`，并原样传给 `AgentRuntime.steer_task()`。
3. 保持 `session.steer` 的 `accepted`、`already_finished`、`not_found` 等结构化结果；
   `SubmissionConflictError` 继续映射为现有 `submission_conflict` 业务错误。
4. 增加回归断言：Gateway 不实现“steer 失败自动 submit”的复合操作，也不缓存幂等结果。
5. 不增加队列、intent、route 或客户端状态字段。

### 定向测试

- 带 `submission_id` 的 steer 精确透传文本、资源、display text 和 ID。
- 省略 `submission_id` 的旧客户端仍可调用。
- 空 ID 被参数校验拒绝。
- AgentRuntime 的 submission conflict 保持既有业务错误码。

### 验证命令

```bash
apps/gateway/.venv/bin/python -m pytest \
  apps/gateway/test/test_methods.py -q
```

## 阶段五：同步 Gateway 事实文档并全量验证

### 更新文件

- `README.md`
- `apps/gateway/README.md`
- `apps/gateway/docs/spec/2026-08-29-agent-gateway-positioning/arch.md`

### 文档内容

- 在 Gateway 公共 RuntimeUpdate 能力中列出 thinking 实时/历史通路和 Tool 安全预览。
- 明确 Gateway 不聚合 thinking、不读取 Agent 本地结果文件。
- 根 README 只描述用户可见的最终能力；与 TUI 计划共改同一段时在一个最终文档提交中合并，避免
  不同应用提交互相覆盖。
- 不把 RuntimeUpdate 新类型宣传成新的 RPC 方法。

### 验证命令

```bash
make test-gateway
git diff --check
```

跨应用全部完成后执行：

```bash
make test
```

## 建议提交边界

实际获得提交授权后，Gateway 部分建议作为一个逻辑提交：

```text
feat(gateway): expose thinking and tool preview updates
```

提交包含 steer 参数透传、RuntimeUpdate 协议回归测试，不混入 TUI 渲染代码。

## 完成标准

- 新 thinking update 通过现有实时订阅和历史 RPC 传输。
- 新增 payload 通过真实 Domain/Wire 用例固定，开放字符串 type 兼容性保留。
- Tool preview JSON 类型、截断和完整结果可用性在 Wire 层不丢失。
- Gateway 不聚合 delta、不接收本地结果路径、不改变 submit/steer 的单一操作语义。
- `make test-gateway` 与 `git diff --check` 通过。
