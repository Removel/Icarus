# Icarus WebUI · 交互 Demo

统一的 Icarus 外壳，重构后的「记忆」与「知识库」工作台。用于评审视觉、信息架构和操作流程；当前使用内存示例数据，没有连接 Mem0、OpenKB 或模型服务，刷新恢复初始内容。

## 启动

需要 Node.js 22.12+ 和 pnpm 10.32.1。在 `apps/webui` 执行：

```sh
pnpm install --frozen-lockfile
pnpm dev
```

打开 <http://127.0.0.1:5173>。可直接访问 `/#/memory`、`/#/knowledge` 和 `/#/design`；`Ctrl/Cmd + K` 打开快捷导航。

## 这版可以体验什么

- 记忆：文本搜索、用户与作用范围筛选、失效状态、添加、修正、变更记录、暂停与恢复、删除确认。
- 知识库：切换与新建、资料筛选、导入演示、编译重试、原文阅读、知识正文编辑、来源跳转、关系图示例、质量检查与修复示例、资料移除确认。
- 设计规范：颜色、组件状态、排版、布局和动效规则。

桌面端以单块内容画布展示记忆和资料，底部浮动导航切换应用，内容区独立滚动；分类固定在内容顶部，条目采用紧凑列表，详情按需从右侧打开。手机宽度使用单列内容和底部导航，页面自然滚动。界面使用 Semi UI 的 Universe Design 主题，按钮、表单、标签、弹窗和反馈保持一致。记忆筛选默认收起，支持减少动效偏好。

## 目录

```text
apps/shell/                导航、工作区外壳、设计规范页
packages/ui/               两个模块共用的组件与主题变量
packages/memory-app/       记忆页面及其示例数据
packages/knowledge-app/    知识页面及其示例数据
test/                     按 apps / packages 镜像组织的浏览器功能测试
docs/api-map.md            已核对的服务接口和接入差异
docs/screenshots/          实际浏览器截图
```

当前业务模块作为 workspace 包嵌入外壳，保留各自的页面状态；它们没有调用或导入彼此的业务实现。此 Demo 尚未实现独立前端部署、iframe 嵌入或服务鉴权。

## 验证

启动开发服务后，使用 uv 运行隔离的 pytest + Playwright 测试。首次使用默认 Chromium 时安装浏览器：

```sh
uv run --no-project --with-requirements test/requirements.txt python -m playwright install chromium
pnpm test
pnpm typecheck
pnpm build
```

Windows 已安装 Edge 时，可在 PowerShell 设置 `$env:WEBUI_BROWSER_CHANNEL = 'msedge'` 后直接 `pnpm test`。用 `WEBUI_BASE_URL` 指定其他预览地址。单独验证记忆页：`pnpm test test/packages/memory_app`。

## 演示边界

文件只读取名称与大小，不上传、不解析；编译、检查报告和图谱关系为预设示例。页面阅读器只演示标题和段落，不是完整 Markdown 渲染器。导入不会生成真实知识页面。搜索为当前列表文本筛选。暂停使用以过去的失效日期表示，恢复后变为长期有效。接入时需依据服务真实状态、删除影响预览、认证和错误返回替换这些演示行为，详见接口映射。
