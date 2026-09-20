# Tool Execution Guard 开发计划

## 目标

基于 `apps/agent/docs/arch/tool-execution-guard-design.md`，为所有 Agent Tool 建立统一、不可绕过的
第一阶段执行保护：

- Agent 通过保留 `_execution` 参数申请 timeout 和最大可见输出 Token；
- Harness 使用稳定默认值、模型窗口比例和硬上限裁决实际预算；
- ToolExecutor 统一执行 timeout，并为每个 Tool Call 生成协议闭合的结果；
- 对整个 `ToolExecutionResult.as_dict()` 计量，超预算时将预算处理前文本保存到当前 Session，并在原输出
  结构中生成确定性的 Head/Tail Preview；
- 单个 Tool Group 使用总预算和最多 8 个调用的上限，结果按原 Tool Call 顺序写回；
- List/Read 能力使用有界分页，不能一次无界返回全部数据。

本计划按“配置与协议 → timeout → Tool Result 文件 → 单结果裁剪 → Batch → 分页 → 集成验证”实施。每个
任务先增加失败测试，再修改生产代码。

## 范围

### 本期实现

- `AgentSettings` 下的 Tool Guard 默认配置与严格校验；
- 所有 Agent 可见 Tool Schema 的 `_execution` 自动扩展；
- `_execution` 参数解析、剥离与有效预算裁决；
- 可取消异步 Tool 与独立进程 Tool 的统一 timeout 边界；
- Bash 进程组终止与 16 MiB 流式输出采集上限；
- Session 级 Tool Result `.txt` 文件与 `ToolResultStore`；
- Tool Result 稳定序列化和 Token 计量 fallback；
- 字符串、对象、数组和嵌套 JSON 的递归 Head/Tail Preview；
- 单结果 4K 默认/16K 上限、Batch 16K 总预算、每 Batch 最多 8 个 Tool Call；
- MCP Tool list 现有分页上限复核，以及 Builtin `read`、`skills_list`、`knowledge_list` 的有界分页；
- Hook/Trace 预算元数据、定向测试、Agent 全量回归和文档同步。

### 本期不实现

- Active Run 中旧 Tool Result 的 request-local 二次降级；
- System Prompt、Tool Schema、完整历史、图片和输出预留组成的 Request Assembler；
- 跨 Run 历史 Compact 或 proactive prune；
- 重复 Tool 指纹、相同结果、无进展或 Cursor 不推进检测；
- HITL、Tool Policy、风险分类、Effect Journal 和副作用恢复；
- 图片数量、分辨率或视觉 Token 预算；
- Session archive/delete 产品接口、累计空间上限和自动 Tool Result 文件清理；
- Gateway/TUI 新协议或独立 Tool Result 浏览器；
- Git 推送或分支同步。

## 当前基线与目标差异

| 能力 | 当前实现 | 本计划完成后 |
| --- | --- | --- |
| Tool 控制参数 | 各 Tool 自行声明，只有 Bash 有可选 timeout | 所有 Agent Tool 自动获得 `_execution` |
| 默认 timeout | ToolExecutor 无默认值 | 默认 120 秒、Agent 可申请、硬上限 600 秒 |
| 同步 Tool 超时 | `to_thread` 可取消等待但线程继续运行 | 本期不提前返回；需确定性 timeout 的 Tool 改造成异步或独立进程 |
| Bash 超时 | 同步路径依赖 `subprocess.run`，异步路径只终止直接进程 | 独立进程组，TERM/KILL 收束；达到采集上限也停止 |
| 单结果预算 | 无；完整 `as_dict()` 进入模型 | 默认 4K Token、最小 512、硬上限 16K |
| JSON 裁剪 | 无 | 保持类型与顺序的递归 50/50 Head/Tail |
| 预算处理前结果 | 不外置 | 超预算时写当前 Session `.txt` Tool Result 文件，超过 16 MiB 标记不完整 |
| Tool Batch | 只按可并行性分执行批次 | 最多 8 个调用、16K Token 总预算 |
| List/Read | MCP list 已分页，Builtin read 无分页 | List 默认 50/最大 200；Read 默认 200 行/最大 2000 |
| 最终断言 | 无 Result Token 硬检查 | 每次裁剪和整个 Batch 均重新序列化、重新计量 |

