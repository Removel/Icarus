import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from icarus.core.hooks.hook_event import HookEvent
from agent_app.plugins.persistence import (
    DataPathResolver,
    FileTraceWriter,
    Redactor,
    SessionIdentity,
    TraceRecord,
    TraceWriteRequest,
)


def make_event(session_id="session-1", value=1):
    return HookEvent.create(
        name="custom.event",
        phase="after",
        run_id="run-1",
        context={
            "workspace_path": "/workspace",
            "workspace_key": "workspace-key",
            "session_id": session_id,
            "task_id": "task-1",
        },
        data={"value": value},
    )


def test_trace_writer_多线程入队并按session写入(tmp_path):
    resolver = DataPathResolver(tmp_path)
    writer = FileTraceWriter(resolver, flush_every=2)
    redactor = Redactor()
    first_identity = SessionIdentity(
        workspace_path=tmp_path / "workspace",
        workspace_key="workspace-key",
        session_id="session-1",
    )
    second_identity = SessionIdentity(
        workspace_path=tmp_path / "workspace",
        workspace_key="workspace-key",
        session_id="session-2",
    )
    writer.start()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = []
        for value in range(10):
            identity = first_identity if value % 2 == 0 else second_identity
            event = make_event(identity.session_id, value)
            record = TraceRecord.from_hook_event(event, redactor)
            futures.append(
                executor.submit(
                    writer.offer,
                    TraceWriteRequest(identity, record),
                )
            )
        assert all(future.result() for future in futures)
    writer.stop(drain=True)

    first_lines = resolver.trace_file(first_identity).read_text(
        encoding="utf-8"
    ).splitlines()
    second_lines = resolver.trace_file(second_identity).read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(first_lines) == 5
    assert len(second_lines) == 5
    assert writer.written_count == 10
    assert writer.pending_count == 0


def test_recording_backend_入队并跳过缺失身份(tmp_path):
    from icarus.core.recording import RuntimeRecord
    from agent_app.plugins.persistence.recording import FileRecordingBackend
    resolver = DataPathResolver(tmp_path)
    writer = FileTraceWriter(resolver)
    backend = FileRecordingBackend(writer, Redactor())
    backend.start()
    for value in (1, 2):
        event = make_event(value=value)
        assert backend.offer(RuntimeRecord(event.event_id, event.occurred_at, event.run_id,
            event.name, event.phase, event.context, event.data))
    missing = make_event()
    assert backend.offer(RuntimeRecord(missing.event_id, missing.occurred_at, None,
        missing.name, missing.phase, {}, {})) is False
    backend.stop(drain=True)
    identity = SessionIdentity(workspace_path=tmp_path / "workspace", workspace_key="workspace-key", session_id="session-1")
    lines = resolver.trace_file(identity).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["data"]["value"] for line in lines] == [1, 2]
    assert all(json.loads(line)["record_type"] == "runtime_record" for line in lines)
