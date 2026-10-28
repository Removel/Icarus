# Development skills

This directory is the repository's development-time skill source, not Icarus Agent's
production skill store. Installation/discovery depends on the coding assistant used;
placing files here does not register plugins, run workflows, or install dependencies.

| Skill | Origin | Intended use |
| --- | --- | --- |
| mem0 | Mem0 | SDK reference, including OSS and upstream Platform distinctions |
| mem0-integrate | Mem0 | Reference workflow for integrating Mem0 in another project |
| mem0-test-integration | Mem0 | Reference verification workflow |
| mem0-vercel-ai-sdk | Mem0 | External Vercel integration reference, not an Icarus dependency |
| mem0-oss-to-platform | Mem0 | Upstream migration reference, not permission to migrate Icarus |
| openkb | OpenKB | Development navigation of an OpenKB knowledge base |

Mem0 origin: `mem0ai/mem0@c7ee362aff94a369af70f13f2b4f853f6793ff4c`.
OpenKB origin: `VectifyAI/OpenKB@ff54396e575ee6feb0113b631a34caa082b441cc`.
Each bundle includes its applicable LICENSE; see [third-party notices](../THIRD_PARTY_NOTICES.md).
Keep supporting references, scripts, and attribution with the skill when moving it.

These upstream references do not override [AGENTS.md](../AGENTS.md) or
[CONTRIBUTING.md](../CONTRIBUTING.md). In particular, branch creation, code changes,
platform migrations, external publication, and real-model calls require the relevant
approval. Keep local paths updated, and do not assume upstream examples are supported
Icarus features. The removed standalone Mem0 CLI is not installed by this repository.

Production baseline skills belong in [skills/](../skills/README.md). OpenKB's deck
styles and HTML critic are application runtime resources and stay inside that App.