## 关键接口与数据流

```text
AgentFactory
  └─ ToolExecutor(
       registry,
       policy,
       token_counter,
       result_store,
     )
       └─ snapshot(names)
            └─ Run-scoped ToolExecutor snapshot

ReActAgent Step
  └─ model tool_calls
       └─ ToolExecutor.prepare_calls()
            ├─ 解析并剥离 _execution
            ├─ 裁决 effective timeout/output budget
            └─ 拒绝超过 8 个的调用
       └─ execute/iter_completed
            ├─ timeout
            └─ 原始 ToolExecutionResult
       └─ finalize_batch()
            ├─ 完整结果 Token 计量
            ├─ Tool Result 文件写入
            ├─ 单结果结构化 Preview
            ├─ Batch water-filling
            └─ 最终硬断言
       └─ ReActAgent 按原调用顺序写入 Tool Message
```

## 任务一：固化配置模型与预算领域类型

### 新增文件

- `apps/agent/src/agent_orchestration/tools/execution_policy.py`
- `apps/agent/test/agent_orchestration/tools/test_execution_policy.py`

### 更新文件

- `apps/agent/src/model_config/config_model.py`
- `apps/agent/src/model_config/__init__.py`
- `apps/agent/settings.json`
- `apps/agent/test/model_config/test_config_loader.py`

### 开发内容

1. 在 `AgentSettings` 增加嵌套 `tool_execution` 配置，使用严格 Pydantic 字段：

   ```text
   default_timeout_seconds = 120
   max_timeout_seconds = 600
   default_output_tokens = 4000
   min_output_tokens = 512
   max_output_tokens = 16000
   batch_output_tokens = 16000
   max_calls_per_batch = 8
   preview_target_ratio = 0.9
   head_ratio = 0.5
   max_result_file_bytes = 16 MiB
   default_page_size = 50
   max_page_size = 200
   default_read_lines = 200
   max_read_lines = 2000
   ```

   仓库默认 `settings.json` 只显式展示最常调整的
   `default_timeout_seconds` 与 `default_output_tokens`。其余字段使用配置模型默认值；用户需要调整时
   可以按同名字段显式补充。

2. 配置校验必须保证：
   - 默认值位于对应最小/最大范围内；
   - Batch 预算至少容纳一个最小 Result；
   - `max_calls_per_batch × min_output_tokens` 不超过 Batch 预算；
   - 比例为有限的 `(0, 1)` 数字，布尔值不作为整数或浮点数接受；
3. 在 `execution_policy.py` 增加不可变领域类型：
   - `ToolExecutionSettings` 或直接复用配置快照；
   - `ToolExecutionRequest(timeout_seconds, max_output_tokens)`；
   - `EffectiveToolBudget(timeout_seconds, output_tokens, preview_tokens)`；
   - 稳定错误 code：`invalid_execution_control`、`timed_out`、
     `output_limit_exceeded`、`budget_exhausted`、
     `context_budget_exhausted`。
4. `ToolExecutionPolicy.resolve()` 只做纯计算：缺省使用默认值，合法申请值被硬上限、模型窗口 8% 和
   调用方传入的 Batch 余额继续收紧；不能返回 0 或无限值。
5. 领域类型不依赖 ToolRegistry、Persistence 或 ReActAgent，保证可独立测试。

### 定向测试

- 旧 settings 未包含 `tool_execution` 时获得全部稳定默认值；
- 非法范围、布尔值、NaN/Infinity、反向上下限在配置加载期失败；
- Agent 申请小于默认值时采用申请值；大于硬上限时不执行并返回参数错误，不静默夹断；
- 模型窗口 8% 小于申请值时得到更小有效预算；
- Preview 目标为有效预算的 90%，但不少于最小结果外壳所需额度。

## 任务二：为 Tool Schema 注入并剥离 `_execution`

### 更新文件

- `apps/agent/src/agent_orchestration/tools/tool_checker.py`
- `apps/agent/src/agent_orchestration/tools/tool_registry.py`
- `apps/agent/src/agent_orchestration/tools/tool_executor.py`
- `apps/agent/src/agent_orchestration/tools/types.py`
- `apps/agent/test/agent_orchestration/tools/test_tools.py`
- `apps/agent/test/agent_orchestration/tools/builtin/test_builtin_tools.py`

