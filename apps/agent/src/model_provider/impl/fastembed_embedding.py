"""Local FastEmbed adapter."""

import asyncio
from pathlib import Path
from typing import Any

from apps.agent.src.model_config import EmbeddingSettings
from apps.agent.src.model_provider.base_embedding import BaseEmbedding


class FastEmbedEmbedding(BaseEmbedding):
    def __init__(
        self,
        settings: EmbeddingSettings,
        cache_dir: str | Path,
    ) -> None:
        self._settings = settings
        self._cache_dir = Path(cache_dir)
        self._model: Any | None = None
        self._model_lock = asyncio.Lock()
        self._model_task: asyncio.Task[Any] | None = None

    async def embed_query(self, text: str) -> list[float]:
        model = await self._get_model()
        vectors = await asyncio.to_thread(
            lambda: list(model.query_embed(text))
        )
        return self._to_list(vectors[0])

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._get_model()
        vectors = await asyncio.to_thread(
            lambda: list(model.passage_embed(texts))
        )
        return [self._to_list(vector) for vector in vectors]

    async def aclose(self) -> None:
        task = self._model_task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=1.0,
            )
        except TimeoutError:
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is None:
                if self._model_task is None:
                    self._model_task = asyncio.create_task(
                        asyncio.to_thread(self._create_model)
                    )
                    self._model_task.add_done_callback(
                        self._model_task_completed
                    )
            task = self._model_task
        if task is None:
            return self._model
        try:
            model = await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._model_lock:
                if self._model_task is task:
                    self._model_task = None
            raise
        async with self._model_lock:
            if self._model is None:
                self._model = model
            if self._model_task is task:
                self._model_task = None
            return self._model

    def _model_task_completed(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        try:
            model = task.result()
        except Exception:
            model = None
        if model is not None and self._model is None:
            self._model = model
        if self._model_task is task:
            self._model_task = None

    def _create_model(self) -> Any:
        from fastembed import TextEmbedding

        return TextEmbedding(
            model_name=self._settings.model_name,
            cache_dir=str(self._cache_dir),
        )

    @staticmethod
    def _to_list(vector: Any) -> list[float]:
        values = vector.tolist() if hasattr(vector, "tolist") else list(vector)
        return [float(value) for value in values]
