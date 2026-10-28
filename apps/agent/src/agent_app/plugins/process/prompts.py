"""Guidance for the background process tool, independent of process state."""

PROCESS_GUIDANCE = """短命令用 bash；服务、watcher 或明显耗时的命令用 background_process，避免前台阻塞和重复启动。start 返回创建成功不代表应用 ready，必须用 get/logs 或实际健康检查确认；使用 list/get/logs 跟进已有 process_id，不把截断日志或旧状态当作完整结果。停止时只操作本次任务拥有且已获授权的进程，不用广泛进程匹配误伤其它服务。"""
