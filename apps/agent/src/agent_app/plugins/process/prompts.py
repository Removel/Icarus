"""Guidance for the background process tool, independent of process state."""

PROCESS_DISCOVERY = "Use background_process for services, watchers or long commands; bash for short ones. Inspect existing jobs before starting another; stop only authorized, task-owned processes."

PROCESS_GUIDANCE = "Inspect list/get/logs before starting a duplicate. start means spawned, not application readiness; verify logs or health. Stop only authorized, task-owned processes. Log cursors belong to one process and stream; inspect state before retrying a failed start or stop."
