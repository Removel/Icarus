# Agent Core TODO

## 待办

- 完善 Agent Core 的多模态输入能力。
- 改造 Blackboard 的上下文组织与动态上下文收集能力。
- 实现 Skill 动态维护与加载插件。
- 实现角色卡片风格化输出插件。
- 实现情感响应插件。
- 改造 AgentPlugin 执行过程的安全控制能力。

## 任务级取消

- [ ] 在 `AgentRuntimeService` 提供按 `task_id` 取消当前任务的公开接口，不要求停止或
  重建整个 Runtime。
- [ ] 让 `UserInputPlugin`、`AgentPlugin`、模型流和工具调用真实传播取消信号，并提供
  `InputFinishedEvent(status="cancelled")` 终态。
- [ ] 取消轮次只清理 Blackboard 和 Skill 的任务状态，不把不完整用户/助手消息提交到
  Session History；已发生的文件修改和外部副作用不做隐式回滚。
- [ ] 为重复取消、已结束任务、错误 task ID、工具执行中取消和资源清理增加测试。
