import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.coordinator import (
    WorkspaceMaintenanceCoordinator,
)


def test_coordinator同workspace排他且release后可重新claim():
    coordinator = WorkspaceMaintenanceCoordinator()

    token = coordinator.claim("workspace-a")
    assert isinstance(token, str)
    assert coordinator.claim("workspace-a") is None
    assert coordinator.is_claimed("workspace-a") is True
    assert coordinator.active_workspace_keys == frozenset({"workspace-a"})

    assert coordinator.release("workspace-a", token) is True
    assert coordinator.release("workspace-a") is False
    assert coordinator.is_claimed("workspace-a") is False
    assert isinstance(coordinator.claim("workspace-a"), str)


def test_coordinator不同workspace可同时claim():
    coordinator = WorkspaceMaintenanceCoordinator()

    assert isinstance(coordinator.claim("workspace-a"), str)
    assert isinstance(coordinator.claim("workspace-b"), str)
    assert coordinator.active_workspace_keys == frozenset(
        {"workspace-a", "workspace-b"}
    )


def test_coordinator统一规范化workspace_key首尾空白():
    coordinator = WorkspaceMaintenanceCoordinator()

    token = coordinator.claim("  workspace-a  ")
    assert isinstance(token, str)
    assert coordinator.claim("workspace-a") is None
    assert coordinator.is_claimed(" workspace-a") is True
    assert coordinator.active_workspace_keys == frozenset({"workspace-a"})
    assert coordinator.release("workspace-a ", token) is True


def test_coordinator拒绝空workspace_key():
    coordinator = WorkspaceMaintenanceCoordinator()

    with pytest.raises(ValueError, match="workspace_key cannot be empty"):
        coordinator.claim("  ")
    with pytest.raises(ValueError, match="workspace_key cannot be empty"):
        coordinator.release("")
    with pytest.raises(ValueError, match="workspace_key cannot be empty"):
        coordinator.is_claimed(" ")


def test_coordinator多线程同时claim同workspace只有一个成功():
    coordinator = WorkspaceMaintenanceCoordinator()
    worker_count = 16
    barrier = Barrier(worker_count)

    def claim_once():
        barrier.wait()
        return coordinator.claim("shared")

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(lambda _: claim_once(), range(worker_count)))

    assert sum(isinstance(result, str) for result in results) == 1
    assert results.count(None) == worker_count - 1


def test_coordinator可由不同event_loop共享且不保存task():
    coordinator = WorkspaceMaintenanceCoordinator()
    barrier = Barrier(2)

    def run_claim():
        async def claim():
            await asyncio.to_thread(barrier.wait)
            return coordinator.claim("shared-loop")

        return asyncio.run(claim())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run_claim(), range(2)))

    assert sum(isinstance(result, str) for result in results) == 1
    assert results.count(None) == 1
    assert not any(isinstance(value, asyncio.Task) for value in vars(coordinator).values())


def test_coordinator异常路径finally释放声明():
    coordinator = WorkspaceMaintenanceCoordinator()

    with pytest.raises(RuntimeError, match="maintenance failed"):
        token = coordinator.claim("workspace-a")
        if token is not None:
            try:
                raise RuntimeError("maintenance failed")
            finally:
                coordinator.release("workspace-a", token)

    assert isinstance(coordinator.claim("workspace-a"), str)


def test_coordinator旧token不能释放后来runtime的新claim():
    coordinator = WorkspaceMaintenanceCoordinator()
    old_token = coordinator.claim("workspace-a")
    assert isinstance(old_token, str)
    assert coordinator.release("workspace-a", old_token) is True

    new_token = coordinator.claim("workspace-a")
    assert isinstance(new_token, str)
    assert new_token != old_token
    assert coordinator.release("workspace-a", old_token) is False
    assert coordinator.is_claimed("workspace-a") is True
    assert coordinator.release("workspace-a", new_token) is True
