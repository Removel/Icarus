"""Small stdlib-only helpers for the Icarus lifecycle control."""

from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Mapping


def read_env_file(path: Path) -> dict[str, str]:
    """Read the simple dotenv syntax used by the repository without interpolation."""

    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = raw_value.strip()
        if value.startswith(("'", '"')):
            try:
                value = str(ast.literal_eval(value))
            except (SyntaxError, ValueError):
                continue
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def expand_braced_variables(value: str, values: Mapping[str, str]) -> str:
    """Expand ${NAME} references while leaving unknown references visible."""

    return re.sub(
        r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
        lambda match: values.get(match.group(1), match.group(0)),
        value,
    )
