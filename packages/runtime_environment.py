"""Shared loading for Runtime environment values used by local apps."""

import os
from pathlib import Path

from dotenv import load_dotenv


def load_icarus_environment() -> None:
    """Load the repository-root .env without depending on the process cwd."""

    repository_env = Path(__file__).resolve().parents[1] / ".env"
    if repository_env.is_file():
        load_dotenv(dotenv_path=repository_env, override=False)
        return
    load_dotenv(override=False)


def get_icarus_data_dir() -> Path:
    load_icarus_environment()
    value = os.getenv("ICARUS_DATA_DIR")
    if not value:
        raise RuntimeError("ICARUS_DATA_DIR is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeError("ICARUS_DATA_DIR must be an absolute path")
    return path.resolve()
