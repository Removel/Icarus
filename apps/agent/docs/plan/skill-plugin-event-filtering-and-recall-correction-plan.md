# SkillPlugin 事件过滤、日志降噪与召回门槛修正计划

## 目标

按 `apps/agent/docs/arch/skill-plugin-design.md` 的已确认修正实现：

- SkillPlugin 从 Agent 来源只接收 `AgentCompletedEvent`；
- 从完整 `response.messages` 恢复当前轮工具轨迹；
- Agent 文本与工具增量继续供 TUI 使用，但不进入 SkillPlugin，也不写事件流级 Trace；
- Skill 先以 `content_score >= 0.80` 过滤，再执行现有 80/20 排名；
- 每轮只记录一条最小化的 Skill 检索聚合信息。

## 任务一：Plugin Runtime 通用接收过滤

**更新文件**

- `apps/agent/src/agent_orchestration/plugin_runtime/base_plugin.py`
- `apps/agent/src/agent_orchestration/plugin_runtime/plugin_runtime.py`
- 对应 Plugin Runtime 测试

**实现内容**

- `BasePlugin.accepts_event(source_plugin_id, event)` 默认返回 `True`；
- `PluginRuntime.enqueue()` 在入队前调用该方法并返回是否接收；
- 拒绝的事件不进入 inbox，不改变 Runtime 计数，不触发 `plugin.consume` Hook；
- 接收判断异常隔离为该订阅者的路由错误，不影响其他订阅者；
- EventBus 继续只按来源 Plugin 路由，不导入领域 Event。

## 任务二：增量事件 Trace 降噪

**更新文件**

- `apps/agent/src/agent_orchestration/events/base_event.py`
- `apps/agent/src/agent_orchestration/capability/types.py`
- `apps/agent/src/agent_orchestration/plugin_runtime/wrappers/observable_event_bus.py`
- `apps/agent/src/agent_orchestration/plugin_runtime/wrappers/observable_plugin_runtime.py`
- 对应观测和持久化测试

**实现内容**

- Event 默认 `trace_event_flow=True`；
- Text Delta、Tool Started、Tool Completed 覆盖为 `False`；
- Observable EventBus 对关闭策略的事件不触发 publish/route Hook，但仍正常发布和路由；
- Observable Plugin Runtime 对关闭策略的事件不触发 consume Hook，但仍正常消费；
- Agent/LLM 聚合终态与 Tool Executor 完整执行 Hook 保持不变；
- 验证 OutputBridge / TUI 仍收到原始增量事件。

## 任务三：召回门槛与聚合检索日志

**更新文件**

- `apps/agent/settings.json`
- `apps/agent/src/model_config/config_model.py`
- `apps/agent/src/agent_orchestration/plugins/skill/ranker.py`
- `apps/agent/src/agent_orchestration/plugins/skill/plugin.py`
- `apps/agent/src/application/agent_runtime_service.py`
- 配置、Ranker、SkillPlugin 和应用测试

**实现内容**

- 新增默认兼容的 `SkillSettings(minimum_content_score=0.8)`；
- Ranker 在计算内容分后先过滤，再做 80/20 排名与 Top 3；
- 空召回不 `mark_used`；首次空召回发布 completed 空贡献；已有累计列表时保持
  `unchanged` 和七轮刷新；
- 每轮触发一次 `skill.retrieval` Hook，记录候选数、合格数、阈值、最多三个命中摘要、
  注入模式、累计数和耗时；
- 错误和超时记录一次聚合错误；不记录 Prompt、向量、Skill 正文或工具输出正文。

## 任务四：从 AgentCompletedEvent 恢复工具轨迹

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/skill/turn_state.py`
- `apps/agent/src/agent_orchestration/plugins/skill/plugin.py`
- 对应 Turn State、维护和应用集成测试

**实现内容**

- SkillPlugin 的 `accepts_event` 只接受 UserInput、失败 InputFinished 和 AgentCompleted；
- 删除通过 Tool Started / Completed 增量维护状态的主路径；
- 从最后一条 User Message 后的 Assistant / Tool Message 恢复 Step、ToolCall 和 Result；
- 轨迹不完整时聚合报错并 fail closed，不启动维护；
- `finish_reason != stop` 或工具数不大于十时不启动维护；
- 成功和失败的完整工具结果都计数；
- 维护 Agent 继续获得完整多轮 messages 与当前轮结构化轨迹。

## 任务五：文档与验证

**更新文件**

- `apps/agent/docs/arch/plugin-event-flow-current-state.md`
- 必要的测试说明

**验证顺序**

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/plugin_runtime -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/plugins/skill -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/application -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test
git diff --check
```

最后执行一个隔离数据目录的真实 TUI / Runtime Smoke Test，确认：

- 普通问候空召回；
- 明确 SkillPlugin 请求召回对应 Skill；
- TUI 仍流式显示文字和工具状态；
- 新 Trace 不再出现文本与工具增量的事件流记录；
- `skill.retrieval` 只记录聚合最小字段。
