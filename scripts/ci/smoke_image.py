"""Start a built image and its app-owned fixtures on a disposable internal network."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def wait_healthy(name: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = json.loads(docker("inspect", name))[0]["State"]
        health = state.get("Health", {}).get("Status")
        if state["Status"] in {"exited", "dead"} or health == "unhealthy":
            raise RuntimeError(f"Container startup failed: {name} ({health or state['Status']})")
        if health == "healthy":
            return
        time.sleep(2)
    raise RuntimeError(f"Container health deadline exceeded: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", choices=("webui", "mem0", "openkb", "rela-mem"))
    parser.add_argument("image")
    args = parser.parse_args()
    test_directory = "tests" if args.app in {"mem0", "openkb"} else "test"
    spec = json.loads((ROOT / "apps" / args.app / test_directory / "image-smoke.json").read_text(encoding="utf-8"))
    prefix = "icarus-ci-smoke-" + uuid4().hex[:12]
    started = []
    network_created = False
    try:
        # No outbound network and no exposed host ports: a smoke cannot invoke
        # real model endpoints or touch developer services by accident.
        docker("network", "create", "--internal", prefix)
        network_created = True
        for fixture in spec.get("services", []):
            name = prefix + "-" + fixture["name"]
            command = ["run", "-d", "--name", name, "--network", prefix, "--network-alias", fixture["name"]]
            if fixture.get("entrypoint"):
                command.extend(["--entrypoint", fixture["entrypoint"]])
            for key, value in fixture.get("env", {}).items():
                command.extend(["-e", f"{key}={value}"])
            command.extend([fixture["image"], *fixture.get("command", [])])
            docker(*command)
            started.append(name)
            deadline = time.monotonic() + 90
            while subprocess.run(["docker", "exec", name, *fixture["ready"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Fixture readiness deadline exceeded: {name}")
                time.sleep(1)
        name = prefix + "-app"
        command = ["run", "-d", "--name", name, "--network", prefix,
                   "--health-interval", "2s", "--health-timeout", "10s", "--health-retries", "5"]
        for key, value in spec.get("env", {}).items():
            command.extend(["-e", f"{key}={value}"])
        command.append(args.image)
        docker(*command)
        started.append(name)
        wait_healthy(name, spec.get("timeout", 180))
        identity = docker("image", "inspect", args.image, "--format", "{{.Id}}")
        print(f"Healthy {args.app}: {identity}")
    finally:
        for name in reversed(started):
            subprocess.run(["docker", "logs", name], check=False)
            subprocess.run(["docker", "rm", "-f", "-v", name], check=False, stdout=subprocess.DEVNULL)
        if network_created:
            subprocess.run(["docker", "network", "rm", prefix], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
