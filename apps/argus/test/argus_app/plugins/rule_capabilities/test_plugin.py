from types import SimpleNamespace
import httpx
import pytest
from icarus.core.capabilities import CapabilityStatus
from argus_app.contracts.capabilities import ObservationQueryRequest, SemanticJudgeRequest, MemorySearchRequest, KnowledgeQueryRequest, RelationshipSearchRequest
from argus_app.plugins.rule_capabilities.observation_query.plugin import ObservationQueryPlugin
from argus_app.plugins.rule_capabilities.semantic_judgement.plugin import SemanticJudgementPlugin
from argus_app.plugins.rule_capabilities.memory_evidence.plugin import MemoryEvidencePlugin
from argus_app.plugins.rule_capabilities.knowledge_evidence.plugin import KnowledgeEvidencePlugin
from argus_app.plugins.rule_capabilities.relationship_evidence.plugin import RelationshipEvidencePlugin




@pytest.mark.asyncio
@pytest.mark.parametrize('plugin_type,request_type,path,method,args,expected', [
    (MemoryEvidencePlugin, MemorySearchRequest, '/search', 'POST', {'query': 'x', 'user_id': 'attacker'}, {'query': 'x', 'filters': {'user_id': 'trusted-user', 'agent_id': 'agent', 'OR': [{'run_id': 'global'}, {'run_id': 'workspace:workspace'}]}, 'top_k': 10}),
    (KnowledgeEvidencePlugin, KnowledgeQueryRequest, '/api/v1/query', 'POST', {'query': 'x', 'kb': 'k'}, {'kb': 'k', 'question': 'x', 'stream': False, 'save': False}),
    (RelationshipEvidencePlugin, RelationshipSearchRequest, '/persons', 'GET', {'query': 'x'}, None),
])
async def test_external_read_uses_existing_service_contract(plugin_type, request_type, path, method, args, expected):
    async def transport(request):
        import json
        assert request.url.path == path
        assert request.method == method
        if expected is not None:
            assert json.loads(request.content) == expected
        else:
            assert request.url.params['query'] == 'x'
        return httpx.Response(200, json={'result': []})
    async with httpx.AsyncClient(base_url='http://fixture', transport=httpx.MockTransport(transport)) as client:
        if plugin_type is MemoryEvidencePlugin:
            plugin = plugin_type('evidence', client, user_id='trusted-user', agent_id='agent', workspace_key='workspace')
        elif plugin_type is KnowledgeEvidencePlugin:
            plugin = plugin_type('evidence', client, kb='k')
        else:
            plugin = plugin_type('evidence', client)
        response = (await plugin.query('workflow-rules', request_type(channel_id='ch', args=args)))[0]
        assert response.status == CapabilityStatus.OK
        assert response.data == {'result': []}
