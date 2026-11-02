from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.capabilities import MemorySearch, MemorySearchRequest
from argus_app.plugins.rule_capabilities.http_read import read
from argus_app.plugins.rule_capabilities.responses import failure


class MemoryEvidencePlugin(BasePlugin):
    provided_capabilities = (MemorySearch,)

    def __init__(self, plugin_id, client=None, *, user_id='', agent_id='', workspace_key=''):
        super().__init__(plugin_id)
        self.client = client
        self.user_id, self.agent_id, self.workspace_key = user_id, agent_id, workspace_key

    @handles(MemorySearchRequest)
    async def query(self, source, request):
        args = request.args
        if not all(isinstance(value, str) and value for value in (self.user_id, self.agent_id, self.workspace_key)):
            return failure(request, 'memory_scope_unavailable', unavailable=True)
        if not isinstance(args.get('query'), str):
            return failure(request, 'memory_invalid', 'query required')
        limit = args.get('limit', 10)
        if type(limit) is not int or not 1 <= limit <= 50:
            return failure(request, 'memory_invalid', 'limit must be 1..50')
        filters = {'user_id': self.user_id, 'agent_id': self.agent_id,
                   'OR': [{'run_id': 'global'}, {'run_id': f'workspace:{self.workspace_key}'}]}
        return await read(self.client, request, 'POST', '/search', json={'query': args['query'], 'filters': filters, 'top_k': limit})
