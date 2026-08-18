# Skill 事件过滤与召回门槛设计

## 背景

当前 EventBus 只按来源 Plugin 路由。SkillPlugin 订阅 `agent` 后，会收到
AgentPlugin 发布的全部事件，包括文本增量、工具开始、工具完成、最终完成和错误。
SkillPlugin 虽然会在 `consume()` 中忽略无关事件，但这些事件已经进入它的 Runtime
队列，并产生 `event.publish`、`event.route` 和 `plugin.consume` Hook。一次长回复可能
因此产生数千条无业务价值的 Trace 记录。

当前 Skill 检索只执行 Top K，没有最低语义相似度门槛。当可用 Skill 很少时，即使
用户输入只是普通问候，也会返回候选并更新使用状态。

## 目标

- SkillPlugin 在运行时入口只接收它实际处理的完整事件。
- Agent 文本增量和工具执行流继续提供给 TUI，但不进入 SkillPlugin 队列。
- 增量流事件不产生 EventBus 和 Plugin Runtime 的 Hook Trace。
- 自动维护只依赖 `AgentCompletedEvent` 中的完整响应，不依赖工具流事件。
- Skill 召回允许返回空集合，默认最低内容匹配度为 `0.80`。
- 保持 EventBus 只按来源 Plugin 路由，不让基础设施解释具体业务事件类型。

## 非目标

- 不改变 TUI 的流式展示。
- 不改变 AgentPlugin 发布原始执行流的职责。
- 不新增 Skill 关键词规则、强制注入规则或特殊 Skill 分支。
- 不主动移除会话中已经注入的旧 Skill。
- 不新增 Embedding 持久化。
- 不改变自动维护的 `> 10` 工具调用门槛。

## 事件接收边界

### Plugin 声明接收规则

`BasePlugin` 提供通用的事件接收判断，默认接受来源订阅送达的所有事件：

```python
def accepts_event(self, source_plugin_id: PluginId, event: Event) -> bool:
    return True
```

`PluginRuntime.enqueue()` 在事件进入 inbox 前调用该判断，并以布尔返回值表示是否
接收；拒绝的事件不入队、不计入 Runtime 的已接收或已处理数量，也不会触发
`plugin.consume` Hook。现有未覆盖该方法的 Plugin 行为不变。

该判断属于消费者 Plugin，不属于 EventBus。EventBus 仍只根据来源 Plugin 找到订阅者，
不导入或识别 `AgentCompletedEvent` 等领域类型。

SkillPlugin 的接收规则为：

| 来源 | 接收事件 | 忽略事件 |
|---|---|---|
| `user-input` | `UserInputEvent`、`InputFinishedEvent` | queued、started 等其他事件 |
| `agent` | `AgentCompletedEvent` | 文本增量、工具开始、工具完成、错误及未知事件 |
| 其他来源 | 无 | 全部 |

因此注册关系仍然是 `skill <- user-input` 和 `skill <- agent`，但 SkillPlugin 的逻辑
订阅只包含上述完整事件。

### 增量事件 Trace 策略

Event 基类提供通用的 Trace 策略标记，默认允许记录：

```python
trace_event_flow: ClassVar[bool] = True
```

下列增量事件将该标记覆盖为 `False`，关闭 EventBus 和 Plugin Runtime 级 Trace：

- `AgentTextDeltaEvent`；
- `AgentToolStartedEvent`；
- `AgentToolCompletedEvent`。

Observable EventBus 和 Observable Plugin Runtime 只读取通用标记，不判断领域事件类型。
关闭 Trace 不影响事件发布、路由和消费，因此 OutputBridge 与 TUI 仍按原顺序接收并
渲染增量事件。完整的 `AgentCompletedEvent`、`AgentErrorEvent`、User Input、Blackboard
Context 及生命周期事件继续记录。`agent.stream`、`llm.stream` 的聚合终态和工具执行
本身仍由现有 Agent、LLM、Tool Executor 边界 Hook 记录。

## 从 AgentCompletedEvent 提取维护轨迹

`AgentCompletedEvent.response.messages` 已包含该次 Agent 调用的完整消息序列：历史消息、
当前 User Prompt、各 Step 的 Assistant ToolCall、对应 Tool Result，以及最终 Assistant
Message。SkillPlugin 不再通过工具开始和工具完成流事件维护轨迹。

处理流程：

1. `UserInputEvent` 到达时，仅保存本轮用户输入、图片和匹配 Skill。
2. `AgentCompletedEvent` 到达时，从 `response.messages` 中定位最后一条 User Message，
   其后的消息视为当前轮 ReAct 轨迹。
3. 按 Assistant Message 顺序生成 Step 编号；读取其中完整 `tool_calls`。
4. 使用后续 Tool Message，通过 `tool_call_id` 与最早尚未完成的同 ID ToolCall 关联，
   并解析统一 `ToolExecutionResult`。成功和失败的工具都计数。
5. 只有 `finish_reason == "stop"` 且工具调用数量大于 10 时，才启动维护 Agent。
6. `InputFinishedEvent(status="failed")` 丢弃失败轮次状态；`length`、
   `content_filter` 等非成功完成事件会弹出状态但不启动维护。

若完整响应中的 ToolCall 与 Tool Result 无法一一关联，自动维护应 fail closed：记录一条
聚合错误并跳过本轮维护，不影响主 Agent 已完成的响应。

## Skill 召回门槛

### 配置

新增 Skill 检索配置：

