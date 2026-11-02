"""Server-side business commit; transport identities are supplied by the runtime."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import Fact


class CommitResult(Fact):
    observation_id: Annotated[str, Field(min_length=1, max_length=128)]
    status: Literal['handled', 'no_action']
    reason: Annotated[str, Field(max_length=4096)] = ''


class CommitPayload(Fact):
    expected_version: Annotated[int, Field(ge=1)]
    results: Annotated[list[CommitResult], Field(min_length=1, max_length=20)]
    progress: Annotated[str, Field(max_length=8192)] | None = None
    source_keys: Annotated[list[Annotated[str, Field(min_length=1, max_length=512)]], Field(max_length=16)] | None = None
    rule_path: str | None = None
    rule_hash: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')] | None = None
    lifecycle: Literal['active', 'paused', 'completed'] | None = None

    @model_validator(mode='after')
    def explicit_values(self):
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError('Omit unchanged fields instead of null')
        if ('rule_path' in self.model_fields_set) != ('rule_hash' in self.model_fields_set):
            raise ValueError('Prepared rule path and hash must be supplied together')
        if self.source_keys is not None and len(set(self.source_keys)) != len(self.source_keys):
            raise ValueError('Duplicate source keys')
        return self
