"""Stateless application projection and identity helpers."""
from __future__ import annotations
from datetime import datetime
from hashlib import sha256
import json
from icarus.core.identity import SessionIdentity
from icarus.core.resource_ref import ResourceRef
from icarus.core.runtime_update import RuntimeUpdate

def _key(identity: SessionIdentity) -> tuple[str, str]:
    return identity.workspace_key, identity.session_id


def _coalesce_legacy_assistant_deltas(
    records: tuple[RuntimeUpdate, ...],
) -> tuple[RuntimeUpdate, ...]:
    """Project legacy persisted stream chunks as complete assistant messages."""

    complete_keys = {
        (record.task_id, record.payload.get("step"))
        for record in records
        if record.type == "assistant.message"
    }
    compacted: list[RuntimeUpdate] = []
    index = 0
    while index < len(records):
        record = records[index]
        if record.type != "assistant.text_delta":
            compacted.append(record)
            index += 1
            continue

        key = (record.task_id, record.payload.get("step"))
        chunks: list[str] = []
        last = record
        while index < len(records):
            candidate = records[index]
            candidate_key = (
                candidate.task_id,
                candidate.payload.get("step"),
            )
            if (
                candidate.type != "assistant.text_delta"
                or candidate_key != key
            ):
                break
            text = candidate.payload.get("text")
            if isinstance(text, str):
                chunks.append(text)
            last = candidate
            index += 1

        if key not in complete_keys and chunks:
            compacted.append(
                RuntimeUpdate(
                    workspace_key=last.workspace_key,
                    session_id=last.session_id,
                    task_id=last.task_id,
                    type="assistant.message",
                    payload={
                        "step": last.payload.get("step"),
                        "text": "".join(chunks),
                    },
                    occurred_at=last.occurred_at,
                    sequence=last.sequence,
                )
            )
    return tuple(compacted)


def _submission_fingerprint(
    prompt: str,
    resources: tuple[ResourceRef, ...],
    display_text: str | None,
) -> str:
    payload = {
        "prompt": prompt,
        "display_text": display_text,
        "resources": [
            {
                "resource_id": item.resource_id,
                "media_type": item.media_type,
            }
            for item in resources
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _steer_fingerprint(
    task_id: str,
    content: str,
    resources: tuple[ResourceRef, ...],
    display_text: str | None,
) -> str:
    payload = {
        "task_id": task_id,
        "content": content,
        "display_text": display_text,
        "resources": [
            {
                "resource_id": item.resource_id,
                "media_type": item.media_type,
            }
            for item in resources
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _max_datetime(*values: datetime | None) -> datetime | None:
    present = tuple(value for value in values if value is not None)
    return max(present) if present else None


def _safe_error(error: BaseException) -> str:
    return type(error).__name__

