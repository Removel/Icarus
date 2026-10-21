from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))

from registry import Component, Connection, Dependency, Registry


def component(name, **kwargs):
    defaults = dict(kind="app", mode="local", provisioning="process")
    defaults.update(kwargs)
    return Component(name=name, **defaults)


def registry(*components):
    return Registry(1, {item.name: item for item in components})


def database(**kwargs):
    defaults = dict(kind="middleware", provisioning="compose", host="127.0.0.1", port=15432,
                    network_host="postgres", network_port=5432, shared_network=True)
    defaults.update(kwargs)
    return component("postgres", **defaults)


def consumer(**kwargs):
    defaults = dict(provisioning="compose", shared_network=True, requires={
        "postgres": Dependency("postgres", connection=Connection(host_env="PG_HOST", port_env="PG_PORT")),
    })
    defaults.update(kwargs)
    return component("app", **defaults)


def test_compose_consumer_uses_shared_network_and_preserves_inherited_secrets():
    db = database()
    app = consumer()
    base = {"PATH": "/bin", "PG_PASSWORD": "inherited-private"}
    app.requires["postgres"] = Dependency("postgres", connection=Connection(
        host_env="PG_HOST", port_env="PG_PORT", password_env="PG_PASSWORD"))
    from injection import build_child_environment

    environment = build_child_environment(app, registry(app, db), base).environment
    assert environment == {"PATH": "/bin", "PG_PASSWORD": "inherited-private",
                           "PG_HOST": "postgres", "PG_PORT": "5432"}
    assert base == {"PATH": "/bin", "PG_PASSWORD": "inherited-private"}


@pytest.fixture
def inject():
    from injection import build_child_environment

    return build_child_environment


@pytest.mark.parametrize("app_changes,db_changes,host,port", [
    ({}, {}, "postgres", "5432"),
    ({"provisioning": "process"}, {}, "127.0.0.1", "15432"),
    ({"provisioning": "foreground"}, {}, "127.0.0.1", "15432"),
    ({"shared_network": False}, {}, "127.0.0.1", "15432"),
    ({}, {"shared_network": False}, "127.0.0.1", "15432"),
    ({"network": "other"}, {}, "127.0.0.1", "15432"),
    ({}, {"mode": "remote", "host": "db.example.test", "port": 6432}, "db.example.test", "6432"),
    ({}, {"network_host": None}, "127.0.0.1", "15432"),
    ({}, {"network_port": None}, "postgres", "15432"),
])
def test_connection_address_requires_actual_shared_network(inject, app_changes, db_changes, host, port):
    app, db = consumer(**app_changes), database(**db_changes)
    result = inject(app, registry(app, db), {})
    assert result.environment == {"PG_HOST": host, "PG_PORT": port}
    assert result.warnings == ()


@pytest.mark.parametrize("owner_provisioning,expected_host", [("process", "127.0.0.1"), ("compose", "postgres")])
def test_embedded_consumer_uses_owner_runtime_network(inject, owner_provisioning, expected_host):
    owner = component("gateway", provisioning=owner_provisioning, shared_network=True)
    # The logical agent's network metadata must not decide its actual reachability.
    app = consumer(provisioning="embedded", owner="gateway", shared_network=False, network="ignored")
    db = database()
    result = inject(app, registry(owner, app, db), {})
    assert result.environment["PG_HOST"] == expected_host
    assert "PG_HOST" not in owner.env


def test_nested_embedded_runtime_owner_is_resolved(inject):
    owner = component("gateway", provisioning="compose", shared_network=True)
    middle = component("middle", provisioning="embedded", owner="gateway")
    app = consumer(provisioning="embedded", owner="middle")
    db = database()
    assert inject(app, registry(owner, middle, app, db), {}).environment["PG_HOST"] == "postgres"


