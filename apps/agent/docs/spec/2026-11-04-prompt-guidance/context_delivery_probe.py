"""Offline native-WSL request measurement against an installed checkout.

Run from the checkout root with its Agent Python. Requires dev-only tiktoken.
The only HTTP server is a scripted loopback fixture, never a model or database.
Tokenizers are comparison proxies, not the configured provider's billing counts.
"""

import argparse
import asyncio
import importlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import tempfile
import threading

import tiktoken

from agent_app.application.session_runtime import SessionRuntime
from agent_app.config import ConfigModel
from agent_app.plugins.memory.mem0_http_adapter import Mem0HttpAdapter
from agent_app.plugins.memory.models import MemoryRecallResult
from icarus.core.identity import SessionIdentity
from icarus.core.tools.result_budget import conservative_token_count


FIXTURE_ROOT = ""


def encode(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path.home() / ".cache/icarus-prompt-tokenizers"))
ENCODERS = {name: tiktoken.get_encoding(name) for name in ("cl100k_base", "o200k_base")}


def measure(value):
    text = encode(value)
    if FIXTURE_ROOT:
        text = text.replace(FIXTURE_ROOT, "<fixture-root>")
    return {"characters": len(text), "budget_units": conservative_token_count(text),
            **{name: len(encoder.encode(text, disallowed_special=())) for name, encoder in ENCODERS.items()}}


