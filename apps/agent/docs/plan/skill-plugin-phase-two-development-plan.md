# SkillPlugin Phase Two Development Plan｜轮后自动维护开发计划

> 状态说明：本文记录第二阶段最初的实施路径。工具轨迹来源现已修正为仅从
> `AgentCompletedEvent.response.messages` 恢复；SkillPlugin 不再消费
> `AgentToolStartedEvent`、`AgentToolCompletedEvent` 或错误 Event。`AgentErrorEvent` 已被统一
> `TaskErrorEvent` 替代。权威行为以
> `apps/agent/docs/arch/skill-plugin-design.md` 为准，后续修正计划单独维护。

## 目标

实现 `skill-plugin-design.md` 的第二阶段：在一轮主 Agent 对话成功结束后，根据本轮工具调用数量决定是否启动后台 Skill 维护 Agent，并以结构化计划安全地创建、更新、合并或删除 Workspace Skill。

本阶段沿用第一阶段已经确认的边界：

- 用户显式要求创建或安装 Skill 时，仍由主 Agent 通过动态检索到的管理 Skill 和现有工具完成；
- 内部维护 Agent 只做轮后自动沉淀，不直接操作文件；
- 全局 Skill 对自动维护流程只读；
- 主对话结果不等待也不展示后台维护结果；
- 失败对话不触发维护；
- 同一 Workspace 同时最多运行一个维护任务。

## 实施原则

- 每轮只在 `AgentCompletedEvent` 到达后判断一次，不在 ReAct step 内调用维护 Agent；
- 只统计 `AgentToolStartedEvent`，成功和失败工具都计数；
- 唯一自动触发条件是 `tool_call_count > 10`；
- 维护 Agent 使用独立、无工具的 AgentFactory，不共享主 Agent 工具注册表；
- 完整会话与工具轨迹作为数据注入维护 Agent 的当前 Prompt，不改变稳定 System Prompt；
- LLM 只输出 JSON 计划；文件副作用只允许由 SkillRepository 执行；
- 所有文件操作先校验 Workspace 边界、YAML 头和内容 Hash；
- 单项操作失败不回滚已经成功的其他操作；
- 自动维护失败只记录日志和 Trace，不改变主任务终态；
- 不实现跨进程锁、远程任务队列和用户确认 UI。

## 目录结构

新增或扩展：

```text
apps/agent/src/agent_orchestration/plugins/skill/
├── maintenance_models.py
├── maintenance_prompt.py
├── maintenance_parser.py
├── maintainer.py
├── repository.py
├── coordinator.py
├── turn_state.py
└── plugin.py
```

测试镜像：

```text
apps/agent/test/agent_orchestration/plugins/skill/
├── test_maintenance_models.py
├── test_maintenance_prompt.py
├── test_maintenance_parser.py
├── test_maintainer.py
├── test_repository.py
├── test_coordinator.py
├── test_turn_state.py
└── test_plugin_maintenance.py
```

## 任务一：轮级执行轨迹

**新增文件**

- `turn_state.py`
- `test_turn_state.py`

**更新文件**

- `plugin.py`
- `test_plugin_maintenance.py`

**实现内容**

- `SkillTurnState` 按 `task_id` 保存：
  - 本轮原始用户输入；
  - 本轮检索命中的 Skill；
  - 有序的工具调用与完成结果；
  - 工具调用计数；
