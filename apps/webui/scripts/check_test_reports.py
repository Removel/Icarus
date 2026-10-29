"""Reject missing/duplicate shard coverage and summarize test phase durations."""

from collections import Counter
import argparse
import json
import os
from pathlib import Path


def summarize(reports, expected_shards):
    if expected_shards < 1 or len(reports) != expected_shards:
        raise ValueError(f"Expected {expected_shards} shard reports, got {len(reports)}")
    if {report["shard_index"] for report in reports} != set(range(1, expected_shards + 1)):
        raise ValueError("Missing or duplicate shard index")
    collected = reports[0]["collected"]
    if not collected or len(collected) != len(set(collected)):
        raise ValueError("Empty or duplicate collection")
    selected = []
    passed = skipped = 0
    lines = ["| Shard | Tests | Setup | Call | Teardown | Elapsed |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for report in sorted(reports, key=lambda report: report["shard_index"]):
        if report["shard_total"] != expected_shards or report["collected"] != collected:
            raise ValueError("Shards collected different suites")
        if report["exitstatus"] != 0:
            raise ValueError(f"Shard {report['shard_index']} failed: exit {report['exitstatus']}")
        if Counter(report["selected"]) != Counter(report["tests"].keys()):
            raise ValueError("Selected tests were not all executed exactly once")
        selected.extend(report["selected"])
        for phases in report["tests"].values():
            if any(phase["outcome"] == "failed" for phase in phases.values()):
                raise ValueError("A test phase failed")
            if any(phase["outcome"] == "skipped" for phase in phases.values()):
                skipped += 1
            elif phases.keys() >= {"setup", "call", "teardown"}:
                passed += 1
            else:
                raise ValueError("A test did not complete all phases")
        totals = [sum(test.get(phase, {}).get("duration", 0) for test in report["tests"].values())
                  for phase in ("setup", "call", "teardown")]
        values = " | ".join(f"{value:.2f}s" for value in [*totals, report["elapsed_seconds"]])
        lines.append(f"| {report['shard_index']} | {len(report['selected'])} | {values} |")
    if Counter(selected) != Counter(collected):
        raise ValueError("Shard coverage has missing or duplicate tests")
    return f"{passed} passed, {skipped} skipped; {len(collected)} tests covered exactly once.\n\n" + "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected-shards", type=int, required=True)
    args = parser.parse_args()
    reports = [json.loads(path.read_text(encoding="utf-8"))
               for path in sorted(args.directory.rglob("timings.json"))]
    try:
        summary = summarize(reports, args.expected_shards)
    except ValueError as error:
        parser.exit(1, f"WebUI report validation failed: {error}\n")
    print(summary)
    if target := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(target).open("a", encoding="utf-8") as output:
            output.write("## WebUI test coverage and timings\n\n" + summary + "\n")


if __name__ == "__main__":
    main()
