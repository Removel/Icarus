from __future__ import annotations

from pathlib import Path
import sys
import textwrap

import pytest


CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))


def write_config(root: Path, text: str, name: str = "icarus.toml") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def test_foreground_endpoint_args_are_declared_dependency_bindings(tmp_path):
    from registry import load_registry, RegistryError
    text = '''version = 1
[components.ui]
kind = "app"
provisioning = "foreground"
endpoint_args = { "--gateway-url" = "server" }
[components.ui.requires.server]
required = true
[components.server]
kind = "app"
provisioning = "none"
'''
    write_config(tmp_path, text)
    try:
        registry = load_registry(tmp_path)
    except RegistryError:
        registry = None
    assert registry is not None, "foreground endpoint bindings must be loadable"
    assert registry.get("ui").endpoint_args == {"--gateway-url": "server"}
    write_config(tmp_path, text.replace('"server" }', '"unknown" }'))
    with pytest.raises(RegistryError):
        load_registry(tmp_path)


def test_loads_base_registry_with_defaults(tmp_path):
    write_config(tmp_path, """
        version = 1
        [defaults]
        mode = "local"
        default_stack = false
        [components.service]
        kind = "app"
        provisioning = "process"
        endpoint = "http://127.0.0.1:8765"
        probe = { type = "http", path = "/health" }
    """)
    try:
        from registry import load_registry
    except ModuleNotFoundError as error:
        if error.name != "registry":
            raise
        result = None
    else:
        result = load_registry(tmp_path)

    assert result is not None, "registry loader must produce a registry from valid TOML"
    service = result.get("service")
    assert service.mode == "local"
    assert service.enabled is True
    assert service.probe.path == "/health"
    assert service.compose_project == "icarus-service"
    assert service.expected_containers == 1
    assert result.version == 1
    assert result.names() == ("service",)
    assert result.default_stack() == ()


@pytest.fixture
def api():
    import registry

    return registry


BASE = """
    version = 1
    [components.service]
    kind = "app"
    provisioning = "process"
"""


def load_fields(tmp_path, api, fields):
    write_config(tmp_path, BASE)
    return api.load_registry(tmp_path, overrides={"service": fields}).get("service")


def test_workdir_and_explicit_image_action_are_operational_metadata(tmp_path, api):
    component = load_fields(tmp_path, api, {"workdir": ".", "image_action": "pull"})
    assert component.workdir == "."
    assert component.image_action == "pull"
    assert load_fields(tmp_path, api, {}).image_action == "build"
    for value in ("../outside", "/tmp", "C:/outside"):
        with pytest.raises(api.RegistryError, match="workdir"):
            load_fields(tmp_path, api, {"workdir": value})
    with pytest.raises(api.RegistryError, match="image_action"):
        load_fields(tmp_path, api, {"image_action": "guess-yaml"})
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir(exist_ok=True)
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(api.RegistryError, match="workdir"):
        load_fields(tmp_path, api, {"workdir": "escape"})


def test_recursive_overlay_then_component_override_without_mutating_input(tmp_path, api):
    write_config(tmp_path, """
        version = 1
        [defaults]
        mode = "remote"
        default_stack = true
        [components.database]
        kind = "middleware"
        provisioning = "compose"
        compose = "infra/database/compose.yaml"
        host = "127.0.0.1"
        port = 15432
        engine = "sql"
        probe = { type = "tcp", timeout_seconds = 3 }
        env = { DATABASE = "base", DEBUG = false }
        [components.service]
        kind = "app"
        provisioning = "process"
        default_stack = false
        start_command = ["python3", "scripts/start.py", "--base"]
    """)
    write_config(tmp_path, """
        [components.database]
        host = "db.example.test"
        port = 5432
        probe = { timeout_seconds = 4 }
        env = { DATABASE = "overlay" }
        [components.service]
        start_command = ["bash", "scripts/start.sh"]
    """, "icarus.local.toml")
    overrides = {"database": {"port": 6432, "env": {"DEBUG": True}}}
    result = api.load_registry(tmp_path, overrides=overrides)
    database = result.get("database")
    assert database.mode == "remote"
    assert database.engine == "sql"
    assert database.host == "db.example.test"
    assert database.port == 6432
    assert database.probe.type == "tcp"
    assert database.probe.timeout_seconds == 4
    assert database.env == {"DATABASE": "overlay", "DEBUG": True}
    assert result.get("service").start_command == ("bash", "scripts/start.sh")
    assert result.names() == ("database", "service")
    assert result.default_stack() == ("database",)
    assert overrides == {"database": {"port": 6432, "env": {"DEBUG": True}}}
    database.env["DEBUG"] = False
    assert overrides["database"]["env"]["DEBUG"] is True


