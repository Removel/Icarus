"""Typed, side-effect-free policy contract."""
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

InputT = TypeVar('InputT')
OutputT = TypeVar('OutputT')


class BasePolicy(ABC, Generic[InputT, OutputT]):
    name: str
    version: str

    @abstractmethod
    async def evaluate(self, value: InputT) -> OutputT:
        raise NotImplementedError
