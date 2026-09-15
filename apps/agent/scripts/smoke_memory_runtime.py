"""Verify SessionRuntime automatic recall against the real local Mem0."""

from __future__ import annotations

import asyncio
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

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent, AgentResponse,
)
from apps.agent.src.agent_orchestration.plugins.memory import Mem0HttpAdapter
from apps.agent.src.agent_orchestration.plugins.persistence import SessionIdentity
from apps.agent.src.application.session_runtime import SessionRuntime
from apps.agent.src.model_config import ConfigModel, LLMConfig, ModelSettings, ThinkMode
from apps.agent.src.model_provider.types import Message, TextPart, Usage


class CapturingAgent:
    def __init__(self) -> None:
        self.runtime_context = ""
        self.input_prompt = ""

    async def astream(self, **kwargs):
        self.input_prompt = kwargs["input_prompt"]
        batch = kwargs["run_control"].drain_context(applied_before_step=1)
        self.runtime_context = (
            batch.message.content[0].text if batch is not None else ""
        )
        message = Message("assistant", [TextPart("runtime smoke complete")])
        yield AgentCompletedEvent(
            step=1,
            response=AgentResponse(
                message=message, usage=Usage(10, 2), last_usage=Usage(10, 2),
                finish_reason="stop", steps=1,
                messages=[Message("user", [TextPart(self.input_prompt)]), message],
                task_message_start=0,
            ),
        )


async def run() -> dict:
    env = dotenv_values(REPO_ROOT / ".env", interpolate=False)
    api_key = env.get("ICARUS_MEM0_API_KEY") or ""
    marker = uuid4().hex[:12]
    user_id = f"runtime-smoke-{marker}"
    adapter = Mem0HttpAdapter(
        "http://127.0.0.1:8888", user_id=user_id, agent_id="icarus-smoke",
        api_key=api_key, timeout_seconds=60,
    )
    created = adapter.remember(
        f"Remember the runtime marker nebula-{marker}.",
        workspace_key="ignored", scope="global",
        metadata={"origin": "runtime-smoke"},
    )
    refs = [record.item.ref for record in created]
    if not refs:
        raise RuntimeError("Unable to seed runtime smoke memory")
    try:
        with tempfile.TemporaryDirectory(prefix="icarus-memory-runtime-") as temp:
            root = Path(temp)
            model = LLMConfig(
                model_name="stub", context_window=128000, max_tokens=1024,
                temperature=0, default_think_level=ThinkMode.LOW,
            )
            config = ConfigModel(
                openai_base_url="http://unused",
                anthropic_base_url="http://unused",
                icarus_data_dir=root / "data",
                runtime={
                    "plugin_config": {
                        "memory": {
                            "user_id": user_id, "agent_id": "icarus-smoke",
                            "recall": {"threshold": 0, "deadline_ms": 1000},
                    },
                    "knowledge": {"knowledge_base": "icarus-project"},
                    }
                },
                model_settings=ModelSettings(thinking=model, perception=model),
            )
            runtime = SessionRuntime(
                SessionIdentity.create(root / "workspace", "session"),
                config=config, publish_update=lambda update: asyncio.sleep(0),
            )
            await runtime.start()
            agent = CapturingAgent()
            runtime.runtime_host.get_plugin("agent").agent_factory.get_agent = (
                lambda role: agent
            )
            started = perf_counter()
            await runtime.submit(f"What is the runtime marker {marker}?")
            for _ in range(300):
                if not runtime.snapshot().has_work:
                    break
                await asyncio.sleep(0.01)
            elapsed_ms = (perf_counter() - started) * 1000
            blackboard = runtime.runtime_host.get_plugin("blackboard")
            region = blackboard.region_store.get("memory")
            conversation = blackboard.get_messages()
            await runtime.checkpoint()
            state_path = (
                runtime.persistence.resolver.session_dir(runtime.identity)
                / "plugin-state" / "blackboard.json"
            )
            state_text = state_path.read_text(encoding="utf-8")
            await runtime.stop("smoke", timeout=5)
            if f"nebula-{marker}" not in agent.runtime_context:
                raise RuntimeError("Real memory was not injected before model step 1")
            if region.state.status != "idle" or not region.complete_for_input:
                raise RuntimeError("Memory Region did not reach complete idle state")
            if f"nebula-{marker}" in state_text:
                raise RuntimeError("Recalled memory leaked into Product Conversation state")
            if [message.role for message in conversation] != ["user", "assistant"]:
                raise RuntimeError("Product Conversation contains execution messages")
            return {
                "status": "ok", "elapsed_ms": round(elapsed_ms, 2),
                "region_status": region.state.status,
                "region_refs": len(region.output.refs if region.output else ()),
                "conversation_roles": [message.role for message in conversation],
            }
    finally:
        for ref in refs:
            try:
                adapter.delete(ref)
            except Exception:
                pass
        adapter.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False))
