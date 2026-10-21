"""Explicitly authorized smoke against an isolated real rela-mem and Gateway/model.

Run with Gateway's environment (it supplies websockets). Never target user data.
No fake provider is used. --real-model spends up to --model-budget model calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
from uuid import uuid4

import httpx

from agent_app.plugins.relationship.plugin import RelationshipPlugin
from agent_app.plugins.relationship.rela_mem_http_adapter import RelaMemHttpAdapter


def rest_flow(url: str, token: str) -> dict:
    marker = uuid4().hex[:8]
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=url, headers=headers, timeout=10) as client:
        assert client.get("/healthz").json() == {"status": "ready"}
        def create(name, aliases=None):
            response = client.post("/v1/persons", json={"display_name": name, "aliases": aliases or []})
            response.raise_for_status()
            return response.json()["person"]["person_id"]
        a = create(f"验收甲{marker}", [{"namespace": "qq", "value": "901" + str(int(marker, 16))}])
        b = create(f"验收乙{marker}")
        circle = client.post("/v1/circles", json={"name": f"验收圈{marker}", "kind": "test"}).json()["circle"]["circle_id"]
        response = client.put(f"/v1/circles/{circle}/members/{a}", json={"role": "member"})
        response.raise_for_status()
        backend = RelaMemHttpAdapter(url, api_token=token)
        plugin = RelationshipPlugin("relationship", backend)
        tools = {tool.definition.name: tool for tool in plugin.build_tools()}
        def invoke(name, **arguments):
            result = tools["relationship_" + name].invoke(arguments, task_id="smoke", run_id=marker)
            assert result.success, result.error
            return result.output
        try:
            assert invoke("find", query=f"验收甲{marker}")["items"][0]["person_id"] == a
            assert invoke("find", query="qq:901" + str(int(marker, 16)))["items"][0]["person_id"] == a
            assert invoke("remember", person_id=a, profile={"likes": "摄影", "null_value": None})["created"] is False
            two = invoke("relate", from_person_id=a, to_person_id=b, kind="kin", forward_detail="表哥", reverse_detail="表弟")
            one = invoke("relate", from_person_id=b, to_person_id=a, kind="friend", forward_detail="朋友")
            context = invoke("context", person_id=a)
            assert {item["attr_key"] for item in context["profile"]["items"]} == {"likes", "null_value"}
            assert context["circles"]["items"][0]["circle"]["name"] == f"验收圈{marker}"
            assert {item["direction"] for item in context["relations"]["items"]} == {"one_way", "two_way"}
            invoke("forget", person_id=a, keys=["likes"])
            history = client.get(f"/v1/persons/{a}/profile/likes/history").json()["items"]
            assert history[0]["status"] == "deleted" and history[0]["source"] == "agent"
            assert history[0]["source_ref"] == f"agent:smoke:{marker}"
            invoke("relate", relation_id=one["relation"]["relation_id"])
            assert len(invoke("context", person_id=a)["relations"]["items"]) == 1
            assert len(client.get("/v1/relations", params={"person_id": a, "status": "invalid"}).json()["items"]) == 1
            # Name-only confirmation, unique exact vs fuzzy, and a new identity with an old name.
            name = f"确认人物{marker}"
            assert invoke("remember", display_name=name)["status"] == "confirmation_required"
            created = invoke("remember", display_name=name, confirm_create=True)
            assert created["created"] and not invoke("remember", display_name=name)["created"]
            assert invoke("remember", display_name=name, identities=[f"wechat:smoke-{marker}"])["status"] == "confirmation_required"
            fuzzy = f"模糊人物{marker}"
            create(fuzzy + "丰")
            assert invoke("remember", display_name=fuzzy, profile={"likes": "不应写"})["status"] == "confirmation_required"
            exact_alias = f"别名证据{marker}"
            alias_person = create(f"别的显示名{marker}", [{"namespace": "name", "value": f"普通{i}{marker}"} for i in range(10)] + [{"namespace": "name", "value": exact_alias}])
            assert invoke("remember", display_name=exact_alias, profile={"verified": True})["person"]["person_id"] == alias_person
            conflict = tools["relationship_remember"].invoke({"person_id": b, "identities": ["qq:901" + str(int(marker, 16))], "profile": {"wrong_person": True}})
            assert conflict.success and conflict.output["status"] == "confirmation_required"
            assert client.get(f"/v1/persons/{b}/profile").json()["items"] == []
            response = client.put(f"/v1/persons/{a}/profile/huge", json={"value": "x" * 65000, "source": "agent"})
            response.raise_for_status()
            bounded = invoke("context", person_id=a)
            assert bounded["profile"]["truncated"] and len(json.dumps(bounded, ensure_ascii=False)) <= 24000
            # Clear oversized fixtures before asking the actual model for a representative conversation.
            invoke("forget", person_id=a, keys=["huge"])
            return {"status": "passed", "marker": marker, "person_a": a, "person_b": b,
                    "name_a": f"验收甲{marker}", "name_b": f"验收乙{marker}",
                    "relation_id": two["relation"]["relation_id"], "tools": sorted(tools),
                    "checks": ["five_tools", "exact_identity", "name_confirmation", "unique_exact_name", "fuzzy_no_write", "new_identity_existing_name_confirmation", "full_alias_evidence", "identity_conflict_no_write", "circle_context", "two_way_and_one_way", "profile_tombstone", "relation_invalidation", "bounded_context"]}
        finally:
            backend.close()


async def model_flow(url: str, state: dict, budget: int, stop_pid: int | None,
                     evidence_path: Path | None = None, *, offline_only: bool = False,
                     single_task: bool = False) -> dict:
    import signal
    import uvicorn
    import websockets
    from agent_app.application import AgentRuntime
    from agent_app.config import get_config
    from agent_app.model_provider.factory import LLMFactory
    from apps.gateway.src.app import create_app

    # Instrument the public model boundary only; every response still comes from the configured provider.
    original_create = LLMFactory.create_llm
    calls = 0
    def create(factory, *args, **kwargs):
        nonlocal calls
        llm = original_create(factory, *args, **kwargs)
        original_stream, original_invoke = llm.astream, llm.ainvoke
        async def stream(*args, **kwargs):
            nonlocal calls
            if calls >= budget:
                raise RuntimeError("authorized real-model budget exhausted")
            calls += 1
            print(f"Real model request {calls}/{budget}", flush=True)
            async for chunk in original_stream(*args, **kwargs):
                yield chunk
        async def invoke(*args, **kwargs):
            nonlocal calls
            if calls >= budget:
                raise RuntimeError("authorized real-model budget exhausted")
            calls += 1
            return await original_invoke(*args, **kwargs)
        llm.astream, llm.ainvoke = stream, invoke
        return llm
    LLMFactory.create_llm = create
    traces = []
    with tempfile.TemporaryDirectory(prefix="icarus-relationship-e2e-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "acceptance.txt").write_text("relationship-offline-session-ok\n")
        config = get_config().model_copy(deep=True)
        config.icarus_data_dir = root / "data"
        config.mcp_servers = {}
        config.agent.max_steps = 8
        for model in config.model_settings:
            model.max_tokens = min(model.max_tokens, 2048)
        # Exclude unrelated external calls, rather than silently touching the user's Mem0/OpenKB.
        config.runtime.required_plugin_ids = [p for p in config.runtime.required_plugin_ids if p not in {"memory", "knowledge"}]
        config.runtime.plugin_config.pop("memory", None)
        config.runtime.plugin_config.pop("knowledge", None)
        config.runtime.plugin_config["relationship"] = {"endpoint": url, "timeout_seconds": 3}
        runtime = AgentRuntime(config_loader=lambda: config)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        server = uvicorn.Server(uvicorn.Config(create_app(runtime), log_level="error", lifespan="on"))
        started = asyncio.Event()
        original_startup = server.startup
        async def startup(*args, **kwargs):
            await original_startup(*args, **kwargs)
            started.set()
        server.startup = startup
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            await asyncio.wait_for(started.wait(), 15)
            async with websockets.connect(f"ws://127.0.0.1:{sock.getsockname()[1]}/rpc", max_size=4 * 1024 * 1024) as ws:
                request_id = 0
                async def rpc(method, params):
                    nonlocal request_id
                    request_id += 1
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
                    while True:
                        event = json.loads(await asyncio.wait_for(ws.recv(), 180))
                        if event.get("id") == request_id:
                            assert "error" not in event, event
                            return event["result"]
                        if event.get("method") == "runtime.update":
                            traces.append(event["params"])
                params = {"workspace_path": str(workspace), "session_id": "relationship-e2e"}
                created = await rpc("session.create", params)
                await rpc("session.subscribe", {"workspace_key": created["workspace_key"], "session_id": params["session_id"]})
                async def submit(prompt, submission):
                    start = len(traces)
                    await rpc("session.submit", params | {"prompt": prompt, "submission_id": submission})
                    while not any(item["type"] == "task.finished" for item in traces[start:]):
                        event = json.loads(await asyncio.wait_for(ws.recv(), 180))
                        if event.get("method") == "runtime.update":
                            traces.append(event["params"])
                    finished = next(item for item in traces[start:] if item["type"] == "task.finished")
                    assert finished["payload"]["status"] == "completed", finished
                    return traces[start:]
                if offline_only:
                    off = await submit(f"请调用 relationship_find 查询 {state['name_a']}，如果服务离线不要重试。无论查询结果如何，再用 read 工具读取绝对路径 {workspace / 'acceptance.txt'}，告诉我标记。", "offline")
                    failed = any(item["type"] == "tool.completed" and item["payload"].get("tool_name") == "relationship_find" and not item["payload"].get("success") for item in off)
                    read = any(item["type"] == "tool.completed" and item["payload"].get("tool_name") == "read" and item["payload"].get("success") for item in off)
                    assert failed and read
                    await rpc("session.unload", params)
                    return {"status": "passed", "real_model": config.get_model(config.model_roles.agent).model_name,
                            "model_calls": calls, "budget": budget, "offline_new_session_and_other_tool": True}
                if single_task:
                    conversation = await submit(
                        f"这是隔离测试数据，请完成一个任务后简短报告。已知 {state['name_a']} 的 person_id 是 {state['person_a']}。"
                        f"请按以下操作完成：1 用 relationship_find 查询名字；2 relationship_remember 给该ID写 camera_preference=定焦镜头；"
                        f"3 relationship_context 读回；4 我明确要求 relationship_forget 删除 camera_preference；"
                        f"5 我明确要求 relationship_relate 失效关系 {state['relation_id']}。不要重复查询，不要使用其他工具，不删除人物。"
                        "独立操作可在同一轮发出；完成五项后立即简短总结。", "single-task-five-tools")
                    used = {item["payload"].get("tool_name") for item in conversation
                            if item["type"] == "tool.completed" and item["payload"].get("success")}
                    assert {"relationship_find", "relationship_remember", "relationship_context", "relationship_forget", "relationship_relate"} <= used, used
                    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {os.environ['RELA_MEM_API_TOKEN']}"}) as client:
                        history = client.get(f"/v1/persons/{state['person_a']}/profile/camera_preference/history").json()["items"]
                        assert history[0]["status"] == "deleted"
                        assert any(row["attr_value"] == "定焦镜头" and row["status"] == "active" for row in history)
                        invalid = client.get("/v1/relations", params={"person_id": state["person_a"], "status": "invalid"}).json()["items"]
                        assert any(row["relation_id"] == state["relation_id"] for row in invalid)
                    await rpc("session.unload", params)
                    return {"status": "passed", "real_model": config.get_model(config.model_roles.agent).model_name,
                            "model_calls": calls, "budget": budget, "tools_used": sorted(used), "completed_task": True}
                # Natural user requests, not fabricated model responses; data is checked separately below.
                first = await submit(f"请查一下{state['name_a']}。请把TA的稳定偏好 camera_preference=定焦镜头 记入人物档案，再读取TA的完整档案、圈子和关系给我看。", "write-and-read")
                used = {item["payload"].get("tool_name") for item in first if item["type"] == "tool.completed" and item["payload"].get("success")}
                assert {"relationship_find", "relationship_remember", "relationship_context"} <= used, used
                with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {os.environ['RELA_MEM_API_TOKEN']}"}) as client:
                    rows = client.get(f"/v1/persons/{state['person_a']}/profile").json()["items"]
                    assert any(row["attr_key"] == "camera_preference" and row["attr_value"] == "定焦镜头" for row in rows)
                second = await submit(f"请忘掉刚刚那条 camera_preference 档案。另外明确请将关系 {state['relation_id']} 失效，只处理该关系，不要删除人物；完成后报告结果。", "forget-and-invalidate")
                used |= {item["payload"].get("tool_name") for item in second if item["type"] == "tool.completed" and item["payload"].get("success")}
                assert {"relationship_forget", "relationship_relate"} <= used, used
                with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {os.environ['RELA_MEM_API_TOKEN']}"}) as client:
                    history = client.get(f"/v1/persons/{state['person_a']}/profile/camera_preference/history").json()["items"]
                    assert history[0]["status"] == "deleted"
                    invalid = client.get("/v1/relations", params={"person_id": state["person_a"], "status": "invalid"}).json()["items"]
                    assert any(row["relation_id"] == state["relation_id"] for row in invalid)
                await rpc("session.unload", params)
                offline = False
                if stop_pid is not None:
                    os.kill(stop_pid, signal.SIGTERM)
                    for _ in range(100):
                        try:
                            async with httpx.AsyncClient(timeout=0.2) as client:
                                await client.get(url + "/healthz")
                        except httpx.HTTPError:
                            break
                        await asyncio.sleep(0.05)
                    else:
                        raise RuntimeError("owned rela-mem server did not stop")
                    params["session_id"] = "relationship-offline"
                    created = await rpc("session.create", params)
                    await rpc("session.subscribe", {"workspace_key": created["workspace_key"], "session_id": params["session_id"]})
                    off = await submit(f"请查一下{state['name_a']}。如果人物服务不可用，不要重试；请用 read 工具读取工作区的 acceptance.txt，告诉我其中的标记。", "offline")
                    failed = any(item["type"] == "tool.completed" and item["payload"].get("tool_name") == "relationship_find" and not item["payload"].get("success") for item in off)
                    read = any(item["type"] == "tool.completed" and item["payload"].get("tool_name") == "read" and item["payload"].get("success") for item in off)
                    assert failed and read
                    offline = True
                    await rpc("session.unload", params)
            return {"status": "passed", "real_model": config.get_model(config.model_roles.agent).model_name,
                    "model_calls": calls, "budget": budget, "tools_used": sorted(used), "offline_new_session_and_other_tool": offline,
                    "trace": [{"type": row["type"], "payload": row["payload"]} for row in traces if row["type"] in {"tool.started", "tool.completed", "task.finished", "task.usage"}]}
        finally:
            if evidence_path is not None:
                evidence_path.write_text(json.dumps({"model_calls": calls, "budget": budget,
                    "trace": [{"type": row["type"], "payload": row["payload"]} for row in traces
                              if row["type"] in {"tool.started", "tool.completed", "task.finished", "task.error", "task.usage"}]}, ensure_ascii=False, indent=2))
            server.should_exit = True
            await asyncio.wait_for(task, 15)
            sock.close()
            LLMFactory.create_llm = original_create


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--allow-isolated-writes", action="store_true")
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--model-budget", type=int, default=12)
    parser.add_argument("--stop-owned-service-pid", type=int)
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--single-task", action="store_true")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_isolated_writes or not 1 <= args.model_budget <= 12:
        parser.error("isolated write authorization and model budget 1..12 are required")
    token = os.environ.get("RELA_MEM_API_TOKEN", "")
    if not token:
        parser.error("RELA_MEM_API_TOKEN is required")
    if args.offline_only:
        if not args.real_model:
            parser.error("--offline-only requires --real-model")
        state = json.loads(args.report.read_text())["rest"]
    else:
        state = rest_flow(args.url, token)
    report = {"rest": state}
    if not args.offline_only:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("Real REST/tool acceptance passed", flush=True)
    if args.real_model:
        report["e2e"] = asyncio.run(model_flow(args.url, state, args.model_budget, args.stop_owned_service_pid,
                                                args.evidence or args.report.with_suffix(".evidence.json"),
                                                offline_only=args.offline_only, single_task=args.single_task))
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Real Gateway/model E2E passed ({report['e2e']['model_calls']} model calls)", flush=True)


if __name__ == "__main__":
    main()
