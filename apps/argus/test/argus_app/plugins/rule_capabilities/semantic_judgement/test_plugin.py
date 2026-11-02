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
async def test_semantic_unavailable_is_distinct_from_false():
    plugin = SemanticJudgementPlugin('semantic-judgement')
    response = (await plugin.judge('workflow-rules', SemanticJudgeRequest(channel_id='ch')))[0]
    assert response.status == CapabilityStatus.UNAVAILABLE
    assert response.error_code == 'semantic_unavailable'
    async def semantic(args):
        return {'match': False}
    plugin = SemanticJudgementPlugin('semantic-judgement', semantic)
    response = (await plugin.judge('workflow-rules', SemanticJudgeRequest(channel_id='ch', args={'text': 'x'})))[0]
    assert response.status == CapabilityStatus.OK
    assert response.data == {'match': False}
