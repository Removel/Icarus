"""Deterministic CI sharding and phase timings, shared by local and Actions runs."""

import json
from pathlib import Path
import time

import pytest


STATE = pytest.StashKey[dict]()


def pytest_addoption(parser):
    group = parser.getgroup("webui")
    group.addoption("--shard-index", type=int, default=1, help="1-based WebUI shard index")
    group.addoption("--shard-total", type=int, default=1, help="Number of WebUI shards")
    group.addoption("--timings-json", help="Write collection, outcomes and phase durations")
    group.addoption("--use-existing-build", action="store_true",
                    help="Production tests require an existing pnpm build instead of rebuilding")


def pytest_configure(config):
    index, total = config.getoption("shard_index"), config.getoption("shard_total")
    if not 1 <= index <= total:
        raise pytest.UsageError("WebUI requires 1 <= --shard-index <= --shard-total")
    config.stash[STATE] = {
        "shard_index": index, "shard_total": total,
        "collected": [], "selected": [], "tests": {},
        "started": time.monotonic(),
    }


def shard_for(nodeids, total):
    """Spread large parameterized/chat files; keep production fixtures in one job."""
    assignments = {}
    next_index = 0
    for nodeid in sorted(nodeids):
        if nodeid.startswith("test/server/"):
            assignments[nodeid] = 1
        else:
            assignments[nodeid] = next_index % total + 1
            next_index += 1
    return assignments


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    state = config.stash[STATE]
    state["collected"] = [item.nodeid for item in items]
    assignments = shard_for(state["collected"], state["shard_total"])
    selected, deselected = [], []
    for item in items:
        (selected if assignments[item.nodeid] == state["shard_index"] else deselected).append(item)
    state["selected"] = [item.nodeid for item in selected]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    item.config.stash[STATE]["tests"].setdefault(item.nodeid, {})[report.when] = {
        "duration": report.duration, "outcome": report.outcome,
    }


def pytest_sessionfinish(session, exitstatus):
    state = session.config.stash[STATE]
    state["elapsed_seconds"] = time.monotonic() - state.pop("started")
    state["exitstatus"] = int(exitstatus)
    if path := session.config.getoption("timings_json"):
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pytest_terminal_summary(terminalreporter):
    state = terminalreporter.config.stash[STATE]
    terminalreporter.section("WebUI phase totals (seconds)")
    for phase in ("setup", "call", "teardown"):
        duration = sum(test.get(phase, {}).get("duration", 0) for test in state["tests"].values())
        terminalreporter.write_line(f"{phase}: {duration:.2f}")
