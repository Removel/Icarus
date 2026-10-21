import json

import pytest
from pydantic import ValidationError


def configuration():
    return {
        "identity": {
            "user": {"person_id": "removel", "display_name": "鱼梦"},
            "agent": {"person_id": "icarus", "display_name": "Icarus"},
        },
        "postgres": {"schema": "rela_mem_test"},
    }


def test_load_config_keeps_identity_and_hides_secrets(tmp_path):
    from config import load_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps(configuration()), encoding="utf-8")
    settings = load_settings(path, {
        "RELA_MEM_API_TOKEN": "test-token-not-for-production",
        "RELA_MEM_FALKORDB_PASSWORD": "graph-test-password",
        "RELA_MEM_PG_PASSWORD": "pg-test-password",
    })
    assert settings.identity.user.person_id == "removel"
    assert settings.identity.agent.person_id == "icarus"
    assert settings.postgres.schema_name == "rela_mem_test"
    assert "pg-test-password" not in repr(settings)
    assert "test-token-not-for-production" not in repr(settings)


@pytest.mark.parametrize("change", [
    {"unexpected": True},
    {"postgres": {"schema": "public; DROP SCHEMA public"}},
    {"identity": {"user": {"person_id": "same", "display_name": "me"},
                  "agent": {"person_id": "same", "display_name": "bot"}}},
])
def test_invalid_configuration_is_rejected(tmp_path, change):
    from config import load_settings

    config = configuration() | change
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(path, {"RELA_MEM_API_TOKEN": "token", "RELA_MEM_FALKORDB_PASSWORD": "pw", "RELA_MEM_PG_PASSWORD": "pw"})


def test_standalone_configuration_does_not_assume_repository_depth(tmp_path, monkeypatch):
    import config

    path = tmp_path / "settings.json"
    path.write_text(json.dumps(configuration()), encoding="utf-8")
    monkeypatch.setattr(config, "__file__", "/app/src/config.py")
    monkeypatch.setenv("RELA_MEM_CONFIG", str(path))
    monkeypatch.setenv("RELA_MEM_API_TOKEN", "test-token")
    monkeypatch.setenv("RELA_MEM_FALKORDB_PASSWORD", "graph-password")
    monkeypatch.setenv("RELA_MEM_PG_PASSWORD", "pg-password")
    assert config.load_settings().identity.user.person_id == "removel"


@pytest.fixture
def connection_environment():
    return {
        "RELA_MEM_API_TOKEN": "config-test-token",
        "RELA_MEM_FALKORDB_PASSWORD": "config-test-graph-password",
        "RELA_MEM_PG_PASSWORD": "config-test-pg-password",
    }


@pytest.fixture
def connection_configuration():
    return configuration() | {
        "postgres": {"host": "pg-file.example.invalid", "port": 15433,
                     "database": "file_database", "user": "file_user", "schema": "file_schema"},
        "falkordb": {"host": "graph-file.example.invalid", "port": 16380,
                     "graph": "file_graph", "timeout_seconds": 17},
    }


def test_connection_environment_overrides_all_fields_without_changing_business_config(
    tmp_path, connection_environment, connection_configuration,
):
    from config import load_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps(connection_configuration), encoding="utf-8")
    environ = connection_environment | {
        "RELA_MEM_PG_HOST": "pg-env.example.invalid", "RELA_MEM_PG_PORT": "5433",
        "RELA_MEM_PG_DATABASE": "env_database", "RELA_MEM_PG_USER": "env_user",
        "RELA_MEM_PG_SCHEMA": "env_schema", "RELA_MEM_FALKORDB_HOST": "graph-env.example.invalid",
        "RELA_MEM_FALKORDB_PORT": "6380", "RELA_MEM_FALKORDB_GRAPH": "env_graph",
    }
    original_environ = dict(environ)

    settings = load_settings(path, environ)

    assert settings.postgres.model_dump(by_alias=True) == {
        "host": "pg-env.example.invalid", "port": 5433, "database": "env_database",
        "user": "env_user", "schema": "env_schema",
    }
    assert settings.falkordb.model_dump() == {
        "host": "graph-env.example.invalid", "port": 6380, "graph": "env_graph", "timeout_seconds": 17,
    }
    assert type(settings.postgres.port) is int
    assert type(settings.falkordb.port) is int
    assert settings.identity.model_dump() == connection_configuration["identity"]
    assert json.loads(path.read_text(encoding="utf-8")) == connection_configuration
    assert environ == original_environ
    for secret in connection_environment.values():
        assert secret not in repr(settings)
        assert secret not in settings.model_dump_json()


def test_connection_environment_creates_missing_sections(tmp_path, connection_environment):
    from config import load_settings

    data = configuration()
    del data["postgres"]
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    settings = load_settings(path, connection_environment | {
        "RELA_MEM_PG_HOST": "pg-env.example.invalid", "RELA_MEM_PG_SCHEMA": "env_schema",
        "RELA_MEM_FALKORDB_HOST": "graph-env.example.invalid", "RELA_MEM_FALKORDB_GRAPH": "env_graph",
    })

    assert settings.postgres.host == "pg-env.example.invalid"
    assert settings.postgres.schema_name == "env_schema"
    assert settings.falkordb.host == "graph-env.example.invalid"
    assert settings.falkordb.graph == "env_graph"


