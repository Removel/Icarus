# Agent 自主联网工具验证记录

日期：2026-10-26。实现基线：`0cb13f8`，工作分支保持 `feat/webui`。
与 [计划](plan.md)、[当前架构](arch.md) 配套阅读。

## 交付范围

主 Agent 现在通过插件注册的 `web_search(query)`、`web_fetch(url)` 按需检索、直接
读页和补搜。已删除回答前自动研究及其独立规划模型。未修改 ReActAgent、通用
Plugin Runtime、供应商协议、公开搜索事件字段、数据库或依赖声明。
SessionRuntime 仅移除搜索规划器专用设施注入，保留初始上下文的等待关系。

插件实现任务级开关与身份校验、请求/正文/活跃时间预算、循环内共享限流、稳定
来源编号、深拷贝快照、有界取消/停止、迟到响应失效。即使后端忽略取消，外层
工具也能按期返回；插件停止无法确认后台结束时报告失败，不冒充清理成功。

WebUI 仅调整 `ChatSearch.tsx` 的直接 Fetch 状态和文案，并增加两项浏览器回归。
Gateway 无代码变更，沿用原请求与历史协议。不涉及数据迁移。
实现验证阶段未新建分支、提交、合并或推送；已有无关未跟踪文件保持原状。
用户完成人工试用后已明确授权本地 commit；不包含推送或合并。
开始实现前已 fetch `origin/feature`、`upstream/feature`，当时两者均包含于当前 HEAD。

## 本地行为验证

| 检查 | 实际结果 |
| --- | --- |
| Windows 联网插件专项 | **75 passed** |
| Windows 提交幂等专项 | **1 passed**，已在此前插件 70 项 + 提交 1 项的组合执行中通过 |
| WSL 联网插件专项 | **75 passed** |
| 提交前 WSL 联网插件与提交幂等专项 | **76 passed** |
| Gateway `test_web_search_options.py` | **1 passed** |
| WebUI `test/packages/chat_app/test_web_search.py` | **14 passed** |
| Agent 最终全套 | **1086 passed, 36 skipped, 54 failed**；基线与不稳定项说明见下文 |
| WebUI 全套 | **291 passed, 13 skipped, 7 failed**；7 项均在原始 HEAD 基线复现 |
| Agent Linux 目标 mypy | **270 个源文件通过** |
| WebUI `pnpm typecheck`、变更 TSX 的 Prettier | 通过 |
| Agent 源码/测试、WebUI Python 测试 compileall；git diff --check | 通过 |
| Windows 最终 wheel 构建与独立安装 | 通过，15 个 manifest、两个工具及资源/新模块均已核对 |

专项测试使用真实 AgentAssembly、ReActAgent、ToolExecutor、插件注册图和应用
AgentRuntime/SQLite 历史。仅替换模型响应与外部 HTTP 传输，不替换 Agent 自身。
因此这里的“真实 Runtime”指真实应用代码运行，不表示使用真实外部服务。

已验证的关键行为：

- 开关关闭、缺 Key、未知/终态身份、非法业务参数均不发送 HTTP；输入贡献前已
  保存选项快照。合法 `_execution` 经真实执行器剥离，不能改变任务身份或业务额度。
- Search → Fetch → 补搜 → 回答，以及直接 Fetch → 回答；工具结果回到下一步
  模型消息，初始 User Prompt 没有预取正文，稳定 System Prompt 不变。
- 空结果、空正文、鉴权/配额/限流/服务错误与超时不阻断最终回答；失败不创建假页面。
- 重复 URL 和排序变化不重排引用，元数据补充不回写旧工具结果/事件；公开进度不含正文。
- 深度次数、正文上限、思考时间不扣减、锁/跨任务限流等待扣减、同任务重叠区间不双扣。
- 发起前取消不计次/不建进度；等待操作不能覆盖另一个活跃 Fetch 的浏览状态。
- 真实公开流及持久化 sequence 中，Search 和直接 Fetch 取消均满足
  `开始 → stopped,browsing=false → task.finished`，历史再次读取保持一致。
- 吞取消的后端不拖延工具超时；有界 stop、上层更短停止取消、全部输入终态清理、
  同身份重建后迟到结果不能污染新状态。无法确认结束的后台任务被追踪至完成。
- 同一真实 SessionRuntime 连续提交启用、关闭、重新启用的任务，开关、来源、编号及
  Fetch 剩余额度均独立；稳定 System Prompt 保持不变。
- 真实会话停止明确报告吞取消请求未清理，再装配同身份的新会话；释放旧请求后旧任务
  不再发搜索进度，新插件状态保持干净。当前 Runtime 不支持原地重新 start，此处
  按实际的停止/重建流程验证。
- 浏览器直接 Fetch 进行中/成功、来源角标与历史回放、后续累计快照保留旧页面和编号。

先写的工具注册行为测试在旧实现中因工具集合为空而失败；WebUI 直接 Fetch 测试
也先暴露原文案/浏览状态缺失。额外超时回归先复现了吞取消后端拖延返回，再通过
独立受追踪的请求 task 修复。没有将导入失败当成这些行为测试的失败证据。

## 全套失败的边界

