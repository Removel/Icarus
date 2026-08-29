import enum
from pathlib import Path
from typing import Any, Literal

import pydantic
from pydantic import StrictBool


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
    thinking: LLMConfig
    perception: LLMConfig


class SkillSettings(pydantic.BaseModel):
    allow_produce: StrictBool = False
    allow_evolve: StrictBool = False


class AgentSettings(pydantic.BaseModel):
    max_steps: int = pydantic.Field(default=256, ge=1)


class RuntimeSettings(pydantic.BaseModel):
    plugin_dirs: list[Path] = pydantic.Field(default_factory=list)
    plugin_config: dict[str, dict[str, Any]] = pydantic.Field(
        default_factory=dict
    )
    required_plugin_ids: list[str] = pydantic.Field(
        default_factory=lambda: [
            "persistence",
            "builtin-tools",
            "agent",
            "user-input",
            "skill",
            "blackboard",
            "runtime-update",
        ]
    )


LLMProtocol = Literal["openai", "anthropic"]
LLMRole = Literal["thinking", "perception"]


class ConfigModel(pydantic.BaseModel):
    openai_base_url: str
    anthropic_base_url: str
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    icarus_data_dir: Path | None = None
    skill: SkillSettings = pydantic.Field(default_factory=SkillSettings)
    agent: AgentSettings = pydantic.Field(default_factory=AgentSettings)
    runtime: RuntimeSettings = pydantic.Field(default_factory=RuntimeSettings)
    model_settings: ModelSettings
    use_protocol: LLMProtocol = "openai"