def test_alternative_config_uses_only_sibling_overlay(tmp_path, api):
    write_config(tmp_path, "not valid TOML")
    base = write_config(tmp_path, BASE, "fixtures/isolated.toml")
    write_config(tmp_path, """
        [components.service]
        mode = "remote"
    """, "fixtures/isolated.local.toml")
    write_config(tmp_path, "not valid TOML", "icarus.local.toml")
    for path in (Path("fixtures/isolated.toml"), base):
        result = api.load_registry(tmp_path, config_path=path)
        assert result.get("service").mode == "remote"


def test_operational_metadata_and_dependency_bindings_are_typed(tmp_path, api):
    write_config(tmp_path, """
        version = 1
        [components.database]
        kind = "middleware"
        provisioning = "compose"
        compose = "infra/database.yaml"
        host = "db.example.test"
        port = 5432
        network_host = "database"
        network_port = 5432
        shared_network = true
        network = "custom-net"
        compose_project = "isolated-database"
        expected_containers = 2
        env = { DATABASE = "bootstrap", CONNECTIONS = 20, DEBUG = false }
        secret_env = { DB_PASSWORD = "ISOLATED_DB_PASSWORD" }
        compose_command = ["bash", "infra/compose.sh"]
        data_dir = "data/database"
        probe = { type = "tcp" }
        [components.service]
        kind = "app"
        provisioning = "process"
        endpoint = "https://service.example.test:8765"
        host = "service.example.test"
        port = 8765
        bind_host = "0.0.0.0"
        host_env = "LISTEN_HOST"
        port_env = "LISTEN_PORT"
        endpoint_env = "PUBLIC_ENDPOINT"
        start_command = ["python3", "scripts/start.py"]
        install_command = ["bash", "scripts/install.sh"]
        dev_install_command = ["bash", "scripts/install.sh", "--dev"]
        process_markers = ["scripts/start.py", "--serve"]
        probe = { type = "http", token_env = "HEALTH_TOKEN", header = "X-Health-Token", expect_status = 204, expect_body = { status = "ready" }, timeout_seconds = 1.5, max_response_bytes = 1024 }
        [components.service.requires.database]
        required = false
        endpoint_env = "DATABASE_URL"
        connection = { host_env = "DB_HOST", port_env = "DB_PORT", database_env = "DB_NAME", user_env = "DB_USER", schema_env = "DB_SCHEMA", graph_env = "DB_GRAPH", password_env = "DB_PASSWORD", database = "records", user = "reader", schema = "private", graph = "relations" }
    """)
    result = api.load_registry(tmp_path)
    service = result.get("service")
    database = result.get("database")
    assert service.bind_host == "0.0.0.0"
    assert service.host == "service.example.test"
    assert service.endpoint == "https://service.example.test:8765"
    assert (service.host_env, service.port_env, service.endpoint_env) == (
        "LISTEN_HOST", "LISTEN_PORT", "PUBLIC_ENDPOINT"
    )
    assert service.start_command == ("python3", "scripts/start.py")
    assert service.install_command == ("bash", "scripts/install.sh")
    assert service.dev_install_command == ("bash", "scripts/install.sh", "--dev")
    assert service.process_markers == ("scripts/start.py", "--serve")
    assert service.probe.token_env == "HEALTH_TOKEN"
    assert service.probe.header == "X-Health-Token"
    assert service.probe.expect_status == 204
    assert service.probe.expect_body == {"status": "ready"}
    assert service.probe.timeout_seconds == 1.5
    assert service.probe.max_response_bytes == 1024
    assert database.compose == "infra/database.yaml"
    assert database.compose_project == "isolated-database"
    assert database.expected_containers == 2
    assert database.data_dir == "data/database"
    assert database.shared_network is True
    assert database.network == "custom-net"
    assert (database.network_host, database.network_port) == ("database", 5432)
    assert database.env == {"DATABASE": "bootstrap", "CONNECTIONS": 20, "DEBUG": False}
    assert database.secret_env == {"DB_PASSWORD": "ISOLATED_DB_PASSWORD"}
    assert database.compose_command == ("bash", "infra/compose.sh")
    dep = service.requires["database"]
    assert dep.name == "database"
    assert dep.required is False
    assert dep.endpoint_env == "DATABASE_URL"
    assert dep.connection.password_env == "DB_PASSWORD"
    assert dep.connection.host_env == "DB_HOST"
    assert dep.connection.port_env == "DB_PORT"
    assert dep.connection.database_env == "DB_NAME"
    assert dep.connection.user_env == "DB_USER"
    assert dep.connection.schema_env == "DB_SCHEMA"
    assert dep.connection.graph_env == "DB_GRAPH"
    assert (dep.connection.database, dep.connection.user, dep.connection.schema,
            dep.connection.graph) == ("records", "reader", "private", "relations")


