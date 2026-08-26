# Agent Core Capability Completion Development Plan｜Agent 基础能力补全实施计划

## 目标

基于 `apps/agent/docs/arch/agent-core-capability-completion-design.md`，在进入 Session 和 UI 产品化
之前完成以下 Agent 基础能力：

- 提取 ReAct 四种入口的重复执行逻辑；
- 由 Harness 为一次 Agent Run 提供默认 256 个模型 Step 的硬上限；
- 使用一个 TaskErrorEvent 表达 Task 内致命与非致命错误；
- 在每轮开始时按上一轮 Usage 和 85% 阈值 Compact Blackboard 旧历史；
- 将本地图片导入现有 Session assets，并通过稳定引用交给 Provider Adapter。

本计划不实现 Memory、Knowledge/RAG、业务 Session 列表与恢复，也不实现 TUI/WebUI 图片交互。

## 当前进度

| 里程碑 | 状态 |
|---|---|
| ReAct 公共流程与 last_usage | 已完成 | 四入口共享单次 Run 状态和纯处理逻辑 |
| 256 Step Harness 与统一错误 | 已完成 | 第 257 Step 前截停，TaskErrorEvent 覆盖 fatal/nonfatal |
| Blackboard Compact | 已完成 | 85% 阈值、直接替换、失败保留和 Usage 标记已接通 |
| 本地图片稳定引用 | 已完成 | Session assets、稳定引用与双 Provider 转换已接通 |
| 全量验证与文档收口 | 已完成 | Agent/TUI 全量测试与静态检查通过 |

## 实施原则

- ReActAgent 保持无状态，不读取 ConfigModel、Persistence 或 Plugin Runtime；
- Agent 的四个公开入口和同步、异步、流式、非流式语义保持兼容；
- 确定性运行限制放在 Harness/Run Control，不交给模型判断；
- EventBus 只按来源 Plugin 路由，不解释错误或修改 Task 状态；
- Blackboard 只维护当前有效模型历史，不在本阶段承担原始业务会话存档；
- Provider Adapter 只接收注入的图片解析 callable，不导入 Persistence Plugin；
- 配置在 Task/Run 开始时形成快照，中途变化不影响当前 Run；
- 每个任务先补定向测试，再修改实现；完成全部里程碑后运行全量验证；
- 不为未来场景增加 AssetStore、预算器、状态机或其他新顶层抽象。

## 交付顺序

```text
行为基线
  ↓
ReAct 公共流程与 last_usage
  ↓
256 Step Harness + TaskErrorEvent
  ↓
Blackboard Compact
  ↓
Session 本地图片引用与 Provider 转换
  ↓
全量验证与文档收口
```

后一个里程碑依赖前一个里程碑的公共类型和行为。实施时按此顺序推进，不并行修改同一条事件、
历史或 Provider 数据链。

## 任务一：固化 ReAct 行为并提取公共流程

### 更新文件

- `apps/agent/src/agent_orchestration/capability/react_agent.py`
- `apps/agent/src/agent_orchestration/capability/types.py`
- `apps/agent/test/agent_orchestration/capability/test_react_agent.py`
- `apps/agent/test/agent_orchestration/capability/test_react_agent_stream.py`
- 必要时更新 `apps/agent/test/agent_orchestration/hooks/wrappers/test_observable_wrappers.py`

### 开发内容

1. 先用参数化测试固定四个入口共有的行为：
   - 初始 System、History、当前 User Message 的排列；
   - 多 Step ToolCall、ToolResult 顺序与最终回答；
   - Tool Batch 的并行/顺序屏障；
   - 运行中 Context 的注入位置；
   - 完成、失败和取消时的唯一终态；
   - Run 总 Usage、最后一次调用 Usage、Step 和历史检查点。
2. 在 `react_agent.py` 内增加一个私有、仅限单次调用的运行数据载体，统一保存：
   - `messages`；
   - `task_message_start`；
   - Tool 快照与 ToolDefinition；
   - `steps`；
   - 累计 `usage`；
   - `last_usage`；
   - `reasoning_parts`。
3. 提取纯公共逻辑，包括初始化、模型响应归并、Usage 更新、Tool Result 顺序回填、检查点更新和
   AgentResponse 构造。
4. 同步和异步 I/O 仍由各自驱动执行；流式入口继续负责发送文本与 Tool 生命周期 Event。不得用
   反射、运行时方法名分派或新的公共状态机强行合并四个入口。
