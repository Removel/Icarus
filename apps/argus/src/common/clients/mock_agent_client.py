"""Explicitly simulated processor: no sessions, tools, network or platform actions."""

from datetime import UTC, datetime, timedelta

from ..schemas.wake import WakeOutcome, WakeReceipt


class MockAgentClient:
    def __init__(self, *, completion_delay_seconds=0.05, clock=lambda: datetime.now(UTC)):
        self.delay = completion_delay_seconds
        self.clock = clock
        self._pending = {}
        self._closed = False

    async def submit(self, payload):
        if self._closed:
            return WakeReceipt(accepted=False, error_type='processor_closed')
        existing = self._pending.get(payload.wake_id)
        if existing is not None:
            return WakeReceipt(accepted=existing[0] == payload,
                               error_type=None if existing[0] == payload else 'payload_conflict')
        self._pending[payload.wake_id] = (payload, self.clock() + timedelta(seconds=self.delay))
        return WakeReceipt(accepted=True)

    async def get_outcome(self, wake_id):
        value = self._pending.get(wake_id)
        if value is None or self.clock() < value[1]:
            return None
        payload = value[0]
        return WakeOutcome(wake_id=wake_id, item_ids=tuple(item.item_id for item in payload.items),
                           status='resolved', simulated=True, reason='mock processing completed')

    async def release(self, wake_id):
        self._pending.pop(wake_id, None)

    async def aclose(self):
        self._closed = True
        self._pending.clear()
