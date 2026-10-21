"""Tool-only relationship operations; no automatic recall or event handlers."""

from __future__ import annotations

from typing import Any

from icarus.core.events import Event
from icarus.core.plugin import BasePlugin
from agent_app.plugins.relationship.backend import RelationshipBackend, RelationshipBackendError
from agent_app.contracts.prompt import PromptSection
from agent_app.plugins.relationship.config import RelationshipConfig
from agent_app.plugins.relationship.prompts import RELATIONSHIP_GUIDANCE
from agent_app.plugins.relationship.models import PersonSummary


class RelationshipPlugin(BasePlugin):
    config_schema = RelationshipConfig

    def __init__(self, plugin_id: str, backend: RelationshipBackend) -> None:
        super().__init__(plugin_id)
        self.backend = backend

    def accepts_event(self, source_plugin_id: str, event: Event) -> bool:
        return False

    def static_prompt_sections(self):
        return (PromptSection("relationship.guidance", "session", RELATIONSHIP_GUIDANCE),)

    def build_tools(self):
        from agent_app.plugins.relationship.tools import create_relationship_tools
        return create_relationship_tools(self)

    async def stop(self) -> None:
        self.backend.close()

    def remember(self, *, person_id: str | None, display_name: str | None,
                 identities: list[str], profile: dict[str, Any], confirm_create: bool,
                 source_ref: str) -> dict[str, Any]:
        # Archived identities still own their global keys and must never be treated as new.
        resolved = [self.backend.resolve_identity(identity, include_archived=True) for identity in identities]
        owners = {p.person_id: p for p in resolved if p is not None}
        if any(p.status != "active" for p in owners.values()):
            raise ValueError("identity belongs to an archived person; no changes made")
        person = self.backend.get_person(person_id) if person_id else None
        if len(owners) > 1 or (person is not None and any(owner != person.person_id for owner in owners)):
            return _confirmation(tuple(owners.values()) + ((person,) if person else ()), "conflicting_identities")
        if person is None and owners:
            person = next(iter(owners.values()))
        created = False
        if person is None:
            if not display_name:
                raise ValueError("display_name is required to create a person")
            candidates = self.backend.search_people(display_name)
            if identities:
                if (candidates.items or candidates.truncated) and not confirm_create:
                    return _confirmation(candidates.items, "existing_name_candidates", would_create=True,
                                         truncated=candidates.truncated)
            else:
                if candidates.truncated:
                    return _confirmation(candidates.items, "incomplete_name_search", truncated=True)
                # Candidate aliases are bounded for display; exact-name evidence comes from full detail.
                details = tuple(self.backend.get_person(item.person_id) for item in candidates.items)
                name = display_name.strip().lower()
                exact = {item.person_id: item for item in details
                         if item.display_name.strip().lower() == name or name in item.names}
                if len(exact) == 1:
                    person = next(iter(exact.values()))
                elif details:
                    return _confirmation(details, "ambiguous_person")
                elif not confirm_create:
                    return _confirmation((), "person_not_found", would_create=True)
            if person is None:
                person = self.backend.create_person(display_name, identities)
                created = True
        # Bind identities before profiles. A conflict after a previous alias write is a partial failure,
        # not a confirmation result that falsely promises that nothing changed.
        if not created:
            for identity, owner in zip(identities, resolved):
                if owner is not None:
                    continue
                try:
                    self.backend.add_alias(person.person_id, identity)
                except RelationshipBackendError as error:
                    if error.status_code != 409:
                        raise
                    owner = self.backend.resolve_identity(identity, include_archived=True)
                    if owner is None or owner.person_id != person.person_id or owner.status != "active":
                        raise RelationshipBackendError("identity ownership conflict") from None
        for key, value in profile.items():
            self.backend.write_profile(person.person_id, key, value, source_ref)
        return {"status": "ok", "person": person.as_dict(), "created": created, "written": list(profile)}


def _confirmation(candidates: tuple[PersonSummary, ...], reason: str, *, would_create: bool = False,
                  truncated: bool = False) -> dict[str, Any]:
    unique = {item.person_id: item for item in candidates}
    return {"status": "confirmation_required", "reason": reason,
            "candidates": [item.as_dict() for item in unique.values()],
            "would_create": would_create, "truncated": truncated}