def test_probe_body_json_subset_accepts_scalar_json_values(tmp_path, api):
    body = {"status": "ready", "healthy": True, "workers": 2, "ratio": 0.5}
    service = load_fields(tmp_path, api, {
        "endpoint": "http://127.0.0.1:8765",
        "probe": {"type": "http", "expect_body": body},
    })
    assert service.probe.expect_body == body
    assert service.probe.expect_body is not body


@pytest.mark.parametrize("body", ["ready", {"nested": {"status": "ready"}},
                                  {"status": ["ready"]}, {"status": float("nan")},
                                  {"token": "do-not-leak"}])
def test_probe_body_rejects_nonflat_or_secret_expectations(tmp_path, api, body):
    with pytest.raises(api.RegistryError, match="expect_body") as error:
        load_fields(tmp_path, api, {
            "endpoint": "http://127.0.0.1:8765",
            "probe": {"type": "http", "expect_body": body},
        })
    assert "do-not-leak" not in str(error.value)


def test_frozen_models_have_independent_default_mappings(api):
    from dataclasses import FrozenInstanceError, fields

    component = api.Component("first", "app", "local", "process")
    other = api.Component("second", "app", "local", "process")
    component.requires["second"] = api.Dependency("second")
    component.env["DEBUG"] = True
    component.secret_env["DB_PASSWORD"] = "SOURCE_PASSWORD"
    assert other.requires == {}
    assert other.env == {}
    assert other.secret_env == {}
    assert api.Probe("tcp").timeout_seconds == 2
    assert api.Probe("tcp").max_response_bytes == 65536
    assert component.network == "icarus"
    assert component.shared_network is False
    for model in (component, api.Probe("tcp"), api.Connection(), api.Dependency("x"),
                  api.Registry(1, {})):
        name = fields(model)[0].name
        with pytest.raises(FrozenInstanceError):
            setattr(model, name, getattr(model, name))


def test_default_stack_excludes_disabled_components_and_missing_get_is_actionable(tmp_path, api):
    write_config(tmp_path, BASE + "\n default_stack = true\n enabled = false\n")
    result = api.load_registry(tmp_path)
    assert result.default_stack() == ()
    with pytest.raises(api.RegistryError, match="missing"):
        result.get("missing")


@pytest.mark.parametrize("provisioning", ["embedded", "bundled"])
def test_hosted_components_have_declared_owner(tmp_path, api, provisioning):
    write_config(tmp_path, BASE + f"""
        [components.worker]
        kind = "app"
        provisioning = "{provisioning}"
        owner = "service"
    """)
    assert api.load_registry(tmp_path).get("worker").owner == "service"


def test_bundled_middleware_can_use_probe_address_without_own_host_port(tmp_path, api):
    write_config(tmp_path, BASE + """
        [components.database]
        kind = "middleware"
        provisioning = "bundled"
        owner = "service"
        probe = { type = "tcp", host = "localhost", port = 9999 }
    """)
    database = api.load_registry(tmp_path).get("database")
    assert database.host is None
    assert database.port is None
    assert (database.probe.host, database.probe.port) == ("localhost", 9999)


@pytest.mark.parametrize("kind", ["app", "middleware"])
def test_remote_compose_does_not_require_local_commands_or_file(tmp_path, api, kind):
    service = load_fields(tmp_path, api, {
        "kind": kind, "mode": "remote", "provisioning": "compose",
        "host": "db.example.test", "port": 5432,
    })
    assert service.compose is None
    assert service.compose_command == ()
    assert service.start_command == ()


