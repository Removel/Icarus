from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.capabilities import RelationshipSearch, RelationshipSearchRequest
from argus_app.plugins.rule_capabilities.http_read import read
from argus_app.plugins.rule_capabilities.responses import failure


class RelationshipEvidencePlugin(BasePlugin):
    provided_capabilities = (RelationshipSearch,)

    def __init__(self, plugin_id, client=None):
        super().__init__(plugin_id)
        self.client = client

    @handles(RelationshipSearchRequest)
    async def query(self, source, request):
        query = request.args.get('query')
        if not isinstance(query, str) or len(query) > 256:
            return failure(request, 'relationship_invalid')
        return await read(self.client, request, 'GET', '/persons', params={'query': query, 'limit': 20})
