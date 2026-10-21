from domain.enums import EntityStatus
from schemas.common import Kind, PersonId, StrictModel, Text


class CircleCreate(StrictModel):
    name: Text
    kind: Kind
    owner_person_id: PersonId | None = None


class CirclePatch(StrictModel):
    name: Text | None = None
    kind: Kind | None = None
    owner_person_id: PersonId | None = None
    status: EntityStatus | None = None
