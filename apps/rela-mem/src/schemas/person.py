from typing import Annotated, Literal

from pydantic import Field, field_validator

from domain.enums import EntityStatus
from schemas.common import StrictModel, Text
from utils.normalize import normalize_alias


class AliasWrite(StrictModel):
    namespace: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
    value: Text
    display_value: Text | None = None

    @field_validator("value")
    @classmethod
    def valid_alias(cls, value, info):
        if "namespace" in info.data:
            normalize_alias(info.data["namespace"], value)
        return value


class PersonCreate(StrictModel):
    display_name: Text
    kind: Literal["human", "virtual"] = "human"
    aliases: list[AliasWrite] = Field(default_factory=list, max_length=50)


class PersonPatch(StrictModel):
    display_name: Text | None = None
    kind: Literal["human", "virtual"] | None = None
    status: EntityStatus | None = None
