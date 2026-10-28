"""Real sockets, production plugins/SQLite/LLM adapter, isolated local HTTP provider."""

import asyncio
import json
import socket

import uvicorn
import websockets

from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app
from apps.gateway.test.test_runtime_e2e import ProviderHandler, config_for, exchange, local_provider


PRIVATE = "\nCALLER-PRIVATE-指令\n"


def write_contributor(root):
    directory = root / "prompt-producer"
    package = directory / "prompt_producer"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "factory.py").write_text('''
from agent_app.contracts.prompt import PromptSection, PromptContributionEvent
from icarus.core.contracts.input import UserInputEvent
from icarus.core.plugin import BasePlugin, handles
from icarus.core.registration import PluginRegistration

class Contributor(BasePlugin):
    def static_prompt_sections(self):
        return (PromptSection("identity", "persona", "PERSONA-FROM-PLUGIN"),)

    @handles(UserInputEvent)
    async def on_input(self, source, event):
        return (PromptContributionEvent(task_id=event.task_id, status="completed",
            sections=(PromptSection("task", "mode", "DYNAMIC-FROM-PLUGIN"),)),)

def create_plugin(plugin_id, **kwargs):
    return PluginRegistration(plugin=Contributor(plugin_id))
''')
    (directory / "manifest.json").write_text(json.dumps({
        "schema_version": 2, "plugin_id": "prompt-producer", "plugin_version": "1.0.0",
        "entrypoint": "prompt_producer.factory:create_plugin", "contracts": "agent_app.contracts.prompt",
        "tier": "external", "python_requires": [], "required_capabilities": [], "provided_capabilities": [],
        "provided_tools": [], "published_events": ["prompt.contribution"], "consumed_events": ["input.user_input"],
        "state_scopes": [],
    }))


async def phase(config, workspace, *, first):
    runtime = AgentRuntime(config_loader=lambda: config)
    server = uvicorn.Server(uvicorn.Config(create_app(runtime), log_level="error", lifespan="on"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    started = asyncio.Event()
    original = server.startup
    async def startup(*args, **kwargs):
        await original(*args, **kwargs)
        started.set()
    server.startup = startup
    task = asyncio.create_task(server.serve(sockets=[sock]))
    wire = []
    params = {"workspace_path": str(workspace), "session_id": "private"}
    try:
        await asyncio.wait_for(started.wait(), 10)
        async with websockets.connect(f"ws://127.0.0.1:{sock.getsockname()[1]}/rpc") as ws:
            if first:
                result, notices = await exchange(ws, 1, "session.create", params | {"load_runtime": False, "instructions": PRIVATE})
                assert result["lifecycle"] == "unloaded"
                wire.extend(notices)
                empty, _ = await exchange(ws, 2, "session.get_history", params)
                assert empty["records"] == [] and empty["history_cursor"] == 0
            status, _ = await exchange(ws, 3, "session.get", params)
            await exchange(ws, 4, "session.subscribe", {"workspace_key": status["workspace_key"], "session_id": "private"})
            _, notices = await exchange(ws, 5, "session.submit", params | {"prompt": "hello", "submission_id": "first" if first else "second"})
            wire.extend(notices)
            while True:
                notice = json.loads(await asyncio.wait_for(ws.recv(), 10))
                wire.append(notice)
                if notice.get("method") == "runtime.update" and notice["params"]["type"] == "task.finished":
                    assert notice["params"]["payload"]["status"] == "completed"
                    break
            history, notices = await exchange(ws, 6, "session.get_history", params)
            wire.extend(notices)
            wire.append(history)
            assert [row["sequence"] for row in history["records"]] == list(range(1, history["history_cursor"] + 1))
            assert PRIVATE not in json.dumps(wire, ensure_ascii=False)
            assert "session.instructions" not in json.dumps(wire)
            result, _ = await exchange(ws, 7, "session.unload", params)
            assert result["status"] == "unloaded"
            return history["history_cursor"]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


def test_prompt_contributor_to_model_and_private_instructions_survive_real_gateway_restart(tmp_path):
    extensions = tmp_path / "extensions"
    write_contributor(extensions)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("WORKSPACE-FROM-FILE")
    with local_provider() as port:
        config = config_for(tmp_path / "data", port)
        config.runtime.plugin_dirs = [extensions]
        config.runtime.plugin_config["prompt"] = {"required_contributors": ["prompt-producer"]}
        first_cursor = asyncio.run(phase(config, workspace, first=True))
        (workspace / "AGENTS.md").write_text("WORKSPACE-UPDATED")
        second_cursor = asyncio.run(phase(config, workspace, first=False))
    assert second_cursor > first_cursor
    calls = [payload for path, payload in ProviderHandler.requests if path.endswith("/chat/completions")]
    assert len(calls) == 2
    for index, call in enumerate(calls):
        system = next(message["content"] for message in call["messages"] if message["role"] == "system")
        assert "PERSONA-FROM-PLUGIN" in system
        assert PRIVATE in system
        assert system.index("PERSONA-FROM-PLUGIN") < system.index(PRIVATE)
        assert ("WORKSPACE-FROM-FILE" if index == 0 else "WORKSPACE-UPDATED") in system
        assert any("DYNAMIC-FROM-PLUGIN" in str(message["content"]) for message in call["messages"] if message["role"] == "user")


def test_builtin_plugin_guidance_reaches_system_once_not_user_context(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill_dir = workspace / "skills" / "e2e-guide"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: e2e-guide\ndescription: test procedure\n---\nBODY-NOT-AUTO-INJECTED\n")
    with local_provider() as port:
        config = config_for(tmp_path / "data", port)
        config.mcp_servers = {"configured-only": {"url": f"http://127.0.0.1:{port}/mcp"}}
        config.runtime.plugin_config["prompt"] = {"required_contributors": ["memory", "skill", "process", "knowledge", "mcp", "builtin-tools"]}
        asyncio.run(phase(config, workspace, first=True))
    call = next(payload for path, payload in ProviderHandler.requests if path.endswith("/chat/completions"))
    system = next(message["content"] for message in call["messages"] if message["role"] == "system")
    user = next(message["content"] for message in call["messages"] if message["role"] == "user")
    for section in ("builtin-tool-use", "knowledge-use", "mcp-use", "memory-use", "process-use", "skill-discovery"):
        assert f"## session/" in system
        assert section in system
        assert section not in user
    assert "你自己的长期记忆" in system
    assert "mcp_tool_search" in system
    assert "当前未开放" in system
    assert "e2e-guide" in system
    assert "BODY-NOT-AUTO-INJECTED" not in system
    assert not any(path.endswith("/mcp") for path, _ in ProviderHandler.requests)
