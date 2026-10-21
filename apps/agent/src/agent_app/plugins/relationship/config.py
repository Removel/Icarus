"""Strict user configuration; injected runtime facilities are not user fields."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class RelationshipConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    endpoint: str = "http://127.0.0.1:8767"
    timeout_seconds: StrictInt = Field(default=10, ge=1, le=60)

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                    or port == 0):
                raise ValueError
        except ValueError:
            raise ValueError("relationship endpoint must be an absolute HTTP(S) origin without credentials, query or path") from None
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and parsed.scheme != "https":
            raise ValueError("relationship endpoint must use HTTPS for non-loopback service")
        return value.rstrip("/")


PluginConfig = RelationshipConfig
