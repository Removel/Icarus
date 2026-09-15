# Icarus modifications

Imported from `https://github.com/mem0ai/mem0` at commit
`c7ee362aff94a369af70f13f2b4f853f6793ff4c`.

Icarus maintains this directory as ordinary Monorepo source. It does not contain an
embedded Git repository or require Git submodules.

Current modifications:

- `server/docker-compose.yaml` stores PostgreSQL and history data under
  `$ICARUS_DATA_DIR/services/mem0` through explicit bind mounts.
- `scripts/icarus-compose.sh` and `scripts/icarus_compose.py` use only the system
  Python standard library to safely load the repository-root `.env`, validate
  variables, create data directories, and invoke Docker Compose without
  shell-evaluating Secret values. The root `icarus` command is the public
  lifecycle entrypoint.
- The server image installs and runs the imported repository source instead of
  replacing it with the latest PyPI package at container startup.
- The managed default uses an OpenAI-compatible Flash LLM and local FastEmbed
  embeddings; the configured embedding dimension is also passed to pgvector.
- The REST add endpoint accepts `preserve_input_language`; Icarus enables it by
  default so inferred memories keep the language and script of the input.
- FastEmbed model files are bind-mounted under
  `$ICARUS_DATA_DIR/services/mem0/models` instead of remaining in an ephemeral
  container cache.
- The root and server READMEs identify Icarus-managed startup and bind-mount
  behavior instead of directing users to delete an upstream named volume.
- The upstream `evaluation` benchmark Git submodule is omitted from the Icarus
  import because it is not needed to build or run Mem0; no nested submodule remains.
- Icarus runtime credentials are injected by the parent environment; secrets are not
  committed in this directory.

The upstream Apache License 2.0 remains in `LICENSE`. Re-check upstream `LICENSE` and
`NOTICE` files on every source sync.