def test_missing_on_disk_compose_is_valid_schema_and_tcp_override_is_preserved(tmp_path, api):
    service = load_fields(tmp_path, api, {
        "provisioning": "compose", "compose": "not-created/compose.yaml",
        "probe": {"type": "tcp", "host": "localhost", "port": 65535},
    })
    assert service.compose == "not-created/compose.yaml"
    assert service.probe.port == 65535
    assert not (tmp_path / "not-created/compose.yaml").exists()


@pytest.mark.parametrize("fields, message", [
    ({"kind": "daemon"}, "kind"),
    ({"kind": 1}, "kind"),
    ({"mode": "mixed"}, "mode"),
    ({"mode": False}, "mode"),
    ({"provisioning": "shell"}, "provisioning"),
    ({"provisioning": []}, "provisioning"),
    ({"enabled": "true"}, "enabled"),
    ({"enabled": 1}, "enabled"),
    ({"default_stack": 1}, "default_stack"),
    ({"shared_network": "true"}, "shared_network"),
    ({"network": ""}, "network"),
    ({"host": 1}, "host"),
    ({"host": ""}, "host"),
    ({"bind_host": False}, "bind_host"),
    ({"engine": []}, "engine"),
    ({"owner": 2}, "owner"),
    ({"port": True}, "port"),
    ({"port": 0}, "port"),
    ({"port": 65536}, "port"),
    ({"port": "5432"}, "port"),
    ({"port": 5.5}, "port"),
    ({"network_port": False}, "network_port"),
    ({"expected_containers": True}, "expected_containers"),
    ({"expected_containers": 0}, "expected_containers"),
    ({"compose_project": 1}, "compose_project"),
    ({"compose": 42}, "compose"),
    ({"data_dir": []}, "data_dir"),
    ({"provisioning": "compose"}, "compose"),
    ({"kind": "middleware"}, "host.*port"),
    ({"kind": "middleware", "host": "localhost"}, "host.*port"),
    ({"probe": "http"}, "probe"),
    ({"probe": {"type": "http"}}, "endpoint"),
    ({"probe": {"type": "tcp"}}, "host.*port"),
    ({"probe": {}}, "type"),
    ({"probe": {"type": "icmp"}}, "type"),
    ({"probe": {"type": False}}, "type"),
    ({"probe": {"type": "tcp", "port": False}}, "port"),
    ({"probe": {"type": "tcp", "port": 65536}}, "port"),
    ({"probe": {"type": "http", "path": "http://evil.test"}}, "path"),
    ({"probe": {"type": "http", "path": "//evil.test"}}, "path"),
    ({"probe": {"type": "http", "timeout_seconds": 0}}, "timeout_seconds"),
    ({"probe": {"type": "http", "timeout_seconds": True}}, "timeout_seconds"),
    ({"probe": {"type": "http", "timeout_seconds": "2"}}, "timeout_seconds"),
    ({"probe": {"type": "http", "timeout_seconds": float("nan")}}, "timeout_seconds"),
    ({"probe": {"type": "http", "timeout_seconds": float("inf")}}, "timeout_seconds"),
    ({"probe": {"type": "http", "max_response_bytes": 0}}, "max_response_bytes"),
    ({"probe": {"type": "http", "max_response_bytes": True}}, "max_response_bytes"),
    ({"probe": {"type": "http", "expect_status": True}}, "expect_status"),
    ({"probe": {"type": "http", "expect_status": 600}}, "expect_status"),
    ({"probe": {"type": "http", "expect_body": 2}}, "expect_body"),
    ({"probe": {"type": "http", "header": "bad\r\nHeader"}}, "header"),
    ({"requires": []}, "requires"),
    ({"requires": {"service": {}}}, "self"),
    ({"requires": {"missing": {}}}, "missing"),
    ({"requires": {"missing": {"required": "yes"}}}, "required"),
    ({"owner": "missing"}, "owner"),
    ({"owner": "service"}, "owner"),
    ({"provisioning": "embedded"}, "owner"),
    ({"provisioning": "bundled"}, "owner"),
    ({"start_command": "python3 start.py"}, "start_command"),
    ({"start_command": [True]}, "start_command"),
    ({"start_command": [""]}, "start_command"),
    ({"install_command": ["bash", "bad\x00arg"]}, "install_command"),
    ({"dev_install_command": {}}, "dev_install_command"),
    ({"compose_command": [1]}, "compose_command"),
    ({"process_markers": [""]}, "process_markers"),
    ({"env": {"FLOAT": 1.2}}, "env"),
    ({"env": {"NESTED": {"value": "no"}}}, "env"),
    ({"env": {"BAD-NAME": "value"}}, "env"),
    ({"secret_env": {"PASSWORD": "bad-name"}}, "secret_env"),
    ({"secret_env": []}, "secret_env"),
    ({"host_env": "BAD-NAME"}, "host_env"),
    ({"port_env": 3}, "port_env"),
    ({"endpoint_env": "3BAD"}, "endpoint_env"),
    ({"probe": {"type": "http", "token_env": "BAD-NAME"}}, "token_env"),
])
def test_rejects_invalid_component_fields(tmp_path, api, fields, message):
    with pytest.raises(api.RegistryError, match=message):
        load_fields(tmp_path, api, fields)


