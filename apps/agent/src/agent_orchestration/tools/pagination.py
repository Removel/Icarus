"""Shared validation and projections for bounded Tool lists."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar


T = TypeVar("T")


def page_number(value: object, *, default: int = 1) -> int:
    return _bounded_int(value, name="page_num", default=default, maximum=1_000_000)


def page_size(value: object, *, default: int = 50, maximum: int = 200) -> int:
    return _bounded_int(value, name="page_size", default=default, maximum=maximum)


def result_limit(value: object, *, default: int = 50, maximum: int = 200) -> int:
    return _bounded_int(value, name="limit", default=default, maximum=maximum)


def slice_page(
    values: Sequence[T], *, page_num: int, page_size: int
) -> tuple[list[T], bool]:
    start = (page_num - 1) * page_size
    end = start + page_size
    return list(values[start:end]), end < len(values)


def _bounded_int(value, *, name, default, maximum):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 1 to {maximum}")
    return value
