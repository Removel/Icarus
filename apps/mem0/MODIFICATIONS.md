# Icarus modifications

Imported from `https://github.com/mem0ai/mem0` at commit
`c7ee362aff94a369af70f13f2b4f853f6793ff4c`.

Icarus maintains this directory as ordinary Monorepo source. It does not contain an
embedded Git repository or require Git submodules.

Current modifications:

- Memory updates record category and expiration changes in both sync and async history.
  SQLite adds the `changes` column without discarding existing history; back up history.db
  before upgrading and preserve that backup when rolling back to older schema handling.
- The `icarus` Hatch environment runs core memory regressions with test dependencies;
  provider-wide suites retain their existing optional dependency environments.
- `.dockerignore` excludes local environments, caches and credentials from image build context.
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

## Frozen subset and repository infrastructure

The upstream import is frozen at the full commit above; Icarus patches and security
maintenance continue. This first cleanup removes external integration packages,
standalone Node/Python CLI and its mem0-cli skill, TypeScript SDK, examples, the
upstream documentation site, marketplace manifests, nested CI/release automation,
and obsolete upstream contribution/development entry points. Icarus `docs/spec/`,
Python core/providers, runtime JSON, server/auth/migrations, dashboard, maintenance
scripts, and the original test suite remain.

Five development skill bundles moved to root `.agents/skills/` with their own LICENSE
and support files. Only relocation references were adjusted. The SDK
`preserve_input_language` documentation previously added to upstream add.mdx is recorded
here and in the Icarus README; the runtime patch and tests remain unchanged.

The OSS-to-Platform script moved to `tests/fixtures/oss-to-platform-migrate.sh` with
its original regression tests; it is not a managed Icarus CLI. The server image now
copies the required LICENSE alongside package metadata. Its pip installation still
does not consume `poetry.lock` as a locked resolution.

App-owned install/test scripts provide a private development environment. The installer
checks virtual-environment ownership and isolates pip configuration/destination overrides.
Root CI,
text/SDD/governance conventions and test aggregation replace the removed nested
infrastructure, without inheriting upstream release secrets or community gates.

The upstream Apache License 2.0 remains in `LICENSE`; root `THIRD_PARTY_NOTICES.md`
records the source subset and relocated skills. Re-check license and NOTICE obligations
before incorporating any future third-party source change.
