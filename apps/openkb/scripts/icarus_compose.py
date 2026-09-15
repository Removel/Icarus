"""Validate Icarus OpenKB configuration and execute Docker Compose."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts" / "icarus"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from environment import read_env_file


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    env_file = repo_root / ".env"
    compose_file = repo_root / "apps" / "openkb" / "docker-compose.yaml"
    values = read_env_file(env_file)
    environment = {**os.environ, **values}
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    passive = action in {
        "build", "down", "stop", "logs", "ps", "kill", "rm"
    }
    if passive:
        environment["ICARUS_DATA_DIR"] = environment.get(
            "ICARUS_DATA_DIR"
        ) or str(Path(tempfile.gettempdir()) / "icarus-compose")
        environment["ICARUS_OPENKB_API_TOKEN"] = environment.get(
            "ICARUS_OPENKB_API_TOKEN"
        ) or "not-used-not-used"
        environment["ICARUS_OPENKB_LLM_API_KEY"] = environment.get(
            "ICARUS_OPENKB_LLM_API_KEY"
        ) or "not-used"
    else:
        _validate_start_environment(environment, repo_root)
    return _run_compose(environment, env_file, compose_file)


def _validate_start_environment(environment: dict[str, str], repo_root: Path) -> None:
    data_dir = Path(environment.get("ICARUS_DATA_DIR", ""))
    if not data_dir.is_absolute():
        raise SystemExit("ICARUS_DATA_DIR must be an absolute path.")
    token = environment.get("ICARUS_OPENKB_API_TOKEN", "")
    if len(token) < 16:
        raise SystemExit(
            "ICARUS_OPENKB_API_TOKEN must contain at least 16 characters."
        )
    llm_key = environment.get("ICARUS_OPENKB_LLM_API_KEY") or environment.get(
        "OPENAI_API_KEY"
    )
    if not llm_key:
        raise SystemExit(
            "ICARUS_OPENKB_LLM_API_KEY or OPENAI_API_KEY is required for OpenKB."
        )
    environment["ICARUS_OPENKB_LLM_API_KEY"] = llm_key
    if not environment.get("ICARUS_OPENKB_OPENAI_BASE_URL"):
        environment["ICARUS_OPENKB_OPENAI_BASE_URL"] = "https://api.deepseek.com"
    if not environment.get("ICARUS_OPENKB_LLM_MODEL"):
        environment["ICARUS_OPENKB_LLM_MODEL"] = "deepseek/deepseek-v4-flash"
    settings_path = repo_root / "apps" / "agent" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    knowledge = settings.get("runtime", {}).get("plugin_config", {}).get(
        "knowledge", {}
    )
    if not environment.get("ICARUS_OPENKB_KNOWLEDGE_BASE"):
        environment["ICARUS_OPENKB_KNOWLEDGE_BASE"] = knowledge.get(
            "knowledge_base", "icarus-project"
        )
    compose = shutil.which("docker-compose")
    docker = shutil.which("docker")
    if compose is None and docker is None:
        raise SystemExit("OpenKB requires Docker with the compose plugin.")
    for relative in ("config", "kbs", "backups"):
        (data_dir / "services" / "openkb" / relative).mkdir(
            parents=True, exist_ok=True
        )


def _run_compose(
    environment: dict[str, str], env_file: Path, compose_file: Path
) -> int:
    compose = shutil.which("docker-compose")
    docker = shutil.which("docker")
    if compose is None and docker is None:
        raise SystemExit("OpenKB requires Docker with the compose plugin.")
    command = [compose] if compose is not None else [docker, "compose"]
    env_args = ["--env-file", str(env_file) if env_file.is_file() else os.devnull]
    completed = subprocess.run(
        [
            *command, *env_args, "-f",
            str(compose_file), *sys.argv[1:],
        ],
        env=environment,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
