import asyncio
from dataclasses import asdict

import pytest
import yaml

from agent_app.contracts.persona import PersonaSnapshot
from agent_app.plugins.persona.config import PersonaConfig
from agent_app.plugins.persona.store import EvolvedEntry, PersonaStore
from icarus.core.identity import SessionIdentity


def test_empty_consolidation_advances_source_and_survives_reload(tmp_path):
    store = PersonaStore(tmp_path, "default", PersonaConfig())
    assert hasattr(store, "commit_consolidation"), "missing atomic evolution and progress commit"
    snapshot = PersonaSnapshot(SessionIdentity.create(tmp_path, "s"), "default", 0, 3, ())
    result = asyncio.run(store.commit_consolidation(snapshot, (), expected_version=store.version()))
    assert result.status == "no_change"
    restored = PersonaStore(tmp_path, "default", PersonaConfig())
    assert restored.processed_through(snapshot.identity) == 3
    original = restored.overlay_path.read_bytes()
    duplicate = asyncio.run(restored.commit_consolidation(snapshot, (), expected_version="stale"))
    assert duplicate.already_processed
    assert restored.overlay_path.read_bytes() == original


def entry(note="I keep answers concise."):
    return EvolvedEntry("e_old", "voice", note, "Explicit lasting preference", "2026-11-08T00:00:00Z", "s")


def test_v1_upgrade_preserves_entries_and_progress_is_scoped_by_workspace(tmp_path):
    store = PersonaStore(tmp_path, "default", PersonaConfig())
    store.directory.mkdir(parents=True)
    store.overlay_path.write_text(yaml.safe_dump({"version": 1, "entries": [asdict(entry())]}), encoding="utf-8")
    identity = SessionIdentity.create(tmp_path / "first", "s")
    snapshot = PersonaSnapshot(identity, "default", 0, 3, ())
    asyncio.run(store.commit_consolidation(snapshot, (), expected_version=store.version()))
    assert store.read_entries() == (entry(),)
    assert yaml.safe_load(store.overlay_path.read_text(encoding="utf-8"))["version"] == 2
    assert store.processed_through(identity) == 3
    assert store.processed_through(SessionIdentity.create(tmp_path / "second", "s")) == 0
    continued = PersonaSnapshot(identity, "default", 3, 6, ())
    asyncio.run(store.commit_consolidation(continued, (), expected_version=store.version()))
    assert store.processed_through(identity) == 6


def test_changed_persona_or_stale_cursor_cannot_commit(tmp_path):
    store = PersonaStore(tmp_path, "default", PersonaConfig())
    identity = SessionIdentity.create(tmp_path, "s")
    snapshot = PersonaSnapshot(identity, "default", 0, 3, ())
    version = store.version()
    store.directory.mkdir(parents=True)
    (store.directory / "persona.md").write_text("## soul\nChanged manually", encoding="utf-8")
    with pytest.raises(ValueError, match="persona_changed"):
        asyncio.run(store.commit_consolidation(snapshot, (entry(),), expected_version=version))
    assert not store.overlay_path.exists()
    stale = PersonaSnapshot(identity, "default", 1, 3, ())
    with pytest.raises(ValueError, match="persona_changed"):
        asyncio.run(store.commit_consolidation(stale, (), expected_version=store.version()))


def test_capacity_keeps_old_entries_and_advances_progress(tmp_path):
    store = PersonaStore(tmp_path, "default", PersonaConfig(evolution_max_entries=1))
    asyncio.run(store.append(entry()))
    identity = SessionIdentity.create(tmp_path, "s")
    snapshot = PersonaSnapshot(identity, "default", 0, 3, ())
    result = asyncio.run(store.commit_consolidation(snapshot, (entry("I use warm language."),), expected_version=store.version()))
    assert result.status == "no_change" and result.skipped_count == 1
    assert store.read_entries() == (entry(),)
    assert store.processed_through(identity) == 3


def test_failed_atomic_replace_keeps_both_entries_and_cursor(tmp_path, monkeypatch):
    store = PersonaStore(tmp_path, "default", PersonaConfig())
    identity = SessionIdentity.create(tmp_path, "s")
    initial = PersonaSnapshot(identity, "default", 0, 3, ())
    asyncio.run(store.commit_consolidation(initial, (), expected_version=store.version()))
    original = store.overlay_path.read_bytes()
    def fail(*args):
        raise OSError("disk failure")
    monkeypatch.setattr("agent_app.plugins.persona.store.os.replace", fail)
    continued = PersonaSnapshot(identity, "default", 3, 6, ())
    with pytest.raises(OSError):
        asyncio.run(store.commit_consolidation(continued, (entry(),), expected_version=store.version()))
    assert store.overlay_path.read_bytes() == original
    assert store.processed_through(identity) == 3
    assert not list(store.directory.glob(".evolved-*"))


def test_v2_without_progress_is_not_silently_overwritten(tmp_path):
    store = PersonaStore(tmp_path, "default", PersonaConfig())
    store.directory.mkdir(parents=True)
    original = b"version: 2\nentries: []\n"
    store.overlay_path.write_bytes(original)
    identity = SessionIdentity.create(tmp_path, "s")
    with pytest.raises(ValueError):
        asyncio.run(store.commit_consolidation(PersonaSnapshot(identity, "default", 0, 3, ()), (), expected_version=store.version()))
    assert store.overlay_path.read_bytes() == original
