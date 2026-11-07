# TUI 联网引用展示架构

2026-11-06，依据当前 projector、ConversationProjection、message_cards 和对应回放测试整理。

`web_search.updated` 经既有 task/session 过滤后交由 `WebSearchProjector`，只接受已知状态、正整数 citeIndex 及有效 HTTP(S) 来源，输出终端本地 `UpdateWebSources`。`ConversationProjection` 保存每个 task 的编号集合，更新已有助手展示单元并将其带入历史窗口快照；重置会话时一起清除。无需更改 Gateway 或模型提示词。

`_StreamingMessage` 保留原始 Markdown，并通过 parser_factory 为 Textual 的 Markdown 渲染器提供当前来源。inline rule 在 Markdown 链接解析之后隐藏已知 `[n]`，代码、转义、链接/图片及美元公式保留；无来源编号仍作为普通文本显示。流式尾部仅抑制已知编号的有限前缀，结束时释放未完成内容。元数据晚到时停止当前流并从原文重渲染，后续 delta 继续接收；已完成消息也可更新，不追加重复正文。

这一路径只影响终端展示和渲染选区复制，原始事件、Store 正文及调试 transcript 回放保持原文。WebUI 继续从同一来源事件构造可点击引用。验证和限制见 [实施计划](plan.md)。
