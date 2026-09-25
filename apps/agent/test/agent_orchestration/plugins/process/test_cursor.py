import pytest

from apps.agent.src.agent_orchestration.plugins.process.cursor import (
    CursorError,
    ProcessCursorCodec,
)


def test_list_cursor_round_trips_and_is_bound_to_session():
    codec = ProcessCursorCodec(b"a" * 32)
    cursor = codec.encode_list("session-a", "2026-09-22T00:00:00Z", "proc_a")

    assert codec.decode_list(cursor, session_id="session-a") == (
        "2026-09-22T00:00:00Z",
        "proc_a",
    )
    with pytest.raises(CursorError):
        codec.decode_list(cursor, session_id="session-b")


def test_log_cursor_round_trips_and_rejects_wrong_binding():
    codec = ProcessCursorCodec(b"a" * 32)
    cursor = codec.encode_log("session", "proc_a", "generation", 42)

    assert codec.decode_log(
        cursor, session_id="session", process_id="proc_a", generation="generation"
    ) == 42
    with pytest.raises(CursorError):
        codec.decode_log(
            cursor, session_id="session", process_id="proc_b", generation="generation"
        )


def test_cursor_rejects_tampering_and_other_plugin_instance():
    codec = ProcessCursorCodec(b"a" * 32)
    cursor = codec.encode_list("session", "now", "proc_a")
    payload, signature = cursor.split(".")

    with pytest.raises(CursorError):
        codec.decode_list(f"{payload[:-1]}A.{signature}", session_id="session")
    with pytest.raises(CursorError):
        ProcessCursorCodec(b"b" * 32).decode_list(cursor, session_id="session")


def test_log_cursor_rejects_negative_offset():
    codec = ProcessCursorCodec(b"a" * 32)
    cursor = codec.encode_log("session", "proc_a", "generation", -1)

    with pytest.raises(CursorError, match="offset"):
        codec.decode_log(
            cursor, session_id="session", process_id="proc_a", generation="generation"
        )
