"""User configuration; runtime resources are injected separately."""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

class RecallConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_k: StrictInt = Field(default=3, ge=1, le=20)
    threshold: float = Field(default=0.25, ge=0, le=1)
    max_context_chars: StrictInt = Field(default=6000, ge=1)
    deadline_ms: StrictInt = Field(default=5000, ge=1, le=30000)


class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    backend: Literal["mem0_http"] = "mem0_http"
    endpoint: str = Field(default="http://127.0.0.1:8888", min_length=1)
    preserve_input_language: StrictBool = True
    recall: RecallConfig = Field(default_factory=RecallConfig)

    @field_validator("user_id", "agent_id", "endpoint")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("configuration value must not be blank")
        return value.strip()
