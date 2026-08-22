# TUI Character Theme Design｜TUI 角色主题设计

## 目标

在保留黑色终端底色和开发工具可读性的前提下，让 Icarus TUI 具有参考角色的视觉辨识度。
本次只调整品牌展示、色彩语义和视觉层级，不改变消息、队列或 Runtime 行为。

## 视觉语言

- 羽翼浅粉用于 Logo、Agent 标签和输入焦点，是主要品牌色；
- 缎带粉用于用户消息和次级品牌强调；
- 暖象牙白用于正文，降低纯白在黑底上的刺眼程度；
- 暖金只表示 Tool 正在运行和待处理队列；
- 眼眸绿只表示 Tool 成功或 Ready；
- 红色只表示失败、取消和危险，不用于正常完成状态；
- 背景使用暖黑和带粉调的深色表面，避免蓝灰色偏离角色气质。

## Logo 与响应式边界

欢迎卡在宽度大于 70 且高度大于 20 的终端展示用户提供的 8 行 ASCII Logo。窄屏或短屏隐藏
完整 Logo，显示紧凑的 `Icarus` 标题，优先保留 Conversation、Queue 和 Composer 空间。

## 组件映射

| 组件 | 颜色语义 |
|---|---|
| 应用标题、欢迎 Logo、Assistant 边线、Composer 焦点 | 羽翼浅粉 / 缎带粉 |
| User 边线与标签 | 较深缎带粉 |
| Tool 运行中、Queue 标题 | 暖金 |
| Tool 成功、Ready | 眼眸绿 |
| Tool 失败、Agent Error、Task failed/cancelled | 红色 |
| Workspace、帮助文案、普通状态 | 暖灰粉 |

## 验收

- 完整 Logo 在 100 列常规终端中不截断；
- 58 列窄屏和 12 行短屏隐藏完整 Logo，不挤压主要交互区域；
- running、completed、failed 三种 Tool 状态仅凭颜色和文字均可区分；
- 失败与取消使用红色，成功不使用红色；
- Textual 组件测试、交互测试和视觉快照全部通过，并人工查看实际 SVG。
