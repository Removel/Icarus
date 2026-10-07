"""Administrative memory browsing over the server's bounded listing snapshot."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional


def serialize_memory(row: Any) -> Dict[str, Any]:
    """Match SDK get(): identity and storage fields are not custom metadata."""
    payload = getattr(row, "payload", None) or {}
    promoted = ("user_id", "agent_id", "run_id", "actor_id", "role", "attributed_to", "expiration_date")
    core = {"data", "hash", "created_at", "updated_at", "id", "text_lemmatized", *promoted}
    result = {
        "id": getattr(row, "id", None), "memory": payload.get("data"),
        "hash": payload.get("hash"), "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "metadata": {key: value for key, value in payload.items() if key not in core},
    }
    result.update({key: payload[key] for key in promoted if key in payload})
    return result


def memory_page(
    rows: List[Dict[str, Any]], *, page: int = 1, page_size: int = 12,
    query: str = "", category: Optional[str] = None, user_id: Optional[str] = None,
    run_id: Optional[str] = None, state: str = "all", descending: bool = True,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    today = today or date.today().isoformat()

    def expired(row):
        return bool(row.get("expiration_date") and row["expiration_date"] < today)

    def category_of(row):
        return str((row.get("metadata") or {}).get("category") or "未分类")

    def timestamp(row):
        try:
            return datetime.fromisoformat(row.get("updated_at") or row.get("created_at") or "").timestamp()
        except (ValueError, TypeError, OverflowError, OSError):
            return 0

    query = query.strip().casefold()
    filtered = [row for row in rows
                if (state == "all" or expired(row) == (state == "expired"))
                and (not category or category_of(row) == category)
                and (not user_id or row.get("user_id") == user_id)
                and (not run_id or row.get("run_id") == run_id)
                and query in f'{row.get("memory", "")} {category_of(row)} {row.get("user_id", "")}'.casefold()]
    filtered.sort(key=lambda row: (timestamp(row), str(row.get("id", ""))), reverse=descending)
    total = len(filtered)
    page = min(page, max(1, (total + page_size - 1) // page_size))
    active = sum(not expired(row) for row in rows)
    return {
        "results": filtered[(page - 1) * page_size:page * page_size],
        "page": page, "page_size": page_size, "total": total,
        "counts": {"all": len(rows), "active": active, "expired": len(rows) - active},
        "categories": sorted({category_of(row) for row in rows}),
        "users": sorted({str(row.get("user_id") or "") for row in rows}),
        "scopes": sorted({str(row.get("run_id") or "") for row in rows}),
    }
