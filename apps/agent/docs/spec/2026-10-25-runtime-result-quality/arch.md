# Agent 工具结果质量当前架构

## 位置与证据

本文件描述 2026-10-25 本轮已实现并审阅的应用本地变更，不描述尚在开发的语义会话路由。需求/执行步骤见 [plan](plan.md)。证据：`src/icarus/core/tools/{settings.py,execution_policy.py,tool_executor.py,result_budget.py}`、`src/agent_app/plugins/{builtin_tools,mcp,memory}/` 及镜像测试；两批修复均经独立审阅，宽映射/图片开销与联合 schema 诊断各修正一轮。

完整 Agent 测试最近一次为 1243 passed（隔离真实 dotenv），其中还包含会话持久化批次；这不是语义会话整体完成或真实 QQ 验收。工具本地模块最后专属验证为 MCP 101 passed、builtin/core tools 121 passed；后续源码变化须重新测试，不沿用此计数作永久门禁。

## 组件与边界

- `ToolExecutionSettings` 的单项绝对上限25000、并发绝对总上限100000，配置不能提高到其上；可设置更小值。框架小默认4000，应用打包/示例小默认3000。`ToolExecutionPolicy` 保留按模型窗口8%/15%的收紧，工具schema与执行校验共用settings。
- `ToolExecutor.finalize_group()` 先分配实际组预算。超限或超过短预览目标的大失败结果尝试写入现有 ToolResultStore，再调用 renderer；最终检查实际单项和组预算。
- `_execution.max_output_tokens` 是本地可见结果请求，不是通用远端业务分页。MCP 目录分页也不是成员表/消息业务结果分页。
- 所有变化保持在工具设施/所属插件，ReActAgent 没有 QQ/MCP 业务成功或 Memory 状态分支，Runtime/Hook 没有新增领域控制。

## 结果与渐进披露

原始结果保存先于省略渲染。默认文件保存上限仍为 16 MiB，并保留完整性；采集已不完整或文件不完整时不声称可读取完整原文。

成功结果沿用有界 head/tail。失败结果只有在完整原文已保存时，才偏好 1000 计量单位的短视图，保留有限错误、退出码/部分 stdout 证据和单一原文位置。1000 只是偏好，不是额外硬上限：图片/信封固定开销无法容纳时回到实际分配预算，不能把本来容纳得下的失败变成预算异常。宽字典按整个输出预算裁剪，不因保留所有标量而抹掉基本失败证据。

省略说明在模型可见结果中明确标记 truncated；完整内部 metadata 不被自动序列化。`original_tokens` 等是现有保守计量，不等于 provider usage。

Bash 非零退出且 stderr 空白时给 `exit_code=<n>, no stderr`。超时/采集上限仍使用原错误；失败不代表外部副作用回滚。

## 现有 read 的续读

同一个 read 工具提供两种独立范围：

- `offset/limit`：选择行页，返回 has_more/next_offset。
- `text_offset/max_chars`：在该行页内取 Unicode 字符片段，默认 2000、最大 20000，返回 truncated/next_text_offset。

先读完当前行页字符片段，再推进下一行页。字符串转义、并发预算等仍可导致工具展示再次截断；这种情况调小 max_chars 并重读相同输入偏移，不能以候选 next_text_offset 证明模型已消费未展示正文。没有另一个绕预算的 read_result，也不推荐 bash cat 整段工具原文。

## MCP 适配

- list/search 的 schema 默认值与真实执行一致，仍复用当前目录缓存。
- text 中完整 JSON 对象/数组只解码一层；有 structured_content 时，只有语义相等的 JSON 副本才去重。普通文字、不同数据及图片保留；不递归猜 data/messages。
- 去重在脱敏前比较，true 与 1 区分；输出继续脱敏，原 MCPCallResult 不被就地修改。这不等于另建了原始 MCP wire 存档。
- 参数错误最多八条有界诊断，保留缺失/未知字段和类型；anyOf/oneOf 以编号 alternative 表达分支，深度四层后安全降级。每条完整诊断最长 240 字符，不回显参数实例、enum 值或任意 jsonschema.message。
- success 继续表示 MCP 调用/协议及 isError；QQ 业务是否成功仍归下游适配，通用层不解释 retcode。

## Memory 展示

Memory region 保留 required_for_start，但 auto_expose=False。空/失败/超时状态不自动进入最终 User Prompt；有记忆事实仍通过 TaskContextInputEvent 进入动态上下文。显式 memory_recall 和 Blackboard 查询仍能读取诊断状态，不删除内部数据或伪装成功。

## 验证与非目标

镜像测试覆盖字符重组/EOF、默认预算可前进、更小片段重读、512 预算下失败证据、宽字典和图片固定开销、保存失败/不完整、同步/异步、MCP 混合内容/脱敏/联合 schema、实际 Blackboard 最终 prompt 和非空记忆注入。

测试环境通过 `PYTHON_DOTENV_DISABLED=1` 和移除继承模型密钥，避免真实根 .env 污染；没有为测试修改生产环境或跳过旧用例。Gateway 的既有 anyio 告警、TUI 的既有快照失败不属于本功能修复。

未实现通用结果字段投影、所有上游服务分页、同轮缓存/上下文垃圾回收、动态工具延迟加载、视觉输入、QQ 专用工具或发送模块。本文件不把这些延期项描述成已解决。
