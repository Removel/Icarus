# 验收与并发回退复核

2026-10-01。Windows / Node.js 24.14.0 / Edge 154；容器使用 Node 22。未创建分支、暂存或提交。

## 回退影响与恢复

对照两个会话的操作记录、回退备份和当前源码，确认回退删除了本文件，移除了 WebSocket 扩展协商转发、已升级连接的退出清理及对应测试，并把 Mem0 默认端口改回 8000。已恢复这些实现和测试，将开发代理、生产入口、检查命令、环境样例及文档统一为 Compose 对外端口 8888。

同时保留并验证 Host 校验。浏览器损坏协议响应的 4002 关闭码、会话切换清理、工具对象预览、Mem0 服务接入等实现仍完整。其余测试与回退备份进行了语法树比较：除生产代理测试外，差异是等待开发服务的辅助 fixture 和一个未使用变量，不涉及既有业务断言。此前 OpenKB、知识图谱及界面改动继续保留，没有整树重置。

## 当前验证结果

| 检查 | 结果 |
| --- | --- |
| 恢复后的专项 | 生产入口、对话、记忆共 40 项通过，71.36 秒 |
| WebUI 全套 | 87 passed，4 skipped，131.17 秒；跳过项需显式开启真实服务验收 |
| 真实 Mem0 | 原文创建、编辑、metadata 保留、历史、刷新、暂停恢复、删除通过；实际 PostgreSQL 与本地 fastembed |
| 真实 OpenKB | 隔离库创建、读取、删除；模型编译 Markdown、读取资料/摘要、修改正文、图谱及浏览器刷新通过 |
| 真实 Gateway | 使用现有有效配置完成模型回复、历史重载、WebSocket 重连和取消 |
| WebUI 容器完整链路 | 4 项真实服务测试通过，19.45 秒；浏览器经 Node 22 容器的认证入口和 HTTP/WebSocket 代理访问后端 |
| Docker 镜像 | `icarus-webui:delivery-check` 构建成功；非 root 运行，健康端点与认证已验证 |
| 工程检查 | lint、format、typecheck、build、diff 通过；Vite 仍提示主入口约 779 kB，属于现有体积提示 |

Docker Hub 的认证域名在本机解析/连接失败。从 AWS Public ECR 的 `docker/library/node:22-bookworm-slim` 取得基础镜像并本地标记后完成原 Dockerfile 构建，没有更换为第三方自制镜像。

## 实际配置问题与处理

Mem0 数据库存在 `embedder.provider=openai` 覆盖，指向不可用的预览端点。虽然容器环境声明 fastembed，新增仍返回 502。确认该预览实例记忆列表为空、向量维数为 384 后，通过管理配置接口恢复原定的 multilingual MiniLM / fastembed（384 维）；随后真实增删改全部通过。没有清空数据库或修改既有记忆。

仓库根 `.env` 中 Gateway 模型 Key 为空，Mem0/OpenKB 模型配置为预览占位值。Gateway 本轮只在启动进程中注入旧 `apps/agent/.env` 中已有的有效密钥，未将密钥写入源码、测试或日志，也未改写根 `.env`。

真实知识编译在独立 OpenKB 容器完成，使用临时数据目录和有效凭据。首次失败暴露出新建库模板固化了不匹配的模型名；配置隔离容器的 `/app/config.yaml` 为该服务支持的模型后通过。默认管理库配置和全局模型设置不能替代新建库模板，详见 [部署说明](../../deployment.md)。常驻 OpenKB 预览实例的模型占位配置没有改写，其存活检查通过不代表可以直接编译。

测试创建的记忆和知识库均已删除；临时 WebUI 与 OpenKB 验收容器已停止并移除。保留开发入口 5173、Mem0 8888、OpenKB 7566、Gateway 8765 及 PostgreSQL 供后续使用。Gateway 当前进程可调用模型，按原环境重新启动前仍需补齐模型配置。Gateway 独立临时工作区的测试会话保留用于历史核对。

远端 GitHub Actions 尚未运行；本地已验证对应命令与 Docker 构建。真实模型证据来自显式开启的集成测试，不来自模拟响应或 skipped 项。

## 后续验收反馈

本文件保留前一轮基线。本次记忆、报告管理与工具修复的最新实现、全套失败范围和配置变化见 [execution.md](execution.md)，接手步骤见 [handoff.md](handoff.md)。
