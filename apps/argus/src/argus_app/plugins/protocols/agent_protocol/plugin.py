from icarus.core.plugin import BasePlugin
from argus_app.contracts.capabilities import RuleControlRequest

KINDS = {'delivered': 'agent.delivered', 'check': 'rule.check_requested', 'commit': 'agent.commit_requested'}


class AgentProtocol(BasePlugin):
    def __init__(self, plugin_id, store):
        super().__init__(plugin_id)
        self.store = store

    async def submit(self, binding_id: str, request_id: str, kind: str, payload: dict) -> dict:
        kind = KINDS.get(kind, kind)
        if kind not in KINDS.values():
            raise ValueError('unsupported agent control kind')
        row = await self.store.ingest(source_key=f'agent:{binding_id}', message_id=request_id, kind=kind, content=payload, detail={'binding_id': binding_id, 'payload': payload})
        if 'result' in row.detail:
            return row.detail['result']
        channel = await self.store.bound_channel(binding_id)
        response = await self.request(RuleControlRequest(channel_id=channel.id, args={'observation_id': row.id}))
        return response.unwrap().data
