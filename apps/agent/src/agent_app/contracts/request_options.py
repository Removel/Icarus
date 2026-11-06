"""Application input validation without materializing task policy defaults."""

from collections.abc import Mapping


def validate_search_request_options(options: Mapping[str, object]) -> None:
    for field in ("web_search", "deep_search"):
        if field in options and not isinstance(options[field], bool):
            raise ValueError(f"{field} must be a boolean when provided")
