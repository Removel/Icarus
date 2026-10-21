import asyncio
from dataclasses import replace
import logging
from pathlib import Path

import pytest
import yaml

from agent_app.plugins.persona.config import PersonaConfig
from agent_app.plugins.persona.store import EvolvedEntry, PersonaFileMissing, PersonaStore
from icarus.core.tools.result_budget import conservative_token_count


BASE = "---\nid: default\nname: Iris\nversion: 1\n---\n## soul\nA curious companion.\n## voice\nWarm and concise.\n## boundaries\nNever lie.\n## anti-goals\nDo not refuse real work.\n## examples\nHello!\n"


def make_store(tmp_path, content=BASE, **settings):
    path = tmp_path / "personas/default/persona.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return PersonaStore(tmp_path, "default", PersonaConfig(**settings))


def entry(number=1, **changes):
    return replace(EvolvedEntry(f"e_{number}", "voice", f"I speak warmly {number}.", "Observed preference.",
                               f"2026-10-20T12:00:{number:02d}+00:00", "session"), **changes)


def test_load_parses_frontmatter_and_sections(tmp_path):
    doc = make_store(tmp_path).load()
    assert doc.name == "Iris"
    assert "A curious companion." in doc.identity_text
    assert "Warm and concise." in doc.drift_reference_text
    assert "Never lie." in doc.invariants_text
    assert "Warm and concise." not in doc.invariants_text


def test_load_ignores_unknown_sections_with_warning(tmp_path, caplog):
    doc = make_store(tmp_path, BASE + "## unknown\nIGNORE-ME\n").load()
    assert "IGNORE-ME" not in doc.identity_text
    assert "unknown" in caplog.text


@pytest.mark.parametrize("prefix", ["", "---\nname: [broken\n---\n"])
def test_bad_or_absent_frontmatter_keeps_valid_body(tmp_path, prefix, caplog):
    doc = make_store(tmp_path, prefix + "## soul\nIntact soul.\n## voice\nKind.\n").load()
    assert "Intact soul." in doc.identity_text
    assert "Kind." in doc.identity_text
    if prefix:
        assert caplog.records


def test_data_dir_wins_over_packaged_fallback(tmp_path):
    doc = make_store(tmp_path).load()
    assert doc.name == "Iris"
    assert "A curious companion." in doc.identity_text


def test_packaged_fallback_still_writes_global_overlay(tmp_path):
    store = PersonaStore(tmp_path, "default", PersonaConfig())
    assert "Icarus" in store.load().identity_text
    asyncio.run(store.append(entry()))
    assert (tmp_path / "personas/default/evolved.yaml").is_file()


def test_missing_everywhere_raises_persona_file_missing(tmp_path):
    store = PersonaStore(tmp_path, "missing", PersonaConfig())
    with pytest.raises(PersonaFileMissing):
        store.load()


def test_soul_over_budget_is_truncated(tmp_path, caplog):
    doc = make_store(tmp_path, "## soul\n" + "我" * 500, max_soul_tokens=40).load()
    assert conservative_token_count(doc.identity_text) <= 1500
    assert doc.identity_text.count("我") <= 40
    assert caplog.records


def test_identity_sections_dropped_in_priority_order(tmp_path):
    doc = make_store(tmp_path, "## soul\nCORE\n## boundaries\nSAFE\n## voice\n" + "V" * 80
                     + "\n## examples\nEXAMPLE\n", max_identity_tokens=65).load()
    assert "CORE" in doc.identity_text and "SAFE" in doc.identity_text
    assert "VVVV" not in doc.identity_text
    assert "EXAMPLE" not in doc.identity_text
    assert conservative_token_count(doc.identity_text) <= 65


def test_overlay_merges_and_only_renders_notes(tmp_path):
    store = make_store(tmp_path)
    async def run():
        await store.append(entry())
        await store.append(entry(2))
    asyncio.run(run())
    payload = yaml.safe_load(store.overlay_path.read_text())
    assert payload["version"] == 1 and payload["updated_at"]
    assert len(payload["entries"]) == 2
    text = store.load().evolved_text
    assert "I speak warmly 1." in text
    assert "Observed preference." not in text
    assert "session" not in text


@pytest.mark.parametrize("settings", [{"evolution_max_entries": 2}, {"evolution_max_tokens": 100}])
def test_append_evicts_oldest_within_budgets(tmp_path, settings):
    store = make_store(tmp_path, **settings)
    async def run():
        for i in range(1, 6):
            await store.append(entry(i))
    asyncio.run(run())
    rows = store.read_entries()
    assert rows[-1].id == "e_5"
    assert "e_1" not in [row.id for row in rows]
    assert len(rows) <= 2 if "evolution_max_entries" in settings else True
    assert conservative_token_count(store.load().evolved_text) <= store.config.evolution_max_tokens


def test_corrupt_overlay_warns_and_recovers(tmp_path, caplog):
    store = make_store(tmp_path)
    store.overlay_path.write_text("entries: [broken")
    assert store.read_entries() == ()
    assert caplog.records
    asyncio.run(store.append(entry()))
    assert len(store.read_entries()) == 1


def test_concurrent_sessions_merge_without_lost_entries(tmp_path):
    first = make_store(tmp_path)
    second = PersonaStore(tmp_path, "default", PersonaConfig())
    async def run():
        await asyncio.gather(first.append(entry()), second.append(entry(2)))
    asyncio.run(run())
    assert [row.id for row in first.read_entries()] == ["e_1", "e_2"]


def test_write_is_atomic_and_cleans_temp_files_on_failure(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    asyncio.run(store.append(entry()))
    original = store.overlay_path.read_bytes()
    def fail_replace(source, target):
        assert yaml.safe_load(Path(source).read_text())["entries"]
        assert Path(target).read_bytes() == original
        raise OSError("disk unavailable")
    monkeypatch.setattr("agent_app.plugins.persona.store.os.replace", fail_replace)
    with pytest.raises(OSError):
        asyncio.run(store.append(entry(2)))
    assert store.overlay_path.read_bytes() == original
    assert sorted(p.name for p in store.overlay_path.parent.iterdir()) == ["evolved.yaml", "persona.md"]


def test_default_caps_preserve_unicode_audit_metadata_on_reload(tmp_path):
    store = make_store(tmp_path)
    async def run():
        for number in range(1, 31):
            await store.append(entry(number, id=f"e_{number:032d}", note=f"温暖问候{number}",
                reason="🕊" * 500, created_at=f"2026-10-20T12:00:{number:02d}.123456+00:00",
                session_id="s" * 32))
    asyncio.run(run())
    restored = PersonaStore(tmp_path, "default", PersonaConfig())
    rows = restored.read_entries()
    assert len(rows) == 30
    assert rows[0].reason == "🕊" * 500
    assert conservative_token_count(restored.load().evolved_text) <= 600


def test_symlink_cannot_escape_persona_root(tmp_path):
    root = tmp_path / "personas"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (root / "default").symlink_to(other, target_is_directory=True)
    store = PersonaStore(tmp_path, "default", PersonaConfig())
    with pytest.raises(ValueError):
        store.load()
