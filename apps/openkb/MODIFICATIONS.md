# Icarus modifications

Imported from `https://github.com/VectifyAI/OpenKB` at commit
`ff54396e575ee6feb0113b631a34caa082b441cc`.

Icarus maintains this directory as ordinary Monorepo source. It does not contain an
embedded Git repository or require Git submodules.

Current modifications:

- `OPENKB_CONFIG_DIR` can override the upstream `~/.config/openkb` location.
- The package has a fixed Icarus version because vendored source intentionally has no
  nested Git metadata for Hatch-VCS to inspect.
- `Dockerfile.icarus`, `docker-compose.yaml`, and `scripts/icarus-*` run the imported
  source and place config, knowledge bases, and backups under
  `$ICARUS_DATA_DIR/services/openkb`.
- The managed startup creates the configured knowledge base with the selected Flash
  model before serving requests.
- REST compilation no longer closes LiteLLM's event-loop-wide cached client after each
  document; CLI compilation still closes it before its short-lived loop exits.
- Debug logging redacts LLM credentials, API mutation locks use canonical KB paths,
  uploads publish into watched `raw/` only after completing a staged write, and chat
  session paths reject traversal.
- Upstream parser and PageIndex import guards bound requested page ranges and avoid
  truncating cloud documents at blank pages; CLI query saves reuse the atomic helper.
- The managed image builds the Workbench bundle and the vendored UI removes unsafe
  top-level execution of generated artifact HTML while keeping sandboxed previews.
- Knowledge mutation and session paths add canonical locks, transactional recompile,
  staged upload publication, locked watcher snapshots, and serialized chat turns.
- The launcher uses only the system Python standard library to read the
  repository-root `.env`, then Compose maps only the credentials OpenKB needs
  instead of injecting every Icarus secret into the container. The root
  `icarus` command is the public lifecycle entrypoint. Secrets are not committed
  in this directory.
- The upstream app-local `.env.example` is omitted so the Monorepo root
  `.example.env` remains the single Icarus configuration template.

## Frozen subset and repository infrastructure

The upstream import is frozen at the full commit above; Icarus patches and security
maintenance continue. This first cleanup removes the repository-level upstream plugin
marketplace, nested CI/PyPI release workflows, examples and the unused local-env
preparation helper. Runtime knowledge-base marketplace generation and all original
tests remain; those generated files are not the deleted repository manifest.

The development navigation bundle moved to root `.agents/skills/openkb/`, with the
OpenKB LICENSE attached and container/host path guidance. The three deck theme/critic
bundles remain app-owned runtime resources; existing wheel force-include paths are
unchanged. CLI/API helpers, Workbench, Deck/Skill Factory, watch, configuration template,
lockfile, architecture asset, local development rules, and Icarus docs/spec remain.

The existing `api` extra had changed to the `web` alias without refreshing uv.lock
metadata. Only that metadata was aligned (no package/version changes) so locked dev
installation works. The Dockerfile still uses pip and does not consume uv.lock.
App-owned install/test scripts prepare a private locked dev environment. The default
gate includes real bundled deck/critic discovery; root ignore and Dockerignore retain
recursive private KB/data exclusions. Repository
CI and control entry points replace nested infrastructure without upstream credentials.

The upstream Apache License 2.0 remains in `LICENSE`; root `THIRD_PARTY_NOTICES.md`
records source and relocated skill paths. Re-check license and NOTICE obligations
before incorporating any future third-party source change.
