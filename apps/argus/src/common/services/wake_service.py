"""Advance one durable submission or explicit-outcome step per chat turn."""

import asyncio
from datetime import UTC, datetime, timedelta

from ..schemas.wake import WakeOutcome, WakeReceipt
from ..repositories.observation_repository import StaleSelection


class WakeService:
    def __init__(self, repository, agent, *, submit_timeout_seconds=5.0, max_submit_attempts=3,
                 retry_delays_seconds=(1.0, 2.0), outcome_timeout_seconds=60.0,
                 clock=lambda: datetime.now(UTC)):
        self.repository, self.agent, self.clock = repository, agent, clock
        self.submit_timeout = submit_timeout_seconds
        self.max_attempts, self.retry_delays = max_submit_attempts, retry_delays_seconds
        self.outcome_timeout = outcome_timeout_seconds

    async def _release(self, wake):
        release = getattr(self.agent, 'release', None)
        if release is not None:
            await release(wake.id)

    async def _failed(self, wake, error_type):
        await self.repository.finish_wake(wake.id, status='failed', outcome=None, error_type=error_type)
        await self._release(wake)

    async def _retry(self, wake, error_type):
        if wake.attempt_count >= self.max_attempts:
            await self._failed(wake, error_type)
        else:
            delay = self.retry_delays[wake.attempt_count - 1]
            await self.repository.schedule_retry(wake.id, error_type=error_type,
                next_attempt_at=self.clock() + timedelta(seconds=delay))

    async def advance(self, wake_id):
        wake = await self.repository.get_wake(wake_id)
        if wake.boot_id != self.repository.boot_id:
            return
        now = self.clock()
        if wake.status == 'sent':
            if (now - wake.updated_at).total_seconds() >= self.submit_timeout:
                await self._retry(wake, 'submission_unknown')
            return
        if wake.status == 'waiting':
            if wake.next_attempt_at is not None and now < wake.next_attempt_at:
                return
            try:
                wake = await self.repository.begin_attempt(wake.id, now=now)
            except StaleSelection:
                await self._failed(wake, 'stale_before_submission')
                return
            try:
                receipt = await asyncio.wait_for(self.agent.submit(wake.payload), self.submit_timeout)
                if not isinstance(receipt, WakeReceipt):
                    await self._failed(wake, 'invalid_receipt')
                    return
            except (TimeoutError, ConnectionError, OSError):
                await self._retry(wake, 'transport')
                return
            if receipt.accepted:
                now = self.clock()
                await self.repository.mark_accepted(wake.id, now=now,
                    deadline_at=now + timedelta(seconds=self.outcome_timeout))
            elif receipt.retryable:
                await self._retry(wake, receipt.error_type or 'rejected')
            else:
                await self._failed(wake, receipt.error_type or 'rejected')
            return
        if wake.status != 'accepted':
            return
        if wake.deadline_at is None or now >= wake.deadline_at:
            await self._failed(wake, 'outcome_timeout')
            return
        try:
            result = await asyncio.wait_for(self.agent.get_outcome(wake.id),
                min(self.submit_timeout, (wake.deadline_at - now).total_seconds()))
        except (TimeoutError, ConnectionError, OSError):
            await self.repository.record_poll_error(wake.id, error_type='poll_transport')
            return
        if result is None:
            return
        expected = {item.item_id for item in wake.payload.items}
        if (not isinstance(result, WakeOutcome) or result.wake_id != wake.id
                or set(result.item_ids) != expected or not result.simulated):
            await self._failed(wake, 'invalid_outcome')
            return
        await self.repository.finish_wake(wake.id, status=result.status, outcome=result)
        await self._release(wake)
