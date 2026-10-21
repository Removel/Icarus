"""User configuration; runtime resources are injected separately."""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_output_bytes: StrictInt = Field(default=16 * 1024 * 1024, ge=1)
    default_read_lines: StrictInt = Field(default=200, ge=1)
    max_read_lines: StrictInt = Field(default=2000, ge=1)
