# WebUI 后续体验改动验收

2026-10-05。本记录对应 PR #6 合入后的后续改动；此前的验收记录保留其各自时间点的结果。

## 改动范围

- 知识库导航在桌面支持分类折叠；图标栏及手机保留四个分类入口，保持当前页面、筛选和导航展开状态。
- 记忆每页固定 15 条，桌面五列、窄屏两列或一列。收起侧栏仅调整宽度，不替换卡片或重新请求列表；翻页取消旧动画，筛选与分页等待服务响应。
- 对话复用 Semi 代码高亮及按需加载的思考组件，支持代码和完整 Markdown 回答复制、复制失败反馈，以及折叠后重新展开不重播思考文本。
- 配套的已提交改动包含 Agent 会话标题、软删除，Gateway 公共 RPC，以及 Mem0 筛选后分页和来源字段序列化。
- 更新真实分页验收的数据量与 15 条页面断言；批量启停测试等待异步筛选结果，不立即读取上一页内容。Vite 缓存加入 WebUI 忽略规则。

## 环境与方法

使用本机 WSL2 Ubuntu 26.04，在 Linux 文件系统的独立源码副本中安装 Linux Node 22.22.0、pnpm 10.32.1 和 uv 0.12.23。WebUI 使用 Python 3.12.15、pytest 8.4.2、Playwright 1.58.0；Agent/Gateway 使用现有 Linux Python 3.14 测试环境。

Playwright 尚未识别 Ubuntu 26.04，设置 `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64`，使用与该版本对应的 Linux Chromium headless shell revision 1208。浏览器成功启动并执行实际功能测试。

Windows 工作区的部分未改动文件是 CRLF，Git blob 为 LF。验证副本按 Git 的 Linux 检出内容转换为 LF；未为此修改无关源码。初次格式检查中的 24 项换行报错随后全部消失。

离线副本根 `.env` 为空。真实联调只在子进程中加载本机服务凭据，生产 Node 入口与浏览器均运行在 WSL；Mem0/OpenKB 访问本机实际服务，Gateway 使用独立 Linux 进程与数据目录。只创建并清理带唯一标识的测试数据；未替换用户正在运行的服务。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 记忆服务、Shell、对话受影响专项 | 97 passed |
| 批量启停异步断言修正后的记忆列表目录 | 24 passed |
| 同步基线前的 WebUI 全套 | 170 passed、13 skipped，293.11 秒 |
| 合入 feature 后的 WebUI 最终全套 | 180 passed、13 skipped，288.91 秒 |
| 最新 feature 的 WebUI 生命周期测试（独立导出预览） | 10 passed |
| WSL 真实服务与模型联调 | 5 passed、8 deselected |
| Agent 会话标题、存储与 Runtime 专项 | 46 passed |
| Agent 全套 | 718 passed |
| Gateway 全套 | 19 passed |
| Mem0 分页与序列化 | 3 passed |
| ESLint、完整 Prettier、TypeScript | 通过 |
| Vite 生产构建、Node/Python 编译、diff 检查 | 通过 |

真实联调覆盖 Mem0 修改后刷新、历史、暂停恢复和删除；筛选后分页与浏览器 15 条分页；OpenKB 创建、读取和删除独立知识库；Gateway 模型回复、生成标题、历史刷新、断线恢复、停止执行、按需创建和删除。

浏览器全套默认跳过的真实测试不能算作模型验收。本次没有执行其余 8 项真实测试，包括知识模型编译、完整队列和引导、模型记忆写入与召回、真实图谱；历史记录中的结果不替代本次验证。

桌面折叠导航、稳定五列记忆、手机深色代码块截图已检查，无页面横向溢出。截图、JUnit 和原始日志保留在本地忽略目录，凭据和私有运行日志不作为 PR 附件。

构建主入口 794.09 kB（gzip 243.24 kB），按需加载的思考组件 673.30 kB（gzip 193.74 kB）。Vite 的大块体积提示仍保留，不影响构建成功。

## Git 与 PR 目标

目标仓库为 `Removel/Icarus`，目标分支为 `feature`，来源为 `xilele777:feat/webui`。已确认此前 PR #6 已合入；当前主仓库基线为 `c5f21cf`，`origin/feature` 仍为 `2633c14`，二者不同。

用户授权提交 PR 后，已通过 `889cdf0` 将最新 `upstream/feature`（`c5f21cf`）无冲突合入 `feat/webui`，并通过 `git merge-base --is-ancestor upstream/feature HEAD` 确认包含目标基线。合并后的最终 WSL 回归为 180 passed、13 skipped，完整工程检查、构建与 Node/Python 编译通过。远端 CI 以 PR 的检查结果为准。

额外导出最新基线中的 WebUI 生命周期脚本、测试和仓库控制依赖到 Linux 验证副本，10 项测试通过；此操作没有合并或改写 Git 分支。另逐文件核对 73 个已提交的 WebUI 源码、配置和测试文件，与实际验收副本一致（按 LF 换行比较）。

实现与测试按记忆/导航、对话两组提交，文档单独提交。提交范围与远端检查见 [PR #8](https://github.com/Removel/Icarus/pull/8)，此前各轮改动过程见 [历史执行记录](execution-experience.md)。
