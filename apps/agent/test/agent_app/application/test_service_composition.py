"""The public runtime composes responsibility-specific application services."""

from agent_app.application import AgentRuntime


def test_runtime_has_single_service_instances_and_compatible_entrypoint(tmp_path):
    from importlib import import_module
    module = import_module("agent_app.application.controllers.session_api")
    assert AgentRuntime is module.SessionApi
    runtime = AgentRuntime(data_dir=tmp_path)
    assert runtime.sessions is not runtime.submissions
    assert runtime.updates is not runtime.history
    assert runtime.reaper is not runtime.resources
    assert runtime._entries is runtime.sessions.entries
