"""Validated resource limits for ProcessPlugin."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, StrictFloat, StrictInt, model_validator


class ProcessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_running_processes: StrictInt = 8
    max_terminal_records: StrictInt = 200
    max_log_bytes: StrictInt = 16 * 1024 * 1024
    terminate_grace_seconds: StrictFloat = 3.0
    default_log_page_bytes: StrictInt = 32 * 1024
    max_log_page_bytes: StrictInt = 256 * 1024
    default_page_size: StrictInt = 50
    max_page_size: StrictInt = 200

    @model_validator(mode="after")
    def validate_limits(self) -> "ProcessConfig":
        integer_fields = (
            "max_running_processes",
            "max_terminal_records",
            "max_log_bytes",
            "default_log_page_bytes",
            "max_log_page_bytes",
            "default_page_size",
            "max_page_size",
        )
        for name in integer_fields:
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if (
            not math.isfinite(self.terminate_grace_seconds)
            or self.terminate_grace_seconds <= 0
        ):
            raise ValueError("terminate_grace_seconds must be finite and positive")
        if self.default_log_page_bytes > self.max_log_page_bytes:
            raise ValueError(
                "default_log_page_bytes cannot exceed max_log_page_bytes"
            )
        if self.default_page_size > self.max_page_size:
            raise ValueError("default_page_size cannot exceed max_page_size")
        return self
