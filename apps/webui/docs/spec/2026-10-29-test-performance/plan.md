# WebUI 测试耗时优化

## 目标与基线

保留全部默认测试和真实服务 opt-in 边界，缩短 Actions 等待时间，并提供可复查的
用例清单与 setup/call/teardown 耗时。暂不迁移测试框架或将浏览器断言改成单元测试。

基线代码为 `2edab90`（个人仓库 `xilele777/Icarus:feature`）。主仓库
`Removel/Icarus:feature` 是独立基线，不在本次工作中自动合并。

已核对的远端证据：

| 运行 | WebUI job | 测试步骤 | 测试结果 |
| --- | --- | --- | --- |
| [Removel 40850888458](https://github.com/Removel/Icarus/actions/runs/40850888458) | 12 分钟 | 11 分 10 秒 | 351 passed、13 skipped |
| [xilele777 40836393498](https://github.com/xilele777/Icarus/actions/runs/40836393498) | 14 分 55 秒 | 13 分 58 秒 | 354 passed、13 skipped |

两轮代码与 runner 不同，不将这两行用作优化前后对比。

## 实施顺序

1. 记录 collection、选择清单、退出码及各用例分阶段耗时。
2. 拆分空白页、带离线 Memory mock 的应用页及已打开的记忆页；聊天直接加载目标路由。
3. 将整个 collection 按用例轮转分为四组，生产服务测试集中在第一组。
   每组使用独立 runner、端口、浏览器上下文、构建目录与报告产物。
4. 汇总四组报告，拒绝缺组、漏跑、重复用例、collection 不一致和失败。
5. 先运行 focused 回归，再验证完整套件、workflow、编译及差异。
   对比同一 Linux 工具链下的基线与优化结果；远端 Actions 性能须推送后单独验证。

第一轮远端总耗时目标为 5–8 分钟，以实际 Actions 运行结果为准。
提交、推送和远端触发遵守仓库授权规则。

## 验收

- 原有用例身份全部保留；不新增 skip、不放宽超时、不掩盖错误。
- 新增测试覆盖直接打开聊天、替换已有 RPC mock、分片完整性及失败阻断。
- 默认串行入口仍可直接运行；生产测试单独运行时仍自行构建。
- 四组汇总后的原有 13 个 opt-in skip 不变，并区分新增基础设施测试。
- 报告同时保留 JUnit、截图/HTML、服务日志、collection 和分阶段耗时。

## 本地实施与验证结果

2026-10-29：页面夹具拆分、四分片、构建复用、分阶段报告及完整性门禁已实现。
本地验证完成后，用户明确授权提交、推送至个人仓库 `origin/feat/webui` 并手动运行 WebUI Actions；
远端验收与本地证据分开记录。
未修改应用运行源码、依赖声明、测试超时或既有断言；原有浏览器用例的身份保持不变。

使用同一台 WSL Ubuntu 26.04、Node 22.23.3、pnpm 10.32.1、Python 3.12.15、
pytest 8.4.2、Playwright 1.58.0。浏览器使用 Ubuntu 24.04 构件，平台 override 只用于本地；
CI 仍直接运行受支持的 Ubuntu 24.04。
基线和优化版本使用独立 Linux 目录，不复制开发者 `.env`，测试均通过离线 runner 启动。
先完成基线，再启动优化版本，未同时执行两套浏览器测试。

| 检查 | 结果 |
| --- | --- |
| 原始完整串行套件，仅增加相同的计时插件 | 354 passed、13 skipped；pytest elapsed 762.25 秒（12 分 42 秒） |
| 优化后完整四分片 | 368 passed、13 skipped；四进程总等待 196.24 秒（3 分 16 秒，含 dev server 启动） |
| 用例清单核对 | 原有 367 项全部保留，新增 14 项；381 项恰好各执行一次；13 个 skip 身份完全一致 |
| lint / format:check / typecheck / build | 全部通过，合计 23.01 秒；与上述测试时间分开记录 |
| 分片/runner 最小回归 | 16 passed（Windows 和 Linux 均通过） |
| CI 影响选择/workflow/汇总门禁相关测试 | 44 passed |
| actionlint 1.7.7、Python 编译、差异检查 | 通过；actionlint 未启用可选的 shellcheck/pyflakes |

四个分片分别覆盖 111、90、90、90 项，pytest elapsed 为 192.46、189.23、186.26、193.01 秒，
分配没有出现明显长尾。相对串行基线，本地等待时间缩短约 **74.3%**；
静态检查与四分片测试串行衔接合计约 3 分 39 秒，不含依赖安装及远端排队/上传/汇总开销。

阶段累计从 setup 320.96 / call 437.45 / teardown 2.28 秒，变为四组累计
118.26 / 634.46 / 2.96 秒。首次页面加载部分从 setup 移到了 call，且四个进程共享本机资源；
不能把 setup 的下降直接当作净节省，也不能据此宣称总 CPU 成本下降。本轮明确验证的是等待时间的缩短。

浏览器 focused 首轮出现两项失败：新增断言误把内部路由更新计入整页加载，另一个圈子用例仍从空白 URL 拼路由。
两处修正后 focused 2 passed，随后完整四分片全部通过，不将首轮称为全通过。

可复查的本地 JSON、JUnit、日志和汇总位于忽略的
`apps/webui/test-results/test-performance-20261029/`。
本地验证阶段没有提交、推送或触发远端 workflow；随后按明确授权进行的远端验收见下一节。

## 远端 Actions 验收

用户授权后，将应用实现、workflow 接线及文档分别提交为 `7b21df5`、`2c3073f`、`b8ebece`，
推送至个人仓库 `xilele777/Icarus:feat/webui`，没有推送 upstream 或修改 `feature`。
手动触发 [运行 40870241958](https://github.com/xilele777/Icarus/actions/runs/40870241958)，
受测 SHA 为 `b8ebece0255021bdc8005c86c3c58289f7d12182`，最终 `conclusion=success`。

北京时间 2026-10-29 20:04:01 开始，20:07:37 完成，完整流程 **3 分 36 秒**，
包含依赖安装、静态检查/构建、浏览器准备、四组测试、产物上传及报告汇总。
旧版个人仓库 [运行 40836393498](https://github.com/xilele777/Icarus/actions/runs/40836393498)
的 WebUI job 为 14 分 55 秒，本轮完整 WebUI workflow 等待缩短约 **75.9%**。
这是两轮真实运行的对比，runner 速度存在波动，不代表所有后续运行都固定为 3 分 36 秒。

| 分片 | 完整 job | 测试步骤 | pytest elapsed | 覆盖项数 |
| --- | --- | --- | --- | --- |
| 1 | 3 分 08 秒 | 2 分 18 秒 | 136.38 秒 | 111 |
| 2 | 2 分 49 秒 | 2 分 15 秒 | 133.30 秒 | 90 |
| 3 | 3 分 22 秒 | 2 分 46 秒 | 163.12 秒 | 90 |
| 4 | 2 分 52 秒 | 2 分 20 秒 | 138.39 秒 | 90 |

四个 job 均成功；第一组静态检查及生产构建通过，用时 19 秒。
报告汇总 job 通过，用时 6 秒，实际输出：
`368 passed, 13 skipped; 381 tests covered exactly once.`
默认真实服务 opt-in 边界保持不变，各组 JUnit、耗时 JSON、截图及服务日志已上传，保留 14 天。
原定 5–8 分钟目标已由本轮真实 Actions 验收，未以本地计时代替远端结果。

本地归档的 GitHub job/run 元数据及完整性门禁日志位于忽略的
`apps/webui/test-results/actions-40870241958/`。
后续验证记录提交仅更新文档，不改受测实现。改动尚未合入 `feature`，常规集成 CI 在合入后使用新分片。
