"""Installed package behavior, independent of the repository working directory."""

from pathlib import Path
import subprocess
import sys

APP_DIR = Path(__file__).resolve().parents[1]


def isolated_code(tmp_path, code):
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_editable_import_outside_checkout(tmp_path):
    assert isolated_code(tmp_path, "import icarus, agent_app; from agent_app.application import AgentRuntime; print('installed')") == "installed"


def test_default_settings_resource_is_installed(tmp_path):
    assert isolated_code(tmp_path,
        "from importlib.resources import files; import json; "
        "p=files('agent_app.resources').joinpath('default_settings.json'); "
        "assert json.loads(p.read_text())['model_settings']; print('resource')") == "resource"


def test_installed_discovery_keeps_plugins_lazy(tmp_path):
    assert isolated_code(tmp_path,
        "import sys; from icarus.runtime.discovery import PluginManifestDiscovery; "
        "result=PluginManifestDiscovery(builtin_package='agent_app.plugins').discover(); "
        "assert len(result.plugins)==14;"
        "assert not any(n.startswith('agent_app.plugins.') for n in sys.modules); print('lazy')") == "lazy"


def test_installed_package_exposes_default_persona_resource(tmp_path):
    assert isolated_code(tmp_path,
        "from importlib.resources import files; "
        "p=files('agent_app.resources').joinpath('personas/default/persona.md'); "
        "assert '## soul' in p.read_text(); print('persona')") == "persona"


def test_typesafe_sdk_importable(tmp_path):
    assert isolated_code(tmp_path,
        "from importlib.metadata import version; from packaging.version import Version; "
        "from typesafe_sdk import AsyncTypeSafeClient, Noul, Score; "
        "assert Version('0.7.2') <= Version(version('typesafe-sdk')) < Version('0.8'); "
        "print('judge')") == "judge"


def test_framework_import_does_not_load_vendor_or_application(tmp_path):
    assert isolated_code(tmp_path,
        "import sys; import icarus; "
        "assert not any(n.startswith(('agent_app', 'openai', 'anthropic')) for n in sys.modules); print('core')") == "core"
