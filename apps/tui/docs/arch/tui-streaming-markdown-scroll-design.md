# TUI Streaming Markdown and Scroll Design｜TUI 流式 Markdown 与滚动设计

## 文档定位

本文定义 Icarus Textual TUI 的流式 Markdown 性能优化和输出期间历史浏览修复。变更范围严格
限制在 `apps/tui`：不修改 Agent、Gateway、公共协议、Event 类型或上游 delta 粒度。

本文细化并修正以下现有设计：

- `tui-first-interaction-experience-design.md` 的 anchor 跟随策略；
- `tui-persistent-input-queue-design.md` 的输出期间可编辑要求；
- `textual-tui-development-plan.md` 中使用 Textual `Markdown` / `MarkdownStream` 的既定方向。

## 问题与根因

### 每个 delta 都重新渲染完整 Markdown

当前 `AssistantMessage.append_delta()` 将新 delta 追加到 `_markdown_parts`，随后对全部片段执行
`join`，再调用 `Markdown.update(完整文本)`。Textual 8.2.8 的 `Markdown.update()` 会重新解析
完整文档、移除原有 Markdown block，并挂载重新生成的 block。

因此长度持续增长的回答会重复处理已经稳定的前缀：

```text
delta 1 → parse(content[0:1])
delta 2 → parse(content[0:2])
...
delta n → parse(content[0:n])
```

除了累计开销接近平方增长，反复替换 Markdown 子树还会持续触发布局重算，并增加鼠标选择命中
已卸载旧节点的机会。

### 输出期间无法用滚轮稳定浏览历史

现有实现已经从“每次 UiAction 强制 `scroll_end()`”迁移到 Textual anchor，但仍有两个缺口：

1. `ConversationView._on_mouse_scroll_up/down()` 在 Conversation 未持有焦点时主动停止事件。正常
   聊天时焦点留在 Composer，因此鼠标位于 Conversation 上也无法滚动。
2. 现有回归测试直接调用 `page_up()`，没有经过真实指针事件；随后追加的 `new delta` 不改变内容
   高度，也没有覆盖长 Markdown 持续增长和 ScrollBar 拖动期间的布局变化。

Textual anchor 本身支持正确语义：用户滚动会 `release_anchor()`，滚到底部会重新激活；ScrollBar
开始拖动时也会主动释放 anchor。问题来自 TUI 的焦点门禁和缺失的真实交互覆盖，而不是需要另造
一套滚动容器。

## 方案选择

| 方案 | 优点 | 问题 | 结论 |
| --- | --- | --- | --- |
| 保留 `update()`，对刷新做时间节流 | 改动较小 | 仍重复解析完整回答，只降低发生频率；结束时仍需全量刷新 | 不采用 |
| 使用 Textual `MarkdownStream` 增量追加 | 使用框架原生增量解析；自动合并积压 fragment；只重算尚未稳定的尾部 | 需要管理 stream 创建、flush 和关闭生命周期 | 采用 |
| 自行解析 Markdown block 并局部更新 | 可完全控制性能和 block 稳定性 | 重复实现 Textual 已有解析逻辑，兼容成本高 | 不采用 |

滚动继续采用 Textual 原生 anchor / release-anchor，不引入平行的布尔状态机或自定义 ScrollBar。

## 流式 Markdown 设计

### 数据流

```text
RuntimeUpdate(type=assistant.text_delta)
→ Projector 生成 AppendAssistantDelta
→ ConversationView 定位当前 AssistantMessage
→ AssistantMessage 把原始 delta 写入 MarkdownStream
→ Markdown.append() 只解析未稳定尾部并更新必要 block
```

Projector、UiAction 和 Conversation 分段规则保持不变。工具开始、错误或 Turn 结束仍会结束当前
AssistantMessage；工具之后到达的新文本仍创建新的 AssistantMessage。

### AssistantMessage 生命周期

每个 AssistantMessage 拥有一个 `StreamingMarkdown` 和至多一个 `MarkdownStream`：

1. Widget 完成挂载后，从内部 Markdown widget 创建 stream；
2. `append_delta(text)` 继续把原始片段保存到 `_markdown_parts`，以支持 transcript、测试和状态检查；
3. 同一个 `text` 直接写入 stream，不拼接完整历史，不调用全量 `update()`；
4. `finish()` 停止 stream，并等待所有 pending fragment 刷入 Markdown；
5. `finish()` 保持幂等；关闭后的 message 拒绝新 delta；
6. Session reset、切换、Tool 边界、Error 边界和任务终态都必须先完成当前 stream，再移除或切换
   Widget。

`MarkdownStream.write()` 可以在渲染速度低于 token 到达速度时合并 pending fragment。
`Markdown.append()` 会保留已稳定 block，只重新解析从最后一个未稳定 block 开始的尾部，因此不会
对每个 token 重建整篇文档。

### 完整性与失败处理

- `markdown_text` 必须始终等于所有接收 delta 的原始拼接，不能因 UI 节流丢字；
- `finish()` 返回前，UI 中 Markdown source 必须与 `markdown_text` 一致；
- Markdown stream 的异常沿现有 `ConversationView action` 错误边界进入 TUI fatal 状态，不静默
  丢失内容；
