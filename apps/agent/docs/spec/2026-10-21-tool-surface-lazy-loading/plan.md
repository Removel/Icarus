# Tool Surface Lazy Loading Implementation Plan｜工具面延迟加载实施计划

> 状态：Phase 0–4 的实现与文档已完成；实际验证结果见第 11 节。可选真实模型 smoke 未执行。
> 目标架构与实施前基线见 [arch.md](arch.md)；原始设计见
> [eager-scribbling-pony.md](docs/plan/eager-scribbling-pony.md)。

## 1. Goal and design

在默认 Session Agent 中把模型可见面固定为 9 个常驻 Tool，以 `tool_search` /
`tool_execute` 双桥按需发现和调用其他工具。整个 Run 复用单次冻结的 `tool_definitions`，加载过程只
更新 `_RunState.loaded_tools`，不重建模型工具数组。

范围仅限 [apps/agent/](apps/agent/)。实现必须保持 ReActAgent 无状态、core/runtime/app 依赖方向、
Provider 可插拔、MCP 动态通道独立，以及既有调用方的 `tools=None` 行为。

## 2. Baseline and constraints

- Phase 0 源码核对基线：`5e6ebd9`。
- 当前快照链路、四条 ReAct 路径、Registry 白名单语义、Runtime 装配点和 metadata 保留行为已按
  [arch.md](arch.md) 第 2 节列出的源码与测试核对。
- 不修改任何 manifest、既有 Session 数据、消息历史、MCP 动态 Catalog 或用户配置。
- 不创建分支、提交、amend、rebase 或 push，除非用户另行明确要求。
- 每个实现 Phase 先写最小失败行为测试，再做满足契约的最小实现；同步/异步路径必须一致。
- 验证按聚焦测试、Agent 全量测试、compile/diff 的顺序进行。既有失败按测试身份记录，不修复无关失败。

## 3. Phase 0：Spec 文档（SDD 先行）

### Files and interfaces

- 新建 [arch.md](arch.md) 与 [plan.md](plan.md)，目录日期固定为设计首次进入 Git 的
  `2026-10-21`。
- 更新 [Agent 编排基础架构](apps/agent/docs/spec/2026-08-15-agent-orchestration-foundation/arch.md)
  的工具暴露描述，明确“单次 Run 内冻结快照”。

### Steps

- [x] 核对当前工具注册、快照、Runtime 装配、MCP 和 metadata 链路。
- [x] 按“数组冻结 + 双桥”记录目标组件、状态所有权、数据流、错误和兼容边界。
- [x] 分开描述已验证现状与待实施设计，不把目标机制写成当前代码事实。
- [x] 在既有编排文档中补充冻结快照契约并链接本设计。
- [x] 记录后续约束：设计变化必须先更新本 spec，再实施对应代码。

本 Phase 不修改源码或测试；文档验收使用 `git diff --check`，代码测试从 Phase 1 开始。

## 4. Phase 1：核心 Catalog + 搜索/分发工具

### Files and interfaces

新增实现：

- [tool_surface.py](apps/agent/src/icarus/core/tools/tool_surface.py)：
  `ToolSurfaceCatalog.build(pairs, registry)`、不可变快照、常驻排除和 `search(scope)`。
- [tool_search.py](apps/agent/src/icarus/core/tools/tool_search.py)：
  `ToolSearchTool`、功能面目录、完整定义内联返回和 `tools_loaded` metadata。
- [tool_execute.py](apps/agent/src/icarus/core/tools/tool_execute.py)：
  `ToolExecuteTool`、`name: str` / `arguments: object`、硬门控、Schema 校验、unwrap 和全量
  Registry late-bind 接口。

新增测试：

- [test_tool_surface.py](apps/agent/test/icarus/core/tools/test_tool_surface.py)
- [test_tool_search.py](apps/agent/test/icarus/core/tools/test_tool_search.py)
- [test_tool_execute.py](apps/agent/test/icarus/core/tools/test_tool_execute.py)

### Steps

- [x] 先添加 Catalog 查组、常驻排除和冻结不可变测试，确认当前缺少实现。
- [x] 实现 `ToolSurfaceCatalog`；定义取 Registry 原始 Schema，不含 `_execution` 包装，不新建
  `search_scoring.py`。
- [x] 添加搜索参数校验、目录输出、整组召回、未命中目录和 `tools_loaded` metadata 测试。
- [x] 实现 `ToolSearchTool`；参数校验镜像 `_MCPTool` 风格，目录条目为
  `scope_id(N): tool1, tool2, ...`，description 直通，`can_run_parallel=True`。
- [x] 添加分发参数校验、未加载引导错误、已加载 unwrap、字段级 Schema 错误和 object 参数测试。
- [x] 实现 `ToolExecuteTool` 与 late-bind 接口；复用 MCP 已验证的 jsonschema 校验行为，但不得在
  core 中 import MCPPlugin。
- [x] 验证 unwrap 后目标工具的真实身份贯穿既有执行、finalize、metadata、预算和 Hook 链路。
- [x] 运行三个新增测试文件，并核对 sync/async 行为一致。

