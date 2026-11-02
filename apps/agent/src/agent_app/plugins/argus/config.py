"""No user credentials or routing identities are accepted as plugin config.

The bridge object is a trusted application-injected facility, never a JSON value.
"""
from pydantic import BaseModel, ConfigDict

class PluginConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
