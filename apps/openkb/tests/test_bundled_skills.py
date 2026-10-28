from pathlib import Path

import openkb.agent.skills as skill_scan


def test_real_bundled_decks_remain_readable_without_user_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_scan, "DEFAULT_SKILL_ROOTS", ())
    entries = {entry["name"]: entry for entry in skill_scan.scan_local_skills(tmp_path)}
    for name in ("openkb-deck-neon", "openkb-deck-editorial", "openkb-html-critic"):
        body = (Path(entries[name]["path"]) / "SKILL.md").read_text(encoding="utf-8")
        assert body.strip()
        assert entries[name]["description"]
