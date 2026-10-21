import json
from datetime import datetime

from errors import ServiceError
from utils.pagination import decode_cursor, encode_cursor, page
from utils.time import utc_now


class ProfileService:
    def __init__(self, repository, persons):
        self.repository = repository
        self.persons = persons

    async def write(self, person_id, key, value):
        await self.persons.get(person_id)
        return await self.repository.append(person_id, key, value.value, value.source, value.source_ref,
                                            value.recorded_at or utc_now(), "active")

    async def delete(self, person_id, key, source, source_ref):
        await self.persons.get(person_id)
        await self.repository.append(person_id, key, None, source, source_ref, utc_now(), "deleted")

    async def current(self, person_id, q="", keys=None, limit=20, cursor=None, include_archived=False):
        await self.persons.get(person_id, include_archived)
        keys = sorted(set(keys or []))
        filters = {"person_id": person_id, "q": q, "keys": keys, "include_archived": include_archived}
        after = decode_cursor(cursor, filters)
        rows = await self.repository.current(person_id, q, keys, after, limit + 1)
        return page(rows, limit, filters, "attr_key")

    async def history(self, person_id, key, limit=20, cursor=None, include_archived=False):
        await self.persons.get(person_id, include_archived)
        filters = {"person_id": person_id, "key": key, "include_archived": include_archived}
        last = decode_cursor(cursor, filters)
        try:
            before = None
            if last:
                position = json.loads(last)
                if not isinstance(position, list) or len(position) != 2:
                    raise ValueError
                time, row_id = position
                if not isinstance(time, str) or type(row_id) is not int or not 1 <= row_id <= 9223372036854775807:
                    raise ValueError
                parsed = datetime.fromisoformat(time)
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError
                before = (parsed, row_id)
        except (ValueError, TypeError, IndexError, KeyError):
            raise ServiceError("VALIDATION_ERROR", "invalid history cursor") from None
        rows = await self.repository.history(person_id, key, before, limit + 1)
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if more:
            next_cursor = encode_cursor(json.dumps([rows[-1]["recorded_at"].isoformat(), rows[-1]["id"]]), filters)
        return {"items": rows, "has_more": more, "next_cursor": next_cursor}
