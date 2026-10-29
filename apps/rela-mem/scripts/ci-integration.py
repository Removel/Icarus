"""Run integration tests with disposable databases and no developer configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4


APP = Path(__file__).resolve().parents[1]


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def wait_ready(name: str, command: list[str]) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if subprocess.run(["docker", "exec", name, *command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"Database did not become ready: {name}")


def main() -> int:
    prefix = "icarus-ci-rela-" + uuid4().hex[:12]
    names = [prefix + "-pg", prefix + "-graph"]
    started = []
    try:
        docker("run", "-d", "--name", names[0], "-p", "127.0.0.1::5432",
               "-e", "POSTGRES_USER=rela_mem", "-e", "POSTGRES_DB=rela_mem_test",
               "-e", "POSTGRES_PASSWORD=rela-test-pg-password", "pgvector/pgvector:pg17")
        started.append(names[0])
        docker("run", "-d", "--name", names[1], "-p", "127.0.0.1::6379", "--entrypoint", "redis-server",
               "falkordb/falkordb:v4.22.1", "--loadmodule", "/var/lib/falkordb/bin/falkordb.so",
               "--requirepass", "rela-test-graph-password")
        started.append(names[1])
        wait_ready(names[0], ["pg_isready", "-h", "127.0.0.1", "-U", "rela_mem", "-d", "rela_mem_test"])
        wait_ready(names[1], ["redis-cli", "-a", "rela-test-graph-password", "ping"])
        ports = []
        for name, port in zip(names, ["5432/tcp", "6379/tcp"]):
            state = json.loads(docker("inspect", name))[0]
            ports.append(state["NetworkSettings"]["Ports"][port][0]["HostPort"])
        environment = {**os.environ, "RELA_TEST_PG_PORT": ports[0], "RELA_TEST_GRAPH_PORT": ports[1]}
        python = APP / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        return subprocess.call([str(python), "-m", "pytest", "test", "-q",
                                "--junitxml=test-results/integration.xml"], cwd=APP, env=environment)
    finally:
        for name in reversed(started):
            subprocess.run(["docker", "logs", name], check=False)
            subprocess.run(["docker", "rm", "-f", "-v", name], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
