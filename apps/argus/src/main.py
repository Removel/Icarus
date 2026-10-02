"""Local service entrypoint for Argus."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI, HTTPException, Query
import uvicorn

from packages.runtime_environment import get_icarus_data_dir
from .config.config_loader import load_config
from .config.config_model import ArgusConfig
from .connector.qq_connector import QQConnector
from .dao.observation_store import ObservationStore
from .dao.database import Database
from .service.observation_status import ObservationStatusService
from .service.batch_state import reset_interrupted
from .handler.qq_handler import QQHandler
from .clients.icarus_client import IcarusClient
from .clients.jev_client import JevClient
from .service.qq_service import QQService
from .dao.batch_store import BatchStore
from .service.batch_scheduler import BatchScheduler
from .service.batch_runtime import BatchRuntime

logger = logging.getLogger("argus.main")


def create_app(
    config: ArgusConfig | None = None, *, database: Database | None = None,
    judge=None, gateway=None, qq_secret: str | None = None,
) -> FastAPI:
    config = config or load_config()
    if config.qq.enabled:
        qq_secret = qq_secret if qq_secret is not None else os.getenv("ARGUS_QQ_REPORT_SECRET")
        if not qq_secret:
            raise ValueError("ARGUS_QQ_REPORT_SECRET is required when QQ is enabled")
    elif qq_secret is None:
        qq_secret = ""
    database = database or Database(get_icarus_data_dir())
    store = ObservationStore(database)
    batches = BatchStore(database)
    status_service = ObservationStatusService(store,batches)
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

    scheduler = BatchScheduler(database,store,batches,quiet_seconds=config.batch_quiet_seconds,
        max_messages=config.batch_max_messages,max_seconds=config.batch_max_seconds,
        bot_names=config.qq.bot_names,capacity=config.queue_capacity,
        base_seconds=config.no_action_backoff_base_seconds,start_count=config.no_action_backoff_start_count,
        cap_seconds=config.no_action_backoff_cap_seconds,spacing_seconds=config.submission_spacing_seconds,
        bypass_messages=config.pacing_bypass_messages,submission_window_seconds=config.submission_window_seconds)
    service = None
    if config.qq.enabled:
        service = QQService(database,store,batches,judge,gateway,workspace_path=config.qq.workspace_path,
            bot_names=config.qq.bot_names,max_steps=config.graph_max_steps,
            graph_timeout_seconds=config.graph_timeout_seconds,waiting_check_seconds=config.waiting_check_seconds,
            submission_window_seconds=config.submission_window_seconds)
        service.notify = scheduler.notify
        scheduler.waiting_changed = service.waiting_changed

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store, app.state.batches, app.state.scheduler = store,batches,scheduler
        app.state.ready = app.state.accepting = False
        app.state.execution_tasks = []
        await database.start()
        try:
            if config.qq.enabled:
                await reset_interrupted(database,batches)
                await gateway.connect()
                runtime = BatchRuntime(scheduler,service,recovery_interval_seconds=config.recovery_interval_seconds)
                app.state.execution_tasks = [
                    asyncio.create_task(runtime.run_checks(),name='argus:recovery'),
                    asyncio.create_task(runtime.run_batches(),name='argus:batches')]
            app.state.ready = app.state.accepting = True
            yield
        finally:
            app.state.accepting = app.state.ready = False
            for task in app.state.execution_tasks:
                task.cancel()
            await asyncio.gather(*app.state.execution_tasks,return_exceptions=True)
            if config.qq.enabled:
                await gateway.close()
            if owned_jev_client is not None:
                await owned_jev_client.aclose()
            await database.close()

    app = FastAPI(lifespan=lifespan)
    if config.qq.enabled:
        handler = QQHandler(config.qq.bot_id,receive=service.receive)
        app.include_router(QQConnector(config.qq,qq_secret,handler).router)

    @app.get("/health")
    async def health():
        if (not getattr(app.state,'ready',False) or not database.is_running
                or any(t.done() for t in getattr(app.state,'execution_tasks',()))
                or (config.qq.enabled and not gateway.connected)):
            raise HTTPException(status_code=503, detail="Argus is not ready")
        if not await database.is_available():
            raise HTTPException(status_code=503, detail="Argus database is unavailable")
        return {"status": "ready"}

    @app.get("/observations/{observation_id}/status")
    async def observation_status(observation_id: str, after_run: int = Query(default=0,ge=0),
                                 limit: int = Query(default=20,ge=1,le=100)):
        try:
            return await status_service.get(observation_id,after_run=after_run,limit=limit)
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
