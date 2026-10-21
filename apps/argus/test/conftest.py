"""Keep ordinary pytest isolated from real QQ credentials and application data."""

import json
import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from uuid import uuid4

from apps.argus.src.core import config
from apps.argus.src.core.database import Database
from packages import runtime_environment


@pytest.fixture(autouse=True)
def isolated_argus_environment(tmp_path, monkeypatch):
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'qq': {'enabled': False}}))
    monkeypatch.setenv('ICARUS_DATA_DIR', str(tmp_path / 'runtime_data'))
    for variable in ('ARGUS_JEV_API_KEY', 'ARGUS_QQ_REPORT_SECRET', 'ARGUS_DATABASE_URL'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(config, 'load_icarus_environment', lambda: None)
    monkeypatch.setattr(runtime_environment, 'load_icarus_environment', lambda: None)
    # Accidental default load must fail for missing DSN, not load real settings.
    monkeypatch.setattr(config, 'DEFAULT_SETTINGS_PATH', settings)


@pytest.fixture
async def database():
    dsn = os.getenv('ARGUS_TEST_DATABASE_URL')
    if not dsn or not (make_url(dsn).database or '').endswith('_test'):
        pytest.fail('Set ARGUS_TEST_DATABASE_URL to an isolated database ending in _test')
    schema = f'test_{uuid4().hex}'
    admin = create_async_engine(dsn)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    instance = Database(dsn, schema=schema)
    try:
        await instance.start()
        yield instance
    finally:
        await instance.close()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
