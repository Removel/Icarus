"""Relationship values independent of the service's HTTP payloads."""

from dataclasses import dataclass
import re
from typing import Any


IDENTITY = re.compile(r"^([a-z][a-z0-9_]{0,31}):(.+)$")


def split_identity(value: str) -> tuple[str, str] | None:
    match = IDENTITY.fullmatch(value)
    if match is None or match[1] == "name":
        return None
    return match[1], match[2]


@dataclass(frozen=True)
class PersonSummary:
    person_id: str
    display_name: str
    kind: str
    status: str
    aliases: tuple[dict[str, Any], ...] = ()
    aliases_truncated: bool = False
    names: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"person_id": self.person_id, "display_name": self.display_name,
                  "kind": self.kind, "status": self.status,
                  "aliases": list(self.aliases)}
        if self.aliases_truncated:
            result["aliases_truncated"] = True
        return result


@dataclass(frozen=True)
class FindResult:
    items: tuple[PersonSummary, ...] = ()
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"items": [item.as_dict() for item in self.items],
                "ambiguous": len(self.items) > 1 or self.truncated,
                "truncated": self.truncated}
