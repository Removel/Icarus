"""Application composition: reuse Icarus Host; repositories are ordinary objects."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import hashlib
import hmac
import json
import logging
from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import httpx

from icarus.runtime.host import PluginRuntimeHost
from argus_app.config import ArgusConfig

LOG = logging.getLogger("argus.application")


class ControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    binding_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any]


class CreateChannelBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=4000)
    source_keys: list[str] = Field(min_length=1, max_length=16)
    rule_source: str = Field(default='def main(event, channel):\n    return {"passed": False, "reason": "waiting for a configured rule"}\n', max_length=32768)


async def recovery_scan_once(host, store) -> list[str]:
    """Recover independent channels even if one evaluator or remote is unavailable."""
    errors = []
    try:
        await host.get_plugin("workflow-rules").scan_once()
    except Exception as error:
        errors.append(type(error).__name__)
        LOG.exception("Argus rule recovery failed; continuing delivery recovery")
    bridge = host.get_plugin("icarus-bridge")
    for channel in await store.channels():
        if channel.lifecycle != "active" and not channel.runtime.get("round"):
            continue
        try:
            await bridge.deliver_channel(channel.id)
        except Exception as error:
            errors.append(type(error).__name__)
            LOG.exception("Argus delivery recovery failed for %s", channel.id)
    return errors


def create_app(config: ArgusConfig | None = None, *, store=None, semantic=None,
               bridge_client=None, evidence_backends: dict | None = None,
               discovery_fallback=None, channel_namer=None) -> FastAPI:
    config = config or ArgusConfig.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Register ownership immediately, including resources created before host
        # startup, so invalid configuration and partial startup also clean up.
        async with AsyncExitStack() as resources:
            from argus_app.infrastructure.store import Store
            from argus_app.rule_sdk import RuleFiles, RuleEngine
            from argus_app.plugins.icarus_bridge.client import BridgeClient
            current_store = store or Store(config.database_url.get_secret_value())
            resources.push_async_callback(current_store.close)
            client = await resources.enter_async_context(httpx.AsyncClient(base_url=config.gateway_url,
                headers={"Authorization": f"Bearer {config.gateway_token.get_secret_value()}"}, timeout=10))
            files = RuleFiles(config.data_dir / "rules")
            engine = RuleEngine(timeout=config.rule_timeout)
            current_bridge = bridge_client or BridgeClient(client)
            plugin_ids = ("qq-protocol", "feishu-protocol", "agent-protocol", "workflow-rules",
                          "icarus-bridge", "observation-query", "mechanical-rules", "semantic-judgement",
                          "memory-evidence", "relationship-evidence", "knowledge-evidence")
            configs = {name: {"store": current_store} for name in plugin_ids}
            configs["workflow-rules"].update(engine=engine, rule_files=files,
                discovery_enabled=config.discovery_enabled, discovery_rule_path=config.discovery_rule_path)
            configs["agent-protocol"].update(rule_files=files)
            current_semantic = semantic
            if current_semantic is None and config.semantic_api_key.get_secret_value():
                from argus_app.model_provider.semantic import configured_backend
                current_semantic, owner = configured_backend(config.semantic_api_key.get_secret_value(),
                    config.semantic_base_url, config.semantic_model)
                resources.push_async_callback(owner.aclose)
            configs["semantic-judgement"].update(semantic=current_semantic)
            if config.safety_review_enabled:
                if not config.semantic_api_key.get_secret_value():
                    raise ValueError('Safety review enabled without configured JEV credentials')
                from argus_app.model_provider.semantic import _TypedClient
                from argus_app.model_provider.safety import RuleSafetyReview
                safety_owner = _TypedClient(config.semantic_api_key.get_secret_value(), config.semantic_base_url, config.semantic_model)
                resources.push_async_callback(safety_owner.aclose)
                configs['workflow-rules']['safety_review'] = RuleSafetyReview(safety_owner, budget=config.safety_review_budget)
            if config.discovery_fallback_enabled:
                current_fallback = discovery_fallback
                if current_fallback is None:
                    from argus_app.model_provider.discovery_fallback import configured_discovery_fallback
                    current_fallback, fallback_owner = configured_discovery_fallback(
                        config.discovery_fallback_model, config.discovery_fallback_settings_path)
                    resources.push_async_callback(fallback_owner.aclose)
                configs['workflow-rules']['discovery_fallback'] = current_fallback
            if config.channel_naming_enabled:
                current_namer = channel_namer
                if current_namer is None:
                    try:
                        from argus_app.model_provider.channel_naming import configured_channel_namer
                        current_namer, naming_owner = configured_channel_namer(
                            config.channel_naming_model, config.channel_naming_settings_path)
                    except (ValueError, OSError):
                        # Naming is cosmetic: missing model configuration must not stop ingress.
                        LOG.warning('Channel naming unavailable; preserving fallback titles')
                    else:
                        resources.push_async_callback(naming_owner.aclose)
                configs['workflow-rules']['channel_namer'] = current_namer
            from argus_app.rule_sdk.guide import rule_guide
            guide = rule_guide(config)
            guide['capabilities']['semantic']['configured'] = current_semantic is not None
            configs["icarus-bridge"].update(bridge_client=current_bridge, argus_url=config.public_url,
                rule_files=files, guide=guide)
            endpoints = (("memory-evidence", config.memory_endpoint, config.memory_token),
                         ("relationship-evidence", config.relationship_endpoint, config.relationship_token),
                         ("knowledge-evidence", config.knowledge_endpoint, config.knowledge_token))
            for name, endpoint, token in endpoints:
                if endpoint:
                    headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token.get_secret_value() else {}
                    backend = await resources.enter_async_context(httpx.AsyncClient(
                        base_url=endpoint.rstrip("/"), headers=headers, timeout=5))
                    configs[name]["client"] = backend
            configs["memory-evidence"].update(user_id=config.memory_user_id, agent_id=config.memory_agent_id,
                workspace_key=config.memory_workspace_key)
            configs["knowledge-evidence"]["kb"] = config.knowledge_base
            for name, backend in (evidence_backends or {}).items():
                if name not in configs:
                    raise ValueError(f"Unknown evidence plugin: {name}")
                configs[name]["client"] = backend
            host = PluginRuntimeHost(config.data_dir, "argus-runtime", builtin_package="argus_app.plugins",
                required_plugin_ids=frozenset(plugin_ids), plugin_configs=configs)
            resources.push_async_callback(host.stop)
            app.state.store, app.state.rule_files = current_store, files
            app.state.host, app.state.rule_engine, app.state.rule_guide = host, engine, guide
            task = None
            try:
                await current_store.start()
                await host.start()
                app.state.ready = True
                if config.auto_scan:
                    async def scan():
                        while app.state.ready:
                            try:
                                errors = await recovery_scan_once(host, current_store)
                                app.state.scan_error = errors[-1] if errors else None
                            except asyncio.CancelledError:
                                raise
                            except Exception as error:
                                app.state.scan_error = type(error).__name__
                                LOG.exception("Argus recovery scan failed")
                            await asyncio.sleep(config.scan_interval)
                    task = asyncio.create_task(scan(), name="argus-recovery-scan")
                yield
            finally:
                app.state.ready = False
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="Argus", lifespan=lifespan)
    app.state.ready = False
    app.state.scan_error = None

    async def body(request: Request) -> tuple[bytes, dict]:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > config.max_body_bytes:
                raise HTTPException(413, "request_too_large")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise HTTPException(422, "invalid_json") from None
        if not isinstance(value, dict):
            raise HTTPException(422, "object_required")
        return bytes(data), value

    def ready():
        if not app.state.ready:
            raise HTTPException(503, "not_ready")

    def authorized(request: Request):
        ready()
        expected = f"Bearer {config.agent_token.get_secret_value()}"
        if not config.agent_token.get_secret_value() or not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            raise HTTPException(401, "unauthorized")

    async def guarded(operation):
        from argus_app.contracts.models import DomainError
        try:
            return await operation
        except DomainError as error:
            code = error.code
            status = 404 if code in {"not_found", "channel_not_found", "observation_not_found"} else 409
            if code.startswith('qq_'):
                status = 422
            if code in {"unauthorized", "binding_mismatch", "source_forbidden"}:
                status = 403
            raise HTTPException(status, code) from None
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "invalid_protocol_payload") from None

    @app.get("/health")
    async def health():
        if not app.state.ready:
            raise HTTPException(503, "not_ready")
        isolation = await asyncio.to_thread(app.state.rule_engine.launcher.probe)
        return {"status": "ready", "scan_error": app.state.scan_error,
                'rule_execution_available': isolation.available, 'rule_execution_error': isolation.reason}

    @app.post("/protocols/qq")
    async def qq(request: Request):
        ready()
        raw, value = await body(request)
        secret = config.qq_token.get_secret_value()
        if not secret:
            raise HTTPException(503, "qq_not_configured")
        signature = "sha1=" + hmac.new(secret.encode(), raw, hashlib.sha1).hexdigest()
        if not hmac.compare_digest(request.headers.get("x-signature", ""), signature):
            raise HTTPException(401, "invalid_signature")
        event_type = value.get('post_type', 'message')
        if not isinstance(event_type, str):
            raise HTTPException(422, 'qq_event_type_invalid')
        if event_type in {'meta_event', 'notice', 'request'}:
            return {'accepted': True, 'ignored': True, 'reason': 'unsupported_event_type'}
        if event_type not in {'message', 'message_sent'}:
            raise HTTPException(422, 'qq_event_type_invalid')
        observation = await guarded(app.state.host.get_plugin("qq-protocol").receive(value))
        return {"observation_id": observation.id}

    @app.post("/protocols/feishu")
    async def feishu(request: Request):
        ready()
        raw, value = await body(request)
        verification = config.feishu_verification_token.get_secret_value()
        if not verification:
            raise HTTPException(503, "feishu_not_configured")
        token = value.get("token") or value.get("header", {}).get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token, verification):
            raise HTTPException(401, "invalid_verification_token")
        encrypt_key = config.feishu_encrypt_key.get_secret_value()
        if encrypt_key:
            timestamp = request.headers.get("x-lark-request-timestamp", "")
            nonce = request.headers.get("x-lark-request-nonce", "")
            try:
                timely = abs(time.time() - int(timestamp)) <= 300
            except ValueError:
                timely = False
            digest = hashlib.sha256(timestamp.encode() + nonce.encode() + encrypt_key.encode() + raw).hexdigest()
            if not timely or not nonce or not hmac.compare_digest(request.headers.get("x-lark-signature", ""), digest):
                raise HTTPException(401, "invalid_signature")
        if value.get("type") == "url_verification":
            challenge = value.get("challenge")
            if not isinstance(challenge, str):
                raise HTTPException(422, "invalid_challenge")
            return {"challenge": challenge}
        if "encrypt" in value:
            raise HTTPException(422, "encrypted_envelope_not_supported")
        observation = await guarded(app.state.host.get_plugin("feishu-protocol").receive(value))
        return {"observation_id": observation.id}

    @app.post("/agent/events")
    async def agent_events(request: Request):
        authorized(request)
        _, value = await body(request)
        try:
            event = ControlBody.model_validate(value)
        except ValidationError:
            raise HTTPException(422, "invalid_control") from None
        return await guarded(app.state.host.get_plugin("agent-protocol").submit(
            binding_id=event.binding_id, request_id=event.request_id, kind=event.kind, payload=event.payload))

    @app.get('/agent/commits/{binding_id}/{request_id}')
    async def commit_status(binding_id: str, request_id: str, request: Request):
        authorized(request)
        return await app.state.store.commit_status(binding_id, request_id)

    @app.post("/channels")
    async def create_channel(request: Request):
        authorized(request)
        _, value = await body(request)
        try:
            data = CreateChannelBody.model_validate(value)
        except ValidationError:
            raise HTTPException(422, "invalid_channel") from None
        from uuid import uuid4
        channel_id = str(uuid4())
        from argus_app.rule_sdk.engine import RuleError
        try:
            artifact = app.state.rule_files.publish(data.rule_source, channel_id)
        except (ValueError, SyntaxError, RuleError) as error:
            raise HTTPException(422, "invalid_rule") from error
        channel = await guarded(app.state.store.create_channel(data.title, data.goal, data.source_keys,
                                rule_path=artifact["rule_path"], rule_hash=artifact["rule_hash"], channel_id=channel_id))
        return channel.model_dump()

    @app.get("/channels")
    async def channels(request: Request):
        authorized(request)
        return [channel.model_dump() for channel in await app.state.store.channels()]

    @app.get("/channels/{channel_id}")
    async def channel_read(channel_id: str, request: Request, binding_id: str | None = None):
        authorized(request)
        channel = await guarded(app.state.store.channel(channel_id))
        if binding_id:
            bound = await guarded(app.state.store.bound_channel(binding_id))
            if bound.id != channel.id:
                raise HTTPException(403, "binding_mismatch")
        result = channel.model_dump(exclude={'runtime'} if binding_id else set())
        result['rule_sdk'] = app.state.rule_guide
        if channel.rule_path:
            result["rule_source"] = app.state.rule_files.read(channel.rule_path, channel.rule_hash)
        return result

    @app.get("/observations/{observation_id}")
    async def observation_read(observation_id: str, request: Request, binding_id: str):
        authorized(request)
        channel = await guarded(app.state.store.bound_channel(binding_id))
        observation = await guarded(app.state.store.observation(observation_id))
        if observation.kind != "message.observed" or observation.source_key not in channel.subscriptions:
            raise HTTPException(403, "source_forbidden")
        return observation.model_dump()

    @app.get("/channels/{channel_id}/history")
    async def channel_history(channel_id: str, request: Request, binding_id: str,
                              limit: int = 20, before: str | None = None, text: str | None = None):
        authorized(request)
        if not 1 <= limit <= 50:
            raise HTTPException(422, "invalid_limit")
        channel = await guarded(app.state.store.bound_channel(binding_id))
        if channel.id != channel_id:
            raise HTTPException(403, "binding_mismatch")
        return [row.model_dump() for row in await app.state.store.observations(
            list(channel.subscriptions), limit=limit, before=before, text=text)]

    @app.get("/observations")
    async def history(request: Request, binding_id: str, limit: int = 20):
        authorized(request)
        if not 1 <= limit <= 50:
            raise HTTPException(422, "invalid_limit")
        channel = await guarded(app.state.store.bound_channel(binding_id))
        return [row.model_dump() for row in await app.state.store.observations(list(channel.subscriptions), limit=limit)]

    return app
