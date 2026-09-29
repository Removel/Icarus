"""Local service entrypoint for Argus."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
import uvicorn

from packages.runtime_environment import get_icarus_data_dir
from .config.config_loader import load_config
from .config.config_model import ArgusConfig
from .connector.QQConnector import QQConnector
from .dao.observation_store import ObservationStore
from .handler.QQHandler import QQHandler
from .queue import InboundQueue
from .clients.IcarusClient import IcarusClient
from .clients.JevClient import JevClient
from .service.QQService import QQService
from .worker import QueueWorker, recover_pending

logger = logging.getLogger("argus.main")


def create_app(
    config: ArgusConfig | None = None, *, store: ObservationStore | None = None,
    judge=None, gateway=None, qq_secret: str | None = None,
) -> FastAPI:
    config = config or load_config()
    if config.qq.enabled:
        qq_secret = qq_secret if qq_secret is not None else os.getenv("ARGUS_QQ_REPORT_SECRET")
        if not qq_secret:
            raise ValueError("ARGUS_QQ_REPORT_SECRET is required when QQ is enabled")
    elif qq_secret is None:
        qq_secret = ""
    store = store or ObservationStore(get_icarus_data_dir())
    owned_jev_client = None
    if config.qq.enabled and judge is None:
        api_key = os.getenv("ARGUS_JEV_API_KEY")
        if not api_key:
            raise ValueError("ARGUS_JEV_API_KEY is required when QQ is enabled")
        owned_jev_client = JevClient(
            api_key=api_key, base_url=config.jev_base_url,
            model=config.jev_model, timeout_seconds=config.judge_timeout_seconds,
        )
        judge = owned_jev_client
    if config.qq.enabled and gateway is None:
        gateway = IcarusClient(config.qq.gateway_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        queue = InboundQueue(config.queue_capacity)
        app.state.queue = queue
        app.state.store = store
        app.state.ready = False
        app.state.accepting = False
        app.state.recovery_task = None
        scheduled: set[str] = set()
        await store.start()
        try:
            await store.reset_interrupted()
            if config.qq.enabled:
                await gateway.connect()
                service = QQService(
                    store, judge, gateway, workspace_path=config.qq.workspace_path,
                    reply_hint=QQHandler.reply_hint, bot_names=config.qq.bot_names,
                )
                async def process(observation):
                    try:
                        await service.process(observation)
                    finally:
                        scheduled.discard(observation.id)
            else:
                async def process(observation):
                    scheduled.discard(observation.id)
            worker = QueueWorker(queue, process=process)
            app.state.worker = worker
            worker.start()
            if config.qq.enabled:
                await recover_pending(store, queue, scheduled, limit=config.queue_capacity)

            async def replenish():
                while True:
                    await asyncio.sleep(config.recovery_interval_seconds)
                    if config.qq.enabled:
                        await recover_pending(store, queue, scheduled, limit=config.queue_capacity)

            app.state.recovery_task = asyncio.create_task(replenish(), name="argus:recovery")
            app.state.ready = True
            app.state.accepting = True
            try:
                yield
            finally:
                app.state.accepting = False
                app.state.ready = False
                app.state.recovery_task.cancel()
                await asyncio.gather(app.state.recovery_task, return_exceptions=True)
                await worker.stop()
        finally:
            if config.qq.enabled:
                await gateway.close()
            if owned_jev_client is not None:
                await owned_jev_client.aclose()
            await store.close()

    app = FastAPI(lifespan=lifespan)
    if config.qq.enabled:
        connector = QQConnector(config.qq, qq_secret, store, None)
        app.include_router(connector.router)

    @app.get("/health")
    async def health():
        worker: QueueWorker | None = getattr(app.state, "worker", None)
        recovery = getattr(app.state, "recovery_task", None)
        if (not getattr(app.state, "ready", False) or worker is None
                or not worker.is_running or recovery is None or recovery.done()
                or store._sessions is None
                or (config.qq.enabled and not gateway.connected)):
            raise HTTPException(status_code=503, detail="Argus is not ready")
        return {"status": "ready"}

    @app.get("/observations/{observation_id}/status")
    async def observation_status(observation_id: str):
        try:
            return await store.status_details(observation_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Observation not found") from error

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Icarus Argus")
    parser.parse_args(argv)
    config = load_config()
    logging.basicConfig(level=config.log_level)
    uvicorn.run(create_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
