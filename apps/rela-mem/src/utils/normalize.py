import re


def normalize_alias(namespace: str, value: str) -> tuple[str, str | None]:
    if namespace in {"qq", "phone"}:
        normalized = re.sub(r"[^0-9]", "", value)
    elif namespace == "wechat":
        normalized = value.strip()
    else:
        normalized = value.strip().lower()
    if not normalized:
        raise ValueError("alias normalizes to an empty value")
    return normalized, None if namespace == "name" else f"{namespace}:{normalized}"
