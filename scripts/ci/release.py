"""Validate release identity and promote only tested platform image digests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


APPS = ("webui", "mem0", "openkb", "rela-mem")
VERSION = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\Z")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")


def parse_version(value: str) -> tuple[tuple[int, int, int], str]:
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError("Release tag must be a version such as v1.2.3 or v1.2.3-rc.1")
    return tuple(int(match[i]) for i in (1, 2, 3)), match[4] or ""


def should_update_latest(version: str, current: str | None) -> bool:
    numeric, prerelease = parse_version(version)
    if prerelease:
        return False
    if current is None:
        return True
    previous, previous_pre = parse_version(current)
    if previous_pre:
        raise ValueError("Existing latest unexpectedly points to a prerelease")
    return numeric >= previous


def validate_records(records: list[dict], app: str, sha: str) -> list[str]:
    rows = [record for record in records if record.get("app") == app]
    if len(rows) != 2 or {row.get("arch") for row in rows} != {"amd64", "arm64"}:
        raise ValueError(f"Missing or duplicate platform records: {app}")
    if any(row.get("sha") != sha or not DIGEST.fullmatch(row.get("digest", "")) for row in rows):
        raise ValueError(f"Unverified commit or image digest: {app}")
    return [row["digest"] for row in sorted(rows, key=lambda row: row["arch"])]


def same_platforms(manifest: dict, expected: list[str]) -> bool:
    return sorted(row["digest"] for row in manifest.get("manifests", [])) == sorted(expected)


def inspect_manifest(reference: str) -> dict | None:
    # Buildx --raw preserves the registry bytes without adding a newline.
    result = subprocess.run(["docker", "buildx", "imagetools", "inspect", "--raw", reference], capture_output=True)
    if result.returncode:
        # Only a missing tag allows creation. Network/auth/registry failures fail closed.
        error = result.stderr.decode("utf-8", errors="replace")
        if "manifest unknown" in error.lower() or (
            reference in error and re.search(r": not found\s*$", error, re.IGNORECASE)
        ):
            return None
        raise RuntimeError(error)
    manifest = json.loads(result.stdout)
    manifest["digest"] = "sha256:" + hashlib.sha256(result.stdout).hexdigest()
    return manifest


def prepare(tag: str) -> None:
    numeric, prerelease = parse_version(tag)
    if os.environ.get("GITHUB_REPOSITORY") != "Removel/Icarus":
        raise ValueError("Publishing is restricted to Removel/Icarus")
    sha = subprocess.check_output(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], text=True).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if sha != head:
        raise ValueError("Checked out commit does not match release tag")
    if os.environ.get("GITHUB_SHA") != sha:
        raise ValueError("Dispatch the workflow on the release tag so reusable checks verify the same SHA")
    allowed = ["main"] if not prerelease else ["main", "dev", "feature"]
    if not any(subprocess.run(["git", "merge-base", "--is-ancestor", sha, f"origin/{branch}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0 for branch in allowed):
        raise ValueError("Release commit is outside the allowed source branches")
    event_file = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(event_file).read_text(encoding="utf-8")) if event_file else {}
    release = event.get("release")
    if release and bool(release.get("prerelease")) != bool(prerelease):
        raise ValueError("Release prerelease flag and version suffix disagree")
    values = {"sha": sha, "version": ".".join(map(str, numeric)) + prerelease}
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")
    print(json.dumps(values))


def promote(version: str, sha: str, directory: Path, *, dry_run: bool) -> None:
    _, prerelease = parse_version(version)
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Invalid commit SHA")
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]
    if len(records) != 8 or any(row.get("app") not in APPS for row in records):
        raise ValueError("Expected exactly eight verified platform image records")
    # Validate every application and immutable tag before making any mutation.
    plans = []
    for app in APPS:
        digests = validate_records(records, app, sha)
        repository = f"removel/icarus-{app}"
        existing = inspect_manifest(f"{repository}:{version}")
        if existing is not None and not same_platforms(existing, digests):
            raise ValueError(f"Refusing to overwrite an existing version with different images: {app}:{version}")
        latest = None if prerelease else inspect_manifest(f"{repository}:latest")
        current = latest.get("annotations", {}).get("org.opencontainers.image.version") if latest else None
        if latest is not None and current is None:
            raise ValueError(f"Existing latest has no version annotation; establish its baseline explicitly: {app}")
        update_latest = should_update_latest(version, current)
        command = ["docker", "buildx", "imagetools", "create", "--annotation", f"index:org.opencontainers.image.version={version}",
                   "--annotation", f"index:org.opencontainers.image.revision={sha}", "-t", f"{repository}:{version}"]
        if update_latest:
            command.extend(["-t", f"{repository}:latest"])
        command.extend(f"{repository}@{digest}" for digest in digests)
        plans.append({"app": app, "digests": digests, "update_latest": update_latest, "command": command})
    report = {"version": version, "sha": sha, "dry_run": dry_run, "status": "validated", "applications": plans}
    record = Path("release-record.json")
    record.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    try:
        for plan in plans:
            if not dry_run:
                report["status"] = "promoting"
                subprocess.run(plan["command"], check=True)
                manifest = inspect_manifest(f"removel/icarus-{plan['app']}:{version}")
                if manifest is None or not same_platforms(manifest, plan["digests"]):
                    raise ValueError(f"Published manifest does not match tested platform digests: {plan['app']}")
                plan["manifest_digest"] = manifest["digest"]
                plan["manifest"] = manifest
        report["status"] = "dry-run" if dry_run else "published"
    except Exception:
        report["status"] = "failed"
        raise
    finally:
        # Preserve completed applications even if a later registry mutation fails.
        record.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "promote"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.operation == "prepare":
        prepare(os.environ["RELEASE_TAG"])
    else:
        promote(os.environ["RELEASE_VERSION"], os.environ["RELEASE_SHA"], Path("digests"), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
