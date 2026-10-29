# CI 改造实施与验证记录

日期：2026-10-29。实施基线：`885a4318cfaec7742da9ff0986ceb09f5b79f234`，当前分支 `feat/webui`；已按授权从 `upstream/feature` 快进同步。验收以 Linux 为准，Windows 结果仅作辅助。最初仅进行本地实现；随后用户授权提交并推送到个人仓库 `origin = xilele777/Icarus`，以草稿 PR 验证真实 Actions。未授权或执行合并、修改分支保护及镜像发布。

## 已实施

- 补 TUI、rela-mem 离线/隔离集成、Agent wheel 门禁，修复 Shell 检查和 Git 差异检查，WebUI 快速检查前移。
- 新增完整 Git 差异分析、保守影响选择、预期任务汇总和 `ci-required`，现有 workflow 支持 reusable 调用。
- 四个应用增加自有镜像 smoke 夹具，通用执行器创建内部隔离网络并清理资源。Mem0 smoke 发现实际 libpq 缺失，生产 Dockerfile 已补依赖。
- 发布绑定精确 SHA、完整 CI、经过 smoke 的架构 digest；预发布/版本不可变/旧版本保护和 dry-run 已实现。
- Actions 完整 SHA 固定并经官方仓库 tag API 核验，增加 Dependabot 和每周 retained 套件。根 `make test` 补齐 rela-mem 离线和 WebUI 默认入口。

保留用户先前的控制层文档修改、`scripts/install-commands.sh` 删除及其他未跟踪文件。本次没有修复或提交这些内容。

## 本地验证

| 检查 | 实际结果 |
| --- | --- |
| CI 检查/影响选择/汇总/发布/容器策略、仓库基础设施及 WebUI runner focused 测试 | Linux/WSL：89 passed；Windows 早期辅助 focused 65 passed。收尾修改后相关 workflow/runner focused 11 passed |
| actionlint 1.7.7 | 通过，包含所有新增/修改 workflow |
| Python 源码编译、逐文件 Shell 语法检查、工作区差异检查 | 通过；Shell 按 LF 检查，不改写工作区 |
| TUI 完整套件 | 512 passed，1 skipped；先补齐最新源码要求的 Pillow，再完整复跑 |
| rela-mem 离线（Linux Python 3.12） | 41 passed，29 deselected；Windows 辅助结果相同 |
| rela-mem 临时 PostgreSQL/FalkorDB 全套（Linux Python 3.12） | 70 passed；临时容器和匿名卷已清理，Windows 辅助结果相同 |
| WebUI lint / format:check / typecheck / build | 全部通过 |
| WebUI 完整套件（Linux Python 3.12） | 354 passed，13 skipped，12 分 21 秒；跳过的仅为既有真实服务 opt-in 用例 |
| 收尾后的 WebUI runner/代理隔离回归（Linux Python 3.12） | 25 passed；覆盖服务集成、runner 退出/清理与隔离断言、聊天及记忆浏览器 fixture |
| WebUI 生命周期脚本（Linux） | 10 passed；Windows 首轮 8 项平台相关失败不作为 Linux 验收结论 |
| WebUI 首轮完整套件（Windows，仅辅助） | 342 passed，13 skipped，9 failed，3 errors；8 个脚本平台失败，1 个搜索时序失败及 3 个页面初始化超时。后四项单独调查复跑 4 passed，不能将首轮全量改称通过 |
| 四个生产镜像（Linux amd64） | WebUI、rela-mem、Mem0、OpenKB 均成功构建，实际容器启动与 HEALTHCHECK smoke 全部通过；本次容器和网络已清理 |
| Agent wheel（Linux Python 3.12） | 成功构建；全新环境安装 wheel，15 个 manifest、默认资源、AgentRuntime 与插件发现通过，无 system-site-packages，临时 wheel 环境已清理 |
| Agent mypy（Linux Python 3.12，应用 typecheck.sh） | 273 个文件通过；Windows 原生目标的 13 个 POSIX API 平台错误仅留作辅助记录 |
| Gateway 完整套件（Linux Python 3.12，隔离副本） | 50 passed；未修改业务测试或放宽超时 |
| Agent 完整套件（Linux Python 3.12，隔离副本） | 1197 passed；使用空根 .env 和当前 Git 基线源码，应用入口执行并完成编译检查 |
| Gateway/Agent 早期本机 WSL Python 3.14 排查记录 | Gateway 49 passed、1 个重启 E2E 超时；Agent 1120 passed、2 个服务配置失败、27 分钟后中断。该轮涉及开发者配置/挂载环境，不作为最终验收；日志不复制敏感值 |
| 根控制层全套首次运行 | 397 passed，1 skipped，7 failed；其中 reusable job 的旧结构断言已修复并包含在最终 89 项通过结果中；其余为原工作区安装脚本删除及进程/探测超时，未改控制层业务代码 |
| Mem0/OpenKB 默认开发契约 | 尝试应用脚本，因本机缺少两应用开发环境退出，未标记通过；容器构建与 smoke 独立记录 |

