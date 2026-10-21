"""Recording failures must not leak earlier backends or interrupt remaining cleanup."""

from icarus.runtime.recording import RuntimeRecorder


def test_backend_start_failure_closes_already_started_backends():
    events = []
    class First:
        def start(self): events.append("first-start")
        def stop(self, drain=True): events.append("first-stop")
        def offer(self, row): return True
    class Broken(First):
        def start(self): raise RuntimeError("start failed")
        def stop(self, drain=True): events.append("broken-stop")
    recorder = RuntimeRecorder()
    recorder.register_backend(First())
    recorder.register_backend(Broken())
    try:
        recorder.start()
    except RuntimeError:
        pass
    recorder.stop()
    assert "first-stop" in events


def test_backend_stop_failure_does_not_skip_other_backends():
    events = []
    class First:
        def start(self): pass
        def stop(self, drain=True): events.append("first-stop")
        def offer(self, row): return True
    class Broken(First):
        def stop(self, drain=True): raise RuntimeError("stop failed")
    recorder = RuntimeRecorder()
    recorder.register_backend(First()); recorder.register_backend(Broken())
    recorder.start()
    try:
        recorder.stop()
    except RuntimeError:
        pass
    assert events == ["first-stop"]
