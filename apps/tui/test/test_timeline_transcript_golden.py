import os
from pathlib import Path

from apps.tui.src.replay import load_replay
from apps.tui.src.transcript import transcript_from_scenario


TEST_DIR = Path(__file__).parent
FIXTURE = TEST_DIR / "fixtures" / "synthetic_tui_events.jsonl"
GOLDEN = TEST_DIR / "golden" / "synthetic_tui_transcript.txt"


def test_synthetic_tui_transcript_matches_golden():
    actual = transcript_from_scenario(load_replay(FIXTURE))

    if os.environ.get("UPDATE_GOLDENS") == "1":
        GOLDEN.write_text(actual, encoding="utf-8")

    assert actual == GOLDEN.read_text(encoding="utf-8")
    assert "这段不应显示" not in actual
    assert "secret" not in actual.lower()
