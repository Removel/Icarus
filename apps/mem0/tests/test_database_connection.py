"""Database credentials are URL encoded before SQLAlchemy/psycopg consume them."""

import importlib.util
from pathlib import Path

from sqlalchemy.engine import make_url


def test_server_database_url_preserves_reserved_password(monkeypatch):
    for key, value in {'POSTGRES_HOST': 'postgres', 'POSTGRES_USER': 'mem0_local',
                       'POSTGRES_PASSWORD': 'p@ss:/?#word', 'APP_DB_NAME': 'mem0_app'}.items():
        monkeypatch.setenv(key, value)
    path = Path(__file__).resolve().parents[1] / 'server/db.py'
    spec = importlib.util.spec_from_file_location('database_connection_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    url = make_url(module._build_database_url())
    assert url.host == 'postgres'
    assert url.username == 'mem0_local'
    assert url.password == 'p@ss:/?#word'
    module.engine.dispose()


def test_vector_store_connection_url_preserves_reserved_password(monkeypatch):
    from mem0.vector_stores import pgvector
    seen = []

    class Pool:
        def __init__(self, **kwargs):
            seen.append(kwargs['conninfo'])
        def open(self, *, wait):
            pass

    monkeypatch.setattr(pgvector, 'ConnectionPool', Pool)
    monkeypatch.setattr(pgvector, 'PSYCOPG_VERSION', 3)
    pgvector.PGVector('mem0_app', 'test', 384, 'mem0_local', 'p@ss:/?#word', 'postgres', 5432, False, False)
    url = make_url(seen[0])
    assert url.host == 'postgres'
    assert url.password == 'p@ss:/?#word'
