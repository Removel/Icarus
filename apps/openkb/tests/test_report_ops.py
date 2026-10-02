import pytest
from fastapi.testclient import TestClient

from openkb.api import create_app
from openkb.report_ops import delete_report


def test_report_delete_preserves_knowledge_and_requires_auth(kb_dir, monkeypatch):
    tmp_path = kb_dir
    reports = tmp_path / "wiki" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / "lint_20261001.md"
    report.write_text("report", encoding="utf-8")
    page = tmp_path / "wiki" / "index.md"
    page.write_text("knowledge", encoding="utf-8")
    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", lambda name: tmp_path)
    monkeypatch.setenv("OPENKB_API_TOKEN", "secret")
    client = TestClient(create_app())
    body = {"kb": "test", "path": "reports/lint_20261001.md"}
    headers = {"Authorization": "Bearer secret"}
    listing = client.post("/api/v1/list", json={"kb": "test"}, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["reports"] == ["lint_20261001.md"]
    loaded = client.post("/api/v1/page", json=body, headers=headers)
    assert loaded.json()["content"] == "report"
    assert client.post("/api/v1/report/delete", json=body).status_code == 401
    assert report.exists()
    result = client.post(
        "/api/v1/report/delete", json=body, headers={"Authorization": "Bearer secret"}
    )
    assert result.status_code == 200
    assert result.json()["status"] == "deleted"
    assert not report.exists()
    assert page.read_text(encoding="utf-8") == "knowledge"
    assert (
        client.post(
            "/api/v1/report/delete", json=body, headers={"Authorization": "Bearer secret"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "path", ["concepts/a", "../a", "reports/../index", "reports/.hidden", "reports/a\\b"]
)
def test_report_delete_rejects_other_paths(tmp_path, path):
    with pytest.raises(ValueError):
        delete_report(tmp_path, path)


def test_report_delete_rejects_symlink(tmp_path):
    reports = tmp_path / "wiki" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.md"
    outside.write_text("keep", encoding="utf-8")
    try:
        (reports / "linked.md").symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with pytest.raises(ValueError):
        delete_report(tmp_path, "reports/linked.md")
    assert outside.read_text(encoding="utf-8") == "keep"
