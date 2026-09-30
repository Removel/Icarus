"""Validated settings for the Argus process."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QQConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    bot_id: str | None = None
    max_body_bytes: int = Field(default=1048576, gt=0, le=16 * 1024 * 1024)
    workspace_path: str | None = None
    gateway_url: str | None = None
    bot_names: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_enabled(self):
        if not self.enabled:
            return self
        if not self.bot_id or not self.bot_id.isascii() or not self.bot_id.isdecimal() or int(self.bot_id) <= 0:
            raise ValueError("QQ bot_id must be a positive number")
        if not self.workspace_path or not Path(self.workspace_path).is_absolute():
            raise ValueError("QQ workspace_path must be absolute")
        try:
            url = urlsplit(self.gateway_url or "")
            valid = (
                url.scheme == "ws" and url.hostname == "127.0.0.1"
                and url.port is not None and url.username is None and url.password is None
                and url.path == "/rpc" and not url.query and not url.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("QQ gateway_url must be a local Gateway WebSocket /rpc endpoint")
        return self


class ArgusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: Literal["127.0.0.1"]
    port: int = Field(ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    queue_capacity: int = Field(gt=0)
    worker_count: Literal[1]
    recovery_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    judge_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    jev_base_url: str = "https://openrouter.ai/api"
    jev_model: str = "typesafe/jev-1.13"
    batch_quiet_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    batch_max_messages: int = Field(default=20, gt=0, le=100)
    batch_max_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    graph_timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    graph_max_steps: int = Field(default=32, gt=0)
    qq: QQConfig = Field(default_factory=QQConfig)
