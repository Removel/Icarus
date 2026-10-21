from errors import ServiceError


class GraphService:
    def __init__(self, relations, persons):
        self.relations = relations
        self.persons = persons

    async def shortest_path(self, source, target, max_depth):
        start = await self.persons.get(source)
        await self.persons.get(target)
        paths = {source: ([start], [])}
        frontier = [source]
        if source == target:
            return {"path": [start], "edges": [], "length": 0}
        for _ in range(max_depth):
            rows = await self.relations.neighbors(frontier, 5001)
            if len(rows) > 5000:
                raise ServiceError("LIMIT_EXCEEDED", "graph search exceeds bounded traversal budget")
            next_frontier = []
            for current, edge, person in rows:
                person_id = person["person_id"]
                if person_id in paths:
                    continue
                person.pop("special_kind", None)
                nodes, edges = paths[current]
                paths[person_id] = (nodes + [person], edges + [edge])
                if person_id == target:
                    nodes, edges = paths[person_id]
                    return {"path": nodes, "edges": edges, "length": len(edges)}
                next_frontier.append(person_id)
            if not next_frontier:
                break
            if len(paths) > 5000:
                raise ServiceError("LIMIT_EXCEEDED", "graph search exceeds bounded traversal budget")
            frontier = next_frontier
        return {"path": [], "edges": [], "length": None}
