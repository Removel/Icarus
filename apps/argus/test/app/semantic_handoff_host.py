"""Owned Gateway/Agent subprocess for the offline semantic integration scenario.

Only the clock and passive event observation are fixture additions. SessionRuntime,
ReActAgent, provider transport, TaskChannel, SQLite and Argus tools are production.
"""

import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys

import uvicorn
from fastapi import Request

from agent_app.application import AgentRuntime, SessionRuntime
from agent_app.config import ConfigModel
from agent_app.contracts.prompt import PromptUpdateEvent
from icarus.core.agents import AgentToolCompletedEvent
from icarus.core.run_control.events import TaskContextInputEvent, TaskContextInputResultEvent
from apps.gateway.src.app import create_app


def main():
    config_path, port, initial_time = sys.argv[1:]
    config = ConfigModel.model_validate(json.loads(Path(config_path).read_text()))
    current_time = [datetime.fromisoformat(initial_time)]
    notices, updates = [], []
    noticed = asyncio.Event()
    grants = {}

    class ObservedSession(SessionRuntime):
        def __init__(self, *args, publish_update, **kwargs):
            async def observe(update):
                updates.append({'type': update.type, 'task_id': update.task_id,
                                'session_id': update.session_id, 'payload': dict(update.payload)})
                await publish_update(update)
            super().__init__(*args, publish_update=observe, **kwargs)

        async def start(self):
            await super().start()
            plugin = self.runtime_host.get_plugin('argus')
            original = plugin.publish

            async def observe(event):
                await original(event)
                if isinstance(event, PromptUpdateEvent):
                    notices.append({'task_id': event.task_id,
                                    'text': '\n'.join(section.content for section in event.sections)})
            plugin.publish = observe
            prompt = self.runtime_host.get_plugin('prompt')
            prompt_publish, context_result = prompt.publish, prompt.on_context_result
            notice_events = set()

            async def observe_prompt(event):
                if isinstance(event, TaskContextInputEvent) and 'argus_wait' in event.content:
                    notice_events.add(event.event_id)
                await prompt_publish(event)

            async def observe_context_result(source, event):
                await context_result(source, event)
                if isinstance(event, TaskContextInputResultEvent) and event.request_event_id in notice_events:
                    assert event.status == 'accepted', event
                    noticed.set()

            prompt.publish, prompt.on_context_result = observe_prompt, observe_context_result
            completed = plugin.on_tool_completed

            async def observe_completed(source, event):
                await completed(source, event)
                if isinstance(event, AgentToolCompletedEvent):
                    for identifier in plugin.exposed:
                        ref = plugin.references._observations[identifier]
                        grants.setdefault((event.tool_call.id, ref), asyncio.Event()).set()

            plugin.on_tool_completed = observe_completed

    runtime = AgentRuntime(config_loader=lambda: config, clock=lambda: current_time[0],
                           session_factory=ObservedSession)
    app = create_app(runtime)

    @app.get('/fixture/notice')
    async def notice():
        await asyncio.wait_for(noticed.wait(), 15)
        return {'notices': notices}

    @app.get('/fixture/granted/{call_id}/{number}')
    async def granted(call_id: str, number: int):
        await asyncio.wait_for(grants.setdefault((call_id, f'#{number}'), asyncio.Event()).wait(), 15)
        return {'granted': True}

    @app.get('/fixture/released/{wake_id}')
    async def released(wake_id: str):
        controller = runtime.scoped_executions
        monitor = controller._monitors.get(wake_id)
        if monitor is not None:
            await asyncio.wait_for(asyncio.shield(monitor), 20)
        view = await controller.status(wake_id=wake_id)
        record = await controller.store.scoped_record(wake_id)
        records = await controller.store.read_internal(record.identity, update_types={
            'argus_reference', 'argus_report', 'argus_report_synced', 'argus_report_rejected'})
        return {'view': view.model_dump(mode='json'), 'binding': record.properties['binding'],
                'records': [{'task_id': row.task_id, 'type': row.update_type, 'payload': row.payload}
                            for row in records], 'notices': notices, 'updates': updates}

    @app.post('/fixture/clock')
    async def advance(request: Request):
        body = await request.json()
        current_time[0] = datetime.fromisoformat(body['now'])
        await runtime.cleanup_argus_sessions()
        return {'now': current_time[0].isoformat()}

    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=int(port), log_level='error'))
    original_startup = server.startup

    async def startup(*args, **kwargs):
        await original_startup(*args, **kwargs)
        print('semantic-fixture-ready', flush=True)

    server.startup = startup
    asyncio.run(server.serve())


if __name__ == '__main__':
    main()
