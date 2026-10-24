# 图片输入验证记录

日期：2026-10-24（Asia/Shanghai）。基线 `50996d8`，分支 `feat/webui`。
已 fetch 并确认 HEAD 包含当时的 upstream/feature 与 origin/feature。
实现与验证阶段没有创建分支、合并或推送；原有根目录演示文件不纳入改动。
验证完成后经用户授权，将 Gateway 接口、WebUI 实现与测试、文档分别提交。

## 验证范围

- WebUI：Windows 自有 `.venv`、Playwright 与系统 Edge，独立 Vite 端口 5179。
- Gateway：WSL Ubuntu 自有 `.venv`（Python 3.14），隔离临时数据目录。
- 浏览器测试模拟 RPC/HTTP；完整图片链路使用真实 Gateway、Runtime、Plugin、SQLite 与
  模型适配器，仅外部模型和资料服务由本地确定性 HTTP 服务替代。
- 不上传真实用户文件，不读取生产会话，不调用付费模型，不重启已有业务服务。
- Agent、共享协议包与依赖声明均无变更。Gateway 只新增资源 HTTP 模块及 create_app 接线。

## 图片专项

`test/packages/chat_app/test_images.py` 首批 10 项通过，后补桌面/手机大图布局 2 项通过。
覆盖：

- 文件选择、粘贴、拖入、预览、移除、不支持的格式与超限文件。
- 图片与文字、纯图片首条消息的自动会话创建、上传失败保留草稿及重试。
- 队列编辑、队列派发不清理新草稿附件、纯图片引导。
- 提交拒绝恢复图片；未知结果重试复用图片引用和 submission_id。
- 公共事件替换本地消息不重复，刷新读取历史图片。
- 编辑历史图片先重新上传为 incoming 引用，避免直接提交 asset 路径。
- 取消未完成上传不会恢复已移除的附件；图片草稿触发离开保护。
- 损坏/缺失图片显示可读提示，390 px 和 1440 px 下大图受限且页面不横向溢出。

已人工查看 `test-results/image-input-390.png` 与 `image-message-1440.png`，确认预览卡片、
图片消息及工具栏布局。截图是本地忽略产物，不进入提交。

Gateway `test_resources.py` 首轮 13 项通过，覆盖四种图片签名、MIME 不匹配、空文件、
声明长度及实际流超限、路径拒绝、会话边界、符号链接逃逸、上传后按原始字节读取和既有导入器转换。
收尾时 `test_resources.py` 与 `test_image_e2e.py` 联合复跑 **14 passed**。

`test_image_e2e.py` 单项通过：原始图片上传 → ResourceRef → 纯图片 session.submit →
未修改的 Agent 导入器 → OpenAI 适配器的 image_url/data URI → 本地模拟模型 → 历史 asset 引用。
验证同 submission_id 重试返回同一 task；重启 Runtime 后历史仍只有一条用户记录，图片可读取。
这是本地模拟模型端到端验证，不等同于真实模型图像识别质量验收。

## 回归与既有失败

WebUI 执行范围：`test/packages/chat_app test/apps/shell test/server`。
首次结果 **169 passed、5 failed、2 errors**，耗时约 8 分 50 秒。
原始报告：本地 `test-results/image-regression.xml`。

- 4 个聊天布局参数化失败及 2 个 teardown error：旧夹具 records 缺少 session_id，
  与本轮未改动的公共 RPC 测试夹具按 record['session_id'] 筛选冲突。
  该问题已在性能改造的独立 HEAD 应用上复现，见
  [既有失败对照](../2026-10-24-webui-performance/plan-validation.md#完整测试与既有问题)。
  未修改旧夹具或跳过失败。
- 另 1 个新增代理测试失败：插入新测试时，原 WebSocket 测试的末尾断言误归入新函数，
  出现 NameError。已将断言放回原函数；鉴权、Origin、二进制逐字节转发与凭据移除断言均通过。
  服务器文件修正后复跑 **13 passed**，报告为本地 `test-results/image-server.xml`。

Gateway 全套结果 **49 passed、1 failed**，耗时约 2 分 27 秒。
失败为 `test_prompt_contributor_to_model_and_private_instructions_survive_real_gateway_restart`，
在 session.unload 等待响应时超过 10 秒。单项复跑同样失败。
随后用 `git show HEAD:apps/gateway/src/app.py` 编译原始 create_app 到独立模块，
同一环境与相同 Agent/协议源码再次运行同一测试，不注册本轮图片路由，仍在相同卸载位置超时。
基线报告：WSL `/tmp/icarus-image-gateway-baseline.xml`；全套报告：
`/tmp/icarus-image-gateway-regression.xml`。未扩大范围修改 Agent 卸载或测试超时。

本轮没有重新执行未受影响的记忆、知识库与生命周期测试，也不宣称整个仓库测试全绿。

## 构建与静态检查

- `pnpm typecheck`、`pnpm lint`、全部改动源码的 Prettier 检查通过。
- `pnpm build` 通过，最大 JS chunk 仍为 392.04 KB；聊天 chunk 348.99 KB，gzip 108.40 KB。
- Python compileall 与 `git diff --check` 通过。
- 未新增依赖、环境变量或 Agent 数据迁移。使用时须更新并重启 Gateway；生产还需构建 WebUI 并重启 Node。

## 保留边界

当前文件类型校验与 Agent 保持同样的签名口径，不是完整图片解码或像素尺寸校验。
暂存目录 `incoming/webui` 不自动回收，移除浏览器附件不删除服务端资源；
会话资产和执行中的引用不由前端清理。Gateway 保持本机单用户接口边界，浏览器经过 WebUI 认证代理。
Markdown 导出包含受入口认证保护的图片链接，没有内嵌图片，离线查看需另存图片。
