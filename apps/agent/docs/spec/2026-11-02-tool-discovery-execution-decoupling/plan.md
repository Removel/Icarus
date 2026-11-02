# 工具发现与执行解耦设计及实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 用户要求先整理文档，不自动实施或派发工作流。

**Goal:** 将工具描述发现与执行资格分离，已授权注册工具不因本轮未search被拒绝。

**Architecture:** tool_search继续提供功能面目录/完整描述，tool_execute使用当前会话真实registry及完整ToolExecutor链校验执行。移除loaded_tools执行门禁，不扩大registry或allowlist；同步/异步一致。

**Tech Stack:** Python、既有ToolRegistry/ToolSurfaceCatalog/ToolExecutor、pytest。

**Spec:** 本文§1设计；Argus消费者见[适配设计](../2026-11-02-decision-script-atomic-commit/plan-design.md)。本feature独立于Argus，原[当前lazy工具架构](../2026-10-21-tool-surface-lazy-loading/arch.md)保留为当前实现依据，不提前修改为新行为。

## Global Constraints

- 描述加载不是授权；当前会话registry/可信allowlist才是执行范围。
- 不绕过参数校验、取消、超时、结果budget/transform、Hook已声明值传递点或完整executor链。
- 同步与异步行为一致；不以Argus专用例外修通用机制。
- 保留每Run模型tool_definitions固定快照与现有常驻工具面，不在Run中增删/重排工具定义；只移除loaded_tools执行门禁，不引入模型厂商分支。
- 不把动态Channel内容加进稳定System Prompt；只纠正通用工具机制说明。
- 不自动提交/推送；用户授权的后续提交不加Co-Authored-By。

## Review Focus

- 未search但已授权工具应成功，而被allowlist删除的同名工具仍失败。
- 历史描述已过期：按当前schema校验，返回明确错误，不执行旧参数。
- 工具注销/取消发生在搜索后：搜索结果不构成持续授权。
- 裁剪后的目录不应让已有权限失效，也不扩大权限。
- 直接dispatch与正常工具链应保持相同超时、参数、结果处理和审计记录。

---

## 1. 设计与范围

来源是用户在真实Argus验证报告中的批注：动态加载用于让Agent知道工具用法，不应规定只有本轮加载才可执行。当前`icarus/core/tools/tool_execute.py:175`硬拒绝name不在loaded_tools；tool_search/finalize_loaded_tools又把完整描述是否留在结果中当作加载凭据；通用Prompt和web_search提示每Run重新加载。

调整后：search是可选的描述发现操作；知道名称和参数即可执行当前已注册工具。registry仍在SessionRuntime绑定目录/executor前按可信allowlist裁剪；未注册、已注销、未授权工具不能调用。当前schema为准，不能因为历史中有完整描述就免校验。

第一版可保留tools_loaded元数据用于可观测/上下文统计，但名称和提示应明确这是description discovery，不再进入权限判断。无需为此扩展持久会话授权表，也无需把历史loaded列表永远保留。MCP自己的连接/授权和tool_ref生命周期不因本次通用工具解耦自动改变。

## 2. 文件职责

修改`apps/agent/src/icarus/core/tools/tool_execute.py`、`tool_search.py`：去掉加载门禁并保留schema/registry/executor；同步`tool_executor.py`和Agent Run代码中仅为该门禁传递的loaded_tools，不无关重构。提示修改`apps/agent/src/agent_app/plugins/prompt/base_prompt.py`、`plugins/web_search/plugin.py`及检索到的同义机制说明。测试在`apps/agent/test/icarus/core/tools/`镜像，消费者测试各归其插件。

## 3. Tasks

### Task T1：执行资格解耦

**Interfaces:** ToolExecuteTool.invoke/ainvoke现有参数调用方先保持兼容；loaded_tools不决定权限。ToolRegistry.get(name)、当前schema校验与ToolExecutor.execute/aexecute保持唯一实际分派入口。

- [ ] **Step 1：写失败测试。** 替换旧`test_tool_execute未加载时硬门拒绝并引导搜索`，新增`test_registered_tool_executes_without_search_sync_and_async`；`test_disallowed_or_unregistered_tool_still_fails_without_execution`；`test_current_schema_overrides_stale_description`。
- [ ] **Step 2：跑red。** `apps/agent/.venv/bin/python -m pytest apps/agent/test/icarus/core/tools/test_tool_execute.py -q`；预期旧门禁使未search合法工具失败。
- [ ] **Step 3：最小实现。** 删除loaded_tools前置拒绝，保留registry/schema和完整executor。按实际调用点清理只服务门禁的参数或保留无权含义兼容，不创建第二执行路径。
- [ ] **Step 4：green与边界。** 覆盖工具搜索后注销、allowlist裁掉、取消、超时、输入schema错误、result transform/budget、同步/异步行为；用fake工具计数断言失败没有执行副作用。运行tools目录。
- [ ] **Step 5：diff与授权后提交。** 建议「fix(agent): separate tool discovery from execution eligibility」。

### Task T2：提示/搜索语义与应用回归

**Interfaces:** search输出完整定义、scope目录和参数schema不变；说明改为“不了解工具时搜索，执行资格由当前会话决定”，不再说saved file或本轮重新搜索决定授权。

- [ ] **Step 1：写失败测试。** `test_search_documentation_does_not_grant_execution_rights`、`test_prompt_does_not_require_search_every_run`、`test_argus_second_round_can_execute_known_tool_without_search`；直接覆盖描述裁剪但registry工具仍合法，工具未注册则仍拒绝。
- [ ] **Step 2：跑red。** test_tool_search、prompt对应测试、Argus插件测试；准确定位当前文案/门禁失败。
- [ ] **Step 3：同步描述。** 更新tool_search/tool_execute定义、通用提示与web_search等插件的加载说明；保留完整目录和建议查schema，不把search删除为无用工具。同步与旧门禁绑定的测试期待。
- [ ] **Step 4：应用回归。** 最小文件→`make test-agent`→`make test-gateway`（Argus消费者）→type/compile/diff；记录真实失败身份，不修无关失败。
- [ ] **Step 5：实施后更新当前arch与授权后提交。** 计划不提前改写旧arch；建议单独文档提交说明授权/描述分离。

## 4. 验收与交付边界

T1独立可测，T2保证Agent实际指导与执行契约一致；Argus原子commit不是本feature前置，使用现有Argus工具也能复现并验证跨Run问题。本设计仅约束机制，不能让模型猜工具名/无视参数定义成为推荐用法。未实现前不得宣布工具加载问题已修复。
