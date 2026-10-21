from __future__ import annotations

from errors import ServiceError, not_found
from repositories.graph_client import properties


class RelationRepository:
    def __init__(self, graph):
        self.graph = graph

    async def upsert(self, data):
        rows = await self.graph.query(
            "MATCH (a:Person {person_id:$from}),(b:Person {person_id:$to}) "
            "MERGE (a)-[r:RELATED_TO {relation_key:$key}]->(b) "
            "ON CREATE SET r.relation_id=$id,r.created_at=$now "
            "SET r += $data RETURN r",
            {"from": data["from_person_id"], "to": data["to_person_id"], "key": data["relation_key"],
             "id": data["relation_id"], "now": data["updated_at"],
             "data": {k: v for k, v in data.items() if k != "relation_id"}})
        if not rows:
            raise not_found("relation endpoints")
        result = properties(rows[0][0])
        return result, result["relation_id"] == data["relation_id"]

    async def get(self, relation_id):
        rows = await self.graph.query("MATCH ()-[r:RELATED_TO {relation_id:$id}]->() RETURN r", {"id": relation_id})
        if not rows:
            raise not_found("relation")
        return properties(rows[0][0])

    async def replace(self, relation_id, data, expected):
        predicates = []
        params = {"id": relation_id, "from": data["from_person_id"], "to": data["to_person_id"], "data": data}
        for index, key in enumerate(("relation_key", "forward_detail", "reverse_detail", "source", "source_ref", "status", "updated_at")):
            name = f"expected{index}"
            value = expected.get(key)
            if value is None:
                predicates.append(f"old.{key} IS NULL")
            else:
                predicates.append(f"old.{key}=${name}")
                params[name] = value
        rows = await self.graph.query(
            "MATCH ()-[old:RELATED_TO {relation_id:$id}]->() WHERE " + " AND ".join(predicates) + " "
            "WITH old DELETE old WITH 1 AS _ "
            "MATCH (a:Person {person_id:$from}),(b:Person {person_id:$to}) "
            "CREATE (a)-[r:RELATED_TO]->(b) SET r=$data RETURN r", params)
        if not rows:
            raise ServiceError("CONFLICT", "relation changed during update; reread before retrying", 409)
        return properties(rows[0][0])

    async def invalidate(self, relation_id, now):
        rows = await self.graph.query("MATCH ()-[r:RELATED_TO {relation_id:$id}]->() SET r.status='invalid',r.updated_at=$now RETURN r", {"id": relation_id, "now": now})
        if not rows:
            raise not_found("relation")

    async def list(self, person_id, other_id, circle_id, kind, status, include_incoming, archived, after, limit):
        rows = await self.graph.query(
            "MATCH (a:Person)-[r:RELATED_TO]->(b:Person) "
            "WHERE ($archived OR (a.status='active' AND b.status='active')) "
            "AND ($person='' OR a.person_id=$person OR (($incoming OR r.direction='two_way') AND b.person_id=$person)) "
            "AND ($other='' OR (a.person_id=$person AND b.person_id=$other) OR (b.person_id=$person AND a.person_id=$other)) "
            "AND ($circle='' OR r.scope_circle_id=$circle) AND ($kind='' OR r.kind=$kind) "
            "AND r.status=$status AND r.relation_id>$after "
            "RETURN r,a,b ORDER BY r.relation_id LIMIT $limit",
            {"person": person_id or "", "other": other_id or "", "circle": circle_id or "", "kind": kind or "",
             "status": status, "incoming": include_incoming, "archived": archived, "after": after, "limit": limit})
        result = []
        for row in rows:
            item = properties(row[0])
            item.setdefault("reverse_detail", None)
            item.setdefault("scope_circle_id", None)
            if person_id:
                outgoing = item["from_person_id"] == person_id
                other = properties(row[2] if outgoing else row[1])
                other.pop("special_kind", None)
                detail = item.get("reverse_detail") if outgoing else item["forward_detail"]
                item.update(other_person=other, relative_direction="outgoing" if outgoing else "incoming",
                            detail=detail, detail_missing=detail is None)
            result.append(item)
        return result

    async def neighbors(self, frontier: list[str], limit: int):
        rows = await self.graph.query(
            "MATCH (a:Person)-[r:RELATED_TO]-(b:Person) WHERE a.person_id IN $frontier "
            "AND a.status='active' AND b.status='active' AND r.status='active' "
            "RETURN a.person_id,r,b ORDER BY r.relation_id LIMIT $limit",
            {"frontier": frontier, "limit": limit})
        return [(row[0], properties(row[1]), properties(row[2])) for row in rows]
