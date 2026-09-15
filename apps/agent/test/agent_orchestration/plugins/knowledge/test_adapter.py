import json

import httpx
import pytest

from apps.agent.src.agent_orchestration.plugins.knowledge import (
    KnowledgeBackendError,
    KnowledgeUploadSource,
    OpenKBHttpAdapter,
)


def make_adapter(handler):
    return OpenKBHttpAdapter(
        "http://openkb.test",
        knowledge_base="icarus-project",
        api_token="secret-token",
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://openkb.test",
            headers={"Authorization": "Bearer secret-token"},
        ),
    )


def test_adapter映射query_list_read且固定非流式不保存():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer secret-token"
        if request.url.path == "/api/v1/query":
            return httpx.Response(200, json={"answer": "grounded answer"})
        if request.url.path == "/api/v1/list":
            return httpx.Response(
                200,
                json={
                    "documents": [
                        {
                            "hash": "abc", "name": "guide.md",
                            "type": "md", "display_type": "Markdown",
                            "pages": None,
                        }
                    ],
                    "document_count": 1,
                    "summaries": ["guide"],
                    "concepts": ["runtime"],
                    "entities": [],
                    "reports": [],
                },
            )
        return httpx.Response(
            200, json={"path": "summaries/guide", "content": "content"}
        )

    adapter = make_adapter(handler)
    assert adapter.query("What is Icarus?").answer == "grounded answer"
    catalog = adapter.list()
    assert catalog.documents[0].ref == "knowledge-document:abc"
    assert catalog.summaries == ("summaries/guide",)
    assert catalog.concepts == ("concepts/runtime",)
    assert adapter.read("summaries/guide").content == "content"
    query_payload = json.loads(requests[0].content)
    assert query_payload == {
        "kb": "icarus-project",
        "question": "What is Icarus?",
        "stream": False,
        "save": False,
    }
    assert [request.url.path for request in requests] == [
        "/api/v1/query", "/api/v1/list", "/api/v1/page"
    ]
    adapter.close()


def test_adapter上传使用multipart且不返回正文(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("private body", encoding="utf-8")

    def handler(request):
        assert request.url.path == "/api/v1/add"
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b'private body' in request.content
        assert b'name=\"stream\"' in request.content
        assert b"false" in request.content
        return httpx.Response(
            200,
            json={
                "kb": "icarus-project",
                "files": [
                    {
                        "original_name": "knowledge.md",
                        "saved_path": "/data/raw/knowledge.md",
                        "status": "added",
                        "message": "Compiled.",
                    }
                ],
                "added_count": 1,
                "skipped_count": 0,
                "failed_count": 0,
            },
        )

    adapter = make_adapter(handler)
    with source.open("rb") as stream:
        output = adapter.upload(
            (KnowledgeUploadSource(source.name, stream),)
        ).as_dict()
    assert output == {
        "files": [
            {"name": "knowledge.md", "status": "added", "message": "Compiled."}
        ],
        "added_count": 1,
        "skipped_count": 0,
        "failed_count": 0,
    }
    assert "private body" not in str(output)
    adapter.close()


def test_adapter重编译映射逐文档结果和歧义候选():
    responses = iter(
        [
            httpx.Response(
                200,
                json={
                    "status": "done", "total": 1, "recompiled": 1,
                    "skipped": 0,
                    "candidates": None,
                    "docs": [
                        {
                            "name": "guide.md", "doc_name": "guide",
                            "type": "md", "status": "recompiled",
                            "elapsed": 1.25, "message": "done",
                        }
                    ],
                },
            ),
            httpx.Response(
                409,
                json={
                    "detail": {
                        "message": "multiple matches",
                        "candidates": [{"name": "guide.md", "doc_name": "guide"}],
                    }
                },
            ),
        ]
    )
    adapter = make_adapter(lambda request: next(responses))
    result = adapter.recompile(
        document="guide", all_documents=False, refresh_schema=True
    )
    assert result.documents[0].document == "guide"
    assert result.documents[0].elapsed_seconds == pytest.approx(1.25)
    ambiguous = adapter.recompile(
        document="guide", all_documents=False, refresh_schema=False
    )
    assert ambiguous.status == "ambiguous"
    assert ambiguous.candidates[0]["doc_name"] == "guide"
    adapter.close()


def test_adapter服务端错误脱敏且不泄露上游详情():
    adapter = make_adapter(
        lambda request: httpx.Response(
            500, json={"detail": "upstream failed with secret-token"}
        )
    )
    with pytest.raises(KnowledgeBackendError) as error:
        adapter.query("question")
    assert "HTTP 500" in str(error.value)
    assert "secret-token" not in str(error.value)
    adapter.close()


def test_adapter重编译网络错误统一为安全后端错误():
    def handler(request):
        raise httpx.ConnectError("connection failed with secret-token", request=request)

    adapter = make_adapter(handler)
    with pytest.raises(KnowledgeBackendError, match="service is unavailable"):
        adapter.recompile(
            document="guide", all_documents=False, refresh_schema=False
        )
    adapter.close()