@pytest.mark.parametrize("endpoint", [
    "/relative", "example.test:8765", "//example.test", "http:///missing-host",
    "file:///tmp/config", "http://example.test:bad", "http://example.test:0",
    "http://example.test:65536", "http://[broken", "http://exa mple.test",
    "https://user:do-not-leak@example.test", "https://user@example.test",
    "https://example.test/?access_token=do-not-leak", 123,
])
def test_rejects_invalid_or_credential_bearing_urls_without_echoing_secrets(tmp_path, api, endpoint):
    with pytest.raises(api.RegistryError, match="endpoint") as error:
        load_fields(tmp_path, api, {"endpoint": endpoint})
    assert "do-not-leak" not in str(error.value)


@pytest.mark.parametrize("field", ["compose", "data_dir"])
@pytest.mark.parametrize("path", ["../escape", "safe/../escape", "/absolute/path",
                                   "", "..\\escape", "C:\\absolute\\path"])
def test_rejects_unsafe_relative_paths(tmp_path, api, field, path):
    with pytest.raises(api.RegistryError, match=field):
        load_fields(tmp_path, api, {field: path})


@pytest.mark.parametrize("field", ["compose", "data_dir"])
def test_rejects_existing_symlink_escape_even_with_nonexistent_leaf(tmp_path, api, field):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(api.RegistryError, match=field):
        load_fields(root, api, {field: "linked/not-created.yaml"})
    (root / "inside").mkdir()
    (root / "safe").symlink_to(root / "inside", target_is_directory=True)
    assert getattr(load_fields(root, api, {field: "safe/not-created.yaml"}), field) == "safe/not-created.yaml"


@pytest.mark.parametrize("fields", [
    {"password": "do-not-leak"}, {"api_key": "do-not-leak"},
    {"token": "do-not-leak"}, {"env": {"DB_PASSWORD": "do-not-leak"}},
    {"env": {"API_KEY": "do-not-leak"}}, {"env": {"ACCESS_TOKEN": "do-not-leak"}},
    {"env": {"CLIENT_SECRET": "do-not-leak"}},
    {"env": {"DB_PASS": "do-not-leak"}},
    {"probe": {"type": "http", "token": "do-not-leak"}},
    {"requires": {"missing": {"password": "do-not-leak"}}},
    {"requires": {"missing": {"connection": {"password": "do-not-leak"}}}},
])
def test_rejects_secret_literals_without_echoing_values(tmp_path, api, fields):
    with pytest.raises(api.RegistryError) as error:
        load_fields(tmp_path, api, fields)
    assert "do-not-leak" not in str(error.value)


@pytest.mark.parametrize("destination", ["PGPASSWORD", "MYSQL_PWD", "REDISCLI_AUTH"])
def test_standard_credential_aliases_require_secret_references(tmp_path, api, destination):
    write_config(tmp_path, BASE + f'\nenv = {{ {destination} = "do-not-leak" }}\n')
    with pytest.raises(api.RegistryError, match="secret_env") as error:
        api.load_registry(tmp_path)
    assert f"components.service.env.{destination}" in str(error.value)
    assert "do-not-leak" not in str(error.value)

    write_config(tmp_path, BASE + f'\nsecret_env = {{ {destination} = "SOURCE_CREDENTIAL" }}\n')
    service = api.load_registry(tmp_path).get("service")
    assert service.secret_env == {destination: "SOURCE_CREDENTIAL"}
    assert service.env == {}


def test_rejects_nul_in_toml_env_string_without_echoing_value(tmp_path, api):
    write_config(tmp_path, BASE + '\nenv = { BOOTSTRAP_VALUE = "do-not-leak\\u0000suffix" }\n')
    with pytest.raises(api.RegistryError, match="components.service.env.BOOTSTRAP_VALUE") as error:
        api.load_registry(tmp_path)
    assert "do-not-leak" not in str(error.value)


