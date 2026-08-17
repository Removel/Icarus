"""Provider-independent text embedding interface."""

from abc import ABC, abstractmethod


class BaseEmbedding(ABC):
    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed one retrieval query."""

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed retrieval documents in input order."""

    async def aclose(self) -> None:
        """Release provider-owned async resources, if any."""
