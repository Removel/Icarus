# 仓库基建第一批执行记录

- 基线：`b3837bed94e473f86d950079f386fd0b3e96fa84`。
- 隔离 worktree：`.claude/worktrees/vendored-app-slimming`，detached feature 基线。
- 用户已授权第一批实施、隔离开发依赖安装、启动已有 Docker Desktop、校验官方 Actionlint 和隔离容器验证；未授权提交/推送、生产数据或真实模型操作。
- [设计](arch.md) · [计划](plan.md)

## 实施与验证

根文本/ignore/SDD/治理、服务 dev 安装控制面、Makefile/test 聚合、两服务
contracts/images/syntax workflows、第三方声明均已实施；不建立根 .venv。

| 检查 | 实际结果 |
| --- | --- |
| Git 属性/ignore 契约 | RED 7 → GREEN 7 |
| 根控制面新增契约 | RED 5 → GREEN；审查新增递归私有数据保护 RED→GREEN；最终 root suite 38 passed，含原 23 项 |
| Mem0 默认/脚本 | 82 passed；加固后的实际 installer 成功 |
| OpenKB 默认/脚本 | 325 passed；实际 locked installer 成功 |
| Agent | 空离线 `.env` 下全量 695 passed；服务配置专项 5 passed |
| Gateway | 全量 15 passed |
| TUI | 242 passed/13 snapshot failures；同环境导出未改 HEAD 后复现完全相同 13 失败身份 |
| 根 `make test` | 运行各上述 gate 后因 TUI 快照失败非零退出；不宣称全仓全绿 |
| 原 vendored 全量 | Mem0 前后均 37 缺可选依赖 collection errors；OpenKB 同 1 既有 file-size 失败 |
| 来源/资源/链接 | 490 份受保护文件未改；skill LICENSE/helper 完整；本地文档链接有效 |
| 两个 wheel | 实际构建与 JSON/LICENSE/runtime skill 资源验收通过 |
| 新 app Python Ruff | 通过；旧 compose adapters 的 lint 债务未修 |
| compile / diff | Python 编译与 git diff 空白检查通过 |
| Actionlint / ShellCheck | 官方 Actionlint 1.7.7 SHA256 校验后检查三个工作流通过；独立无网络 ShellCheck v0.10.0 检查 12 段 workflow shell 和四个新脚本通过；内联 Python AST 通过 |
| 源码镜像 | Mem0 API、Mem0 dashboard（额外无缓存重建）和 OpenKB 原 Dockerfile 全构建通过；镜像身份已记录；两服务镜像 `pip check` 通过 |
| Docker 上下文保护 | 真实 Docker COPY 排除合成的嵌套 DB/KB/`.env`/私有环境，保留源码/配置/skill/LICENSE 输入 |
| 部署协议 | 原 Compose 实际解析通过；Mem0 3 服务、OpenKB 1 服务、bind 路径不变 |
| 真实隔离服务 | Mem0/PostgreSQL 记忆 CRUD/search/history/scope 与 dashboard health/setup 通过；OpenKB auth/KB/graph/page/init/frontend 通过 |
| 重建持久化 | 临时容器重建后 Mem0 数据/历史与 OpenKB 别名/config/页面均保留；只使用临时卷 |
| 网络/费用 | 独立 internal 网络、无宿主端口、telemetry/tracing关闭，本地 embedding fixture；无远程模型调用；容器/网络已 down，临时卷与镜像保留 |

## 重要裁决与限制

1. Native worktree 默认 main，实施前对齐为确认的 feature SHA，不在错误基线上裁剪。
2. 计划中的纯 prose/absence change-detector 测试改为一次性资源/哈希/链接验收；
   永久测试以脚本行为、控制面、Git 属性消费和 CI 公共契约为主。
3. 所有 plan.md basename 相同，执行统一 ledger 以 app/task 前缀区分，避免 scratch 冲突。
4. 未授权 Git 提交，保持完整未提交 diff、新文件和忽略验证记录，不清除唯一执行证据。
5. OpenKB 原 locked 安装因 extra metadata 失配失败，仅做无版本升级的元数据修复。
6. 新 dev 环境要求 app Dockerignore，避免 COPY 私有环境/Secret/cache；构建目录归属不变。
7. 没有统一全仓 lint；旧 compose adapters Ruff 债务记录，不为清理进行无关格式修复。
8. Agent 的测试需要 repo .env，创建了空的忽略测试文件，未复制用户 Secret 配置。
9. 原 Docker 阻塞已补齐：用户授权后启动已安装 Docker Desktop，不改设置；源码镜像与隔离真实服务验收通过，未调用真实模型。
10. 官方预编译 Actionlint 1.7.7 验证 SHA256 后直接检查通过；其本机缺失 ShellCheck/Pyflakes 辅助项已分别用隔离 ShellCheck 与内联 Python AST 补验证，不伪称 actionlint 内置运行了这些工具。
11. ShellCheck 提示新脚本 `CDPATH= cd` 的空赋值歧义；仅四个新增脚本改为显式 `CDPATH=''`，原行为测试全部通过，不改存量脚本。
12. OpenKB 第一次构建因 Debian 包 HTTP 500/EOF 失败，原样重试成功；第一次 smoke 错将生成 index 页当可编辑页收到预期 400，换临时 concept fixture 验证；均未改业务源码。

## 后续门槛

PR6 未合入，因此当前 CI 只含两个服务镜像，不引用不存在报告测试/WebUI。
合入后按实际 source/history/report/CI 内容适配，保留 WebUI 第三镜像并收敛重复 job。
旧 UI/数据库迁移/生产数据操作不属于第一批，第二批仍另写计划与验收。
独立代码审查确认四项 Important：Mem0 私有环境/pip 目的地越界、OpenKB 构建夹带私有 KB、
合并 Gitignore 收窄递归数据保护、真实 bundled deck 测试未入默认门槛。四项已修；环境/递归ignore/
默认gate 有 RED→GREEN 证据。暂时撤回 Dockerignore 保护以复现旧规则的操作被安全策略拒绝，
未绕过且不称已完成此项 RED→GREEN；后续已通过真实 Docker COPY 的合成私有数据排除测试，
填补实际构建验证缺口。审查无 Critical 或额外 Minor，第一批镜像/工具/隔离服务验收已完成；
全量测试既有失败、未合入 PR6 与第二批继续独立记录，不能称全仓全绿。
