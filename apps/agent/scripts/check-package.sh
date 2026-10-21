#!/usr/bin/env bash
set -euo pipefail

app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin="$app_dir/.venv/bin/python"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

"$python_bin" -m build --wheel --outdir "$work/dist" "$app_dir"
"$python_bin" - "$work/dist" <<'PY'
from pathlib import Path
import sys
from zipfile import ZipFile

wheel = next(Path(sys.argv[1]).glob('*.whl'))
with ZipFile(wheel) as archive:
    names = archive.namelist()
    manifests = [name for name in names if name.startswith('agent_app/plugins/') and name.endswith('/manifest.json')]
    assert len(manifests) == 14, manifests
    assert 'agent_app/resources/default_settings.json' in names
    assert 'agent_app/resources/personas/default/persona.md' in names
    assert not any('.env' in Path(name).parts for name in names)
    assert not any(name.startswith(('apps/', 'test/')) for name in names)
print('Wheel resources verified')
PY
"$python_bin" -m venv --system-site-packages "$work/venv"
"$work/venv/bin/python" -m pip install "$work"/dist/*.whl
(cd "$work" && "$work/venv/bin/python" -I - <<'PY'
from importlib.resources import files
import icarus
from agent_app.application import AgentRuntime
from icarus.runtime.discovery import PluginManifestDiscovery

assert files('agent_app.resources').joinpath('default_settings.json').is_file()
assert '## soul' in files('agent_app.resources').joinpath('personas/default/persona.md').read_text()
assert len(PluginManifestDiscovery(builtin_package='agent_app.plugins').discover().plugins) == 14
assert AgentRuntime is not None
print('Installed wheel imports and discovery verified')
PY
)
