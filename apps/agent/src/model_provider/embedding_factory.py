"""Factory for configured embedding providers."""

from pathlib import Path

from apps.agent.src.model_config import ConfigModel
from apps.agent.src.model_provider.base_embedding import BaseEmbedding
from apps.agent.src.model_provider.impl.fastembed_embedding import (
    FastEmbedEmbedding,
)


class EmbeddingFactory:
    def __init__(self, config: ConfigModel, cache_dir: str | Path) -> None:
        self.config = config
        self.cache_dir = Path(cache_dir)

    def create_embedding(self) -> BaseEmbedding:
        settings = self.config.embedding
        if settings.provider == "fastembed":
            return FastEmbedEmbedding(settings=settings, cache_dir=self.cache_dir)
        raise ValueError(f"Unsupported embedding provider: {settings.provider}")
