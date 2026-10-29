"""Shared loading for Runtime environment values used by local apps."""

import os
from pathlib import Path

from dotenv import load_dotenv


def load_icarus_environment() -> None:
    """Load the repository-root .env without depending on the process cwd."""

    if os.environ.get("ICARUS_ENVIRONMENT_READY") == "1":
        # The control plane already selected and injected this child's configuration.
        return
    repository_root = Path(__file__).resolve().parents[1]
    repository_env = repository_root / ".env"
    # Runtime metadata is public TOML; existing process values keep precedence.
    import tomllib
    runtime = {}
    for name in ('icarus.toml', 'icarus.local.toml'):
        path = repository_root / name
        if path.is_file():
            values = tomllib.loads(path.read_text(encoding='utf-8')).get('runtime', {})
            runtime = {**runtime, **{key: value for key, value in values.items() if key != 'env'},
                       'env': {**runtime.get('env', {}), **values.get('env', {})}}
    for key, value in runtime.get('env', {}).items():
        os.environ.setdefault(key, str(value).lower() if type(value) is bool else str(value))
    if runtime.get('data_dir'):
        os.environ.setdefault('ICARUS_DATA_DIR', str(Path(runtime['data_dir']).expanduser()))
    if repository_env.is_file():
        load_dotenv(dotenv_path=repository_env, override=False)
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
