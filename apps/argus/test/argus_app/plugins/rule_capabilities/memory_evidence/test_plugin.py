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
async def test_external_failure_not_empty_evidence():
    async def transport(request):
        return httpx.Response(503)
    async with httpx.AsyncClient(base_url='http://fixture', transport=httpx.MockTransport(transport)) as client:
        plugin = MemoryEvidencePlugin('memory-evidence', client, user_id='u', agent_id='a', workspace_key='w')
        response = (await plugin.query('workflow-rules', MemorySearchRequest(channel_id='ch', args={'query': 'x', 'user_id': 'u'})))[0]
        assert response.status == CapabilityStatus.UNAVAILABLE
        assert response.error_code == 'evidence_unavailable'