### 开发内容

1. `ToolChecker` 将 `_execution` 设为框架保留参数：业务 Tool 原始 `properties`、`required` 或其他
   Schema 位置声明该名称时拒绝注册，并给出 Tool 名称和冲突位置。
2. `ToolExecutor.definitions()` 对深拷贝后的 ToolDefinition 注入 `_execution`，不得修改 Registry 中
   的原始 Schema，也不得让不同 Agent Run 共享可变字典。
3. `_execution` Schema 使用任务一的默认值和硬上限，描述中明确“申请会被 Harness 裁决”。
4. `ToolCall.arguments` 在可重放历史中保持模型原始输入，包括 `_execution`；只有传给：
   - `can_run_parallel()`；
   - Tool `invoke/ainvoke()`；
   - Tool 参数校验；
   时使用剥离 `_execution` 后的深拷贝。
5. 增加内部 `PreparedToolCall`，关联原 ToolCall、业务参数和有效预算。不要改
   `model_provider.types.ToolCall` 的 Provider 无关消息语义。
6. 控制参数非法时不调用业务 Tool，返回 `success=false` 的协议完整 Result；错误文本包含字段和允许
   范围，但不暴露内部配置对象。
7. Tool 自带 timeout 参数继续保留并生效；框架 timeout 是外层总时限，两者取先到者。
   `bash.timeout` 继续保留在 Agent 可见 Schema；同时出现 `_execution.timeout_seconds` 时取两者较小值，
   不把兼容输入判为错误。

### 定向测试

- 所有选中 Tool 的定义都注入独立 `_execution`，原 ToolDefinition 字节级不变；
- Registry 快照和 ObservableToolExecutor 包装后仍保留控制参数；
- 业务 Tool 收不到 `_execution`，`can_run_parallel()` 也只看到业务参数；
- 原 Assistant Tool Call 历史仍保留 `_execution`；
- Tool 自己声明保留字段时注册失败；
- 同时传框架与 Tool 自带 timeout 时取较小值；未知控制字段或非法类型时 Tool 未启动且结果配对完整。

## 任务三：实现统一 Timeout 与执行终态

### 更新文件

