"""Shared loading for Runtime environment values used by local apps."""

from importlib.resources import as_file, files
import os
from pathlib import Path

from dotenv import load_dotenv


def load_icarus_environment() -> None:
    """Load the repository Agent .env, then fall back to normal discovery."""

    resource = files("apps.agent").joinpath(".env")
    if resource.is_file():
        with as_file(resource) as env_path:
            load_dotenv(dotenv_path=env_path, override=False)
    else:
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
