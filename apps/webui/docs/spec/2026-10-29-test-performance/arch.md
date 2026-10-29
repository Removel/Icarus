# WebUI 默认测试与 CI 分片

## 页面与离线服务

`test/conftest.py` 的 session 浏览器保持复用，每个用例使用独立上下文。
`blank_page` 只配置 base URL、视口、超时、错误收集与失败截图；`page` 默认返回空白页。
`app_page` 注册离线 Memory HTTP/RPC mock，但不加载页面；`memory_page` 才进入记忆路由并等待列表。

聊天与知识库目录的 `page` 使用 `app_page`，由调用者先注册业务 mock，再打开目标路由。
记忆目录与需要记忆初始状态的 Shell 用例显式使用 `memory_page`。
人物和加载稳定性用例先注册 rela mock，再直接打开人物路由。
`gateway()` 首次直接进入聊天；同一用例内重新设置 mock 时先离开旧文档，确保旧 RPC 连接不再被复用。
已有验证刷新、重连、跨模块导航的测试仍执行原来的交互和断言。

## 分片、构建和失败传播

`test/ci.py` 是 pytest 插件，接受 `--shard-index`（从 1 开始）和 `--shard-total`。
所有 runner 先收集完整 suite，再将排序后的用例轮转分配；`test/server/` 集中在第一组，
因此生产构建/服务夹具不会在多个分片重复启动。新用例自动加入，不维护手工文件列表。

`.github/workflows/webui.yml` 的四个 matrix job 独立运行，`fail-fast: false` 保留各组结果。
第一组先执行 lint/format/typecheck/build；测试通过 `--use-existing-build` 使用这次构建，
缺少 `dist/index.html` 时明确失败。普通本地测试仍默认由生产夹具执行一次构建。
构建使用测试 token，既有生产资源测试继续检查 token 未进入客户端资源。

每组始终上传独立命名的 `webui-test-results-<index>`。
`reports` job 在所有 matrix job 结束后执行，下载报告并运行
`scripts/check_test_reports.py`：

- 报告必须恰好覆盖 1–4 四个分片，collection 完全一致且无重复。
- selected 的并集必须恰好等于 collection，每项恰好一次。
- 所有 selected 都必须实际产生测试阶段报告；所有分片退出码为 0，且没有失败阶段。
- 输出通过/跳过数量，以及每组 setup/call/teardown 总耗时和 elapsed。

`reports` 还要求 matrix job 汇总结果成功，取消或失败不能被完整旧报告误报为成功。
统一 CI 的 WebUI reusable job 同时等待测试矩阵和报告门禁。

## 耗时产物

`--timings-json=<path>` 记录完整 collection、selected、退出码、总耗时和各用例三阶段结果。
只记录用例身份、状态和耗时，不记录服务配置或请求内容。
本地不分片时也可输出相同格式，用于比较 fixture 开销和各模块累计时间。
Actions 汇总结果写入 Step Summary；JSON/JUnit 和失败现场保留 14 天。
