import json

from utils.pagination import decode_cursor, page
from utils.time import timestamp


class MemberService:
    def __init__(self, repository, persons, circles):
        self.repository = repository
        self.persons = persons
        self.circle_service = circles

    async def upsert(self, circle_id, person_id, value):
        await self.persons.get(person_id)
        await self.circle_service.get(circle_id)
        data = value.model_dump(mode="json") | {
            "membership_key": json.dumps([person_id, circle_id], separators=(",", ":")),
            "status": "active", "updated_at": timestamp()}
        return await self.repository.upsert(person_id, circle_id, data)

    async def leave(self, circle_id, person_id):
        await self.persons.get(person_id, True)
        await self.circle_service.get(circle_id, True)
        await self.repository.leave(person_id, circle_id, timestamp())

    async def list_members(self, circle_id, include_left, include_archived, limit, cursor):
        await self.circle_service.get(circle_id, include_archived)
        filters = {"circle": circle_id, "left": include_left, "archived": include_archived}
        rows = await self.repository.members(circle_id, include_left, include_archived, decode_cursor(cursor, filters), limit + 1)
        return page(rows, limit, filters, "person_id")

    async def list_circles(self, person_id, include_left, include_archived, limit, cursor):
        await self.persons.get(person_id, include_archived)
        filters = {"person": person_id, "left": include_left, "archived": include_archived}
        rows = await self.repository.circles(person_id, include_left, include_archived, decode_cursor(cursor, filters), limit + 1)
        return page(rows, limit, filters, "circle_id")
