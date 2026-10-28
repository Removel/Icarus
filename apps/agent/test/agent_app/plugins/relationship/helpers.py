"""Test-only in-memory backend; production tools/selection stay real."""

from agent_app.plugins.relationship.backend import RelationshipBackendError
from agent_app.plugins.relationship.models import FindResult, PersonSummary


class Backend:
    def __init__(self):
        self.people = {}
        self.identities = {}
        self.writes = []
        self.closed = False
        self.search_more = False
        self.alias_conflict_owner = None
        self.fail_key = None

    def add(self, person_id="p_a", name="张三", *, names=(), status="active"):
        record = PersonSummary(person_id, name, "human", status, names=tuple(names))
        self.people[person_id] = record
        return record

    def search_people(self, query):
        return self.find(query)

    def find(self, query):
        items = tuple(p for p in self.people.values() if p.status == "active" and
                      (query.lower() in p.display_name.lower() or any(query.lower() in n for n in p.names)))
        return FindResult(items[:10], self.search_more or len(items) > 10)

    def resolve_identity(self, identity, *, include_archived=False):
        record = self.people.get(self.identities.get(identity))
        return record if record and (include_archived or record.status == "active") else None

    def get_person(self, person_id):
        if person_id not in self.people or self.people[person_id].status != "active":
            raise RelationshipBackendError("service returned HTTP 404", status_code=404)
        return self.people[person_id]

    def context(self, person_id, query=None):
        return {"person": self.get_person(person_id).as_dict(), "profile": {"items": [], "truncated": False}}

    def create_person(self, display_name, identities):
        record = self.add(f"p_{len(self.people) + 1}", display_name)
        for identity in identities:
            self.identities[identity] = record.person_id
        self.writes.append(("create", record.person_id, tuple(identities)))
        return record

    def add_alias(self, person_id, identity):
        if self.alias_conflict_owner:
            self.identities[identity] = self.alias_conflict_owner
            raise RelationshipBackendError("service returned HTTP 409", status_code=409)
        self.identities[identity] = person_id
        self.writes.append(("alias", person_id, identity))

    def write_profile(self, person_id, key, value, source_ref):
        if key == self.fail_key:
            raise RelationshipBackendError("service unavailable")
        self.writes.append(("profile", person_id, key, value, source_ref))

    def forget_profile(self, person_id, key, source_ref):
        self.writes.append(("forget", person_id, key, source_ref))

    def upsert_relation(self, **fields):
        self.writes.append(("relation", fields))
        return {"relation": {"relation_id": "r_1", "direction": "two_way" if fields["reverse_detail"] else "one_way"}, "created": True}

    def invalidate_relation(self, relation_id):
        self.writes.append(("invalidate", relation_id))

    def close(self):
        self.closed = True
