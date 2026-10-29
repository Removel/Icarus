"""Exercise real pytest collection/outcomes and the CI completeness gate."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


def checker():
    path = Path(__file__).resolve().parents[1] / "scripts/check_test_reports.py"
    spec = importlib.util.spec_from_file_location("webui_test_reports", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def reports(pytester):
    pytester.makepyfile("""
        import pytest
        @pytest.mark.parametrize('value', range(8))
        def test_chat(value):
            assert value >= 0
        @pytest.mark.skip(reason='existing opt-in')
        def test_live():
            pass
    """)
    server = pytester.path / "test/server"
    server.mkdir(parents=True)
    (server / "test_production.py").write_text("def test_server():\n    assert True\n")
    result = []
    for index in range(1, 5):
        path = pytester.path / f"shard-{index}.json"
        run = pytester.runpytest("-p", "test.ci", "--shard-total=4", f"--shard-index={index}",
                               f"--timings-json={path}")
        assert run.ret == 0
        result.append(json.loads(path.read_text(encoding="utf-8")))
    return result


def test_shards_cover_every_case_once_and_preserve_skips(reports):
    summary = checker().summarize(reports, 4)
    assert "9 passed, 1 skipped; 10 tests covered exactly once" in summary
    assert [report["shard_index"] for report in reports
            if "test/server/test_production.py::test_server" in report["selected"]] == [1]


@pytest.mark.parametrize("damage", ["missing-report", "missing-test", "duplicate-test",
                                   "duplicate-index", "different-collection", "incomplete-test",
                                   "failed-phase", "failed-shard"])
def test_gate_rejects_incomplete_or_failed_shards(reports, damage):
    damaged = copy.deepcopy(reports)
    if damage == "missing-report":
        damaged.pop()
    elif damage == "missing-test":
        damaged[0]["selected"].pop()
    elif damage == "duplicate-test":
        damaged[0]["selected"].append(damaged[0]["selected"][0])
    elif damage == "duplicate-index":
        damaged[1]["shard_index"] = 1
    elif damage == "different-collection":
        damaged[0]["collected"] = []
    elif damage == "incomplete-test":
        damaged[0]["tests"].pop(damaged[0]["selected"][0])
    elif damage == "failed-phase":
        damaged[0]["tests"][damaged[0]["selected"][0]]["teardown"]["outcome"] = "failed"
    else:
        damaged[0]["exitstatus"] = 1
    with pytest.raises(ValueError):
        checker().summarize(damaged, 4)


def test_failed_test_is_retained_in_timings(pytester):
    pytester.makepyfile("def test_failure():\n    assert False\n")
    path = pytester.path / "timings.json"
    result = pytester.runpytest("-p", "test.ci", f"--timings-json={path}")
    assert result.ret == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["exitstatus"] == 1
    phases = next(iter(report["tests"].values()))
    assert phases["call"]["outcome"] == "failed"
    assert phases.keys() == {"setup", "call", "teardown"}


@pytest.mark.parametrize("arguments", [["--shard-total=0"], ["--shard-index=2"],
                                        ["--shard-index=0", "--shard-total=4"]])
def test_invalid_shard_is_an_error(pytester, arguments):
    result = pytester.runpytest("-p", "test.ci", *arguments)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
