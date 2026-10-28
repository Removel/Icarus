import json

from pydantic import model_validator

from domain.enums import RelationDirection, RelationStatus, Source
from schemas.common import Kind, PersonId, SourceRef, StrictModel, Text


class RelationWrite(StrictModel):
    from_person_id: PersonId
    to_person_id: PersonId
    kind: Kind
    direction: RelationDirection
    forward_detail: Text
    reverse_detail: Text | None = None
    scope_circle_id: PersonId | None = None
    source: Source
    source_ref: SourceRef | None = None
    status: RelationStatus = RelationStatus.ACTIVE

    @model_validator(mode="after")
    def valid_directions(self):
        if self.from_person_id == self.to_person_id:
            raise ValueError("relation endpoints must differ")
        if self.direction == RelationDirection.TWO_WAY and not self.reverse_detail:
            raise ValueError("two_way requires reverse_detail")
        if self.direction == RelationDirection.ONE_WAY and "reverse_detail" in self.model_fields_set:
            raise ValueError("one_way must omit reverse_detail")
        return self

    def canonical(self) -> dict:
        data = self.model_dump(mode="json")
        if self.direction == RelationDirection.TWO_WAY and self.from_person_id > self.to_person_id:
            data["from_person_id"], data["to_person_id"] = data["to_person_id"], data["from_person_id"]
            data["forward_detail"], data["reverse_detail"] = data["reverse_detail"], data["forward_detail"]
        data["relation_key"] = json.dumps([
            self.direction, data["from_person_id"], data["to_person_id"], self.kind, self.scope_circle_id
        ], ensure_ascii=False, separators=(",", ":"))
        return data


class RelationPatch(StrictModel):
    kind: Kind | None = None
    direction: RelationDirection | None = None
    forward_detail: Text | None = None
    reverse_detail: Text | None = None
    scope_circle_id: PersonId | None = None
    source: Source | None = None
    source_ref: SourceRef | None = None
    status: RelationStatus | None = None