- `apps/agent/src/agent_orchestration/tools/tool_executor.py`
- `apps/agent/src/agent_orchestration/tools/types.py`
- `apps/agent/src/agent_orchestration/tools/builtin/bash_tool.py`
- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_tool_executor.py`
- `apps/agent/test/agent_orchestration/tools/test_tools.py`
- `apps/agent/test/agent_orchestration/tools/builtin/test_builtin_tools.py`
- `apps/agent/test/agent_orchestration/hooks/wrappers/test_observable_wrappers.py`

### 开发内容

1. `ToolExecutionResult` 增加不参与 `as_dict()` 的内部元数据字段，例如：
   - `disposition`: `completed | failed | timed_out | cancelled_before_start | cancelled`；
   - `budget`: 有效 timeout、输出预算、耗时；
   - `result_file`: 后续任务补充的 Tool Result 文件信息。
   现有模型可见 `success/output/error/images` 外壳保持兼容。
2. 异步 Tool 使用 `asyncio.timeout()` 或 `wait_for()` 包住实际调用；超时后取消 Task，并等待固定、
   短小的清理宽限期。确认 Task 已结束后返回 `timed_out`。
3. 继承 `BaseTool.ainvoke`、实际通过线程执行的同步 Tool 不能安全强杀，本期不在 timeout 到达后提前
   返回。ToolExecutor 记录 deadline 已到但继续等待真实结束；需要确定性 timeout 的 Tool 必须先改造成
   可取消异步实现或独立进程实现。
4. 直接同步 `execute()` 保持现有同步语义；不为了统一 timeout 在事件循环中嵌套 `asyncio.run()`。
   ReAct 的 sync/stream 路径继续走同步 Tool API，async/astream 路径走异步 Tool API。通过共享的
   参数裁决、Result Guard 和 Batch finalize 保证输出一致，不强求执行机制相同。
5. 重构 `BashTool`：
   - 同步和异步实现统一使用独立进程组；
   - timeout 时 TERM 整组，宽限期后 KILL；
   - 取消路径复用同一收束函数；
   - `stderr` 仍进入原输出结构，错误文本不重复携带无界 stderr。
6. ObservableToolExecutor 的 after/error Hook 增加 disposition、effective timeout 和 duration；不把
   `_execution` 当业务参数展示。
7. Timeout 或取消返回后，ReActAgent 仍为对应 Tool Call 写入一个 Tool Message，保持历史合法。
8. 审计 Tool 内部 timeout：
   - MCP 当前 Client 固定 120 秒，改为每次调用接收 effective timeout，或保证内部 timeout 不短于外层；
   - 其他 HTTP/SDK Tool 保留自身 timeout，并与外层取先到者；
   - Tool 内层先超时时保留其原错误语义，外层先超时时使用统一 `tool_timed_out`。

### 定向测试

- 异步 Tool 在默认 120 秒和申请值上使用正确 deadline；测试使用毫秒级配置，不真实等待；
- 可取消异步 Tool 超时后完成清理并返回 `timed_out`；
- 线程 Tool 到达 deadline 后不提前返回，真实完成后结果只写入一次；
- MCP/HTTP 内层 timeout 与 effective timeout 一致或更短，Agent 申请更长时不会被隐藏的固定默认值误导；
- Bash 子进程和孙进程都被终止；timeout/Cancel 不遗留监听端口或输出管道；
- sync/async/stream/astream 四个 ReAct 入口得到相同 disposition 和 Tool Message；
- Hook 不泄漏控制参数中的未来敏感字段。

## 任务四：增加 Session ToolResultStore

### 新增文件

- `apps/agent/src/agent_orchestration/tools/result_store.py`
- `apps/agent/test/agent_orchestration/tools/test_result_store.py`

### 更新文件

- `apps/agent/src/agent_orchestration/plugins/persistence/path_resolver.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/runtime.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/factory.py`
- `apps/agent/src/agent_orchestration/plugins/agent/factory.py`
- `apps/agent/src/agent_orchestration/agent_factory.py`
- `apps/agent/test/agent_orchestration/plugins/persistence/test_images.py`
- `apps/agent/test/agent_orchestration/plugins/agent/test_plugin.py`
- `apps/agent/test/agent_orchestration/test_agent_factory.py`

### 开发内容

1. `DataPathResolver` 增加：

   ```text
   tool_results_dir(identity, task_id)
   ```

   并复用现有安全 ID 校验。
2. `ToolResultStore` 绑定一个固定 SessionIdentity：
   - 输入为 task_id、tool_call_id 和稳定序列化文本；
   - 文件名使用安全化 Call ID + 短 SHA-256，避免路径穿越和碰撞；
   - 写入 `<name>.tmp`，flush/fsync 后原子 replace；
   - 目录强制 0700，文件强制 0600；
   - 写入后校验实际字节数；
   - 返回真实绝对 Path、原始/保存字节数和 `complete`。
3. 第一阶段不计算 Session 累计目录大小，不做累计配额、并发预留、TTL 或自动清理；这些与
   Session archive/delete 一起设计。
4. 稳定序列化规则：
   - `ToolExecutionResult.as_dict()` 中的 JSON 兼容值使用 `json.dumps(indent=2, ensure_ascii=False,
     sort_keys=False, default=str)`；
   - 字符串 output 仍保存完整 Result 外壳，不能只保存内容字段；
   - 统一换行和 UTF-8 `errors=replace`，保证 `wc/rg/sed` 可读。
5. 单个 Tool Result 文件超过 16 MiB 时只写允许范围，并返回 `complete=false`；通用 Result 标记完整
   内容未保存。
   Bash 在任务七改为采集阶段即停止，能产生更准确的 `output_limit_exceeded`。
6. 通过 Persistence Capability 将 Store 注入 AgentFactory/ToolExecutor；ToolExecutor 不自行拼接
   `$ICARUS_DATA_DIR`。`snapshot()` 共享同一个 Session Store 实例。
7. 不新增 `.meta.json`。路径、字节数、Token、complete、失败原因通过 Tool Result 内部元数据进入 Hook/
   Trace。Session unload 不清理 Tool Result 文件。

### 定向测试

- 不安全 task/call ID 不逃逸目录，重复 ID 生成稳定、无覆盖的文件；
- 正常 JSON 格式化为 UTF-8 `.txt`，字符串和 Unicode 可由 `sed/rg/wc` 读取；
- 原子写入、0700/0600、写入大小校验和失败清理；
- 16 MiB 单文件边界；
- Session unload/reload 后 Tool Result 文件路径仍可读；不同 Session 不共享目录。

## 任务五：实现 TokenCounter 与结构化 Result Renderer

### 新增文件

- `apps/agent/src/agent_orchestration/tools/result_budget.py`
- `apps/agent/test/agent_orchestration/tools/test_result_budget.py`

### 更新文件

- `apps/agent/src/agent_orchestration/tools/types.py`
- `apps/agent/src/agent_orchestration/tools/tool_executor.py`
- `apps/agent/src/agent_orchestration/agent_factory.py`
- `apps/agent/src/model_provider/base_llm.py`
- `apps/agent/src/model_provider/impl/openai_llm.py`
- `apps/agent/src/model_provider/impl/anthropic_llm.py`
- 对应 Provider 与 AgentFactory 测试

### 开发内容

1. 定义 `ToolResultTokenCounter` 协议，输入为最终稳定序列化文本；第一阶段使用与 Provider 无关的
   保守 fallback，并保留之后由 LLMFactory/Adapter 注入模型对应计数器的接口：

   ```text
   max(Unicode 字符数, ceil(UTF-8 字节数 / 3))
   ```

   将来注入的 Tokenizer 异常时回退，不能跳过预算。
2. `serialize_tool_result()` 成为唯一稳定序列化入口，同时供：
   - Token 计量；
   - ToolResultStore；
   - ReActAgent Tool Message。
   未超预算结果必须与当前 `json.dumps(result.as_dict(), ensure_ascii=False, default=str)` 语义兼容。
3. `ToolResultRenderer.render(result, budget, stored_file)` 是纯函数，返回新的 Result，不修改原对象：
   - 整个序列化 Result 未超有效预算：原样返回；
   - 超限：从原值生成 Preview，并在 output 中加入省略标记；
   - `success/error/images` 和内部元数据保持不变；错误字符串本身超限时也按字符串规则处理。
4. 类型规则：
   - 字符串：50/50 Head/Tail，中间插入隐藏 Token 数、完整路径或“未保存”；
   - 数组：累计 Token 选择前后完整元素，中间插入占位对象；
   - 对象：保持插入顺序，累计 Token 选择前后字段，中间插入无冲突占位字段；
   - 大型头尾节点递归处理；设固定最大深度，超过后使用同类型最小占位；
   - 数字、布尔和 null 原样保留。
5. `_icarus_omitted` 冲突时顺序尝试 `_icarus_omitted_2`、`_3`；数组扫描现有对象元素中的同名
   字段后选择一个统一无冲突名称。
6. 每轮从未裁剪原值按更小内容额度重新生成、序列化、重新计量。禁止在已有 Preview 上嵌套裁剪。
   使用单调的二分/收缩搜索，并设置最大迭代次数。
7. 有效预算 90% 用于初始 Preview 目标；最终整个序列化 Result 必须不超过 100% 硬预算。若同类型
   最小 Preview 仍超限，返回最小 Tool Result 外壳并标记 `context_budget_exhausted`。
8. 未超预算不写 Tool Result 文件；超预算先写文件，再生成包含有效路径的 Preview。写入失败或内容不完整
   时使用不同省略提示，不产生悬空引用或“完整结果”承诺。

### 定向测试

- 刚好等于预算原样返回，超过 1 Token 才裁剪；
- ASCII、中文、emoji、JSON 转义和 tokenizer 异常的计量；
- 长字符串 50/50、单侧余额转移、短预算最小占位；
- 一万个小数组元素和一万个短对象字段按累计 Token 裁剪；
- 单个巨大嵌套元素递归裁剪，最大深度确定降级；
- 字段顺序、元素顺序和原对象不变；占位字段冲突自动避让；
- Tool Result 文件成功、失败、不完整三种提示准确；
- 任意输出经 Renderer 后的最终 Token 永不超过预算，增加性质/参数化测试。

## 任务六：实现单 Tool Group 的 Batch Budget

### 更新文件

- `apps/agent/src/agent_orchestration/tools/tool_executor.py`
- `apps/agent/src/agent_orchestration/capability/react_agent.py`
- `apps/agent/src/agent_orchestration/capability/types.py`
- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_tool_executor.py`
- `apps/agent/test/agent_orchestration/tools/test_tools.py`
- `apps/agent/test/agent_orchestration/capability/test_react_agent.py`
- `apps/agent/test/agent_orchestration/capability/test_react_agent_stream.py`
- `apps/agent/test/agent_orchestration/hooks/wrappers/test_observable_wrappers.py`

