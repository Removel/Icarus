import asyncio
from importlib.resources import files
import json
import logging
from pathlib import Path

from agent_app.config.config_model import RuntimeSettings
from agent_app.plugins.persona.factory import create_plugin
from icarus.core.contracts.input import UserInputEvent
from icarus.core.agents import AgentCompletedEvent
from icarus.core.manifest import load_manifest
from icarus.runtime.discovery import PluginManifestDiscovery


class Config:
    def __init__(self, path):
        self.icarus_data_dir = path


def make_plugin(tmp_path, settings=None):
    return create_plugin("persona", tmp_path, "session", {
        "config_model": Config(tmp_path / "data"), "_user_config": settings or {},
    }, {}, logging.getLogger("test.persona")).plugin


def test_factory_constructs_without_network_and_defers_file_load(tmp_path):
    plugin = make_plugin(tmp_path, {"persona_id": "missing"})
    assert plugin.plugin_id == "persona"
    assert plugin.static_prompt_sections() == ()


def test_minimal_input_handler_publishes_empty_completed_contribution(tmp_path):
    async def run():
        plugin = make_plugin(tmp_path)
        published = []

        async def publish(event):
            published.append(event)
        plugin.bind_publisher(publish)
        await plugin.dispatch("user-input", UserInputEvent(task_id="t", prompt="hello"))
        assert len(published) == 1
        assert published[0].status == "completed"
        assert published[0].sections == ()
        assert published[0].task_id == "t"
    asyncio.run(run())


def test_manifest_tools_match_factory_and_event_bindings(tmp_path):
    manifest, _ = load_manifest(Path(str(files("agent_app.plugins").joinpath("persona/manifest.json"))))
    assert manifest.tier == "core"
    assert manifest.published_events == ("prompt.contribution",)
    assert manifest.state_scopes == ()
    plugin = make_plugin(tmp_path)
    plugin.validate_manifest(manifest, (UserInputEvent, AgentCompletedEvent))
    assert manifest.provided_tools == tuple(t.definition.name for t in plugin.build_tools())


def test_default_required_plugins_include_persona():
    assert "persona" in RuntimeSettings().required_plugin_ids
    for path in (files("agent_app.resources").joinpath("default_settings.json"),
                 Path(__file__).resolve().parents[4] / "settings.json"):
        config = json.loads(path.read_text())
        assert "persona" in config["runtime"]["required_plugin_ids"]
        assert "persona" in config["runtime"]["plugin_config"]["prompt"]["required_contributors"]


def test_discovery_keeps_persona_lazy():
    result = PluginManifestDiscovery(builtin_package="agent_app.plugins").discover()
    assert "persona" in {item.manifest.plugin_id for item in result.plugins}
