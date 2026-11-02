from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.capabilities import KnowledgeQuery, KnowledgeQueryRequest
from argus_app.plugins.rule_capabilities.http_read import read
from argus_app.plugins.rule_capabilities.responses import failure


class KnowledgeEvidencePlugin(BasePlugin):
    provided_capabilities = (KnowledgeQuery,)

    def __init__(self, plugin_id, client=None, *, kb=''):
        super().__init__(plugin_id)
        self.client = client
        self.kb = kb

    @handles(KnowledgeQueryRequest)
    async def query(self, source, request):
        args = request.args
        if not isinstance(self.kb, str) or not self.kb:
            return failure(request, 'knowledge_scope_unavailable', unavailable=True)
        if not isinstance(args.get('query'), str) or args.get('kb', self.kb) != self.kb:
            return failure(request, 'knowledge_invalid', 'query required; kb must match configured scope')
        return await read(self.client, request, 'POST', '/api/v1/query', json={'kb': self.kb, 'question': args['query'], 'stream': False, 'save': False})
