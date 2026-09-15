"""Run an upload, list, read, query, and recompile smoke against OpenKB."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import dotenv_values

from apps.agent.src.agent_orchestration.plugins.knowledge import (
    KnowledgePlugin,
    OpenKBHttpAdapter,
)


def main() -> None:
    env = dotenv_values(REPO_ROOT / ".env", interpolate=False)
    token = env.get("ICARUS_OPENKB_API_TOKEN") or ""
    knowledge_base = env.get("ICARUS_OPENKB_KNOWLEDGE_BASE") or "icarus-project"
    marker = uuid4().hex[:12]
    content = (
        "# Icarus smoke fact\n\n"
        f"The unique OpenKB verification marker is aurora-{marker}.\n"
    )
    adapter = OpenKBHttpAdapter(
        "http://127.0.0.1:7566",
        knowledge_base=knowledge_base,
        api_token=token,
    )
    plugin = None
    try:
        with tempfile.TemporaryDirectory(prefix="icarus-openkb-smoke-") as temp:
            workspace = Path(temp)
            plugin = KnowledgePlugin(
                "knowledge", adapter, workspace_path=workspace
            )
            source = workspace / f"icarus-smoke-{marker}.md"
            source.write_text(content, encoding="utf-8")
            started = perf_counter()
            uploaded = plugin.upload((source.name,))
            upload_ms = (perf_counter() - started) * 1000
            if uploaded["added_count"] != 1:
                raise RuntimeError(f"OpenKB upload failed: {uploaded}")
            catalog = adapter.list()
            document = next(
                (item for item in catalog.documents if item.name == source.name), None
            )
            if document is None:
                raise RuntimeError("Uploaded document is missing from knowledge_list")
            page_path = next(
                (path for path in catalog.summaries if marker in path), None
            )
            if page_path is None:
                raise RuntimeError("Compiled summary page is missing")
            page = adapter.read(page_path)
            if marker not in page.content:
                raise RuntimeError("Compiled page does not contain the marker")
            started = perf_counter()
            answer = adapter.query(f"What is the verification marker {marker}?")
            query_ms = (perf_counter() - started) * 1000
            if f"aurora-{marker}" not in answer.answer:
                raise RuntimeError("Knowledge query did not recover the marker")
            recompiled = adapter.recompile(
                document=source.stem,
                all_documents=False,
                refresh_schema=False,
            )
            if recompiled.recompiled != 1:
                raise RuntimeError("OpenKB did not recompile the uploaded document")
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "document": source.name,
                        "page": page.path,
                        "upload_ms": round(upload_ms, 2),
                        "query_ms": round(query_ms, 2),
                        "recompiled": recompiled.recompiled,
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        if plugin is None:
            adapter.close()
        else:
            import asyncio

            asyncio.run(plugin.stop())


if __name__ == "__main__":
    main()