def test_connection_environment_partial_override_preserves_other_file_values(
    tmp_path, connection_environment, connection_configuration,
):
    from config import load_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps(connection_configuration), encoding="utf-8")

    settings = load_settings(path, connection_environment | {"RELA_MEM_PG_HOST": "pg-env.example.invalid"})

    assert settings.postgres.model_dump(by_alias=True) == {
        "host": "pg-env.example.invalid", "port": 15433, "database": "file_database",
        "user": "file_user", "schema": "file_schema",
    }
    assert settings.falkordb.model_dump() == connection_configuration["falkordb"]


def test_connection_environment_supplied_mapping_is_isolated_from_process(
    tmp_path, monkeypatch, connection_environment, connection_configuration,
):
    from config import load_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps(connection_configuration), encoding="utf-8")
    for name, value in {
        "RELA_MEM_CONFIG": str(tmp_path / "does-not-exist.json"),
        "RELA_MEM_PG_HOST": "pg-process.example.invalid", "RELA_MEM_PG_PORT": "invalid-process-port",
        "RELA_MEM_PG_DATABASE": "process_database", "RELA_MEM_PG_USER": "process_user",
        "RELA_MEM_PG_SCHEMA": "process_schema", "RELA_MEM_FALKORDB_HOST": "graph-process.example.invalid",
        "RELA_MEM_FALKORDB_PORT": "invalid-process-port", "RELA_MEM_FALKORDB_GRAPH": "process_graph",
        "RELA_MEM_API_TOKEN": "process-token", "RELA_MEM_PG_PASSWORD": "process-password",
        "RELA_MEM_FALKORDB_PASSWORD": "process-graph-password",
    }.items():
        monkeypatch.setenv(name, value)

    settings = load_settings(environ=connection_environment | {"RELA_MEM_CONFIG": str(path)})

    assert settings.postgres.model_dump(by_alias=True) == connection_configuration["postgres"]
    assert settings.falkordb.model_dump() == connection_configuration["falkordb"]
    assert settings.api_token.get_secret_value() == "config-test-token"
    with pytest.raises(ValidationError):
        load_settings(path, {})


@pytest.mark.parametrize("variable, field", [
    ("RELA_MEM_PG_PORT", "postgres.port"), ("RELA_MEM_FALKORDB_PORT", "falkordb.port"),
])
@pytest.mark.parametrize("value", ["port-test-secret", "", "0", "65536", "-1"])
def test_connection_environment_invalid_port_has_sanitized_validation_error(
    tmp_path, connection_environment, variable, field, value,
):
    from config import load_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps(configuration()), encoding="utf-8")

    with pytest.raises(ValidationError, match=field) as error:
        load_settings(path, connection_environment | {variable: value})

    for text in (str(error.value), repr(error.value)):
        assert "port-test-secret" not in text
        assert "input_value" not in text
        for secret in connection_environment.values():
            assert secret not in text


@pytest.mark.parametrize("variable", [
    "RELA_MEM_PG_HOST", "RELA_MEM_PG_DATABASE", "RELA_MEM_PG_USER", "RELA_MEM_PG_SCHEMA",
    "RELA_MEM_FALKORDB_HOST", "RELA_MEM_FALKORDB_GRAPH",
])
def test_connection_environment_empty_value_is_rejected(tmp_path, connection_environment, variable):
    from config import load_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps(configuration()), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_settings(path, connection_environment | {variable: ""})


def test_connection_environment_default_source_preserves_dotenv_precedence(tmp_path, monkeypatch):
    import config

    repository = tmp_path / "repository"
    (repository / "apps" / "rela-mem" / "src").mkdir(parents=True)
    (repository / "AGENTS.md").write_text("test repository", encoding="utf-8")
    (repository / ".env").write_text(
        "RELA_MEM_PG_HOST=pg-dotenv.example.invalid\nRELA_MEM_PG_PORT=5434\n"
        "RELA_MEM_FALKORDB_HOST=graph-dotenv.example.invalid\nRELA_MEM_FALKORDB_PORT=6381\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "__file__", str(repository / "apps" / "rela-mem" / "src" / "config.py"))
    monkeypatch.setattr(config.os, "environ", {
        "RELA_MEM_PG_HOST": "pg-process.example.invalid",
        "RELA_MEM_API_TOKEN": "dotenv-test-token",
        "RELA_MEM_PG_PASSWORD": "dotenv-test-pg-password",
        "RELA_MEM_FALKORDB_PASSWORD": "dotenv-test-graph-password",
    })
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(configuration()), encoding="utf-8")

    settings = config.load_settings(path)

    assert settings.postgres.host == "pg-process.example.invalid"
    assert settings.postgres.port == 5434
    assert settings.falkordb.host == "graph-dotenv.example.invalid"
    assert settings.falkordb.port == 6381


def test_missing_secret_is_not_silently_accepted(tmp_path):
    from config import load_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps(configuration()), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(path, {})
