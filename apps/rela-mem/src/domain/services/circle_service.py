from errors import ServiceError
from utils.ids import new_id
from utils.pagination import decode_cursor, page
from utils.time import timestamp


class CircleService:
    def __init__(self, repository, persons):
        self.repository = repository
        self.persons = persons

    async def create(self, value):
        if value.owner_person_id:
            await self.persons.get(value.owner_person_id)
        now = timestamp()
        data = value.model_dump(exclude_none=True) | {"circle_id": new_id("c"), "status": "active", "created_at": now, "updated_at": now}
        return await self.repository.create(data)

    async def get(self, circle_id, include_archived=False):
        return await self.repository.get(circle_id, include_archived)

    async def list(self, query="", kind=None, include_archived=False, limit=20, cursor=None):
        filters = {"query": query, "kind": kind, "archived": include_archived}
        after = decode_cursor(cursor, filters)
        rows = await self.repository.list(query, kind, include_archived, after, limit + 1)
        return page(rows, limit, filters, "circle_id")

    async def patch(self, circle_id, value):
        await self.get(circle_id, True)
        changes = value.model_dump(mode="json", exclude_unset=True)
        if not changes or any(v is None for k, v in changes.items() if k != "owner_person_id"):
            raise ServiceError("VALIDATION_ERROR", "patch must contain valid changes")
        if changes.get("owner_person_id"):
            await self.persons.get(changes["owner_person_id"])
        changes["updated_at"] = timestamp()
        return await self.repository.patch(circle_id, changes)
