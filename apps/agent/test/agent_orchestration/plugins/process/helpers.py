import asyncio
import os
from pathlib import Path

from apps.agent.src.agent_orchestration.plugins.process import (
    ProcessConfig,
    ProcessCursorCodec,
    ProcessManager,
)


def create_manager(tmp_path: Path, **config_overrides):
    workspace = tmp_path / "workspace"
    processes = tmp_path / "processes"
    workspace.mkdir()
    processes.mkdir()
    snapshots = []
    tasks = []

    async def publish(snapshot):
        snapshots.append(snapshot)

    def start_background(name, operation):
        task = asyncio.create_task(operation(), name=name)
        tasks.append(task)
        return task

    manager = ProcessManager(
        session_id="session-1",
        workspace_path=workspace,
        processes_dir=processes,
        config=ProcessConfig(**config_overrides),
        cursor_codec=ProcessCursorCodec(os.urandom(32)),
        start_background_work=start_background,
        publish_snapshot=publish,
    )
    return manager, snapshots, tasks, workspace


async def wait_for_status(manager, process_id, status, timeout=3.0):
    async with asyncio.timeout(timeout):
        while True:
            snapshot = await manager.get(process_id)
            if snapshot.status == status:
                return snapshot
            await asyncio.sleep(0.01)
