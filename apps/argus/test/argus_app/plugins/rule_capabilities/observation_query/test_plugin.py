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
async def test_history_denies_sources_outside_channel_before_store_query():
    class Store:
        async def channel(self, id):
            return SimpleNamespace(subscriptions={'qq:group:1': None})
        async def observations(self, **kwargs):
            raise AssertionError('unauthorized query reached store')
    plugin = ObservationQueryPlugin('observation-query', Store())
    response = (await plugin.query('workflow-rules', ObservationQueryRequest(channel_id='ch', args={'source_keys': ['secret']})))[0]
    assert response.status == CapabilityStatus.FAILED
    assert response.error_code == 'source_forbidden'
