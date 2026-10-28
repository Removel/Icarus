# 图片资源 HTTP 接口

Gateway 在既有 `/rpc` 之外提供单张图片的上传和读取，Agent 源码与 RPC 模型保持不变。
`src/resources.py` 复用公开的 `DataPathResolver`、`SessionIdentity` 与 `ResourceRefModel`。
默认数据目录来自与 Runtime 相同的 Agent 配置；嵌入时可向 `create_app` 显式传入
`resource_data_dir`，自定义 Runtime 配置的数据目录也必须同步传入。

- `POST /resources`：Content-Type 为 image/png、image/jpeg、image/gif 或 image/webp，
  body 为单张图片原始字节，不使用 multipart 或 JSON/base64。声明长度和实际读取均限制 20 MiB；
  魔数与声明类型必须一致。原子写入 `incoming/webui/<随机 UUID>.<ext>`，返回 resource_id、media_type。
  客户端文件名不参与路径。后续 submit/steer 沿用既有 ResourceImporter 导入会话 assets。
- `GET /resources?resource_id=webui/<UUID>.<ext>`：读取本接口已上传的临时图片。
- `GET /resources?resource_id=assets/<SHA256>.<ext>&workspace_path=...&session_id=...`：
  确认会话存在后，从该会话资产目录读取历史事件中的图片。

读取限定生成的名称格式与已知图片类型，校验解析后的路径位于数据目录及对应资源根目录，
拒绝末级符号链接和超限文件，响应 no-store、nosniff，不返回 SVG 或任意文件。
图片类型验证沿用现有 Agent 的文件签名口径，不声称完成完整图片解码或像素尺寸校验。

Gateway 仍是本机单用户接口，没有在本功能中引入独立的远程鉴权。
浏览器通过 WebUI 同源代理访问，沿用 WebUI 的 Basic 认证、Origin 校验和凭据隔离。
上传暂存文件与现有 incoming 资源一样不自动删除，移除草稿只释放浏览器预览；
资源磁盘回收策略需由独立需求定义，不在执行或未知结果重试中删除文件。

WebUI 方案与测试入口见 [图片输入计划](../../../../webui/docs/spec/2026-10-24-chat-image-input/plan.md)。

测试位于 `test/test_resources.py` 与 `test/test_image_e2e.py`，联合复跑 14 项通过；
后者覆盖真实 Runtime 与模型适配器及重启后的历史图片读取，外部模型为本地确定性服务。
全套中的既有卸载超时及 HEAD 对照详见
[验证记录](../../../../webui/docs/spec/2026-10-24-chat-image-input/plan-validation.md)。
