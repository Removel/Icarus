"""Signed opaque cursors for process lists and append-only logs."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping


class CursorError(ValueError):
    pass


class ProcessCursorCodec:
    VERSION = 1

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("cursor secret must contain at least 256 bits")
        self._secret = bytes(secret)

    def encode_list(self, session_id: str, started_at: str, process_id: str) -> str:
        return self._encode(
            {"v": self.VERSION, "k": "list", "s": session_id,
             "at": started_at, "p": process_id}
        )

    def decode_list(self, cursor: str, *, session_id: str) -> tuple[str, str]:
        payload = self._decode(cursor)
        self._require_keys(payload, {"v", "k", "s", "at", "p"})
        if payload["k"] != "list" or payload["s"] != session_id:
            raise CursorError("cursor does not belong to this process list")
        started_at = payload["at"]
        process_id = payload["p"]
        if not isinstance(started_at, str) or not isinstance(process_id, str):
            raise CursorError("cursor fields are invalid")
        return started_at, process_id

    def encode_log(
        self, session_id: str, process_id: str, generation: str, offset: int
    ) -> str:
        return self._encode(
            {"v": self.VERSION, "k": "log", "s": session_id,
             "p": process_id, "g": generation, "o": offset}
        )

    def decode_log(
        self, cursor: str, *, session_id: str, process_id: str, generation: str
    ) -> int:
        payload = self._decode(cursor)
        self._require_keys(payload, {"v", "k", "s", "p", "g", "o"})
        if (
            payload["k"] != "log"
            or payload["s"] != session_id
            or payload["p"] != process_id
            or payload["g"] != generation
        ):
            raise CursorError("cursor does not belong to this process log")
        offset = payload["o"]
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise CursorError("cursor offset is invalid")
        return offset

    def _encode(self, payload: Mapping[str, object]) -> str:
        raw = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
        return f"{_b64(raw)}.{_b64(signature)}"

    def _decode(self, cursor: str) -> dict[str, object]:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 4096:
            raise CursorError("cursor is invalid")
        try:
            payload_part, signature_part = cursor.split(".")
            raw = _unb64(payload_part)
            signature = _unb64(signature_part)
        except (ValueError, binascii.Error) as error:
            raise CursorError("cursor is invalid") from error
        expected = hmac.new(self._secret, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise CursorError("cursor signature is invalid")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CursorError("cursor payload is invalid") from error
        if not isinstance(payload, dict) or payload.get("v") != self.VERSION:
            raise CursorError("cursor version is unsupported")
        return payload

    @staticmethod
    def _require_keys(payload: Mapping[str, object], expected: set[str]) -> None:
        if set(payload) != expected:
            raise CursorError("cursor fields are invalid")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    if not value:
        raise ValueError("empty base64 value")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)
