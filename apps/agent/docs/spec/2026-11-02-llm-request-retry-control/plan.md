# Explicit LLM Request Retry Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 为现有公共 LLM factory 和适配器增加向后兼容的可选重试参数，使 Argus 单次发现兜底能够显式关闭 SDK 自动重试。

**Architecture:** 仅在 Agent 自有 `model_provider` 构造边界传递 `max_retries`。不修改 `BaseLLM`、ReActAgent、Plugin Runtime 或各模型的请求/响应转换；未传参数时保留 SDK 现有默认行为。Argus 的业务去重和最多一次意图仍由其自身 Store 管理。

**Tech Stack:** Python / pytest / 现有 OpenAI 与 Anthropic Python SDK。

**Spec:** 本文件“范围与接口”以及 [Argus 已确认的单次兜底设计](../../../../argus/docs/spec/2026-11-02-decision-script-atomic-commit/plan-discovery-fallback.md#已确认设计与新证据)。两部分可独立实施，因此按应用所有权拆分计划，不创建重复实现细节的根 spec。

**Status:** 可选重试参数与回归测试已实现；provider 聚焦51项、Agent整套1320项通过，typecheck280源文件无错误。同步/异步本地HTTP错误请求计数证明零重试；最终独立审阅并入[Argus实施记录](../../../../argus/docs/spec/2026-11-02-decision-script-atomic-commit/plan-discovery-fallback-execution.md)。

## Global Constraints

- 模型协议差异留在 `apps/agent/src/agent_app/model_provider/`，上层调用者不得访问 `_client` / `_async_client` 或直接分支构造供应商 SDK。
- 优先复用公共 `BaseLLM` / `LLMFactory` / `Message` / `TextPart` / `LLMResponse`，不新增重复配置模型。
- `max_retries=None` 保持现有行为，`max_retries=0` 必须真实到达同步与异步 SDK 客户端。
- sync/async 与 stream/astream 复用同一初始化策略，不出现异步关闭重试而同步仍默认重试的差异。
- 不切换模型、base_url、凭据、SDK 版本或 thinking 策略；不默认向现有调用者施加零重试。
- 不创建分支/worktree、不提交/推送；保留当前未提交改动。正式实施前核对 intended remote 的 feature 基线，所需 Git 变更另获授权。

## Review Focus

1. `0` 被 truthiness 回退到 SDK 默认：构造器/factory 测试必须断言精确传递 0。
2. 只关闭一种客户端重试：两个 SDK 的同步与异步客户端均断言参数一致。
3. 修改所有旧调用者的容错行为：未传或传 None 时不发送 SDK 重试覆盖参数。
4. 负数、布尔、字符串或浮点重试值：在创建客户端和发请求前明确拒绝。
5. 一次方法调用掩盖 SDK 多次上游请求：对可重试的本地 HTTP 错误计数，证明 zero-retry 模式只收到一次请求。

---

## 范围与接口

已有事实：`LLMFactory.create_llm(...)` 返回 `BaseLLM`；其公开单次异步接口是 `ainvoke(messages, tools=None)`，不是 generate。当前 OpenAILLM 与 AnthropicLLM 在初始化时创建 sync/async SDK 客户端，均未显式配置 max_retries，继承 SDK 默认值。

唯一 API 扩展为在以下现有签名末尾增加 `max_retries: int | None = None`：

- `agent_app.model_provider.factory.LLMFactory.create_llm(...) -> BaseLLM`
- `agent_app.model_provider.impl.openai_llm.OpenAILLM.__init__(...) -> None`
- `agent_app.model_provider.impl.anthropic_llm.AnthropicLLM.__init__(...) -> None`

非 None 时要求 `type(max_retries) is int and max_retries >= 0`，否则 ValueError。SDK 构造参数仅在非 None 时增加 `max_retries`。factory 未覆盖时不需要改变现有 mock 断言中的 kwargs 形状。

### Task 1: 可选请求重试策略与回归验证

**Files:**
- Modify: `apps/agent/src/agent_app/model_provider/factory.py`
- Modify: `apps/agent/src/agent_app/model_provider/impl/openai_llm.py`
- Modify: `apps/agent/src/agent_app/model_provider/impl/anthropic_llm.py`
- Modify: `apps/agent/test/agent_app/model_provider/test_factory.py`
- Modify: `apps/agent/test/agent_app/model_provider/impl/test_openai_llm.py`
- Modify: `apps/agent/test/agent_app/model_provider/impl/test_anthropic_llm.py`

**Interfaces:**
- Consumes: 现有 SDK 客户端公开构造参数 `max_retries`，正式写代码时按已安装 SDK 版本核对。
- Produces: `LLMFactory(config).create_llm(role=existing_model_name, max_retries=0)` 返回一份无隐式重试的现有 BaseLLM；Argus 可以只调用一次 `ainvoke`，且无需触碰私有成员。
- 不承诺服务端不重复执行请求，只保证当前 SDK 不自动补发；消息级 at-most-once 由 Argus 认领协议保证。

- [x] **Step 1: 写失败测试。** factory 两种协议分别覆盖 None、0、正整数，断言参数选择/传递正确；两个适配器分别覆盖 SDK 的 sync/async 构造参数。负数/True/字符串/小数输入均失败，构造无效参数时不得发请求或遗留半初始化客户端。
- [x] **Step 2: 运行并确认失败。** Run `apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/model_provider/test_factory.py apps/agent/test/agent_app/model_provider/impl/test_openai_llm.py apps/agent/test/agent_app/model_provider/impl/test_anthropic_llm.py -q`；新参数缺失导致失败，而非依赖/凭据问题。
- [x] **Step 3: 最小实现。** 扩展三个现有签名，验证可选非负整数，仅将非 None 的参数加入 SDK 构造。保持 invoke/ainvoke/stream/astream 的调用和转换实现不变；不新增 ConfigModel 字段或通用重试策略类。
- [x] **Step 4: 验证实际上游请求数。** 用本地 HTTP fixture 返回 500 或 429，分别让 zero-retry OpenAI/Anthropic adapter 执行 invoke/ainvoke，断言服务端每次只收到一个请求、方法正常抛出对应错误。无真实凭据、无付费调用；最后关闭同步和异步客户端。
- [x] **Step 5: 聚焦与全应用验证。** 先运行受影响 provider 文件，再 `make test-agent`，再 `make typecheck-agent`、`apps/agent/.venv/bin/python -m compileall -q apps/agent/src/agent_app/model_provider` 与 `git diff --check`。如发现不相关基线失败，按测试身份报告，不扩展修复范围。
- [x] **Step 6: 交付依赖。** 保持这些更改及测试属于 Agent 应用；由 Argus 计划 Task 2 消费新参数。无授权不执行任何 git add/commit/push，也不把仓库其他模型改为 zero-retry。
