# Session 创建指令｜Gateway 实现计划

> 状态：代码已实现，真实socket/重启与远端模型验证已执行；最终证据见 [Agent 验证记录](../../../../agent/docs/spec/2026-10-17-prompt-plugin/plan-validation.md)。
> 本文只负责 Gateway RPC 边界，不复制 Agent 的提示词装配/持久化方案。
> 前置：[Agent 功能设计](../../../../agent/docs/spec/2026-10-17-prompt-plugin/plan.md)；
> 依赖与执行总表：[Agent T8](../../../../agent/docs/spec/2026-10-17-prompt-plugin/plan-implementation.md)。

## 共享要求与非目标

- `session.create` 新增可选 `instructions: str | None`；仅创建时接受，进入 Agent 的静态 external 层。
- 原始 Unicode/空白保留；None 表示无记录，空字符串是显式空内容。
- Gateway 入口防护 + Agent 应用入口独立防护；超限报错，不截断。
- 现有 session.instructions 私有标记不进入公共更新/历史/wire。Gateway 不操作私有 DB 行。
- 公共 RuntimeUpdateModel、sequence/cursor、ConnectionHub/WebSocket 分发逻辑均不改；不新增客户端输入控件。
- submit/steer 等参数继续 extra=forbid，不允许中途传入/替换 instructions。

## 修改与接口

- Modify：`apps/gateway/src/protocol/methods.py`。
- Test：新 `apps/gateway/test/test_session_instructions.py`、现 `test_methods.py`、`test_app.py`、`test_runtime_e2e.py`。
- Agent consumes：`SessionApi.validate_session_instructions(instructions: str | None) -> None`（同步、读实际 prompt 配置、纯字符/字节验证）；`create_session(..., instructions: str | None=None)`；安全 `PromptError(code,message)`。
- 只在 CreateSessionParams 增加 `instructions: StrictStr | None=None`；不放入通用 SessionParams。
- `_session_create` 在调用应用创建前执行共享字符/UTF-8 bytes 保护，限额来自运行时实际 PluginConfig；校验失败返回 -32602，业务阶段返回 BUSINESS_ERROR、data.code=prompt_too_large 等安全 code。
- RequiredPluginError 包装的 PromptError：只沿已知异常 cause 查找可信类型，不把原始异常 str/输入正文返回。其他异常保持既有 Internal error。
- Pydantic 错误只返回安全参数说明，不能带 input/input_value 原文。

## TDD 步骤

- [ ] 写 `test_create_instructions_only_and_exact_roundtrip`：omitted/None/空/Unicode 空白原样转发，旧无参 API 行为不变。
- [ ] 写 `test_transport_limits_use_runtime_budget`：T=4、4ASCII通过/5失败、4中文通过/5失败、4emoji bytes失败；异常 surrogate、整数、数组、object 拒绝。失败时应用 create 调用数为0，错误不含哨兵正文。用 Pydantic StrictStr 避免输入类型强转。
- [ ] 写 `test_application_budget_error_is_safe`：Agent 独立抛出 PromptError/已知启动包装链，得到稳定业务 code；未知内部异常仍不泄漏。
- [ ] 写 `test_instructions_cannot_replace_existing_session`：重复 create 不变更原文；submit/steer/get 带 instructions（包括 None）拒绝。
- [ ] 用 Gateway 私有 venv 运行新文件，先看到预期行为 RED，再实现字段/调用/错误映射，运行 GREEN。必要时更新旧 RuntimeStub 以实现 Agent 新公开方法，不写运行期 hasattr 兼容代码。
- [ ] 扩展真实 TCP/WebSocket E2E：本地模型 fixture 捕获请求 system。lazy create→AgentRuntime 重启→首次 submit，有完整外部指令；所有结果/通知/history/status/list/title 不含私有标记/正文；公开记录 sequence 从1连续。
- [ ] 运行 `make test-gateway`；其后按 Agent T9 完整验收。测试只使用临时目录/本地 HTTP fixture，不用真实凭据/模型。

## 回滚与交接

Gateway 参数新增可向后兼容旧客户端；禁用新客户端参数不等于可把 Agent DB reader 直接退回不识别 sequence=0 的旧版本。
数据兼容按 Agent T9 处理，不删除真实记录。本文与 Agent 计划共同审阅后才开始编码；不自动提交或部署。