### 开发内容

1. 明确预算对象是一个 Assistant Message 中声明的完整 Tool Group，而不是 ToolExecutor 为串并行安全
   拆出的单个执行 batch。所有子 batch 完成后统一裁决，总额度 16K Token。
2. 模型一次声明超过 8 个 Tool Call：
   - 只执行前 8 个；
   - 剩余调用返回 `success=false`、`budget_exhausted`；
   - 仍按原 Tool Call 顺序生成全部 Tool Message。
3. Executor 返回原始 Result 与每个调用申请的最大输出预算；`finalize_group()` 使用 water-filling：
   - 小结果能完整放下时优先原样保留；
   - 所有需要 Preview 的结果至少分配 512 Token；
   - 剩余预算在大型结果间均分，并受各自有效单结果上限约束；
   - 结果超限时调用任务五 Renderer 和任务四 ToolResultStore。
4. 对最终 Tool Message 文本而不是仅 Result output 重新计量。总量仍超 16K 时同步收缩最大的 Preview，
   直到满足预算或进入最小外壳。
5. 如果全部 Tool Call 的最小结果外壳仍无法放入 16K，则不执行该 Tool Group，回退到本 Group
   之前的安全检查点，并以 `context_budget_exhausted` 结束 Task；不得写入部分 Tool Group。
