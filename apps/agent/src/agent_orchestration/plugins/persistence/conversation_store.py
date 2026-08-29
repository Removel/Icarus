"""Append-only public conversation history for one Session."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import logging
from pathlib import Path

from apps.agent.src.agent_orchestration.plugins.persistence.path_resolver import (
    DataPathResolver,
)
from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)
from apps.agent.src.runtime_update import RuntimeUpdate


logger = logging.getLogger(__name__)


class ConversationHistoryCorruptError(RuntimeError):
    pass


class ConversationStore:
    """Persist public RuntimeUpdates without depending on internal Events."""

    def __init__(self, resolver: DataPathResolver) -> None:
        self.resolver = resolver
        self._last_sequences: dict[Path, int] = {}

    def append(
        self, identity: SessionIdentity, update: RuntimeUpdate
    ) -> RuntimeUpdate:
        if (update.workspace_key, update.session_id) != (
            identity.workspace_key,
            identity.session_id,
        ):
            raise ValueError("Conversation update identity does not match Session")
        path = self.resolver.conversation_file(identity)
        self.resolver.ensure_session(identity)
        last_sequence = self._last_sequences.get(path)
        if last_sequence is None:
            _, last_sequence = self._read(
                path, identity=identity, repair_tail=True
            )
        recorded = replace(update, sequence=last_sequence + 1)
        payload = {
            "schema_version": 1,
            "workspace_key": recorded.workspace_key,
            "session_id": recorded.session_id,
            "task_id": recorded.task_id,
            "type": recorded.type,
            "payload": dict(recorded.payload),
            "occurred_at": recorded.occurred_at.isoformat(),
            "sequence": recorded.sequence,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            handle.flush()
        try:
            path.chmod(0o600)
        except OSError:
            pass
        self._last_sequences[path] = recorded.sequence
        return recorded

    def read(
        self, identity: SessionIdentity, *, after_sequence: int = 0
    ) -> tuple[tuple[RuntimeUpdate, ...], int]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        path = self.resolver.conversation_file(identity)
        records, cursor = self._read(
            path, identity=identity, repair_tail=True
        )
        self._last_sequences[path] = cursor
        return (
            tuple(
                record
                for record in records
                if record.sequence is not None
                and record.sequence > after_sequence
            ),
            cursor,
        )

    def _read(
        self,
        path: Path,
        *,
        identity: SessionIdentity,
        repair_tail: bool,
    ) -> tuple[tuple[RuntimeUpdate, ...], int]:
        if not path.is_file():
            return (), 0
        data = path.read_bytes()
        records: list[RuntimeUpdate] = []
        offset = 0
        expected_sequence = 1
        lines = data.splitlines(keepends=True)
        for index, raw_line in enumerate(lines):
            line_start = offset
            offset += len(raw_line)
            complete = raw_line.endswith((b"\n", b"\r"))
            try:
                value = json.loads(raw_line.decode("utf-8"))
                record = self._decode(value, expected_sequence)
                if (record.workspace_key, record.session_id) != (
                    identity.workspace_key,
                    identity.session_id,
                ):
                    raise ValueError("conversation history identity mismatch")
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                if index == len(lines) - 1 and not complete:
                    if repair_tail:
                        logger.warning(
                            "Discarding truncated conversation history tail: "
                            "session_id=%s",
                            identity.session_id,
                        )
                        with path.open("r+b") as handle:
                            handle.truncate(line_start)
                    break
                raise ConversationHistoryCorruptError(
                    "Conversation history contains invalid JSON"
                ) from error
            except (KeyError, TypeError, ValueError) as error:
                raise ConversationHistoryCorruptError(
                    "Conversation history contains an invalid record"
                ) from error
            records.append(record)
            expected_sequence += 1
            if index == len(lines) - 1 and not complete and repair_tail:
                with path.open("ab") as handle:
                    handle.write(b"\n")
        return tuple(records), expected_sequence - 1

    @staticmethod
    def _decode(value: object, expected_sequence: int) -> RuntimeUpdate:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("unsupported conversation history schema")
        sequence = value["sequence"]
        if sequence != expected_sequence:
            raise ValueError("conversation history sequence is not contiguous")
        payload = value["payload"]
        if not isinstance(payload, dict):
            raise TypeError("conversation history payload must be an object")
        return RuntimeUpdate(
            workspace_key=str(value["workspace_key"]),
            session_id=str(value["session_id"]),
            task_id=(
                str(value["task_id"])
                if value.get("task_id") is not None
                else None
            ),
            type=str(value["type"]),  # type: ignore[arg-type]
            payload=payload,
            occurred_at=datetime.fromisoformat(str(value["occurred_at"])),
            sequence=sequence,
        )
