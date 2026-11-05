"""Guidance for the background process tool, independent of process state."""

PROCESS_GUIDANCE = """短命令用 bash；服务、watcher 或耗时命令用 background_process。先用 list/get/logs 跟进已有进程，避免重复启动。start 不代表应用 ready，需日志或健康检查确认；停止仅限任务拥有且已获授权的进程。"""
