"""Conservative Git-based CI selection. No application or third-party imports."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


ALL_TASKS = ("syntax", "repository-control", "backend", "tui", "services", "webui", "images")
SERVICE_APPS = ("mem0", "openkb", "rela-mem")
IMAGE_APPS = ("webui", "mem0", "openkb", "rela-mem")
CONSUMERS = {
    "agent": {"backend", "webui"},
    "gateway": {"backend", "tui", "webui"},
    "tui": {"tui"},
    "webui": {"webui", "images"},
    "mem0": {"services", "backend", "webui", "images"},
    "openkb": {"services", "backend", "webui", "images"},
    "rela-mem": {"services", "backend", "webui", "images"},
}


def changed_paths(raw: bytes) -> list[str]:
    fields = raw.decode("utf-8").split("\0")
    if fields[-1] != "":
        raise ValueError("Git change list must be NUL terminated")
    fields.pop()
    paths = []
    index = 0
    while index < len(fields):
        status = fields[index]
        count = 2 if status.startswith(("R", "C")) else 1
        if not status or status[0] not in "ACDMRTUXB" or index + count >= len(fields):
            raise ValueError("Incomplete Git change record")
        paths.extend(fields[index + 1:index + 1 + count])
        index += count + 1
    return paths


def select_tasks(paths: list[str], *, full: bool = False) -> dict:
    tasks = {"syntax"}
    services = set()
    images = set()
    reasons = []
    for path in paths:
        # Markdown and JSON inside source trees can be runtime resources.
        if path.startswith("docs/") or path in {"README.md", "CONTRIBUTING.md", "AGENTS.md", "LICENSE", "THIRD_PARTY_NOTICES.md"}:
            reasons.append(f"documentation: {path}")
            continue
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == "apps" and parts[1] in CONSUMERS:
            app = parts[1]
            tasks.update(CONSUMERS[app])
            if app in SERVICE_APPS:
                services.add(app)
            if app in IMAGE_APPS:
                images.add(app)
            reasons.append(f"application {app}: {path}")
        else:
            full = True
            reasons.append(f"shared or unclassified input; full verification: {path}")
    if full:
        tasks.update(ALL_TASKS)
        services.update(SERVICE_APPS)
        images.update(IMAGE_APPS)
    result = {
        "expected": [task for task in ALL_TASKS if task in tasks],
        "service_apps": [app for app in SERVICE_APPS if app in services],
        "images": [app for app in IMAGE_APPS if app in images],
        "reasons": reasons or ["full verification" if full else "no changed files"],
    }
    for task in ALL_TASKS:
        result["run_" + task.replace("-", "_")] = task in tasks
    return result


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def event_range(event_name: str, event: dict) -> tuple[str | None, str, bool]:
    if event_name == "pull_request":
        pr = event["pull_request"]
        return pr["base"]["sha"], pr["head"]["sha"], True
    if event_name == "push":
        before = event.get("before", "")
        return (before if before and set(before) != {"0"} else None), event["after"], False
    return None, "HEAD", False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")
    event_file = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(event_file).read_text(encoding="utf-8")) if event_file else {}
    base, head, merge_base = event_range(event_name, event)
    full = args.full or base is None
    paths = []
    if base is not None:
        try:
            if merge_base:
                base = git("merge-base", base, head).decode().strip()
            paths = changed_paths(git("diff", "--name-status", "-z", "--find-renames", base, head, "--"))
        except subprocess.CalledProcessError:
            # An unavailable base must never turn into an empty successful selection.
            full = True
    result = select_tasks(paths, full=full)
    result.update(base=base, head=head, test_sha=git("rev-parse", "HEAD").decode().strip())
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            for key, value in result.items():
                stream.write(f"{key}={json.dumps(value, ensure_ascii=True, separators=(',', ':'))}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write("## CI selection\n\n```json\n" + json.dumps(result, indent=2, ensure_ascii=True) + "\n```\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
