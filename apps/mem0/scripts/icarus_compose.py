"""Validate Icarus Mem0 configuration and execute Docker Compose."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

from dotenv import dotenv_values


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    env_file = repo_root / ".env"
    compose_file = repo_root / "apps" / "mem0" / "server" / "docker-compose.yaml"
    values = {
        key: value or ""
        for key, value in dotenv_values(env_file, interpolate=False).items()
        if key
    }
    environment = {**os.environ, **values}
    data_dir = Path(environment.get("ICARUS_DATA_DIR", ""))
    if not data_dir.is_absolute():
        raise SystemExit("ICARUS_DATA_DIR must be an absolute path.")
    for name in ("ICARUS_MEM0_POSTGRES_PASSWORD", "ICARUS_MEM0_JWT_SECRET"):
        if not environment.get(name):
            raise SystemExit(f"{name} is required in the repository .env.")
    if (
        environment.get("ICARUS_MEM0_AUTH_DISABLED", "false").lower()
        != "true"
        and not environment.get("ICARUS_MEM0_API_KEY")
    ):
        raise SystemExit(
            "ICARUS_MEM0_API_KEY is required unless "
            "ICARUS_MEM0_AUTH_DISABLED=true."
        )
    api_key = environment.get("ICARUS_MEM0_API_KEY", "")
    if api_key and len(api_key) < 16:
        raise SystemExit("ICARUS_MEM0_API_KEY must contain at least 16 characters.")
    llm_key = environment.get("ICARUS_MEM0_LLM_API_KEY") or environment.get(
        "OPENAI_API_KEY"
    )
    if not llm_key:
        raise SystemExit(
            "ICARUS_MEM0_LLM_API_KEY or OPENAI_API_KEY is required for Mem0."
        )
    environment["ICARUS_MEM0_LLM_API_KEY"] = llm_key
    if not environment.get("ICARUS_MEM0_OPENAI_BASE_URL"):
        environment["ICARUS_MEM0_OPENAI_BASE_URL"] = (
            "https://api.deepseek.com"
        )
    if not environment.get("ICARUS_MEM0_LLM_MODEL"):
        environment["ICARUS_MEM0_LLM_MODEL"] = "deepseek-v4-flash"
    compose = shutil.which("docker-compose")
    docker = shutil.which("docker")
    if compose is None and docker is None:
        raise SystemExit("Mem0 requires Docker with the compose plugin.")
    for relative in ("postgres", "history", "models", "backups"):
        (data_dir / "services" / "mem0" / relative).mkdir(
            parents=True, exist_ok=True
        )
    command = (
        [compose]
        if compose is not None
        else [docker, "compose"]
    )
    completed = subprocess.run(
        [
            *command, "--env-file", str(env_file), "-f",
            str(compose_file), *sys.argv[1:],
        ],
        env=environment,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