- `UserInputEvent` 创建本轮状态；
- `AgentToolStartedEvent` 追加工具调用并计数；
- `AgentToolCompletedEvent` 按 call ID 回填结果；
- `AgentErrorEvent` 删除状态，不触发维护；
- `AgentCompletedEvent` 弹出状态并只判断一次；
- 10 次工具调用不触发，11 次触发；
- 工具完成失败仍计入调用数；
- 未识别来源、无 task_id 或迟到事件安全忽略。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_turn_state.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_plugin_maintenance.py -q
```

## 任务二：维护上下文与结构化计划

**新增文件**

- `maintenance_models.py`
- `maintenance_prompt.py`
- `maintenance_parser.py`
- 对应测试

**实现内容**

### Skill 快照

维护开始时生成不可变快照：

```text
name
description
scope
path
content
content_hash
lifecycle_status
last_used_at
use_count
```

快照包含轮后重新扫描到的有效 Skill。完整正文只进入后台维护调用，不进入主 Agent 的常规 Skill 注入。

### 维护输入

Prompt Builder 稳定序列化：

- `AgentCompletedEvent.response.messages` 中的完整多轮上下文；
- 当前轮结构化 ToolCall 与 ToolExecutionResult；
- 本轮命中和当前累计 Skill；
- 当前 Skill 快照与生命周期；
- 自动维护规则和输出 JSON Schema。

维护 Agent 必须先判断主 Agent 是否已经完成显式 Skill 创建、更新或安装；轮后目录已出现对应结果时不得重复执行。

### 结构化计划

使用 Pydantic 定义：

```text
SkillMaintenancePlan
└── operations[]
    ├── action: create | update | merge | delete | no_op
    ├── target_name
    ├── source_names[]
    ├── content
    └── reason
```

校验规则：

- `no_op` 必须是唯一操作；
- `create/update/merge` 必须提供完整 `SKILL.md` 内容；
- `merge` 至少提供两个来源名称；
- `delete` 不接受 content；
- 名称必须满足安全目录名规则；
- 同一计划不得重复写入或删除同一目标；
- 单次计划最多十个操作；
- 支持纯 JSON 和 Markdown fenced JSON；
- 非法或无法解析的输出视为维护失败，不执行任何 CRUD。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_maintenance_models.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_maintenance_prompt.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_maintenance_parser.py -q
```

## 任务三：内部维护 Agent

**新增文件**

- `maintainer.py`
- `test_maintainer.py`

**更新文件**

- `agent_runtime_service.py`
- 应用测试

**实现内容**

- 应用层创建独立 `AgentFactory(register_builtin_tools=False)`；
- 维护 Agent 使用 `thinking` 模型角色；
- AgentFactory 在第一次实际触发时才创建 LLM；
- 维护调用使用独立稳定 System Prompt；
- 完整维护上下文放入当前 `input_prompt`；
- `history_messages=[]`，避免维护 Agent 把原对话误当成自身对话角色；
- `tools=[]`，维护 Agent 不获得 read/write/bash 等工具；
- 调用 `BaseAgent.ainvoke()`，取得最终文本后交给 Parser；
- 设置独立维护超时，默认 120 秒；
- 维护 Agent/Parser 失败只记录日志；
- Runtime 关闭时在 SkillPlugin drain 后关闭独立 AgentFactory。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_maintainer.py \
  apps/agent/test/application/test_agent_runtime_service.py -q
```

## 任务四：SkillRepository 与内部 CRUD

**新增文件**

- `repository.py`
- `test_repository.py`

**实现内容**

- 只允许写入当前 Workspace Skill 目录；
- 全局 Skill 只能读取，任何 delete 都拒绝；
- 更新全局 Skill 时写入 Workspace 同名覆盖版本，不修改全局文件；
- 目录名使用规范化安全名称；
- `create/update/merge` 内容必须：
  - 是 UTF-8 文本；
  - 包含合法 YAML front matter；
  - `name` 与目标名称一致；
  - `description` 非空；
- 写入使用同目录临时文件、`fsync` 和原子 `replace`；
- 文件权限收紧为目录 `0700`、文件 `0600`；
- `delete` 只允许 Workspace Skill；
- 删除 `SKILL.md` 后仅在目录为空时删除目录；
- `merge` 先原子写目标，只删除 `deletion_candidate` 的 Workspace 来源；其他 Workspace 来源和全局来源保留；
- 每个操作返回结构化成功、跳过或失败结果；
- 成功创建、更新或合并后更新 Skill 使用状态为 active。

### Hash 冲突

- 维护 Agent 分析前记录所有目标/来源内容 Hash；
- 执行前重新读取文件；
- create 目标从无到有视为冲突；
- update/delete/merge 来源 Hash 变化视为冲突；
- 同进程 Repository writer 共享 Workspace 写锁，最终校验和 mutation 在锁内执行；
- 被同进程 Repository writer 修改过的冲突操作跳过并记录；
- 跨进程、编辑器和通用文件工具不在本阶段原子 CAS 保证内。

### 删除规则

- 只有 Workspace Skill 可删除；
- 自动 `delete` 目标必须为 `deletion_candidate`；
- 全局 Skill 即使超过 60 天也拒绝删除；
- 非删除候选的 delete 计划视为校验失败。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_repository.py -q
```

