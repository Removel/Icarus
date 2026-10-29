# Platform 阶段执行清单

日期：2026-10-28。所属总计划：[plan.md](plan.md)。

本清单只覆盖本轮用户指定的 platform 工作，不将总计划八项任务视为同一交付。

## 交付范围

- [x] `ClipboardContent` frozen dataclass、明确的不可用错误、保留旧读取签名。
- [x] `host.py` 收口 WSL / Wayland / X11 探测、PowerShell 固定代码/数据通道、Windows 文件路径映射。
- [x] macOS / Windows / Linux / WSL 类型化读取与系统文本写入代码路径。
- [x] 文件优先于图片、图片优先于文本；多文件整体拒绝。
- [x] Windows 剪贴板忙碌有限重试、全宽句柄类型、成功内存所有权转移/失败释放。
- [x] stdlib DIB→PNG；PNG/JPEG 真实内容解码验证、20 MiB / 40 百万像素限制。
- [x] 单次类型化快照进入 AttachmentsService；受控图片副本、源文件保留、原有释放生命周期。
- [x] HTTP(S) 统一校验和默认浏览器 platform opener。
- [x] WSL 通过固定 PowerShell + URL stdin，避免 cmd 解析 URL 元字符。
- [x] DesktopService 线程化 I/O、复制序列化、取消后真实操作排空和 App 退出接线。
- [x] App 注入真实 platform opener；新增功能不直接修改 Widget/Gateway 分层。
- [x] 聚焦验证：151 passed（platform、services、App desktop 生命周期）。
- [x] WSL 固定脚本语法经 Windows PowerShell AST parser 解析通过，未执行剪贴板/浏览器动作。
- [x] 最终独立审查及修复：原生 owner、Linux MIME/运行时回退/daemon 管道、macOS bridge/转换前限额、可取消关闭排空、错误分类与常见 bitfields DIB 已补回归。
- [x] 最终完整套件重跑：`make test-tui` 473 passed；compileall / diff check 通过。
- [ ] macOS / Windows / Linux Wayland / Linux X11 / WSL+Windows Terminal 实机矩阵。

## 后续 UI 状态（2026-10-29）

原平台分段之后，聊天跨消息/窗口选区、Ctrl+C 优先复制、安全剪切 UI、裸 URL 点击/拖选/双击仲裁、
键盘 message→URL picker 和欢迎说明均已实现。完整回归更新为 **501 passed**，见总 plan 与 validation。
真实各平台验收仍未执行，按用户要求留本地 feat/tui 待真实使用，不发布。

## Git 状态

本轮前半段曾创建 `49420ff`、`f281693`、`fbc98d8` 三个提交；这并非用户明确授权的 Git 操作。
当时停止追加 TUI 提交，修正保留在工作区。用户随后明确授权将远端 `feat/agent` 合入远端 `feature`，
并同步本地 `feature` 与 `feat/tui`、保留改动。2026-10-29 用户进一步明确授权本地提交 TUI 开发成果，
但不推送或合入远端 feature；本地 TUI 交付仍等待用户真实使用验收。

本地实现提交：`120cb1b`（平台/service 及测试）、`9de5f29`（桌面 UI 交互及测试）。
