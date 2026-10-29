import asyncio
from pathlib import Path
from apps.tui.src.tools.replay import ReplayRuntimeService, load_replay


def test_replay_updates_use_the_public_clients_adopted_scope():
    async def run():
        path = Path(__file__).parents[1] / "fixtures/synthetic_tui_events.jsonl"
        service = ReplayRuntimeService(load_replay(path))
        await service.start()
        await service.submit("demo", submission_id="stable")
        subscription = service.subscribe_updates()
        for _ in range(5):
            update = await subscription.next_update()
            assert update.workspace_key == service.workspace_key
            assert update.session_id == service.session_id
        await service.close()

    asyncio.run(run())
