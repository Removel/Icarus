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
- The launcher reads the repository-root `.env`, then Compose maps only the credentials
  OpenKB needs instead of injecting every Icarus secret into the container. Secrets are
  not committed in this directory.
- The upstream app-local `.env.example` is omitted so the Monorepo root
  `.example.env` remains the single Icarus configuration template.

The upstream Apache License 2.0 remains in `LICENSE`. Re-check upstream `LICENSE` and
`NOTICE` files on every source sync.
