from types import SimpleNamespace

from server.memory_page import memory_page, serialize_memory


def test_filter_and_sort_before_paging_and_keep_facets():
    rows = [dict(id=str(i), memory=f"内容 {i}", user_id="user", run_id="global",
                 updated_at=f"2026-10-01T00:{i:02}:00Z", metadata={"category": "约定"},
                 expiration_date=None) for i in range(30)]
    rows.append(dict(id="stopped", memory="停用", user_id="other", run_id="global",
                     metadata={}, expiration_date="1970-01-01"))
    result = memory_page(rows, page=2, page_size=12, state="active", today="2026-10-04")
    assert [row["id"] for row in result["results"]] == [str(i) for i in range(17, 5, -1)]
    assert result["total"] == 30
    assert result["counts"] == {"all": 31, "active": 30, "expired": 1}
    assert result["users"] == ["other", "user"]
    narrowed = memory_page(rows, query="内容 29", page=8, page_size=12, today="2026-10-04")
    assert narrowed["page"] == 1
    assert [row["id"] for row in narrowed["results"]] == ["29"]


def test_expiry_boundary_unknown_dates_and_empty_page():
    rows = [dict(id="today", memory="边界", updated_at="invalid", metadata={},
                 expiration_date="2026-10-04")]
    assert memory_page(rows, state="active", today="2026-10-04")["total"] == 1
    empty = memory_page(rows, state="expired", today="2026-10-04")
    assert empty["results"] == []
    assert empty["page"] == 1


def test_agent_payload_keeps_source_metadata_and_promotes_identity():
    payload = dict(data="Agent 写入的正文", user_id="configured-user", agent_id="icarus",
                   run_id="workspace:1234567890abcdef", origin="explicit",
                   source_session_id="session-42", source_run_id="execution-42",
                   source_workspace_key="1234567890abcdef", source_operation_id="operation-42",
                   text_lemmatized="internal search text", role="user")
    row = serialize_memory(SimpleNamespace(id="memory-42", payload=payload))
    assert row["memory"] == "Agent 写入的正文"
    assert row["run_id"] == "workspace:1234567890abcdef"
    assert row["user_id"] == "configured-user"
    assert row["role"] == "user"
    assert row["metadata"] == {key: payload[key] for key in (
        "origin", "source_session_id", "source_run_id", "source_workspace_key", "source_operation_id")}
    result = memory_page([row], user_id="configured-user", run_id=row["run_id"])
    assert result["results"] == [row]
    assert result["categories"] == ["未分类"]
