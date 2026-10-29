"""Database URLs preserve credentials and IPv6 hosts before driver access."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url


@pytest.mark.parametrize("host", ["postgres", "::1"])
def test_server_database_url_preserves_reserved_password(monkeypatch, host):
    for key, value in {"POSTGRES_HOST": host, "POSTGRES_PORT": "5432", "POSTGRES_USER": "mem0_local",
                       "POSTGRES_PASSWORD": "p@ss:/?#word", "APP_DB_NAME": "mem0_app"}.items():
        monkeypatch.setenv(key, value)
    path = Path(__file__).resolve().parents[1] / "server/db.py"
    spec = importlib.util.spec_from_file_location("database_connection_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        url = make_url(module._build_database_url())
        assert url.host == host
        assert url.port == 5432
        assert url.username == "mem0_local"
        assert url.password == "p@ss:/?#word"
        assert url.database == "mem0_app"
    finally:
        module.engine.dispose()


@pytest.mark.parametrize("host", ["postgres", "::1"])
def test_vector_store_connection_url_preserves_reserved_components(monkeypatch, host):
    from mem0.vector_stores import pgvector
    from psycopg.conninfo import conninfo_to_dict

    seen = []

    class Pool:
        def __init__(self, **kwargs):
            seen.append(kwargs["conninfo"])

        def open(self, *, wait):
            pass

    monkeypatch.setattr(pgvector, "ConnectionPool", Pool)
    monkeypatch.setattr(pgvector, "PSYCOPG_VERSION", 3)
    pgvector.PGVector("mem0/app", "test", 384, "mem0@local", "p@ss:/?#word", host, 5432, False, False)
    parameters = conninfo_to_dict(seen[0])
    assert parameters["host"] == host
    assert parameters["port"] == "5432"
    assert parameters["user"] == "mem0@local"
    assert parameters["password"] == "p@ss:/?#word"
    assert parameters["dbname"] == "mem0/app"


def test_alembic_configuration_preserves_percent_encoded_url(monkeypatch):
    import configparser
    import runpy
    import sys
    from contextlib import nullcontext
    from types import ModuleType, SimpleNamespace

    url = "postgresql+psycopg://mem0_local:p%40ss%3Aword@postgres:5432/mem0_app"
    parser = configparser.ConfigParser()
    parser.add_section("alembic")
    configured = {}
    config = SimpleNamespace(
        config_file_name=None,
        set_main_option=lambda key, value: parser.set("alembic", key, value),
        get_main_option=lambda key: parser.get("alembic", key),
    )
    context = SimpleNamespace(
        config=config, is_offline_mode=lambda: True,
        configure=lambda **kwargs: configured.update(kwargs),
        begin_transaction=nullcontext, run_migrations=lambda: None,
    )
    alembic = ModuleType("alembic")
    alembic.context = context
    database = ModuleType("db")
    database.Base = SimpleNamespace(metadata=object())
    database._build_database_url = lambda: url
    monkeypatch.setitem(sys.modules, "alembic", alembic)
    monkeypatch.setitem(sys.modules, "db", database)
    monkeypatch.setitem(sys.modules, "models", ModuleType("models"))
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "server/alembic/env.py"))
    assert configured["url"] == url
    assert make_url(configured["url"]).password == "p@ss:word"
