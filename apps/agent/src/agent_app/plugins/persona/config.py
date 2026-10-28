"""Global persona configuration; external services are opt-in."""

import math
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator


class PersonaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    persona_id: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    jev_enabled: StrictBool = False
    jev_base_url: str = "https://openrouter.ai/api"
    jev_model: str = Field(default="typesafe/jev-1.13", min_length=1)
    jev_timeout_seconds: float = Field(default=1.2, gt=0, le=4)
    attack_defend_threshold: float = Field(default=0.5, ge=0, le=1)
    attack_refuse_threshold: float = Field(default=0.8, ge=0, le=1)
    drift_every_turns: StrictInt = Field(default=5, ge=1)
    drift_threshold: float = Field(default=0.7, ge=0, le=1)
    evolution_min_probability: float = Field(default=0.6, ge=0, le=1)
    evolution_max_entries: StrictInt = Field(default=30, ge=1, le=100)
    evolution_max_tokens: StrictInt = Field(default=600, ge=1)
    evolution_max_note_chars: StrictInt = Field(default=300, ge=1, le=300)
    evolution_max_reason_chars: StrictInt = Field(default=500, ge=1, le=500)
    evolution_min_turns_between_accepts: StrictInt = Field(default=10, ge=1)
    relationship_enabled: StrictBool = False
    relationship_endpoint: str = "http://127.0.0.1:8767"
    relationship_timeout_seconds: float = Field(default=2.0, gt=0, le=10)
    max_identity_tokens: StrictInt = Field(default=1500, ge=1)
    max_soul_tokens: StrictInt = Field(default=400, ge=1)
    max_relationship_tokens: StrictInt = Field(default=200, ge=1)
    max_dynamic_tokens: StrictInt = Field(default=200, ge=1)

    @field_validator("jev_base_url", "relationship_endpoint")
    @classmethod
    def validate_endpoint(cls, value, info):
        try:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or parsed.port == 0
                    or (info.field_name == "relationship_endpoint" and parsed.path not in {"", "/"})):
                raise ValueError
        except ValueError:
            raise ValueError("Endpoint must be an HTTP(S) URL without credentials, query or fragment") from None
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_limits(self):
        if self.attack_defend_threshold > self.attack_refuse_threshold:
            raise ValueError("Defense threshold cannot exceed refusal threshold")
        for value in self.model_dump().values():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Numeric limits must be finite")
        return self


PluginConfig = PersonaConfig
