"""Fail closed when an expected CI task did not succeed."""

import json
import os
from pathlib import Path


TASKS = {"syntax", "repository-control", "backend", "tui", "services", "webui", "images"}


def failures(expected: list[str], results: dict) -> list[str]:
    errors = []
    if not expected or "syntax" not in expected or not set(expected) <= TASKS:
        errors.append("Missing or invalid expected task set")
    for task in ["changes", *expected]:
        result = results.get(task, {}).get("result", "missing")
        if result != "success":
            errors.append(f"{task}: expected success, got {result}")
    for task, value in results.items():
        if task not in expected and task != "changes" and value.get("result") not in {"success", "skipped"}:
            errors.append(f"{task}: unexpected {value.get('result', 'missing')}")
    return errors


def main() -> None:
    expected = json.loads(os.environ.get("EXPECTED_TASKS") or "[]")
    results = json.loads(os.environ["JOB_RESULTS"])
    errors = failures(expected, results)
    report = {"expected": expected, "results": results, "errors": errors}
    text = json.dumps(report, indent=2)
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write("## CI required result\n\n```json\n" + text + "\n```\n")
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
