"""Administrative browser authentication and complete-pagination delegation."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from mem0.vector_stores.pgvector import PGVector


@pytest.fixture
def browser(monkeypatch):
    # All server state is mocked before importing: no production DB or model IO.
    monkeypatch.setenv("JWT_SECRET", "isolated-browser-test-secret")
    import auth
    monkeypatch.setattr(auth, "JWT_SECRET", "isolated-browser-test-secret")
    with patch("mem0.Memory.from_config", return_value=MagicMock()), patch("db.SessionLocal", MagicMock()):
        import server.main as main
    store = MagicMock(spec=PGVector)
    store.browse_memories.return_value = dict(
        results=[SimpleNamespace(id="row", payload=dict(data="新记录", user_id="second"))],
        page=2, page_size=12, total=1005, counts=dict(all=1005, active=1005, expired=0),
        categories=["未分类"], users=["second"], scopes=["global"], truncated=False,
    )
    monkeypatch.setattr(main, "get_memory_instance", lambda: SimpleNamespace(vector_store=store))
    monkeypatch.setattr(main, "SessionLocal", MagicMock())
    main.app.dependency_overrides[main.verify_auth] = lambda: SimpleNamespace(role="admin")
    try:
        yield main, store, TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


def test_complete_page_forwards_filters_and_serializes(browser):
    _, store, client = browser
    response = client.get("/memories/page", params=dict(page=2, user_id="second", query="中文 % _", descending=False))
    assert response.status_code == 200
    value = response.json()
    assert value["total"] == 1005 and value["truncated"] is False
    assert value["results"][0]["user_id"] == "second"
    store.browse_memories.assert_called_once_with(page=2, page_size=12, query="中文 % _",
        category=None, user_id="second", run_id=None, state="all", descending=False)


def test_non_admin_cannot_browse_other_users(browser):
    main, store, client = browser
    main.app.dependency_overrides[main.verify_auth] = lambda: SimpleNamespace(role="user")
    assert client.get("/memories/page").status_code == 403
    store.browse_memories.assert_not_called()


def test_unsupported_store_is_explicit(browser, monkeypatch):
    main, _, client = browser
    monkeypatch.setattr(main, "get_memory_instance", lambda: SimpleNamespace(vector_store=object()))
    assert client.get("/memories/page").status_code == 501
