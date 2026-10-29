import asyncio
from datetime import UTC, datetime
from pathlib import Path

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.tools.replay import ReplayScenario, ReplayTurn, ReplayRuntimeService


def test_visual_preview_fixture_has_a_separate_completed_assistant():
    from apps.tui.src.tools.preview import preview_scenario

    scenario = preview_scenario()
    assert any(u.type == "assistant.message" for u in scenario.turns[0].updates)
    assert any(u.type == "user.correction" for u in scenario.turns[0].updates)
    assert len([u for u in scenario.turns[0].updates if u.type == "tool.started"]) >= 5
