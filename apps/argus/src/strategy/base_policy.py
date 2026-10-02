"""Typed judgment contract; external queries are injected by callers."""
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

InputT = TypeVar('InputT')
OutputT = TypeVar('OutputT')


class BasePolicy(ABC, Generic[InputT, OutputT]):
    name: str

    @abstractmethod
    async def evaluate(self, value: InputT) -> OutputT:
        raise NotImplementedError
