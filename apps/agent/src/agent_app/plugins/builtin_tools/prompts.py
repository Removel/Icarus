"""Task-scoped guidance for the built-in file and command tools."""

BUILTIN_TOOLS_GUIDANCE = "本地文件用 read 分页核对，修改用 write 或 insert；具体续读、覆盖与行前插入规则见工具描述。命令用 bash，核对退出码与输出；耗时任务或服务用可用的后台进程能力跟进，不重复启动。"
