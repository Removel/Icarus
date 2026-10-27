"""Small text preview shared by queue and turn navigation."""


def message_preview(message: str, *, limit: int = 120) -> str:
    compact = " ↵ ".join(part.strip() for part in message.splitlines())
    compact = " ".join(compact.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"
