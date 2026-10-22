"""Scope notifications only reduce latency; observations remain in PostgreSQL."""

import asyncio
from contextlib import contextmanager


class ObservationNotifier:
    def __init__(self):
        self._waiters = {}

    @contextmanager
    def listen(self, chat_id):
        event = asyncio.Event()
        self._waiters.setdefault(chat_id, set()).add(event)
        try:
            yield event
        finally:
            self._waiters[chat_id].discard(event)
            if not self._waiters[chat_id]:
                del self._waiters[chat_id]

    def notify(self, chat_id, **kwargs):
        for event in tuple(self._waiters.get(chat_id, ())):
            event.set()