镜像构建以当前 Dockerfile 和 Git 中的应用运行源码为输入，使用临时 tar 上下文避免 Windows 换行污染。首轮 Git 子树 archive 没有继承根 `.gitattributes`，产生过 CRLF OpenKB 旧产物；后续明确禁用 archive 的 autocrlf 重建。构建或日志打印出错不被当作 smoke 成功。

Linux Python 3.12 验证使用临时 ext4 副本，保留当前 Git 基线的应用源码与完整共享输入，并叠加本次相关应用入口；不复制开发者根 .env。WebUI 使用 Node 22.23.3、pnpm 10.32.1、Python 3.12.15 和已固定的 pytest/Playwright 依赖，lint、格式、类型和构建均通过。本机是 Ubuntu 26.04，Playwright 1.58 不识别其平台名，本地显式选择 Ubuntu 24.04 浏览器构件；远端 workflow 已固定受支持的 `ubuntu-24.04`，未向远端引入该本地 override。

完整浏览器回归后，进一步收紧 runner 的端口与默认代理环境，防止读取开发者真实服务配置；对应服务、聊天/记忆 fixture 和 runner focused 25 项通过。首次对已有本机旧镜像的 smoke 失败只用于诊断，不作为新产物验收。可复查日志与导出的 Linux JUnit 位于忽略的 `test-results/` 和应用 `test-results/`，其中隔离副本的报告已保存至 `test-results/linux/`。不得将可能含本地配置值的原始日志直接发布。

测试容器、匿名卷及网络已清理。自动审批对临时目录递归清理返回 `blocked by policy`，因此临时源码/工具链保留在 WSL `/tmp/icarus-ci-linux-4tx3pjsa` 与 Windows `%TEMP%\icarus-ci-linux-4tx3pjsa`；不混入仓库提交。

## 待远端验收与后续任务

1. 先按用户授权在 `origin = xilele777/Icarus` 完成真实 Actions 对照运行；`upstream = Removel/Icarus` 的正式迁移另行进行。当前保留旧触发器，因此迁移期间会有重复任务。
2. 真实 PR 验证失败/跳过/取消阻止门禁，纯文档正常完成；再切换必需检查至稳定的 `ci-required`，移除旧重复触发器。未修改远端设置。
3. ARM 构建、真实候选推送和 manifest 晋级尚未执行；既有 `latest` 可能没有版本 annotation，首次正式晋级前必须核验并建立基线。未进行真实发布。
4. 应用 Python 依赖锁消费、完整 retained 环境补齐、耗时统计、长期发布归档、扫描与来源证明仍按计划后续推进，不把一次本地运行称为完成长期治理。

未执行真实模型调用，未访问生产数据。Docker Desktop 已启动用于本次隔离测试，未停止未知服务。
