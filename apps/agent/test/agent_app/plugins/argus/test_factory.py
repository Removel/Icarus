"""Factory activation is explicit: no context never exposes background tools."""

import asyncio
import logging

from icarus.core.identity import SessionIdentity
from icarus.core.session import SessionContext


def test_argus_factory_rejects_interactive_and_requires_bound_background(tmp_path):
    from agent_app.plugins.argus.factory import create_plugin
    import pytest

    with pytest.raises(ValueError, match='not active'):
        create_plugin('argus', tmp_path, 'session', {'session_context': SessionContext()}, {}, logging.getLogger('test'))
    with pytest.raises(ValueError, match='binding'):
        create_plugin('argus', tmp_path, 'session', {
            'session_context': SessionContext(kind='argus_background'),
        }, {}, logging.getLogger('test'))


def test_interactive_runtime_has_no_argus_tools_or_active_plugin(tmp_path):
    from agent_app.application.session_runtime import SessionRuntime
    from apps.agent.test.agent_app.application.test_session_runtime import make_config

    async def run():
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, 'interactive-argus'),
            config=make_config(tmp_path / 'data'), publish_update=lambda _: asyncio.sleep(0))
        await runtime.start()
        try:
            assert not any(name.startswith('argus_') for name in runtime.tool_registry.names())
            assert 'argus' not in runtime.runtime_host.graph.registrations
        finally:
            await runtime.stop('test')

    asyncio.run(run())
