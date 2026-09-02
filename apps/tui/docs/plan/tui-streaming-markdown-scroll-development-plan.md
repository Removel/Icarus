# TUI Streaming Markdown and Scroll Development Plan｜TUI 流式 Markdown 与滚动实施计划

> 实施状态：已完成。TUI 全量 `161 passed`，12 个 snapshot 通过；无模型 transcript 已验证，
> 完整 Textual shell 已实际启动、提交并退出，事件链由确定性测试与 snapshot 覆盖；流式 Markdown
> 与 detached 滚动快照已人工检查。

## 目标与依据

依据 `apps/tui/docs/arch/tui-streaming-markdown-scroll-design.md`，完成两个已经定位的问题：

1. Assistant 流式输出不再对每个 delta 拼接并全量 `Markdown.update()`，改用 Textual 官方
   `Markdown.get_stream()` 增量追加；
2. Composer 保持焦点时，用户仍能在 Conversation 区域通过鼠标滚轮或右侧 ScrollBar 浏览历史，
   并在离开底部后暂停自动跟随。

本计划只修改 `apps/tui/`。不修改 `apps/agent/`、`apps/gateway/`、
`packages/gateway_protocol/`、Event 数据格式或上游 delta 粒度，也不更新 `feature` 分支。所有开发
留在当前 `feat/tui` 分支。

## 已确认的根因

- `AssistantMessage.append_delta()` 当前对 `_markdown_parts` 全量 `join`，再调用
  `Markdown.update(完整文本)`；Textual 会重新解析完整 Markdown 并替换已有 block。
- `ConversationView._on_mouse_scroll_up/down()` 以 `has_focus` 为门禁；正常聊天时 Composer 持有
  焦点，因此指针即使位于 Conversation，滚轮事件也会被主动停止。
- 原有滚动回归直接调用 `page_up()`，没有经过真实鼠标事件；后续 delta 又不足以增加布局高度，
  所以没有覆盖用户实际遇到的场景。

## 实施原则

- 每项先补最小失败测试，再修改实现；
- 使用 Textual 8.2.8 的公开 `Markdown.get_stream()` 入口，不直接复制内部解析算法；
- 保留现有 RuntimeUpdate → Projector → UiAction → ConversationView 分层；
- 保留每个 Assistant 段在 Tool、Error、Turn 终态处结束的语义；
- 自动跟随继续使用 Textual anchor / release-anchor，不新增重复滚动状态机；
- 测试必须覆盖真实内容高度增长和真实指针路由，不能只调用内部便捷方法；
- 时间数据只作为诊断信息，不设置容易受机器负载影响的毫秒级 CI 门槛；
- 未经用户另行要求，不创建 commit、不 push，也不更新 `feature`。

## 阶段一：建立失败回归和渲染基线

**更新文件**

- `apps/tui/test/widgets/test_conversation.py`
- `apps/tui/test/test_app.py`

**新增 Markdown 回归**

- Markdown 初次挂载完成后，将实例的全量 `update()` 替换为会立即失败的 stub；连续发送多个
  `AppendAssistantDelta`，证明当前实现会错误触发全量更新；
- 记录每次传入 stream 的 fragment，断言收到的是本次 delta，而不是截至当前的完整回答；
- 使用跨 delta 的标题、粗体、列表和 fenced code block，断言 `markdown_text` 与 Markdown
  widget 的最终 source 都等于原始片段拼接；
- 保存一个已经稳定的前部 MarkdownBlock 身份，继续追加新段落后断言该 block 没有被整体替换；
- 在 Tool start、Error、FinishTurn 和 reset 前后断言 pending fragment 已完成刷新，且不存在仍运行的
  Markdown stream；
- 保留并改写旧的 stale selection 回归：只允许已卸载的旧 block 禁用选择，仍挂载的当前 block
  必须保持可选择。

**新增滚动回归**

- 在完整 `IcarusTextualApp` 中保持 Composer focus、草稿、Selection 和 Cursor；通过
  Conversation 内的屏幕坐标分发 `MouseScrollUp`，断言 Conversation 实际上滚；
- 进入 detached 后连续追加多段足以增高内容的 Markdown，等待布局稳定，再断言 `scroll_y` 未被拉回
  底部且 `max_scroll_y` 已增长；
- 通过 ScrollBar 的 mouse down、move、mouse up 路径拖动滑块，断言 anchor 被释放，并在流式增长时
  保持阅读位置；
- 将滑块或滚轮移回底部后追加新内容，断言重新跟随；
- 保留 `Ctrl+End` 恢复跟随测试，并把后续 delta 改成会实际增加多行高度的内容。

如果 Textual Headless Driver 没有公开滚轮辅助方法，测试通过 Screen 的事件路由发送真实
`MouseScrollUp` / `MouseScrollDown`，坐标必须命中 Conversation；不能直接调用 `page_up()` 代替
这条回归。ScrollBar 拖动优先使用 Pilot 的 `mouse_down`、`hover` 和 `mouse_up`，以覆盖 capture 与
release-anchor 行为。