5. 为 `AgentResponse` 增加可选 `last_usage`；`usage` 继续保持 Run 总消耗，避免改变现有调用方口径。
6. Run Control 的历史检查点同时记录与该消息前缀对应的 `last_usage`，为最大 Step 截停、取消和
   Blackboard Token 标记复用。

### 验收

- 四种入口对同一模型脚本得到相同的 Message、Tool、Step、Usage 和终态；
- 流式事件顺序、取消边界和完整 Tool Batch 检查点不回归；
- 只减少内部重复，不增加新的公开 Agent 调用模型。

## 任务二：实现 256 Step Harness 与统一错误事件

### 新增文件

- `apps/agent/src/agent_orchestration/events/task_error.py`
- `apps/agent/test/agent_orchestration/events/test_task_error.py`

### 更新文件

- `apps/agent/src/model_config/config_model.py`
- `apps/agent/src/model_config/__init__.py`
- `apps/agent/settings.json`
- `apps/agent/src/agent_orchestration/events/__init__.py`
- `apps/agent/src/agent_orchestration/capability/types.py`
- `apps/agent/src/agent_orchestration/capability/react_agent.py`
- `apps/agent/src/agent_orchestration/capability/__init__.py`
- `apps/agent/src/agent_orchestration/run_control/types.py`
- `apps/agent/src/agent_orchestration/run_control/channel.py`
- `apps/agent/src/agent_orchestration/run_control/registry.py`
- `apps/agent/src/agent_orchestration/plugins/agent/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/agent/factory.py`
- `apps/agent/src/agent_orchestration/plugins/agent/manifest.json`
- `apps/agent/src/agent_orchestration/plugins/user_input/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/user_input/manifest.json`
- `apps/agent/src/agent_orchestration/plugins/blackboard/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/blackboard/manifest.json`
- `apps/agent/src/agent_orchestration/plugins/output_bridge/manifest.json`
- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_agent.py`
- `apps/agent/test/application/test_output_bridge.py`
- `apps/tui/src/event_pipeline/projectors/agent.py`
- `apps/tui/src/event_pipeline/projectors/user_input.py`
- `apps/tui/src/event_pipeline/projectors/blackboard.py`
- `apps/tui/src/event_pipeline/projectors/task_error.py`
- `apps/tui/src/event_pipeline/dispatcher.py`
- `apps/tui/src/replay.py`
- 上述模块对应测试与 TUI 回放 Fixture

### 开发内容

1. 在配置模型中新增：

   ```python
   class AgentSettings(BaseModel):
       max_steps: int = Field(default=256, ge=1)

   class ConfigModel(BaseModel):
       agent: AgentSettings = Field(default_factory=AgentSettings)
   ```

   保持旧配置未声明 agent 时仍可加载，并在仓库 settings.json 中显式写出默认 256，便于使用者
   发现和修改。
2. TaskChannel 创建时保存 max_steps 快照；Run Control 在每次新模型 Step 前检查上限。Step 1 到
   Step 256 合法，准备进入 Step 257 时抛出明确的 MaxStepsExceededError。
3. Step 256 返回 ToolCall 时完成已启动的完整 Tool Batch 并更新检查点；截停后不再启动 LLM 或 Tool。
4. 在编排层通用 events 中定义 TaskErrorEvent，字段严格按设计文档实现，不携带 Exception 对象或
   traceback。删除 AgentErrorEvent，不长期保留兼容别名。
5. ReActAgent 遇到不可恢复异常时继续抛出；ObservableAgent 记录异常 Hook；AgentPlugin 作为 Agent
   Run 所有者按已知异常映射稳定 code，未知异常使用 `agent_run_failed`，并只发布一次致命
   TaskErrorEvent。
6. 对 `max_steps_exceeded` 附带 TaskChannel 的安全 `task_messages` 和 `last_usage`；Blackboard 提交
   该检查点。最后一个 ToolResult 尚未再次提交给模型，因此允许 last_usage 不覆盖该部分；不额外
   调用模型或引入 tokenizer。其他意外致命失败默认不提交历史。
7. AgentPlugin 转发失败的 AgentToolCompletedEvent 后，再发布
   `TaskErrorEvent(fatal=False, code="tool_execution_failed")`；ToolExecutionResult 仍照常回填模型。
8. UserInputPlugin 仅在 fatal=True 时结束当前输入；非致命错误不唤醒完成等待，不改变 TaskChannel。
9. Blackboard 对 Agent 来源的 fatal Error 标记 Agent 已结束；仅在事件携带明确安全检查点时提交
   历史。
10. Blackboard 收到失败的 ContextContributionEvent 时发布一次 nonfatal context_provider_failed，
    并继续使用其余 Context；同一来源被替换时不得重复报告旧失败。
11. UserInputPlugin 与 BlackboardPlugin 用 `accepts_event()` 限制 TaskErrorEvent 的允许来源。发布者
    直接处理自身状态，不消费自己发布的 Error；UserInputPlugin 只接受 AgentPlugin 和
    BlackboardPlugin，BlackboardPlugin 拒绝自身全部 Event 并只接受 AgentPlugin 的错误，EventBus
    不增加特殊分支。
12. AgentCancelledEvent 增加可选 last_usage，与其携带的安全检查点对应；取消仍不转换为 Error。
13. 同步迁移 Plugin Manifest、OutputBridge、TUI Projector、Replay 编解码和测试：
    - 提取一个小型共享 Task Error 投影函数；
    - AgentProjector、UserInputProjector 和新增 BlackboardProjector 分别处理对应来源；
    - BlackboardCompactedEvent 在当前 TUI 中识别但不展示，留待产品化阶段设计交互；
    - Replay Schema 升为 v3，并迁移仓库内 v2 Fixture，不长期解码 AgentErrorEvent；
    - 同一 Tool 失败只展示 Tool 卡片或统一错误中的一种，不生成重复可见错误。
14. Runtime 启动和 Manifest 错误继续留在 Task 之外。

### 定向测试

- max_steps 默认值、非法值和旧配置兼容；
- Step 256 正常完成、Step 256 Tool Batch 完整结束、Step 257 前截停；
- 截停后的检查点、last_usage 和下一轮历史提交；
- fatal/nonfatal 对 TaskChannel、UserInput 队列和 Blackboard 的不同影响；
- Tool 失败同时具有 Tool Result 和非致命错误观察，但 TUI 不重复展示；
- 同一异常只产生一个 TaskErrorEvent 和一个 failed InputFinishedEvent；
- Cancel 与 Error/Completed 竞争时只有一个终态；
- Manifest 发布/消费声明和 Replay 兼容新事件结构；
- 多来源 TaskErrorEvent 不被发布者自身重复消费或形成错误终态。

## 任务三：实现 Blackboard Compact

### 新增文件

- `apps/agent/src/agent_orchestration/plugins/blackboard/history_compactor.py`
- `apps/agent/test/agent_orchestration/plugins/blackboard/test_history_compactor.py`

### 更新文件

- `apps/agent/src/model_config/config_model.py`
- `apps/agent/settings.json`
- `apps/agent/src/agent_orchestration/plugins/blackboard/events.py`
- `apps/agent/src/agent_orchestration/plugins/blackboard/state.py`
- `apps/agent/src/agent_orchestration/plugins/blackboard/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/blackboard/factory.py`
- `apps/agent/src/agent_orchestration/plugins/blackboard/manifest.json`
- `apps/agent/src/agent_orchestration/plugins/output_bridge/manifest.json`
- `apps/agent/src/application/agent_runtime_service.py`
- `apps/agent/test/model_config/test_config_loader.py`
- `apps/agent/test/model_provider/test_llm_factory.py`
- `apps/agent/test/agent_orchestration/plugins/blackboard/test_plugin.py`
- `apps/agent/test/application/test_agent_runtime_service.py`

### 开发内容

1. 为每个 LLMConfig 增加必填正整数 context_window，并同步更新 settings.json 与测试构造器。实施
   前先核对当前部署模型的真实窗口，配置值不得从 model_name 猜测；无法确认时停止在配置更新
   之前，不用臆测值让 Compact 错误运行。
2. Blackboard 状态增加 `context_tokens: int | None`；这是 v1 State 的可选增量字段，不升级版本。
   恢复不含该字段的旧快照时保留 messages，并把 context_tokens 置为 None。
3. Blackboard 在收到当前 UserInputEvent、等待所需 Context 完成后、发布 Context Ready 前执行一次
   检查；触发计算只读旧历史标记，不包含当前输入。
4. 固定触发条件为：

   ```text
   context_tokens >= context_window * 0.85
   ```

   不增加 tokenizer、阈值配置、Token 预算或多级摘要策略。
5. 增加普通内部组件 HistoryCompactor：使用设计文档给出的固定 System Prompt 和全部旧历史调用
   thinking BaseLLM，返回一条 summary User Message 及本次 Usage。
6. Blackboard Factory 创建并持有专用 ObservableLLM；适配器在 Plugin 生命周期内复用，在 stop 时
   关闭。不得复用或访问 AgentPlugin 的私有 AgentFactory。
7. Compact 成功时原子替换全部旧历史，context_tokens 更新为 Compact output_tokens，然后发布不含
   摘要正文的 BlackboardCompactedEvent，最后发布摘要历史加当前输入的 Context Ready。
8. Compact 失败时历史和 Token 标记都不变；Blackboard 先收束自己的 Task 状态，再发布 fatal
   compact_failed；UserInputPlugin 结束本轮，Agent Run 不创建。
9. 正常完成、最大 Step 截停和带历史的取消都用对应 last_usage.total_tokens 更新标记。Usage 缺失
   时保留旧值，并发布一次 nonfatal usage_unavailable。
10. 对当前文本输入只做极端值检查：UTF-8 字节数不小于 context_window * 4 时发布 fatal
    input_too_long，不裁剪原文；其余超限由 Provider 返回 model_request_failed。
11. LLM Hook/Trace 保留 Compact 输入、输出、Usage 和耗时；Blackboard 的有效历史不额外保留压缩前
    副本。

### 定向测试

- 低于、等于和高于 85% 阈值；
- 当前新输入不参与触发判断，也不进入 Compact 请求；
- 成功后旧历史被一条摘要完全替换，事件先于 Context Ready；
- Compact output_tokens 成为新标记，下一轮正常完成后改用 last_usage.total_tokens；
- Compact 异常、空结果或无 Usage 时不产生半替换状态；
- Usage 缺失不记零，nonfatal 错误不结束 Task；
- 超长输入检查不修改原文且不创建 Agent Run；
- 不含 context_tokens 的旧 v1 State 恢复和新 v1 snapshot round trip；
- Compactor 的 ObservableLLM 被复用并在 Plugin stop 时关闭。

## 任务四：实现本地图片稳定引用

### 更新文件

- `apps/agent/src/model_provider/types.py`
- `apps/agent/src/model_provider/llm_factory.py`
- `apps/agent/src/model_provider/impl/openai_llm.py`
- `apps/agent/src/model_provider/impl/anthropic_llm.py`
- `apps/agent/src/agent_orchestration/agent_factory.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/runtime.py`
- `apps/agent/src/agent_orchestration/plugins/user_input/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/agent/factory.py`
- `apps/agent/src/agent_orchestration/plugins/agent/manifest.json`
- `apps/agent/src/agent_orchestration/plugins/blackboard/factory.py`
- `apps/agent/src/agent_orchestration/plugins/blackboard/manifest.json`
- `apps/agent/src/application/agent_runtime_service.py`
- `apps/agent/test/model_provider/impl/test_openai_llm.py`
- `apps/agent/test/model_provider/impl/test_anthropic_llm.py`
- `apps/agent/test/model_provider/test_llm_factory.py`
- `apps/agent/test/agent_orchestration/plugins/persistence/` 下的对应测试
- `apps/agent/test/agent_orchestration/plugins/user_input/test_plugin.py`
- `apps/agent/test/agent_orchestration/plugins/blackboard/test_plugin.py`
- `apps/agent/test/application/test_agent_runtime_service.py`

### 开发内容

1. 将 ImagePart 改为 `source`、`source_type`、`media_type` 三个扁平字段；第一个位置参数和默认
   source_type 继续表达 URL。
2. 更新 Blackboard Message 的序列化与反序列化：新快照写入新字段，读取旧快照的 url 字段时转换为
   URL ImagePart。
3. 直接在 PersistenceSession 增加 import_image 和 resolve_image，不新增 AssetStore：
   - 只接受 JPEG、PNG、GIF、WebP；
   - 使用小型文件签名检查识别媒体类型并使用规范扩展名，不新增图片处理依赖；
   - 以 SHA-256 命名并在同一 Session 内去重；
   - 写入 Session assets 时采用临时文件加原子替换，避免留下半文件；
   - resolve 时拒绝绝对路径、非 assets 前缀和目录逃逸。
4. AgentRuntimeService.submit 与 UserInputPlugin.submit 接受 `ImagePart | str | Path`；原始路径只存于
   PendingInput，发布 UserInputEvent 前全部转为 ImagePart。
5. 图片导入失败时，错误信息不得暴露原始绝对路径；UserInputPlugin 发布一个 fatal
   image_import_failed 和一个 failed InputFinishedEvent，不发布 UserInputEvent。PersistenceSession
   在边界处把文件系统异常转换为不含原路径的领域错误；Trace 记录错误类别和脱敏摘要，不记录
   原始异常文本中的绝对路径。
6. AgentPlugin 和 BlackboardPlugin 的 Manifest 都显式依赖 persistence/runtime 与
   persistence/session；Factory 构造 session-bound resolver 并注入各自的 LLMFactory。
7. LLMFactory 将可选 resolver 传给 Provider Adapter。未注入时 URL 图片正常；Asset 图片抛出可由
   AgentPlugin 转换为 image_asset_unavailable 的明确异常。
8. OpenAI Chat Completions 将 Asset 读取为 Data URL；Anthropic Messages 将其读取为 base64 source。
   Data URL/Base64 只存在于 Provider 请求组装期间，不写入 Message、Event、Blackboard 或 Trace。
9. URL 图片保持现有协议；不迁移 OpenAI Responses API，也不引入 Files API 缓存。

### 定向测试

- URL ImagePart 的位置参数兼容和 v1 Blackboard 状态恢复；
- 本地图片导入、SHA-256 命名、规范扩展名和同内容去重；
- 删除或移动原始文件后 Asset 仍可解析；
- 绝对路径、目录逃逸、缺失、不可读、伪造扩展名和不支持格式被拒绝；
- UserInputEvent、Blackboard State 与 Trace 不出现原始绝对路径或二进制；
- OpenAI 生成正确 Data URL，Anthropic 生成正确 media_type/base64 source；
- 无 resolver 的独立 AgentFactory 仍支持纯文本和 URL 图片；
- 图片导入失败只产生一个 fatal 错误和一个 InputFinishedEvent。

## 任务五：集成验证与文档收口

### 定向验证

每完成一个任务先运行最小测试。四个里程碑全部完成后按以下顺序执行：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/capability \
  apps/agent/test/agent_orchestration/run_control \
  apps/agent/test/agent_orchestration/events -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/agent \
  apps/agent/test/agent_orchestration/plugins/user_input \
  apps/agent/test/agent_orchestration/plugins/blackboard \
  apps/agent/test/agent_orchestration/plugins/persistence \
  apps/agent/test/application -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/model_config \
  apps/agent/test/model_provider -q

apps/agent/.venv/bin/python -m pytest apps/tui/test -q
```

