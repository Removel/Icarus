from pathlib import Path

import pytest

from apps.agent.src.application.agent_runtime import AgentRuntime
from apps.agent.src.agent_orchestration.plugins.persistence import SessionIdentity
from apps.agent.test.application.test_agent_runtime import make_config


def test_memory_context_matches_session_identity_without_creating_session(tmp_path):
    config = make_config(tmp_path / 'data')
    config.runtime.plugin_config['memory']['endpoint'] = 'https://private.example'
    runtime = AgentRuntime(config_loader=lambda: config)
    assert runtime.get_memory_context() == {
        'user_id': 'test-user', 'agent_id': 'test-agent', 'run_id': 'global',
    }
    workspace = tmp_path / 'project' / '..' / 'workspace'
    identity = SessionIdentity.create(workspace)
    assert runtime.get_memory_context(workspace) == {
        'user_id': 'test-user', 'agent_id': 'test-agent',
        'run_id': f'workspace:{identity.workspace_key}',
        'workspace_path': str(identity.workspace_path),
    }
    assert not (tmp_path / 'data').exists()
    assert not runtime._entries


@pytest.mark.parametrize('field', ['user_id', 'agent_id'])
def test_memory_context_requires_configured_identity(tmp_path, field):
    config = make_config(tmp_path)
    config.runtime.plugin_config['memory'][field] = ''
    with pytest.raises(ValueError, match='must be configured'):
        AgentRuntime(config_loader=lambda: config).get_memory_context()


def test_memory_context_rejects_relative_workspace(tmp_path):
    runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path))
    with pytest.raises(ValueError, match='absolute'):
        runtime.get_memory_context(Path('relative'))
