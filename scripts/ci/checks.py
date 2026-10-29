"""Repository syntax checks shared by local validation and GitHub Actions."""

from __future__ import annotations

import argparse
import compileall
import json
import os
from pathlib import Path
import subprocess


SOURCES = (
    "scripts", "packages", "apps/agent/src", "apps/agent/scripts", "apps/gateway/src", "apps/tui/src",
    "apps/mem0/mem0", "apps/mem0/server", "apps/mem0/scripts",
    "apps/openkb/openkb", "apps/openkb/scripts", "apps/rela-mem/src", "apps/rela-mem/scripts",
    "apps/webui/scripts",
)


def check_shell(paths: list[str]) -> int:
    if not paths:
        raw = subprocess.check_output(["git", "ls-files", "-z", "--", "*.sh", "bin/icarus"])
        paths = [path for path in raw.decode("utf-8").split("\0") if path and Path(path).is_file()]
    failed = False
    for path in paths:
        # Match the LF text Git checks out in CI without rewriting a Windows worktree.
        source = Path(path).read_bytes().replace(b"\r\n", b"\n")
        result = subprocess.run(["bash", "-n"], input=source, capture_output=True)
        if result.returncode:
            print(f"Shell syntax failed: {path}")
            print(result.stderr.decode("utf-8", errors="replace"))
            failed = True
    return int(failed)


def check_diff(paths: list[str], *, no_index: bool) -> int:
    if no_index:
        return subprocess.run(["git", "diff", "--no-index", "--check", *paths]).returncode
    if paths:
        if len(paths) != 2:
            raise ValueError("Supply both base and head, or neither")
        base, head = paths
    else:
        # Import from the same directory when executed as a script.
        from changes import event_range

        event_file = os.environ.get("GITHUB_EVENT_PATH")
        event = json.loads(Path(event_file).read_text(encoding="utf-8")) if event_file else {}
        base, head, merge_base = event_range(os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch"), event)
        if base and merge_base:
            base = subprocess.check_output(["git", "merge-base", base, head], text=True).strip()
        if base is None:
            # Manual runs check the selected commit, rather than introducing a
            # new all-history whitespace gate on unchanged vendored sources.
            parent = subprocess.run(["git", "rev-parse", f"{head}^"], capture_output=True, text=True)
            base = parent.stdout.strip() if parent.returncode == 0 else subprocess.check_output(
                ["git", "hash-object", "-w", "-t", "tree", "--stdin"], input=b""
            ).decode().strip()
    return subprocess.run(["git", "diff", "--check", base, head, "--"]).returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("shell", "diff", "compile"))
    parser.add_argument("--no-index", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_intermixed_args()
    if args.check == "shell":
        result = check_shell(args.paths)
    elif args.check == "diff":
        result = check_diff(args.paths, no_index=args.no_index)
    else:
        failed = False
        for name in args.paths or SOURCES:
            if not Path(name).is_dir():
                print(f"Missing source directory: {name}")
                failed = True
            elif not compileall.compile_dir(name, quiet=1):
                failed = True
        result = int(failed)
    raise SystemExit(result)


if __name__ == "__main__":
    main()
