# Session History RPC Development Plan｜Session 历史 RPC 实施计划

## 目标

通过 Gateway 暴露 AgentRuntime 的公共会话记录，不让 Gateway 解释 Trace、Blackboard 或内部
Plugin Event。

## 实施步骤

1. 在共享 wire model 中为 RuntimeUpdate 增加可选 sequence，并定义 history 结果模型。
2. 增加 `session.get_history(workspace_path, session_id, after_sequence=0)`。
3. 返回有序 records 与一致读取边界 `history_cursor`。
4. 保持 `session.subscribe` 现有参数和响应不变；客户端先订阅缓冲，再查询历史。
5. 将 Session 不存在、历史损坏和参数错误映射为稳定安全错误。

## 验证

- 空历史、完整历史和 after_sequence 增量读取；
- 历史 RPC 不加载 SessionRuntime；
- sequence 和 RuntimeUpdate payload 正确序列化；
- 不暴露绝对路径、Trace 或内部异常。

## 实施结果

- 已实现 `session.get_history`、`after_sequence`、`history_cursor` 和稳定错误映射；
- 已扩展共享 RuntimeUpdate wire model 的可选 sequence；
- 已验证 Gateway/Runtime 重启后可从磁盘读取同一 Session 的完整公共记录。
