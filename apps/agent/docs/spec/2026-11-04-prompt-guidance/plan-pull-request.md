# PR 提交材料

日期：2026-11-05。工作分支 `feat/agent`，目标 `Removel/Icarus:feature`，来源 `xilele777/Icarus:feat/agent`。本次提交前重新 fetch 并核实上游仍为 `c019e9a7b7f12e175e59c4928c1678aba448cc70`，当前分支包含该基线。用户已授权提交、推送并创建草稿 PR；真实模型行为验收仍待执行。

## PR 标题

`feat(agent): clarify prompt ownership and deliver English guidance on demand`

## PR 正文

Agent 原有插件 guidance 混有参数细则，部分工具单独加载时缺少前置条件和恢复说明；完整插件指导与动态人物事实还占用常驻 System。本次明确规则所有者，统一仓库维护的提示词为英文，并在需要能力时交付完整指导，减少无关任务的固定上下文。

### 改动

- 共通事实和授权约束归 base，guidance 负责场景及跨工具分工，具体前置条件、副作用、恢复方式和字段规则归工具 description/schema。补齐文件分页、Memory 纠错、Relationship 身份确认及方向、Process 跟进和 Knowledge 写入等说明。
- 仓库维护的 base、插件及工具指导、摘要、标题、JEV 判据和默认人格统一英文；回复与标题遵循用户语言。用户正文、工作区规范、自定义人格、Skill 和第三方内容保留原文。
- Memory/Relationship/Process/Knowledge/Skill 在 System 中保留短发现和必要触发规则，完整范围指导随 `tool_search.scope_guidance` 返回。裁剪后指导与单项工具定义均完整才进入 Run 加载状态；同步/异步均可恢复，加载不授予业务操作权限。core 保持不依赖应用插件。
- Skill 目录按调用发现，正文使用前读取；Persona 核心身份/边界保留 System，人物档案按任务刷新、演化观察按会话冻结并作为当前 User 背景交付。防御和重锚条件注入，WebSearch 当轮提示精简。
- 补齐缺失内容和预算诊断，保留 Persona invariants 与防御预算。生产 Prompt 总预算 12288、单节 8192、防御 200 不变；base 文本单测护栏因英文化由 600 调整为 1800。
- 提供 [104 条当前规则承接表](plan-context-rule-mapping.md)、[位置/时机与测量说明](plan-context-delivery.md)、可重复探针和 [手工验收任务](plan-manual-acceptance.md)。调研原文归入 Agent 专题，记录适用范围及证据限制。

### WSL 验证

原生 WSL Ubuntu 隔离副本，Linux Python 3.14.4，各应用独立 editable 环境，仅注释空 `.env`，未复制生产凭据或数据库：

- 最后受影响聚焦测试：45 passed；`make test-agent`：1304 passed；`make test-gateway`：50 passed。
- `make typecheck-agent`：273 源文件通过；两应用 compileall 和最终补丁空白检查通过。
- 提交前重新比对 55 个源码/测试文件，与已验收 WSL 副本在统一行尾后完全一致；104 条规则无缺失/重复，文档本地链接和探针/JSON 检查通过。

完整请求离线探针使用生产运行时/执行器/适配器与 loopback 脚本服务，0 个 Skill 的首请求 o200k 输入代理值 3562→2910（约 -18.3%），3 任务 5 次输入合计 20383→17608（约 -13.6%）。这是相对上一轮 `13743b0` 的离线比较，不是实际计费或所有场景的节省承诺；按需指导增加首次搜索成本，Persona 可选档案功能未纳入该成本夹具。

### 草稿状态与待验收

尚未运行收费模型、真实 JEV 或外部数据库行为评估。中文回复质量、工具选择、记忆纠错、身份确认与抗间接注入仍需按手工任务验收；CI 结果以远端实际运行状态为准。完成行为验收和评审后再转为正式 PR，不自动合并。

## 提交拆分与范围

已有 `272a922` 为首轮代码/测试，`13743b0` 为首轮文档；本轮 `077f9d2` 包含英文化、上下文交付及对应的 55 个源码/测试文件，另行提交本轮架构、承接表、过程、探针和测量文档。

排除用户 `.gitignore`、`pelican-bicycle.html`、同期 web-search 自主性文档，以及原始 `.log`、JUnit `.xml`、`wsl-*.json`、本地提交清单、虚拟环境、凭据和运行数据。原始证据仅在本地保留，精简测量 JSON 与可重复探针随专题提交。
