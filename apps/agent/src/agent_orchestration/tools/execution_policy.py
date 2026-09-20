"""Framework-owned Tool execution controls and effective budget resolution."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import math
from typing import Any

from apps.agent.src.model_config import ToolExecutionSettings


CONTROL_KEY = "_execution"


class ToolExecutionControlError(ValueError):
    pass


class ToolContextBudgetExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolExecutionRequest:
    timeout_seconds: float | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class EffectiveToolBudget:
    timeout_seconds: float
    output_tokens: int
    preview_tokens: int


@dataclass(frozen=True)
class PreparedToolArguments:
    arguments: dict[str, Any]
    request: ToolExecutionRequest
    budget: EffectiveToolBudget


class ToolExecutionPolicy:
    def __init__(
        self,
        settings: ToolExecutionSettings | None = None,
        *,
        context_window: int | None = None,
    ) -> None:
        self.settings = settings or ToolExecutionSettings()
        self.context_window = context_window

    @property
    def batch_output_tokens(self) -> int:
        limit = self.settings.batch_output_tokens
        if self.context_window is not None:
            limit = min(limit, max(1, int(self.context_window * 0.15)))
        return limit

    def control_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "description": (
                "Optional execution budget request. Icarus may apply a "
                "smaller effective budget."
            ),
            "properties": {
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": self.settings.max_timeout_seconds,
                    "default": self.settings.default_timeout_seconds,
                },
                "max_output_tokens": {
                    "type": "integer",
                    "minimum": self.settings.min_output_tokens,
                    "maximum": self.settings.max_output_tokens,
                    "default": self.settings.default_output_tokens,
                },
            },
            "additionalProperties": False,
        }

    def prepare(
        self,
        arguments: dict[str, Any],
        *,
        batch_remaining_tokens: int | None = None,
    ) -> PreparedToolArguments:
        if not isinstance(arguments, dict):
            raise ToolExecutionControlError("Tool arguments must be an object")
        business = deepcopy(arguments)
        raw = business.pop(CONTROL_KEY, None)
        request = self._parse_request(raw)
        timeout = (
            self.settings.default_timeout_seconds
            if request.timeout_seconds is None
            else request.timeout_seconds
        )
        requested_output = (
            self.settings.default_output_tokens
            if request.max_output_tokens is None
            else request.max_output_tokens
        )
        output_limit = self.settings.max_output_tokens
        if self.context_window is not None:
            output_limit = min(output_limit, max(1, int(self.context_window * 0.08)))
        if batch_remaining_tokens is not None:
            output_limit = min(output_limit, max(1, batch_remaining_tokens))
        output_tokens = min(requested_output, output_limit)
        preview_tokens = max(
            1, int(output_tokens * self.settings.preview_target_ratio)
        )
        return PreparedToolArguments(
            arguments=business,
            request=request,
            budget=EffectiveToolBudget(timeout, output_tokens, preview_tokens),
        )

    def _parse_request(self, raw: object) -> ToolExecutionRequest:
        if raw is None:
            return ToolExecutionRequest()
        if not isinstance(raw, dict):
            raise ToolExecutionControlError("_execution must be an object")
        unknown = set(raw) - {"timeout_seconds", "max_output_tokens"}
        if unknown:
            raise ToolExecutionControlError(
                "Unknown _execution fields: " + ", ".join(sorted(unknown))
            )
        timeout = raw.get("timeout_seconds")
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or not 1 <= float(timeout) <= self.settings.max_timeout_seconds
        ):
            raise ToolExecutionControlError(
                "timeout_seconds must be a finite number from 1 to "
                f"{self.settings.max_timeout_seconds:g}"
            )
        output = raw.get("max_output_tokens")
        if output is not None and (
            isinstance(output, bool)
            or not isinstance(output, int)
            or not self.settings.min_output_tokens
            <= output
            <= self.settings.max_output_tokens
        ):
            raise ToolExecutionControlError(
                "max_output_tokens must be an integer from "
                f"{self.settings.min_output_tokens} to "
                f"{self.settings.max_output_tokens}"
            )
        return ToolExecutionRequest(
            timeout_seconds=None if timeout is None else float(timeout),
            max_output_tokens=output,
        )
