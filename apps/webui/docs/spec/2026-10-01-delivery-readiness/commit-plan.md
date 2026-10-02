# 提交文件清单

2026-10-02。相对当前 HEAD `382f329` 的工作区清单；所有文件尚未暂存。本清单给出逻辑分组，
不执行 `git add`、commit、push 或 PR 创建。`A` 为新增、`M` 为修改、`D` 为有意删除。
已有 4 个 WebUI 原型提交保留原状，不重写历史。

## 使用方式

按下面的逻辑单元提交，每个实现与对应测试放在一起；文档集中在最后一个独立提交。
先审查每组 diff，再只暂存组内路径，避免 `git add .` 纳入其他工作。
WebUI 的 Shell、公共 UI、业务包、包依赖、生产入口与相应测试构成一个完整交付单元，
本次不拆成会缺少 workspace 依赖的中间提交。后端先于 WebUI 集成。

OpenKB 的 uv.lock 变动是现有 `api` extra 指向 `web` 的元数据同步，已通过 `uv lock --check`；
没有在本轮顺带升级依赖版本。Windows Bash 单列为独立逻辑功能。

## 1. Mem0 属性历史与构建环境

共 6 个路径。

- `A` `apps/mem0/.dockerignore`
- `M` `apps/mem0/mem0/memory/main.py`
- `M` `apps/mem0/mem0/memory/storage.py`
- `M` `apps/mem0/pyproject.toml`
- `M` `apps/mem0/tests/memory/test_main.py`
- `M` `apps/mem0/tests/memory/test_storage.py`

## 2. OpenKB 资料、报告与部署模板

共 12 个路径。

- `A` `apps/openkb/.dockerignore`
- `M` `apps/openkb/Dockerfile.icarus`
- `M` `apps/openkb/openkb/api_models.py`
- `M` `apps/openkb/openkb/api_pages_router.py`
- `M` `apps/openkb/openkb/cli.py`
- `A` `apps/openkb/openkb/report_ops.py`
- `M` `apps/openkb/scripts/prepare_managed_kb.py`
- `M` `apps/openkb/tests/test_api.py`
- `A` `apps/openkb/tests/test_managed_kb_template.py`
- `M` `apps/openkb/tests/test_remove.py`
- `A` `apps/openkb/tests/test_report_ops.py`
- `M` `apps/openkb/uv.lock`

## 3. Agent/Gateway 记忆身份与召回

共 9 个路径。

- `M` `apps/agent/src/agent_orchestration/plugins/memory/factory.py`
- `M` `apps/agent/src/agent_orchestration/plugins/memory/plugin.py`
- `M` `apps/agent/src/application/agent_runtime.py`
- `M` `apps/agent/test/agent_orchestration/plugins/memory/test_factory.py`
- `M` `apps/agent/test/agent_orchestration/plugins/memory/test_plugin.py`
- `M` `apps/agent/test/application/test_agent_runtime.py`
- `A` `apps/agent/test/application/test_memory_context.py`
- `M` `apps/gateway/src/protocol/methods.py`
- `M` `apps/gateway/test/test_methods.py`

## 4. Windows Bash 进程生命周期

共 3 个路径。

- `M` `apps/agent/src/agent_orchestration/tools/builtin/bash_tool.py`
- `A` `apps/agent/src/agent_orchestration/tools/builtin/windows_process.py`
- `M` `apps/agent/test/agent_orchestration/tools/builtin/test_builtin_tools.py`

## 5. WebUI 工作台、生产入口及回归

共 76 个路径。

