from errors import ServiceError
from utils.ids import new_id
from utils.normalize import normalize_alias
from utils.pagination import decode_cursor, page
from utils.time import timestamp


def alias_properties(alias) -> dict:
    normalized, key = normalize_alias(alias.namespace, alias.value)
    result = alias.model_dump(exclude_none=True) | {"alias_id": new_id("a"), "normalized_value": normalized, "created_at": timestamp()}
    if key is not None:
        result["identity_key"] = key
    return result


class PersonService:
    def __init__(self, repository):
        self.repository = repository

    async def create(self, value):
        now = timestamp()
        data = {"person_id": new_id("p"), "display_name": value.display_name,
                "kind": value.kind, "status": "active", "created_at": now, "updated_at": now}
        await self.repository.create(data, [alias_properties(a) for a in value.aliases])
        return {"person": await self.repository.get(data["person_id"]), "created": True}

    async def get(self, person_id, include_archived=False):
        return await self.repository.get(person_id, include_archived)

    async def list(self, query="", include_archived=False, kind=None, limit=20, cursor=None):
        filters = {"query": query, "include_archived": include_archived, "kind": kind}
        after = decode_cursor(cursor, filters)
        items = await self.repository.list(query, include_archived, kind, after, limit + 1)
        return page(items, limit, filters, "person_id")

    async def patch(self, person_id, value):
        existing = await self.get(person_id, True)
        changes = value.model_dump(mode="json", exclude_unset=True)
        if not changes or any(v is None for v in changes.values()):
            raise ServiceError("VALIDATION_ERROR", "patch must contain non-null changes")
        if "kind" in changes and existing["kind"] in {"user", "agent"}:
            raise ServiceError("VALIDATION_ERROR", "cannot change special identity kind")
        changes["updated_at"] = timestamp()
        return await self.repository.patch(person_id, changes)

    async def resolve(self, namespace, value, include_archived=False):
        if namespace == "name":
            raise ServiceError("VALIDATION_ERROR", "use person search for names")
        try:
            _, key = normalize_alias(namespace, value)
        except ValueError as error:
            raise ServiceError("VALIDATION_ERROR", str(error)) from None
        return await self.repository.resolve(key, include_archived)

    async def add_alias(self, person_id, value):
        await self.get(person_id)
        return await self.repository.add_alias(person_id, alias_properties(value))

    async def delete_alias(self, person_id, alias_id):
        await self.get(person_id, True)
        await self.repository.delete_alias(person_id, alias_id)
