"""Bounded persona files with atomic evolution and source progress commits."""

import asyncio
from dataclasses import asdict, dataclass
from hashlib import sha256
from datetime import UTC, datetime
from importlib.resources import files
from importlib.resources.abc import Traversable
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

import yaml

from agent_app.plugins.persona.config import PersonaConfig
from agent_app.contracts.persona import PersonaConsolidationResult, PersonaSnapshot, persona_source
from icarus.core.identity import SessionIdentity
from icarus.core.tools.result_budget import conservative_token_count

logger = logging.getLogger(__name__)
_WRITE_LOCKS: dict[Path, asyncio.Lock] = {}
_SECTIONS = ("soul", "boundaries", "anti-goals", "voice", "examples")
_EVOLVED_HEADER = "Style only; defer to identity, boundaries and current user requests.\n"
_MAX_FILE_BYTES = 65536
_MAX_OVERLAY_BYTES = 1024 * 1024


class PersonaFileMissing(FileNotFoundError):
    pass


@dataclass(frozen=True)
class PersonaDocument:
    persona_id: str
    name: str
    identity_text: str
    drift_reference_text: str
    invariants_text: str
    evolved_text: str


@dataclass(frozen=True)
class EvolvedEntry:
    id: str
    section: Literal["voice", "relationship"]
    note: str
    reason: str
    created_at: str
    session_id: str
    evidence_ids: tuple[str, ...] = ()
    workspace_key: str = ""