6. 将四个 ReAct 入口的重复 Tool Group 执行和写回整理为共享 helper，避免同步、异步、流式和
   非流式出现不同预算行为。
7. `AgentToolCompletedEvent.result` 使用模型可见、预算后的 Result，保证 UI/RuntimeUpdate 与后续消息
   一致；Blackboard 保存相同 Preview 与文件路径。原始长正文只存在 Session Tool Result 文件中；
   “完整历史”指消息与 Tool 配对协议完整，不表示所有正文都内联。内部 Trace 同时记录原始大小、
   有效预算、Preview 大小和文件路径。
8. Renderer 返回 `context_budget_exhausted` 时，先写入所有匹配 Tool Result 闭合 Group，再由 Harness
   终止下一次模型请求并产生明确 Task Error；不得把部分 Group 提交为安全检查点。

### 定向测试

- 单个小结果不变；一个大结果外置；多个中等结果累计触发 Batch 预算；
- 大小混合时小结果完整，大结果获得均衡额度；
- Agent 为不同调用申请不同预算时不突破各自上限；
- 并发乱序完成后预算计算和写回仍按原 Tool Call 顺序；
- 9 个以上 Tool Call 只有允许数量实际执行，但所有调用获得 Tool Result；
- 最小外壳不足、Tool Result 文件写入失败和 Renderer 不可收敛时确定性失败；
- 四个 ReAct 入口的消息、事件、Hook 和安全检查点一致。

## 任务七：让 Bash 在采集阶段受硬上限保护

### 更新文件

- `apps/agent/src/agent_orchestration/tools/builtin/bash_tool.py`
- `apps/agent/test/agent_orchestration/tools/builtin/test_builtin_tools.py`

### 开发内容

1. 不再使用无界 `subprocess.run(capture_output=True)` 或 `communicate()` 一次性收集所有输出。
2. 同步/异步路径共享一个进程执行器：
   - stdout/stderr 增量写入本次 Tool 的临时采集文件；
   - 内存仅保留 Renderer 需要的有限 Head/Tail；
   - 合计输出超过 16 MiB 时终止整个进程组；
   - 等待 TERM 宽限期，必要时 KILL；
   - 返回 `output_limit_exceeded`，Tool Result 文件标记 `complete=false`。
