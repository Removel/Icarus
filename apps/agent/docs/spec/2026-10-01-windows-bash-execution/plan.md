# Windows Bash 对话工具修复

2026-10-01，来自 WebUI 对话验收反馈。

## 实现

Bash 由 Agent 内建工具执行，WebUI/TUI 继续使用公共 Gateway 协议。Windows 优先从 Git 安装目录定位原生 Git Bash，避免误用 WindowsApps 的 WSL 启动入口；未安装时返回明确失败。同步接口在专用线程运行异步管道读取，以避开 Windows selectors 不支持匿名管道的限制，并允许同步调用发生在已有事件循环中。

Windows 使用带 KILL_ON_JOB_CLOSE 的原生 Job Object 管理本次命令树；超时、输出限额、取消和正常退出时关闭 Job，清理仍存活的子进程。最终回归发现外部 taskkill 偶发等待过长，因此常规命令清理不再依赖外部 taskkill。异步进程创建使用 shield，在创建期间取消时仍取得进程并清理，避免遗留进程与管道。POSIX 保留原进程组语义。

## 验证

- 内建工具专项 23 项通过，覆盖同步/异步、超时、输出限额、取消、创建期间取消和找不到 Bash。
- Agent 全套：619 passed、45 failed、36 skipped。失败涉及本次未修改的 Knowledge/Skill POSIX 文件系统操作、进程插件及权限语义等；没有扩大为全应用 Windows 移植。
- WebUI 的真实模型用例进一步验证 Gateway → Agent → bash / memory_remember，结果见 WebUI 交付执行记录。

Memory 写入失败来自实际 Mem0 模型配置：infer=true 返回 502，而管理页 infer=false 能保存。已用已有有效凭据修复本地 Mem0 LLM 配置并验证写入/删除；不改变 MemoryPlugin 的抽取与去重协议。
