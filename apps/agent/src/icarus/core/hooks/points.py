"""Consume hooks cannot alter routing facts encoded in event payloads."""

from dataclasses import fields, is_dataclass

_CONTROL_FIELDS = frozenset({
    "event_id", "task_id", "occurred_at", "correlation_id", "timeout_seconds", "expires_at",
    "source_plugin_id", "owner_plugin_id", "region", "input_id", "complete_for_input",
    "status", "fatal", "code", "model_role", "tools", "run_id", "request_event_id",
    "applied_before_step", "tool_call", "result", "response", "step",
})


def preserve_event_control(previous, result) -> None:
    if type(previous) is not type(result):
        raise ValueError("Hooks cannot change event type")
    if not is_dataclass(previous):
        raise TypeError("Only declared dataclass event values can be rewritten")
    for field in fields(previous):
        if field.name in _CONTROL_FIELDS and getattr(previous, field.name) != getattr(result, field.name):
            raise ValueError(f"Hooks cannot change event control field: {field.name}")
