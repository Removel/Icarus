import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from apps.agent.src.agent_orchestration.hooks.hook_event import HookEvent
from apps.agent.src.agent_orchestration.plugins.persistence import (
    DataPathResolver,
    FileTraceHook,
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


def test_trace_hook_同步异步入队并跳过缺失身份(tmp_path):
    resolver = DataPathResolver(tmp_path)
    writer = FileTraceWriter(resolver)
    hook = FileTraceHook(writer, Redactor())
    writer.start()

    hook.handle(make_event(value=1))
    asyncio.run(hook.ahandle(make_event(value=2)))
    missing = HookEvent.create(
        "custom.event",
        "after",
        run_id=None,
        data={},
    )
    hook.handle(missing)
    writer.stop(drain=True)

    identity = SessionIdentity(
        workspace_path=tmp_path / "workspace",
        workspace_key="workspace-key",
        session_id="session-1",
    )
    lines = resolver.trace_file(identity).read_text(
        encoding="utf-8"
    ).splitlines()
    assert [json.loads(line)["data"]["value"] for line in lines] == [1, 2]
    assert hook.skipped_count == 1
