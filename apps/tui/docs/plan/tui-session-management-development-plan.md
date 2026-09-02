# TUI Session Management Development Plan｜TUI Session 管理实施计划

## 目标

在保持默认启动和 `--session-id` 兼容行为的前提下，实现 `/resume`、Session Picker、统一 Session
激活和 `/clear`，并在正常路径清理空 Session。

设计依据：

- `spec/session-management.md`；
- `apps/tui/docs/arch/tui-session-management-design.md`。

## 实施顺序

### 阶段一：本地命令和空闲门禁

新增 `apps/tui/src/commands.py`：

- 定义 `resume`、`clear`；
- 只解析精确本地命令；
- 单元测试普通文本和未知斜杠输入。

更新 `ChatState`：

- 增加 Session Command 空闲判断；
- 增加 Switching 状态或明确操作标志；
- 验证 Pending、dispatch、active task 和失败状态不会执行命令。

更新 Composer 提交处理：

- 在 `_has_user_submission` 和 `enqueue` 前路由本地命令；
- 带附件命令恢复草稿；
- 命令不可用时提示且不排队。

### 阶段二：GatewayClient Session API

更新 RuntimeClient Protocol、Client Factory 和 GatewayClient：

- 增加扁平 `create_if_missing`；
- 增加 `list_sessions()`；
- 增加 `get_session_status()`；
- 增加 `discard_empty_session(session_id)`；
- 保持初始启动和 reconnect 行为；
- 更新 Fake Client 和 Replay Client。

定向测试自动创建、existing-only、列表、清理和当前调用参数。

### 阶段三：Session Picker

新增 `apps/tui/src/screens/session_picker.py` 和必要样式：

- 显示第一条用户输入与缩写 Session ID；
- 标记当前 Session；
- 键盘选择、Enter 和 Escape；
- 空列表；
- 宽屏和窄屏布局；
- 不在 Screen 内执行网络请求。

增加 Widget/Screen 单元测试和快照。

### 阶段四：统一 Session 准备

重构初始 `_start_runtime`：

- 使用参数化 Client Factory；
- 提取 `_prepare_session(session_id, create_if_missing)`；
- 启动、subscribe、history 和状态验证使用同一路径；
- 保持先订阅缓冲、后读取历史的顺序；
- `/resume` existing-only，`/clear` 允许创建。

增加 Candidate 准备失败的资源清理测试。

### 阶段五：Conversation 与 Session 状态重置

在 `ConversationView` 增加 `reset()`：

- 清空子 Widget、Assistant 和 Tool 引用；
- 恢复 Welcome、历史模式和自动跟随初始值。

在 App 增加 Session 激活提交：

- 停止旧 Event Worker；
- 重建 `ChatState`；
- 清空 `_last_sequence` 和 `_early_updates`；
- 重置 Session 级错误与用户输入标记；
- 切换当前 Client/Subscription；
- 在目标身份已成为当前投影后批量恢复目标历史；
- 启动目标 Event Worker；
- 关闭旧 Client；
- 丢弃旧空 Session。

在 RuntimeUpdate 入口增加当前 SessionIdentity 过滤，覆盖迟到旧事件。

### 阶段六：实现 `/resume`

- 空闲时读取 `session.list`；
- 打开 Picker 前和确认选择后分别查询当前 Session 权威状态；
- 打开 Picker；
- 取消时无副作用；
- 选择时准备 existing-only Candidate；
- 目标 Busy 或准备失败时保留当前界面；
- 选择当前 Session 也走完整准备与激活；
- 不支持搜索和筛选。

### 阶段七：实现 `/clear` 和退出清理

- 增加独立的 `_session_has_user_input`，只用公共 `user.message` 更新；
- 创建新 Session 前查询当前 Session 权威状态；
- 当前空 Session 的 `/clear` 不创建新 Session；
- 非空 Session 创建并激活新 Session；
- Candidate 在激活提交前失败时丢弃新空 Session；
- 切走旧空 Session 后请求安全丢弃；
- 正常退出时先停止事件投影，再通过仍连接的 Client 请求丢弃当前空 Session，失败不阻塞退出。

### 阶段八：端到端回归与文档同步

- 更新 TUI README 的命令和按键说明；
- 更新 `docs/todo/tui.md` 和产品路线图状态；
- 更新确定性 Replay/Fake Client；
- 验证无参数启动与 `--session-id`；
- 不实现 `/compact`、搜索、筛选和非空 Session 删除。

## 验证顺序

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/test_chat_state.py \
  apps/tui/test/gateway_client/test_client.py -q

apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/screens \
  apps/tui/test/widgets/test_conversation.py \
  apps/tui/test/test_app.py -q

apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/test_app_snapshots.py -q

make test-tui
git diff --check
```

跨应用完成后：

```bash
make test
```

真实闭环：

```text
icarus 创建 A
→ 在 A 完成一轮对话
→ /clear 创建 B
→ B 中完成一轮对话
→ /resume 恢复 A
→ 退出并重启 Gateway/TUI
→ /resume 再次恢复 A 和 B
```

额外验证目标 Busy、历史损坏、Gateway 断线、Picker 取消和空 Session 清理。

## 完成标准

- `/resume` 和 `/clear` 在空闲态完整可用；
- 当前 Session 忙时命令明确拒绝且不进入队列；
- Session 切换恢复完整历史并继续实时输出；
- 候选失败不改变当前界面；
- 空 Session 不长期积累；
- TUI 全量测试和视觉快照通过。
