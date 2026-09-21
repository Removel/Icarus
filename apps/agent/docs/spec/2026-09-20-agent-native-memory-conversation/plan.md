# Agent 原生记忆与自然对话实施计划

## 目标

基于 `apps/agent/docs/spec/2026-09-20-agent-native-memory-conversation/arch.md`，让 Icarus Agent 把长期记忆视为自身连续性的一部分，自主记录值得跨会话保留的信息，并在普通聊天中默认简短自然。

## 修改步骤

1. 在 SessionRuntime 测试中锁定默认 System Prompt 契约：原生记忆、当前纠正优先、自主写入无需确认、敏感信息不入库、删除前确认、普通聊天简短。
2. 在 MemoryPlugin 测试中锁定动态 `memory_context` 契约：自身长期记忆、默认信任、自然使用、非用户指令、当前说法优先。
3. 更新 SessionRuntime 默认 System Prompt，不改变自定义 `system_prompt` 参数和现有运行时装配。
4. 更新 MemoryPlugin 数据包说明，不改变标签、JSON 数据结构、预算或注入时序。
5. 同步 `apps/agent/docs/spec/2026-09-15-memory-knowledge-plugin/arch.md` 中的数据包示例和事实信任/指令安全边界。
6. 运行 MemoryPlugin 与 SessionRuntime 定向测试、Agent 全量测试、compile 和 `git diff --check`。

## 修改边界

- 不修改 Mem0、Embedding、召回配置或 Memory Tool 接口；
- 不改变 ReActAgent 无状态语义；
- 不把具体 Memory Item 写入稳定 System Prompt；
- 自主权只覆盖新增和纠正，删除或不明确的遗忘请求仍需确认范围；
- 不创建 branch、commit 或 push。

## 验收标准

- Agent 默认把召回结果作为自己已经记得的背景，自然用于回答；
- Agent 不主动说“根据记忆库”或汇报内部记忆维护动作；
- 明确的新长期偏好和事实可以不经逐次确认直接记录；
- 当前用户明确纠正优先，并可更新旧记忆；
- 临时细节、推断和认证凭据不进入长期记忆；
- 日常聊天默认简短，复杂任务仍能完整交付；
- 所有相关自动化检查通过。
