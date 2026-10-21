import asyncio
from time import monotonic

from errors import ServiceError

CONSTRAINTS = (
    ("NODE", "Person", "person_id"),
    ("NODE", "Person", "special_kind"),
    ("NODE", "Alias", "alias_id"),
    ("NODE", "Alias", "identity_key"),
    ("NODE", "Circle", "circle_id"),
    ("RELATIONSHIP", "RELATED_TO", "relation_key"),
    ("RELATIONSHIP", "RELATED_TO", "relation_id"),
    ("RELATIONSHIP", "MEMBER_OF", "membership_key"),
)


async def initialize_graph(graph, timeout: float = 20):
    existing = await graph.query("CALL db.indexes() YIELD label, properties, entitytype RETURN label, properties, entitytype")
    for entity, label, prop in CONSTRAINTS:
        if not any(row[0] == label and prop in row[1] and row[2] == entity for row in existing):
            target = f"(n:{label})" if entity == "NODE" else f"()-[n:{label}]-()"
            await graph.query(f"CREATE INDEX FOR {target} ON (n.{prop}) OPTIONS {{indexType:'EXACT'}}")
    deadline = monotonic() + timeout
    while True:
        indexes = await graph.query("CALL db.indexes() YIELD status RETURN status")
        if any(row[0] == "FAILED" for row in indexes):
            raise ServiceError("UNAVAILABLE", "graph index validation failed", 503)
        if all(row[0] == "OPERATIONAL" for row in indexes):
            break
        if monotonic() >= deadline:
            raise ServiceError("UNAVAILABLE", "graph index initialization timed out", 503)
        await asyncio.sleep(0.05)
    existing = await graph.query("CALL db.constraints() YIELD label, properties, entitytype, status RETURN label, properties, entitytype, status")
    for entity, label, prop in CONSTRAINTS:
        if not any(row[0] == label and row[1] == [prop] and row[2] == entity for row in existing):
            await graph.command("GRAPH.CONSTRAINT", "CREATE", graph.name, "UNIQUE", entity, label, "PROPERTIES", 1, prop)
    while True:
        rows = await graph.query("CALL db.constraints() YIELD status RETURN status")
        if any(row[0] == "FAILED" for row in rows):
            raise ServiceError("UNAVAILABLE", "graph constraint validation failed", 503)
        if len(rows) >= len(CONSTRAINTS) and all(row[0] == "OPERATIONAL" for row in rows):
            return
        if monotonic() >= deadline:
            raise ServiceError("UNAVAILABLE", "graph constraint initialization timed out", 503)
        await asyncio.sleep(0.05)
