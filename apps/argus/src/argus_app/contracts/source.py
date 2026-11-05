"""Explicit source-region facts; account/tenant describe access, not the observer."""
from datetime import datetime, timezone

from .models import Fact


class SourceDescriptor(Fact):
    platform: str
    kind: str
    region_id: str
    account_id: str | None = None
    tenant_id: str | None = None
    parent_id: str | None = None
    display_name: str | None = None


def sent_at(value, *, milliseconds=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return datetime.fromtimestamp(float(value) / (1000 if milliseconds else 1), timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None
