"""Run a destructive-but-self-cleaning smoke test against local Mem0."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import dotenv_values

from apps.agent.src.agent_orchestration.plugins.memory import Mem0HttpAdapter


def main() -> None:
    env = dotenv_values(REPO_ROOT / ".env", interpolate=False)
    api_key = env.get("ICARUS_MEM0_API_KEY") or ""
    marker = uuid4().hex[:12]
    user_id = f"icarus-smoke-{marker}"
    agent_id = "icarus-smoke"
    content = f"The user's unique verification preference is cobalt-{marker}."
    adapter = Mem0HttpAdapter(
        "http://127.0.0.1:8888", user_id=user_id, agent_id=agent_id,
        api_key=api_key, timeout_seconds=60,
    )
    refs: list[str] = []
    try:
        created = adapter.remember(
            content, workspace_key="smoke-workspace", scope="global",
            metadata={"origin": "smoke", "marker": marker},
        )
        refs = [record.item.ref for record in created]
        if not refs:
            raise RuntimeError("Mem0 did not create a memory")
        query = f"What is the unique verification preference {marker}?"
        latencies = []
        recalled = None
        for _ in range(6):
            started = perf_counter()
            recalled = adapter.recall(
                query, workspace_key="smoke-workspace", scope=None,
                include_stopped=False, top_k=3, threshold=0,
            )
            latencies.append((perf_counter() - started) * 1000)
        assert recalled is not None
        target = next((item for item in recalled.items if item.ref in refs), None)
        if target is None:
            raise RuntimeError("Created memory was not recalled")
        record = adapter.get(target.ref)
        history_before = adapter.history(target.ref)
        corrected_text = f"The user's unique verification preference is amber-{marker}."
        corrected = adapter.correct(target.ref, corrected_text)
        if "amber" not in corrected.item.content:
            raise RuntimeError("Memory correction was not persisted")
        adapter.set_expiration(target.ref, "1970-01-01")
        active = adapter.recall(
            marker, workspace_key="smoke-workspace", scope=None,
            include_stopped=False, top_k=10, threshold=0,
        )
        if target.ref in {item.ref for item in active.items}:
            raise RuntimeError("Stopped memory remained in default recall")
        stopped = adapter.recall(
            marker, workspace_key="smoke-workspace", scope=None,
            include_stopped=True, top_k=10, threshold=0,
        )
        if target.ref not in {item.ref for item in stopped.items}:
            raise RuntimeError("Stopped memory was not available for restore")
        adapter.set_expiration(target.ref, None)
        restored = adapter.recall(
            marker, workspace_key="smoke-workspace", scope=None,
            include_stopped=False, top_k=10, threshold=0,
        )
        if target.ref not in {item.ref for item in restored.items}:
            raise RuntimeError("Restored memory was not recalled")
        history_after = adapter.history(target.ref)
        adapter.delete(target.ref)
        refs.remove(target.ref)
        deleted = adapter.recall(
            marker, workspace_key="smoke-workspace", scope=None,
            include_stopped=True, top_k=10, threshold=0,
        )
        if target.ref in {item.ref for item in deleted.items}:
            raise RuntimeError("Deleted memory remained searchable")
        sorted_latency = sorted(latencies)
        p95 = sorted_latency[max(0, int(len(sorted_latency) * 0.95) - 1)]
        print(
            json.dumps(
                {
                    "status": "ok",
                    "created_count": len(created),
                    "history_before": len(history_before),
                    "history_after": len(history_after),
                    "latency_ms": {
                        "min": round(min(latencies), 2),
                        "median": round(median(latencies), 2),
                        "p95_sample": round(p95, 2),
                        "max": round(max(latencies), 2),
                    },
                },
                ensure_ascii=False,
            )
        )
    finally:
        for ref in refs:
            try:
                adapter.delete(ref)
            except Exception:
                pass
        adapter.close()


if __name__ == "__main__":
    main()
