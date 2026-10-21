"""User configuration; runtime resources are injected separately."""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_base: str = Field(min_length=1)
    backend: Literal["openkb_http"] = "openkb_http"
    endpoint: str = Field(default="http://127.0.0.1:7566", min_length=1)
    max_file_bytes: StrictInt = Field(default=100 * 1024 * 1024, ge=1)
    max_request_bytes: StrictInt = Field(default=500 * 1024 * 1024, ge=1)
    default_page_size: StrictInt = Field(default=50, ge=1)
    max_page_size: StrictInt = Field(default=200, ge=1)