### 全量验证

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src apps/agent/test apps/tui/src apps/tui/test
git diff --check
```

有可用凭据时，再执行三个小型真实模型冒烟：

- 一次包含 ToolCall 的多 Step Run；
- 一次人为构造到 85% 以上的 Compact；
- OpenAI-compatible 和 Anthropic 各一次本地图片输入。

冒烟测试不得输出 API Key、原始图片绝对路径或图片 Base64。缺少凭据不阻塞确定性测试完成，但
必须在实施结果中明确记录未执行项。

### 文档同步

实现完成后根据真实代码更新：

- `apps/agent/docs/arch/agent-core-capability-completion-design.md`；
- 本计划的进度和实施结果；
- `docs/todo/agent-core.md`；
- `docs/todo/development-roadmap.md`；
- `docs/todo/product-experience.md`。

## 分阶段完成标准

| 里程碑 | 完成条件 |
|---|---|
| ReAct 与 Harness | 四入口行为一致，Step 257 前确定性截停，安全检查点可继续使用 |
| 统一错误 | Task 内错误只使用 TaskErrorEvent，fatal/nonfatal 终态正确，UI 不重复显示 |
| Compact | 只检查旧历史，85% 时直接摘要替换，失败保留历史并结束本轮 |
| 本地图片 | Context 只持有稳定 Asset 引用，双 Provider 能转换且不泄漏原始路径/二进制 |
| 最终验收 | Agent 与 TUI 全量测试、compileall、diff check 通过，文档与代码一致 |

## 实施结果

当前状态：已完成。

- Agent 全量测试：337 passed；
- TUI 全量测试：97 passed，包含 8 个 Snapshot；
- `compileall`：通过；
- `git diff --check`：通过；
- 未执行真实模型冒烟：本轮没有使用外部凭据，Tool、Compact 与双 Provider 图片转换均由确定性
  单元和集成测试覆盖。
