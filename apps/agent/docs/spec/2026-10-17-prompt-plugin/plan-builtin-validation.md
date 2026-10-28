# 内置插件贡献｜增量验证

> 2026-10-17。**后续修订：** 六段稳定业务指导已从每任务 `prompt.contribution` 迁移到 `session` 静态层；
> 下方保留最初增量记录，当前实现以新增“Session 静态迁移”一节和 [arch.md](arch.md) 为准。

> 2026-10-17，新增于同一开发周期的Prompt实现之上；用户授权接入合理插件指导并精简system。原功能验证见[plan-validation.md](plan-validation.md)。

## 本轮交付

memory/skill/process/knowledge/mcp/builtin-tools六个插件通过真实input→prompt.contribution参与准备，每个插件独立prompts.py。
Memory指导先发、召回/Region/ACK不变；Skill增加有界目录与写开关事实，生成Agent/Job通知不变；
Process只给使用规则；Knowledge不自动RAG；MCP不主动远端枚举，无server发空completed；基础工具保持原schema。

基础system1110→472字符/保守token（减少57.5%），3208→1374 UTF-8 bytes（减少57.2%）。
记忆规则迁到Memory贡献，通用身份、安全、事实、验证、表达规则保留。整次请求包含新增动态指导，
因此**不能把base减少量宣称为整次调用tokens/费用同幅下降**；动态指导每task进入当前User Prompt并进入历史。

## RED/GREEN

- T1四个测试RED（没有Memory贡献/Skill拒绝输入/Process无handler）→4GREEN。
- 原Memory事件序列断言只增加一条Prompt贡献，保留原Region→Context→Region顺序；三插件原套件恢复。
- T2四测试RED（Knowledge拒绝事件/MCP、Builtin无handler）→4GREEN。
- 六插件套件328passed（10.13s）。
- T3 base1110>600及真实socket中system仍含记忆规则RED→精简后GREEN；2项Gateway E2E5.79s。
- scanner两项RED（完整正文IO、元数据无界）→GREEN，Skill全套110passed2.39s；只读取16KiB以内frontmatter。
- mypy一次entries空list缺注解已修正，make typecheck-agent238文件通过；没有ignore掩盖。

## 最新全套

- make test-agent：**902 passed in42.03s**。
- make test-gateway：**34 passed in7.51s**，1条既有Starlette/AnyIO弃用warning。
- make typecheck-agent：**238 files，no issues**。
- make check-package-agent：wheel构建、12manifest、临时venv安装/非repo cwd导入通过。
- compileall Agent/Gateway src/test、git diff --check通过。

## 真实网络验证

`test_builtin_plugin_guidance_reaches_user_context_not_base_system` 使用实际Uvicorn/TCP/WebSocket、生产12插件/Agent/SQLite/模型适配器：
将六个来源设为required；模型HTTP请求的user消息含六个section（builtin-tool-use/knowledge-use/mcp-use/memory-use/process-use/skill-discovery），
system不含记忆专属段且短于800字符。MCP配置存在但没有任何/mcp网络请求。创建实际SKILL.md验证目录名称进入user，正文BODY-NOT-AUTO-INJECTED不进入。
另一项外部persona贡献/私有指令/Gateway重启E2E继续通过。

本轮不重复真实远端调用；上一轮一次受限deepseek-flash验证见原记录。本轮无新客户端/协议字段，无DB/框架变化。

## 取舍与审查

- 仅接入六个有实际工具业务的插件，persistence/runtime-update/user-input/agent不强制成为贡献者；避免日志回灌/输出循环。
- 工具指导使用 `session` 静态层，不冒充 persona；稳定规则不每任务动态注入。
- Skill元数据收集以前读取全文件，本轮有界frontmatter是自动发现的必要IO边界；超过16KiB头部现在省略并warning，不截断语义。
- 旧base记忆断言移至Memory贡献测试，不删除记忆约束；旧文档草稿保留为历史设计，当前文案以base_prompt.py为准。
- 开发验收阶段未自动提交；之后按用户单独授权与前轮Prompt实现一并分层提交，不建分支/推送。

独立Opus增量审查完成：本轮范围未发现需修复的确定缺陷，无deferred minors。审查者另跑聚焦贡献/scanner15passed与六插件完整目录330passed10.15s；未将这些结果冒称主会话全套结果。
主会话最后真实socket2passed5.53s，optional-source/装配/边界9passed3.19s，文档链接/manifest/diff检查通过。

## Session 静态迁移

确认六段稳定指导不应每任务重复后，新增 `session` 静态层：

- `PromptLayer`/`STATIC_LAYERS` 增加 `session`，顺序为
  `base → workspace → session → persona → external`。
- 六个插件改为 `static_prompt_sections()` 返回 `layer="session"`，不再每任务发布
  `PromptContributionEvent`；Memory 的召回、Skill 的 Job 通知仍保留动态路径。
- 六个 manifest 删除 `prompt.contribution`/不再需要的 `input.user_input`/`contracts`；
  Prompt 的 `prompt.read`、Blackboard 的 required/fatal source 不变。
- 默认预算提高到 `max_prompt_tokens=12288`、`max_section_tokens=8192`，使当前仓库
  AGENTS.md（约6090保守token）可以进入 System；`default_settings.json` 同步。
- 实测 SessionRuntime System：约7970字符/10600 bytes，包含 base、workspace、
  五个已启用 session section（MCP 未配置时为空）和 external；两个连续任务的 System 相同，
  User Prompt 不含 `session/` 指导。
- 聚焦测试：契约/六插件/预算39 passed；应用/optional/边界17 passed；Gateway E2E 2 passed。
- 当前最终全量结果：Agent **903 passed in43.14s**、Gateway **34 passed in8.08s**、
  make typecheck-agent **238 files green**、wheel 隔离安装/发现通过、compileall 与
  git diff --check 通过。六段 session 指导不再出现在两个连续任务的 User Prompt。
- 真实 socket E2E：`test_prompt_e2e.py` **2 passed in5.69s**。
- 真实远端模型复验：`deepseek-flash`，临时数据，工具关闭，max_steps=1，
  max_output_tokens=128，SDK自动重试关闭；外部插件提供 session 静态码 `S23` 与
  task 动态码 `D47`。结果 **task_status=completed、both_plugin_codes_match=true、
  private_instructions_hidden=true、history_cursor=7**。这证明 session 静态指导和
  真正的动态贡献可以同时到达真实模型。
- 实际 SessionRuntime System：约7970字符/10600 bytes，包含完整 AGENTS.md、
  五个已启用 session section（MCP 未配置时为空）与 external；两个连续任务 System
  相同，User Prompt 不含 session 指导。

## 提交前复验

用户另行授权commit后重新执行：Agent **902 passed in43.99s**、Gateway **34 passed in7.86s**、
make typecheck-agent **238文件通过**，compileall通过。暂存检查发现新文件末尾额外空行，清理后再执行cached diff检查；
仅包含Agent/Gateway本功能实现、测试与所属文档，`.claude/`与忽略的临时证据不提交。
