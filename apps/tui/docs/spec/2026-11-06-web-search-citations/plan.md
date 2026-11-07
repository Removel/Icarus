# TUI 联网引用展示实施计划

## 目标与基线

日期：2026-11-06。状态：已实施；聚焦与离线 App 回放通过，最终全量 535 passed。

基于 PR #17 的 284bcc5ec4decb74a95d6d5a1aa13b09b48c82be，统一修复分支及 PR 安排见 [实施入口](../../../../webui/docs/spec/2026-11-06-chat-reliability/plan.md)。实现按仓库 textual-tui 技能的 App/Screen/Widget/Service/Store 边界及回放验证流程执行。

联网工具在定义及返回 notice 中统一要求实际来源使用 [n]。WebUI 使用来源元数据把编号转为可点击引用；TUI 的 AgentProjector 原样传递 assistant 文本，默认 ProjectorRegistry 没有注册 web_search.updated，因此终端显示编号而没有对应来源交互。

目标：终端正文隐藏已确认的联网来源标记；Gateway 事件、服务端持久化、WebUI 引用和模型行为保持现有契约。不要按客户端类型修改 System Prompt 或让 AgentPlugin 重写原始执行流。

## 文件与接口

- src/event_pipeline/dispatcher.py、actions.py：注册既有 web_search.updated 的终端投影，使用显式的本地展示动作。
- src/event_pipeline/projectors/：新增 web_search.py，按任务验证并提取 citeIndex；不从 tool.completed.output_preview 猜来源。
- src/stores/conversation.py：拥有任务来源事实，支撑实时与历史回放、迟到元数据和会话切换隔离。
- src/widgets/chat/message_cards.py、conversation.py 及合适的纯展示辅助模块：以原文和已知来源生成显示文本；必要时调整 streaming markdown 边界。
- test/event_pipeline/projectors/、test/event_pipeline/test_dispatcher.py、test/stores/test_conversation.py、test/widgets/chat/test_conversation.py；按实际实现新增纯文本/Markdown 展示测试。
- 参照 apps/agent/src/agent_app/plugins/runtime_update/plugin.py 的 web_search.updated 和 WebUI 的 webSearch.ts、remarkCitations.ts，不另造服务端来源协议。

## Task T1：按任务维护来源事实

- [x] 补来源事件投影与 registry 测试，验证有效 citeIndex、重复编号、损坏 payload 和无关 task 的边界。
- [x] 将编号集合保持在无 Textual 依赖的 Store/展示状态，按 task 隔离；重置、切会话和历史回放不残留前一任务的来源。
- [x] 显示顺序与原有 sequence/历史处理一致；元数据先到、后到或重复到都得到相同最终显示，不修改服务端原始事件。
- [x] 保留原始 assistant 文本供回放和最终消息校正使用，过滤后的文本不是第二份业务 transcript。

## Task T2：隐藏引用并处理流式边界

- [x] 只隐藏当前 task 的有效来源引用，跟随 WebUI 已有引用语法；没有来源元数据的普通 [1] 等文本不做全局删除。
- [x] 保留代码围栏、行内代码、转义括号、Markdown 链接、图片与正常序号；先补这些边界的回归，再选择复用现有 Markdown 解析能力或小型语法处理。
- [x] 覆盖“[”“1”“]”分散在多个 delta、连续多引用、末尾不完整括号以及最终 assistant.message 覆盖此前增量的情况。
- [x] 流式期间对疑似引用尾部采取有界处理，最终闭合引用不反复闪现；结束时未闭合的普通文本必须释放显示，不能吞掉内容。
- [x] 来源晚到时只重新推导显示；更正已有显示时不重复追加正文，不破坏文本选择和当前阅读位置。
- [x] 最终正文、历史恢复及终端复制/导出的用户可见正文保持一致；调试原始事件仍保留原文。
- [x] 错误、取消和无来源联网结果走既有结束流程；不新增终端内的来源面板，也不改变现有 TUI 思考折叠策略。

## 验收

~~~bash
apps/tui/.venv/bin/python -m pytest apps/tui/test/event_pipeline apps/tui/test/stores/test_conversation.py apps/tui/test/widgets/chat/test_conversation.py -q
make test-tui
apps/tui/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test packages
git diff --check
~~~

- [x] 补充联网来源、跨 delta 引用、代码内数字括号和元数据迟到的离线事件夹具，用真实 Textual shell 回放。
- [x] 查看 120x40、70x24 和 100x12 下的临时渲染结果，确认隐藏引用不引入断行、闪烁或滚动回跳；不恢复已删除的旧 SVG 基线测试。
- [x] 复跑 WebUI 联网引用相关回归，确认同一原始事件在网页仍可查看来源。
- [x] 本任务不调用真实搜索或模型，不写用户 Session；记录实际测试与未执行项。

回滚终端投影和显示逻辑即可，服务端原文与来源仍完整，现有数据库无需转换。


## 实施记录

2026-11-06：注册 `WebSearchProjector`，校验来源并投影 `UpdateWebSources`；Store 按 task 保留编号并在历史快照中携带，原始正文保持完整。助手消息的 Markdown inline rule 在展示时隐藏已知引用，代码、转义、链接、图片、美元公式和无来源普通括号保留。跨 delta 的候选尾部仅暂存已知编号前缀，结束释放；迟到来源重新推导已挂载消息，包括已完成消息。

终端选区复制使用渲染后的文字；`tools/transcript.py` 的调试语义回放保留原文并明确忽略来源展示动作，不将调试日志作为用户显示正文导出。

新增 `web_citations.jsonl` 与真实 App 离线回放，在 120x40、70x24、100x12 下检查输出和截图；验证最终展示隐藏来源 1、保留代码 `data[1]` 与无来源 `[9]`，原始正文不变。最终聚焦解析与 App 回放 6 passed；WebUI 联网来源回归随聊天套件运行，仍保留网页引用。没有调用真实搜索/模型或写入用户 Session。

最终 `make test-tui`：535 passed，退出 0；入口包含源码、测试及共享包 compileall。三种尺寸截图均已查看，截图仅存应用忽略目录；未新增 SVG 基线。
