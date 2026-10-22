"""Argus process composition; external handling is explicitly simulated."""

import argparse
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import logging
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
import uvicorn

from .core.config import ArgusConfig, load_config
from .core.database import Database
from .common.api.router import create_router
from .common.api.handoff import create_handoff_router
from .common.repositories.handoff_repository import HandoffRepository
from .common.services.handoff_service import HandoffService
from .common.services.observation_access_policy import ObservationAccessPolicy
from .common.services.observation_notifier import ObservationNotifier
from .common.services.real_wake_service import RealWakeService
from .common.clients.gateway_agent_client import GatewayAgentClient
from .common.clients.jev_client import JevClient
from .common.clients.mock_agent_client import MockAgentClient
from .common.repositories.observation_repository import ObservationRepository
from .common.services.rule_service import load_rule_set
from .common.services.judgement_service import JudgementService
from .common.services.wake_service import WakeService
from .common.tasks.observation_scheduler import ObservationScheduler
from .surfaces.qq.services.parser_service import QQParser
from .surfaces.qq.services.fact_service import QQFactService
from .surfaces.qq.services.observation_service import QQObservationService
from .surfaces.qq.api.onebot import create_qq_router


def create_app(config: ArgusConfig | None = None, *, database=None, jev=None, agent=None, qq_secret=None):
    config = config or load_config()
    if config.qq.enabled:
        qq_secret = qq_secret if qq_secret is not None else os.getenv('ARGUS_QQ_REPORT_SECRET')
        if not qq_secret:
            raise ValueError('ARGUS_QQ_REPORT_SECRET is required when QQ is enabled')
    if config.execution.mode == 'gateway' and not os.getenv('ARGUS_AGENT_API_TOKEN'):
        raise ValueError('ARGUS_AGENT_API_TOKEN is required in gateway mode')
    if config.execution.mode == 'gateway' and not os.getenv('ARGUS_GATEWAY_TOKEN') and agent is None:
        raise ValueError('ARGUS_GATEWAY_TOKEN is required in gateway mode')
    owns_database = database is None
    if database is None:
        if config.database_url is None:
            raise ValueError('ARGUS_DATABASE_URL is required')
        database = Database(config.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(app):
        owned_clients, scheduler, task = [], None, None
        app.state.ready = app.state.accepting = False
        app.state.scheduler_task = None
        try:
            await database.start()
            repository = ObservationRepository(database, boot_id=uuid4())
            app.state.repository = repository
            notifier = ObservationNotifier()
            policy = ObservationAccessPolicy(config.sensitive_keywords,
                permission_for=lambda chat: config.permission_overrides.get(chat, config.default_permission_level))
            app.state.handoff_service = HandoffService(HandoffRepository(database), policy=policy, notifier=notifier)
            if config.qq.enabled:
                surface = QQFactService()
                rules = load_rule_set(Path(__file__).resolve().parents[1] / 'rules/qq.yaml',
                                      known_facts=set(surface.known_facts))
                current_jev = jev
                if current_jev is None and config.jev.enabled:
                    api_key = os.getenv('ARGUS_JEV_API_KEY')
                    if not api_key:
                        raise ValueError('ARGUS_JEV_API_KEY is required when Jev is enabled')
                    current_jev = JevClient(api_key=api_key, base_url=config.jev.base_url, model=config.jev.model,
                        timeout_seconds=config.jev.timeout_seconds, attempts=config.jev.attempts)
                    owned_clients.append(current_jev)
                current_agent = agent
                if current_agent is None:
                    current_agent = (GatewayAgentClient(config.execution.gateway_url, os.environ['ARGUS_GATEWAY_TOKEN'],
                        timeout_seconds=config.submit_timeout_seconds) if config.execution.mode == 'gateway'
                        else MockAgentClient(completion_delay_seconds=config.mock.completion_delay_seconds))
                    owned_clients.append(current_agent)
                judgement = JudgementService(rules, surface, current_jev,
                    sensitive_keywords=config.sensitive_keywords, max_items=config.max_items_per_wake,
                    max_chars=config.max_chars_per_wake, recent_chars=config.recent_context_chars)
                wakes = (RealWakeService(repository, current_agent, policy=policy) if config.execution.mode == 'gateway'
                    else WakeService(repository, current_agent, submit_timeout_seconds=config.submit_timeout_seconds,
                        max_submit_attempts=config.max_submit_attempts, retry_delays_seconds=config.retry_delays_seconds,
                        outcome_timeout_seconds=config.outcome_timeout_seconds))
                scheduler = ObservationScheduler(repository, judgement, wakes,
                    permission_for=lambda chat: config.permission_overrides.get(chat, config.default_permission_level),
                    quiet_seconds=config.quiet_seconds, max_debounce_seconds=config.max_debounce_seconds,
                    scan_interval_seconds=config.scan_interval_seconds, max_concurrent_chats=config.max_concurrent_chats,
                    max_items=config.max_items_per_wake, recent_items=config.recent_context_items)
                def notify(chat_id, **kwargs):
                    scheduler.notify(chat_id, **kwargs)
                    notifier.notify(chat_id)
                service = QQObservationService(repository, QQParser(config.qq.bot_id, config.qq.bot_names), notify=notify)
                app.state.qq_service = service
                task = asyncio.create_task(scheduler.run(), name='argus:observations')
                app.state.scheduler_task = task
            app.state.ready = app.state.accepting = True
            yield
        finally:
            app.state.ready = app.state.accepting = False
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            async with AsyncExitStack() as cleanup:
                if owns_database:
                    cleanup.push_async_callback(database.close)
                for client in owned_clients:
                    cleanup.push_async_callback(client.aclose)
                if scheduler is not None:
                    cleanup.push_async_callback(scheduler.aclose)

    app = FastAPI(lifespan=lifespan)
    app.include_router(create_router(database=database))
    if config.qq.enabled:
        app.include_router(create_qq_router(config.qq, secret=qq_secret))
    if config.execution.mode == 'gateway':
        app.include_router(create_handoff_router(_ServiceProxy(app), token=os.environ['ARGUS_AGENT_API_TOKEN']))
    return app


class _ServiceProxy:
    def __init__(self, app):
        self.app = app

    def __getattr__(self, name):
        return getattr(self.app.state.handoff_service, name)


def create_service_app(owner):
    app = FastAPI()
    app.include_router(create_handoff_router(_ServiceProxy(owner), token=os.environ.get('ARGUS_AGENT_API_TOKEN', '')))
    return app


async def serve_pair(owner, service, config):
    async with owner.router.lifespan_context(owner):
        servers = [uvicorn.Server(uvicorn.Config(owner, host=config.host, port=config.port, lifespan='off')),
            uvicorn.Server(uvicorn.Config(service, host=config.service_host, port=config.service_port, lifespan='off'))]
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks, return_exceptions=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Icarus Argus')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--service-host')
    parser.add_argument('--service-port', type=int)
    args = parser.parse_args(argv)
    config = load_config()
    changes = {key: value for key, value in vars(args).items() if value is not None}
    config = ArgusConfig.model_validate({**config.model_dump(), 'database_url': config.database_url, **changes})
    logging.basicConfig(level=config.log_level)
    owner = create_app(config)
    if config.service_host is None:
        uvicorn.run(owner, host=config.host, port=config.port)
    else:
        if config.execution.mode != 'gateway' or not os.getenv('ARGUS_AGENT_API_TOKEN'):
            parser.error('Authenticated gateway mode is required for service listener')
        service = create_service_app(owner)
        asyncio.run(serve_pair(owner, service, config))


if __name__ == '__main__':
    main()
