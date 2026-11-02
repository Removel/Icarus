"""Directed typed rule capabilities, independent of provider identities."""
from dataclasses import dataclass, field
from typing import ClassVar
from icarus.core.capabilities import CapabilityRequest, CapabilityResponse
from icarus.core.capabilities.catalog import CapabilityContract
from argus_app.contracts.events import EVENTS


@dataclass(frozen=True, kw_only=True)
class RuleResponse(CapabilityResponse):
    data: dict = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class RuleRequest(CapabilityRequest):
    channel_id: str
    args: dict = field(default_factory=dict)
    response_type: ClassVar = RuleResponse


@dataclass(frozen=True, kw_only=True)
class ObservationQueryRequest(RuleRequest):
    capability_id: ClassVar = 'argus.observation_query'


@dataclass(frozen=True, kw_only=True)
class MechanicalMatchRequest(RuleRequest):
    capability_id: ClassVar = 'argus.mechanical_match'


@dataclass(frozen=True, kw_only=True)
class SemanticJudgeRequest(RuleRequest):
    capability_id: ClassVar = 'argus.semantic_judge'


@dataclass(frozen=True, kw_only=True)
class MemorySearchRequest(RuleRequest):
    capability_id: ClassVar = 'argus.memory_search'


@dataclass(frozen=True, kw_only=True)
class RelationshipSearchRequest(RuleRequest):
    capability_id: ClassVar = 'argus.relationship_search'


@dataclass(frozen=True, kw_only=True)
class KnowledgeQueryRequest(RuleRequest):
    capability_id: ClassVar = 'argus.knowledge_query'


@dataclass(frozen=True, kw_only=True)
class RuleControlRequest(RuleRequest):
    capability_id: ClassVar = 'argus.rule_control'


class ObservationQuery(CapabilityContract):
    capability_id = ObservationQueryRequest.capability_id
    access_mode = 'event'
    request_type = ObservationQueryRequest
    response_type = RuleResponse


class MechanicalMatch(CapabilityContract):
    capability_id = MechanicalMatchRequest.capability_id
    access_mode = 'event'
    request_type = MechanicalMatchRequest
    response_type = RuleResponse


class SemanticJudge(CapabilityContract):
    capability_id = SemanticJudgeRequest.capability_id
    access_mode = 'event'
    request_type = SemanticJudgeRequest
    response_type = RuleResponse


class MemorySearch(CapabilityContract):
    capability_id = MemorySearchRequest.capability_id
    access_mode = 'event'
    request_type = MemorySearchRequest
    response_type = RuleResponse


class RelationshipSearch(CapabilityContract):
    capability_id = RelationshipSearchRequest.capability_id
    access_mode = 'event'
    request_type = RelationshipSearchRequest
    response_type = RuleResponse


class KnowledgeQuery(CapabilityContract):
    capability_id = KnowledgeQueryRequest.capability_id
    access_mode = 'event'
    request_type = KnowledgeQueryRequest
    response_type = RuleResponse


class RuleControl(CapabilityContract):
    capability_id = RuleControlRequest.capability_id
    access_mode = 'event'
    request_type = RuleControlRequest
    response_type = RuleResponse


CAPABILITIES = (ObservationQuery, MechanicalMatch, SemanticJudge, MemorySearch, RelationshipSearch, KnowledgeQuery, RuleControl)
RULE_FUNCTIONS = {
    'history': ObservationQueryRequest, 'mechanical': MechanicalMatchRequest,
    'semantic': SemanticJudgeRequest, 'memory': MemorySearchRequest,
    'relationships': RelationshipSearchRequest, 'knowledge': KnowledgeQueryRequest,
}
