"""A skipped, cancelled, or missing required task must never produce a green gate."""

import importlib.util
from pathlib import Path

import pytest


def gate_module():
    path = Path(__file__).resolve().parents[2] / "scripts/ci/check_results.py"
    assert path.is_file(), "CI must explicitly validate expected job results"
    spec = importlib.util.spec_from_file_location("ci_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gate_accepts_success_and_explicitly_unselected_skip():
    assert gate_module().failures(["syntax"], {
        "changes": {"result": "success"}, "syntax": {"result": "success"},
        "tui": {"result": "skipped"},
    }) == []


@pytest.mark.parametrize("state", ["failure", "cancelled", "skipped", None])
def test_gate_rejects_every_required_non_success(state):
    results = {"changes": {"result": "success"}, "syntax": {"result": "success"}}
    if state is not None:
        results["tui"] = {"result": state}
    assert gate_module().failures(["syntax", "tui"], results)


def test_gate_rejects_failed_detector_even_when_downstream_is_skipped():
    assert gate_module().failures([], {"changes": {"result": "failure"}})


def test_gate_rejects_empty_or_unknown_expected_task_sets():
    gate = gate_module()
    assert gate.failures([], {"changes": {"result": "success"}})
    assert gate.failures(["syntax", "unknown"], {"changes": {"result": "success"}, "syntax": {"result": "success"}})


def test_gate_does_not_hide_failure_in_an_unselected_task():
    assert gate_module().failures(["syntax"], {
        "changes": {"result": "success"}, "syntax": {"result": "success"},
        "tui": {"result": "failure"},
    })