def test_bootstrap_env_keeps_empty_string_and_int_bool_values(tmp_path, api):
    write_config(tmp_path, BASE + '\nenv = { EMPTY = "", COUNT = 2, DEBUG = false }\n')
    assert api.load_registry(tmp_path).get("service").env == {
        "EMPTY": "", "COUNT": 2, "DEBUG": False,
    }


@pytest.mark.parametrize("fields, message", [
    ({"surprise": True}, "unknown"),
    ({"probe": {"type": "http", "surprise": True}}, "unknown"),
    ({"requires": {"missing": {"surprise": True}}}, "unknown"),
    ({"requires": {"missing": {"connection": {"surprise": True}}}}, "unknown"),
    ({"requires": {"missing": {"connection": []}}}, "connection"),
    ({"requires": {"missing": {"connection": {"password_env": "bad-name"}}}}, "password_env"),
    ({"requires": {"missing": {"connection": {"database": 42}}}}, "database"),
    ({"requires": {"missing": {"endpoint_env": "bad-name"}}}, "endpoint_env"),
])
def test_nested_schema_is_strict(tmp_path, api, fields, message):
    with pytest.raises(api.RegistryError, match=message):
        load_fields(tmp_path, api, fields)


@pytest.mark.parametrize("text, message", [
    ("[components.service]\nkind = 'app'\nprovisioning = 'process'", "version"),
    ("version = true\n[components.service]\nkind = 'app'\nprovisioning = 'process'", "version"),
    ("version = 2\n[components.service]\nkind = 'app'\nprovisioning = 'process'", "version"),
    ("version = 1", "components"),
    ("version = 1\ncomponents = {}", "components"),
    ("version = 1\ncomponents = []", "components"),
    ("version = 1\ndefaults = false", "defaults"),
    ("version = 1\nsurprise = true", "unknown"),
    ("version = 1\n[defaults]\nsurprise = true", "unknown"),
    ("version = 1\n[defaults]\nmode = 'mixed'", "mode"),
    ("version = 1\n[defaults]\ndefault_stack = 1", "default_stack"),
    ("version = 1\n[components.service]\nprovisioning = 'process'", "kind"),
    ("version = 1\n[components.service]\nkind = 'app'", "provisioning"),
    ("version = 1\n[components]\nservice = false", "service"),
])
def test_rejects_missing_or_invalid_root_schema(tmp_path, api, text, message):
    write_config(tmp_path, text)
    with pytest.raises(api.RegistryError, match=message):
        api.load_registry(tmp_path)


@pytest.mark.parametrize("overrides, message", [
    ([], "overrides"), ({"missing": {"enabled": True}}, "missing"),
    ({"service": False}, "service"),
])
def test_rejects_bad_override_shape_or_unknown_targets(tmp_path, api, overrides, message):
    write_config(tmp_path, BASE)
    with pytest.raises(api.RegistryError, match=message):
        api.load_registry(tmp_path, overrides=overrides)


@pytest.mark.parametrize("name", ["icarus.toml", "icarus.local.toml"])
def test_wraps_toml_errors_with_path_but_not_source_values(tmp_path, api, name):
    write_config(tmp_path, BASE)
    write_config(tmp_path, 'password = "do-not-leak" trailing\n', name)
    with pytest.raises(api.RegistryError, match=name) as error:
        api.load_registry(tmp_path)
    assert "do-not-leak" not in str(error.value)


def test_wraps_missing_base_file(tmp_path, api):
    with pytest.raises(api.RegistryError, match="icarus.toml"):
        api.load_registry(tmp_path)


def test_graph_cycles_are_not_validated_by_registry(tmp_path, api):
    write_config(tmp_path, BASE + """
        [components.service.requires.worker]
        [components.worker]
        kind = "app"
        provisioning = "foreground"
        [components.worker.requires.service]
    """)
    result = api.load_registry(tmp_path)
    assert result.get("service").requires["worker"].required is True
    assert result.get("worker").requires["service"].connection is None


def test_supports_remaining_provisioning_modes_and_boundary_ports(tmp_path, api):
    service = load_fields(tmp_path, api, {
        "provisioning": "none", "host": "localhost", "port": 1,
        "network_port": 65535, "probe": {"type": "tcp"},
    })
    assert service.provisioning == "none"
    assert service.port == 1
    assert service.network_port == 65535
