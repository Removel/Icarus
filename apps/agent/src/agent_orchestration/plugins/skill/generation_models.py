"""Strict data model returned by Skill generation Agents."""

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator


class GeneratedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    content: StrictStr

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("generated Skill content cannot be empty")
        return value