- `A` `apps/webui/.dockerignore`
- `A` `apps/webui/.env.example`
- `M` `apps/webui/.gitignore`
- `A` `apps/webui/.prettierignore`
- `A` `apps/webui/.prettierrc.json`
- `A` `apps/webui/Dockerfile`
- `D` `apps/webui/apps/shell/content-study.html`
- `M` `apps/webui/apps/shell/index.html`
- `M` `apps/webui/apps/shell/package.json`
- `M` `apps/webui/apps/shell/src/App.tsx`
- `D` `apps/webui/apps/shell/src/DesignSystem.tsx`
- `D` `apps/webui/apps/shell/src/design-study/ContentStudy.tsx`
- `D` `apps/webui/apps/shell/src/design-study/study.css`
- `D` `apps/webui/apps/shell/src/design.css`
- `M` `apps/webui/apps/shell/src/main.tsx`
- `A` `apps/webui/apps/shell/src/vite-env.d.ts`
- `M` `apps/webui/apps/shell/src/workspace.css`
- `M` `apps/webui/apps/shell/vite.config.ts`
- `A` `apps/webui/eslint.config.mjs`
- `M` `apps/webui/package.json`
- `A` `apps/webui/packages/chat-app/package.json`
- `A` `apps/webui/packages/chat-app/src/ChatApp.tsx`
- `A` `apps/webui/packages/chat-app/src/ChatTranscript.tsx`
- `A` `apps/webui/packages/chat-app/src/chat.css`
- `A` `apps/webui/packages/chat-app/src/gateway.ts`
- `A` `apps/webui/packages/chat-app/src/updates.ts`
- `A` `apps/webui/packages/chat-app/src/useChatSession.ts`
- `M` `apps/webui/packages/knowledge-app/package.json`
- `A` `apps/webui/packages/knowledge-app/src/EvidenceCanvas.tsx`
- `A` `apps/webui/packages/knowledge-app/src/EvidenceMap.tsx`
- `D` `apps/webui/packages/knowledge-app/src/Graph.tsx`
- `A` `apps/webui/packages/knowledge-app/src/GraphDetails.tsx`
- `A` `apps/webui/packages/knowledge-app/src/KnowledgeActions.tsx`
- `M` `apps/webui/packages/knowledge-app/src/KnowledgeApp.tsx`
- `A` `apps/webui/packages/knowledge-app/src/KnowledgeQuality.tsx`
- `M` `apps/webui/packages/knowledge-app/src/KnowledgeReader.tsx`
- `M` `apps/webui/packages/knowledge-app/src/Reading.tsx`
- `D` `apps/webui/packages/knowledge-app/src/demo.ts`
- `A` `apps/webui/packages/knowledge-app/src/evidence.ts`
- `A` `apps/webui/packages/knowledge-app/src/graphLayout.ts`
- `M` `apps/webui/packages/knowledge-app/src/knowledge.css`
- `A` `apps/webui/packages/knowledge-app/src/openkb.ts`
- `A` `apps/webui/packages/knowledge-app/src/preview.ts`
- `A` `apps/webui/packages/knowledge-app/src/types.ts`
- `A` `apps/webui/packages/knowledge-app/src/useKnowledge.ts`
- `M` `apps/webui/packages/memory-app/package.json`
- `M` `apps/webui/packages/memory-app/src/MemoryApp.tsx`
- `A` `apps/webui/packages/memory-app/src/MemoryCreate.tsx`
- `A` `apps/webui/packages/memory-app/src/MemoryDetail.tsx`
- `A` `apps/webui/packages/memory-app/src/MemoryHistory.tsx`
- `A` `apps/webui/packages/memory-app/src/context.ts`
- `D` `apps/webui/packages/memory-app/src/demo.ts`
- `A` `apps/webui/packages/memory-app/src/mem0.ts`
- `M` `apps/webui/packages/memory-app/src/memory.css`
- `A` `apps/webui/packages/memory-app/src/types.ts`
- `A` `apps/webui/packages/memory-app/src/useMemories.ts`
- `M` `apps/webui/packages/ui/package.json`
- `A` `apps/webui/packages/ui/src/gateway.ts`
- `M` `apps/webui/packages/ui/src/index.tsx`
- `M` `apps/webui/packages/ui/src/navigation.ts`
- `M` `apps/webui/packages/ui/src/styles.css`
- `M` `apps/webui/pnpm-lock.yaml`
- `A` `apps/webui/server/check-services.mjs`
- `A` `apps/webui/server/index.mjs`
- `A` `apps/webui/test/apps/shell/test_design_document.py`
- `M` `apps/webui/test/apps/shell/test_shell.py`
- `M` `apps/webui/test/conftest.py`
- `A` `apps/webui/test/integration/test_live.py`
- `A` `apps/webui/test/mem0_service.py`
- `A` `apps/webui/test/packages/chat_app/test_chat.py`
- `A` `apps/webui/test/packages/knowledge_app/test_graph.py`
- `M` `apps/webui/test/packages/knowledge_app/test_knowledge.py`
- `A` `apps/webui/test/packages/knowledge_app/test_preview.py`
- `M` `apps/webui/test/packages/memory_app/test_memory.py`
- `A` `apps/webui/test/packages/memory_app/test_service.py`
- `A` `apps/webui/test/server/test_server.py`

