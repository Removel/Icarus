from errors import not_found
from repositories.graph_client import properties


class MemberRepository:
    def __init__(self, graph):
        self.graph = graph

    async def upsert(self, person_id, circle_id, data):
        rows = await self.graph.query(
            "MATCH (p:Person {person_id:$person}),(c:Circle {circle_id:$circle}) "
            "MERGE (p)-[m:MEMBER_OF {membership_key:$key}]->(c) "
            "ON CREATE SET m.created_at=$now SET m += $data RETURN m",
            {"person": person_id, "circle": circle_id, "key": data["membership_key"], "now": data["updated_at"], "data": data})
        if not rows:
            raise not_found("person or circle")
        return properties(rows[0][0])

    async def leave(self, person_id, circle_id, now):
        rows = await self.graph.query("MATCH (p:Person {person_id:$person})-[m:MEMBER_OF]->(c:Circle {circle_id:$circle}) SET m.status='left',m.updated_at=$now RETURN m", {"person": person_id, "circle": circle_id, "now": now})
        if not rows:
            raise not_found("membership")

    async def members(self, circle_id, include_left, include_archived, after, limit):
        rows = await self.graph.query("MATCH (p:Person)-[m:MEMBER_OF]->(c:Circle {circle_id:$circle}) WHERE ($left OR m.status='active') AND ($archived OR p.status='active') AND p.person_id>$after RETURN m,p ORDER BY p.person_id LIMIT $limit", {"circle": circle_id, "left": include_left, "archived": include_archived, "after": after, "limit": limit})
        return [properties(row[0]) | {"person_id": row[1].properties["person_id"], "person": properties(row[1])} for row in rows]

    async def circles(self, person_id, include_left, include_archived, after, limit):
        rows = await self.graph.query("MATCH (p:Person {person_id:$person})-[m:MEMBER_OF]->(c:Circle) WHERE ($left OR m.status='active') AND ($archived OR c.status='active') AND c.circle_id>$after RETURN m,c ORDER BY c.circle_id LIMIT $limit", {"person": person_id, "left": include_left, "archived": include_archived, "after": after, "limit": limit})
        return [properties(row[0]) | {"circle_id": row[1].properties["circle_id"], "circle": properties(row[1])} for row in rows]
