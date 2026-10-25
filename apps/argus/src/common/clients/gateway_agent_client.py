"""Real service adapter; control RPCs never carry a local workspace path."""

from uuid import UUID

from ..schemas.handoff import Scope
from ..schemas.wake import WakeReceipt, wake_wire
from .gateway_client import IcarusClient, GatewayTransportError


class GatewayAgentClient:
    simulated = False

    def __init__(self, url, token, *, transport=None, timeout_seconds=5):
        if not token or not url.startswith(('ws://', 'wss://')) or not url.rstrip('/').endswith('/argus/rpc'):
            raise ValueError('Gateway service URL and token are required')
        self._token = token
        self.transport = transport or IcarusClient(url, timeout_seconds=timeout_seconds)

    async def _call(self, method, payload, *, protocol_version=1):
        version = payload.get('protocol_version', protocol_version)
        result = await self.transport.call(f'argus.{method}', {'protocol_version': version, 'caller_token': self._token, **payload})
        if not isinstance(result, dict) or type(result.get('protocol_version')) is not int or result['protocol_version'] != version:
            raise GatewayTransportError('Invalid Gateway protocol response')
        return result

    async def submit(self, payload):
        result = await self._call('wake', wake_wire(payload))
        return WakeReceipt(accepted=result.get('accepted') is True)

    async def status(self, *, wake_id=None, scope=None):
        if (wake_id is None) == (scope is None):
            raise ValueError('Specify exactly one execution selector')
        result = await self._call('status', {'wake_id': str(wake_id)} if wake_id is not None else {'scope': scope})
        if result.get('found') is False and result.get('execution') is None:
            return None
        if result.get('found') is not True or not isinstance(result.get('execution'), dict):
            raise GatewayTransportError('Invalid Gateway status')
        return result['execution']

    async def cancel(self, wake_id, reason):
        return (await self._call('cancel', {'wake_id': str(wake_id), 'reason': reason}))['execution']

    async def release(self, wake_id):
        return (await self._call('release', {'wake_id': str(wake_id)}))['execution']

    async def sessions(self, scope, *, cursor=None, limit=10) -> dict:
        scope = Scope.model_validate(scope).model_dump()
        if type(limit) is not int or not 1 <= limit <= 20 or (cursor is not None and
                (not isinstance(cursor, str) or len(cursor) > 2048)):
            raise ValueError('Invalid candidate page')
        result = await self._call('sessions', {'scope': scope, 'cursor': cursor, 'limit': limit}, protocol_version=2)
        values, next_cursor = result.get('sessions'), result.get('next_cursor')
        if (set(result) != {'protocol_version', 'sessions', 'next_cursor'} or not isinstance(values, list)
                or len(values) > limit or (next_cursor is not None and
                (not isinstance(next_cursor, str) or len(next_cursor) > 2048))):
            raise GatewayTransportError('Invalid Gateway sessions')
        seen = set()
        for value in values:
            if (not isinstance(value, dict) or set(value) != {'session_id', 'summary', 'last_related_at', 'has_work'}
                    or not isinstance(value['summary'], str) or type(value['has_work']) is not bool
                    or (value['last_related_at'] is not None and not isinstance(value['last_related_at'], str))):
                raise GatewayTransportError('Invalid Gateway candidate')
            try:
                identifier = str(UUID(value['session_id']))
            except (ValueError, TypeError, AttributeError):
                raise GatewayTransportError('Invalid Gateway candidate') from None
            if identifier in seen:
                raise GatewayTransportError('Duplicate Gateway candidate')
            seen.add(identifier)
            value['session_id'] = identifier
        return result

    async def notify(self, wake_id, session_id) -> dict:
        result = await self._call('notify', {'wake_id': str(UUID(str(wake_id))),
            'session_id': str(UUID(str(session_id)))}, protocol_version=2)
        if (set(result) != {'protocol_version', 'accepted', 'merged'} or result['accepted'] is not True
                or type(result['merged']) is not bool):
            raise GatewayTransportError('Invalid Gateway notify')
        return result

    async def aclose(self):
        await self.transport.close()