def test_own_bootstrap_listener_and_endpoint_exports(inject):
    app = component("service", env={"COUNT": 3, "DEBUG": False, "FEATURE": True, "EMPTY": ""},
                    host="127.0.0.1", bind_host="0.0.0.0", port=8765,
                    endpoint="http://localhost:8765", host_env="BIND_HOST", port_env="PORT",
                    endpoint_env="PUBLIC_ENDPOINT")
    result = inject(app, registry(app), {"PATH": "/bin"})
    assert result.environment == {"PATH": "/bin", "COUNT": "3", "DEBUG": "false", "FEATURE": "true",
                                  "EMPTY": "", "BIND_HOST": "0.0.0.0", "PORT": "8765",
                                  "PUBLIC_ENDPOINT": "http://localhost:8765"}
    assert app.env == {"COUNT": 3, "DEBUG": False, "FEATURE": True, "EMPTY": ""}


def test_own_listener_uses_advertised_host_when_no_bind_host(inject):
    app = component("service", host="localhost", host_env="HOST", port_env="PORT")
    assert inject(app, registry(app), {}).environment == {"HOST": "localhost"}


def test_secret_mapping_reads_only_supplied_base_environment(inject, monkeypatch):
    monkeypatch.setenv("SOURCE_TOKEN", "process-must-not-win")
    app = component("service", env={"SOURCE_TOKEN_ALIAS": "literal"}, secret_env={"API_TOKEN": "SOURCE_TOKEN"})
    base = {"SOURCE_TOKEN": "base-private", "API_TOKEN": "stale-private"}
    result = inject(app, registry(app), base)
    assert result.environment["API_TOKEN"] == "base-private"
    assert result.environment["SOURCE_TOKEN"] == "base-private"
    assert base["API_TOKEN"] == "stale-private"
    assert len(result.warnings) == 1
    assert "API_TOKEN" in result.warnings[0]
    assert "base-private" not in str(result.warnings) and "stale-private" not in str(result.warnings)


@pytest.mark.parametrize("base", [{}, {"SOURCE_TOKEN": ""}])
def test_missing_secret_names_source_without_leaking_values(inject, base):
    app = component("service", secret_env={"API_TOKEN": "SOURCE_TOKEN"})
    with pytest.raises(ValueError) as caught:
        inject(app, registry(app), base)
    detail = str(caught.value)
    assert "service" in detail and "SOURCE_TOKEN" in detail


def test_secret_source_is_not_resolved_from_other_injected_exports(inject):
    app = component("service", env={"SOURCE": "not-a-secret-source"}, secret_env={"TOKEN": "SOURCE"})
    with pytest.raises(ValueError, match="SOURCE"):
        inject(app, registry(app), {})


def test_all_connection_static_exports_and_optional_dependency_endpoint(inject):
    db = database()
    service = component("optional", endpoint="https://remote.example.test/api", mode="remote")
    app = component("app", requires={
        "postgres": Dependency("postgres", connection=Connection(
            host_env="HOST", port_env="PORT", database_env="DATABASE", database="app-db",
            user_env="USER", user="app-user", schema_env="SCHEMA", schema="app-schema",
            graph_env="GRAPH", graph="app-graph", password_env="PASSWORD")),
        "optional": Dependency("optional", required=False, endpoint_env="OPTIONAL_ENDPOINT"),
    })
    result = inject(app, registry(app, db, service), {})
    assert result.environment == {"HOST": "127.0.0.1", "PORT": "15432", "DATABASE": "app-db",
                                  "USER": "app-user", "SCHEMA": "app-schema", "GRAPH": "app-graph",
                                  "OPTIONAL_ENDPOINT": "https://remote.example.test/api"}
    assert "PASSWORD" not in result.environment


def test_password_is_not_derived_from_provider_or_other_environment(inject):
    db = database(secret_env={"PASSWORD": "DB_PASSWORD"})
    app = consumer(requires={"postgres": Dependency("postgres", connection=Connection(password_env="APP_PASSWORD"))})
    result = inject(app, registry(app, db), {"DB_PASSWORD": "provider-private"})
    assert result.environment == {"DB_PASSWORD": "provider-private"}


def test_only_declared_static_bindings_with_values_are_exported(inject):
    app = consumer(requires={"postgres": Dependency("postgres", connection=Connection(
        database="ignored", user_env="USER", schema_env="SCHEMA", graph="ignored"))})
    db = database()
    assert inject(app, registry(app, db), {}).environment == {}


