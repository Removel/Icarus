"""Initialization contributions are sampled after restoration, not factory construction."""

import asyncio
import json

from agent_app.application.session_runtime import SessionRuntime
from icarus.core.identity import SessionIdentity
from apps.agent.test.agent_app.application.test_session_runtime import make_config


def extension(root, *, fails=False):
    directory = root / "persona-example"
    package = directory / "persona_example"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "factory.py").write_text('''
from agent_app.contracts.prompt import PromptSection
from icarus.core.plugin import BasePlugin
from icarus.core.registration import PluginRegistration

class Persona(BasePlugin):
    value = "BEFORE-INITIALIZATION"
    async def on_session_start(self):
        FAIL
        self.value = "AFTER-INITIALIZATION"
    def static_prompt_sections(self):
        return (PromptSection("identity", "persona", self.value),)

def create_plugin(plugin_id, **kwargs):
    return PluginRegistration(Persona(plugin_id))
'''.replace("FAIL", 'raise RuntimeError("optional failed")' if fails else "pass"))
    (directory / "manifest.json").write_text(json.dumps({
        "schema_version": 2, "plugin_id": "persona-example", "plugin_version": "1.0.0",
        "entrypoint": "persona_example.factory:create_plugin", "python_requires": [],
        "required_capabilities": [], "provided_capabilities": [], "provided_tools": [],
        "published_events": [], "consumed_events": [], "state_scopes": [],
    }))


def test_static_contributions_use_initialized_values(tmp_path):
    async def run():
        extension(tmp_path / "plugins")
        config = make_config(tmp_path / "data")
        config.runtime.plugin_dirs = [tmp_path / "plugins"]
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, "restored"), config=config,
                                 publish_update=lambda event: asyncio.sleep(0))
        await runtime.start()
        try:
            prompt = runtime.runtime_host.get_plugin("prompt")
            assert "AFTER-INITIALIZATION" in prompt._system_prompt
            assert "BEFORE-INITIALIZATION" not in prompt._system_prompt
        finally:
            await runtime.stop("test")
    asyncio.run(run())


def test_failed_optional_static_contributor_is_not_sampled(tmp_path):
    async def run():
        extension(tmp_path / "plugins", fails=True)
        config = make_config(tmp_path / "data")
        config.runtime.plugin_dirs = [tmp_path / "plugins"]
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, "optional"), config=config,
                                 publish_update=lambda event: asyncio.sleep(0))
        await runtime.start()
        try:
            assert "persona-example" not in runtime.runtime_host.graph.registrations
            assert "BEFORE-INITIALIZATION" not in runtime.runtime_host.get_plugin("prompt")._system_prompt
        finally:
            await runtime.stop("test")
    asyncio.run(run())