## 5. Phase 2：ReActAgent 加载名单循环

### Files and interfaces

- 修改 [react_agent.py](apps/agent/src/icarus/core/agents/react_agent.py)：
  `_RunState.loaded_tools: list[str]`，每 Run 初始化，保序去重。
- 修改 [test_react_agent.py](apps/agent/test/icarus/core/agents/test_react_agent.py) 与
  [test_react_agent_stream.py](apps/agent/test/icarus/core/agents/test_react_agent_stream.py)，复用
  现有 `QueueLLM` / `EchoTool` 和流式测试模式。

### Steps

- [x] 先添加四路径测试：搜索结果的加载名在 finalize 后并入名单，后续 `tool_execute` 才放行。
- [x] 在 `_RunState` 增加有序 `loaded_tools`；不新增 Agent 入口参数，不把 Run 状态保存到工具实例。
- [x] `invoke` / `ainvoke` 接收工具组 finalize 返回值，`stream` / `astream` 使用内联 finalize 结果，
  统一扫描 `metadata["tools_loaded"]`。
- [x] 新增名显式与可执行全集求交，再保序去重并入名单；不得依赖 `select` 的 skip-missing 行为代替
  这层求交。
- [x] 将当前 Run 名单接入 `tool_execute` 硬门控；加载只增不减，跨 Run 重置。
- [x] 保持 `tool_definitions` 在 `_create_run_state` 快照一次，四路径均不得重建或修改定义数组。
- [x] 覆盖未加载硬门、白名单求交、重复搜索去重、跨 Run 重搜，以及 `tools=None` 既有行为不变。
- [x] 运行两个 ReAct 测试文件，确认四路径模型调用始终收到同一冻结定义。

## 6. Phase 3：运行时装配（app 层接线）

### Files and interfaces

- 修改 [session_runtime.py](apps/agent/src/agent_app/application/session_runtime.py)。
- 将 [host.py](apps/agent/src/icarus/runtime/host.py) 的 `graph_snapshot` 生成前移到
  `before_ready` 之前，使 `_finalize_links` 能消费同一份图快照；其余 READY 顺序不变。
- 按实际装配接缝最小修改 [factory.py](apps/agent/src/agent_app/plugins/agent/factory.py)、
  [plugin.py](apps/agent/src/agent_app/plugins/agent/plugin.py) 和
  [config.py](apps/agent/src/agent_app/plugins/agent/config.py)；不得增加 config_model 可见性字段。
- 修改 [base_prompt.py](apps/agent/src/agent_app/plugins/prompt/base_prompt.py)，增加双桥静态引导。
- 修改 [test_session_runtime.py](apps/agent/test/agent_app/application/test_session_runtime.py) 和
  [test_plugin.py](apps/agent/test/agent_app/plugins/agent/test_plugin.py)。

### Steps

- [x] 先添加 Registry 名称、Catalog late-bind、目录定义和 AgentPlugin `tools=` 传参测试。
- [x] `SessionRuntime.__init__` 创建 Registry 后直接注册 `ToolSearchTool` 与 `ToolExecuteTool`；
  单一属主、无 manifest、freeze 前完成。
- [x] `_finalize_links` 从 `host.graph` 快照的 `(tool_name, plugin_id)` 对构建 Catalog，READY 前
  late-bind 给搜索工具，并把全量 Registry late-bind 给分发工具。
- [x] 在 READY 前把功能面目录写入 `tool_search` 定义；不得在 Run 中更新目录或定义。
- [x] AgentPlugin 调 `astream` 时传入硬编码常驻 9 元组；与可执行全集求交，未注册项不阻断 Run。
- [x] Base Prompt 增加两行静态引导：需要面外能力先 `tool_search`；已加载工具经
  `tool_execute(name, arguments)` 调用，新 Run 必须重新 search。
- [x] 保持 Skill Evolver 的 generation tools、manifest 和显式白名单行为不变，不增加专门 canary。
- [x] 运行 SessionRuntime 与 AgentPlugin 聚焦测试，再运行 Phase 1–2 测试排查接线回归。

## 7. Phase 4：文档收尾

### Files and interfaces

- 更新本目录 [arch.md](arch.md) 与 [plan.md](plan.md) 的实施状态和已验证行为。
- 检查 [agent-core.md](docs/todo/agent-core.md)，仅在存在工具暴露表述不一致时做最小对齐。

### Steps

- [x] 将 `arch.md` 中已落地内容改为当前源码/测试事实，保留尚未实现项的明确状态。
- [x] 记录实际测试结果、设计偏差和未执行的 optional smoke；不把计划完成写成验证通过。
- [x] 检查 spec、源码命名和实际 Tool 描述一致，不留已作废的“Run 中重建 definitions”方案。
- [x] 运行最终全量验收和 `git diff --check`。

## 8. Acceptance

