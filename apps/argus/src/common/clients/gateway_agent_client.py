"""Real service adapter; control RPCs never carry a local workspace path."""

from ..schemas.wake import WakeReceipt, wake_wire, request_fingerprint
from .gateway_client import IcarusClient, GatewayTransportError


class GatewayAgentClient:
    simulated = False

    def __init__(self, url, token, *, transport=None, timeout_seconds=5):
        if not token or not url.startswith(('ws://', 'wss://')) or not url.rstrip('/').endswith('/argus/rpc'):
            raise ValueError('Gateway service URL and token are required')
        self._token = token
        self.transport = transport or IcarusClient(url, timeout_seconds=timeout_seconds)

    async def _call(self, method, payload):
        result = await self.transport.call(f'argus.{method}', {'protocol_version': 1, 'caller_token': self._token, **payload})
        if not isinstance(result, dict) or result.get('protocol_version') != 1:
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

    async def aclose(self):
        await self.transport.close()
