# Builtin Prompt Contributions Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline; all edits remain uncommitted.

**Goal:** 将业务指导归还现有插件，经Prompt注入，并精简基础System Prompt。
**当前状态（2026-10-17）：** 实施、回归与独立增量审查完成；结果见[plan-builtin-validation.md](plan-builtin-validation.md)。
> **后续修订（2026-10-17）：** 本文记录最初“每任务动态贡献”的增量计划。随后确认稳定业务指导不应每任务重复，已迁到 `session` 静态层并进入 System；当前实现与验证以 [arch.md](arch.md)、[plan-builtin-validation.md](plan-builtin-validation.md) 为准。

**Architecture:** memory/skill/process/knowledge/mcp/builtin-tools的稳定指导通过 `static_prompt_sections()` 进入 `session` 静态层；真正随任务变化的内容才使用 `PromptContributionEvent`。业务数据路径保持原状。基础system只保留身份、授权、证据、工具和沟通约束。
**Tech Stack:** 现有Python/dataclass/asyncio/pytest与manifest contracts。
**Spec:** [功能设计](plan.md)、[当前实现](arch.md)；本轮用户授权“能加入的尝试加入，提高能力，system精简”。

## Global Constraints

- 不用静态persona槽塞工具/业务指导，不改ReAct/core，不增加抽象/mixin/配置开关/数据库字段。
- 稳定业务指导使用 `session` 静态层，不放进 persona，也不每任务动态注入；真正任务级内容才用动态层。
- Memory指导先于自动召回发送，不改变召回正文/Region/ACK，也不重复注入召回结果。
- Skill只给轻量目录（最多8项，单项超过256字符整项省略，总JSON<=1200字符，正文不预加载）；元数据明确为非可信数据。
- MCP无配置server发空completed；knowledge不自动RAG；process不自动回灌状态流；不迁移Job通知。
- 各handler保留原事件处理；调整accepts_event及manifest一致性。无内容仍发completed，失败发安全failed。
- 不建分支、提交、推送；保留前序未提交工作。

## Review Focus

1. 六插件manifest声明及accepts_event必须让真实总线路由到handler（T1/T3）。
2. Memory结果事件/ACK/门闩顺序保持，只增加一条独立指导贡献（T1）。
3. Skill扫描异常、空目录、大量/超大元数据不会挂住或污染Prompt（T1/T3）。
4. MCP无配置不宣称远端能力；工具/后台任务既有接口不变（T2）。
5. 精简后记忆安全/自然措辞由Memory贡献保留，缺插件时不再有该指导（T3）。

## Task 1：Memory、Skill、Process

Files：各自plugin.py/manifest.json，新prompts.py，对应test_prompt_contribution.py；迁移application/test_session_runtime.py的旧base记忆断言。

Interfaces：复用PromptSection/PromptContributionEvent；handler `on_prompt_input(source_plugin_id, event: UserInputEvent)` 返回事件tuple；Memory在原on_event先publish指导再启动召回。Skill允许UserInput但保留Agent ACK来源校验，catalog.list_skills在线程读取；异常安全failed。

- [ ] 写RED：Memory指导+旧召回存在且不混正文；Skill只含8项有界元数据、不读正文、异常failed、权限条件；Process只给指导、不创建进程。
- [ ] 运行聚焦文件确认行为失败。
- [ ] 加插件专属文案和handler、更新manifest contracts/published/consumed，不复制基座注入代码。
- [ ] 运行三插件完整目录，原测试适应新增独立贡献但保留业务断言。

## Task 2：Knowledge、MCP、Builtin-tools

Files：各自plugin.py/manifest.json，新prompts.py及test_prompt_contribution.py。

- [ ] 写RED：Knowledge贡献检索/引用规则而不查询后端；MCP按有配置/空配置指导或空completed；Builtin指导真实read/write/insert/bash边界。
- [ ] 调整Knowledge accepts_event，让UserInput进入；MCP按client_manager.configs判断server配置。
- [ ] handler返回轻量contributor指导，更新manifest，跑三个插件目录。

## Task 3：精简base、真实整合与验证

Files：prompt/base_prompt.py、应用旧提示词断言、Gateway test_prompt_e2e.py及现状/验证文档。

- [ ] RED：base不再携带记忆专属机制，通用规则保留；真实模型请求User Prompt含各已启用业务指导，system更短且无记忆专属段。
- [ ] base缩至约400–600中文字符；转移旧记忆安全和表达断言到Memory贡献测试，不删除行为。
- [ ] 真实Gateway socket+重启+外部插件E2E，assert所有6来源（MCP有配置时）的指导出现在正确user位置；MCP空配置无指导但不超时。
- [ ] make test-agent / make test-gateway / make typecheck-agent / make check-package-agent / compileall / diff。
- [ ] 一次独立聚焦审查，严重项TDD修复，文档记录实际结果、提示词长度变化。远端调用不是默认每轮都重复，若执行保持128-token/临时数据/工具关闭。

提交只在用户另行要求时进行。