## 任务五：Workspace 级协调与后台任务

**新增文件**

- `coordinator.py`
- `test_coordinator.py`

**更新文件**

- `plugin.py`
- `test_plugin_maintenance.py`

**实现内容**

- 进程内协调器按 `workspace_key` 维护运行声明；
- claim/release 使用线程锁，支持多个 Event Loop；
- 同 Workspace 已有维护任务时直接跳过，不排队；
- 不同 Workspace 可以并行维护；
- SkillPlugin 收到触发后仅创建后台 Task，不阻塞当前 Event 消费；
- 后台任务流程：
  1. claim Workspace；
  2. 重扫并创建快照；
  3. 构造维护上下文；
  4. 调用维护 Agent 得到计划；
  5. 校验并应用计划；
  6. 更新使用状态；
  7. 记录结果并 release；
- `drain()` 等待所有维护任务完成；
- `stop()` 取消仍在运行的维护 Agent；若不可取消的 Repository 线程已开始，则等线程真正完成后再 release Workspace；
- `AgentRuntimeService.stop(timeout)` 的 timeout 限制 PluginManager drain；已经提交到工作线程的文件 mutation 为保证关闭后不再产生副作用，会继续等待真实结束；外部取消 stop 也会先完成 shielded 清理再重抛取消；
- 任务异常必须被消费，不产生未获取异常；
- 第二阶段订阅 `skill <- agent`，不把维护事件发给 OutputBridge。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_coordinator.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_plugin_maintenance.py -q
```

## 任务六：应用集成与真实验证

**更新文件**

- `agent_runtime_service.py`
- `plugins/skill/__init__.py`
- `plugins/__init__.py`（仅确有公共类型时）
- 应用与持久化集成测试

**实现内容**

- 创建独立维护 AgentFactory 和 SkillMaintainer；
- 将 Repository、Coordinator、Prompt Builder、Parser 注入 SkillPlugin；
- 新增订阅 `skill <- agent`；
- 保留第一阶段 `skill <- user-input` 与 `blackboard <- skill`；
- 关闭顺序：
  1. PluginManager drain/stop；
  2. 主 AgentFactory 关闭；
  3. 维护 AgentFactory 关闭；
  4. PersistenceRuntime 关闭；
- 启动失败时两个 AgentFactory 和 SkillPlugin 均可幂等清理；
- 自动维护结果只进入日志与 Trace，不改变 TUI 事件序列。

**聚焦验证**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill -q
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/application \
  apps/agent/test/agent_orchestration/plugins/persistence -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
```

**真实 Smoke Test**

- 使用临时 ICARUS_DATA_DIR 和 Workspace；
- 构造一轮至少 11 次工具调用的成功任务；
- 验证主 Agent 先完成；
- 验证后台维护 Agent 收到完整多轮消息和工具轨迹；
- 验证维护计划可以 create 一个 Workspace Skill；
- 验证下一轮扫描自动发现新 Skill；
- 不在输出或 Trace 中暴露 API Key。

## 最终验证

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src apps/agent/test apps/tui
git diff --check
```

最终代码审查重点：

- 是否可能在主 Agent 完成前启动维护；
- 是否可能重复执行用户显式 Skill 操作；
- 是否存在全局 Skill 写入或删除路径；
- 是否存在目录穿越、符号链接逃逸或 Hash 校验绕过；
- 是否有维护任务阻塞 EventBus、TUI 或 Runtime stop；
- 是否有同 Workspace 重复维护、后台 Task 泄漏或未获取异常；
- 是否有失败维护改变主任务成功状态。
