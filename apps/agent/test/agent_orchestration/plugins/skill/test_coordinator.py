from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.coordinator import (
    SkillWriteCoordinator,
)


def test_coordinator_serializes_same_normalized_skill_name():
    coordinator = SkillWriteCoordinator()
    first_entered = Event()
    release_first = Event()
    observations: list[str] = []
    observations_lock = Lock()

    def first():
        def operation():
            with observations_lock:
                observations.append("first-enter")
            first_entered.set()
            release_first.wait(timeout=2)
            with observations_lock:
                observations.append("first-exit")

        coordinator.run(" Shared ", operation)

    def second():
        first_entered.wait(timeout=2)

        def operation():
            with observations_lock:
                observations.append("second-enter")

        coordinator.run("shared", operation)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first)
        second_future = executor.submit(second)
        assert first_entered.wait(timeout=2)
        assert observations == ["first-enter"]
        release_first.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)

    assert observations == ["first-enter", "first-exit", "second-enter"]


def test_coordinator_allows_different_names_to_run_independently():
    coordinator = SkillWriteCoordinator()
    barrier = Barrier(2)

    def operation(name):
        return coordinator.run(name, lambda: barrier.wait(timeout=2))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(operation, ("alpha", "beta")))

    assert sorted(results) == [0, 1]


def test_coordinator_propagates_result_and_exception_and_releases_lock():
    coordinator = SkillWriteCoordinator()

    assert coordinator.run("alpha", lambda: 42) == 42
    with pytest.raises(RuntimeError, match="failed"):
        coordinator.run("alpha", lambda: (_ for _ in ()).throw(RuntimeError("failed")))
    assert coordinator.run("alpha", lambda: "recovered") == "recovered"


@pytest.mark.parametrize("name", ["", "  ", None])
def test_coordinator_rejects_empty_name(name):
    coordinator = SkillWriteCoordinator()

    with pytest.raises(ValueError, match="skill_name cannot be empty"):
        coordinator.run(name, lambda: None)
