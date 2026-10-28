"""Runtime recording is independent of Hook registration and business events."""

import importlib


def test_recording_aggregates_counts_and_flushes_on_stop():
    api = importlib.import_module("icarus.runtime.recording")
    rows = []
    class Backend:
        def start(self): pass
        def offer(self, record):
            rows.append(record)
            return True
        def drain(self): pass
        def stop(self, drain=True): pass
    recorder = api.RuntimeRecorder()
    recorder.register_backend(Backend())
    recorder.start()
    for _ in range(7):
        recorder.record("event.route", "after", data={"plugin_id": "p", "event_type": "input"})
    recorder.stop()
    assert sum(row.data["count"] for row in rows) == 7
    assert all(row.name == "event.route" for row in rows)
