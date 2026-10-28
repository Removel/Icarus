# WebUI 图片输入

依据：[架构与安全评估的多媒体输入链路](../../research/2026-10-23-architecture-and-security-review.md#2-多媒体输入链路)、
[对话体验补齐方案](../2026-10-23-dswui-chat-integration/plan-refinements.md#A-图片上传下一项)。

## 目标与范围

让浏览器选择、粘贴或拖入图片，预览并移除附件，再通过既有 session.submit/steer resources 提交。
支持图片与文字、纯图片、排队、失败恢复、未知结果重试与刷新后的历史图片展示。
首版仅支持 Agent 已有的 PNG、JPEG、GIF、WebP，单张不超过 20 MiB。
不新增模型供应商判断、OCR、PDF/Office 解析，不调整 Agent、Plugin 或模型提供层。

用户已授权 Gateway 的必要接口改动，要求 Agent 尽量保持不动。
本轮不修改 Agent 源码，Gateway 复用公开的 DataPathResolver、SessionIdentity 与 ResourceRef。
上传使用单文件原始二进制请求，避免新增 multipart 依赖；返回既有 ResourceRef 形状。
Gateway 接口设计见 [图片资源接口](../../../../gateway/docs/spec/2026-10-24-image-resources/arch.md)。

## WebUI 实施项

1. 图片附件独立管理本地预览、上传状态、进度、失败重试与移除；上传失败不清除文字草稿。
2. 上传成功后使用 ResourceRef，RPC 不传本地路径、File、Blob 或内联 base64。
3. 草稿、队列、提交快照各自持有图片引用；重试复用 submission_id 及原图片引用。
   纯图片允许发送，附件未完成上传时不发送。引导同样传 resources。
4. 图片纳入导航和刷新保护。切换会话或放弃草稿释放本地对象 URL，取消未完成上传；
   清理操作不删除服务端执行中或历史中正在使用的资源。
5. 解析 user.message/user.correction.resources，使用同源读取接口展示受限尺寸图片；
   消息图片加载后的尺寸变化通知现有滚动与虚拟列表测量。
6. 生产 Node 入口与 Vite 代理图片路径，沿用同源访问及凭据隔离规则。
   不让 WebUI 服务器直接访问 Agent 数据目录。
7. 用户历史消息编辑时，先读取会话 asset 再上传为新 incoming 引用；不把 asset 路径直接提交给
   只接受 incoming 引用的导入器。Markdown 导出保存受入口认证保护的图片 URL，不嵌入图片字节。

## 已实现结构

- `imageResources.ts` 定义 ResourceRef 形状、格式约束与同源读取 URL；不使用模型供应商分支。
- `useImageInputs.ts` 管理对象 URL、可取消的 XHR 上传、进度、错误与重试；
  引用随提交快照消费，队列派发不清除正在编辑的新附件，清理不会恢复已移除的异步结果。
- `DswComposer` 统一接入 file picker、图片 paste、文件 drop 和缩略图，未上传完成时禁用发送。
- `useChatSession` 给 submit/steer 与队列携带 resources，保留原 submission_id 和未知结果核对语义。
- `updates.ts` 解析公共用户事件的 resources，`ChatImages` 提供受限尺寸的懒加载图片、原图链接和失败提示，
  并通知现有滚动与虚拟列表高度测量。图片进入历史缓存的只是引用，不把图片字节写入 IndexedDB。

实现与结果见 [验证记录](plan-validation.md)。

## 验证

- 文件选择、图片粘贴、拖入、预览、移除及格式/体积拒绝。
- 上传失败保留草稿、重试成功后发送；未完成上传不产生提交。
- 纯图片首条消息创建会话后发送，文字与图片一并提交。
- 图片随队列排队、编辑、移除与执行；引导、明确拒绝、未知结果重试保留正确引用。
- 公共事件替换本地消息无重复；刷新历史仍显示图片；损坏图片有可读提示。
- 代理沿用鉴权与 Origin 校验，不转发浏览器凭据；服务端资源读取不可逃逸目录。
- 先运行图片聚焦测试，再运行受影响应用套件、类型、lint、构建及 diff 检查。
  验证使用隔离资源和模拟服务，不上传用户真实文件或调用付费模型。
