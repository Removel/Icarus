"""Conservative validation shared by candidate and lifecycle remote pending queries."""

from datetime import datetime

from agent_app.argus_protocol import HandoffError


def pending_state(value, *, now):
    if not isinstance(value, dict) or type(value.get('pending_count')) is not int or value['pending_count'] < 0:
        raise HandoffError('protocol_error')
    stamp = value.get('last_related_at')
    if stamp is None:
        return value['pending_count'], None
    try:
        related = datetime.fromisoformat(stamp)
        if related.tzinfo is None or related.utcoffset() is None or related > now:
            raise ValueError('Invalid semantic activity timestamp')
    except (TypeError, ValueError, OverflowError):
        raise HandoffError('protocol_error') from None
    return value['pending_count'], related