3. 命令退出未超限时继续保持现有 output：

   ```json
   {"exit_code": 0, "stdout": "...", "stderr": "..."}
   ```

4. ToolExecutor 最终仍负责 Token 预算和 Session Tool Result 文件；Bash 不自行实现第二套 Preview。采集文件
   通过明确内部结果交给 Store 原子落入 Session，失败时清理临时文件。
5. Agent 可见顶层 `timeout` 继续兼容；与 `_execution.timeout_seconds` 同时存在时取较小值。

### 定向测试

- stdout、stderr、混合输出和非零退出码保持现有结构；
- 默认 timeout、Agent 申请 timeout、取消和 TERM→KILL 路径；
- 子进程与孙进程不会在 timeout 后继续写入；
- 16 MiB 边界前正常完成，越界后及时停止且不无限占用内存；
- 临时采集文件在成功、失败、取消和 Store 失败后都被清理。

## 任务八：收敛 List 与 Read 分页

### 新增文件

- `apps/agent/src/agent_orchestration/tools/pagination.py`
- `apps/agent/test/agent_orchestration/tools/test_pagination.py`

### 更新文件

- `apps/agent/src/agent_orchestration/tools/builtin/read_tool.py`
- `apps/agent/src/agent_orchestration/plugins/mcp/tools.py`
- `apps/agent/src/agent_orchestration/plugins/mcp/catalog.py`
- `apps/agent/src/agent_orchestration/plugins/skill/tools.py`
- `apps/agent/src/agent_orchestration/plugins/skill/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/knowledge/tools.py`
- `apps/agent/src/agent_orchestration/plugins/knowledge/plugin.py`
- 对应 Builtin、MCP、Skill、Knowledge 测试

### 开发内容

1. 增加共享分页校验 Helper，统一拒绝 bool、0、负数和超过硬上限的值，不静默扩大或缩小 Agent
   请求。
2. `read` 增加 `offset`（1-based）和 `limit`：
   - 默认 200 行；
   - 最大 2,000 行；
   - 返回原有 `path/content`，并增加 `has_more`、`next_offset` 和可低成本得到的总行数；
   - 使用逐行读取，不能为返回一页先把整个超大文件加载进内存；
   - Tool Result `.txt` 与普通 UTF-8 文件使用同一入口。
3. MCP `mcp_tool_list` 已有 `page/page_size`：
   - 默认统一为 50、最大 200；
   - 保留 `page` 兼容现有 page_num 语义；
   - 返回 `has_more` 和下一页信息；
   - 搜索 `limit` 也使用共享上限。
4. `skills_list` 当前返回完整 Catalog。Catalog 已在本地内存中且排序稳定，为其增加
   `page_num/page_size`，按已有稳定顺序切片；`skill_search` 继续使用相关性排序，但增加最大 200 的
   `limit`，不伪装成分页遍历。
5. `knowledge_list` 当前无参数且可能返回整个库。本期只改 Icarus Agent 侧：
   - OpenKB Adapter 继续调用现有 `/api/v1/list` 获取全量响应；
   - Icarus 在稳定的原始顺序上使用 `page_num/page_size` 切片，并返回各分类的分页结果和 `has_more`；
   - 该改造限制进入模型的内容，但不减少 OpenKB 到 Agent 的网络与内存开销；这一限制明确留给后续
     OpenKB 服务端分页改造。
6. Memory Recall 已有 `top_k<=20`，不改成通用 List 分页；它属于相关性检索而不是遍历。
   `blackboard_list` 只枚举固定的已注册 Region，也不增加分页。
7. 所有分页结果继续进入 Result Guard；即使一页仍超预算，也按统一 Tool Result 文件/Preview 处理。
8. Cursor 不推进和重复页面检测留给后续无进展预算，本期只保证协议字段正确。

### 定向测试

