"""Disposable PostgreSQL databases for application-level integration tests."""
import os
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def database_url(monkeypatch):
    original = os.environ.get('ARGUS_TEST_DATABASE_URL')
    if not original:
        raise RuntimeError('Run bash apps/argus/scripts/test.sh to supply isolated PostgreSQL')
    url = make_url(original)
    if url.drivername != 'postgresql+asyncpg' or not (url.database or '').endswith('_test'):
        raise RuntimeError('Integration tests require a disposable *_test database')
    name = 'argus_' + uuid4().hex + '_test'
    admin = create_async_engine(original, isolation_level='AUTOCOMMIT')
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    isolated = url.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv('ARGUS_TEST_DATABASE_URL', isolated)
    try:
        yield isolated
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
