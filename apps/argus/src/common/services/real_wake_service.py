"""One bounded remote-control step per scheduler turn, separate from mock outcomes."""

from datetime import UTC, datetime, timedelta

from ..clients.gateway_client import GatewayBusinessError, GatewayTransportError
from .observation_access_policy import ObservationAccessPolicy


class RealWakeService:
    def __init__(self, repository, agent, *, policy=None, clock=lambda: datetime.now(UTC)):
        self.repository, self.agent, self.clock = repository, agent, clock
        self.policy = policy or ObservationAccessPolicy()

    async def advance(self, wake_id):
        wake = await self.repository.get_wake(wake_id)
        if wake.handoff.get('local_terminal') or wake.handoff.get('release_complete'):
            return
        if (wake.boot_id != self.repository.boot_id and wake.handoff.get('definite_not_reserved')
                and not wake.handoff.get('earlier_submission_unknown')):
            await self.repository.update_handoff(wake.id, {'local_terminal': True}, status='failed')
            return
        if wake.next_attempt_at is not None and self.clock() < wake.next_attempt_at:
            return
        try:
            if wake.handoff.get('submission_may_have_run'):
                view = await self.agent.status(wake_id=wake.id)
                if view is not None:
                    await self.repository.update_handoff(wake.id, {'phase': view['phase'], 'remote': view,
                        'release_complete': view['release_complete']})
                    if view['release_complete']:
                        return
                    if view.get('execution_stopped'):
                        try:
                            await self.agent.release(wake.id)
                        except GatewayBusinessError:
                            pass
                    return
                if wake.boot_id != self.repository.boot_id:
                    await self.repository.update_handoff(wake.id, {'phase': 'reconciling', 'error_type': 'execution_unknown'})
                    return
                # A missing response may race an in-flight submit; reuse the original current-boot identity.
            elif wake.boot_id != self.repository.boot_id:
                await self.repository.update_handoff(wake.id, {'mode': 'gateway', 'local_terminal': True}, status='failed')
                return
            else:
                scope = self._scope(wake.chat_id)
                owner = await self.agent.status(scope=scope)
                if owner is not None:
                    await self._defer(wake, 'scope_busy')
                    return
                items = await self.repository.wake_items(wake.id)
                if any(not self.policy.allows(item) for item in items):
                    await self.repository.update_handoff(wake.id, {'mode': 'gateway', 'local_terminal': True}, status='failed')
                    return
            prior_unknown = wake.handoff.get('submission_may_have_run', False) and not wake.handoff.get('definite_not_reserved', False)
            await self.repository.update_handoff(wake.id, {'mode': 'gateway', 'submission_may_have_run': True,
                'earlier_submission_unknown': wake.handoff.get('earlier_submission_unknown', False) or prior_unknown,
                'definite_not_reserved': False}, status='sent')
            receipt = await self.agent.submit(wake.payload)
            if receipt.accepted:
                await self.repository.update_handoff(wake.id, {'phase': 'preparing'}, status='accepted')
            else:
                await self._defer(wake, 'submission_unknown')
        except GatewayBusinessError as error:
            if error.code in {'scope_busy', 'capacity_busy'}:
                current = await self.repository.get_wake(wake.id)
                if not current.handoff.get('earlier_submission_unknown'):
                    await self.repository.update_handoff(wake.id, {'definite_not_reserved': True})
                await self._defer(wake, error.code)
            else:
                await self.repository.update_handoff(wake.id, {'phase': 'reconciling', 'error_type': error.code})
        except (GatewayTransportError, ConnectionError, TimeoutError, OSError):
            await self.repository.update_handoff(wake.id, {'phase': 'reconciling', 'error_type': 'unavailable'})

    async def _defer(self, wake, error):
        await self.repository.update_handoff(wake.id, {'mode': 'gateway', 'phase': 'reconciling', 'error_type': error})
        await self.repository.schedule_retry(wake.id, error_type=error, next_attempt_at=self.clock()+timedelta(seconds=1))

    @staticmethod
    def _scope(chat_id):
        source, account, kind, target = chat_id.split(':', 3)
        return {'source': source, 'account_id': account, 'chat_kind': kind, 'external_id': target}
