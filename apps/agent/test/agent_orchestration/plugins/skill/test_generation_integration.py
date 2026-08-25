import asyncio

from apps.agent.src.agent_orchestration import AgentFactory
from apps.agent.src.agent_orchestration.hooks import (
    BaseHook,
    HookEvent,
    HookRegistry,
)
from apps.agent.src.agent_orchestration.plugins.skill.coordinator import (
    SkillWriteCoordinator,
)
from apps.agent.src.agent_orchestration.plugins.skill.evolver import SkillEvolver
from apps.agent.src.agent_orchestration.plugins.skill.generation_prompt import (
    SkillGenerationPromptBuilder,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_tools import (
    create_generation_tools,
)
from apps.agent.src.agent_orchestration.plugins.skill.job_manager import (
    SkillJobManager,
)
from apps.agent.src.agent_orchestration.plugins.skill.producer import SkillProducer
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    SkillRepository,
)
from apps.agent.src.agent_orchestration.tools import ToolRegistry
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    LLMResponse,
    Message,
    TextPart,
    ToolCall,
)


class GenerationLLM(BaseLLM):
    def __init__(self) -> None:
        self.calls = 0
        self.tool_names = []

    def invoke(self, messages, tools=None):
        raise NotImplementedError

    async def ainvoke(self, messages, tools=None):
        self.calls += 1
        self.tool_names = [tool.name for tool in tools or []]
        if self.calls == 1:
            return LLMResponse(
                Message(
                    "assistant",
                    [],
                    tool_calls=[
                        ToolCall(
                            "write-skill",
                            "write",
                            {
                                "path": "SKILL.md",
                                "content": (
                                    "---\nname: generated\n"
                                    "description: Generated integration Skill.\n"
                                    "---\nUse the bundled fixture.\n"
                                ),
                            },
                        ),
                        ToolCall(
                            "copy-fixture",
                            "copy",
                            {
                                "source_root": "workspace",
                                "source": "fixture.bin",
                                "path": "assets/fixture.bin",
                            },
                        ),
                    ],
                ),
                finish_reason="tool_call",
            )
        return LLMResponse(
            Message("assistant", [TextPart("Draft complete")]),
            finish_reason="stop",
        )

    def stream(self, messages, tools=None):
        return iter(())

    async def astream(self, messages, tools=None):
        if False:
            yield

    def close(self):
        pass

    async def aclose(self):
        pass


class GenerationLLMFactory:
    def __init__(self, llm: GenerationLLM) -> None:
        self.llm = llm

    def create_llm(self, role):
        return self.llm


class RecordingHook(BaseHook):
    def __init__(self) -> None:
        self.events: list[HookEvent] = []

    def handle(self, event: HookEvent) -> None:
        self.events.append(event)


async def wait_terminal(manager: SkillJobManager, job_id: str):
    for _ in range(100):
        job = manager.require(job_id)
        if job.is_terminal:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("Skill Job did not reach a terminal state")


def test_generation_agent_uses_private_tools_and_child_trace(tmp_path):
    async def run():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "fixture.bin").write_bytes(b"\x00fixture")
        global_skills = tmp_path / "data" / "skills"
        workspace_skills = workspace / "skills"

        tool_registry = ToolRegistry()
        tool_registry.register_many(create_generation_tools())
        tool_registry.freeze()
        hook_registry = HookRegistry()
        recorder = RecordingHook()
        hook_registry.register("*", recorder)
        llm = GenerationLLM()
        factory = AgentFactory(
            llm_factory=GenerationLLMFactory(llm),
            tool_registry=tool_registry,
            hook_registry=hook_registry,
            register_builtin_tools=False,
        )
        prompt_builder = SkillGenerationPromptBuilder()
        manager = SkillJobManager(
            producer=SkillProducer(
                lambda: factory.get_agent("thinking"), prompt_builder
            ),
            evolver=SkillEvolver(
                lambda: factory.get_agent("thinking"), prompt_builder
            ),
            repository=SkillRepository(global_skills, workspace_skills),
            coordinator=SkillWriteCoordinator(),
            workspace_dir=workspace,
            close_resource=factory.aclose,
        )
        await manager.start()
        queued = manager.submit_produce(
            name="generated",
            scope="workspace",
            instructions="Build a reusable Skill with the fixture.",
            conversation=(Message("user", [TextPart("Use fixture.bin")]),),
            task_id="task-parent",
            run_id="run-parent",
            step=4,
        )
        finished = await wait_terminal(manager, queued.job_id)
        await manager.stop()
        return finished, llm, recorder.events

    job, llm, events = asyncio.run(run())

    assert job.status == "succeeded"
    assert llm.tool_names == ["read", "write", "copy", "remove", "bash"]
    skill_file = tmp_path / "workspace" / "skills" / "generated" / "SKILL.md"
    assert skill_file.exists()
    assert (skill_file.parent / "assets" / "fixture.bin").read_bytes() == b"\x00fixture"
    job_events = [
        event
        for event in events
        if event.context.get("skill_job_id") == job.job_id
    ]
    assert {event.name for event in job_events} >= {
        "agent.invoke",
        "llm.invoke",
        "tool.execute",
    }
    assert len({event.run_id for event in job_events}) == 1
    assert job_events[0].run_id not in {None, "run-parent"}
    assert all(
        event.context.get("parent_run_id") == "run-parent"
        and event.context.get("agent_kind") == "skill_generation"
        for event in job_events
    )
