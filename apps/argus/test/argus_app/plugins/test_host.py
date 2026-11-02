"""Public Host contract test; flat trusted fixture isolates parent-owned recursion."""
import importlib
import sys
from pathlib import Path

import pytest
from icarus.runtime import PluginRuntimeHost
from argus_app.contracts.capabilities import MechanicalMatchRequest, SemanticJudgeRequest
from argus_app.rule_sdk.engine import RuleEngine
from argus_app.rule_sdk.files import RuleFiles


@pytest.mark.asyncio
async def test_public_host_discovers_all_leafs_and_directed_contracts(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3] / 'src' / 'argus_app' / 'plugins'
    manifests = list(root.rglob('manifest.json'))
    assert len(manifests) == 11
    package = tmp_path / 'argus_fixture_plugins'
    package.mkdir()
    (package / '__init__.py').write_text('')
    for manifest in manifests:
        (package / manifest.parent.name).symlink_to(manifest.parent, target_is_directory=True)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    class Store:
        pass
    store = Store()
    configs = {manifest.parent.name.replace('_', '-'): {'store': store} for manifest in manifests}
    configs['workflow-rules'].update(engine=RuleEngine(), rule_files=RuleFiles(tmp_path / 'rules'))
    configs['icarus-bridge'].update(bridge_client=None, argus_url='http://argus')
    host = PluginRuntimeHost(tmp_path / 'workspace', 'argus-test', builtin_package='argus_fixture_plugins',
        required_plugin_ids=frozenset(configs), plugin_configs=configs)
    try:
        await host.start()
        workflow = host.get_plugin('workflow-rules')
        response = await workflow.request(MechanicalMatchRequest(channel_id='ch', args={'text': 'hello world', 'contains': ['hello']}))
        assert response.unwrap().data == {'match': True}
        response = await workflow.request(SemanticJudgeRequest(channel_id='ch', args={'text': 'hello'}))
        assert response.error_code == 'semantic_unavailable'
        assert all(host.get_plugin(id) is not None for id in configs)
    finally:
        await host.stop()
        sys.modules.pop('argus_fixture_plugins', None)