Agent 全套不是绿色。最终设置隔离 envfile 的执行得到
`1086 passed, 36 skipped, 54 failed`。使用 `git archive HEAD` 的独立原代码安装，
在同一 Windows 解释器/依赖下按 Unicode 测试身份重跑这 54 项：51 项失败、3 项通过。
这不是对全部失败都“同环境原样复现”的声称。

三个差异已分别核查：

- `test_session_runtime_e2e持久化完整agent_run历史` 在原代码通过；当前代码只重跑
  失败集合时也通过。原测试用约 2 秒轮询等待本地服务/Runtime，此项按不稳定项记录。
- Memory 与 Knowledge 的两项 compose 停止配置测试在当前目录失败，在无根环境文件
  的 archive 中通过。旧 `packages.runtime_environment` 可加载根环境并污染进程值，
  即使设置 `ICARUS_ENV_FILE` 也不保证整个历史测试集隔离。为原代码注入合成的
  `ICARUS_MEM0_LLM_API_KEY`、`ICARUS_OPENKB_LLM_API_KEY` 后，两项同样失败，
  不需要复制实际凭据。相关生产代码与测试均不属于本次变更。

其余失败涉及 Windows 下的 Unix API/路径/编码及既有 Skill、配置、插件测试。
不扩展本次改造去修复这些失败。WebUI 的 7 项失败全部来自原有生命周期脚本测试，
按相同测试身份在原 HEAD archive 中得到 **7 failed**；新浏览器专项全部通过。
跳过项不计作通过。原始全套日志可能包含环境值，只保留本地，不作为交付附件。

Windows 默认目标的 mypy 遇到原有 Unix API 类型错误；本次记录的是 Linux 目标
`--platform linux` 检查通过。WSL 原生打包脚本缺少 `build` 模块，未宣称该脚本
通过；已改用 Windows 等价 wheel 构建、资源检查、独立安装与 `-I` 导入验收。

## 复现入口

使用应用自己的已安装环境，不设置根 PYTHONPATH。核心命令：

```powershell
& apps/agent/.venv/windows/Scripts/python.exe -m pytest apps/agent/test/agent_app/plugins/web_search -q
& apps/agent/.venv/windows/Scripts/python.exe -m pytest apps/agent/test/agent_app/application/test_search_submission.py -q
& apps/agent/.venv/windows/Scripts/python.exe -m mypy --platform linux --config-file apps/agent/pyproject.toml -p icarus -p agent_app
& apps/agent/.venv/windows/Scripts/python.exe -m compileall -q apps/agent/src apps/agent/test apps/webui/test
& apps/gateway/.venv/windows/Scripts/python.exe -m pytest apps/gateway/test/test_web_search_options.py -q
git diff --check
```

WebUI 在其目录执行 `pnpm typecheck`，通过本地 dev server 与 `WEBUI_BASE_URL`
运行原生 Playwright/pytest 测试。验收不连接真实模型或搜索服务。
计划里的 `test_plugin.py`、`test_sources.py`、`test_progress.py` 合并为同层的
`test_tools.py`、`test_lifecycle.py`，避免拆出没有独立职责的测试文件。

## 仍待验收

1. **远端 Fetch SSRF 防护是部署前置条件。** 本地 URL 拒绝测试不证明远端初始
   DNS、逐跳重定向、所有 A/AAAA 地址检查、连接地址绑定或 DNS rebinding 防护。
   未取得部署实现或供应商契约/受控验证环境证据；不能用 MockTransport 返回 403
   来替代。证据齐全前不开放面向任意公网域名的 Fetch。
2. **自动验证未调用真实模型和搜索服务。** 本地 fixture 不能证明真实检索质量、
   模型引用遵循程度或供应商的重定向行为。用户随后在 WSL 人工试用，反馈“验收得
   差不多了，基本完毕”，并授权提交；具体调用日志和逐项结果未提供，不据此宣称
   上述远端安全契约已验收。后续由代理执行真实服务仍须按 `CONTRIBUTING.md`
   取得执行/费用授权。

## WSL 人工验收与运行恢复

用户于 2026-10-26 在 WSL 进行人工试用并确认基本完成。期间补齐了当前锁文件中
已有的 Linux 原生前端依赖，没有改动 `package.json` 或锁文件。共享挂载盘中的
依赖重装曾遇目录重命名权限错误，最终以 frozen-lockfile、offline、copy 模式完成。
缺包的最初成因未查实，不将 Windows 验证直接当作已证实的根因。

默认 Vite 配置打包仍遇 `/mnt/f` 根目录读取 I/O 错误；临时使用受支持的 runner
配置加载方式，完成 WSL 生产构建：

```bash
cd apps/webui
pnpm --filter @icarus/shell exec vite build --configLoader runner
```

此命令不修改项目配置，也不代表默认 `pnpm build` 的挂载盘问题已修复。
恢复后实际确认 WebUI 首页 HTTP 200、Gateway `/health` 为 `status=ready`。
用户人工试用与本地自动检查分别记录，不将服务就绪当作所有外部能力验收通过。

无数据库迁移。回退需在另行授权的变更中恢复本次相关文件，再重建会话工具注册表；
不重置工作树、不删除会话数据或其它人的修改。