**检查点**

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/widgets/test_conversation.py \
  apps/tui/test/test_app.py -q
```

预期：新回归在修改实现前按对应根因失败；既有测试保持通过。

## 阶段二：把 AssistantMessage 切换到增量 MarkdownStream

**更新文件**

- `apps/tui/src/widgets/messages.py`
- `apps/tui/test/widgets/test_conversation.py`

**开发内容**

- `AssistantMessage` 继续维护 `_markdown_parts`，保证 `markdown_text` 是完整且无损的原始文本；
- AssistantMessage 挂载后或第一次非空 delta 到达时，通过内部 `StreamingMarkdown` 调用
  `Markdown.get_stream()`，每个 message 最多创建一个 stream；
- `append_delta(text)` 只执行两件事：保存原始 fragment，并调用 `stream.write(text)`；
- 禁止在流式路径执行 `self.markdown_text` 的全量 `join` 后再调用 `update()`；
- `finish()` 先把 message 标记为关闭，再 `await stream.stop()`，保证积压 fragment 在返回前全部写入；
- `finish()` 重复调用必须安全，不重复 stop，也不丢失内容；
- Widget 非正常卸载时增加防御性收口，避免 Session reset、切换或 App 退出留下 Markdown stream
  后台 task；正常路径仍由 ConversationView 在移除 Widget 前显式 await `finish()`；
- 不从 `textual.widgets` 导入未导出的 `MarkdownStream` 类型；生产代码只依赖公开
  `Markdown.get_stream()` 返回对象及其 `write/stop` 契约。

**适配文本选择保护**

现有 `StreamingMarkdown.update()` 会在全量替换前禁用全部旧 child 的选择能力。切换到
`append()` 后，需要把保护收窄到“确实已经卸载的旧 block”：

- 增量追加前记录可能被尾部重解析影响的旧 block；
- append 完成后，仅对 `parent is None` 的旧 block 设置不可选择；
- 仍挂载或被原地更新的 block 保持可选择；
- 不为此恢复全量更新，也不禁用整个 Markdown 的文本选择。

**单元测试**

- 连续 fragment 只走 stream.write；
- stream 积压时可合并刷新，最终 source 不丢字、不重复；
- 已稳定前部 block 身份保持；
- 尾部 block 类型变化时，卸载旧节点不会触发鼠标选择异常；
- 当前可见 block 仍允许文本选择；
- finish、重复 finish、reset 和卸载均收口 stream。

**检查点**

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/widgets/test_conversation.py -q
```

## 阶段三：修复滚轮焦点门禁和自动跟随

**更新文件**

- `apps/tui/src/widgets/conversation.py`
- `apps/tui/test/widgets/test_conversation.py`
- `apps/tui/test/test_app.py`

**开发内容**

- 删除 Conversation 对 `has_focus` 的 MouseScrollUp / MouseScrollDown 门禁；如果覆写方法不再
  承担额外职责，则直接删除覆写并使用 VerticalScroll 原生行为；
- 不改变 Composer focus，不在 App 添加全局滚轮转发；鼠标事件继续按指针命中的 Widget 路由；
- 向上滚轮、PageUp 和 ScrollBar 开始拖动时依赖 Textual 原生 `release_anchor()`；
- 向下滚动或 ScrollBar 释放并到达底部时依赖 Textual `_check_anchor()` 恢复；
- 保留 `resume_follow()` 作为 `Ctrl+End` 和历史恢复完成时的显式“回到实时输出”入口；
- `_anchor_pending` 只处理内容首次从不溢出变为溢出的情况，不把它扩展成第二套 FOLLOWING /
  DETACHED 状态；
- MarkdownStream 导致内容异步增高时，不调用额外 `scroll_end()`；FOLLOWING 由 anchor 处理，
  DETACHED 保持原 `scroll_y`。

**测试内容**

- Composer 聚焦时，Conversation 区域滚轮可用；
- Composer 区域滚轮不被全局转发；
- 鼠标滚动不改变 Composer 的 focus、text、Selection 和 Cursor；
- PageUp、PageDown、方向键和 Ctrl+End 的既有键盘语义不回归；
- detached 状态下至少三次真实高度增长均保持阅读位置；
- ScrollBar 被抓取期间以及释放到非底部后，新内容不会抢回底部；
- ScrollBar 回到底部或 Ctrl+End 后，新内容继续跟随；
- resize 前后不意外恢复或丢失跟随状态。

**检查点**

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/widgets/test_conversation.py \
  apps/tui/test/test_app.py -q
