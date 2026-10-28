# PR #12：2026-10-28 提交验证

## 范围与基线

- 来源：`xilele777/Icarus:feat/webui`；目标：`Removel/Icarus:feature`；更新现有 PR #12，不新建 PR、不直接推送上游 feature。
- 开始时 HEAD 为 `9b715f25e1075a87a6afa25ef64e24f98a53fa52`，已推送至 origin/feat/webui。
- 已 fetch 并核对目标 feature 为 `04b203feda13cc52567609e07729c7629258ca5c`，是当前分支祖先。origin/feature 为 `2633c14`，未用它代替上游基线。
- 纳入人物/圈子接入、相关导航/代理/交互修复、浏览器工作区路径历史，以及启动诊断代码与测试。
- 按用户要求排除整个 `docs/spec/2026-10-19-component-lifecycle/` 的本地改动。另排除 `.workbuddy/`、`pelican-bicycle.html` 和 `scripts/install-commands.sh` 的本地移动；这些内容仍保留在工作区。

## WSL 环境与副本

Ubuntu，Node 24.22.0、pnpm 10.32.1、Python 3.14.4。WebUI 使用独立应用环境中的 pytest 8.4.2 / Playwright 1.58.0 与已有 Chromium headless shell；根控制面使用 Agent 既有环境中的 pytest 9.1.1。

共享工作区的 node_modules 当时供 Windows 使用，缺少 Linux Rollup 原生模块。为避免重装影响实际服务，在 WSL `/tmp/icarus-pr-webui-v9ubj9kg` 准备基线与本次拟提交文件的副本，执行 `pnpm install --frozen-lockfile`。逐文件 SHA-256 比对确认拟提交的代码/测试与工作区一致；未创建 Git 分支，也未修改依赖声明或锁文件。安装、测试与构建日志仅保存在忽略的 `apps/webui/test-results/`。

测试副本保留 HEAD 中未纳入 PR 的安装脚本，补齐根基础设施测试所需的既有工作流、Compose、忽略规则与属性文件；Git 属性/忽略检查显式读取既有仓库元数据。初始精简副本缺少这些上下文产生的失败不计为产品回归，补齐后重新执行根全量。

## 结果

| 检查 | 命令/范围 | 结果 |
| --- | --- | --- |
| WSL WebUI 全量 | 副本应用目录：`python -m pytest -q --tb=short --junitxml=... --durations=12`；独立 Vite 5188 | **349 passed、13 skipped，1038.08s，退出 0** |
| WSL 根控制面全量 | 副本根目录：`python -m pytest scripts/tests -q --tb=short` | **369 passed，44.08s，退出 0** |
| WSL 启动诊断聚焦 | 原工作区：`python -m pytest scripts/tests/test_launcher.py scripts/tests/test_lifecycle.py -q --tb=short` | **17 passed，1.33s，退出 0** |
| WSL 静态检查 | `pnpm lint`、`pnpm format:check`、`pnpm typecheck` | 全部通过，退出 0 |
| WSL 生产构建 | `pnpm build`，默认 Vite 配置加载器 | 通过，7.43s，退出 0 |
| Windows/Edge 全量补充 | 原工作区与独立 Vite 5186，应用完整 pytest 套件 | 342 passed、13 skipped、7 failed，930.53s；7 项为既有 POSIX 脚本环境失败 |
| 布局/交互补充 | 人物包与稳定性、8080 部署回归、桌面/手机/暗色及真实只读截图 | 见人物交付记录及交互稳定性记录 |

原 F 盘工作区根全量首次为 366 passed、3 failed：一项因安装脚本被本地移动（不属于提交），两项为 300ms 探测预算内未完成。未修改的 HEAD 控制面在相同挂载路径复现这两项失败；WSL 原生文件系统中的拟提交副本最终 369 项通过。没有为通过检查放宽断言或增加跳过。

Windows 全量的 7 项安装/启动脚本失败与人物交付记录中同 HEAD 基线复现一致；最终验收以 WSL 为主。Windows 包装器在输出日志末尾遇到编码读取错误，实际 pytest 统计与 JUnit 已完整保存，不将包装器异常当作测试通过。

没有向真实业务库写入数据或调用付费模型。13 项跳过为需要显式启用的真实服务/模型集成检查，不能把隔离 HTTP/RPC 回归当作真实写入或模型验收。完成后已退出本次测试使用的 Vite 进程，实际 8080 服务保持运行。

## Node 22 CI 故障复现与修复

首次远端 [WebUI 检查](https://github.com/Removel/Icarus/actions/runs/40663701122)
为 348 passed、13 skipped、1 failed；失败集中在 rela 服务诊断测试，其他工作流通过。
CI 使用 Node 22.23.3，而上述 WSL 全量使用 Node 24.22.0，后者的通过不能替代前者的运行时验证。

在 WSL 的 Node 22.22.0 和与 CI 相同的 22.23.3 下均复现：Gateway 握手失败后，
`WebSocket.close()` 可同步再次触发 error，原错误回调重复关闭连接，引起递归异常，
使并行执行的 rela/Mem0/OpenKB 诊断结果也无法正常输出。

`server/check-services.mjs` 现在统一成功、错误、关闭与超时的结束路径，在清理前标记已结束，
并仅关闭 CONNECTING/OPEN 状态的连接。保留错误监听以处理关闭期间再次产生的 error。
回归测试同时覆盖真实 HTTP 拒绝握手和同步重入的错误事件，以及 rela 的 200/401 返回；
新增场景在旧实现上失败，在修复后通过，也继续校验凭据与业务记录不会输出。

WSL 验证：Node 22.23.3 与 Node 24.22.0 的聚焦文件各 5 passed；Node 22.23.3 的
`test/server` 共 20 passed（66.67s）；`pnpm lint`、`pnpm format:check`、`pnpm typecheck`
与 `git diff --check` 通过。测试沿用上述隔离副本，未更换实际运行服务的 Node 或依赖。

## 相关记录

- [人物工作台交付记录](../2026-10-23-relationship-workspace/plan-delivery-report.md)
- [公共交互稳定性](../2026-10-27-interaction-stability/plan.md)
- [PR 描述](plan-pull-request.md)