Phase 1–3 的聚焦验收至少覆盖以下文件；可按当前 Phase 缩小到受影响文件：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/icarus/core/tools/test_tool_surface.py \
  apps/agent/test/icarus/core/tools/test_tool_search.py \
  apps/agent/test/icarus/core/tools/test_tool_execute.py \
  apps/agent/test/icarus/core/agents/test_react_agent.py \
  apps/agent/test/icarus/core/agents/test_react_agent_stream.py \
  apps/agent/test/agent_app/application/test_session_runtime.py \
  apps/agent/test/agent_app/plugins/agent/test_plugin.py -q
```

全量验收：

```bash
make test-agent
git diff --check
```

`make test-agent` 已包含 Agent 全量 pytest 和 `compileall`。保留原始退出码，分别记录测试失败、
环境缺失、未执行检查和既有失败；不得把依赖缺失或 collection error 当作预期行为失败。

有凭据且另行获得真实模型调用授权后，可做小范围 smoke，观察：

- 模型遇到未加载能力时是否主动 `tool_search`，并经 `tool_execute` 正确调用；
- `tool_execute` 参数错误率、重试次数、自愈率，以及转义/缺字段/类型错误的分布；
- contributor guidance 点名隐藏工具时是否引发困惑或失败尝试；
- 跨 Run 是否重复搜索同一功能面，是否尝试直接调用未声明工具。

真实 smoke 不暴露 Secret，不用本地 fixture 冒充真实模型结果。

## 9. Review Focus

- 定义数组单次快照、全程冻结；四路径不得触碰 definitions 重建。
- `loaded_tools` 保序去重，新增名显式与可执行全集求交，跨 Run 不继承。
- 默认白名单只作用于默认 Session Agent；`tools=None` 与 Skill Evolver 行为不变。
- Catalog 排除常驻 9 元组，搜索结果与常驻工具无交集。
- 功能面目录严格使用 `scope_id(N): tool1, tool2, ...`，不创造额外目录术语。
- 工具 description 直通且包含何时使用、注意事项和示例调用；渲染层不加工、不兜底补写。
- unwrap 后以目标工具真实身份走 ToolExecutor、finalize、metadata、预算和 Hook 链路。
- 硬门控错误包含下一步 `tool_search('<scope>')` 引导；Schema 错误包含字段、期望和实际信息。
- 工具与参数描述使用英文，流程引导按 Prompt 分层归属。
- sync/async 行为一致，metadata 不进入消息日志。

## 10. Rollback

- Phase 0 回滚只需在确认没有他人后续修改后移除本目录两份新文档，并恢复编排基础文档的新增段落。
- 实现 Phase 按本次逻辑文件和测试的可审阅补丁反向恢复，保留既有会话数据、配置和其他工作区改动。
- 不使用 `git reset --hard`、`git clean` 或覆盖式操作代替精确回滚；不自动执行任何 Git 历史变更。

## 11. 实施验证记录

验证日期：`2026-10-28`。当前 Windows 虚拟环境使用 `.venv/Scripts/python.exe`，而
`apps/agent/scripts/test.sh` 固定查找 `.venv/bin/python`，因此未直接运行 `make test-agent`；
使用同一虚拟环境直接执行其对应的 Agent 全量 pytest 与 compileall 检查。

### 聚焦验收

- Phase 1–3 的 core tools、ReActAgent 与 AgentPlugin 聚焦测试：`79 passed`。
- SessionRuntime 的 READY 前 Catalog / Registry 绑定与双桥注册聚焦测试：`3 passed`。
- `apps/agent/test/icarus/core/tools` 与 `apps/agent/test/icarus/core/agents`：
  `114 passed, 1 failed`；唯一失败为 `test_tool_result_store原子保存session文本并限制权限`，
  在 Windows 上断言 POSIX 文件权限，HEAD 基线同样失败。
- `compileall -q apps/agent/src apps/agent/test packages`：通过。
- `git diff --check`：通过；仅有既有文档的 CRLF/LF 提示，没有 whitespace error。

### Agent 全量与 HEAD 基线对照

两轮均使用同一虚拟环境，并设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`。基线由
`git archive HEAD` 解到独立临时目录后运行，不修改当前工作区。

| 对象 | 结果 |
|---|---|
| 当前工作区 | `1027 passed, 51 failed, 36 skipped, 2 warnings` |
| HEAD 基线 | `1006 passed, 51 failed, 36 skipped, 1 warning` |

按完整测试身份比较，两份 51 项失败清单完全一致，没有发现本次变更新增的失败；当前工作区增加的
21 个通过项来自本次新增测试。既有失败分布于 SessionRuntime、runtime environment、Knowledge、
Mem0、Persona、Prompt workspace source、Skill、ToolResultStore POSIX 权限、Capability handler 和
packaging default persona resource。全量测试因此不能记录为通过，本次也未修复这些无关失败。

### 未执行或未通过的检查

- 可选真实模型 smoke 未执行：本轮没有另行获得真实模型调用授权，也未使用本地 fixture 冒充
  真实模型结果。
- 额外执行的聚焦 mypy 未形成类型验收：当前包缺少 `py.typed`，且环境未安装 `jsonschema`
  类型 stubs，共报告 37 个 `import-untyped`；没有为本计划额外修改依赖或把该结果记为通过。