```

## 阶段四：确定性长流回放和视觉验证

**按需更新文件**

- `apps/tui/test/fixtures/synthetic_tui_events.jsonl`
- `apps/tui/test/test_replay.py`
- `apps/tui/test/test_app_snapshots.py`
- `apps/tui/test/__snapshots__/test_app_snapshots/*.svg`

如果现有 fixture 不足以产生稳定的多屏流式内容，则增加一个 TUI 自有长流 fixture，包含：

- 多个细粒度 `assistant.text_delta`；
- 跨 delta 的 Markdown 列表和 fenced code block；
- 足以让 Conversation 多次增长的内容；
- 完整 `task.finished` 终态。

**确定性验证**

- transcript 仍保持最终语义内容和事件顺序；
- `--tui-real` 回放时可以在输出过程中上滚、拖动滑块并回到底部；
- 增加或更新一个 detached 长流 snapshot，固定显示历史位置而不是最新尾部；
- 现有 streaming Markdown、Tool success/failure、Session Picker 与窄屏 snapshot 不发生非预期
  变化。

**自动化命令**

```bash
apps/tui/.venv/bin/python -m pytest \
  apps/tui/test/test_replay.py \
  apps/tui/test/test_timeline_transcript_golden.py \
  apps/tui/test/test_app_snapshots.py \
  --snapshot-report /private/tmp/icarus-tui-stream-scroll-report.html -q

apps/tui/.venv/bin/python apps/tui/scripts/replay_events.py \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl

apps/tui/.venv/bin/python apps/tui/scripts/replay_events.py \
  --tui-real --speed 20 \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
```

**强制视觉审查**

1. 打开 snapshot report，先关闭 difference overlay；
2. 直接检查 streaming、detached、恢复底部和窄屏状态；
3. 如果查看器不能直接显示 SVG，用浏览器引擎渲染为 PNG 后检查；
4. 只有确认视觉变化符合预期时，才使用 `--snapshot-update` 更新基线；
5. 不以 pytest 通过代替人工查看真实 snapshot。

## 阶段五：同步 TUI 文档并完成回归

**更新文件**

- `apps/tui/README.md`
- `apps/tui/docs/arch/tui-first-interaction-experience-design.md`
- `apps/tui/docs/arch/tui-streaming-markdown-scroll-design.md`
- `apps/tui/docs/plan/tui-streaming-markdown-scroll-development-plan.md`

**文档内容**

- 将 README 中“Composer 聚焦时滚轮不控制 Conversation”改为按指针区域路由；
- 在旧首次交互设计中标明本文替代其 MouseScroll 焦点规则；
- 记录最终 MarkdownStream 生命周期和实际测试结果；
- 若实现与计划存在差异，以当前代码和测试为准修正文档，不保留已经失效的描述；
- 不修改根 README、Agent/Gateway 文档或跨应用 spec。

**完整 TUI 验证**

```bash
make test-tui
git diff --check
git status --short --branch
```

`make test-tui` 已包含 TUI 全量 pytest 和 `apps/tui/src`、`apps/tui/test`、`packages` 的
compileall。由于实现严格限制在 `apps/tui`，本次不要求修改或运行 Agent/Gateway 测试；如果最终
diff 意外越界，必须先撤回越界改动，而不是扩大验证范围。

## 建议的逻辑提交

只有用户后续明确要求提交时才执行，且不 push 到 `feature`：

1. `docs(tui): design incremental markdown and scroll behavior`
2. `fix(tui): stream markdown updates incrementally`
3. `fix(tui): preserve scroll position during streaming`
4. `docs(tui): document streaming and scroll behavior`

实现和对应测试应放在同一个 commit；如果 Markdown 与滚动修改必须共同通过同一组行为测试，
可以合并第 2、3 个提交，但不得混入其他应用变更。

## 完成标准

- 每个 assistant delta 不再触发完整 `Markdown.update()`；
- 最终 `markdown_text` 和 Markdown source 与全部原始 delta 严格一致；
- Tool、Error、FinishTurn、reset、Session 切换和 App 退出不会遗留 stream task；
- Composer 聚焦时，可以在 Conversation 区域使用鼠标滚轮和 ScrollBar 浏览历史；
- 用户离开底部后，持续流式增长不改变阅读位置；
- 回到底部或按 `Ctrl+End` 后恢复实时跟随；
- 文本选择、键盘滚动、历史恢复、Session Picker 和现有视觉样式不回归；
- `make test-tui`、compileall、snapshot 审查与 `git diff --check` 全部通过；
- 最终 diff 只包含 `apps/tui/`。

## 实施结果

- `AssistantMessage` 已使用 `Markdown.get_stream()`，每次只写入新 fragment；Tool、Error、Turn
  终态和 Session reset 通过幂等 `finish()` flush 并停止 stream；
- `StreamingMarkdown` 只在增量解析可能替换尾部 block 的短窗口内禁用旧节点选择，稳定的前部
  block 保持挂载和可选择；
- Markdown append 完成后通知 Conversation 重新检查首次溢出，避免异步布局导致 anchor 未启用；
- Conversation 移除了 MouseScrollUp 的焦点门禁；MouseScrollDown 保留到底部后的 follow 恢复；
- 新增回归覆盖 fragment 写入、稳定 block、stream 清理、Composer 聚焦下真实滚轮、Composer 区域
  隔离、ScrollBar thumb 拖动、detached 状态持续增长和滚回底部恢复跟随；
- 新增 detached 长流 snapshot，并直接确认历史位置保持、滑块位于中段且 Composer 仍聚焦；
- 最终验证：`make test-tui` 为 `161 passed`，12 个 snapshot 通过；`git diff --check` 通过。
