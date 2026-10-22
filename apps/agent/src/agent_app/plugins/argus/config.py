"""Argus user configuration; identities, clients and secrets are trusted facilities."""

from pydantic import BaseModel, ConfigDict


class PluginConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
