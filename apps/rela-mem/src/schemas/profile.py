import json
from datetime import datetime
from typing import Any

from pydantic import field_validator

from domain.enums import Source
from schemas.common import SourceRef, StrictModel
from utils.time import utc_now


class ProfileWrite(StrictModel):
    value: Any
    source: Source
    source_ref: SourceRef | None = None
    recorded_at: datetime | None = None

    @field_validator("value")
    @classmethod
    def bounded_json(cls, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 65536:
            raise ValueError("profile value exceeds 64 KiB")
        return value

    @field_validator("recorded_at")
    @classmethod
    def past_aware_time(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None or value > utc_now()):
            raise ValueError("recorded_at must be timezone-aware and not in the future")
        return value
