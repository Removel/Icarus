"""Read-only status rules used by concrete Session I/O operations."""


def status_is_idle(status):
    return (
        status.get("lifecycle") in {"ready", "running", "unloaded"}
        and not status.get("active_task_ids")
        and all(
            int(status.get(field, 0)) == 0
            for field in (
                "queued_task_count",
                "pending_event_count",
                "pending_plugin_event_count",
            )
        )
    )
