"""Real SQL regression. Use only an explicitly isolated test database."""

import os
from uuid import UUID, uuid4

import pytest

from mem0.vector_stores.pgvector import PGVector
from server.memory_page import memory_page, serialize_memory


@pytest.fixture
def store():
    dsn = os.environ.get("MEM0_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MEM0_TEST_POSTGRES_DSN must point to the isolated browser test database")
    from psycopg.conninfo import conninfo_to_dict

    assert conninfo_to_dict(dsn)["dbname"] == "icarus_memory_browser_test"
    target = PGVector(None, "browser_" + uuid4().hex, 3, None, None, None, None, False, False,
                      connection_string=dsn)
    target._ensure_collection()
    try:
        yield target
    finally:
        target.delete_col()
        target.connection_pool.close()


def seed(store):
    # The only matching row is beyond the old, unordered 1000-row snapshot.
    ids = [str(UUID(int=i + 1)) for i in range(1005)]
    payloads = [dict(data=f"内容 {i}", user_id="first", run_id="global",
                     created_at="2026-10-01T00:00:00Z", category="约定") for i in range(1005)]
    payloads[-1].update(data="唯一的中文 ' % _ 引用", user_id="second", run_id="workspace:other",
                        updated_at="2026-11-06T00:00:00+08:00", category="项目")
    payloads[-2].update(expiration_date="1970-01-01", updated_at="invalid")
    payloads[-3].update(expiration_date="2026-11-06", updated_at="2026-11-05T17:00:00Z")
    store.insert([[0.1, 0.2, 0.3]] * len(ids), payloads, ids)
    return [serialize_memory(type("Row", (), {"id": i, "payload": p})()) for i, p in zip(ids, payloads)]


def test_complete_dataset_is_filtered_and_sorted_before_page(store):
    rows = seed(store)
    for options in ({}, {"descending": False}, {"page": 999},
                    {"user_id": "second"}, {"run_id": "workspace:other"},
                    {"category": "项目"}, {"query": "中文 ' % _"},
                    {"state": "active"}, {"state": "expired"}, {"query": "不存在"}):
        actual = store.browse_memories(today="2026-11-06", **options)
        actual["results"] = [serialize_memory(row) for row in actual["results"]]
        assert actual.pop("truncated") is False
        assert actual == memory_page(rows, today="2026-11-06", **options)
    assert store.browse_memories()["total"] == 1005


def test_empty_database_is_a_complete_first_page(store):
    result = store.browse_memories(page=42)
    assert result == dict(results=[], page=1, page_size=12, total=0,
                          counts=dict(all=0, active=0, expired=0),
                          categories=[], users=[], scopes=[], truncated=False)
