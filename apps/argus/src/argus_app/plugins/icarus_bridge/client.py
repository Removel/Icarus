"""Gateway bridge HTTP wire. Caller injects client with Bearer auth and lifecycle."""


class BridgeClient:
    def __init__(self, client):
        self.client = client

    async def ensure_session(self, channel_id: str, request_id: str) -> str:
        response = await self.client.post('/argus/session', json={'channel_id': channel_id, 'request_id': request_id})
        response.raise_for_status()
        return response.json()['session_id']

    async def wake(self, **payload) -> dict:
        response = await self.client.post('/argus/wake', json=payload)
        response.raise_for_status()
        return response.json()

    async def status(self, wake_id: str) -> dict:
        response = await self.client.post('/argus/status', json={'wake_id': wake_id})
        response.raise_for_status()
        return response.json()