def test_topology_overrides_base_with_names_only_warnings_in_declaration_order(inject):
    first = component("first", endpoint="http://new-private:1")
    second = component("second", endpoint="http://second-private:2")
    app = component("app", requires={"first": Dependency("first", endpoint_env="FIRST"),
                                     "second": Dependency("second", endpoint_env="SECOND")})
    base = {"FIRST": "http://old-private:3", "SECOND": "http://old-private:4"}
    result = inject(app, registry(app, first, second), base)
    assert result.environment == {"FIRST": "http://new-private:1", "SECOND": "http://second-private:2"}
    assert len(result.warnings) == 2
    assert "FIRST" in result.warnings[0] and "first" in result.warnings[0] and "app" in result.warnings[0]
    assert "SECOND" in result.warnings[1] and "second" in result.warnings[1]
    assert "private" not in str(result.warnings) and "http" not in str(result.warnings)
    assert base == {"FIRST": "http://old-private:3", "SECOND": "http://old-private:4"}


def test_matching_inherited_export_has_no_warning(inject):
    app = component("app", env={"COUNT": 2})
    assert inject(app, registry(app), {"COUNT": "2"}).warnings == ()


def test_conflicting_dependency_exports_are_errors_not_order_wins(inject):
    first = component("first", endpoint="http://first-private:1")
    second = component("second", endpoint="http://second-private:2")
    app = component("app", requires={"first": Dependency("first", endpoint_env="ENDPOINT"),
                                     "second": Dependency("second", endpoint_env="ENDPOINT")})
    for requires in (app.requires, dict(reversed(tuple(app.requires.items())))):
        with pytest.raises(ValueError) as caught:
            inject(replace(app, requires=requires), registry(app, first, second), {})
        detail = str(caught.value)
        assert all(name in detail for name in ("app", "first", "second", "ENDPOINT"))
        assert "private" not in detail and "http" not in detail


def test_matching_dependency_exports_are_allowed_and_warn_once(inject):
    first = component("first", endpoint="http://shared:1")
    second = component("second", endpoint="http://shared:1")
    app = component("app", requires={"first": Dependency("first", endpoint_env="ENDPOINT"),
                                     "second": Dependency("second", endpoint_env="ENDPOINT")})
    result = inject(app, registry(app, first, second), {"ENDPOINT": "stale"})
    assert result.environment == {"ENDPOINT": "http://shared:1"}
    assert len(result.warnings) == 1


def test_own_export_conflicting_with_dependency_is_rejected(inject):
    db = database()
    app = consumer(env={"PG_HOST": "stale-private"})
    with pytest.raises(ValueError, match="PG_HOST") as caught:
        inject(app, registry(app, db), {})
    assert "stale-private" not in str(caught.value)


def test_secret_and_listener_export_conflict_is_sanitized(inject):
    app = component("service", secret_env={"HOST": "TOKEN"}, host_env="HOST", host="127.0.0.1")
    with pytest.raises(ValueError, match="HOST") as caught:
        inject(app, registry(app), {"TOKEN": "private-secret"})
    assert "private-secret" not in str(caught.value)


def test_missing_dependency_is_descriptive(inject):
    app = consumer()
    with pytest.raises(ValueError, match="postgres"):
        inject(app, registry(app), {})


def test_ownership_cycles_and_missing_owners_fail_without_recursion(inject):
    app = consumer(provisioning="embedded", owner="gateway")
    gateway = component("gateway", provisioning="embedded", owner="app")
    with pytest.raises(ValueError, match="ownership cycle") as caught:
        inject(app, registry(app, gateway, database()), {})
    assert "app" in str(caught.value) and "gateway" in str(caught.value)
    with pytest.raises(ValueError, match="gateway"):
        inject(app, registry(app, database()), {})


def test_injection_does_not_read_dotenv_or_resolve_paths(inject, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("UNMERGED=value\n")
    monkeypatch.chdir(tmp_path)
    app = component("service", env={"DATA_DIR": "relative/data"})
    assert inject(app, registry(app), {}).environment == {"DATA_DIR": "relative/data"}
