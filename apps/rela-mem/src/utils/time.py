from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def timestamp() -> str:
    return utc_now().isoformat()