def clip_text(text: str, budget: int) -> str:
    if conservative_token_count(text) <= budget:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if conservative_token_count(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def render_entries(entries) -> str:
    if not entries:
        return ""
    return _EVOLVED_HEADER + "\n".join(f"[{entry.section}] {entry.note}" for entry in entries)


class PersonaStore:
    def __init__(self, data_dir: Path, persona_id: str, config: PersonaConfig, package_root=None):
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", persona_id):
            raise ValueError("Invalid persona ID")
        self.root = Path(data_dir).resolve() / "personas"
        self.directory = self.root / persona_id
        self.overlay_path = self.directory / "evolved.yaml"
        self.persona_id = persona_id
        self.config = config
        self.package_root = package_root or files("agent_app.resources").joinpath("personas")

    def _check_path(self, path):
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Persona path escapes data root")

    def _read(self, path, *, max_bytes=_MAX_FILE_BYTES) -> str:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            logger.warning("Persona file exceeds byte limit; reading bounded prefix")
            raw = raw[:max_bytes]
        return raw.decode("utf-8", errors="replace").replace("\r\n", "\n")

    def _persona_source(self) -> Traversable:
        source: Traversable = self.directory / "persona.md"
        self._check_path(self.directory / "persona.md")
        if not source.is_file():
            source = self.package_root.joinpath(self.persona_id, "persona.md")
        if not source.is_file():
            raise PersonaFileMissing(f"Persona file not found: {self.persona_id}")
        return source

    def persona_text(self) -> str:
        source = self._persona_source()
        with source.open("rb") as stream:
            raw = stream.read(_MAX_FILE_BYTES + 1)
        if len(raw) > _MAX_FILE_BYTES:
            raise ValueError("input_too_large")
        return raw.decode("utf-8")

    def version(self) -> str:
        self._check_path(self.overlay_path)
        raw = b""
        if self.overlay_path.exists():
            with self.overlay_path.open("rb") as stream:
                raw = stream.read(_MAX_OVERLAY_BYTES + 1)
        if len(raw) > _MAX_OVERLAY_BYTES:
            raise ValueError("overlay_too_large")
        return sha256(self.persona_text().encode() + b"\x00" + raw).hexdigest()

    def load(self) -> PersonaDocument:
        source = self._persona_source()
        text = self._read(source)
        metadata: dict = {}
        if text.startswith("---\n"):
            boundary = re.search(r"(?m)^---\s*$", text[4:])
            if boundary:
                end = 4 + boundary.start()
                try:
                    metadata = yaml.safe_load(text[4:end]) or {}
                    if not isinstance(metadata, dict):
                        raise ValueError("Invalid metadata")
                except (yaml.YAMLError, ValueError):
                    metadata = {}
                    logger.warning("Persona frontmatter invalid; keeping body sections")
                text = text[4 + boundary.end():]
            else:
                logger.warning("Persona frontmatter incomplete; keeping body sections")
        if metadata.get("id", self.persona_id) != self.persona_id:
            logger.warning("Persona metadata ID differs from directory; using directory")
        sections = dict.fromkeys(_SECTIONS, "")
        parts = re.split(r"(?m)^##\s+([^\n]+)\s*$", text)
        for index in range(1, len(parts), 2):
            key, content = parts[index].strip(), parts[index + 1].strip()
            if key not in sections:
                logger.warning("Unknown persona section: %s", key[:64])
                continue
            sections[key] = "\n".join(filter(None, (sections[key], content)))
        if not sections["soul"]:
            logger.warning("Persona soul section is missing")
        missing_invariants = [key for key in _SECTIONS[:3] if not sections[key]]
        if missing_invariants:
            logger.warning("Persona invariant sections missing: persona=%s sections=%s; evolution input lacks these sections",
                           self.persona_id, ",".join(missing_invariants))
        if conservative_token_count(sections["soul"]) > self.config.max_soul_tokens:
            sections["soul"] = clip_text(sections["soul"], self.config.max_soul_tokens)
            logger.warning("Persona soul exceeded budget; truncated")
        selected: list[str] = []
        for key in _SECTIONS:
            if not sections[key]:
                continue
            proposed = [*selected, f"### {key}\n{sections[key]}"]
            if conservative_token_count("\n\n".join(proposed)) <= self.config.max_identity_tokens:
                selected = proposed
            else:
                logger.warning("Persona section omitted for budget: %s", key)
                break
        name = metadata.get("name", self.persona_id)
        return PersonaDocument(self.persona_id, name if isinstance(name, str) else self.persona_id,
            "\n\n".join(selected), clip_text(sections["soul"] + "\n" + sections["voice"], 600),
            clip_text("\n".join(sections[key] for key in _SECTIONS[:3]), 800),
            render_entries(self._bound(self.read_entries())))

    def _read_overlay(self) -> dict:
        self._check_path(self.overlay_path)
        if not self.overlay_path.exists():
            return {"version": 2, "entries": [], "sources": {}}
        with self.overlay_path.open("rb") as stream:
            raw = stream.read(_MAX_OVERLAY_BYTES + 1)
        if len(raw) > _MAX_OVERLAY_BYTES:
            raise ValueError("overlay_too_large")
        payload = yaml.safe_load(raw.decode("utf-8"))
        if (not isinstance(payload, dict) or payload.get("version") not in (1, 2)
                or not isinstance(payload.get("entries"), list)):
            raise ValueError("Invalid overlay")
        for row in payload["entries"]:
            entry = EvolvedEntry(**row)
            if (entry.section not in {"voice", "relationship"}
                    or any(not isinstance(v, str) or not v.strip() for v in
                           (entry.id, entry.note, entry.reason, entry.created_at, entry.session_id))
                    or len(entry.note) > self.config.evolution_max_note_chars
                    or len(entry.reason) > self.config.evolution_max_reason_chars
                    or not isinstance(entry.workspace_key, str)
                    or not isinstance(entry.evidence_ids, (tuple, list))
                    or any(not isinstance(v, str) or not v for v in entry.evidence_ids)):
                raise ValueError("Invalid entry")
            datetime.fromisoformat(entry.created_at)
        if payload["version"] == 1:
            payload.setdefault("sources", {})
        sources = payload.get("sources")
        if not isinstance(sources, dict):
            raise ValueError("Invalid progress")
        for key, receipt in sources.items():
            if not isinstance(key, str) or not isinstance(receipt, dict):
                raise ValueError("Invalid progress")
            for field in ("start_sequence", "processed_through", "accepted_count", "skipped_count"):
                value = receipt.get(field)
                if type(value) is not int or value < 0:
                    raise ValueError("Invalid progress")
            if (receipt["processed_through"] < receipt["start_sequence"]
                    or receipt.get("status") not in {"applied", "no_change"}
                    or not isinstance(receipt.get("cycle_id"), str)):
                raise ValueError("Invalid progress")
        return payload

    def read_entries(self, *, strict=False) -> tuple[EvolvedEntry, ...]:
        try:
            return tuple(EvolvedEntry(**{**row, "evidence_ids": tuple(row.get("evidence_ids", ()))})
                         for row in self._read_overlay()["entries"])
        except (yaml.YAMLError, TypeError, ValueError, OSError):
            if strict:
                raise ValueError("overlay_invalid") from None
            logger.warning("Persona overlay invalid; using empty overlay")
            return ()

    def processed_through(self, identity: SessionIdentity) -> int:
        return self._read_overlay()["sources"].get(persona_source(identity), {}).get("processed_through", 0)

    def _bound(self, entries):
        entries = sorted(entries, key=lambda item: item.created_at)
        while entries and (len(entries) > self.config.evolution_max_entries
                           or conservative_token_count(render_entries(entries)) > self.config.evolution_max_tokens):
            entries.pop(0)
        return tuple(entries)

    async def append(self, entry: EvolvedEntry) -> str:
        self._check_path(self.overlay_path)
        key = self.overlay_path.resolve()
        lock = _WRITE_LOCKS.setdefault(key, asyncio.Lock())
        async with lock:
            payload = self._read_overlay()
            entries = self._bound((*self.read_entries(strict=True), entry))
            if not any(item.id == entry.id for item in entries):
                raise ValueError("Evolution note exceeds overlay budget")
            payload.update(version=2, entries=[asdict(item) for item in entries])
            self._write_overlay(payload)
            return entry.id

    async def commit_consolidation(self, snapshot: PersonaSnapshot, entries: tuple[EvolvedEntry, ...],
                                   *, expected_version: str, skipped_count: int = 0) -> PersonaConsolidationResult:
        if snapshot.persona_id != self.persona_id:
            raise ValueError("persona_mismatch")
        self._check_path(self.overlay_path)
        lock = _WRITE_LOCKS.setdefault(self.overlay_path.resolve(), asyncio.Lock())
        async with lock:
            payload = self._read_overlay()
            key = persona_source(snapshot.identity)
            previous = payload["sources"].get(key)
            processed = previous["processed_through"] if previous else 0
            if previous and processed >= snapshot.end_sequence:
                return PersonaConsolidationResult(previous["cycle_id"], previous["status"],
                    previous["accepted_count"], previous["skipped_count"], already_processed=True)
            if processed != snapshot.start_sequence or self.version() != expected_version:
                raise ValueError("persona_changed")
            merged = list(self.read_entries(strict=True))
            accepted = 0
            for entry in entries:
                proposed = [*merged, entry]
                if (len(proposed) > self.config.evolution_max_entries
                        or conservative_token_count(render_entries(proposed)) > self.config.evolution_max_tokens):
                    skipped_count += 1
                    continue
                merged = proposed
                accepted += 1
            result = PersonaConsolidationResult(snapshot.cycle_id,
                "applied" if accepted else "no_change", accepted, skipped_count)
            payload.update(version=2, entries=[asdict(item) for item in merged])
            payload["sources"][key] = {**asdict(result), "start_sequence": snapshot.start_sequence,
                "processed_through": snapshot.end_sequence, "processed_at": datetime.now(UTC).isoformat()}
            self._write_overlay(payload)
            return result

    def _write_overlay(self, payload: dict) -> None:
        payload["updated_at"] = datetime.now(UTC).isoformat()
        encoded = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        if len(encoded.encode("utf-8")) > _MAX_OVERLAY_BYTES:
            raise ValueError("Evolution audit file exceeds byte limit")
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".evolved-", delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
            os.replace(temporary, self.overlay_path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
