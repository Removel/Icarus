# 跨消息选区 Spike 结论（Task 1 / design §5.1）

- 日期：2026-10-28。
- 测试：`apps/tui/test/spikes/test_selection_spike.py`（Textual 8.2.8，Pilot 转发 MouseDown/Move/Up 至 Screen，`Screen.get_selected_text()` 提取）。
- 环境：WSL2 (Linux 6.6)，headless Pilot，`apps/tui/.venv`。
- 性质：可丢弃验证；按 design §5.1，本文件只记录实测行为，不声称生产功能完成。

## 逐场景结果

| # | 场景 | 结果 | 实际行为 |
| --- | --- | --- | --- |
| 1 | 跨用户/助手消息拖选 | FAIL | MouseUp 后 `Screen.selections` 为空、`get_selected_text()` 返回 `""`；拖选过程中虽能跨 widget 建立选区，但释放即失效。拖选期间提取文本包含角色装饰（` You`、`✦ Icarus` 标签行被一并选中）。 |
| 2 | CJK+emoji 逐字符提取 | PASS | 选区内含「中文😀测试ABC」时提取无 U+FFFD，宽字符按字符而非 cell 切断（`StreamingMarkdown` 的 `_CJKMarkdownParagraph` + Content 机制已保证）。 |
| 3 | 流式 append 后选区保留 | PASS | 建立选区后 `AppendAssistantDelta` 追加，选区文本不变；`StreamingMarkdown.append` 对旧尾块关闭 `ALLOW_SELECT`、完成后恢复，未冲掉既有选区。 |
| 4 | Markdown 末块替换后提取 | PASS | `CompleteAssistantMessage` 替换尾块后，对同一区域重新提取非空（返回替换后文本），无崩溃、无失效引用异常。 |
| 5 | `page_up` 跨 24 轮窗口切换后选区存活 | FAIL | 切窗 `_render_window` 卸载并重挂所有消息 widget，原生选区持有旧 widget 引用；切窗后 `get_selected_text()` 返回 `""`。选区起点（`question 6`）文本完全丢失。 |
| 6 | 从链接 widget 起拖不触发打开 | PASS | 在 Markdown 链接上 MouseDown 后拖离 MouseUp，未触发 `LinkClicked` / `open_url`（拖选优先于单击，Textual 原生仲裁已正确）。 |

复现：`apps/tui/.venv/bin/python -m pytest apps/tui/test/spikes/test_selection_spike.py -q`
当前输出：`2 failed, 4 passed`（场景 1、5 失败，即跨消息拖选与窗口切换）。

## 分叉结论（Task 4 的输入）

**结论 B：原生 Screen 选区不足，需稳定端点。**

原生选区在「跨消息拖选后提取」与「跨 24 轮窗口切换后存活」两个场景均不达标：

1. 需要的稳定端点映射：`<(turn_index, unit_key), start/end 文字偏移>`——
   - 消息身份沿用 `ConversationView._unit_key(unit, ordinal)`（task_id/kind/step/call_id/segment/ordinal）；
   - 文字偏移相对各显示单元的挂载文本（Static 的 `message_text` / Markdown 段落纯文本），鼠标事件在 MouseDown/Move 时换算；
   - 提取时按语义端点从当前挂载文本（切窗后经 `ConversationProjection` 快照重挂）重新解析，选区起点在窗口外时按 design §5.2 记录文字片段快照以便提示（不建全文 Store）。
2. 另需独立修复的两点（Task 4 范围）：
   - MouseUp 即丢失选区：原生 MouseUp 后 `selections` 清空，提取必须在拖选期间持续维护语义快照，不依赖 MouseUp 后的原生状态；
   - 装饰过滤：提取必须按 widget 类别排除 `message-label`（`❯ You` / `✦ Icarus`）等界面元素，只取 `message-content` / 正文段落。
3. 场景 2/3/4/6 说明底层文本提取（CJK、流式、替换、链接仲裁）可直接复用原生 Content/Selection 机制，无需重做渲染。

风险记录：本 spike 在 headless Pilot 下验证；真实终端的鼠标事件路由一致性在 Task 8 实机验收复核。
