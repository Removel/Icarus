"""Source-message timing, independent of whether a fragment needs a reply."""
from datetime import datetime


def ready_prefix(rows, *, now: datetime, quiet_seconds: float, max_wait_seconds: float, limit: int):
    if not rows:
        return []
    first = last = datetime.fromisoformat(rows[0]['observed_at'])
    selected = []
    boundary = False
    for row in rows:
        stamp = datetime.fromisoformat(row['observed_at'])
        if selected and (len(selected) >= limit or
                (stamp-last).total_seconds() >= quiet_seconds > 0 or
                (stamp-first).total_seconds() > max_wait_seconds):
            boundary = True
            break
        selected.append(row)
        last = stamp
    if (boundary or len(selected) >= limit or quiet_seconds == 0 or
            (now-last).total_seconds() >= quiet_seconds or
            (now-first).total_seconds() >= max_wait_seconds):
        return selected
    return []
