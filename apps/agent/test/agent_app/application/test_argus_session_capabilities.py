"""Argus is an execution origin, not a reduced tool permission profile."""
import asyncio
from types import SimpleNamespace

from agent_app.application.controllers.session_api import SessionApi
from agent_app.application.session_runtime import SessionRuntime
from agent_app.config import ConfigModel, LLMConfig, ModelSettings, ThinkMode
from icarus.core.identity import SessionIdentity


def config(path):
    model = LLMConfig(model_name='synthetic', context_window=128000, max_tokens=1024,
                      temperature=0, default_think_level=ThinkMode.LOW)
    return ConfigModel(openai_base_url='https://example.invalid', anthropic_base_url='https://example.invalid',
        icarus_data_dir=path, model_settings=ModelSettings(thinking=model, perception=model),
        runtime={'plugin_config': {'memory': {'user_id': 'test', 'agent_id': 'test'},
                                  'knowledge': {'knowledge_base': 'test'}}})


def test_argus_does_not_replace_explicit_tool_policy(tmp_path):
    calls = []
    api = SessionApi(session_factory=lambda identity, **kwargs: calls.append(kwargs), data_dir=tmp_path)
    identity = SessionIdentity.create(tmp_path, 'argus-synthetic')
    api.configure_bridge_session(identity.session_id, {'bridge': object()})
    api._configured_session_factory(identity, config=config(tmp_path / 'data'), tools=['read', 'mcp_tool_execute'])
    assert set(calls[0]['tools']) == {'read', 'mcp_tool_execute'}


def test_argus_session_keeps_normal_capabilities_plus_argus_tools(tmp_path):
    async def run():
        identity = SessionIdentity.create(tmp_path, 'argus-synthetic')
        api = SessionApi(data_dir=tmp_path)
        api.configure_bridge_session(identity.session_id, {'bridge': SimpleNamespace(bindings={})})
        runtime = api._configured_session_factory(identity, config=config(tmp_path / 'data'),
            publish_update=lambda update: asyncio.sleep(0))
        await runtime.start()
        try:
            names = set(runtime.tool_registry.names())
            assert {'mcp_tool_execute', 'mcp_tool_search', 'mcp_tool_list', 'read', 'argus_commit'} <= names
        finally:
            await runtime.stop('test')
    asyncio.run(run())