- 不在 TUI 内修改 delta、猜测 Markdown 语法边界或自行补齐未闭合代码块；
- 保留历史恢复行为：历史 delta 仍按原顺序投影，最后通过相同的 finish 路径完成刷新。

## 滚动与自动跟随设计

### 用户可见状态

```text
FOLLOWING
  新内容增长              → 继续停留在实时底部
  PageUp / 向上滚轮       → 释放 anchor，进入 DETACHED
  向上拖动 ScrollBar      → 释放 anchor，进入 DETACHED

DETACHED
  新 delta 或布局增长     → 保持当前 scroll_y，不跳到底部
  向下滚轮直至底部        → 恢复 anchor，进入 FOLLOWING
  ScrollBar 拖回底部      → 恢复 anchor，进入 FOLLOWING
  Ctrl+End                → 滚到底部并恢复 anchor
```

Composer 的焦点、草稿、Selection 和 Cursor 不因 Conversation 的指针滚动而变化。

### 指针与焦点规则

鼠标滚轮按指针所在区域路由，不以键盘焦点作为准入条件：

- 指针位于 Conversation：由 Conversation 滚动，即使 Composer 仍有焦点；
- 指针位于 Composer：保留 Composer/TextArea 自身行为，不全局转发到 Conversation；
- 拖动 Conversation 的垂直 ScrollBar：使用 Textual 原生 capture、release-anchor 和
  `ScrollTo`；
- PageUp、PageDown 与 Ctrl+End 继续是 App 级快捷键，可在 Composer 聚焦时控制 Conversation；
- Conversation 自己获得焦点时，方向键继续按行滚动。

实现上移除 Conversation 对 `has_focus` 的鼠标滚轮门禁，允许 Textual 按命中区域自然冒泡。
不改变 Composer 焦点，也不新增全局 MouseScroll 转发。

### Anchor 生命周期

Conversation 在内容首次超过 viewport 后启用 anchor。用户滚动调用 Textual 的
`release_anchor()`，后续 Markdown 尾部增长只增加 `max_scroll_y`，不改变当前阅读位置。到达底部时
使用 Textual `_check_anchor()` 语义恢复跟随；`Ctrl+End` 通过现有 `resume_follow()` 显式恢复。

不依赖 `_anchored` 单独判断是否正在跟随，因为 Textual 通过 `_anchor_released` 区分“anchor 已配置”
和“用户已暂时释放”。生产代码优先调用公开滚动方法；测试只在无法通过公开可观察状态区分时
检查内部标志。

## 测试设计

### 增量渲染

- 多个 delta 逐个写入时，Markdown 不在初次挂载后调用全量 `update()`；
- 传给增量接口的内容是本次 fragment，而不是截至当前的完整文本；
- 已稳定的前部 MarkdownBlock 在后续 delta 到达后保持挂载；
- 跨 delta 的段落、列表、粗体和代码块最终 source 与渲染结构正确；
- Tool / Error / Finish 边界会 flush pending fragment，且重复 finish 安全；
- Session 历史恢复和 reset 不遗留后台 stream。

性能验证以结构断言为主，不使用容易受机器负载影响的毫秒阈值。可以记录长流测试耗时作为
诊断数据，但不把绝对时间作为回归门槛。

### 滚动交互

- Composer 聚焦时，在 Conversation 坐标发送真实 MouseScrollUp，能够上滚且 Composer 状态不变；
- 上滚后持续追加足以增加多行高度的 Markdown，`scroll_y` 保持不变；
- 拖动垂直 ScrollBar 时释放 anchor，流式内容增长不会把滑块拉回底部；
- 滚轮或 ScrollBar 回到底部后，新 delta 继续跟随；
- `Ctrl+End` 恢复跟随且不改变 Composer 草稿、Selection、Cursor 或焦点；
- resize 和窄终端下保持相同语义。

### 验证顺序

1. `apps/tui/test/widgets/test_conversation.py` 的定向行为测试；
2. synthetic events 的完整 Textual shell replay；
3. TUI snapshot，并直接检查长流、DETACHED 和恢复跟随画面；
4. `make test-tui`；
5. `git diff --check`。

## 范围边界

本次只允许修改：

- `apps/tui/src/` 内的 Markdown message 与 Conversation 滚动实现；
- `apps/tui/test/` 内的行为、回放和 snapshot 覆盖；
- `apps/tui/docs/` 内的设计和实施记录。

本次不修改：

- `apps/agent`、`apps/gateway` 或 `packages/gateway_protocol`；
- AgentPlugin 的原始流发布方式；
- EventBus、Blackboard、Hook 或 Plugin Runtime；
- Session 持久化格式；
- 未读消息计数、跳到底部按钮或虚拟列表；
- `feature` 分支。

## 验收标准

- 流式回答不再对每个 delta 调用完整 Markdown `update()`；
- 最终 Markdown 与全部原始 delta 严格一致；
- Composer 聚焦时可以在 Conversation 区域使用滚轮和 ScrollBar 浏览历史；
- 用户离开底部后，持续输出不会改变当前阅读位置；
- 用户回到底部或按 `Ctrl+End` 后恢复实时跟随；
- 工具分段、错误、历史恢复、Session 切换、文本选择和现有视觉样式不回归；
- 所有代码和测试改动均位于 `apps/tui`。
