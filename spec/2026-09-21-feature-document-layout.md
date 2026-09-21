# Feature Spec 文档聚合设计

## 背景

Icarus 当前按文档类型分别维护 `apps/<app>/docs/arch/` 和
`apps/<app>/docs/plan/`。同一功能的设计与实施计划相隔较远，而且部分功能包含多个阶段计划、
历史计划或只有单侧文档，阅读时难以快速还原完整演进过程。

本次调整同时影响 `agent`、`gateway` 和 `tui`，因此把不可拆分的目录约定记录在仓库根
`spec/`。具体功能文档仍归各自应用所有。

## 目标

把应用内文档从“按文档类型分组”调整为“按功能分组”：

```text
apps/<app>/docs/spec/YYYY-MM-DD-<feature>/
├── arch.md
├── plan.md
└── plan-<phase-or-purpose>.md
```

- `YYYY-MM-DD` 使用该功能设计文档首次进入 Git 的日期；plan-only spec 使用该计划首次进入
  Git 的日期。
- 目录日期表示功能 spec 的起始时间，后续修改不改变目录名。
- `arch.md` 描述该功能的架构、边界和契约。
- 单一或主要实施计划命名为 `plan.md`。
- 多阶段、补充或历史计划使用 `plan-<phase-or-purpose>.md`。
- 没有对应设计的独立计划允许形成 plan-only spec；不为补齐外形而虚构 `arch.md`。
- 没有实施计划的设计允许形成 arch-only spec。

## 文档事实来源

架构文档只描述基于当前源代码和测试确认的架构与系统设计，不反向限制源代码演进。发生不一致时，
以当前源代码和测试为事实依据并更新文档。`AGENTS.md` 中明确列出的仓库规则仍然有效；本原则只
用于区分“描述当前实现的架构文档”和“必须遵守的仓库规则”。

## 边界

本次只迁移 Icarus 自有的以下目录：

- `apps/agent/docs/arch/` 与 `apps/agent/docs/plan/`；
- `apps/gateway/docs/arch/` 与 `apps/gateway/docs/plan/`；
- `apps/tui/docs/arch/` 与 `apps/tui/docs/plan/`。

以下内容不迁移：

- 根 `spec/` 中现有的跨应用需求；
- `docs/` 中的产品定位、路线图和待办；
- `apps/mem0/docs/` 与 `apps/openkb/docs/` 等上游或应用自有文档体系。

## 聚合规则

### 一对一文档

设计与同名实施计划进入同一个 feature 目录，并分别改名为 `arch.md` 和 `plan.md`。例如：

```text
apps/agent/docs/spec/2026-09-20-tool-execution-guard/
├── arch.md
└── plan.md
```

### 一对多文档

以下功能保留完整演进记录：

| 应用 | Feature 目录 | 文件 |
| --- | --- | --- |
| agent | `2026-08-15-plugin-eventbus-blackboard` | `arch.md`、`plan.md`（原 Plugin Runtime 计划） |
| agent | `2026-09-04-session-store` | `arch.md`、`plan.md`、`plan-legacy-conversation-history.md` |
| agent | `2026-08-17-skill-plugin` | `arch.md`、`plan-phase-one.md`、`plan-phase-two.md`、`plan-toolization.md`、`plan-event-filtering-and-recall-correction.md` |
| tui | `2026-08-19-tui-terminal-framework` | `arch.md`、`plan.md`、`plan-legacy-repl.md` |
| tui | `2026-08-19-tui-persistent-input-queue` | `arch.md`、`plan.md`（原 Textual TUI 计划） |

### 单侧文档

以下文档没有自然的一对一配对，保持单侧 spec：

- arch-only：Agent Runtime Service/TUI、Model Provider、Model Config、Plugin Event Flow Current
  State、TUI Character Theme；
- plan-only：Gateway Session History RPC、TUI Gateway Migration。

### 应用边界

跨应用功能仍按应用拆分具体设计与计划。例如 Session Management 继续分别位于：

```text
apps/agent/docs/spec/2026-09-02-session-management/
apps/gateway/docs/spec/2026-09-02-session-management-rpc/
apps/tui/docs/spec/2026-09-02-tui-session-management/
```

仓库根 spec 使用 `spec/YYYY-MM-DD-<feature>.md`，日期同样取首次进入 Git 的日期并保持稳定。
`spec/2026-09-02-session-management.md` 继续作为不可拆分的跨应用需求入口，并链接到三个应用 spec。

## 迁移清单

### Agent

```text
2026-08-12-model-config-layer
2026-08-13-model-provider-layer
2026-08-15-agent-orchestration-foundation
2026-08-15-agent-stream-event
2026-08-15-plugin-eventbus-blackboard
2026-08-16-agent-runtime-service-tui
2026-08-16-file-persistence-observability
2026-08-17-skill-plugin
2026-08-18-plugin-event-flow-current-state
2026-08-22-agent-run-intervention
2026-08-23-plugin-runtime-manifest-lifecycle
2026-08-27-agent-core-capability-completion
2026-08-29-device-agent-runtime-session
2026-09-02-session-management
2026-09-04-session-store
2026-09-05-mcp-client-plugin
2026-09-15-memory-knowledge-plugin
2026-09-20-agent-native-memory-conversation
2026-09-20-agent-run-history-steering
2026-09-20-tool-execution-guard
```

### Gateway

```text
2026-08-29-agent-gateway-positioning
2026-08-29-session-history-rpc
2026-09-02-session-management-rpc
```

### TUI

```text
2026-08-19-tui-persistent-input-queue
2026-08-19-tui-terminal-framework
2026-08-20-tui-first-interaction-experience
2026-08-22-tui-character-theme
2026-08-27-tui-clipboard-image-paste
2026-08-29-tui-gateway-migration
2026-08-29-tui-session-history-restoration
2026-09-02-tui-session-management
2026-09-02-tui-streaming-markdown-scroll
```

## 引用更新

迁移后同步更新所有受版本控制的路径引用，包括：

- 根 `AGENTS.md`、`README.md` 和各应用 `README.md`；
- 根 `spec/` 中的跨应用需求；
- `docs/todo/` 中的路线图和待办；
- spec 文档之间的相互引用。

新文档约定统一写成 `apps/<app>/docs/spec/`，不再指导新增内容进入 `arch/` 或 `plan/`。
根级跨应用 spec 统一使用 `spec/YYYY-MM-DD-<feature>.md`。

## 验证

迁移完成后执行：

1. 确认原 30 份设计和 32 份计划均出现在新目录中；
2. 确认三个应用不再存在 `docs/arch/` 和 `docs/plan/` 文件；
3. 全仓搜索旧路径，确认受版本控制的文档中没有遗留引用；
4. 校验 Markdown 中指向仓库本地文件的路径全部存在；
5. 执行 `git diff --check`，确认没有空白符错误。

本次只调整文档布局和路径引用，不修改实现代码或文档正文语义。
