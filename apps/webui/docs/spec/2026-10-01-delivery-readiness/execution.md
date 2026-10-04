# 验收反馈收尾执行记录

2026-10-01。Windows / Edge；沿用现有工作区修改，不创建分支、暂存或提交。

本文保留 10 月 1 日的历史结果。10 月 2 日的 PR 准备、真实容器验收和完整镜像构建结果见
[pre-pr.md](pre-pr.md)，不以本文件中的旧数量覆盖最新验证。

## 实际交付

1. 记忆属性移到正文上方，调整顶部留白；历史每页 10 条。正文默认展示去除共同前后缀后的变化区段，可展开修改前后全文。新增分类/有效期快照支持暂停、恢复、分类和有效期标签；旧历史缺少信息时保留“更新了记忆”。
2. 记忆规则改为使用说明，明确原文保存、作用范围、暂停与恢复后清除期限。
3. 质量报告直接使用 OpenKB 的服务端档案，按每页 10 份展示，支持刷新、全文弹窗、Markdown 导出和确认删除。报告删除有独立受限接口，不重写知识页面及原始资料。
4. Bash 修复 Windows 可执行文件定位、管道、超时/取消及创建时取消清理，使用原生 Job Object 收回命令树。最终回归曾发现外部 taskkill 取消偶发等待 300 秒，已替换常规清理路径，并增加父 shell 退出后后台子进程回收测试。Mem0 的 infer=true 写入错误由实际 LLM 配置导致，已修复本地配置，没有改为绕过提取的 infer=false。
5. 原始文档转知识页面/关联的人工干预保留待定，不新增未定义的审批或中间状态。

## 验证证据

| 范围 | 结果 |
| --- | --- |
| WebUI 记忆与知识专项 | 第一轮 56 passed；报告对接服务端后的知识专项 27 passed |
| WebUI 最终全套 | 92 passed、5 skipped，138.25 秒；5 项需要显式开启真实服务开关 |
| 真实服务及模型 | 3 passed，15.30 秒：实际 Mem0 编辑/暂停/恢复/属性历史；Gateway 文本、刷新/重连/取消；实际 bash 与 memory_remember、持久化与工具历史恢复 |
| 真实报告档案 | 在唯一临时库中放入已知报告，浏览器经真实 Vite 代理完成列表、刷新、全文、下载、删除、再刷新；临时库清理完成。此项验证档案管理，不声称重新运行质量检查模型 |
| 窄屏检查 | 390×844 下记忆详情、报告列表和报告详情截图检查；无页面横向溢出 |
| Bash 专项 | 最终 23 passed，9.05 秒；覆盖同步/异步、超时、输出限额、取消、创建期间取消、缺少可执行程序及后台子进程回收 |
| Mem0 存储与更新专项 | 76 passed；包含旧表迁移保留历史和同步/异步属性快照 |
| OpenKB 报告专项 | 7 passed；真实临时目录的读取/删除、鉴权、路径与符号链接限制 |
| WebUI 工程 | lint、Prettier、TypeScript、Vite build 通过；现有主包大于 500 kB 提示保留 |
| 编译与差异 | Python compileall、git diff --check 通过 |

Job Object 修复并重启常用 Gateway 后，两项真实 Gateway 测试再次通过（14.83 秒），覆盖工具执行与文本/历史/重连/取消。

真实模型用例在 `test/integration/test_live.py`，使用独立工作区；memory_remember 产生的记录通过本次唯一 source_session_id 清理。其他真实 Mem0 测试只删除本次创建的服务 ID。Gateway 测试会话历史保留用于复核。

## 全项目验证限制

- Agent：619 passed、45 failed、36 skipped。失败集中于本次未修改的 Knowledge/Skill POSIX 文件系统操作、进程插件及权限行为等 Windows 差异。
- OpenKB：1249 passed、16 failed。包括既有 Windows 路径/权限、机器全局 Skill、watcher 差异；`api.py` 810 行及 `api_helpers.py` 800 行违反原有行数检查。本次未编辑这两个文件。
- Mem0 全库：9 skipped，37 个收集错误，缺少 boto3、Azure、sentence_transformers、ollama 等可选供应商依赖。按仓库要求未扩展修复无关失败。
- Python ruff：本轮新增代码检查通过；Bash 原有 `parallel must be a boolean` 使用 ValueError 的 TRY004 提示保留，避免无关接口行为变更。
- 未启动交互式 TUI 做人工验收；WebUI 已经通过相同 Gateway 链路执行真实工具，未将离线回放算作真实打通。

## 本地运行环境变化

恢复 Docker Desktop、Mem0、OpenKB，重启常用 Gateway 8765 加载修复。验证用 8766 已停止；WebUI 开发入口 5173 保留。

根 `.env` 已补齐已有有效的 Gateway 密钥，以及 Mem0 模型密钥和实际模型地址；Mem0 管理配置的 LLM override 同步修复并持久化。凭据没有写入受版本管理的代码或文档。OpenKB 既有库模型配置没有改动；知识编译的模型配置限制仍按部署文档核对。

Docker Hub 认证域名不可达，标准 Mem0 全量构建失败。为验收，基于本机已有镜像复制本轮源文件构建 Mem0/OpenKB 增量镜像并重新创建服务；这不是干净环境全量构建通过的证明。保留 `icarus-mem0:before-history-20261001`、`icarus-openkb:before-reports-20261001` 供本机回滚。网络正常的发布环境仍应从仓库 Dockerfile 完整构建。

迁移前通过 SQLite backup 保存 `F:/IcarusData-WebPreview/services/mem0/history/history-before-attributes-20261001.db`。旧 Mem0 的表迁移逻辑可能丢弃新增 changes 列，降级前必须保留备份，不把降级数据库当作完整审计存档。
