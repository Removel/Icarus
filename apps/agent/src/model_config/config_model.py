import enum
from typing import Literal

import pydantic


class ThinkMode(str, enum.Enum):
    MAX = "max"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class LLMConfig(pydantic.BaseModel):
    model_name: str
    max_tokens: int
    temperature: float
    default_think_level: ThinkMode


class ModelSettings(pydantic.BaseModel):
    thinking: LLMConfig
    execution: LLMConfig
    perception: LLMConfig


LLMProtocol = Literal["openai", "anthropic"]
LLMRole = Literal["thinking", "execution", "perception"]


class ConfigModel(pydantic.BaseModel):
    openai_base_url: str
    anthropic_base_url: str
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    model_settings: ModelSettings
    use_protocol: LLMProtocol = "openai"