```json
{
  "skill": {
    "minimum_content_score": 0.8
  }
}
```

配置值限制在 `[0, 1]`，默认值为 `0.80`。`ConfigModel` 使用默认工厂，因此旧配置
即使暂时没有 `skill` 段也保持兼容。该门槛属于 Skill 检索配置，不放入 Embedding
Provider 配置，也不硬编码在具体 FastEmbed 实现中。

### 排名顺序

每个候选先计算归一化内容相似度：

```text
content_score = normalized_cosine_similarity(query, skill.description)
```

仅保留：

```text
content_score >= minimum_content_score
```

合格候选再使用现有公式排名并选取 Top 3：

```text
final_score = content_score * 0.8 + lifecycle_score * 0.2
```

门槛必须作用于纯内容分，而不是最终分。这样活跃状态只能调整相关 Skill 之间的顺序，
不能把语义无关但经常使用的 Skill 重新带回结果。

当前默认多语言 FastEmbed 模型的本地校准结果：

| 输入 | 对 `skill-plugin-phase-one` 描述的内容分 | 结果 |
|---|---:|---|
| `你好！` | 0.533 | 不召回 |
| TUI 多行输入设计 | 0.751 | 不召回 |
| 总结 SkillPlugin 第一阶段 | 0.895 | 召回 |
| 验证 Skill 动态检索链路 | 0.813 | 召回 |
| 今天天气怎么样 | 0.471 | 不召回 |
| 创建新的可复用 Skill | 0.835 | 召回 |

### 空召回与会话状态

- 本轮没有合格 Skill 时，不调用 `mark_used`，不更新 `last_used_at` 和 `use_count`。
- 会话从未注入过 Skill 时，发布 `completed` 的空 Context Contribution，不向 User
  Prompt 写入空列表或 `unchanged` 文案。
- 会话已经注入过 Skill 时，本轮空召回不主动删除旧 Skill；现有累计列表继续存在。
- 已有累计列表且无新增时，继续使用 `unchanged`；达到七轮刷新点时重新发送当前累计
  列表。
- 已累计 Skill 的文件定义发生变化时，仍以最新扫描结果原位替换并发送 `full`。

## 数据流

```text
AgentPlugin
  ├─ TextDelta / ToolStarted / ToolCompleted
  │    ├─ OutputBridge -> TUI
  │    └─ SkillPlugin Runtime: 接收规则拒绝，不入队，不写事件流 Trace
  │
  ├─ AgentCompletedEvent
  │    ├─ OutputBridge / Blackboard / UserInputPlugin
  │    └─ SkillPlugin -> 从 response.messages 提取完整工具轨迹 -> 判断维护门槛
  │
  └─ AgentErrorEvent
       └─ 不进入 SkillPlugin；UserInputPlugin 生成失败的 InputFinishedEvent

UserInputEvent
  -> SkillPlugin
  -> Scan + Embed
  -> content_score >= 0.80
  -> 80/20 Rank + Top 3
  -> SessionSkillState
  -> ContextContributionEvent

InputFinishedEvent(status=failed)
  -> SkillPlugin -> 丢弃失败轮次状态
```

## 错误处理

- 事件接收判断异常视为 Runtime 配置错误；记录错误并拒绝该事件，不能影响其他订阅者。
- Embedding、扫描和状态库错误继续沿用现有失败贡献机制，不能让 Blackboard 永久等待。
- 空召回是正常结果，不记录为错误。
- 完整工具轨迹提取失败只关闭本轮自动维护，不改变主 Agent 终态。
- Trace 策略只影响观测，不允许改变事件主流程。

## 测试

### Plugin Runtime

- 默认 Plugin 仍接收来源订阅的全部事件。
- 拒绝事件不进入 inbox，不调用 `consume()`，不触发 `plugin.consume` Hook。
- EventBus 不导入领域事件类型。

### Trace

- 文本、工具开始和工具完成事件仍能被 OutputBridge 接收。
- 这些增量事件不产生 `event.publish`、`event.route` 和 `plugin.consume` Trace。
- `AgentCompletedEvent` 和 `AgentErrorEvent` 仍产生完整终态 Trace；后者不投递给
  SkillPlugin。

### SkillPlugin

- SkillPlugin 从 Agent 来源只消费 `AgentCompletedEvent`，不消费文本、工具和错误流事件。
- `AgentCompletedEvent.response.messages` 能恢复工具顺序、Step、参数、成功结果和失败结果。
- 超过 10 个工具且成功完成时启动维护；`length` 和错误终态不启动。
- 轨迹不完整时跳过维护且不影响主流程。

### 召回

- `content_score < 0.80` 的候选全部过滤。
- 生命周期分不能救回低于门槛的候选。
- 合格候选仍按 80/20 排名并最多返回三个。
- 首轮空召回不注入 ContextBlock，也不更新使用记录。
- 已有累计 Skill 后空召回仍保持 `unchanged` 和七轮刷新行为。

## 验收标准

- 长流式回复不再向 SkillPlugin 队列投递文本和工具增量。
- 同一类长回复的 Trace 不再包含增量事件的 EventBus/Plugin Runtime 记录。
- TUI 的文字和工具状态流式展示无回归。
- 普通问候不会召回唯一的无关 Skill。
- 明确的 SkillPlugin 复盘或动态检索需求仍能召回对应 Skill。
- 自动维护继续使用完整多轮上下文和本轮完整工具轨迹。
- Agent、TUI 测试、compileall 和 `git diff --check` 全部通过。
