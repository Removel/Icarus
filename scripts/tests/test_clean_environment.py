"""The checked-in topology must not depend on machine-local generated files."""

from pathlib import Path
import shutil
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'icarus'))
from configuration import load_configuration
from injection import build_child_environment

ROOT = Path(__file__).resolve().parents[2]


def test_clean_topology_shares_postgres_without_ignored_overlay(tmp_path):
    shutil.copy2(ROOT / 'icarus.toml', tmp_path / 'icarus.toml')
    registry = load_configuration(tmp_path)
    assert registry.data_dir == '~/.icarus'
    for name, database, user in [('mem0', 'mem0_app', 'mem0_local'),
                                 ('rela-mem', 'rela_mem', 'rela_mem'), ('argus', 'argus', 'argus')]:
        app = registry.get(name)
        edge = app.requires['postgres']
        assert (edge.connection.database, edge.connection.user) == (database, user)
        assert '.icarus-data' not in str(app)
    assert registry.get('postgres').data_dir == 'services/postgres'
    assert registry.get('falkordb').data_dir == 'services/falkordb'
    assert registry.get('postgres').env['ICARUS_POSTGRES_USER'] != 'rela_mem'


def test_compose_network_follows_declared_topology(tmp_path):
    from compose_runtime import ComposeRuntime
    shutil.copytree(ROOT / 'infra', tmp_path / 'infra')
    shutil.copy2(ROOT / 'icarus.toml', tmp_path / 'icarus.toml')
    registry = load_configuration(tmp_path, overrides={'postgres': {'network': 'isolated-test'}})
    runtime = ComposeRuntime(tmp_path, tmp_path / 'data', {}, registry)
    assert runtime._environment(registry.get('postgres'))['ICARUS_NETWORK'] == 'isolated-test'


def test_development_images_do_not_overwrite_published_latest():
    for app, path in [('mem0', 'apps/mem0/server/docker-compose.yaml'),
                      ('openkb', 'apps/openkb/docker-compose.yaml'),
                      ('rela-mem', 'apps/rela-mem/docker-compose.yaml')]:
        doc = yaml.safe_load((ROOT / path).read_text())
        service = doc['services'][app]
        assert service['image'] == '${ICARUS_' + app.upper().replace('-', '_') + '_IMAGE:-icarus-' + app + ':dev}'
        assert not service['build'].get('tags')


def test_db_init_is_neutral_and_configures_all_application_extensions():
    script = ROOT / 'infra/postgres/init/db-init.sh'
    assert script.is_file()
    text = script.read_text()
    assert 'CREATE EXTENSION IF NOT EXISTS vector' in text
    assert 'NOCREATEDB' in text and 'NOSUPERUSER' in text
    assert not (ROOT / 'infra/postgres/init/10-extra-databases.sh').exists()
