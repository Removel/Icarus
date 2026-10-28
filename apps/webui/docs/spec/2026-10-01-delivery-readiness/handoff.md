# Icarus 本轮交接

2026-10-02。WebUI 已进入 PR 准备阶段；最新验证和基线问题见 [pre-pr.md](pre-pr.md)，
提交清单见 [commit-plan.md](commit-plan.md)，正文草稿见 [pr-description.md](pr-description.md)。
不宣称全项目测试全绿，也没有执行 Git 提交或推送。

## 从哪里继续

- [计划](plan.md)：反馈清单和待定范围。
- [执行记录](execution.md)：本轮真实结果、失败范围、配置和镜像变化。
- [当前架构](arch.md)、[部署说明](../../deployment.md)、[API 映射](../../api-map.md)。
- [acceptance.md](acceptance.md) 和 [execution.md](execution.md) 是前两轮交付基线；最新结果以 pre-pr.md 为准。

## 接手验收

1. 打开 http://127.0.0.1:5173 ，检查记忆属性在正文上方，正文修正显示差异，暂停/恢复历史标签正确；旧历史无法补全缺失的属性。
2. 知识库 → 质量检查：历史报告来自服务端，可刷新、查看、导出和删除；新检查完成后刷新目录。每页 10 份，删除需要确认，不删除知识内容。历史列表和记忆历史均为读取全量后的展示分页。
3. 对话中要求执行 `bash` 的简单只读命令和记忆一条明确的工作区约定，核对工具成功与刷新后的历史。本轮已用唯一测试约定完成实测并清理。

服务端口：WebUI 5173、Gateway 8765、Mem0 8888、OpenKB 7566。根 `.env` 是未跟踪的本地运行配置；不要提交或复制到文档。

Windows 新终端可在仓库根运行 Gateway：

```powershell
uv run --no-project --with-requirements apps/agent/requirements.txt --with-requirements apps/gateway/requirements.txt python -m apps.gateway.src.main --host 127.0.0.1 --port 8765
```

需要 Git for Windows，并让 Git 在 PATH 中。启动已存在的服务前先检查端口，避免重复进程。WebUI 在 `apps/webui` 运行 `pnpm dev`；后端按根启动脚本或各应用部署文档启动。

## 验证与发布边界

WebUI 100 项回归通过；7 项真实服务与模型验收在 Node 入口、新 WebUI 容器分别全部通过。
Agent Linux 全量 709 项通过，Windows 受影响契约与 Gateway 合计 112 项通过。Mem0/OpenKB 后端需配套升级，
才能记录新属性历史并支持报告删除，Gateway 需支持 `memory.get_context`。

三个应用均已从源码完成 Docker 构建；OpenKB 全量仍有既有行数检查失败，Mem0 全库缺少可选依赖，
Windows 全量仍有平台差异，详见最新验证记录。远端 Actions 需要在提交和推送后确认。

工作区原本已有大量未提交修改。本轮主要新增/修改：MemoryHistory、MemoryDetail/mem0/types/文案样式；KnowledgeQuality/openkb 适配；Agent Bash/windows_process；Mem0 history storage/update；OpenKB report_ops/api_pages_router；对应测试和本组文档。提交时按应用/逻辑功能划分，保留原有修改并单独审查文档。

## 待定需求

原始资料 → 知识页面 → 文档关联过程的人工干预尚未实施。下一步先决定：编辑转换文本还是编译草稿、是否逐页确认、关联由谁维护、取消/重编译如何处理人工修改，以及并发与回滚规则。现有正文编辑与重新编译不能替代这个中间流程。
