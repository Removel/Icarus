import enum
import math
from pathlib import Path
from typing import Any, Literal

import pydantic
from icarus.runtime.fanout import DEFAULT_QUEUE_CAPACITY
from pydantic import ConfigDict, StrictBool, StrictFloat, StrictInt, model_validator


class ThinkMode(str, enum.Enum):
    MAX = "max"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class LLMConfig(pydantic.BaseModel):
    model_name: str
    context_window: int = pydantic.Field(gt=0)
    max_tokens: int
    temperature: float
    default_think_level: ThinkMode


class ModelSettings(pydantic.BaseModel):
    """Legacy fixed-role input accepted during the settings migration."""

    thinking: LLMConfig
    perception: LLMConfig


class NamedLLMConfig(LLMConfig):
    name: str = pydantic.Field(min_length=1)

    @pydantic.field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model name cannot be blank")
        return value.strip()


class ModelRoleSettings(pydantic.BaseModel):
    agent: str = "thinking"
    compaction: str = "thinking"
    skill_generation: str = "thinking"
    title: str = "perception"


class SkillSettings(pydantic.BaseModel):
    allow_produce: StrictBool = False
    allow_evolve: StrictBool = False


from icarus.core.tools.settings import ToolExecutionSettings


class AgentSettings(pydantic.BaseModel):
    max_steps: int = pydantic.Field(default=256, ge=1)
    tool_execution: ToolExecutionSettings = pydantic.Field(
        default_factory=ToolExecutionSettings
    )


class RuntimeSettings(pydantic.BaseModel):
    update_queue_capacity: StrictInt = pydantic.Field(default=DEFAULT_QUEUE_CAPACITY, ge=1)
    plugin_dirs: list[Path] = pydantic.Field(default_factory=list)
    plugin_config: dict[str, dict[str, Any]] = pydantic.Field(
        default_factory=dict
    )
    required_plugin_ids: list[str] = pydantic.Field(
        default_factory=lambda: [
            "persistence",
            "prompt",
            "builtin-tools",
            "agent",
            "user-input",
            "skill",
            "blackboard",
            "runtime-update",
            "mcp",
            "memory",
            "knowledge",
            "process",
            "persona",
        ]
    )


LLMProtocol = Literal["openai", "anthropic"]
from icarus.core.model_types import LLMRole


class ConfigModel(pydantic.BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    openai_base_url: str
    anthropic_base_url: str
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    icarus_data_dir: Path | None = None
    skill: SkillSettings = pydantic.Field(default_factory=SkillSettings)
    agent: AgentSettings = pydantic.Field(default_factory=AgentSettings)
    runtime: RuntimeSettings = pydantic.Field(default_factory=RuntimeSettings)
    mcp_servers: dict[str, dict[str, Any]] = pydantic.Field(
        default_factory=dict,
        alias="mcpServers",
    )
    model_settings: list[NamedLLMConfig]
    model_roles: ModelRoleSettings = pydantic.Field(default_factory=ModelRoleSettings)
    use_protocol: LLMProtocol = "openai"

    @pydantic.field_validator("model_settings", mode="before")
    @classmethod
    def legacy_models(cls, value):
        if isinstance(value, ModelSettings):
            value = value.model_dump()
        if isinstance(value, dict):
            return [dict(settings, name=name) for name, settings in value.items()]
        return value

    @model_validator(mode="after")
    def validate_model_roles(self):
        names = [item.name for item in self.model_settings]
        if len(names) != len(set(names)):
            raise ValueError("model names must be unique")
        for role, name in self.model_roles.model_dump().items():
            if name not in names:
                raise ValueError(f"model role {role} references unknown model {name}")
        return self

    def get_model(self, name: str) -> NamedLLMConfig:
        for item in self.model_settings:
            if item.name == name:
                return item
        raise ValueError(f"Unknown model role: {name}")
