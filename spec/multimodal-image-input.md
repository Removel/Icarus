# Multimodal Image Input Spec

## 当前状态

- `ImagePart` 目前只通过 `url` 表达远程图片。
- Agent Core 会将 `TextPart(prompt)` 与 `ImagePart` 组合为同一条多模态 User Message。
- `AgentRuntimeService` 已预留 `input_images` 参数。
- TUI 当前只接收文本输入，尚未提供图片输入入口。

## 目标

- 支持远程图片和本地图片输入。
- 模型协议差异只在模型接入层处理。
- 文本和图片作为同一轮 User Message 提交。
- 上层应用使用统一的图片输入类型。

## 待确定事项

- 图片来源类型的统一表达方式。
- 本地文件向 OpenAI 和 Anthropic 协议的转换方式。
- 文件大小限制、MIME 类型识别与错误反馈。
- 图片是否复制到 Session Assets。
- Trace 中图片引用的记录与脱敏方式。
- TUI 的图片输入交互方式。

## 验证范围

- 远程图片输入。
- 本地图片输入。
- 多张图片输入。
- 非法路径、格式和超限文件。
- 不同模型协议的请求转换。
- Trace 不记录不必要的图片二进制内容。