class ScriptedProvider(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        assert self.path.endswith("/chat/completions"), self.path
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(payload)
        messages = payload["messages"]
        current_user = max(i for i, message in enumerate(messages) if message["role"] == "user")
        tail = messages[current_user + 1:]
        lookup = "LOOKUP-FIXTURE" in encode(messages[current_user]["content"])
        if lookup and not tail:
            name, arguments = "tool_search", {"query": "memory", "name": "memory_recall"}
        elif lookup and tail[-1].get("role") == "tool" and "scope_guidance" in encode(tail[-1]):
            name, arguments = "tool_execute", {"name": "memory_recall", "arguments": {"query": "fixture preference"}}
        elif lookup and len(tail) == 2:
            # Baseline search has no scope_guidance.
            name, arguments = "tool_execute", {"name": "memory_recall", "arguments": {"query": "fixture preference"}}
        else:
            name = None
        delta = ({"tool_calls": [{"index": 0, "id": f"call-{len(self.requests)}", "type": "function",
                  "function": {"name": name, "arguments": encode(arguments)}}]} if name else {"content": "FIXTURE-OK"})
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if name else "stop"}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        ]
        raw = ("".join("data: " + json.dumps({"id": "fixture", "object": "chat.completion.chunk", "created": 1,
                      "model": "fixture", **chunk}) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


async def scenario(root, port, skill_count):
    workspace = root / "workspace"
    workspace.mkdir()
    for number in range(skill_count):
        directory = workspace / "skills" / f"fixture-{number:02d}"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"---\nname: fixture-{number:02d}\ndescription: Specialized fixture workflow {number}\n---\nUSER-SKILL-BODY\n")
    endpoint = f"http://127.0.0.1:{port}"
    model = {"model_name": "fixture", "context_window": 128000, "max_tokens": 100,
             "temperature": 0, "default_think_level": "low"}
    config = ConfigModel(openai_base_url=endpoint + "/v1", anthropic_base_url=endpoint,
        openai_api_key="loopback-fixture-only", icarus_data_dir=root / "data",
        model_settings={"thinking": model, "perception": model},
        runtime={"plugin_config": {"memory": {"user_id": "fixture", "agent_id": "fixture"},
                                   "knowledge": {"knowledge_base": "fixture"}}})
    finished = asyncio.Event()
    updates = []
    async def publish(update):
        updates.append(update)
        if update.type == "task.finished":
            assert update.payload["status"] == "completed", update
            finished.set()
    runtime = SessionRuntime(SessionIdentity.create(workspace, "fixture"), config=config, publish_update=publish)
    await runtime.start()
    try:
        offset = len(ScriptedProvider.requests)
        tasks = []
        for text in ("你好，请简短回答。", "LOOKUP-FIXTURE: 查找我之前的偏好。", "FOLLOWUP-FIXTURE: 谢谢。"):
            begin = len(ScriptedProvider.requests)
            finished.clear()
            await runtime.submit(text, request_options={"local_time": "2026-11-05T12:00:00+08:00", "time_zone": "Asia/Shanghai"})
            await asyncio.wait_for(finished.wait(), 15)
            tasks.append({"user": text, "request_count": len(ScriptedProvider.requests) - begin})
        calls = ScriptedProvider.requests[offset:]
        assert len(calls) == 5, len(calls)
        results = [json.loads(message["content"]) for message in calls[3]["messages"] if message["role"] == "tool"]
        assert len(results) == 2 and all(result["success"] for result in results), results
        if hasattr(runtime.tool_search._catalog, "guidance"):
            assert results[0]["output"]["scope_guidance"] == runtime.tool_search._catalog.guidance("memory")
        systems = [next(message["content"] for message in call["messages"] if message["role"] == "system") for call in calls]
        assert all(system == systems[0] for system in systems)
        assert "你好，请简短回答。" in encode(calls[0]["messages"])
        assert "USER-SKILL-BODY" not in systems[0]
        requests = []
        for call in calls:
            messages = call["messages"]
            latest = max(i for i, message in enumerate(messages) if message["role"] == "user")
            requests.append({
                "serialized_input_proxy": measure({"messages": messages, "tools": call.get("tools", [])}),
                "system_text": measure(next(message["content"] for message in messages if message["role"] == "system")),
                "resident_definitions": measure(call.get("tools", [])),
                "history_messages": measure([message for i, message in enumerate(messages) if message["role"] != "system" and i < latest]),
                "current_user": measure(messages[latest]["content"]),
                "current_run_after_user": measure(messages[latest + 1:]),
            })
        scopes = sorted({entry.plugin_id for entry in runtime.tool_search._catalog.entries})
        searches = {}
        for scope in scopes:
            result = runtime.tool_search.invoke({"query": scope})
            searches[scope] = {"tool_count": len(result.output["tools"]), "response": measure(result.as_dict())}
        return {"skill_count": skill_count, "tasks": tasks, "requests": requests,
                "registered_tools": len(runtime.tool_registry.names()), "all_scope_search_outputs": searches,
                "system_contains_skill_catalog": "fixture-00" in systems[0]}
    finally:
        await runtime.stop("probe")


def main():
    global FIXTURE_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.CRITICAL)
    # Boundaries are production objects; only external recall data is replaced.
    async def arecall(self, query, **kwargs):
        return MemoryRecallResult((), query)
    Mem0HttpAdapter.arecall = arecall
    Mem0HttpAdapter.recall = lambda self, query, **kwargs: MemoryRecallResult((), query)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="prompt-cost-fixture-") as temporary:
            root = Path(temporary)
            FIXTURE_ROOT = str(root)
            scenarios = {}
            for count in (0, 8):
                directory = root / str(count)
                directory.mkdir()
                scenarios[str(count)] = asyncio.run(scenario(directory, server.server_address[1], count))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    surfaces = {}
    for module_name in ("prompt.base_prompt", "builtin_tools.prompts", "memory.prompts", "relationship.prompts",
                        "process.prompts", "knowledge.prompts", "skill.prompts", "mcp.prompts", "persona.prompts",
                        "blackboard.history_compactor", "skill.producer", "skill.evolver"):
        module = importlib.import_module("agent_app.plugins." + module_name)
        for name, text in vars(module).items():
            if name.isupper() and isinstance(text, str):
                surfaces[module_name + "." + name] = measure(text)
    report = {"label": args.label, "tiktoken_version": importlib.metadata.version("tiktoken"),
        "method": "Production SessionRuntime, ReActAgent, executor, history and OpenAI adapter; scripted loopback HTTP responses, empty memory. No paid model or remote service. JEV/relationship snapshots/MCP disabled; empty workspace rules/private instructions. Tools and plugin graph use default configuration.",
        "limits": "Serialized messages/tools tokenizer proxies exclude provider framing, cache/billing and non-OpenAI tokenization. Fixture usage counters are fake and not used. Search output totals are individual cost measurements, not a complete all-scopes trajectory. No inference about model quality.",
        "scenarios": scenarios, "instruction_texts": surfaces}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"label": args.label, "scenarios": {key: [r["serialized_input_proxy"] for r in value["requests"]] for key, value in scenarios.items()}}, indent=2))


if __name__ == "__main__":
    main()
