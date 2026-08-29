# TUI Session History Restoration Development Plan｜TUI Session 历史恢复实施计划

## 目标

使 `icarus --session-id <id>` 在进入 Ready 前恢复该 Session 上次退出时的 Conversation 可见语义
状态，并与实时 RuntimeUpdate 无遗漏、无重复地衔接。

## 实施步骤

1. GatewayClient 启动时先 subscribe 并缓冲实时 Update，再调用 `session.get_history`。
2. 启动结果同时交付 history、history_cursor 和实时订阅。
3. 恢复期间隐藏 Conversation，在 Textual `batch_update` 中构建全部历史 Widget，结束后一次显示并
   定位到底部。
4. Projector 增加 `user.message` 和 interrupted 终态；历史模式跳过 active_task_id 过滤。
5. ConversationView 按 sequence 重放用户、助手、Tool、错误和终态，恢复未完成 Tool 为 interrupted。
6. 删除 submit 成功后的本地用户消息追加，统一等待公共 `user.message`。
7. 历史完成前保留 Composer 和 Pending Queue，但不向 Runtime 派发。
8. 重连按最后已应用 sequence 补齐 journal，再接续缓冲实时记录。

## 验证

- 多轮历史恢复后的 Widget 顺序和退出时一致；
- 部分回复、Tool 与 interrupted 终态正确；
- 历史/实时边界产生 Update 时不丢失、不重复；
- 发起提交的客户端和旁观客户端都只显示一次用户消息；
- 旧 Session 空历史正常启动；
- 草稿、Pending Queue、滚动和焦点不被误当成 Session 状态。

## 实施结果

- GatewayClient 已支持 history RPC；TUI 启动和重连均采用先订阅缓冲、再读取历史的交接顺序；
- 已支持恢复用户消息、助手分段、Tool、错误和 interrupted 终态；
- 实时用户消息改由公共 `user.message` 驱动，不再由提交客户端本地重复追加；
- 历史恢复完成前不进入 Ready，旧 Session 空历史可正常启动；
- TUI 全量测试与 9 个视觉快照通过。