## 6. CI 与本地开发产物忽略规则

共 4 个路径。

- `A` `.github/workflows/backend-contracts.yml`
- `A` `.github/workflows/images.yml`
- `A` `.github/workflows/webui.yml`
- `M` `.gitignore`

## 7. 文档、分支规则与验收截图

共 34 个路径。

- `M` `AGENTS.md`
- `M` `README.md`
- `M` `apps/agent/docs/spec/2026-09-15-memory-knowledge-plugin/arch.md`
- `A` `apps/agent/docs/spec/2026-10-01-windows-bash-execution/plan.md`
- `M` `apps/gateway/README.md`
- `M` `apps/gateway/docs/spec/2026-08-29-agent-gateway-positioning/arch.md`
- `M` `apps/mem0/MODIFICATIONS.md`
- `A` `apps/mem0/docs/spec/2026-10-01-memory-history-attributes/plan.md`
- `M` `apps/openkb/MODIFICATIONS.md`
- `M` `apps/openkb/docs/.gitignore`
- `A` `apps/openkb/docs/spec/2026-10-01-quality-report-management/plan.md`
- `M` `apps/webui/README.md`
- `M` `apps/webui/docs/api-map.md`
- `A` `apps/webui/docs/deployment.md`
- `A` `apps/webui/docs/design-system.html`
- `M` `apps/webui/docs/spec/2026-09-29-content-browsing/plan.md`
- `A` `apps/webui/docs/spec/2026-09-29-evidence-map/plan.md`
- `A` `apps/webui/docs/spec/2026-09-30-interaction-polish/plan.md`
- `A` `apps/webui/docs/spec/2026-09-30-memory-detail-modal/plan.md`
- `A` `apps/webui/docs/spec/2026-09-30-workspace-layout/plan.md`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/acceptance.md`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/arch.md`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/commit-plan.md`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/execution.md`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/handoff.md`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/images/chat.png`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/images/knowledge.png`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/images/memory-detail.png`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/images/memory.png`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/manual-memory-fix.md`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/plan.md`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/pr-description.md`
- `A` `apps/webui/docs/spec/2026-10-01-delivery-readiness/pre-pr.md`
- `A` `apps/webui/test/integration/README.md`

## 明确排除

- `.claude/worktrees/`：其他本地工作树，已忽略，未删除。
- `.playwright-mcp/`：浏览器快照及运行日志，已忽略。
- `.icarus-data/`、`.env`、虚拟环境、node_modules、构建产物及 test-results：本地状态，不提交。
- `docs/progress-baseline-2026-09-24.md`：此前独立的未跟踪进度文档，另行审查。

本次截图只包含合成测试数据。提交前复核暂存差异中的凭据、临时路径及误删文件；
不要把未提交的路径遗漏后，仅推送当前 4 个原型提交。
