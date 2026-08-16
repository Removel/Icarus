# Icarus Repository Guide

## Project

Icarus is a Monorepo for an extensible Agent system. Applications live under `apps/`; the repository may later include backend services, WebUI, clients, and terminal/TUI applications.

Keep application-specific code in `apps/<app-name>/`. Introduce shared packages only after multiple applications have a proven common dependency.

## Architecture

Agent architecture designs are maintained in:

- `apps/agent/docs/arch/`

Implementation plans are maintained in:

- `apps/agent/docs/plan/`

Read the relevant design before changing architecture or data flow. When implementation and documentation differ, verify the current code and tests first, then update the corresponding document.

## Development Red Lines

- Preserve the decoupled layer design. Do not bypass layers or introduce reverse dependencies for convenience.
- Model-vendor differences stay in `model_provider`; upper layers must not branch on OpenAI, Anthropic, or other provider protocols.
- ReActAgent remains stateless and must not depend on Plugin Runtime, Blackboard, UI, TTS, or domain plugins.
- Plugin Runtime is generic infrastructure. It routes by source Plugin identity and must not interpret concrete Event types or import domain plugins.
- Event is the business communication mechanism. Hook is only for persistence, observability, and supervision; Hook must not replace EventBus or change main-flow behavior.
- Blackboard is a Plugin, not the EventBus.
- AgentPlugin publishes the raw execution stream. Styling, TTS, emotion, action, Skill, Memory, and similar processing belong to independent plugins.
- Do not implement nested child plugins. Helpers inside a Plugin are ordinary component objects and do not register with PluginRegistry.
- Concrete plugins use one directory per plugin, with tests mirroring that directory.
- Dynamic Plugin context belongs in the current User Prompt; do not modify the stable System Prompt.
- Prefer simple, explicit, flat parameters and reuse existing public types instead of creating duplicate models.
- Keep sync and async interfaces behaviorally consistent.
- Do not add speculative abstractions without a concrete caller or requirement.

## Testing

Tests stay inside the corresponding application and mirror the source layer:

```text
apps/agent/src/agent_orchestration/plugins/
apps/agent/test/agent_orchestration/plugins/

apps/tui/
apps/tui/test/
```

Use pytest functional tests and native `assert`.

Validation order:

1. smallest affected test file or directory;
2. affected application test suite;
3. compile and diff checks.

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test
git diff --check
```

Add focused tests for model, Agent, Tool, stream, Event, or Plugin changes. When credentials are available, use a small real-model smoke test without exposing secrets.

Do not fix unrelated failures in a focused change.

## Git

- Split commits by logical feature or architectural layer.
- Keep implementation and its tests together.
- Keep documentation-only changes separate when practical.
- Do not mix unrelated refactors or local experiment artifacts into feature commits.
- Run relevant tests before committing.
- Do not create branches, commit, amend, rebase, or push unless explicitly requested.
