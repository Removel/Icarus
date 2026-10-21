from errors import not_found
from repositories.graph_client import properties


class CircleRepository:
    def __init__(self, graph):
        self.graph = graph

    async def create(self, data):
        rows = await self.graph.query("CREATE (c:Circle) SET c=$data RETURN c", {"data": data})
        return properties(rows[0][0])

    async def get(self, circle_id, archived=False):
        rows = await self.graph.query("MATCH (c:Circle {circle_id:$id}) WHERE $archived OR c.status='active' RETURN c", {"id": circle_id, "archived": archived})
        if not rows:
            raise not_found("circle")
        return properties(rows[0][0])

    async def list(self, query, kind, archived, after, limit):
        rows = await self.graph.query("MATCH (c:Circle) WHERE ($archived OR c.status='active') AND ($kind='' OR c.kind=$kind) AND ($q='' OR toLower(c.name) CONTAINS $q) AND c.circle_id>$after RETURN c ORDER BY c.circle_id LIMIT $limit", {"archived": archived, "kind": kind or "", "q": query.lower(), "after": after, "limit": limit})
        return [properties(row[0]) for row in rows]

    async def patch(self, circle_id, changes):
        rows = await self.graph.query("MATCH (c:Circle {circle_id:$id}) SET c += $changes RETURN c", {"id": circle_id, "changes": changes})
        if not rows:
            raise not_found("circle")
        return properties(rows[0][0])
