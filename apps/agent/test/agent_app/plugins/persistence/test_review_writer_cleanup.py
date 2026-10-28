"""Compatibility backend composition must stop its own writer thread."""

from agent_app.plugins.persistence.runtime import PersistenceRuntime


def test_legacy_composition_stop_closes_writer(tmp_path):
    runtime = PersistenceRuntime(tmp_path / "data", tmp_path)
    runtime.start()
    runtime.stop()
    assert not runtime.trace_writer.is_running
