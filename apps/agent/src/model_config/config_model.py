import enum

import pydantic


class ThinkLevel(str, enum.Enum):
    XHIGH = "extra_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ModelConfig(pydantic.BaseModel):
    model_name: str
    max_tokens: int
    min_tokens: int
    temperature: float
    default_think_level: ThinkLevel


class ModelSetting(pydantic.BaseModel):
    think: ModelConfig
    execute: ModelConfig
    emotion: ModelConfig


class ConfigModel(pydantic.BaseModel):
    openai_base_url: str
    anthropic_base_url: str
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    model_settings: ModelSetting