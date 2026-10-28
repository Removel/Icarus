from pydantic import ValidationError

from errors import ServiceError
from schemas.relation import RelationWrite
from utils.ids import new_id
from utils.pagination import decode_cursor, page
from utils.time import timestamp


class RelationService:
    def __init__(self, repository, persons, circles):
        self.repository = repository
        self.persons = persons
        self.circles = circles

    async def validate_references(self, value):
        await self.persons.get(value.from_person_id)
        await self.persons.get(value.to_person_id)
        if value.scope_circle_id:
            await self.circles.get(value.scope_circle_id)

    async def upsert(self, value):
        await self.validate_references(value)
        data = value.canonical() | {"relation_id": new_id("r"), "updated_at": timestamp()}
        result, created = await self.repository.upsert(data)
        return {"relation": result, "created": created}

    async def patch(self, relation_id, patch):
        old = await self.repository.get(relation_id)
        changes = patch.model_dump(mode="json", exclude_unset=True)
        if not changes or any(v is None for k, v in changes.items() if k not in {"scope_circle_id", "source_ref", "reverse_detail"}):
            raise ServiceError("VALIDATION_ERROR", "patch must contain valid changes")
        fields = set(RelationWrite.model_fields)
        resulting_direction = changes.get("direction", old["direction"])
        if resulting_direction == "two_way" and ({"forward_detail", "reverse_detail", "direction"} & changes.keys()):
            if not changes.get("forward_detail") or not changes.get("reverse_detail"):
                raise ServiceError("VALIDATION_ERROR", "two_way content updates require both details")
        data = {k: v for k, v in old.items() if k in fields} | changes
        if data.get("direction") == "one_way" and "reverse_detail" not in changes:
            data.pop("reverse_detail", None)
        try:
            value = RelationWrite.model_validate(data)
        except ValidationError:
            raise ServiceError("VALIDATION_ERROR", "inconsistent relationship direction or content") from None
        await self.validate_references(value)
        normalized = value.canonical() | {"relation_id": relation_id, "created_at": old["created_at"], "updated_at": timestamp()}
        return await self.repository.replace(relation_id, normalized, old)

    async def invalidate(self, relation_id):
        await self.repository.invalidate(relation_id, timestamp())

    async def list(self, person_id, other_person_id, circle_id, kind, status, include_incoming, include_archived, limit, cursor):
        if other_person_id and not person_id:
            raise ServiceError("VALIDATION_ERROR", "other_person_id requires person_id")
        if person_id:
            await self.persons.get(person_id, include_archived)
        if other_person_id:
            await self.persons.get(other_person_id, include_archived)
        if circle_id:
            await self.circles.get(circle_id, include_archived)
        filters = dict(person=person_id, other=other_person_id, circle=circle_id, kind=kind,
                       status=status, incoming=include_incoming, archived=include_archived)
        rows = await self.repository.list(person_id, other_person_id, circle_id, kind, status,
            include_incoming, include_archived, decode_cursor(cursor, filters), limit + 1)
        return page(rows, limit, filters, "relation_id")
