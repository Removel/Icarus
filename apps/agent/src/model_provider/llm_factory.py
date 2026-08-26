from pathlib import Path
from typing import Callable

from apps.agent.src.model_config import (
    ConfigModel,
    LLMProtocol,
    LLMRole,
    ThinkMode,
    get_config,
)
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import ImagePart
from apps.agent.src.model_provider.impl.anthropic_llm import AnthropicLLM
from apps.agent.src.model_provider.impl.openai_llm import OpenAILLM


class LLMFactory:
    """根据协议创建持久化使用的 LLM 适配器。"""

    def __init__(
        self,
        config: ConfigModel | None = None,
        image_resolver: Callable[[ImagePart], Path] | None = None,
    ) -> None:
        self.config = config or get_config()
        self.image_resolver = image_resolver

    def create_llm(
        self,
        role: LLMRole,
        protocol: LLMProtocol | None = None,
        model_name: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        default_think_level: ThinkMode | None = None,
        thinking_budget: int | None = None,
    ) -> BaseLLM:
        model_config = getattr(self.config.model_settings, role)
        selected_protocol = protocol or self.config.use_protocol
        selected_model_name = model_name or model_config.model_name
        selected_max_tokens = (
            max_tokens if max_tokens is not None else model_config.max_tokens
        )
        selected_temperature = (
            temperature if temperature is not None else model_config.temperature
        )
        selected_think_level = (
            default_think_level or model_config.default_think_level
        )

        if selected_protocol == "openai":
            if thinking_budget is not None:
                raise ValueError(
                    "thinking_budget is only supported by Anthropic",
                )
            return OpenAILLM(
                model_name=selected_model_name,
                api_key=self.config.openai_api_key,
                base_url=self.config.openai_base_url,
                max_tokens=selected_max_tokens,
                temperature=selected_temperature,
                reasoning_effort=selected_think_level,
                image_resolver=self.image_resolver,
            )
        if selected_protocol == "anthropic":
            return AnthropicLLM(
                model_name=selected_model_name,
                api_key=self.config.anthropic_api_key,
                base_url=self.config.anthropic_base_url,
                max_tokens=selected_max_tokens,
                temperature=selected_temperature,
                thinking_budget=(
                    thinking_budget if thinking_budget is not None else 1024
                ),
                image_resolver=self.image_resolver,
            )
        raise ValueError(f"Unsupported protocol: {selected_protocol}")