- `read` 默认、边界、末页、空文件、Unicode、超大单行和非法参数；
- `read` 不全量读取大文件，并返回可继续的 offset；
- MCP list/search 默认值和硬上限统一，旧 page 参数兼容；
- Skills Catalog 使用稳定顺序分页，搜索 limit 受到共享硬上限；
- Knowledge Agent 侧分页在稳定顺序下不重不漏，并明确验证 Adapter 当前仍只发起一次全量 `/list`；
- 一页本身超预算时仍可外置并提示 Agent 使用有界 `sed/rg/wc`。

## 任务九：观测、文档与最终验证

### 更新文件

- `apps/agent/src/agent_orchestration/hooks/wrappers/observable_tool_executor.py`
- `apps/agent/src/agent_orchestration/plugins/runtime_update/plugin.py`（仅当现有事件不足以表达致命预算终态）
- `apps/agent/docs/arch/tool-execution-guard-design.md`
- `apps/agent/docs/arch/plugin-event-flow-current-state.md`
- `docs/todo/agent-core.md`
- 相关 Trace/RuntimeUpdate 测试

### 开发内容

1. Tool Hook/Trace 记录非敏感结构化数据：
   - requested/effective timeout；
   - requested/effective/visible/original Token；
   - 原始/保存字节数；
   - timeout/output/context disposition；
   - Tool Result file path、complete 和写入失败 code；
   - Batch 原始/可见 Token 与执行/拒绝数量。
2. Trace 不写重复的完整 Tool Result 正文；原始长正文只在 Session Tool Result 文件中，合法消息历史
   保存预算后的 Preview 与文件路径。
3. `context_budget_exhausted` 使用现有 TaskErrorEvent，标记 fatal，并携带最近协议完整检查点；不新增
   第二套 Task 终态。
4. 更新 TODO：仅在单结果、Batch、统一 Tool 接入和分页真实完成后勾选对应项；Active Run、Request
   Assembler、循环检测继续保持未完成。
5. 清理设计与实现中所有字段优先保留规则、Tool-specific summarizer、全局 TTL 外置目录和专用读取
   Tool 残留。

### 验证命令

按依赖顺序执行，失败时只修复本功能影响范围：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/model_config/test_config_loader.py \
  apps/agent/test/agent_orchestration/tools/test_execution_policy.py \
  apps/agent/test/agent_orchestration/tools/test_pagination.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/tools/test_result_store.py \
  apps/agent/test/agent_orchestration/tools/test_result_budget.py \
  apps/agent/test/agent_orchestration/tools/test_tools.py \
  apps/agent/test/agent_orchestration/tools/builtin/test_builtin_tools.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/capability/test_react_agent.py \
  apps/agent/test/agent_orchestration/capability/test_react_agent_stream.py \
  apps/agent/test/agent_orchestration/hooks/wrappers/test_observable_wrappers.py \
  apps/agent/test/agent_orchestration/plugins/persistence/test_trace_integration.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/mcp/test_tools.py \
  apps/agent/test/agent_orchestration/plugins/knowledge/test_tools.py -q

make test-agent
make test-gateway
make test-tui
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test
git diff --check
```

## 完成标准

- 任意 Agent 可见 Tool 都获得一致 `_execution` Schema，业务 Tool 无需适配即可受到默认保护；
- Agent 能申请更小或范围内更大的 timeout/output budget，但无法关闭或突破硬上限；
- Tool timeout、取消、拒绝和未知执行状态均产生一一匹配的 Tool Result；
- 任何最终模型可见 Tool Result 都不超过其有效单结果预算；任何 Tool Group 都不超过 Batch 预算；
- 未超预算结果保持现有输出，超预算结果保持原类型/顺序并包含准确省略信息；
- 大量小 JSON 字段或元素累计超限时也能收敛到预算内，且不覆盖业务字段；
- Tool Result 文件只属于当前 Session，路径可供 `sed/rg/wc` 使用，写入失败时无悬空引用；
- 超过 8 个 Tool Call 且最小结果外壳可容纳时，只执行前 8 个并为其余调用生成合法拒绝结果；
  若整个 Group 的最小外壳也无法容纳，则在执行前回退并以 `context_budget_exhausted` 终止；
- `read` 和 List Tool 不能无界返回，分页结果仍经过最终 Result Guard；
- Agent、Gateway、TUI 全量测试、compileall、Manifest 校验和 `git diff --check` 全部通过。
