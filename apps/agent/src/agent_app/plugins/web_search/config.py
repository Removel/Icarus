"""Search connection configuration; credentials remain on the server."""

from pydantic import BaseModel, ConfigDict, Field


class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = "https://api.deepseek.com/v1"
    api_key_env: str = "ICARUS_WEB_SEARCH_API_KEY"
    deadline_seconds: int = Field(default=45, ge=1, le=90)
