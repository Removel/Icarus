from errors import ServiceError, not_found
from repositories.graph_client import properties


class PersonRepository:
    def __init__(self, graph):
        self.graph = graph

    async def bootstrap(self, identity):
        expected = [("user", identity.user), ("agent", identity.agent)]
        existing = await self.graph.query("MATCH (p:Person) WHERE p.kind IN ['user','agent'] OR p.person_id IN $ids RETURN p", {"ids": [p.person_id for _, p in expected]})
        for row in existing:
            data = properties(row[0])
            if not any(data["kind"] == kind and data["person_id"] == item.person_id for kind, item in expected):
                raise ServiceError("CONFLICT", "configured user or agent conflicts with existing identity", 409)
        from utils.time import timestamp
        now = timestamp()
        rows = await self.graph.query(
            "MERGE (u:Person {special_kind:'user'}) "
            "ON CREATE SET u.person_id=$uid,u.kind='user',u.display_name=$uname,u.status='active',u.created_at=$now,u.updated_at=$now "
            "MERGE (a:Person {special_kind:'agent'}) "
            "ON CREATE SET a.person_id=$aid,a.kind='agent',a.display_name=$aname,a.status='active',a.created_at=$now,a.updated_at=$now RETURN u,a",
            {"uid": identity.user.person_id, "uname": identity.user.display_name,
             "aid": identity.agent.person_id, "aname": identity.agent.display_name, "now": now})
        if rows[0][0].properties["person_id"] != identity.user.person_id or rows[0][1].properties["person_id"] != identity.agent.person_id:
            raise ServiceError("CONFLICT", "configured identities differ from existing bootstrap", 409)

    async def create(self, data: dict, aliases: list[dict]) -> dict:
        query = "CREATE (p:Person) SET p=$person "
        params = {"person": data}
        for index, alias in enumerate(aliases):
            query += f"CREATE (p)-[:HAS_ALIAS]->(a{index}:Alias) SET a{index}=$alias{index} "
            params[f"alias{index}"] = alias
        rows = await self.graph.query(query + "RETURN p", params)
        return properties(rows[0][0])

    async def get(self, person_id: str, include_archived: bool = False) -> dict:
        rows = await self.graph.query("MATCH (p:Person {person_id:$id}) WHERE $archived OR p.status='active' OPTIONAL MATCH (p)-[:HAS_ALIAS]->(a:Alias) RETURN p,collect(a)", {"id": person_id, "archived": include_archived})
        if not rows:
            raise not_found("person")
        result = properties(rows[0][0])
        result.pop("special_kind", None)
        result["aliases"] = [properties(a) for a in rows[0][1] if a is not None]
        return result

    async def list(self, query: str, include_archived: bool, kind: str | None, after: str, limit: int) -> list[dict]:
        rows = await self.graph.query(
            "MATCH (p:Person) OPTIONAL MATCH (p)-[:HAS_ALIAS]->(a:Alias) "
            "WITH p,collect(a) AS aliases "
            "WHERE ($archived OR p.status='active') AND ($kind='' OR p.kind=$kind) AND p.person_id>$after "
            "AND ($q='' OR toLower(p.display_name) CONTAINS $q OR any(a IN aliases WHERE toLower(a.value) CONTAINS $q OR toLower(coalesce(a.display_value,'')) CONTAINS $q)) "
            "RETURN p,aliases[0..10] ORDER BY p.person_id LIMIT $limit",
            {"q": query.lower(), "archived": include_archived, "kind": kind or "", "after": after, "limit": limit})
        result = []
        for row in rows:
            item = properties(row[0])
            item.pop("special_kind", None)
            item["aliases"] = [
                {key: value for key, value in properties(alias).items()
                 if key in {"alias_id", "namespace", "value", "display_value"}}
                for alias in row[1] if alias is not None
            ]
            result.append(item)
        return result

    async def patch(self, person_id: str, changes: dict):
        rows = await self.graph.query("MATCH (p:Person {person_id:$id}) SET p += $changes RETURN p", {"id": person_id, "changes": changes})
        if not rows:
            raise not_found("person")
        return await self.get(person_id, True)

    async def resolve(self, identity_key: str, archived: bool) -> dict | None:
        rows = await self.graph.query("MATCH (p:Person)-[:HAS_ALIAS]->(a:Alias {identity_key:$key}) WHERE $archived OR p.status='active' RETURN p", {"key": identity_key, "archived": archived})
        if not rows:
            return None
        return await self.get(rows[0][0].properties["person_id"], archived)

    async def add_alias(self, person_id: str, alias: dict):
        rows = await self.graph.query("MATCH (p:Person {person_id:$id}) CREATE (p)-[:HAS_ALIAS]->(a:Alias) SET a=$alias RETURN a", {"id": person_id, "alias": alias})
        if not rows:
            raise not_found("person")
        return properties(rows[0][0])

    async def delete_alias(self, person_id: str, alias_id: str):
        rows = await self.graph.query("MATCH (p:Person {person_id:$id})-[:HAS_ALIAS]->(a:Alias {alias_id:$alias_id}) WITH a,a.alias_id AS id DETACH DELETE a RETURN id", {"id": person_id, "alias_id": alias_id})
        if not rows:
            raise not_found("alias")
