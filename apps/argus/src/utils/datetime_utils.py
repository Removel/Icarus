"""Normalize SQLite timestamps without changing aware instants."""
from datetime import UTC


def utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
