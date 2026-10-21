"""User configuration; runtime resources are injected separately."""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_context_sources: list[str] = Field(default_factory=list)
    fatal_context_sources: list[str] = Field(default_factory=list)
