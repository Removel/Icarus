from datetime import datetime

from pydantic import field_validator

from schemas.common import StrictModel, Text


class MemberWrite(StrictModel):
    alias: Text | None = None
    role: Text = "member"
    identity_label: Text | None = None
    joined_at: datetime | None = None

    @field_validator("joined_at")
    @classmethod
    def aware(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("joined_at must include timezone")
        return value
