"""Load application environment without depending on the caller's directory."""

import os
from pathlib import Path

from dotenv import load_dotenv


def repository_root() -> Path | None:
    app = Path(__file__).resolve().parents[3]
    if app.name == "agent" and app.parent.name == "apps" and (app.parent.parent / "AGENTS.md").is_file():
        return app.parent.parent
    return None


def load_icarus_environment() -> None:
    explicit = os.getenv("ICARUS_ENV_FILE")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError("ICARUS_ENV_FILE does not name a readable file")
        load_dotenv(dotenv_path=path, override=False)
        return
    root = repository_root()
    if root is not None and (root / ".env").is_file():
        load_dotenv(dotenv_path=root / ".env", override=False)
