"""Framework-owned tool execution settings."""

import math

import pydantic
from pydantic import ConfigDict, StrictFloat, StrictInt, model_validator


class ToolExecutionSettings(pydantic.BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_timeout_seconds: StrictFloat = pydantic.Field(default=120.0, ge=1)
    max_timeout_seconds: StrictFloat = pydantic.Field(default=600.0, ge=1)
    default_output_tokens: StrictInt = pydantic.Field(default=4000, ge=1)
    min_output_tokens: StrictInt = pydantic.Field(default=512, ge=1)
    max_output_tokens: StrictInt = pydantic.Field(default=16000, ge=1)
    batch_output_tokens: StrictInt = pydantic.Field(default=16000, ge=1)
    max_calls_per_batch: StrictInt = pydantic.Field(default=8, ge=1)
    preview_target_ratio: StrictFloat = pydantic.Field(default=0.9, gt=0, le=1)
    head_ratio: StrictFloat = pydantic.Field(default=0.5, gt=0, lt=1)
    max_result_file_bytes: StrictInt = pydantic.Field(
        default=16 * 1024 * 1024, ge=1
    )
    default_page_size: StrictInt = pydantic.Field(default=50, ge=1)
    max_page_size: StrictInt = pydantic.Field(default=200, ge=1)
    default_read_lines: StrictInt = pydantic.Field(default=200, ge=1)
    max_read_lines: StrictInt = pydantic.Field(default=2000, ge=1)

    @model_validator(mode="after")
    def validate_ranges(self) -> "ToolExecutionSettings":
        if not math.isfinite(
            self.default_timeout_seconds
        ) or not math.isfinite(self.max_timeout_seconds):
            raise ValueError("Tool timeout settings must be finite")
        if self.default_timeout_seconds > self.max_timeout_seconds:
            raise ValueError(
                "default_timeout_seconds cannot exceed max_timeout_seconds"
            )
        if not (
            self.min_output_tokens
            <= self.default_output_tokens
            <= self.max_output_tokens
        ):
            raise ValueError(
                "default_output_tokens must be between min_output_tokens "
                "and max_output_tokens"
            )
        if (
            self.max_calls_per_batch * self.min_output_tokens
            > self.batch_output_tokens
        ):
            raise ValueError(
                "batch_output_tokens cannot cover the minimum Tool Result "
                "budget for max_calls_per_batch"
            )
        if self.default_page_size > self.max_page_size:
            raise ValueError(
                "default_page_size cannot exceed max_page_size"
            )
        if self.default_read_lines > self.max_read_lines:
            raise ValueError(
                "default_read_lines cannot exceed max_read_lines"
            )
        return self

