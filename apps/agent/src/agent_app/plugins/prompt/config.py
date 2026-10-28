"""Prompt budgets and contributor policy; injected runtime resources are separate."""

import math

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_prompt_tokens: StrictInt = Field(default=12288, ge=1)
    max_section_tokens: StrictInt = Field(default=8192, ge=1)
    contributor_timeout_seconds: float = Field(default=5.0, gt=0)
    required_contributors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_limits(self):
        if self.max_section_tokens > self.max_prompt_tokens:
            raise ValueError("Section budget cannot exceed total prompt budget")
        if not math.isfinite(self.contributor_timeout_seconds):
            raise ValueError("Contributor timeout must be finite")
        if len(set(self.required_contributors)) != len(self.required_contributors):
            raise ValueError("Required contributors must be unique")
        if any(not value.strip() for value in self.required_contributors):
            raise ValueError("Required contributor ID cannot be blank")
        return self
